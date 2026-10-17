"""Grid coordinates for layout and pixel conversion at the view boundary."""

from __future__ import annotations

import math


STEP = 18


def snap(value: float) -> float:
    return float(round(value))


def size(value: float) -> float:
    return float(math.ceil(value))


def from_pixels(x: float, y: float) -> tuple[float, float]:
    return x / STEP, y / STEP
