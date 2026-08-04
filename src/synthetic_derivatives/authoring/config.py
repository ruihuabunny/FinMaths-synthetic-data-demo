"""Generator configuration parsing and deterministic identifiers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UnderlyingConfig:
    underlying_id: str
    initial_spot: Decimal
    physical_drift: float
    physical_volatility: float
    risk_free_rate: float
    dividend_yield: float
    base_implied_volatility: float


@dataclass(frozen=True)
class OptionTemplate:
    template_id: str
    call_put: str
    strike_moneyness: Decimal
    expiry_days: int
    exercise_style: str
    settlement_type: str
    contract_multiplier: Decimal


@dataclass(frozen=True)
class GeneratorConfig:
    raw: dict[str, Any]
    sha256: str
    generator_config_id: str
    generator_version: str
    snapshot_id: str
    seed: int
    rng: str
    start_date: date
    business_days: int
    calendar: str
    day_count: str
    valuation_time_utc: str
    pricing_model: str
    pricing_engine: str
    physical_process: str
    currency: str
    quote_decimal_places: int
    underlyings: tuple[UnderlyingConfig, ...]
    option_templates: tuple[OptionTemplate, ...]
    smile: dict[str, float]
    quote_model: dict[str, Any]


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def load_generator_config(path: str | Path) -> GeneratorConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("generator config must be a JSON object")
    if raw.get("schema_version") != "1.0.0":
        raise ValueError("unsupported generator config schema_version")

    underlyings = tuple(
        UnderlyingConfig(
            underlying_id=item["underlying_id"],
            initial_spot=Decimal(str(item["initial_spot"])),
            physical_drift=float(item["physical_drift"]),
            physical_volatility=float(item["physical_volatility"]),
            risk_free_rate=float(item["risk_free_rate"]),
            dividend_yield=float(item["dividend_yield"]),
            base_implied_volatility=float(item["base_implied_volatility"]),
        )
        for item in raw["underlyings"]
    )
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
    canonical = _canonical_bytes(raw)
    return GeneratorConfig(
        raw=raw,
        sha256=hashlib.sha256(canonical).hexdigest(),
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
        quote_decimal_places=int(raw["quote_decimal_places"]),
        underlyings=underlyings,
        option_templates=templates,
        smile={key: float(value) for key, value in raw["smile"].items()},
        quote_model=dict(raw["quote_model"]),
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
        if item.physical_volatility <= 0 or item.base_implied_volatility <= 0:
            raise ValueError(f"volatility must be positive: {item.underlying_id}")
    for item in templates:
        if item.call_put not in {"call", "put"}:
            raise ValueError(f"unsupported call_put: {item.call_put}")
        if item.exercise_style != "european":
            raise ValueError("the v1 pipeline supports European exercise only")
        if item.strike_moneyness <= 0 or item.expiry_days <= 0:
            raise ValueError(f"invalid option template: {item.template_id}")


def option_id(underlying_id: str, template_id: str) -> str:
    return f"{underlying_id}-{template_id}"
