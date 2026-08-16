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
import color_warper
import colorspace_convert as csc
import exposure_contrast
import grading_primary
import grading_rgb_curve
import grading_tone

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


def test_lut_stage_with_empty_string_retarget_spaces_fails_loudly():
    """Regression guard: make_lut_stage's own constructor validates
    retarget_from/retarget_to with `is None` (an empty string counts as
    "given"), so _apply_stage_to_array's dispatch must use the same check
    -- not plain truthiness, which used to treat "" as "not given" and
    silently apply the raw LUT with no space conversion at all instead of
    raising. Empty-string space names aren't realistic from the GUI, but
    the two checks disagreeing was a real inconsistency worth locking
    down either way."""
    lut = create_identity_lut(size=5)
    stack = st.Stack(size=5)
    stage = st.make_lut_stage(lut, retarget_from="ACEScct", retarget_to="ACES2065-1")
    stage.params["retarget_from"] = ""
    stage.params["retarget_to"] = ""
    stack.add_stage(stage)
    try:
        st.bake_stack_to_lut3d(stack)
        raise AssertionError(
            "expected an empty-string retarget space to fail loudly, not "
            "silently skip the retarget")
    except AssertionError:
        raise
    except Exception:
        pass  # any real failure from colorspace.convert() is the correct outcome
    print("_apply_stage_to_array: empty-string retarget_from/retarget_to "
          "attempts a real (and failing) retarget rather than silently "
          "skipping it -- OK")


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


def test_grading_primary_stage_matches_direct_apply():
    if not grading_primary.OCIO_AVAILABLE:
        print("SKIPPED test_grading_primary_stage_matches_direct_apply -- PyOpenColorIO not installed")
        return
    size = 9
    brightness, contrast = (0.02, -0.01, 0.03, 0.0), (1.1, 1.05, 0.95, 1.0)

    stack = st.Stack(size=size)
    stack.add_stage(st.make_grading_primary_stage(brightness=brightness, contrast=contrast))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    expected = grading_primary.apply_grading_primary(
        identity_grid, brightness=brightness, contrast=contrast)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: single 'grading_primary' stage matches "
          "grading_primary.apply_grading_primary directly on the identity grid -- OK")


def test_color_warper_stage_matches_direct_apply():
    if not aces_drt.OCIO_AVAILABLE:
        print("SKIPPED test_color_warper_stage_matches_direct_apply -- PyOpenColorIO not installed")
        return
    size = 9
    points = [{"h0": 35.0, "c0": 0.15, "h": 55.0, "c": 0.22}]
    wake, dropoff = 40.0, 0.2

    stack = st.Stack(size=size)
    stack.add_stage(st.make_color_warper_stage(points=points, wake=wake, dropoff=dropoff))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    expected = color_warper.apply_color_warper_in_working_space(
        identity_grid, color_warper.DEFAULT_WORKING_COLOURSPACE, points, wake=wake, dropoff=dropoff)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: single 'color_warper' stage matches "
          "color_warper.apply_color_warper_in_working_space directly on the identity grid -- OK")


def test_color_warper_stage_default_is_identity():
    size = 5
    stack = st.Stack(size=size)
    stack.add_stage(st.make_color_warper_stage())
    baked = st.bake_stack_to_lut3d(stack)
    identity_grid = colour.LUT3D(size=size).table
    assert np.array_equal(baked.table, identity_grid)
    print("bake_stack_to_lut3d: a freshly-added 'color_warper' stage "
          "(no seeded points, empty list) is an exact no-op -- OK")


def test_grading_primary_stage_composes_after_a_lut_stage():
    if not grading_primary.OCIO_AVAILABLE:
        print("SKIPPED test_grading_primary_stage_composes_after_a_lut_stage -- PyOpenColorIO not installed")
        return
    size = 9
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    contrast = (1.15, 1.0, 0.9, 1.0)

    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(lut))
    stack.add_stage(st.make_grading_primary_stage(contrast=contrast))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    lut_output = lut.apply(identity_grid)
    expected = grading_primary.apply_grading_primary(lut_output, contrast=contrast)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: 'grading_primary' stage composes correctly "
          "after a 'lut' stage -- OK")


