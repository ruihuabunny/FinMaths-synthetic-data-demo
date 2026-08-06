"""Shared deterministic infrastructure for QuantLib authoring generators."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

import QuantLib as ql

from synthetic_derivatives.authoring.config import GeneratorConfig


PINNED_QUANTLIB_VERSION = "1.39"
PRICE_QUANTUM = Decimal("0.00000001")


def canonical_json(value: Any) -> str:
    """Serialize private provenance with a stable key and separator order."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def quantize_price(value: float | Decimal) -> Decimal:
    """Canonicalize one market decimal and remove signed zero."""

    result = Decimal(str(value)).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)
    return abs(result) if result == 0 else result


def ql_date(value: date) -> ql.Date:
    """Convert a Python date without passing through locale-dependent text."""

    return ql.Date(value.day, value.month, value.year)


def py_date(value: ql.Date) -> date:
    """Convert a QuantLib date without passing through locale-dependent text."""

    return date(value.year(), value.month(), value.dayOfMonth())


class QuantLibGeneratorBase:
    """Common calendar, precision and deterministic-stream behavior.

    Concrete generators inherit infrastructure only.  Product-specific row
    generation stays in ``UnderlyingDailyGenerator`` or ``OptionDailyGenerator``
    so the option generator has no API for P-measure dependence or path shocks.
    """

    def __init__(self, config: GeneratorConfig):
        """Validate pinned runtime conventions and construct shared calendars."""

        if ql.__version__ != PINNED_QUANTLIB_VERSION:
            raise RuntimeError(
                "QuantLib version mismatch: expected "
                f"{PINNED_QUANTLIB_VERSION}, got {ql.__version__}"
            )
        if config.calendar != "WeekendsOnly" or config.day_count != "Actual365Fixed":
            raise ValueError("v1 supports WeekendsOnly and Actual365Fixed only")
        self.config = config
        self.calendar = ql.WeekendsOnly()
        self.day_count = ql.Actual365Fixed()

    def business_dates(self, start: date, count: int) -> list[date]:
        """Return ``count`` business dates beginning at adjusted ``start``."""

        if count < 1:
            raise ValueError("business-day count must be positive")
        current = self.calendar.adjust(ql_date(start), ql.Following)
        result: list[date] = []
        while len(result) < count:
            if self.calendar.isBusinessDay(current):
                result.append(py_date(current))
            current = current + 1
        return result

    def business_dates_between(self, start: date, end: date) -> list[date]:
        """Return all business dates in one inclusive calendar interval."""

        if end < start:
            raise ValueError("end date must not be before start date")
        result: list[date] = []
        current = ql_date(start)
        final = ql_date(end)
        while current <= final:
            if self.calendar.isBusinessDay(current):
                result.append(py_date(current))
            current = current + 1
        return result

    def next_business_dates(self, last_date: date, count: int) -> list[date]:
        """Return the next ``count`` business dates strictly after ``last_date``."""

        if count < 1:
            raise ValueError("business-day count must be positive")
        result: list[date] = []
        current = ql_date(last_date) + 1
        while len(result) < count:
            if self.calendar.isBusinessDay(current):
                result.append(py_date(current))
            current = current + 1
        return result

    def previous_business_date(self, market_date: date) -> date:
        """Return the business date immediately before ``market_date``."""

        return py_date(self.calendar.advance(ql_date(market_date), -1, ql.Days))

    def _derived_seed(self, *parts: Any) -> int:
        """Derive one stable 32-bit QuantLib seed from a semantic namespace."""

        material = "|".join(
            [self.config.snapshot_id, str(self.config.seed), *(str(part) for part in parts)]
        ).encode("utf-8")
        return int.from_bytes(hashlib.sha256(material).digest()[:4], "big")

    def _gaussian(self, *parts: Any) -> float:
        """Draw one replayable Gaussian value from a namespaced seed."""

        uniform = ql.MersenneTwisterUniformRng(self._derived_seed(*parts))
        gaussian = ql.BoxMullerMersenneTwisterGaussianRng(uniform)
        return float(gaussian.next().value())

    def _uniform(self, *parts: Any) -> float:
        """Draw one replayable uniform value from a namespaced seed."""

        uniform = ql.MersenneTwisterUniformRng(self._derived_seed(*parts))
        return float(uniform.next().value())
