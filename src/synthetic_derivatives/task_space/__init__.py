"""Seven-dimensional task grammar and design-catalog compatibility."""

from synthetic_derivatives.task_space.models import (
    AXES,
    F_LEVELS,
    LEGACY_AXES,
    TaskCoordinates,
    TaskSpec,
    TaskSpecV3,
    coordinate_rank,
    migrate_legacy_six_axis_coordinates,
    migrate_legacy_six_axis_task,
)
from synthetic_derivatives.task_space.registry import (
    CompatibilityDecision,
    TaskSpaceRegistry,
)

__all__ = [
    "AXES",
    "CompatibilityDecision",
    "F_LEVELS",
    "LEGACY_AXES",
    "TaskCoordinates",
    "TaskSpaceRegistry",
    "TaskSpec",
    "TaskSpecV3",
    "coordinate_rank",
    "migrate_legacy_six_axis_coordinates",
    "migrate_legacy_six_axis_task",
]
