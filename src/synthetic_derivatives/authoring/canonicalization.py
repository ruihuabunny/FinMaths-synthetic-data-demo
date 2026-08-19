"""Neutral decimal and JSON canonicalization contracts for authoring."""

from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any


def quantize_to_increment(
    value: float | Decimal,
    increment: Decimal,
    *,
    decimal_places: int,
) -> Decimal:
    """Round a market price to an exact configured increment."""

    ticks = (Decimal(str(value)) / increment).quantize(
        Decimal("1"), rounding=ROUND_HALF_EVEN
    )
    result = (ticks * increment).quantize(
        Decimal(1).scaleb(-decimal_places), rounding=ROUND_HALF_EVEN
    )
    return abs(result) if result == 0 else result


def canonical_json(value: Any) -> str:
    """Serialize provenance with a stable key and separator order."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def price_quantum(decimal_places: int) -> Decimal:
    """Return the decimal quantum declared by generator configuration."""

    return Decimal(1).scaleb(-decimal_places)


def quantize_price(
    value: float | Decimal, *, decimal_places: int = 8
) -> Decimal:
    """Canonicalize one market decimal and remove signed zero."""

    result = Decimal(str(value)).quantize(
        price_quantum(decimal_places), rounding=ROUND_HALF_EVEN
    )
    return abs(result) if result == 0 else result
