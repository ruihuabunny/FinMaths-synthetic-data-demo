#!/usr/bin/env python3
"""Deterministic F2A v4 fixture, audit, and end-to-end smoke command."""

from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_HALF_EVEN
from hashlib import sha256
import json
from math import erf, exp, log, sqrt
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from synthetic_derivatives.authoring.f2a_child_materializer import (  # noqa: E402
    find_spec_for_signature,
    load_frozen_parent_fixture,
    materialize_verified_child,
    run_reachability_audit,
)
from synthetic_derivatives.solver.f2a import build_submission  # noqa: E402
from synthetic_derivatives.verifier.f2a import (  # noqa: E402
    validate_lineage_v4,
    verify_submission,
)


DEFAULT_FIXTURE = REPOSITORY_ROOT / "tests/fixtures/f2a/v4_parent.json"
DEFAULT_MANIFEST = REPOSITORY_ROOT / "tests/fixtures/f2a/v4_parent.manifest.json"
DEFAULT_AUDIT = REPOSITORY_ROOT / "datasets/manifests/splits/f2a_v4.json"


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _bsm_price(
    *,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
    call_put: str,
) -> float:
    d1 = (
        log(spot / strike)
        + (rate - dividend_yield + 0.5 * volatility * volatility) * years
    ) / (volatility * sqrt(years))
    d2 = d1 - volatility * sqrt(years)
    call = (
        spot * exp(-dividend_yield * years) * _normal_cdf(d1)
        - strike * exp(-rate * years) * _normal_cdf(d2)
    )
    if call_put == "call":
        return call
    return call - spot * exp(-dividend_yield * years) + strike * exp(-rate * years)


def _cent(value: float) -> float:
    return float(
        Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    )


def build_fixture_payload() -> dict[str, Any]:
    """Build one complete 4-expiry x 7-strike x call/put CI parent slice."""

    snapshot_id = "DERIVATIVES-METALS-F2A-V4-CI-FIXTURE-v1"
    valuation_date = "2026-08-03"
    underlying_id = "SYNTH-METAL-F2A-CI"
    spot = 100.0
    rate = 0.03
    dividend_yield = 0.01
    volatility = 0.20
    multiplier = 100.0
    expiry_days = (
        ("2026-09-02", 30),
        ("2026-10-02", 60),
        ("2026-11-02", 90),
        ("2027-02-01", 182),
    )
    strikes = (70, 80, 90, 100, 110, 120, 130)
    expiry_inputs: dict[str, Any] = {}
    quotes: list[dict[str, Any]] = []
    for expiry, days in expiry_days:
        years = days / 365.0
        expiry_inputs[expiry] = {
            "discount_factor": exp(-rate * years),
            "integrated_dividend_yield": dividend_yield * years,
        }
        for strike in strikes:
            for call_put in ("call", "put"):
                mid = _cent(
                    _bsm_price(
                        spot=spot,
                        strike=float(strike),
                        years=years,
                        rate=rate,
                        dividend_yield=dividend_yield,
                        volatility=volatility,
                        call_put=call_put,
                    )
                )
                bid = _cent(max(0.0, mid - 0.05))
                ask = _cent(mid + 0.05)
                quotes.append(
                    {
                        "option_id": (
                            f"F2A-CI-{expiry}-{strike}-{call_put.upper()}"
                        ),
                        "valuation_date": valuation_date,
                        "underlying_id": underlying_id,
                        "expiry": expiry,
                        "strike": float(strike),
                        "call_put": call_put,
                        "exercise_style": "european",
                        "settlement_type": "cash",
                        "currency": "USD",
                        "contract_multiplier": multiplier,
                        "bid": bid,
                        "ask": ask,
                    }
                )
    return {
        "task_id": "f2a-v4-ci-parent-control",
        "snapshot_id": snapshot_id,
        "snapshot_revision": 1,
        "status": "FROZEN",
        "slices": [
            {
                "snapshot_id": snapshot_id,
                "snapshot_revision": 1,
                "valuation_date": valuation_date,
                "underlying_id": underlying_id,
                "spot": spot,
                "currency": "USD",
                "expiry_inputs": expiry_inputs,
                "quotes": quotes,
            }
        ],
    }


def _write_json(path: Path, payload: Any) -> bytes:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return encoded


