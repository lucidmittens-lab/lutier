"""Tests for stack.py -- run directly: python3 test_stack.py

Fully Qt-free. Cross-checks the new unified Stack against the existing,
already-tested single-purpose bake functions (bake_retargeted_lut,
bake_printer_lights_on_lut, bake_aces_output_transform_to_lut3d,
concatenate_luts_with_opacity) it's meant to subsume -- the point of the
Stack is that it produces IDENTICAL results to those, just reorderable and
composable in one list, not a new/different algorithm to distrust.
"""
from pathlib import Path

import numpy as np
import colour

import stack as st
from app_logic import bake_retargeted_lut, create_identity_lut, resample_lut
from printer_lights import bake_printer_lights_on_lut
import aces_drt
import cdl
import colorspace_convert as csc

SAMPLE_LUT_PATH = str(Path(__file__).parent / "samples" / "roundtrip_check.cube")


class Gain:
    """y = x * factor -- a plain .apply(RGB) object, not a colour.LUT3D,
    to confirm stack.py works with ANY lut-like, not just LUT3D instances
    (same generality app_logic.concatenate_luts already has)."""
    def __init__(self, factor, name="gain"):
        self.factor = factor
        self.name = name

    def apply(self, RGB):
        return np.asarray(RGB, dtype=float) * self.factor


class CountingGain:
    """Same math as Gain, but counts how many times .apply is actually
    called -- lets StageCache tests assert an untouched stage's transform
    was NOT re-run on a later edit, not just that the final result still
    happens to come out correct."""
    def __init__(self, factor, name="counting_gain"):
        self.factor = factor
        self.name = name
        self.call_count = 0

    def apply(self, RGB):
        self.call_count += 1
        return np.asarray(RGB, dtype=float) * self.factor


def _make_gain_lut3d(gain, size=5, name="gain_lut"):
    """A REAL colour.LUT3D (not just a plain .apply() object) whose table
    is an exact linear scaling -- multilinear interpolation of a linear
    function is exact even off-grid, so lut.apply(x) == x * gain exactly
    for any input, which makes hand-computed opacity/order expectations
    possible without approximation error muddying the assertions."""
    identity = colour.LUT3D(size=size).table
    return colour.LUT3D(table=identity * gain, size=size, name=name)


# --- basic stage/stack plumbing ------------------------------------------

def test_stage_rejects_unknown_kind():
    try:
        st.Stage("not_a_real_kind")
        raise AssertionError("expected ValueError for an unknown stage kind")
    except ValueError:
        pass
    print("Stage(): unknown kind raises ValueError -- OK")


def test_make_lut_stage_retarget_requires_both_or_neither():
    lut = create_identity_lut(size=5)
    st.make_lut_stage(lut)  # neither -- fine
    st.make_lut_stage(lut, retarget_from="ACEScct", retarget_to="ACES2065-1")  # both -- fine
    for kwargs in ({"retarget_from": "ACEScct"}, {"retarget_to": "ACES2065-1"}):
        try:
            st.make_lut_stage(lut, **kwargs)
            raise AssertionError(f"expected ValueError for partial retarget kwargs {kwargs}")
        except ValueError:
            pass
    print("make_lut_stage: retarget_from/retarget_to must be given together -- OK")


def test_add_remove_find_move_stage():
    stack = st.Stack()
    s1 = st.make_lut_stage(create_identity_lut(size=5), label="first")
    s2 = st.make_lut_stage(create_identity_lut(size=5), label="second")
    stack.add_stage(s1)
    stack.add_stage(s2)
    assert [s.label for s in stack.stages] == ["first", "second"]
    assert stack.find_stage(s1.id) is s1

    stack.move_stage_up(s2.id)
    assert [s.label for s in stack.stages] == ["second", "first"]
    stack.move_stage_down(s2.id)
    assert [s.label for s in stack.stages] == ["first", "second"]

    stack.remove_stage(s1.id)
    assert [s.label for s in stack.stages] == ["second"]
    try:
        stack.remove_stage(s1.id)
        raise AssertionError("expected KeyError removing an already-removed stage")
    except KeyError:
        pass
    print("Stack: add/remove/find/move_stage_up/move_stage_down behave correctly -- OK")


# --- empty stack is a pure pass-through -----------------------------------

def test_empty_stack_bakes_to_plain_identity():
    stack = st.Stack(size=9)
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=9).table
    assert np.array_equal(baked.table, identity), \
        "an empty stack should bake to exactly the plain identity grid"
    print("bake_stack_to_lut3d: empty stack bakes to a plain identity LUT3D -- OK")


def test_empty_stack_apply_to_image_is_noop():
    stack = st.Stack()
    rng = np.random.default_rng(0)
    img = rng.random((6, 6, 3)).astype(np.float32)
    out = st.apply_stack_to_image_array(stack, img)
    assert np.allclose(out, img, atol=1e-6)
    print("apply_stack_to_image_array: empty stack leaves the image unchanged -- OK")


# --- single-stage stacks match the existing single-purpose bake functions -

def test_single_lut_stage_matches_direct_apply():
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    size = 9
    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(lut))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    direct = lut.apply(identity_grid)
    assert np.allclose(baked.table, direct, atol=1e-9)
    print("bake_stack_to_lut3d: single 'lut' stage matches a direct apply on "
          "the identity grid -- OK")


