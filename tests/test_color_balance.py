"""Tests for color_balance.py -- run directly: python3 -m tests.test_color_balance"""
import math

import color_balance as cb


def test_center_wheel_is_zero_balance():
    balance = cb.channel_balance_from_wheel(0.0, 0.0)
    assert balance == {"R": 0.0, "G": 0.0, "B": 0.0}
    print("channel_balance_from_wheel(0,0): all channels zero -- OK")


def test_balance_always_sums_to_zero():
    for x, y in [(1, 0), (0, 1), (-1, 0), (0.3, -0.7), (-0.6, 0.9), (2.0, -3.0)]:
        balance = cb.channel_balance_from_wheel(x, y)
        total = sum(balance.values())
        assert abs(total) < 1e-9, f"({x},{y}) -> {balance}, sums to {total}"
    print("channel_balance_from_wheel: R+G+B always sums to exactly zero, "
          "for any puck position -- OK")


def test_pure_axis_position_isolates_that_channel_as_the_extreme():
    """Pushing the puck straight along one channel's own axis should make
    that channel's balance the largest of the three (positive), with the
    other two negative and equal to each other -- confirms the axis
    placement actually matches which channel is which, not just that the
    math is self-consistent."""
    for channel, angle_deg in (("R", 90.0), ("G", 210.0), ("B", 330.0)):
        angle = math.radians(angle_deg)
        x, y = math.cos(angle), math.sin(angle)
        balance = cb.channel_balance_from_wheel(x, y)
        others = [balance[c] for c in ("R", "G", "B") if c != channel]
        assert balance[channel] > 0, balance
        assert all(o < 0 for o in others), balance
        assert abs(others[0] - others[1]) < 1e-9, balance
        assert balance[channel] > others[0], balance
        print(f"Puck at {channel}'s own axis ({angle_deg} deg): {channel} is the "
              f"positive extreme, other two are equal and negative -- OK "
              f"({balance})")


def test_wheel_from_channel_balance_round_trips():
    for x, y in [(0.0, 0.0), (0.5, 0.2), (-0.3, 0.9), (1.0, 0.0), (-0.8, -0.4)]:
        balance = cb.channel_balance_from_wheel(x, y)
        x2, y2 = cb.wheel_from_channel_balance(balance["R"], balance["G"], balance["B"])
        assert abs(x - x2) < 1e-9 and abs(y - y2) < 1e-9, (x, y, x2, y2)
    print("wheel_from_channel_balance: exact inverse of channel_balance_from_wheel "
          "for every balance that actually came from a wheel position -- OK")


def test_rgb_from_master_and_wheel_center_is_pure_master():
    r, g, b = cb.rgb_from_master_and_wheel(1.4, 0.0, 0.0, strength=1.0)
    assert r == g == b == 1.4
    print("rgb_from_master_and_wheel: centered wheel (0,0) gives R=G=B=master "
          "exactly, regardless of strength -- OK")


def test_rgb_from_master_and_wheel_preserves_master_average():
    for master, x, y, strength in [(1.0, 0.5, -0.3, 1.0), (0.0, 0.9, 0.9, 0.3), (2.5, -1.0, 0.0, 2.0)]:
        r, g, b = cb.rgb_from_master_and_wheel(master, x, y, strength=strength)
        assert abs((r + g + b) / 3.0 - master) < 1e-9, (master, x, y, r, g, b)
    print("rgb_from_master_and_wheel: (R+G+B)/3 always equals the master value, "
          "confirming a wheel move never changes the average -- OK")


def test_rgb_to_master_and_wheel_round_trips():
    cases = [
        (1.0, 0.0, 0.0, 1.0),
        (0.5, 0.3, -0.6, 1.0),
        (2.0, -0.8, 0.2, 0.5),
        (0.0, 0.0, 0.0, 1.0),
    ]
    for master, x, y, strength in cases:
        r, g, b = cb.rgb_from_master_and_wheel(master, x, y, strength=strength)
        m2, x2, y2 = cb.rgb_to_master_and_wheel(r, g, b, strength=strength)
        assert abs(m2 - master) < 1e-9
        assert abs(x2 - x) < 1e-9 and abs(y2 - y) < 1e-9
        print(f"round trip: master={master}, wheel=({x},{y}), strength={strength} "
              f"-> rgb=({r:.4f},{g:.4f},{b:.4f}) -> back to master={m2:.4f}, "
              f"wheel=({x2:.4f},{y2:.4f}) -- OK")


def test_rgb_to_master_and_wheel_arbitrary_values_gives_sensible_master():
    """Three completely independent R/G/B values (not necessarily reachable
    by any wheel position) should still decompose to a master equal to
    their plain mean -- the one value a master-plus-balance representation
    can always agree on exactly, even when the wheel position itself is
    only a best fit (see the function's own docstring)."""
    r, g, b = 0.2, 0.9, 1.7
    master, x, y = cb.rgb_to_master_and_wheel(r, g, b, strength=1.0)
    assert abs(master - (r + g + b) / 3.0) < 1e-9
    print(f"rgb_to_master_and_wheel({r},{g},{b}): master={master:.4f} matches "
          "the plain mean exactly -- OK")


