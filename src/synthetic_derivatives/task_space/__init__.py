"""Six-dimensional task grammar and compatibility decisions."""

from synthetic_derivatives.task_space.models import AXES, TaskCoordinates, TaskSpec
from synthetic_derivatives.task_space.registry import (
    CompatibilityDecision,
    TaskSpaceRegistry,
)

__all__ = [
    "AXES",
    "CompatibilityDecision",
    "TaskCoordinates",
    "TaskSpaceRegistry",
    "TaskSpec",
]
