"""Synthetic financial data: deterministic generator + database seeding (owner A).

``seed_database`` is resolved lazily so ``python -m app.data.seed`` does not import the
module twice.
"""
from __future__ import annotations

from typing import Any

from app.data.generator import (
    CLOSE_PERIODS,
    EXPECTED_PLANTED,
    GeneratorConfig,
    SyntheticDataset,
    generate_dataset,
)

__all__ = [
    "CLOSE_PERIODS",
    "EXPECTED_PLANTED",
    "GeneratorConfig",
    "SyntheticDataset",
    "generate_dataset",
    "seed_database",
]


def __getattr__(name: str) -> Any:
    if name == "seed_database":
        from app.data.seed import seed_database

        return seed_database
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
