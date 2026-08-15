"""
color_balance.py -- Qt-free math for a "color balance wheel" control: the
2D-puck-plus-master-slider convention DaVinci Resolve/Baselight/Flame use
for Lift/Gamma/Gain (and equally applicable to ASC CDL's Slope/Offset/
Power, since all of these are just "one independent value per R/G/B
channel" underneath). Dragging the puck tints the three channels apart
from each other (a "balance") WITHOUT changing their average (the
"master") -- that's the whole point of separating the two controls: the
wheel answers "which direction/how much of a color cast," a separate
slider answers "brighter or darker/more or less overall," and the two
never fight each other.

THE PROJECTION: R/G/B axes are placed 120 degrees apart around the wheel
(R at 90 degrees/straight up, G at 210 degrees, B at 330 degrees -- an
arbitrary but fixed choice, matching the common "R at top" convention).
Projecting a puck position (x, y) onto each axis (a dot product with that
axis's unit vector) gives that channel's balance. Because the three axis
vectors are symmetric (120 degrees apart, unit length), they sum to
(0, 0) -- which means the three balances ALWAYS sum to exactly zero,
whatever the puck position is. That's the mathematical reason moving the
wheel can never silently change the master value: a sum-zero perturbation
added equally to three channels leaves their average untouched.

THE INVERSE (recovering a wheel position from three arbitrary R/G/B
deltas, e.g. when a stage loaded from a session file has independent
values that didn't come from this wheel at all): since the three axis
vectors form what's called a "tight frame" in 2D (equally spaced unit
vectors), the least-squares inverse has a closed form -- pos = (2/3) *
sum(axis_i * balance_i) -- see wheel_from_channel_balance's docstring for
the derivation. This is exact when the three deltas already sum to zero
(i.e. really did come from a wheel position); when they don't (arbitrary
independent R/G/B), it's the best-fit wheel position for whatever part of
that value CAN be expressed as a pure balance, with the leftover
(non-zero-summing) part folded into the master via channel_balance's own
mean-subtraction -- see rgb_to_master_and_balance.

No PySide6 import here, fully testable without a display -- same rule as
every other backend module in this app (aces_drt.py, printer_lights.py,
cdl.py, ...). color_wheel_widget.py is the thin Qt paint/drag layer on
top of this.
"""
import math

import numpy as np

CHANNELS = ("R", "G", "B")

# Fixed axis angles (degrees, standard screen convention: 0=right,
# 90=up, measured counterclockwise) -- see module docstring for why
# these specific three (120 degrees apart, R at top) were chosen.
_AXIS_ANGLES_DEG = {"R": 90.0, "G": 210.0, "B": 330.0}


def _axis_vector(channel):
    angle = math.radians(_AXIS_ANGLES_DEG[channel])
    return np.array([math.cos(angle), math.sin(angle)])


# Precomputed once -- these never change.
_AXES = {ch: _axis_vector(ch) for ch in CHANNELS}


def channel_balance_from_wheel(x, y):
    """(x, y) puck position (need not be clamped to the unit circle by this
    function -- callers/widgets clamp for DISPLAY, but the math here is
    valid for any radius) -> {"R": bal_r, "G": bal_g, "B": bal_b}, each the
    projection of (x,y) onto that channel's axis. Always sums to exactly
    zero (to floating point precision) -- see module docstring."""
    pos = np.array([float(x), float(y)])
    return {ch: float(np.dot(pos, _AXES[ch])) for ch in CHANNELS}


def wheel_from_channel_balance(balance_r, balance_g, balance_b):
    """Inverse of channel_balance_from_wheel: three per-channel balance
    values -> best-fit (x, y). Exact (round-trips to the original (x,y) to
    within floating point precision) whenever balance_r+balance_g+balance_b
    == 0, which is always true for a balance that actually came from
    channel_balance_from_wheel. The (2/3) factor is the tight-frame
    pseudo-inverse constant for 3 equally spaced unit vectors in 2D: for N
    unit vectors spaced 2*pi/N apart, sum_i(axis_i (x) axis_i) == (N/2) *
    Identity (a standard tight-frame identity), so the least-squares
    inverse of "project onto each axis" is "sum each axis weighted by its
    projection, scaled by 2/N" -- here N=3, so 2/3."""
    balances = {"R": balance_r, "G": balance_g, "B": balance_b}
    pos = sum((2.0 / 3.0) * balances[ch] * _AXES[ch] for ch in CHANNELS)
    return float(pos[0]), float(pos[1])