def test_lut_stage_with_retarget_matches_bake_retargeted_lut():
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    size = 9
    from_space, to_space = "ACEScct", "ACES2065-1"

    expected = bake_retargeted_lut(lut, from_space, to_space, size=size)

    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(lut, retarget_from=from_space, retarget_to=to_space))
    baked = st.bake_stack_to_lut3d(stack)

    assert np.allclose(baked.table, expected.table, atol=1e-9)
    print("bake_stack_to_lut3d: 'lut' stage with a retarget declaration "
          "matches app_logic.bake_retargeted_lut exactly -- OK")


def test_printer_lights_stage_matches_bake_printer_lights_on_lut():
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    size = 9
    r, g, b = 8, -4, 2
    working_space = "ACEScct"

    expected = bake_printer_lights_on_lut(
        lut, r, g, b, working_colourspace=working_space, size=size)

    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(lut))
    stack.add_stage(st.make_printer_lights_stage(r, g, b, working_colourspace=working_space))
    baked = st.bake_stack_to_lut3d(stack)

    assert np.allclose(baked.table, expected.table, atol=1e-9)
    print("bake_stack_to_lut3d: 'lut' stage followed by 'printer_lights' stage "
          "matches printer_lights.bake_printer_lights_on_lut exactly -- OK")


def test_aces_drt_stage_matches_bake_aces_output_transform_to_lut3d():
    if not aces_drt.OCIO_AVAILABLE:
        print("SKIPPED test_aces_drt_stage_matches_bake_aces_output_transform_to_lut3d "
              "-- PyOpenColorIO not installed")
        return
    size = 9
    display, view = aces_drt.get_default_display_and_view()
    working_space = "ACES2065-1"

    expected = aces_drt.bake_aces_output_transform_to_lut3d(
        working_space, display, view, size=size)

    stack = st.Stack(size=size)
    stack.add_stage(st.make_aces_drt_stage(working_space, display, view))
    baked = st.bake_stack_to_lut3d(stack)

    assert np.allclose(baked.table, expected.table, atol=1e-9)
    print("bake_stack_to_lut3d: single 'aces_drt' stage matches "
          "aces_drt.bake_aces_output_transform_to_lut3d exactly -- OK")


def test_cdl_stage_matches_direct_apply_cdl():
    if not cdl.OCIO_AVAILABLE:
        print("SKIPPED test_cdl_stage_matches_direct_apply_cdl -- PyOpenColorIO not installed")
        return
    size = 9
    slope, offset, power, sat = (1.2, 1.0, 0.9), (0.02, 0.0, -0.01), (1.0, 1.05, 1.0), 1.1

    stack = st.Stack(size=size)
    stack.add_stage(st.make_cdl_stage(slope=slope, offset=offset, power=power, sat=sat))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    expected = cdl.apply_cdl(identity_grid, slope=slope, offset=offset, power=power, sat=sat)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: single 'cdl' stage matches cdl.apply_cdl "
          "directly on the identity grid -- OK")


def test_cdl_stage_composes_after_a_lut_stage():
    if not cdl.OCIO_AVAILABLE:
        print("SKIPPED test_cdl_stage_composes_after_a_lut_stage -- PyOpenColorIO not installed")
        return
    size = 9
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    slope = (1.15, 1.0, 0.9)

    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(lut))
    stack.add_stage(st.make_cdl_stage(slope=slope, style="no_clip"))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    lut_output = lut.apply(identity_grid)
    expected = cdl.apply_cdl(lut_output, slope=slope, style="no_clip")
    assert np.allclose(baked.table, expected, atol=1e-9)

    # And confirm it's NOT the same as applying CDL before the LUT (order matters).
    wrong_order = lut.apply(cdl.apply_cdl(identity_grid, slope=slope, style="no_clip"))
    assert not np.allclose(baked.table, wrong_order, atol=1e-6)
    print("bake_stack_to_lut3d: 'cdl' stage after a 'lut' stage applies to "
          "that LUT's OUTPUT, confirmed distinct from the reverse order -- OK")


def test_colorspace_stage_matches_direct_apply_colorspace_conversion():
    if not csc.OCIO_AVAILABLE:
        print("SKIPPED test_colorspace_stage_matches_direct_apply_colorspace_conversion "
              "-- PyOpenColorIO not installed")
        return
    size = 9
    src, dst = "ACEScct", "ACEScg"

    stack = st.Stack(size=size)
    stack.add_stage(st.make_colorspace_stage(src, dst))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    expected = csc.apply_colorspace_conversion(identity_grid, src, dst)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: single 'colorspace' stage matches "
          "colorspace_convert.apply_colorspace_conversion directly on the "
          "identity grid -- OK")