def test_exposure_contrast_stage_matches_direct_apply():
    if not exposure_contrast.OCIO_AVAILABLE:
        print("SKIPPED test_exposure_contrast_stage_matches_direct_apply -- PyOpenColorIO not installed")
        return
    size = 9
    exposure, contrast, gamma = 0.4, 1.15, 1.05

    stack = st.Stack(size=size)
    stack.add_stage(st.make_exposure_contrast_stage(exposure=exposure, contrast=contrast, gamma=gamma))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    expected = exposure_contrast.apply_exposure_contrast(
        identity_grid, exposure=exposure, contrast=contrast, gamma=gamma)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: single 'exposure_contrast' stage matches "
          "exposure_contrast.apply_exposure_contrast directly on the identity grid -- OK")


def test_exposure_contrast_stage_composes_after_a_lut_stage():
    if not exposure_contrast.OCIO_AVAILABLE:
        print("SKIPPED test_exposure_contrast_stage_composes_after_a_lut_stage -- PyOpenColorIO not installed")
        return
    size = 9
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    exposure = 0.3

    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(lut))
    stack.add_stage(st.make_exposure_contrast_stage(exposure=exposure))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    lut_output = lut.apply(identity_grid)
    expected = exposure_contrast.apply_exposure_contrast(lut_output, exposure=exposure)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: 'exposure_contrast' stage composes correctly "
          "after a 'lut' stage -- OK")


def test_grading_tone_stage_matches_direct_apply():
    if not grading_tone.OCIO_AVAILABLE:
        print("SKIPPED test_grading_tone_stage_matches_direct_apply -- PyOpenColorIO not installed")
        return
    size = 9
    midtones = (1.05, 0.98, 1.02, 1.0, 0.4, 0.6)

    stack = st.Stack(size=size)
    stack.add_stage(st.make_grading_tone_stage(midtones=midtones))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    expected = grading_tone.apply_grading_tone(identity_grid, midtones=midtones)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: single 'grading_tone' stage matches "
          "grading_tone.apply_grading_tone directly on the identity grid -- OK")


def test_grading_tone_stage_composes_after_a_lut_stage():
    if not grading_tone.OCIO_AVAILABLE:
        print("SKIPPED test_grading_tone_stage_composes_after_a_lut_stage -- PyOpenColorIO not installed")
        return
    size = 9
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    highlights = (1.1, 1.0, 0.95, 1.0, 0.3, 1.0)

    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(lut))
    stack.add_stage(st.make_grading_tone_stage(highlights=highlights))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    lut_output = lut.apply(identity_grid)
    expected = grading_tone.apply_grading_tone(lut_output, highlights=highlights)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: 'grading_tone' stage composes correctly "
          "after a 'lut' stage -- OK")


def test_grading_rgb_curve_stage_matches_direct_apply():
    if not grading_rgb_curve.OCIO_AVAILABLE:
        print("SKIPPED test_grading_rgb_curve_stage_matches_direct_apply -- PyOpenColorIO not installed")
        return
    size = 9
    master = ((0.0, 0.0), (0.5, 0.6), (1.0, 1.0))

    stack = st.Stack(size=size)
    stack.add_stage(st.make_grading_rgb_curve_stage(master=master))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    expected = grading_rgb_curve.apply_grading_rgb_curve(identity_grid, master=master)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: single 'grading_rgb_curve' stage matches "
          "grading_rgb_curve.apply_grading_rgb_curve directly on the identity grid -- OK")


def test_grading_rgb_curve_stage_composes_after_a_lut_stage():
    if not grading_rgb_curve.OCIO_AVAILABLE:
        print("SKIPPED test_grading_rgb_curve_stage_composes_after_a_lut_stage -- PyOpenColorIO not installed")
        return
    size = 9
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    red = ((0.0, 0.0), (0.5, 0.4), (1.0, 1.0))

    stack = st.Stack(size=size)
    stack.add_stage(st.make_lut_stage(lut))
    stack.add_stage(st.make_grading_rgb_curve_stage(red=red))
    baked = st.bake_stack_to_lut3d(stack)

    identity_grid = colour.LUT3D(size=size).table
    lut_output = lut.apply(identity_grid)
    expected = grading_rgb_curve.apply_grading_rgb_curve(lut_output, red=red)
    assert np.allclose(baked.table, expected, atol=1e-9)
    print("bake_stack_to_lut3d: 'grading_rgb_curve' stage composes correctly "
          "after a 'lut' stage -- OK")


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