def rgb_from_master_and_wheel(master, x, y, strength=1.0):
    """The forward direction a UI actually drives: a master value (applies
    equally to all three channels) plus a wheel position (tints them apart
    from each other), combined into (R, G, B). `strength` scales how much
    balance a given radius produces -- e.g. strength=1.0 means the puck at
    the wheel's very edge along a pure primary axis shifts that channel by
    1.0 away from the other two; callers pick `strength` to match
    whatever numeric range makes sense for the parameter in question (see
    color_wheel_widget.py's per-parameter WHEEL_STRENGTH constants)."""
    balance = channel_balance_from_wheel(x, y)
    return tuple(master + strength * balance[ch] for ch in CHANNELS)


def rgb_to_master_and_wheel(r, g, b, strength=1.0):
    """Inverse of rgb_from_master_and_wheel: (R, G, B) -> (master, x, y).
    master is the plain mean (the only value consistent with "the wheel
    contributes a sum-zero perturbation" -- see module docstring), and the
    wheel position is the best-fit puck for whatever's left over. Exact
    round trip whenever (r,g,b) actually came from
    rgb_from_master_and_wheel; for arbitrary independent R/G/B this is a
    reasonable best-fit rather than a unique inverse (three numbers can't
    always be represented as one master + one 2D position, since that's
    only 3 degrees of freedom out of 3 -- but only when unconstrained;
    once you fix the sum-zero shape, 2 of those DOF are the wheel and the
    UI necessarily approximates anything else via the master alone)."""
    master = (r + g + b) / 3.0
    if strength == 0:
        return master, 0.0, 0.0
    balance_r = (r - master) / strength
    balance_g = (g - master) / strength
    balance_b = (b - master) / strength
    x, y = wheel_from_channel_balance(balance_r, balance_g, balance_b)
    return master, x, y


def wheel_from_rgb_given_master(r, g, b, master, strength=1.0):
    """Like rgb_to_master_and_wheel, but for a control where master is its
    own independently stored value (e.g. OCIO GradingRGBM's 4th
    component) rather than something derived from mean(r, g, b) -- ASC
    CDL's slope/offset/power have no separate master field at all, so
    rgb_to_master_and_wheel deriving master AS the mean is the only
    sensible choice there; GradingRGBM keeps R, G, B, and M as four
    genuinely independent numbers, so recomputing master from R/G/B here
    would silently overwrite whatever the user (or a saved session) had
    actually set it to. This only ever returns the best-fit wheel
    position (x, y) for R/G/B's spread around the GIVEN master -- never
    touches or returns master itself, so a caller can safely leave
    whatever field/spinbox holds it completely alone. Exact whenever
    (r, g, b) really did come from rgb_from_master_and_wheel(master, x,
    y, strength) with this SAME master; otherwise (R/G/B set independently
    of the wheel, e.g. typed by hand, or master edited afterward without
    re-deriving R/G/B) this is the best-fit position for whatever part of
    the spread a 2D wheel can express, same "not a unique inverse for
    arbitrary input" caveat rgb_to_master_and_wheel's own docstring
    covers."""
    if strength == 0:
        return 0.0, 0.0
    balance_r = (r - master) / strength
    balance_g = (g - master) / strength
    balance_b = (b - master) / strength
    return wheel_from_channel_balance(balance_r, balance_g, balance_b)


def clamp_to_unit_circle(x, y):
    """Clamp (x, y) to the closed unit disk -- used by the widget so the
    puck never visually leaves the wheel, and by anything that wants to
    cap wheel-driven balance magnitude at `strength` regardless of how far
    a raw mouse position would otherwise put it."""
    radius = math.hypot(x, y)
    if radius <= 1.0 or radius == 0.0:
        return float(x), float(y)
    scale = 1.0 / radius
    return float(x * scale), float(y * scale)
