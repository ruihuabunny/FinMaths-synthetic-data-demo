"""Observable, non-reasoning reference trajectory records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import RuntimeReplayResult


_EVENT_TYPES = {
    "observation",
    "action",
    "tool_result",
    "decision",
    "artifact",
    "submission",
}


@dataclass(frozen=True)
class TrajectoryEvent:
    step_id: int
    event_type: str
    summary: str
    status: str = "completed"
    tool_name: str | None = None
    public_reference: str | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.step_id) is not int or self.step_id < 1:
            raise ValueError("trajectory step_id must be positive")
        if self.event_type not in _EVENT_TYPES:
            raise ValueError("trajectory event_type is invalid")
        if not isinstance(self.summary, str) or not self.summary:
            raise ValueError("trajectory summary must be non-empty")
        if self.status not in {"started", "completed", "failed"}:
            raise ValueError("trajectory status is invalid")
        if self.digest is not None and (
            not isinstance(self.digest, str)
            or len(self.digest) != 64
            or any(character not in "0123456789abcdef" for character in self.digest)
        ):
            raise ValueError("trajectory digest must be lowercase SHA-256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "event_type": self.event_type,
            "tool_name": self.tool_name,
            "public_reference": self.public_reference,
            "summary": self.summary,
            "status": self.status,
            "digest": self.digest,
        }


def reference_trajectory(result: RuntimeReplayResult) -> tuple[TrajectoryEvent, ...]:
    """Describe only observable inputs, actions, artifacts, and submission."""

    return (
        TrajectoryEvent(
            1,
            "observation",
            "Read the public prompt, runtime contract, and submission schema.",
            public_reference="public/",
        ),
        TrajectoryEvent(
            2,
            "action",
            "Requested the public underlying-market rows once.",
            tool_name="query_greeks_underlying_market_v2",
        ),
        TrajectoryEvent(
            3,
            "tool_result",
            "Received the eight public spot and pricing-context rows.",
            tool_name="query_greeks_underlying_market_v2",
            digest=result.underlying_market_digest,
        ),
        TrajectoryEvent(
            4,
            "action",
            "Requested all canonically ordered public option rows once.",
            tool_name="query_greeks_option_quotes_v2",
        ),
        TrajectoryEvent(
            5,
            "tool_result",
            "Received the 160 public option-contract and quote rows.",
            tool_name="query_greeks_option_quotes_v2",
            digest=result.option_quotes_digest,
        ),
        TrajectoryEvent(
            6,
            "action",
            "Matched option rows to underlying-market rows with the public join key.",
        ),
        TrajectoryEvent(
            7,
            "artifact",
            "Used the audited standard-library BSM implementation.",
            public_reference="reference/artifacts/solver.py",
        ),
        TrajectoryEvent(
            8,
            "action",
            "Computed Decimal quote midpoints and fixed-schedule IV roots.",
        ),
        TrajectoryEvent(
            9,
            "action",
            "Computed five unit Greeks from each unrounded final root.",
        ),
        TrajectoryEvent(
            10,
            "decision",
            "Validated row identities, canonical decimal strings, and output order.",
            public_reference="reference/artifacts/self_check.json",
        ),
        TrajectoryEvent(
            11,
            "submission",
            "Submitted the one-file canonical result once.",
            tool_name="submit_greeks_submission_v2",
            public_reference="reference/final_submission.json",
            digest=result.submission_digest,
        ),
    )


__all__ = ["TrajectoryEvent", "reference_trajectory"]