def test_colorspace_stage_composes_after_a_lut_stage():
    if not csc.OCIO_AVAILABLE:
        print("SKIPPED test_colorspace_stage_composes_after_a_lut_stage "
              "-- PyOpenColorIO not installed")
        return
    size = 9
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    src, dst = "ACEScct", "ACEScg"

    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(lut))
    stack.add_stage(st.make_colorspace_stage(src, dst))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    lut_output = lut.apply(identity_grid)
    expected = csc.apply_colorspace_conversion(lut_output, src, dst)
    assert np.allclose(baked.table, expected, atol=1e-9)

    wrong_order = lut.apply(csc.apply_colorspace_conversion(identity_grid, src, dst))
    assert not np.allclose(baked.table, wrong_order, atol=1e-6)
    print("bake_stack_to_lut3d: 'colorspace' stage after a 'lut' stage "
          "applies to that LUT's OUTPUT, confirmed distinct from the "
          "reverse order -- OK")


# --- opacity semantics match concatenate_luts_with_opacity's proven rules -

def test_opacity_one_is_full_effect():
    size = 5
    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(_make_gain_lut3d(0.4, size=size), opacity=1.0))
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    assert np.allclose(baked.table, identity * 0.4, atol=1e-9)
    print("bake_stack_to_lut3d: opacity=1.0 gives the stage's full effect -- OK")


def test_opacity_zero_is_exact_noop():
    size = 5
    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(_make_gain_lut3d(0.4, size=size), opacity=0.0))
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    assert np.array_equal(baked.table, identity)
    print("bake_stack_to_lut3d: opacity=0.0 is an exact no-op -- OK")


def test_opacity_half_is_arithmetic_midpoint():
    size = 5
    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(_make_gain_lut3d(0.4, size=size), opacity=0.5))
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    expected = identity * 0.5 + (identity * 0.4) * 0.5  # midpoint of input (1.0x) and output (0.4x)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: opacity=0.5 lands exactly at the arithmetic "
          "midpoint between a stage's input and output -- OK")


def test_disabled_stage_is_exact_noop_regardless_of_params():
    size = 5
    stack = st.Stack(size=size)
    stage = st.make_lut_stage(_make_gain_lut3d(0.4, size=size), opacity=1.0)
    stage.enabled = False
    stack.add_stage(stage)
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    assert np.array_equal(baked.table, identity), \
        "a disabled stage should have zero effect, same as not being in the stack at all"
    print("bake_stack_to_lut3d: enabled=False stage is skipped entirely -- OK")


# --- ordering ---------------------------------------------------------

def test_stage_order_matters():
    size = 5
    gain = st.make_lut_stage(Gain(2.0), label="gain")
    offset_lut = colour.LUT3D(table=colour.LUT3D(size=size).table + 0.1, size=size)
    offset = st.make_lut_stage(offset_lut, label="offset")

    gain_then_offset = st.Stack(size=size, stages=[gain, offset])
    offset_then_gain = st.Stack(size=size, stages=[offset, gain])

    baked_a = st.bake_stack_to_lut3d(gain_then_offset)
    baked_b = st.bake_stack_to_lut3d(offset_then_gain)
    assert not np.allclose(baked_a.table, baked_b.table), \
        "gain-then-offset should differ from offset-then-gain, or order isn't respected"
    print("bake_stack_to_lut3d: stage order is respected (gain->offset != "
          "offset->gain) -- OK")


def test_move_stage_actually_changes_the_bake():
    size = 5
    gain = st.make_lut_stage(Gain(2.0), label="gain")
    offset_lut = colour.LUT3D(table=colour.LUT3D(size=size).table + 0.1, size=size)
    offset = st.make_lut_stage(offset_lut, label="offset")

    stack = st.Stack(size=size, stages=[gain, offset])
    before = st.bake_stack_to_lut3d(stack)

    stack.move_stage_up(offset.id)  # now [offset, gain]
    after = st.bake_stack_to_lut3d(stack)

    reference = st.Stack(size=size, stages=[offset, gain])
    reference_baked = st.bake_stack_to_lut3d(reference)

    assert not np.allclose(before.table, after.table)
    assert np.allclose(after.table, reference_baked.table, atol=1e-9), \
        "moving a stage should produce exactly what building that order from scratch would"
    print("Stack.move_stage_up: reordering changes the bake, and matches "
          "building the new order from scratch -- OK")


# --- stack forking (fork_split / fork_merge / branch-tagged stages) -----

def _tagged(stage, branch):
    """Test helper -- set .branch and return the same stage, so a whole
    fork region can be built as one Stack(stages=[...]) list literal."""
    stage.branch = branch
    return stage


def test_fork_merge_blend_zero_is_exactly_branch_a():
    size = 5
    stack = st.Stack(size=size, stages=[
        st.make_fork_split_stage(),
        _tagged(st.make_lut_stage(_make_gain_lut3d(0.4, size=size)), "a"),
        _tagged(st.make_lut_stage(_make_gain_lut3d(2.0, size=size)), "b"),
        st.make_fork_merge_stage(blend=0.0),
    ])
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    assert np.allclose(baked.table, identity * 0.4, atol=1e-9)
    print("fork_merge: blend=0.0 gives exactly branch a's output -- OK")


def test_fork_merge_blend_one_is_exactly_branch_b():
    size = 5
    stack = st.Stack(size=size, stages=[
        st.make_fork_split_stage(),
        _tagged(st.make_lut_stage(_make_gain_lut3d(0.4, size=size)), "a"),
        _tagged(st.make_lut_stage(_make_gain_lut3d(2.0, size=size)), "b"),
        st.make_fork_merge_stage(blend=1.0),
    ])
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    assert np.allclose(baked.table, identity * 2.0, atol=1e-9)
    print("fork_merge: blend=1.0 gives exactly branch b's output -- OK")


