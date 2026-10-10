"""Grid coordinates for layout and pixel conversion at the view boundary."""

import math


STEP = 18


def snap(value):
    return float(round(value))


def size(value):
    return float(math.ceil(value))


def from_pixels(x, y):
    return x / STEP, y / STEP
