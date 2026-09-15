"""JSON I/O and top-level orchestration for generator configuration parsing."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from synthetic_derivatives.authoring.config_models import (
    GeneratorConfig,
    QPricingConfig,
    UnderlyingConfig,
)
from synthetic_derivatives.authoring.config_versions import (
    ConfigVersionPolicy,
    config_version_policy,
)
from synthetic_derivatives.authoring.dependence import (
    parse_measure_qualified_dependence_specs,
    parse_underlying_dependence_spec,
)
from synthetic_derivatives.authoring.deterministic_functions import (
    finite_float,
    parse_deterministic_function,
)
from synthetic_derivatives.authoring.observation_contracts import (
    parse_intraday_bridge,
    parse_volume_model,
)
from synthetic_derivatives.authoring.option_chain import (
    OptionChainBuilder,
    OptionTemplate,
    parse_option_chain,
    parse_quote_model,
    validate_chain_strikes,
)


def load_generator_config(path: str | Path) -> GeneratorConfig:
    """Load JSON and delegate all semantic work to ``parse_generator_config``."""

    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return parse_generator_config(raw)


def parse_generator_config(raw: Mapping[str, Any]) -> GeneratorConfig:
    """Validate one already-decoded generator configuration object."""

    if not isinstance(raw, Mapping):
        raise ValueError("generator config must be a JSON object")
    schema_version_raw = raw.get("schema_version")
    policy = config_version_policy(schema_version_raw)
    schema_version = str(schema_version_raw)

    supported_runtime_fields = {
        "calendar": "WeekendsOnly",
        "day_count": "Actual365Fixed",
        "pricing_model": "Black-Scholes-Merton",
        "pricing_engine": "QuantLib.AnalyticEuropeanEngine",
        "physical_process": "QuantLib.BlackScholesMertonProcess",
    }
    for field_name, supported_value in supported_runtime_fields.items():
        if raw.get(field_name) != supported_value:
            raise ValueError(
                f"{field_name} must be {supported_value} for the current generator"
            )
    quote_decimal_places = raw.get("quote_decimal_places")
    if (
        isinstance(quote_decimal_places, bool)
        or not isinstance(quote_decimal_places, int)
        or not 1 <= quote_decimal_places <= 8
    ):
        raise ValueError("quote_decimal_places must be an integer between 1 and 8")
    underlying_minimum_price_increment = _parse_minimum_price_increment(
        raw.get("underlying_minimum_price_increment"),
        field_name="underlying_minimum_price_increment",
        decimal_places=quote_decimal_places,
    )
    option_minimum_price_increment = _parse_minimum_price_increment(
        raw.get("option_minimum_price_increment"),
        field_name="option_minimum_price_increment",
        decimal_places=quote_decimal_places,
    )

    underlyings_list: list[UnderlyingConfig] = []
    for item in raw["underlyings"]:
        if not policy.allow_deterministic_functions and any(
            isinstance(item[field], dict)
            for field in ("physical_drift", "physical_volatility")
        ):
            raise ValueError(
                "deterministic physical functions require schema_version 1.1.0"
            )
        drift_function = parse_deterministic_function(
            item["physical_drift"], field_name="physical_drift"
        )
        volatility_function = parse_deterministic_function(
            item["physical_volatility"], field_name="physical_volatility"
        )
        if (
            policy.pricing_contract == "common_q"
            and "base_implied_volatility" in item
        ):
            raise ValueError(
                f"schema_version {schema_version} derives Q volatility from the "
                "declared diffusion and forbids base_implied_volatility"
            )
        base_implied_volatility = (
            None
            if policy.pricing_contract == "common_q"
            else float(item["base_implied_volatility"])
        )
        if policy.observation_contract == "bridge_volume":
            base_volume = item.get("base_volume")
            if (
                isinstance(base_volume, bool)
                or not isinstance(base_volume, int)
                or not 1 <= base_volume <= 2**63 - 1
            ):
                raise ValueError(
                    "base_volume must be a positive signed-int64 integer"
                )
            volume_log_stddev = finite_float(
                item.get("volume_log_stddev"), field_name="volume_log_stddev"
            )
            if not 0.0 <= volume_log_stddev <= 2.0:
                raise ValueError("volume_log_stddev must be between 0 and 2")
        else:
            if "base_volume" in item or "volume_log_stddev" in item:
                raise ValueError(
                    "base_volume and volume_log_stddev require schema_version 1.8.0"
                )
            base_volume = None
            volume_log_stddev = None
        underlyings_list.append(
            UnderlyingConfig(
                underlying_id=item["underlying_id"],
                initial_spot=Decimal(str(item["initial_spot"])),
                physical_drift=drift_function.initial_value,
                physical_volatility=volatility_function.initial_value,
                physical_drift_function=drift_function,
                physical_volatility_function=volatility_function,
                risk_free_rate=float(item["risk_free_rate"]),
                dividend_yield=float(item["dividend_yield"]),
                base_implied_volatility=base_implied_volatility,
                base_volume=base_volume,
                volume_log_stddev=volume_log_stddev,
            )
        )
    underlyings = tuple(underlyings_list)

    if policy.option_input == "chain":
        if "option_templates" in raw:
            raise ValueError(
                f"schema_version {schema_version} uses option_chain, not option_templates"
            )
        option_chain = parse_option_chain(
            raw.get("option_chain"), schema_version=schema_version, policy=policy
        )
        builder = OptionChainBuilder(option_chain)
        templates = builder.build_templates()
        validate_chain_strikes(
            underlyings,
            builder,
            underlying_minimum_price_increment,
            quote_decimal_places,
        )
    else:
        if "option_chain" in raw:
            raise ValueError("option_chain requires schema_version 1.3.0+")
        option_chain = None
        templates = tuple(
            OptionTemplate(
                template_id=item["template_id"],
                call_put=item["call_put"],
                strike_moneyness=Decimal(str(item["strike_moneyness"])),
                expiry_days=int(item["expiry_days"]),
                exercise_style=item["exercise_style"],
                settlement_type=item["settlement_type"],
                contract_multiplier=Decimal(str(item["contract_multiplier"])),
            )
            for item in raw["option_templates"]
        )
    _validate_entities(underlyings, templates)

    if policy.pricing_contract == "common_q":
        if "smile" in raw:
            raise ValueError(
                f"schema_version {schema_version} uses an explicit Q pricing "
                "contract and forbids the legacy latent smile"
            )
        q_pricing = _parse_q_pricing(
            raw.get("q_pricing"), schema_version=schema_version, policy=policy
        )
        smile = None
    else:
        if "q_pricing" in raw:
            raise ValueError("q_pricing requires schema_version 1.5.0+")
        q_pricing = None
        smile = {key: float(value) for key, value in raw["smile"].items()}

    if policy.dependence_contract == "p_q":
        if "underlying_simulation" in raw:
            raise ValueError(
                f"schema_version {schema_version} uses "
                "underlying_dependence_specs, not underlying_simulation"
            )
        if q_pricing is None:
            raise ValueError(f"schema_version {schema_version} requires q_pricing")
        underlying_dependence_specs = parse_measure_qualified_dependence_specs(
            raw.get("underlying_dependence_specs"),
            underlyings,
            q_pricing,
            schema_version=schema_version,
        )
    elif policy.dependence_contract == "p_only":
        if "underlying_dependence_specs" in raw:
            raise ValueError(
                "underlying_dependence_specs requires schema_version 1.7.0+"
            )
        underlying_dependence_specs = (
            parse_underlying_dependence_spec(
                raw.get("underlying_simulation"),
                underlyings,
                expected_measure="P",
                field_name="underlying_simulation",
            ),
        )
    else:
        if "underlying_simulation" in raw or "underlying_dependence_specs" in raw:
            raise ValueError(
                "underlying dependence requires schema_version 1.2.0+"
            )
        underlying_dependence_specs = ()

    if raw.get("rng") != policy.expected_rng:
        raise ValueError(
            "rng must match the configured underlying dependence and current "
            f"generator: {policy.expected_rng}"
        )
    quote_model, bid_ask_noise = parse_quote_model(
        raw.get("quote_model"), schema_version=schema_version, policy=policy
    )
    if policy.observation_contract == "bridge_volume":
        intraday_bridge = parse_intraday_bridge(raw.get("intraday_bridge"))
        volume_model = parse_volume_model(raw.get("volume_model"))
        observation_namespaces = {
            intraday_bridge.stream_namespace,
            volume_model.stream_namespace,
        }
        if len(observation_namespaces) != 2:
            raise ValueError("bridge and volume stream namespaces must be distinct")
        if (
            bid_ask_noise is not None
            and bid_ask_noise.stream_namespace in observation_namespaces
        ):
            raise ValueError(
                "bridge, volume and option-noise stream namespaces must be distinct"
            )
    else:
        if "intraday_bridge" in raw or "volume_model" in raw:
            raise ValueError(
                "intraday_bridge and volume_model require schema_version 1.8.0"
            )
        intraday_bridge = None
        volume_model = None

    return GeneratorConfig(
        schema_version=schema_version,
        generator_config_id=raw["generator_config_id"],
        generator_version=raw["generator_version"],
        snapshot_id=raw["snapshot_id"],
        seed=int(raw["seed"]),
        rng=raw["rng"],
        start_date=date.fromisoformat(raw["start_date"]),
        business_days=int(raw["business_days"]),
        calendar=raw["calendar"],
        day_count=raw["day_count"],
        valuation_time_utc=raw["valuation_time_utc"],
        pricing_model=raw["pricing_model"],
        pricing_engine=raw["pricing_engine"],
        physical_process=raw["physical_process"],
        currency=raw["currency"],
        quote_decimal_places=quote_decimal_places,
        underlying_minimum_price_increment=underlying_minimum_price_increment,
        option_minimum_price_increment=option_minimum_price_increment,
        underlyings=underlyings,
        option_templates=templates,
        smile=smile,
        q_pricing=q_pricing,
        quote_model=quote_model,
        bid_ask_noise=bid_ask_noise,
        underlying_dependence_specs=underlying_dependence_specs,
        option_chain=option_chain,
        intraday_bridge=intraday_bridge,
        volume_model=volume_model,
    )


def _parse_minimum_price_increment(
    raw: Any, *, field_name: str, decimal_places: int
) -> Decimal:
    try:
        increment = Decimal(str(raw))
    except Exception as error:
        raise ValueError(f"{field_name} must be a decimal") from error
    storage_quantum = Decimal(1).scaleb(-decimal_places)
    if (
        not increment.is_finite()
        or increment <= 0
        or increment != increment.quantize(storage_quantum)
    ):
        raise ValueError(
            f"{field_name} must be positive and representable with "
            f"quote_decimal_places={decimal_places}"
        )
    return increment


def _parse_q_pricing(
    raw: Any, *, schema_version: str, policy: ConfigVersionPolicy
) -> QPricingConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"schema_version {schema_version} requires q_pricing")
    if (
        not policy.allow_legacy_authoring_iv_solver
        and "implied_volatility_solver" in raw
    ):
        raise ValueError(
            "q_pricing.implied_volatility_solver belongs to task/verifier "
            f"configuration and is forbidden by schema_version {schema_version}"
        )
    required_identifiers = (
        "risk_neutral_measure_id",
        "numeraire_id",
        "rate_path_id",
    )
    identifiers: dict[str, str] = {}
    for field_name in required_identifiers:
        value = raw.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"q_pricing.{field_name} must be non-empty")
        identifiers[field_name] = value
    if raw.get("measure_change") != "girsanov_drift_only":
        raise ValueError("q_pricing.measure_change must be girsanov_drift_only")
    if raw.get("volatility_mapping") != "same_deterministic_diffusion":
        raise ValueError(
            "q_pricing.volatility_mapping must be same_deterministic_diffusion"
        )
    return QPricingConfig(
        risk_neutral_measure_id=identifiers["risk_neutral_measure_id"],
        numeraire_id=identifiers["numeraire_id"],
        rate_path_id=identifiers["rate_path_id"],
        measure_change="girsanov_drift_only",
        volatility_mapping="same_deterministic_diffusion",
        legacy_authoring_iv_solver_present=("implied_volatility_solver" in raw),
    )


def _validate_entities(
    underlyings: tuple[UnderlyingConfig, ...],
    templates: tuple[OptionTemplate, ...],
) -> None:
    if not underlyings or not templates:
        raise ValueError("config requires at least one underlying and one option template")
    underlying_ids = [item.underlying_id for item in underlyings]
    template_ids = [item.template_id for item in templates]
    if len(underlying_ids) != len(set(underlying_ids)):
        raise ValueError("underlying_id values must be unique")
    if len(template_ids) != len(set(template_ids)):
        raise ValueError("option template_id values must be unique")
    for item in underlyings:
        if item.initial_spot <= 0:
            raise ValueError(f"initial_spot must be positive: {item.underlying_id}")
        if (
            any(node.value <= 0 for node in item.physical_volatility_function.nodes)
            or (
                item.base_implied_volatility is not None
                and item.base_implied_volatility <= 0
            )
        ):
            raise ValueError(f"volatility must be positive: {item.underlying_id}")
    for item in templates:
        if item.call_put not in {"call", "put"}:
            raise ValueError(f"unsupported call_put: {item.call_put}")
        if item.exercise_style != "european":
            raise ValueError("the v1 pipeline supports European exercise only")
        if (
            (item.strike_moneyness is None) == (item.strike_absolute is None)
            or (
                item.strike_moneyness is not None
                and (
                    not item.strike_moneyness.is_finite()
                    or item.strike_moneyness <= 0
                )
            )
            or (
                item.strike_absolute is not None
                and (
                    not item.strike_absolute.is_finite()
                    or item.strike_absolute <= 0
                )
            )
            or item.expiry_days <= 0
            or not item.contract_multiplier.is_finite()
            or item.contract_multiplier <= 0
        ):
            raise ValueError(f"invalid option template: {item.template_id}")