def test_fork_merge_blend_half_is_arithmetic_midpoint_of_both_branches():
    size = 5
    stack = st.Stack(size=size, stages=[
        st.make_fork_split_stage(),
        _tagged(st.make_lut_stage(_make_gain_lut3d(0.4, size=size)), "a"),
        _tagged(st.make_lut_stage(_make_gain_lut3d(2.0, size=size)), "b"),
        st.make_fork_merge_stage(blend=0.5),
    ])
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    expected = identity * (0.5 * 0.4 + 0.5 * 2.0)  # == identity * 1.2
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("fork_merge: blend=0.5 lands exactly at the arithmetic midpoint "
          "of the two branches' outputs -- OK")


def test_fork_supports_multiple_stages_per_branch():
    """The whole point of the split/merge redesign over a single
    two-branch stage: each side can hold any number of ordinary stages,
    evaluated in order, exactly like a normal Stack would.

    Branch a's gain is deliberately kept small (0.3, not e.g. 2.0) so its
    output stays inside colour.LUT3D's default [0, 1] domain before it's
    fed into the second (offset) stage's .apply() -- colour.LUT3D clamps
    out-of-domain *input* to its interpolator, so chaining a >1.0-reaching
    stage into another .apply() would make the hand-computed expected
    value below wrong, not stack.py. Keeping intermediate signal in-domain
    is what makes this test's arithmetic unambiguous."""
    size = 5
    offset_lut = colour.LUT3D(table=colour.LUT3D(size=size).table + 0.1, size=size)
    stack = st.Stack(size=size, stages=[
        st.make_fork_split_stage(),
        _tagged(st.make_lut_stage(_make_gain_lut3d(0.3, size=size)), "a"),
        _tagged(st.make_lut_stage(offset_lut), "a"),          # branch a: gain then offset
        _tagged(st.make_lut_stage(_make_gain_lut3d(0.5, size=size)), "b"),  # branch b: one stage
        st.make_fork_merge_stage(blend=0.5),
    ])
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    branch_a_result = identity * 0.3 + 0.1  # gain then offset, in that order
    branch_b_result = identity * 0.5
    expected = 0.5 * branch_a_result + 0.5 * branch_b_result
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("fork: branch a with two chained stages and branch b with one "
          "both evaluate correctly and in order before merging -- OK")


def test_fork_branch_stage_enabled_and_opacity_work_normally_within_branch():
    """A branch-tagged stage's own enabled/opacity work exactly like an
    ordinary top-level stage's would -- nothing fork-specific about them,
    just applied against that branch's own running signal."""
    size = 5
    disabled_stage = _tagged(st.make_lut_stage(_make_gain_lut3d(9.0, size=size)), "a")
    disabled_stage.enabled = False
    stack = st.Stack(size=size, stages=[
        st.make_fork_split_stage(),
        disabled_stage,  # should have zero effect
        _tagged(st.make_lut_stage(_make_gain_lut3d(2.0, size=size), opacity=0.5), "a"),
        _tagged(st.make_lut_stage(_make_gain_lut3d(3.0, size=size)), "b"),
        st.make_fork_merge_stage(blend=0.0),  # all branch a, to isolate its result
    ])
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    # disabled_stage: no-op. Then gain=2.0 at opacity=0.5: 0.5*1.0 + 0.5*2.0 = 1.5x.
    assert np.allclose(baked.table, identity * 1.5, atol=1e-9)
    print("fork: a branch-tagged stage's own enabled/opacity behave exactly "
          "like a top-level stage's would -- OK")


def test_fork_merge_total_opacity_blends_against_signal_before_split():
    """fork_merge's own opacity (per Tom: "a total opacity control like
    every other operator row") blends the combined branch result against
    the signal exactly as it stood right before the matching split --
    same "fully-applied output blended against this stage's own input"
    rule every other stage's opacity already follows.

    pre_split's gain is kept small (0.5, not e.g. 3.0) so its output stays
    inside colour.LUT3D's default [0, 1] domain before being fed as
    *input* into each branch stage's .apply() -- out-of-domain input gets
    clamped by the interpolator, which would make the hand-computed
    expected value below wrong, not stack.py. The branch gains themselves
    (0.4, 2.0) are unconstrained since their *output* never feeds another
    .apply() call, only fork_merge's plain numpy blend."""
    size = 5
    pre_split = st.make_lut_stage(_make_gain_lut3d(0.5, size=size), label="pre-split")
    stack = st.Stack(size=size, stages=[
        pre_split,
        st.make_fork_split_stage(),
        _tagged(st.make_lut_stage(_make_gain_lut3d(0.4, size=size)), "a"),
        _tagged(st.make_lut_stage(_make_gain_lut3d(2.0, size=size)), "b"),
        st.make_fork_merge_stage(blend=0.5, opacity=0.5),
    ])
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    signal_before_split = identity * 0.5
    combined = signal_before_split * 0.4 * 0.5 + signal_before_split * 2.0 * 0.5
    expected = signal_before_split * 0.5 + combined * 0.5  # merge's own opacity=0.5
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("fork_merge: its own opacity blends the combined branch result "
          "against the signal as it stood right before the split -- OK")


