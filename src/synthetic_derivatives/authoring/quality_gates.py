"""Post-write completeness, exact replay, Q-context, and privacy gates."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import duckdb

from synthetic_derivatives.authoring.compatibility import SnapshotCompatibilityValidator
from synthetic_derivatives.authoring.config_models import GeneratorConfig
from synthetic_derivatives.authoring.row_contracts import UnderlyingDailyRow
from synthetic_derivatives.authoring.snapshot_store import SnapshotStore
from synthetic_derivatives.authoring.table_specs import TABLE_SPECS
from synthetic_derivatives.authoring.visibility import assert_no_private_metadata_leakage


class SnapshotQualityGates:
    """Validate a fully materialized snapshot without owning its transaction."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        config: GeneratorConfig,
        underlying_generator: Any,
        compatibility: SnapshotCompatibilityValidator,
        store: SnapshotStore,
    ) -> None:
        self.connection = connection
        self.config = config
        self.underlying_generator = underlying_generator
        self.compatibility = compatibility
        self.store = store

    def validate_materialized_snapshot(self) -> None:
        """Validate cross-table completeness and immutable contract identity.

        These are implementation-integrity gates, not a replacement for the
        no-arbitrage conditions supplied by the selected pricing model and
        numeraire.  In particular, no check treats derivative contracts as
        correlation drivers.
        """

        self.compatibility._assert_underlying_dependence_is_compatible()
        self.compatibility._assert_observation_specs_are_compatible()
        self._assert_common_q_context()
        self.compatibility._assert_option_chain_is_compatible()
        self.compatibility._assert_option_contracts_are_compatible()
        checks = {
            "option_chain_contract_without_spec": """
                SELECT count(*) FROM market.option_contracts contract
                LEFT JOIN market.option_chain_specs chain
                  ON chain.snapshot_id = contract.snapshot_id
                 AND chain.chain_id = contract.chain_id
                WHERE contract.snapshot_id = ?
                  AND contract.chain_id IS NOT NULL
                  AND chain.chain_id IS NULL
            """,
            "incomplete_option_chain_contract": """
                SELECT count(*) FROM market.option_contracts contract
                JOIN market.option_chain_specs chain
                  ON chain.snapshot_id = contract.snapshot_id
                 AND chain.chain_id = contract.chain_id
                WHERE contract.snapshot_id = ?
                  AND (contract.listing_date IS NULL
                       OR contract.listing_spot IS NULL
                       OR (chain.grid_type = 'moneyness'
                           AND contract.strike_moneyness IS NULL)
                       OR (chain.grid_type = 'strike'
                           AND contract.strike_moneyness IS NOT NULL))
            """,
            "orphan_option_contract": """
                SELECT count(*) FROM market.option_contracts option_contract
                LEFT JOIN market.underlyings underlying
                  ON underlying.snapshot_id = option_contract.snapshot_id
                 AND underlying.underlying_id = option_contract.underlying_id
                WHERE option_contract.snapshot_id = ? AND underlying.underlying_id IS NULL
            """,
            "orphan_underlying_daily": """
                SELECT count(*) FROM market.underlying_daily daily
                LEFT JOIN market.underlyings underlying
                  ON underlying.snapshot_id = daily.snapshot_id
                 AND underlying.underlying_id = daily.underlying_id
                WHERE daily.snapshot_id = ? AND underlying.underlying_id IS NULL
            """,
            "orphan_underlying_volume_model": """
                SELECT count(*) FROM market.underlying_volume_models model
                LEFT JOIN market.underlyings underlying
                  ON underlying.snapshot_id = model.snapshot_id
                 AND underlying.underlying_id = model.underlying_id
                WHERE model.snapshot_id = ? AND underlying.underlying_id IS NULL
            """,
            "orphan_option_daily": """
                SELECT count(*) FROM market.option_daily daily
                LEFT JOIN market.option_contracts contract
                  ON contract.snapshot_id = daily.snapshot_id
                 AND contract.option_id = daily.option_id
                WHERE daily.snapshot_id = ? AND contract.option_id IS NULL
            """,
            "option_quote_contract_mismatch": """
                SELECT count(*) FROM market.option_daily daily
                JOIN market.option_contracts contract
                  ON contract.snapshot_id = daily.snapshot_id
                 AND contract.option_id = daily.option_id
                WHERE daily.snapshot_id = ?
                  AND (daily.underlying_id != contract.underlying_id
                       OR daily.call_put != contract.call_put
                       OR daily.strike != contract.strike
                       OR daily.expiry != contract.expiry
                       OR daily.exercise_style != contract.exercise_style
                       OR daily.settlement_type != contract.settlement_type
                       OR daily.contract_multiplier != contract.contract_multiplier)
            """,
            "missing_pricing_metadata": """
                SELECT count(*) FROM market.underlying_daily daily
                LEFT JOIN market.pricing_metadata pricing
                  ON pricing.snapshot_id = daily.snapshot_id
                 AND pricing.valuation_date = daily.date
                 AND pricing.underlying_id = daily.underlying_id
                WHERE daily.snapshot_id = ? AND pricing.underlying_id IS NULL
            """,
            "missing_option_quote": """
                SELECT count(*)
                FROM market.underlying_daily daily
                JOIN market.option_contracts contract
                  ON contract.snapshot_id = daily.snapshot_id
                 AND contract.underlying_id = daily.underlying_id
                 AND daily.date < contract.expiry
                LEFT JOIN market.option_daily quote
                  ON quote.snapshot_id = daily.snapshot_id
                 AND quote.date = daily.date
                 AND quote.option_id = contract.option_id
                WHERE daily.snapshot_id = ? AND quote.option_id IS NULL
            """,
        }
        if not self.store.relation_exists("market.underlying_volume_models"):
            checks.pop("orphan_underlying_volume_model")
        failures = {
            name: self.connection.execute(sql, [self.config.snapshot_id]).fetchone()[0]
            for name, sql in checks.items()
        }
        failures = {name: count for name, count in failures.items() if count}
        if failures:
            raise ValueError(f"snapshot quality gates failed: {failures}")
        counts = self.store.counts()
        if self.config.schema_version == "1.8.0":
            if counts["intraday_bridge_spec_count"] != 1:
                raise ValueError(
                    "config-1.8 snapshot requires exactly one intraday bridge spec"
                )
            if counts["underlying_volume_model_count"] != len(
                self.config.underlyings
            ):
                raise ValueError(
                    "config-1.8 snapshot requires one volume model per underlying"
                )
        elif (
            counts["intraday_bridge_spec_count"]
            or counts["underlying_volume_model_count"]
        ):
            raise ValueError("legacy snapshot must not contain observation-law specs")
        self._assert_underlying_observation_replay()
        self._assert_solver_visible_observation_privacy()

    def _assert_underlying_observation_replay(self) -> None:
        """Exactly replay every config-1.8 OHLCV row from the initial state."""

        if self.config.schema_version != "1.8.0":
            return
        maximum_date = self.connection.execute(
            """
            SELECT max(date) FROM market.underlying_daily
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()[0]
        if maximum_date is None:
            raise ValueError("config-1.8 snapshot contains no underlying rows")
        dates = self.underlying_generator.business_dates_between(
            self.config.start_date, maximum_date
        )
        expected_typed_rows: list[UnderlyingDailyRow] = []
        for underlying in self.config.underlyings:
            previous_date: date | None = None
            previous_close = underlying.initial_spot
            for market_date in dates:
                if previous_date is None:
                    row = self.underlying_generator.initial_underlying_daily_row(
                        underlying, "observation-replay"
                    )
                else:
                    row = self.underlying_generator.underlying_daily_row(
                        underlying,
                        market_date,
                        previous_date,
                        previous_close,
                        "observation-replay",
                    )
                expected_typed_rows.append(row)
                previous_date = market_date
                previous_close = row.spot_close
        expected_typed_rows.sort(key=lambda row: (row.date, row.underlying_id))
        expected_rows = [tuple(row[:-1]) for row in expected_typed_rows]

        logical_columns = TABLE_SPECS["underlying_daily"].columns[:-1]
        actual_rows = self.connection.execute(
            f"""
            SELECT {', '.join(logical_columns)}
            FROM market.underlying_daily
            WHERE snapshot_id = ?
            ORDER BY date, underlying_id
            """,
            [self.config.snapshot_id],
        ).fetchall()
        if [tuple(row) for row in actual_rows] != expected_rows:
            raise ValueError(
                "underlying OHLCV rows conflict with exact config-1.8 replay"
            )

    def _assert_solver_visible_observation_privacy(self) -> None:
        """Reject private observation fields or values in solver-visible views."""

        forbidden_fields = {
            "bridge_spec_id",
            "volume_spec_id",
            "stream_namespace",
            "base_volume",
            "volume_log_stddev",
            "bridge_increment",
        }
        normalized_forbidden = {
            "".join(character for character in value if character.isalnum())
            for value in forbidden_fields
        }
        visible_columns = self.connection.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'solver_visible'
            """
        ).fetchall()
        leaked_columns = [
            column
            for (column,) in visible_columns
            if "".join(
                character
                for character in column.casefold()
                if character.isalnum()
            )
            in normalized_forbidden
        ]
        if leaked_columns:
            raise ValueError(
                f"private observation fields leaked into solver_visible: {leaked_columns}"
            )

        metadata_rows = self.connection.execute(
            """
            SELECT physical_dynamics, pricing_dynamics
            FROM solver_visible.pricing_metadata
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchall()
        decoded_rows: list[tuple[Any, Any]] = []
        for physical, pricing in metadata_rows:
            decoded = (json.loads(physical), json.loads(pricing))
            assert_no_private_metadata_leakage(decoded)
            decoded_rows.append(decoded)
        if self.config.schema_version != "1.8.0":
            return
        bridge = self.config.intraday_bridge
        volume = self.config.volume_model
        if bridge is None or volume is None:
            raise ValueError("config-1.8 observation contracts are required")
        visible_payload = json.dumps(decoded_rows, sort_keys=True, default=str)
        for private_value in (
            bridge.bridge_spec_id,
            bridge.stream_namespace,
            volume.volume_spec_id,
            volume.stream_namespace,
        ):
            if private_value in visible_payload:
                raise ValueError(
                    "private observation contract value leaked into solver_visible"
                )

    def _assert_common_q_context(self) -> None:
        """Freeze the common Q, numeraire and flat rate-path identity.

        Config 1.7's drift-only mapping keeps P/Q Brownian covariance fixed,
        while every same-currency vanilla margin uses the one declared pricing
        measure, money-market numeraire and flat rate path. Correlation remains
        absent from the option generator's marginal pricing inputs.
        """

        q_dependence = self.config.q_underlying_dependence
        if q_dependence is None:
            return
        q_pricing = self.config.q_pricing
        if q_pricing is None:
            raise ValueError("Q dependence requires a common q_pricing contract")
        expected_context = (
            self.config.currency,
            q_pricing.risk_neutral_measure_id,
            q_pricing.numeraire_id,
            q_pricing.rate_path_id,
            q_dependence.dependence_spec_id,
        )
        if (
            q_dependence.risk_neutral_measure_id,
            q_dependence.numeraire_id,
            q_dependence.rate_path_id,
        ) != expected_context[1:4]:
            raise ValueError(
                "Q dependence and q_pricing measure/numeraire/rate-path IDs differ"
            )
        if len({underlying.risk_free_rate for underlying in self.config.underlyings}) != 1:
            raise ValueError(
                "one flat rate_path_id cannot identify different underlying rates"
            )
        persisted_contexts = set(
            self.connection.execute(
                """
                SELECT DISTINCT
                    currency,
                    json_extract_string(
                        pricing_dynamics,
                        '$.q_pricing.risk_neutral_measure_id'
                    ),
                    json_extract_string(
                        pricing_dynamics,
                        '$.q_pricing.numeraire_id'
                    ),
                    json_extract_string(
                        pricing_dynamics,
                        '$.q_pricing.rate_path_id'
                    ),
                    json_extract_string(
                        pricing_dynamics,
                        '$.underlying_dependence.dependence_spec_id'
                    )
                FROM market.pricing_metadata
                WHERE snapshot_id = ?
                """,
                [self.config.snapshot_id],
            ).fetchall()
        )
        if persisted_contexts and persisted_contexts != {expected_context}:
            raise ValueError(
                "pricing metadata does not share one Q/numeraire/rate-path/"
                "dependence identity"
            )
