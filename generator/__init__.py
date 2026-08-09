"""Procedural, uniquely-solvable One Line puzzle generation engine."""

from .engine import GenerationCancelled, GenerationFailure, generate_level
from .models import (
    GENERATOR_VERSION,
    DifficultyTier,
    GeneratedLevel,
    GenerationSettings,
    OutputOptions,
    ShapeMode,
)

__all__ = [
    "GENERATOR_VERSION",
    "DifficultyTier",
    "GeneratedLevel",
    "GenerationCancelled",
    "GenerationFailure",
    "GenerationSettings",
    "OutputOptions",
    "ShapeMode",
    "generate_level",
]