def test_fork_split_disabled_makes_whole_region_a_noop():
    """fork_split's enabled=False treats the WHOLE region -- both
    branches AND the merge -- as if it weren't there at all."""
    size = 5
    split = st.make_fork_split_stage()
    split.enabled = False
    stack = st.Stack(size=size, stages=[
        split,
        _tagged(st.make_lut_stage(_make_gain_lut3d(0.4, size=size)), "a"),
        _tagged(st.make_lut_stage(_make_gain_lut3d(2.0, size=size)), "b"),
        st.make_fork_merge_stage(blend=0.5),
    ])
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    assert np.array_equal(baked.table, identity)
    print("fork_split: enabled=False makes the whole split/branches/merge "
          "region an exact no-op -- OK")


def test_fork_merge_disabled_has_same_effect_as_opacity_zero():
    size = 5

    def build(merge_stage):
        return st.Stack(size=size, stages=[
            st.make_fork_split_stage(),
            _tagged(st.make_lut_stage(_make_gain_lut3d(0.4, size=size)), "a"),
            _tagged(st.make_lut_stage(_make_gain_lut3d(2.0, size=size)), "b"),
            merge_stage,
        ])

    disabled_merge = st.make_fork_merge_stage(blend=0.5)
    disabled_merge.enabled = False
    zero_opacity_merge = st.make_fork_merge_stage(blend=0.5, opacity=0.0)

    baked_disabled = st.bake_stack_to_lut3d(build(disabled_merge))
    baked_zero_opacity = st.bake_stack_to_lut3d(build(zero_opacity_merge))
    identity = colour.LUT3D(size=size).table
    assert np.array_equal(baked_disabled.table, identity)
    assert np.array_equal(baked_zero_opacity.table, identity)
    print("fork_merge: enabled=False has the same net effect as "
          "opacity=0.0 -- both leave the pre-split signal untouched -- OK")


def test_stage_after_fork_merge_sees_only_the_rejoined_single_signal():
    """The whole point of forking: it's entirely internal to the region
    between one split/merge pair -- whatever comes next in the Stack sees
    one signal, same as if forking never happened."""
    size = 5
    stack = st.Stack(size=size, stages=[
        st.make_fork_split_stage(),
        _tagged(st.make_lut_stage(_make_gain_lut3d(0.4, size=size)), "a"),
        _tagged(st.make_lut_stage(_make_gain_lut3d(2.0, size=size)), "b"),
        st.make_fork_merge_stage(blend=0.5),  # rejoins to 1.2x
        st.make_lut_stage(Gain(2.0), label="after"),
    ])
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    assert np.allclose(baked.table, identity * 1.2 * 2.0, atol=1e-9)
    print("fork: the next stage in the Stack sees only the single "
          "rejoined result, moving back to one signal chain -- OK")


def test_fork_merge_with_no_matching_split_raises():
    stack = st.Stack(size=5, stages=[st.make_fork_merge_stage()])
    try:
        st.bake_stack_to_lut3d(stack)
        raise AssertionError("expected ValueError for an unmatched fork_merge")
    except ValueError:
        pass
    print("fork_merge: raises with no preceding fork_split -- OK")


def test_fork_split_with_no_matching_merge_raises():
    stack = st.Stack(size=5, stages=[
        st.make_fork_split_stage(),
        _tagged(st.make_lut_stage(_make_gain_lut3d(0.4, size=5)), "a"),
    ])
    try:
        st.bake_stack_to_lut3d(stack)
        raise AssertionError("expected ValueError for an unmatched fork_split")
    except ValueError:
        pass
    print("fork_split: raises with no following fork_merge -- OK")


def test_nested_fork_split_raises():
    stack = st.Stack(size=5, stages=[
        st.make_fork_split_stage(),
        st.make_fork_split_stage(),
    ])
    try:
        st.bake_stack_to_lut3d(stack)
        raise AssertionError("expected ValueError for a nested fork_split")
    except ValueError:
        pass
    print("fork_split: nesting a second split inside an open region raises -- OK")


def test_stage_inside_fork_region_without_branch_tag_raises():
    stack = st.Stack(size=5, stages=[
        st.make_fork_split_stage(),
        st.make_lut_stage(_make_gain_lut3d(0.4, size=5)),  # no .branch set
        st.make_fork_merge_stage(),
    ])
    try:
        st.bake_stack_to_lut3d(stack)
        raise AssertionError("expected ValueError for a stage with no branch inside a fork region")
    except ValueError:
        pass
    print("fork: a stage inside an open region with no branch assignment raises -- OK")


def test_stray_branch_tag_outside_any_fork_region_is_ignored():
    """A branch tag left over from being dragged out of a fork region in
    the UI shouldn't be a hard failure -- outside any open region it's
    simply inert, and the stage runs as an ordinary top-level stage."""
    size = 5
    stack = st.Stack(size=size, stages=[
        _tagged(st.make_lut_stage(_make_gain_lut3d(2.0, size=size)), "a"),
    ])
    baked = st.bake_stack_to_lut3d(stack)
    identity = colour.LUT3D(size=size).table
    assert np.allclose(baked.table, identity * 2.0, atol=1e-9)
    print("fork: a branch-tagged stage outside any open fork region runs "
          "as an ordinary top-level stage, tag ignored -- OK")


