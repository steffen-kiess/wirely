"""Validate TwoN settings, at compile time and when loading."""


def check_code(code: str) -> None:
    """Check a code for CodeEnteredEvent."""
    if len(code) > 15 or not all('0' <= digit <= '9' for digit in code):
        raise ValueError('TwoN code must contain at most 15 ASCII digits')