def test_wheel_from_rgb_given_master_round_trips_when_master_matches_mean():
    """When the given master happens to equal mean(r,g,b) (i.e. exactly
    the situation rgb_to_master_and_wheel already handles), the wheel
    position must match rgb_to_master_and_wheel's own -- same underlying
    balance math, just with the master supplied rather than derived."""
    r, g, b = 1.5, 0.8, 1.1
    master = (r + g + b) / 3.0
    x, y = cb.wheel_from_rgb_given_master(r, g, b, master, strength=1.0)
    _m2, x2, y2 = cb.rgb_to_master_and_wheel(r, g, b, strength=1.0)
    assert abs(x - x2) < 1e-9 and abs(y - y2) < 1e-9
    print("wheel_from_rgb_given_master: matches rgb_to_master_and_wheel's "
          "own wheel position when the given master equals mean(r,g,b) -- OK")


def test_wheel_from_rgb_given_master_ignores_an_independent_master():
    """The whole point: master here does NOT have to equal mean(r,g,b) --
    e.g. GradingRGBM's M really can differ from the plain average of R/G/B
    (both are independently stored). The wheel position must reflect the
    spread around the GIVEN master, not silently recompute a different
    one."""
    r, g, b = 1.0, 1.0, 1.0  # R=G=B, so ANY spread relative to a master != 1.0 is uniform
    master = 1.5  # independently set, does NOT equal mean(r,g,b) == 1.0
    x, y = cb.wheel_from_rgb_given_master(r, g, b, master, strength=1.0)
    # A uniform (r-master) shift on all three channels is a pure master
    # move, not a balance -- so the best-fit wheel position is the origin.
    assert abs(x) < 1e-9 and abs(y) < 1e-9, (x, y)
    print("wheel_from_rgb_given_master: an independently-set master that "
          "differs from mean(r,g,b) still gives a sensible (here, "
          "center-of-wheel) balance position -- OK")


def test_wheel_from_rgb_given_master_round_trips_through_rgb_from_master_and_wheel():
    """Forward/inverse pair, master held fixed throughout (the real usage
    pattern -- see stack_panel.py's Grading Primary/Tone wheel sync,
    which never re-derives master from R/G/B the way CDL's does)."""
    master = 0.7
    x_in, y_in = 0.4, -0.5
    strength = 1.0
    r, g, b = cb.rgb_from_master_and_wheel(master, x_in, y_in, strength=strength)
    x_out, y_out = cb.wheel_from_rgb_given_master(r, g, b, master, strength=strength)
    assert abs(x_out - x_in) < 1e-9 and abs(y_out - y_in) < 1e-9
    print("wheel_from_rgb_given_master: exact inverse of "
          "rgb_from_master_and_wheel when the SAME master is held fixed "
          "on both sides -- OK")


def test_wheel_from_rgb_given_master_zero_strength():
    x, y = cb.wheel_from_rgb_given_master(2.0, 0.5, 1.0, master=1.0, strength=0)
    assert (x, y) == (0.0, 0.0)
    print("wheel_from_rgb_given_master: strength=0 returns the wheel "
          "center rather than dividing by zero -- OK")


def test_clamp_to_unit_circle():
    assert cb.clamp_to_unit_circle(0.0, 0.0) == (0.0, 0.0)
    x, y = cb.clamp_to_unit_circle(3.0, 4.0)  # radius 5
    assert abs(math.hypot(x, y) - 1.0) < 1e-9
    assert abs(x - 0.6) < 1e-9 and abs(y - 0.8) < 1e-9  # direction preserved
    # already inside the circle -- untouched
    assert cb.clamp_to_unit_circle(0.3, -0.2) == (0.3, -0.2)
    print("clamp_to_unit_circle: leaves in-circle points untouched, scales "
          "out-of-circle points to the rim while preserving direction -- OK")


def test_strength_scales_balance_magnitude_linearly():
    x, y = 1.0, 0.0
    balance_at_1 = cb.rgb_from_master_and_wheel(0.0, x, y, strength=1.0)
    balance_at_2 = cb.rgb_from_master_and_wheel(0.0, x, y, strength=2.0)
    for v1, v2 in zip(balance_at_1, balance_at_2):
        assert abs(v2 - 2 * v1) < 1e-9
    print("rgb_from_master_and_wheel: doubling strength exactly doubles the "
          "balance magnitude at a fixed wheel position -- OK")


if __name__ == "__main__":
    test_center_wheel_is_zero_balance()
    test_balance_always_sums_to_zero()
    test_pure_axis_position_isolates_that_channel_as_the_extreme()
    test_wheel_from_channel_balance_round_trips()
    test_rgb_from_master_and_wheel_center_is_pure_master()
    test_rgb_from_master_and_wheel_preserves_master_average()
    test_rgb_to_master_and_wheel_round_trips()
    test_rgb_to_master_and_wheel_arbitrary_values_gives_sensible_master()
    test_wheel_from_rgb_given_master_round_trips_when_master_matches_mean()
    test_wheel_from_rgb_given_master_ignores_an_independent_master()
    test_wheel_from_rgb_given_master_round_trips_through_rgb_from_master_and_wheel()
    test_wheel_from_rgb_given_master_zero_strength()
    test_clamp_to_unit_circle()
    test_strength_scales_balance_magnitude_linearly()
    print()
    print("All color_balance tests passed.")