def test_stage_copy_preserves_branch_tag():
    stage = _tagged(st.make_lut_stage(_make_gain_lut3d(0.4, size=5)), "a")
    copied = stage.copy()
    assert copied.branch == "a"
    print("Stage.copy(): preserves .branch -- OK")


# --- apply_stack_to_image_array agrees with bake_stack_to_lut3d ----------

def test_apply_to_image_array_agrees_with_bake_on_identity_grid():
    size = 9
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(lut))
    stack.add_stage(st.make_printer_lights_stage(6, -3, 1, working_colourspace="ACEScct"))

    baked = st.bake_stack_to_lut3d(stack)
    identity_grid = colour.LUT3D(size=size).table
    direct = st.apply_stack_to_image_array(stack, identity_grid)

    assert np.allclose(direct, np.clip(baked.table, 0.0, 1.0), atol=1e-6), \
        "apply_stack_to_image_array and bake_stack_to_lut3d should agree on " \
        "the same input, up to apply_stack_to_image_array's [0,1] display clip"
    print("apply_stack_to_image_array: agrees with bake_stack_to_lut3d on the "
          "same identity-grid input (mod display clipping) -- OK")


# --- domain metadata / resize (stack-level, not stages) -------------------

def test_domain_metadata_does_not_affect_table():
    size = 5
    stack_default_domain = st.Stack(size=size, stages=[st.make_lut_stage(Gain(1.5))])
    stack_custom_domain = st.Stack(
        size=size, stages=[st.make_lut_stage(Gain(1.5))],
        domain_min=(-0.5, -0.5, -0.5), domain_max=(2.0, 2.0, 2.0),
    )
    baked_default = st.bake_stack_to_lut3d(stack_default_domain)
    baked_custom = st.bake_stack_to_lut3d(stack_custom_domain)

    assert np.array_equal(baked_default.table, baked_custom.table), \
        "domain_min/domain_max should be metadata only, never affect the computed table"
    assert np.allclose(np.asarray(baked_custom.domain), [[-0.5, -0.5, -0.5], [2.0, 2.0, 2.0]])
    assert np.allclose(np.asarray(baked_default.domain), [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
    print("bake_stack_to_lut3d: domain_min/domain_max are metadata-only, "
          "matching app_logic.set_lut_domain's own always-metadata-only rule -- OK")


def test_resize_recomputes_natively_more_accurately_than_resampling():
    """The whole point of Stack.size replacing the old "Resize" panel:
    changing it and re-baking recomputes the ENTIRE chain fresh from a
    plain identity grid at the new resolution, rather than resampling an
    already-baked (lossy) table -- confirmed here by showing the two
    approaches give genuinely different answers for a nonlinear stage
    (interpolation error compounds when you resample a resample)."""
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    small_size, big_size = 5, 33

    stack = st.Stack(size=small_size, stages=[st.make_lut_stage(lut)])
    baked_small = st.bake_stack_to_lut3d(stack)

    # Old-style: resample the already-baked small table up to big_size.
    old_style_resampled = resample_lut(baked_small, big_size)

    # New-style: just change Stack.size and re-bake from scratch.
    stack.size = big_size
    new_style_rebaked = st.bake_stack_to_lut3d(stack)

    # Both should be reasonably close (same underlying LUT), but NOT
    # identical -- resampling a coarse bake loses information a fresh
    # bake at the target resolution never had to lose.
    assert not np.allclose(old_style_resampled.table, new_style_rebaked.table, atol=1e-6), \
        "resampling an already-baked coarse table should NOT exactly match a fresh bake at full size"
    # And the fresh bake should match a direct apply at that resolution exactly.
    identity_at_big_size = colour.LUT3D(size=big_size).table
    direct = lut.apply(identity_at_big_size)
    assert np.allclose(new_style_rebaked.table, direct, atol=1e-9)
    print("Stack.size + re-bake recomputes natively (matches a direct apply "
          "at the new resolution exactly), unlike resampling an already-baked "
          "coarse table -- confirms the accuracy improvement -- OK")


# --- fork auto-branch helpers ----------------------------------------------

def _build_fork_stack_for_region_tests():
    """before, [split, a1, merge], after -- one fork region with a single
    branch-A stage and an empty branch B, used by several tests below."""
    stack = st.Stack()
    before = st.make_lut_stage(create_identity_lut(size=5), label="before")
    split = st.make_fork_split_stage()
    a1 = st.make_lut_stage(create_identity_lut(size=5), label="a1")
    a1.branch = "a"
    merge = st.make_fork_merge_stage()
    after = st.make_lut_stage(create_identity_lut(size=5), label="after")
    for s in (before, split, a1, merge, after):
        stack.add_stage(s)
    return stack, before, split, a1, merge, after


def test_find_enclosing_fork_region_before_split_and_after_merge_is_none():
    stack, before, split, a1, merge, after = _build_fork_stack_for_region_tests()
    stages = stack.stages
    split_idx, merge_idx = stages.index(split), stages.index(merge)
    # Inserting right before the split (== split's own index) is OUTSIDE.
    assert st.find_enclosing_fork_region(stages, split_idx) is None
    # Inserting right after the merge (== merge's index + 1) is OUTSIDE.
    assert st.find_enclosing_fork_region(stages, merge_idx + 1) is None
    print("find_enclosing_fork_region: positions before the split or after "
          "the merge are correctly NOT inside the region -- OK")


def test_find_enclosing_fork_region_between_split_and_merge_is_inside():
    stack, before, split, a1, merge, after = _build_fork_stack_for_region_tests()
    stages = stack.stages
    split_idx, merge_idx = stages.index(split), stages.index(merge)
    # Right after the split, and right before the merge, are both INSIDE.
    assert st.find_enclosing_fork_region(stages, split_idx + 1) == (split_idx, merge_idx)
    assert st.find_enclosing_fork_region(stages, merge_idx) == (split_idx, merge_idx)
    print("find_enclosing_fork_region: any position strictly between the "
          "split and merge resolves to that exact region -- OK")


def test_find_enclosing_fork_region_with_no_fork_at_all_is_none():
    stack = st.Stack()
    stack.add_stage(st.make_lut_stage(create_identity_lut(size=5)))
    assert st.find_enclosing_fork_region(stack.stages, 0) is None
    assert st.find_enclosing_fork_region(stack.stages, 1) is None
    print("find_enclosing_fork_region: a stack with no fork at all never "
          "reports an enclosing region -- OK")


def test_fork_region_branch_counts_reflects_current_occupancy():
    stack, before, split, a1, merge, after = _build_fork_stack_for_region_tests()
    stages = stack.stages
    split_idx, merge_idx = stages.index(split), stages.index(merge)
    counts = st.fork_region_branch_counts(stages, split_idx, merge_idx)
    assert counts == {"a": 1, "b": 0}
    print("fork_region_branch_counts: counts exactly the branch-tagged "
          "stages strictly between a split/merge pair -- OK")


# --- StageCache -----------------------------------------------------------

def test_stage_cache_matches_uncached_result_across_edits():
    cache = st.StageCache()
    stack = st.Stack(size=5)
    a = st.make_lut_stage(_make_gain_lut3d(2.0, size=5), label="a")
    b = st.make_lut_stage(_make_gain_lut3d(3.0, size=5), label="b")
    c = st.make_cdl_stage(slope=(1.1, 1.0, 0.9))
    stack.add_stage(a)
    stack.add_stage(b)
    stack.add_stage(c)

    def check():
        cached = st.bake_stack_to_lut3d(stack, cache=cache)
        uncached = st.bake_stack_to_lut3d(stack)
        assert np.allclose(cached.table, uncached.table, atol=1e-12)

    check()  # first bake -- nothing cached yet
    b.opacity = 0.5
    check()  # edit a middle stage's opacity
    c.params["slope"] = (1.2, 1.0, 0.8)
    check()  # edit the last stage's params
    stack.move_stage_up(c.id)
    check()  # reorder
    stack.remove_stage(a.id)
    check()  # remove the first stage
    stack.add_stage(st.make_lut_stage(_make_gain_lut3d(0.5, size=5), label="d"))
    check()  # add a new stage at the end
    print("StageCache: bake_stack_to_lut3d with a cache always matches an "
          "uncached bake, across add/remove/reorder/opacity/param edits -- OK")


def test_stage_cache_skips_recompute_of_untouched_stages():
    cache = st.StageCache()
    stack = st.Stack(size=4)
    first = CountingGain(2.0, name="first")
    second = CountingGain(3.0, name="second")
    stack.add_stage(st.make_lut_stage(first, label="first"))
    s2 = st.make_lut_stage(second, label="second")
    stack.add_stage(s2)

    st.bake_stack_to_lut3d(stack, cache=cache)
    assert first.call_count == 1 and second.call_count == 1

    # Editing only the SECOND stage's opacity should reuse the first
    # stage's cached checkpoint rather than re-running its .apply again.
    s2.opacity = 0.5
    st.bake_stack_to_lut3d(stack, cache=cache)
    assert first.call_count == 1, "untouched earlier stage was recomputed unnecessarily"
    assert second.call_count == 2

    # A no-op re-bake (nothing changed at all) shouldn't re-run either stage.
    st.bake_stack_to_lut3d(stack, cache=cache)
    assert first.call_count == 1 and second.call_count == 2
    print("StageCache: editing one stage doesn't re-run an earlier, "
          "unchanged stage's own transform (and a no-op re-bake re-runs "
          "nothing at all) -- OK")


def test_stage_cache_apply_to_image_array_matches_uncached_and_respects_input_key():
    cache = st.StageCache()
    rng = np.random.default_rng(0)
    image = rng.random((3, 3, 3)).astype(np.float32)
    stack = st.Stack()
    stack.add_stage(st.make_lut_stage(_make_gain_lut3d(0.5, size=5)))

    cached = apply_stack_to_image_array_with_cache(stack, image, cache, input_key=1)
    uncached = st.apply_stack_to_image_array(stack, image)
    assert np.allclose(cached, uncached, atol=1e-6)

    # Same input_key, stack unchanged -- must still match (hits the
    # fully-cached path where nothing gets re-run at all).
    again = apply_stack_to_image_array_with_cache(stack, image, cache, input_key=1)
    assert np.allclose(again, uncached, atol=1e-6)

    # A DIFFERENT input_key (e.g. a newly loaded image) must not reuse the
    # previous image's cached result, even though the stack didn't change.
    other_image = rng.random((3, 3, 3)).astype(np.float32)
    other_cached = apply_stack_to_image_array_with_cache(stack, other_image, cache, input_key=2)
    other_uncached = st.apply_stack_to_image_array(stack, other_image)
    assert np.allclose(other_cached, other_uncached, atol=1e-6)
    print("StageCache: apply_stack_to_image_array with a cache matches "
          "uncached results and correctly invalidates on a new input_key -- OK")


def apply_stack_to_image_array_with_cache(stack, image, cache, input_key):
    """Thin wrapper so the test above reads as "the cached call" without
    repeating the cache=/input_key= kwargs at every call site."""
    return st.apply_stack_to_image_array(stack, image, cache=cache, input_key=input_key)


def test_stage_cache_handles_fork_regions_as_one_block():
    cache = st.StageCache()
    stack = st.Stack(size=4)
    before = st.make_lut_stage(_make_gain_lut3d(2.0, size=4), label="before")
    split = st.make_fork_split_stage()
    a = st.make_lut_stage(_make_gain_lut3d(1.5, size=4), label="a")
    a.branch = "a"
    b = st.make_lut_stage(_make_gain_lut3d(0.5, size=4), label="b")
    b.branch = "b"
    merge = st.make_fork_merge_stage(blend=0.5)
    after = st.make_lut_stage(_make_gain_lut3d(3.0, size=4), label="after")
    for s in (before, split, a, b, merge, after):
        stack.add_stage(s)

    def check():
        cached = st.bake_stack_to_lut3d(stack, cache=cache)
        uncached = st.bake_stack_to_lut3d(stack)
        assert np.allclose(cached.table, uncached.table, atol=1e-9)

    check()  # first bake
    merge.params["blend"] = 0.25
    check()  # edit the merge's own blend
    a.opacity = 0.7
    check()  # edit a stage INSIDE the fork region
    after.opacity = 0.5
    check()  # edit a stage AFTER the fork region
    print("StageCache: fork_split..fork_merge regions cache/invalidate as "
          "one block and still match an uncached bake across edits -- OK")


if __name__ == "__main__":
    test_stage_rejects_unknown_kind()
    test_make_lut_stage_retarget_requires_both_or_neither()
    test_add_remove_find_move_stage()
    test_empty_stack_bakes_to_plain_identity()
    test_empty_stack_apply_to_image_is_noop()
    test_single_lut_stage_matches_direct_apply()
    test_lut_stage_with_retarget_matches_bake_retargeted_lut()
    test_printer_lights_stage_matches_bake_printer_lights_on_lut()
    test_aces_drt_stage_matches_bake_aces_output_transform_to_lut3d()
    test_cdl_stage_matches_direct_apply_cdl()
    test_cdl_stage_composes_after_a_lut_stage()
    test_colorspace_stage_matches_direct_apply_colorspace_conversion()
    test_colorspace_stage_composes_after_a_lut_stage()
    test_opacity_one_is_full_effect()
    test_opacity_zero_is_exact_noop()
    test_opacity_half_is_arithmetic_midpoint()
    test_disabled_stage_is_exact_noop_regardless_of_params()
    test_stage_order_matters()
    test_move_stage_actually_changes_the_bake()
    test_fork_merge_blend_zero_is_exactly_branch_a()
    test_fork_merge_blend_one_is_exactly_branch_b()
    test_fork_merge_blend_half_is_arithmetic_midpoint_of_both_branches()
    test_fork_supports_multiple_stages_per_branch()
    test_fork_branch_stage_enabled_and_opacity_work_normally_within_branch()
    test_fork_merge_total_opacity_blends_against_signal_before_split()
    test_fork_split_disabled_makes_whole_region_a_noop()
    test_fork_merge_disabled_has_same_effect_as_opacity_zero()
    test_stage_after_fork_merge_sees_only_the_rejoined_single_signal()
    test_fork_merge_with_no_matching_split_raises()
    test_fork_split_with_no_matching_merge_raises()
    test_nested_fork_split_raises()
    test_stage_inside_fork_region_without_branch_tag_raises()
    test_stray_branch_tag_outside_any_fork_region_is_ignored()
    test_stage_copy_preserves_branch_tag()
    test_apply_to_image_array_agrees_with_bake_on_identity_grid()
    test_domain_metadata_does_not_affect_table()
    test_resize_recomputes_natively_more_accurately_than_resampling()
    test_find_enclosing_fork_region_before_split_and_after_merge_is_none()
    test_find_enclosing_fork_region_between_split_and_merge_is_inside()
    test_find_enclosing_fork_region_with_no_fork_at_all_is_none()
    test_fork_region_branch_counts_reflects_current_occupancy()
    test_stage_cache_matches_uncached_result_across_edits()
    test_stage_cache_skips_recompute_of_untouched_stages()
    test_stage_cache_apply_to_image_array_matches_uncached_and_respects_input_key()
    test_stage_cache_handles_fork_regions_as_one_block()
    print()
    print("All stack tests passed.")