def test_domain_min_max_actually_scale_the_baked_table():
    """A regression test for a real export bug (Tom, 2026-08-12): the
    table used to always be folded over a plain [0,1] grid regardless of
    Stack.domain_min/domain_max, with the domain only ever slapped on
    afterward as metadata -- so an extended-domain export's DOMAIN_MIN/
    DOMAIN_MAX header described a range the table was never actually
    computed for. Any domain-aware reader (this app's own re-import,
    Lattice) that trusted the header then sampled the wrong slice of the
    table, producing a much-too-dark result. The table must actually be
    computed from a grid spanning [domain_min, domain_max], NOT be
    identical to the default-domain bake."""
    size = 5
    stack_default_domain = st.Stack(size=size, stages=[st.make_lut_stage(Gain(1.5))])
    stack_custom_domain = st.Stack(
        size=size, stages=[st.make_lut_stage(Gain(1.5))],
        domain_min=(-0.5, -0.5, -0.5), domain_max=(2.0, 2.0, 2.0),
    )
    baked_default = st.bake_stack_to_lut3d(stack_default_domain)
    baked_custom = st.bake_stack_to_lut3d(stack_custom_domain)

    assert not np.array_equal(baked_default.table, baked_custom.table), \
        "domain_min/domain_max must actually change the computed table, not just its metadata"
    assert np.allclose(np.asarray(baked_custom.domain), [[-0.5, -0.5, -0.5], [2.0, 2.0, 2.0]])
    assert np.allclose(np.asarray(baked_default.domain), [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])

    # The real invariant that matters: applying the baked+domain-tagged
    # LUT (the way any external reader would -- respecting .domain) to
    # real values across the extended range must agree with running the
    # same stages directly on those values, same "grid bake agrees with
    # direct apply" contract as
    # test_apply_to_image_array_agrees_with_bake_on_identity_grid above.
    probe = np.array([[-0.5, -0.5, -0.5], [0.0, 0.0, 0.0], [0.75, 0.75, 0.75], [2.0, 2.0, 2.0]])
    direct = st.apply_stack_to_image_array(stack_custom_domain, probe)
    via_lut = baked_custom.apply(probe)
    assert np.allclose(direct, np.clip(via_lut, 0.0, 1.0), atol=1e-6), \
        "sampling the baked extended-domain LUT via its own .domain must agree with " \
        "applying the stack's stages directly to the same real values"
    print("bake_stack_to_lut3d: domain_min/domain_max now actually scale the baked "
          "grid, and the result agrees with direct stage application across the "
          "real extended range -- OK")


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


# --- find_fork_partner (StackPanel's linked-delete helper) -----------------

def test_find_fork_partner_via_split_id_with_stages_on_both_branches():
    stack = st.Stack()
    split = st.make_fork_split_stage()
    a1 = st.make_lut_stage(create_identity_lut(size=5), label="a1")
    a1.branch = "a"
    b1 = st.make_lut_stage(create_identity_lut(size=5), label="b1")
    b1.branch = "b"
    merge = st.make_fork_merge_stage()
    for s in (split, a1, b1, merge):
        stack.add_stage(s)
    result = st.find_fork_partner(stack.stages, split.id)
    assert result == (split.id, merge.id, [a1.id, b1.id])
    print("find_fork_partner: given the split's id, finds its matching "
          "merge and every interior stage in stack order -- OK")


def test_find_fork_partner_via_merge_id_with_one_branch_empty():
    stack, before, split, a1, merge, after = _build_fork_stack_for_region_tests()
    result = st.find_fork_partner(stack.stages, merge.id)
    assert result == (split.id, merge.id, [a1.id])
    print("find_fork_partner: given the merge's id (with Branch B empty), "
          "finds its matching split and just Branch A's stage -- OK")


def test_find_fork_partner_returns_none_for_a_non_fork_stage():
    stack, before, split, a1, merge, after = _build_fork_stack_for_region_tests()
    assert st.find_fork_partner(stack.stages, before.id) is None
    assert st.find_fork_partner(stack.stages, a1.id) is None
    print("find_fork_partner: None for a stage that isn't a fork_split/"
          "fork_merge marker at all -- OK")


def test_find_fork_partner_returns_none_when_no_matching_marker_exists():
    stack = st.Stack()
    split = st.make_fork_split_stage()
    stack.add_stage(split)  # no matching fork_merge anywhere -- corrupted/incomplete
    assert st.find_fork_partner(stack.stages, split.id) is None
    print("find_fork_partner: None for an unmatched marker (already-corrupted "
          "or hand-edited stack) rather than raising -- OK")


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


# --- matte channels (keyer stage + matte_source_id gating) ---------------

