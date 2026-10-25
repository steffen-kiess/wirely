"""Inclusive limits for individual runtime values, and the check of types against them."""

from ..types import Type

MAX_STORAGE_SIZE = 128
MAX_NESTING_DEPTH = 16
MAX_TOTAL_MEMBER_COUNT = 128


def _limit_violation(value_type: Type) -> tuple[str, int, int] | None:
    """Return the first exceeded limit of a type with a runtime representation, or None."""
    depth = value_type.nesting_depth
    if depth > MAX_NESTING_DEPTH:
        return 'nesting depth', depth, MAX_NESTING_DEPTH
    count = value_type.total_member_count
    if count > MAX_TOTAL_MEMBER_COUNT:
        return 'total member count', count, MAX_TOTAL_MEMBER_COUNT
    size = value_type.size
    if size > MAX_STORAGE_SIZE:
        return 'storage size', size, MAX_STORAGE_SIZE
    return None


def is_runtime_type(value_type: Type) -> bool:
    """Whether values have a representation within the runtime limits."""
    return value_type.has_runtime_representation and _limit_violation(value_type) is None


def runtime_type_error(value_type: Type) -> str | None:
    """Return the reason for runtime ineligibility, or None."""
    if not value_type.has_runtime_representation:
        return value_type.runtime_representation_error()
    violation = _limit_violation(value_type)
    if violation is None:
        return None
    metric, actual, maximum = violation
    if metric == 'storage size':
        return f'{metric} {actual} bytes exceeds {maximum} bytes'
    return f'{metric} {actual} exceeds {maximum}'
