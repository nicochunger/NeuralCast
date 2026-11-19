"""Shared growth allowance for episode planning estimates and recorded speech."""

GROWTH_PERCENT = 15


def budget_with_tolerance(budget: int | float) -> float:
    """Allow modest story growth beyond an initial word or duration estimate."""
    return budget * (100 + GROWTH_PERCENT) / 100