def _half_red_half_blue(size=4):
    """Midtone reddish (hue 0) / bluish (hue 240) halves -- deliberately
    NOT pure saturated (1,0,0)/(0,0,1) primaries, since a channel already
    sitting at 0 or 1 would make an additive grade's effect invisible
    after apply_stack_to_image_array's own final display clip (e.g. a
    red channel already at 1.0 plus a positive offset still clips back
    to 1.0, silently hiding the grade from a simple "did it change"
    assertion)."""
    image = np.zeros((2, size, 3), dtype=np.float64)
    image[:, :size // 2] = (0.6, 0.2, 0.2)
    image[:, size // 2:] = (0.2, 0.2, 0.6)
    return image


def _oklch_hue_of(rgb, working_colourspace=color_warper.REFERENCE_LINEAR_SPACE):
    """The real OKLCh hue of a solid `rgb` sample, decoded via the same
    color_warper.decode_working_colourspace_to_oklch keyer.py's own
    qualifier now uses -- lets these tests pin a keyer's hue_center to
    each _half_red_half_blue() half's OWN actual hue rather than a
    hardcoded HSL-era constant like 0/240 that no longer means anything
    once the qualifier operates in OKLCh."""
    sample = np.array(rgb, dtype=np.float64).reshape(1, 1, 3)
    _, _, h = color_warper.decode_working_colourspace_to_oklch(sample, working_colourspace)
    return float(h[0, 0])


# Tested against REFERENCE_LINEAR_SPACE (see test_keyer.py's own SPACE
# constant for the same convention) so these stay exact, deterministic
# numbers rather than depending on ACEScct's own log encoding.
REDDISH_HUE = _oklch_hue_of((0.6, 0.2, 0.2))
BLUISH_HUE = _oklch_hue_of((0.2, 0.2, 0.6))


def test_keyer_stage_is_identity_passthrough_for_the_image():
    image = _half_red_half_blue()
    keyer_stage = st.make_keyer_stage(hue_center=0.0, hue_width=20.0)
    stack = st.Stack()
    stack.add_stage(keyer_stage)
    result = st.apply_stack_to_image_array(stack, image)
    assert np.allclose(result, image, atol=1e-9)
    print("keyer stage: never grades the image itself, regardless of its "
          "own qualifier settings -- OK")


def test_matte_letters_for_assigns_sequential_letters_to_keyer_stages_only():
    k1 = st.make_keyer_stage()
    grading = st.make_grading_primary_stage()
    k2 = st.make_keyer_stage()
    letters = st.matte_letters_for([k1, grading, k2])
    assert letters == {k1.id: "A", k2.id: "B"}
    assert grading.id not in letters
    print("matte_letters_for: assigns sequential letters to keyer stages "
          "only, skipping every other kind -- OK")


def test_matte_letters_for_doubles_past_26():
    keyers = [st.make_keyer_stage() for _ in range(28)]
    letters = st.matte_letters_for(keyers)
    assert letters[keyers[25].id] == "Z"
    assert letters[keyers[26].id] == "AA"
    assert letters[keyers[27].id] == "BB"
    print("matte_letters_for: past Z, doubles the next letter (AA, BB...) "
          "rather than spreadsheet-style AB/AC -- OK")


def test_matte_letters_for_does_not_shift_when_a_keyer_is_disabled():
    k1 = st.make_keyer_stage()
    k2 = st.make_keyer_stage(enabled=False)
    k3 = st.make_keyer_stage()
    letters = st.matte_letters_for([k1, k2, k3])
    assert letters == {k1.id: "A", k2.id: "B", k3.id: "C"}
    print("matte_letters_for: a disabled keyer keeps its own stable "
          "letter rather than every later letter shifting down -- OK")


def test_matte_gates_a_downstream_stage_per_pixel():
    image = _half_red_half_blue()
    keyer_stage = st.make_keyer_stage(working_colourspace=color_warper.REFERENCE_LINEAR_SPACE,
                                       hue_center=REDDISH_HUE, hue_width=20.0, softness=0.1)
    consumer = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer.params["matte_source_id"] = keyer_stage.id

    stack = st.Stack()
    stack.add_stage(keyer_stage)
    stack.add_stage(consumer)
    result = st.apply_stack_to_image_array(stack, image)

    red_half = result[:, :2]
    blue_half = result[:, 2:]
    assert np.all(red_half > image[:, :2] + 0.1), "keyed (red) half should be brightened"
    assert np.allclose(blue_half, image[:, 2:], atol=1e-6), \
        "unkeyed (blue) half should be untouched"
    print("matte_source_id: a downstream stage's opacity is gated per "
          "pixel by an upstream keyer's matte -- OK")


def test_matte_source_id_none_behaves_like_plain_scalar_opacity():
    """A stage with no matte_source_id at all must be provably unaffected
    by this whole feature -- same exact scalar-opacity blend as before
    any of this existed."""
    image = _half_red_half_blue()
    consumer = st.make_cdl_stage(offset=(0.3, 0.3, 0.3), opacity=0.5)
    stack = st.Stack()
    stack.add_stage(consumer)
    result = st.apply_stack_to_image_array(stack, image)
    direct = cdl.apply_cdl(image, offset=(0.3, 0.3, 0.3))
    expected = np.clip(image * 0.5 + direct * 0.5, 0.0, 1.0)
    assert np.allclose(result, expected, atol=1e-6)
    print("matte_source_id absent: identical to today's plain scalar "
          "opacity blend -- OK")


def test_matte_source_id_referencing_a_removed_keyer_falls_back_to_scalar_opacity():
    image = _half_red_half_blue()
    keyer_stage = st.make_keyer_stage(hue_center=0.0, hue_width=20.0)
    consumer = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer.params["matte_source_id"] = keyer_stage.id  # keyer never actually added to the stack

    stack = st.Stack()
    stack.add_stage(consumer)
    result = st.apply_stack_to_image_array(stack, image)
    direct = cdl.apply_cdl(image, offset=(0.3, 0.3, 0.3))
    assert np.allclose(result, np.clip(direct, 0.0, 1.0), atol=1e-6)
    print("matte_source_id pointing at a nonexistent keyer: silently "
          "falls back to full scalar opacity, no error -- OK")


def test_matte_source_id_referencing_a_downstream_keyer_is_ignored():
    """A consumer positioned BEFORE its own referenced keyer must not see
    a matte that hasn't been produced yet -- degrades to plain scalar
    opacity, same as a removed keyer."""
    image = _half_red_half_blue()
    consumer = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    keyer_stage = st.make_keyer_stage(hue_center=0.0, hue_width=20.0)
    consumer.params["matte_source_id"] = keyer_stage.id

    stack = st.Stack()
    stack.add_stage(consumer)  # consumer runs BEFORE the keyer that would feed it
    stack.add_stage(keyer_stage)
    result = st.apply_stack_to_image_array(stack, image)
    direct = cdl.apply_cdl(image, offset=(0.3, 0.3, 0.3))
    assert np.allclose(result, np.clip(direct, 0.0, 1.0), atol=1e-6)
    print("matte_source_id pointing at a keyer that hasn't run yet "
          "(sits downstream): ignored, falls back to scalar opacity -- OK")


def test_matte_amount_is_scaled_by_the_keyers_own_opacity():
    image = _half_red_half_blue()
    keyer_full = st.make_keyer_stage(hue_center=0.0, hue_width=20.0, softness=0.1, opacity=1.0)
    keyer_half = st.make_keyer_stage(hue_center=0.0, hue_width=20.0, softness=0.1, opacity=0.5)
    consumer_full = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer_full.params["matte_source_id"] = keyer_full.id
    consumer_half = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer_half.params["matte_source_id"] = keyer_half.id

    stack_full = st.Stack()
    stack_full.add_stage(keyer_full)
    stack_full.add_stage(consumer_full)
    stack_half = st.Stack()
    stack_half.add_stage(keyer_half)
    stack_half.add_stage(consumer_half)

    result_full = st.apply_stack_to_image_array(stack_full, image)
    result_half = st.apply_stack_to_image_array(stack_half, image)
    red_delta_full = result_full[:, :2] - image[:, :2]
    red_delta_half = result_half[:, :2] - image[:, :2]
    assert np.all(red_delta_half > 1e-6)
    assert np.all(red_delta_half < red_delta_full - 1e-6), \
        "halving the keyer's own opacity should halve the matte amount, weakening the gated effect"
    print("keyer opacity is repurposed as \"matte amount\" -- halving it "
          "weakens (without zeroing) a downstream consumer's gated effect -- OK")


def test_matte_gating_works_inside_a_fork_branch_and_after_the_merge():
    """Per this module's docstring: mattes are shared across branches --
    a keyer on branch 'a' should still be visible to a consumer on branch
    'b' and to a consumer after the merge. Both branches start identical
    right after the split, so the keyer's own matte (computed from branch
    a's own image at that point) is numerically exactly what branch b's
    own pixels would key to as well."""
    image = _half_red_half_blue()
    keyer_stage = st.make_keyer_stage(working_colourspace=color_warper.REFERENCE_LINEAR_SPACE,
                                       hue_center=REDDISH_HUE, hue_width=20.0, softness=0.1)
    keyer_stage.branch = "a"
    consumer_b = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer_b.params["matte_source_id"] = keyer_stage.id
    consumer_b.branch = "b"
    after = st.make_cdl_stage(offset=(0.2, 0.2, 0.2))
    after.params["matte_source_id"] = keyer_stage.id

    stack = st.Stack()
    stack.add_stage(st.make_fork_split_stage())
    stack.add_stage(keyer_stage)
    stack.add_stage(consumer_b)
    stack.add_stage(st.make_fork_merge_stage(blend=0.5))
    stack.add_stage(after)

    result = st.apply_stack_to_image_array(stack, image)
    # Red half: branch b's +0.3 offset gated in fully, blended 50/50
    # against branch a's untouched output, then `after`'s own +0.2 gated
    # in fully on top -- (image + 0.15) + 0.2.
    expected_red = np.clip(image[:, :2] + 0.15 + 0.2, 0.0, 1.0)
    # Blue half: keyer's matte is ~0 there, so neither consumer_b's nor
    # after's offset should show up at all.
    assert np.allclose(result[:, :2], expected_red, atol=1e-3)
    assert np.allclose(result[:, 2:], image[:, 2:], atol=1e-3)
    print("matte gating composes correctly across a fork branch and "
          "after the merge, with the expected numeric result -- OK")


def test_stage_cache_propagates_matte_across_a_block_boundary():
    """The regression this feature's own StageCache fix guards against:
    an ordinary stage is its own cache BLOCK (_partition_into_blocks), so
    a keyer and its consumer are almost always in different blocks --
    without StageCache's own _block_mattes bookkeeping, a cached bake
    would silently lose the matte the moment the keyer's own block gets
    reused rather than re-run."""
    cache = st.StageCache()
    image = _half_red_half_blue(size=4).astype(np.float32)
    keyer_stage = st.make_keyer_stage(hue_center=0.0, hue_width=20.0, softness=0.1)
    consumer = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer.params["matte_source_id"] = keyer_stage.id
    unrelated = st.make_cdl_stage(offset=(0.0, 0.0, 0.0))

    stack = st.Stack()
    stack.add_stage(keyer_stage)
    stack.add_stage(consumer)
    stack.add_stage(unrelated)

    def check():
        cached = apply_stack_to_image_array_with_cache(stack, image, cache, input_key=1)
        uncached = st.apply_stack_to_image_array(stack, image)
        assert np.allclose(cached, uncached, atol=1e-6)

    check()  # first bake -- nothing cached yet
    unrelated.params["offset"] = (0.0, 0.0, 0.1)
    check()  # edit a LATER, unrelated block -- keyer's block gets reused from cache, not re-run
    keyer_stage.params["hue_center"] = 240.0  # re-key toward blue instead of red
    check()  # edit the keyer itself -- its own block AND everything after it must re-run
    print("StageCache: a matte survives being carried across a cache "
          "block boundary, including when the keyer's own block is "
          "reused (not re-run) on a later, unrelated edit -- OK")


def test_stage_cache_last_mattes_is_empty_before_any_run():
    cache = st.StageCache()
    assert cache.last_mattes() == {}
    print("StageCache.last_mattes(): {} before run() has ever been "
          "called -- OK")


def test_stage_cache_last_mattes_reflects_the_most_recent_run():
    cache = st.StageCache()
    image = _half_red_half_blue(size=4).astype(np.float32)
    keyer_stage = st.make_keyer_stage(working_colourspace=color_warper.REFERENCE_LINEAR_SPACE,
                                       hue_center=REDDISH_HUE, hue_width=20.0, softness=0.1)
    consumer = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer.params["matte_source_id"] = keyer_stage.id
    stack = st.Stack(stages=[keyer_stage, consumer])

    apply_stack_to_image_array_with_cache(stack, image, cache, input_key=1)
    mattes = cache.last_mattes()
    assert set(mattes.keys()) == {keyer_stage.id}
    assert mattes[keyer_stage.id].shape == image.shape[:2]
    # Red half keyed near 1.0, blue half near 0.0 -- same qualifier
    # shape test_stack.py's own matte-gating tests already establish.
    assert np.all(mattes[keyer_stage.id][:, :2] > 0.9)
    assert np.all(mattes[keyer_stage.id][:, 2:] < 0.1)
    print("StageCache.last_mattes(): reflects the most recent run's own "
          "matte-producing stages -- OK")


def test_stage_cache_last_mattes_stays_correct_when_the_keyers_block_is_reused():
    """The exact scenario Highlight mode needs: after an edit to a
    LATER, unrelated block, the keyer's own block is reused from cache
    (not re-run) -- last_mattes() must still reflect it correctly, via
    the SAME _block_mattes snapshot mechanism
    test_stage_cache_propagates_matte_across_a_block_boundary already
    proves keeps consumers working."""
    cache = st.StageCache()
    image = _half_red_half_blue(size=4).astype(np.float32)
    keyer_stage = st.make_keyer_stage(hue_center=0.0, hue_width=20.0, softness=0.1)
    unrelated = st.make_cdl_stage(offset=(0.0, 0.0, 0.0))
    stack = st.Stack(stages=[keyer_stage, unrelated])

    apply_stack_to_image_array_with_cache(stack, image, cache, input_key=1)
    first_matte = cache.last_mattes()[keyer_stage.id]

    unrelated.params["offset"] = (0.0, 0.0, 0.1)  # edit a LATER, unrelated block
    apply_stack_to_image_array_with_cache(stack, image, cache, input_key=1)
    reused_matte = cache.last_mattes()[keyer_stage.id]

    assert np.array_equal(first_matte, reused_matte)
    print("StageCache.last_mattes(): still correct after the keyer's "
          "own block was reused from cache rather than re-run -- OK")


# --- segment_mask stage (segmentation-groundwork second matte-producer) ---

def test_segment_mask_stage_is_identity_passthrough_for_the_image():
    image = _half_red_half_blue()
    stage = st.make_segment_mask_stage(points=[{"x": 0.25, "y": 0.5}])
    stack = st.Stack()
    stack.add_stage(stage)
    result = st.apply_stack_to_image_array(stack, image)
    assert np.allclose(result, image, atol=1e-9)
    print("segment_mask stage: never grades the image itself, regardless "
          "of its own seed points -- OK")


def test_matte_letters_for_shares_one_sequence_across_both_matte_kinds():
    """A keyer and a segment_mask interleaved in the stack share ONE
    letter sequence (A, B, C...) in stack order, not separate per-kind
    sequences -- what produced a matte doesn't matter to how it's
    referenced downstream."""
    k1 = st.make_keyer_stage()
    seg = st.make_segment_mask_stage()
    k2 = st.make_keyer_stage()
    letters = st.matte_letters_for([k1, seg, k2])
    assert letters == {k1.id: "A", seg.id: "B", k2.id: "C"}
    print("matte_letters_for: 'keyer' and 'segment_mask' share one "
          "interleaved letter sequence -- OK")


def test_matte_gates_a_downstream_stage_per_pixel_via_segment_mask():
    image = _half_red_half_blue()
    seg = st.make_segment_mask_stage(points=[{"x": 0.25, "y": 0.5}], tolerance=0.15)
    consumer = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer.params["matte_source_id"] = seg.id

    stack = st.Stack()
    stack.add_stage(seg)
    stack.add_stage(consumer)
    result = st.apply_stack_to_image_array(stack, image)

    red_half = result[:, :2]
    blue_half = result[:, 2:]
    assert np.all(red_half > image[:, :2] + 0.1), "the seeded (red) half should be brightened"
    assert np.allclose(blue_half, image[:, 2:], atol=1e-6), \
        "the unseeded (blue) half should be untouched"
    print("matte_source_id: a downstream stage's opacity is gated per "
          "pixel by an upstream segment_mask's matte -- OK")


def test_segment_mask_matte_amount_is_scaled_by_its_own_opacity():
    image = _half_red_half_blue()
    seg_full = st.make_segment_mask_stage(points=[{"x": 0.25, "y": 0.5}], tolerance=0.15, opacity=1.0)
    seg_half = st.make_segment_mask_stage(points=[{"x": 0.25, "y": 0.5}], tolerance=0.15, opacity=0.5)
    consumer_full = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer_full.params["matte_source_id"] = seg_full.id
    consumer_half = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer_half.params["matte_source_id"] = seg_half.id

    stack_full = st.Stack(stages=[seg_full, consumer_full])
    stack_half = st.Stack(stages=[seg_half, consumer_half])
    result_full = st.apply_stack_to_image_array(stack_full, image)
    result_half = st.apply_stack_to_image_array(stack_half, image)
    delta_full = result_full[:, :2] - image[:, :2]
    delta_half = result_half[:, :2] - image[:, :2]
    assert np.all(delta_half > 1e-6)
    assert np.all(delta_half < delta_full - 1e-6)
    print("segment_mask opacity is repurposed as \"matte amount\" too, "
          "same as keyer's own -- OK")


if __name__ == "__main__":
    test_stage_rejects_unknown_kind()
    test_make_lut_stage_retarget_requires_both_or_neither()
    test_add_remove_find_move_stage()
    test_empty_stack_bakes_to_plain_identity()
    test_empty_stack_apply_to_image_is_noop()
    test_single_lut_stage_matches_direct_apply()
    test_lut_stage_with_retarget_matches_bake_retargeted_lut()
    test_lut_stage_with_empty_string_retarget_spaces_fails_loudly()
    test_printer_lights_stage_matches_bake_printer_lights_on_lut()
    test_aces_drt_stage_matches_bake_aces_output_transform_to_lut3d()
    test_cdl_stage_matches_direct_apply_cdl()
    test_cdl_stage_composes_after_a_lut_stage()
    test_color_warper_stage_matches_direct_apply()
    test_color_warper_stage_default_is_identity()
    test_grading_primary_stage_matches_direct_apply()
    test_grading_primary_stage_composes_after_a_lut_stage()
    test_exposure_contrast_stage_matches_direct_apply()
    test_exposure_contrast_stage_composes_after_a_lut_stage()
    test_grading_tone_stage_matches_direct_apply()
    test_grading_tone_stage_composes_after_a_lut_stage()
    test_grading_rgb_curve_stage_matches_direct_apply()
    test_grading_rgb_curve_stage_composes_after_a_lut_stage()
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
    test_domain_min_max_actually_scale_the_baked_table()
    test_resize_recomputes_natively_more_accurately_than_resampling()
    test_find_enclosing_fork_region_before_split_and_after_merge_is_none()
    test_find_enclosing_fork_region_between_split_and_merge_is_inside()
    test_find_enclosing_fork_region_with_no_fork_at_all_is_none()
    test_fork_region_branch_counts_reflects_current_occupancy()
    test_find_fork_partner_via_split_id_with_stages_on_both_branches()
    test_find_fork_partner_via_merge_id_with_one_branch_empty()
    test_find_fork_partner_returns_none_for_a_non_fork_stage()
    test_find_fork_partner_returns_none_when_no_matching_marker_exists()
    test_stage_cache_matches_uncached_result_across_edits()
    test_stage_cache_skips_recompute_of_untouched_stages()
    test_stage_cache_apply_to_image_array_matches_uncached_and_respects_input_key()
    test_stage_cache_handles_fork_regions_as_one_block()
    test_keyer_stage_is_identity_passthrough_for_the_image()
    test_matte_letters_for_assigns_sequential_letters_to_keyer_stages_only()
    test_matte_letters_for_doubles_past_26()
    test_matte_letters_for_does_not_shift_when_a_keyer_is_disabled()
    test_matte_gates_a_downstream_stage_per_pixel()
    test_matte_source_id_none_behaves_like_plain_scalar_opacity()
    test_matte_source_id_referencing_a_removed_keyer_falls_back_to_scalar_opacity()
    test_matte_source_id_referencing_a_downstream_keyer_is_ignored()
    test_matte_amount_is_scaled_by_the_keyers_own_opacity()
    test_matte_gating_works_inside_a_fork_branch_and_after_the_merge()
    test_stage_cache_propagates_matte_across_a_block_boundary()
    test_stage_cache_last_mattes_is_empty_before_any_run()
    test_stage_cache_last_mattes_reflects_the_most_recent_run()
    test_stage_cache_last_mattes_stays_correct_when_the_keyers_block_is_reused()
    test_segment_mask_stage_is_identity_passthrough_for_the_image()
    test_matte_letters_for_shares_one_sequence_across_both_matte_kinds()
    test_matte_gates_a_downstream_stage_per_pixel_via_segment_mask()
    test_segment_mask_matte_amount_is_scaled_by_its_own_opacity()
    print()
    print("All stack tests passed.")