def command_build_fixture(args: argparse.Namespace) -> int:
    fixture_path = Path(args.fixture)
    manifest_path = Path(args.manifest)
    payload = build_fixture_payload()
    encoded = _write_json(fixture_path, payload)
    manifest = {
        "fixture_role": "tracked_ci_only_not_production_parent_fallback",
        "snapshot_id": payload["snapshot_id"],
        "snapshot_revision": payload["snapshot_revision"],
        "status": payload["status"],
        "schema": "solver-visible-f2a-json-v1",
        "model": "flat_continuous_rate_dividend_BSM_clean_quote_fixture",
        "rate": 0.03,
        "dividend_yield": 0.01,
        "volatility": 0.20,
        "option_half_spread_usd": 0.05,
        "option_tick_usd": 0.01,
        "underlying_tick_usd": 0.01,
        "complete_chain": {
            "expiry_count": 4,
            "strike_count_per_expiry": 7,
            "option_type_count": 2,
            "quote_count": 56,
            "calendar_candidate_count": 42,
        },
        "sha256": sha256(encoded).hexdigest(),
        "materialization_command": (
            "python scripts/materialize_f2a.py build-fixture"
        ),
    }
    _write_json(manifest_path, manifest)
    print(f"wrote frozen F2A CI fixture: {fixture_path}")
    print(f"wrote fixture manifest: {manifest_path}")
    return 0


def command_audit(args: argparse.Namespace) -> int:
    parent = load_frozen_parent_fixture(args.fixture, args.manifest)
    audit = run_reachability_audit(parent.slices[0])
    publication_signatures = [
        signature
        for signature in audit["requested_signature_order"]
        if audit["results"][signature]["reachable"]
    ]
    artifact = {
        "schema_version": "4.0.0",
        "dataset_config_id": "f2a-dataset-v4",
        "status": "EXECUTABLE_REACHABILITY_PROVED_SCOPE",
        "parent_grouping_key": "DERIVATIVES-METALS-F2A-V4-CI-FIXTURE-v1",
        "split_names": ["audit"],
        "usable_train_validation_test_split": False,
        "publication_signatures": publication_signatures,
        "publication_task_count": len(publication_signatures),
        "reachability_audit": audit,
    }
    _write_json(Path(args.output), artifact)
    for signature in audit["requested_signature_order"]:
        result = audit["results"][signature]
        print(
            signature,
            "reachable" if result["reachable"] else "unreachable",
            result["operator"],
            result["exact_integer_tick_intervals"],
        )
    return 0


def command_smoke(args: argparse.Namespace) -> int:
    parent = load_frozen_parent_fixture(args.fixture, args.manifest)
    spec = find_spec_for_signature(parent.slices[0], args.signature)
    output_root = Path(args.output_root)
    child_path = output_root / f"child-{args.signature}.json"
    lineage_path = output_root / f"lineage-{args.signature}.json"
    child, lineage = materialize_verified_child(
        parent=parent,
        spec=spec,
        requested_signature=args.signature,
        task_id=f"f2a-v4-smoke-{args.signature}",
        output_path=child_path,
        lineage_path=lineage_path,
    )
    submission = build_submission(child.to_dict())
    submission_path = output_root / f"submission-{args.signature}.json"
    _write_json(submission_path, submission)
    validate_lineage_v4(
        lineage,
        schema_root=REPOSITORY_ROOT / "schemas",
        public_child=child,
    )
    report = verify_submission(
        public_child_path=child_path,
        submission=submission,
        schema_root=REPOSITORY_ROOT / "schemas",
        variant_path=(
            REPOSITORY_ROOT
            / "configs/variants/bsm_arbitrage_finding_f2a_v4.json"
        ),
    )
    if not report.accepted:
        raise SystemExit("trusted verifier rejected the Solver submission")
    print(
        json.dumps(
            {
                "accepted": report.accepted,
                "realized_signature": report.realized_signature,
                "orm": report.expected_orm,
                "child": str(child_path),
                "lineage": str(lineage_path),
                "submission": str(submission_path),
            },
            sort_keys=True,
        )
    )
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-fixture")
    build.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    build.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    build.set_defaults(handler=command_build_fixture)

    audit = subparsers.add_parser("audit")
    audit.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    audit.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    audit.add_argument("--output", default=str(DEFAULT_AUDIT))
    audit.set_defaults(handler=command_audit)

    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    smoke.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    smoke.add_argument("--signature", default="001")
    smoke.add_argument("--output-root", default="/tmp/f2a-v4-smoke")
    smoke.set_defaults(handler=command_smoke)
    return result


def main() -> int:
    args = parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
