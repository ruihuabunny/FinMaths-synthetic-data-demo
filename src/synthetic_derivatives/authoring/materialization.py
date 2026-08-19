"""Incremental row generation and insert-only snapshot materialization."""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb

from synthetic_derivatives.authoring.config_models import GeneratorConfig
from synthetic_derivatives.authoring.persistence import merge_rows
from synthetic_derivatives.authoring.row_contracts import OptionDailyRow, UnderlyingDailyRow
from synthetic_derivatives.authoring.table_specs import TABLE_SPECS


class IncrementalMaterializer:
    """Generate missing logical rows without owning the transaction boundary."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        config: GeneratorConfig,
        underlying_generator: Any,
        option_generator: Any,
    ) -> None:
        self.connection = connection
        self.config = config
        self.underlying_generator = underlying_generator
        self.option_generator = option_generator

    def materialize(
        self, dates: list[date], run_id: str
    ) -> dict[str, dict[str, int]]:
        """Insert missing business-key rows without rewriting historical state.

        Underlying closes are generated chronologically from the last persisted,
        quantized close.  For the current GBM that value and the interval dates
        form the complete Markov restart state.  Date-partitioned factor and
        idiosyncratic streams make this append path identical to a one-shot run.

        Chain specs and contract masters are reconstructed from immutable config
        on every run, then merged by stable keys.  Daily option rows are built
        only after the underlying slice exists and receive realized spots, not
        the underlying dependence matrix.
        """

        underlying_dependence_rows = (
            self.underlying_generator.underlying_dependence_rows(run_id)
        )
        intraday_bridge_spec_rows = (
            self.underlying_generator.intraday_bridge_spec_rows(run_id)
        )
        underlying_volume_model_rows = (
            self.underlying_generator.underlying_volume_model_rows(run_id)
        )
        option_chain_spec_row = self.option_generator.option_chain_spec_row(run_id)
        # Private provenance and immutable masters are always reconstructed
        # from config. MERGE inserts them once and compatibility checks protect
        # existing economic definitions from silent updates.
        underlying_master_rows = [
            self.underlying_generator.underlying_master_row(underlying, run_id)
            for underlying in self.config.underlyings
        ]
        option_contract_rows = [
            self.option_generator.option_contract_row(underlying, template, run_id)
            for underlying in self.config.underlyings
            for template in self.config.option_templates
        ]
        stats = {
            "intraday_bridge_specs": merge_rows(
                self.connection,
                TABLE_SPECS["intraday_bridge_specs"],
                intraday_bridge_spec_rows,
            ),
            "underlying_volume_models": merge_rows(
                self.connection,
                TABLE_SPECS["underlying_volume_models"],
                underlying_volume_model_rows,
            ),
            "underlying_dependence": merge_rows(
                self.connection,
                TABLE_SPECS["underlying_dependence"],
                underlying_dependence_rows,
            ),
            "option_chain_specs": merge_rows(
                self.connection,
                TABLE_SPECS["option_chain_specs"],
                [option_chain_spec_row]
                if option_chain_spec_row is not None
                else [],
            ),
            "underlyings": merge_rows(
                self.connection, TABLE_SPECS["underlyings"], underlying_master_rows
            ),
            "option_contracts": merge_rows(
                self.connection, TABLE_SPECS["option_contracts"], option_contract_rows
            ),
        }

        underlying_daily_rows: list[UnderlyingDailyRow] = []
        requested_dates = set(dates)
        previous_date_by_key: dict[tuple[date, str], date | None] = {}
        # Each underlying path advances chronologically from its last persisted,
        # quantized close. Filling a gap before a later close would invalidate
        # all subsequent Markov transitions and is therefore rejected.
        for underlying in self.config.underlyings:
            existing = {
                path_date: spot_close
                for path_date, spot_close in self.connection.execute(
                    """
                    SELECT date, spot_close
                    FROM market.underlying_daily
                    WHERE snapshot_id = ? AND underlying_id = ?
                    ORDER BY date
                    """,
                    [self.config.snapshot_id, underlying.underlying_id],
                ).fetchall()
            }
            for market_date in dates:
                if market_date in existing:
                    continue
                later_dates = [value for value in existing if value > market_date]
                if later_dates:
                    raise ValueError(
                        f"cannot fill a historical path gap for {underlying.underlying_id}; "
                        "create a new snapshot instead"
                    )
                prior_dates = [value for value in existing if value < market_date]
                if prior_dates:
                    previous_date = max(prior_dates)
                    previous_close = existing[previous_date]
                else:
                    if market_date != self.config.start_date:
                        raise ValueError(
                            f"new underlying {underlying.underlying_id} must be generated "
                            f"from configured start_date {self.config.start_date}"
                        )
                    previous_date = None
                    previous_close = underlying.initial_spot
                if previous_date is None:
                    row = self.underlying_generator.initial_underlying_daily_row(
                        underlying, run_id
                    )
                else:
                    row = self.underlying_generator.underlying_daily_row(
                        underlying, market_date, previous_date, previous_close, run_id
                    )
                underlying_daily_rows.append(row)
                existing[market_date] = row.spot_close

            prior_path_date: date | None = None
            for path_date in sorted(existing):
                if path_date in requested_dates:
                    previous_date_by_key[(path_date, underlying.underlying_id)] = (
                        prior_path_date
                        if prior_path_date is not None
                        else None
                    )
                prior_path_date = path_date

        stats["underlying_daily"] = merge_rows(
            self.connection, TABLE_SPECS["underlying_daily"], underlying_daily_rows
        )

        existing_metadata = {
            (valuation_date, underlying_id)
            for valuation_date, underlying_id in self.connection.execute(
                """
                SELECT valuation_date, underlying_id
                FROM market.pricing_metadata
                WHERE snapshot_id = ? AND valuation_date BETWEEN ? AND ?
                """,
                [self.config.snapshot_id, dates[0], dates[-1]],
            ).fetchall()
        }
        # Metadata must use the actual predecessor date, including multi-day
        # weekend intervals, so deterministic drift/variance reductions replay.
        metadata_rows = [
            self.underlying_generator.pricing_metadata_row(
                underlying,
                market_date,
                previous_date_by_key[(market_date, underlying.underlying_id)],
                run_id,
            )
            for underlying in self.config.underlyings
            for market_date in dates
            if (market_date, underlying.underlying_id) not in existing_metadata
        ]
        stats["pricing_metadata"] = merge_rows(
            self.connection, TABLE_SPECS["pricing_metadata"], metadata_rows
        )

        spot_rows = self.connection.execute(
            """
            SELECT date, underlying_id, spot_close
            FROM market.underlying_daily
            WHERE snapshot_id = ? AND date BETWEEN ? AND ?
            """,
            [self.config.snapshot_id, dates[0], dates[-1]],
        ).fetchall()
        spot_by_key = {
            (market_date, underlying_id): spot_close
            for market_date, underlying_id, spot_close in spot_rows
        }
        existing_option_daily = {
            (market_date, option_id)
            for market_date, option_id in self.connection.execute(
                """
                SELECT date, option_id
                FROM market.option_daily
                WHERE snapshot_id = ? AND date BETWEEN ? AND ?
                """,
                [self.config.snapshot_id, dates[0], dates[-1]],
            ).fetchall()
        }
        underlyings_by_id = {
            item.underlying_id: item for item in self.config.underlyings
        }
        option_daily_rows: list[OptionDailyRow] = []
        # Options are priced only after the realized spot slice is materialized.
        # The option generator receives no Lambda/D/R object; dependence reaches
        # it only through spot_by_key.
        for contract in option_contract_rows:
            underlying_id = contract.underlying_id
            for market_date in dates:
                quote_key = (market_date, contract.option_id)
                quote_exists = quote_key in existing_option_daily
                if quote_exists:
                    continue
                spot = spot_by_key[(market_date, underlying_id)]
                result = self.option_generator.option_daily_result(
                    underlyings_by_id[underlying_id],
                    contract,
                    market_date,
                    spot,
                    run_id,
                )
                if result is not None:
                    option_daily_rows.append(result.quote_row)
        stats["option_daily"] = merge_rows(
            self.connection, TABLE_SPECS["option_daily"], option_daily_rows
        )
        return stats
