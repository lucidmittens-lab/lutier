"""Tests for session_io.py -- run directly: python3 test_session_io.py

Focused on the LUT-embedding additions (VERSION 4: lut_library +
"embedded" lut-stage source) -- the whole point being that a session (or
a single Stack stage built from the LUT Library) survives being opened
somewhere that's never seen the original file, since the full table is
written directly into the .ltr JSON rather than a path reference. See
lut_library.py's docstring and session_io.py's docstring on LUT stage
provenance for the reasoning.
"""
import json
import os
import tempfile

import av
import numpy as np
import colour
from PIL import Image

import canvas as cv
import color_warper
import custom_drt
import keyer
import exposure_contrast
import grading_primary
import grading_rgb_curve
import grading_tone
import session_io
import stack as st
import stack_memory as st_mem
import timeline as tl
import thumbnail
import media_cache
from lut_library import LUTLibraryEntry, LUTLibrary
from media_library import MediaEntry, MediaLibrary, MediaPool
from undo_history import UndoHistory


def _make_test_clip(path, frame_count=6, frame_rate=24, width=16, height=12):
    """Same tiny-real-encoded-clip fixture convention as
    test_video_source.py's own _make_test_clip -- a genuinely decodable
    file, not a mock, so media_library_from_session_list's video branch
    is exercised for real."""
    container = av.open(path, mode="w")
    stream = container.add_stream("libx264", rate=frame_rate)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    for i in range(frame_count):
        color = (min(255, i * 20), max(0, 255 - i * 20), 128)
        frame = av.VideoFrame.from_ndarray(
            np.full((height, width, 3), color, dtype=np.uint8), format="rgb24")
        frame.pts = i
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def _make_lut3d(gain=2.0, size=3, name="test_lut"):
    identity = colour.LUT3D(size=size).table
    return colour.LUT3D(table=identity * gain, size=size, name=name)


def _make_lut1d(name="test_1d"):
    table = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    return colour.LUT1D(table=table, name=name)


# ---- lut_to_embedded_dict / embedded_dict_to_lut ---------------------------

def test_embedded_dict_round_trips_lut3d_table_exactly():
    lut = _make_lut3d(gain=1.7, size=4, name="Roundtrip3D")
    data = session_io.lut_to_embedded_dict(lut)
    # Must be plain JSON-able (no numpy types) -- prove it by actually
    # round-tripping through json.dumps/loads, not just trusting the dict.
    restored_data = json.loads(json.dumps(data))
    rebuilt = session_io.embedded_dict_to_lut(restored_data)
    assert isinstance(rebuilt, colour.LUT3D)
    assert rebuilt.name == "Roundtrip3D"
    assert np.allclose(rebuilt.table, lut.table)
    assert np.allclose(rebuilt.domain, lut.domain)
    print("lut_to_embedded_dict/embedded_dict_to_lut: a LUT3D's table/"
          "domain/name survive a JSON round trip exactly -- OK")


def test_embedded_dict_round_trips_lut1d_table_exactly():
    lut = _make_lut1d(name="Roundtrip1D")
    data = session_io.lut_to_embedded_dict(lut)
    restored_data = json.loads(json.dumps(data))
    rebuilt = session_io.embedded_dict_to_lut(restored_data)
    assert isinstance(rebuilt, colour.LUT1D)
    assert rebuilt.name == "Roundtrip1D"
    assert np.allclose(rebuilt.table, lut.table)
    print("lut_to_embedded_dict/embedded_dict_to_lut: a LUT1D round-trips "
          "as a LUT1D (not misclassified as a LUT3D), table intact -- OK")


def test_embedded_dict_distinguishes_1d_from_3d_by_table_ndim():
    lut3d = _make_lut3d(size=3)
    lut1d = _make_lut1d()
    assert session_io.lut_to_embedded_dict(lut3d)["ndim"] == 4
    assert session_io.lut_to_embedded_dict(lut1d)["ndim"] in (1, 2)
    print("lut_to_embedded_dict: records table ndim (4 for LUT3D, 1/2 for "
          "LUT1D) so embedded_dict_to_lut can pick the right class back "
          "-- OK")


# ---- stack_to_session_dict / stack_from_session_dict "embedded" source ----

def test_embedded_lut_stage_round_trips_without_any_file():
    lut = _make_lut3d(gain=3.0, size=4, name="EmbeddedStage")
    stage = st.make_lut_stage(lut, label="From Library")
    stage.params["source_kind"] = "embedded"
    stack = st.Stack(stages=[stage])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    assert len(rebuilt_stack.stages) == 1
    rebuilt_stage = rebuilt_stack.stages[0]
    assert rebuilt_stage.params["source_kind"] == "embedded"
    assert np.allclose(rebuilt_stage.params["lut"].table, lut.table)
    print("stack_to_session_dict/stack_from_session_dict: an 'embedded' "
          "lut stage round-trips its full table through plain JSON with "
          "no file path at all -- OK")


def stack_to_session_dict_round_trip_via_json(stack):
    """Helper -- serialize then immediately re-parse as JSON, so the test
    actually proves the dict is JSON-safe (matches how save_session/
    load_session really move this data through a file)."""
    return json.loads(json.dumps(session_io.stack_to_session_dict(stack)))


# ---- stack forking (fork_split/fork_merge + Stage.branch tags) -----------

def _make_embedded_lut_stage(gain, size=4, name="branch", **stage_kwargs):
    """An 'embedded' lut stage (see test_embedded_lut_stage_round_trips_
    without_any_file above) -- JSON-safe with no real file needed, so it
    makes a good branch-tagged stage for a round-trip test."""
    stage = st.make_lut_stage(_make_lut3d(gain=gain, size=size, name=name), **stage_kwargs)
    stage.params["source_kind"] = "embedded"
    return stage


def test_fork_split_and_merge_round_trip_with_branch_tagged_stages():
    """fork_split, two branch-tagged lut stages (one per branch), and
    fork_merge all round-trip through plain JSON as an ordinary flat
    stage list -- no recursion, no special-casing beyond restoring each
    entry's "branch" field (see session_io.py's docstring, STACK
    FORKING)."""
    split = st.make_fork_split_stage(label="Split")
    branch_a = _make_embedded_lut_stage(0.4, name="BranchA", label="Branch A")
    branch_a.branch = "a"
    branch_b = _make_embedded_lut_stage(2.0, name="BranchB", label="Branch B", opacity=0.75)
    branch_b.branch = "b"
    merge = st.make_fork_merge_stage(blend=0.3, label="Merge", opacity=0.9)
    stack = st.Stack(stages=[split, branch_a, branch_b, merge])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    assert len(rebuilt_stack.stages) == 4
    r_split, r_a, r_b, r_merge = rebuilt_stack.stages

    assert r_split.kind == "fork_split" and r_split.label == "Split" and r_split.branch is None
    assert r_a.branch == "a" and r_a.label == "Branch A"
    assert r_b.branch == "b" and r_b.label == "Branch B" and r_b.opacity == 0.75
    assert r_merge.kind == "fork_merge" and r_merge.label == "Merge"
    assert r_merge.opacity == 0.9
    assert r_merge.params["blend"] == 0.3
    assert np.allclose(r_a.params["lut"].table, branch_a.params["lut"].table)
    assert np.allclose(r_b.params["lut"].table, branch_b.params["lut"].table)

    # And the rebuilt region actually bakes to the same thing the
    # original does -- not just matching field-by-field, but
    # functionally identical.
    original_baked = st.bake_stack_to_lut3d(st.Stack(stages=stack.stages), st.BakeSettings(size=5))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(stages=rebuilt_stack.stages), st.BakeSettings(size=5))
    assert np.allclose(original_baked.table, rebuilt_baked.table, atol=1e-9)
    print("stack_to_session_dict/stack_from_session_dict: a fork_split/"
          "fork_merge region with a branch-tagged stage on each side "
          "round-trips through plain JSON as an ordinary flat stage list, "
          "and bakes identically to the original -- OK")


def test_grading_primary_stage_round_trips():
    if not grading_primary.OCIO_AVAILABLE:
        print("SKIPPED test_grading_primary_stage_round_trips -- PyOpenColorIO not installed")
        return
    stage = st.make_grading_primary_stage(
        style="video", brightness=(0.0, 0.0, 0.0, 0.0), contrast=(1.0, 1.0, 1.0, 1.0),
        gamma=(1.0, 1.0, 1.0, 1.0), lift=(0.01, -0.02, 0.0, 0.0), gain=(1.1, 0.95, 1.0, 1.0),
        pivot=-0.15, saturation=1.05, label="My Grade", opacity=0.8,
    )
    stack = st.Stack(stages=[stage])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    assert len(rebuilt_stack.stages) == 1
    r = rebuilt_stack.stages[0]
    assert r.kind == "grading_primary" and r.label == "My Grade" and r.opacity == 0.8
    assert r.params["style"] == "video"
    assert tuple(r.params["lift"]) == (0.01, -0.02, 0.0, 0.0)
    assert tuple(r.params["gain"]) == (1.1, 0.95, 1.0, 1.0)
    assert r.params["pivot"] == -0.15
    assert r.params["saturation"] == 1.05

    original_baked = st.bake_stack_to_lut3d(st.Stack(stages=stack.stages), st.BakeSettings(size=5))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(stages=rebuilt_stack.stages), st.BakeSettings(size=5))
    assert np.allclose(original_baked.table, rebuilt_baked.table, atol=1e-9)
    print("stack_to_session_dict/stack_from_session_dict: a 'grading_primary' "
          "stage round-trips through plain JSON and bakes identically -- OK")


def test_exposure_contrast_stage_round_trips():
    if not exposure_contrast.OCIO_AVAILABLE:
        print("SKIPPED test_exposure_contrast_stage_round_trips -- PyOpenColorIO not installed")
        return
    stage = st.make_exposure_contrast_stage(
        style="log", exposure=0.4, contrast=1.15, gamma=1.05, label="My EC", opacity=0.8)
    stack = st.Stack(stages=[stage])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    assert len(rebuilt_stack.stages) == 1
    r = rebuilt_stack.stages[0]
    assert r.kind == "exposure_contrast" and r.label == "My EC" and r.opacity == 0.8
    assert r.params["style"] == "log"
    assert r.params["exposure"] == 0.4
    assert r.params["contrast"] == 1.15
    assert r.params["gamma"] == 1.05

    original_baked = st.bake_stack_to_lut3d(st.Stack(stages=stack.stages), st.BakeSettings(size=5))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(stages=rebuilt_stack.stages), st.BakeSettings(size=5))
    assert np.allclose(original_baked.table, rebuilt_baked.table, atol=1e-9)
    print("stack_to_session_dict/stack_from_session_dict: an 'exposure_contrast' "
          "stage round-trips through plain JSON and bakes identically -- OK")


def test_custom_drt_stage_round_trips():
    """Regression test: an earlier version had no save-path branch for
    'custom_drt' at all, so saving a session with a CAMier stage raised
    (Tom, 2026-08-13, "if you try to save session with this op, it
    cannot")."""
    if not custom_drt.aces_drt.OCIO_AVAILABLE:
        print("SKIPPED test_custom_drt_stage_round_trips -- PyOpenColorIO not installed")
        return
    stage = st.make_custom_drt_stage(
        "ARRI LogC4", contrast=0.96, headroom=0.6, blend_percentile=0.95,
        tail_width=0.5, desat_start=0.87, desat_end=1.0, identity_zone=0.8,
        transition_softness=0.3, fullness_r=0.1, fullness_g=0.0, fullness_b=0.04,
        mastering_display="HDR", final_target="P3-D65", mastering_brightness=1000,
        label="My CAMier", opacity=0.9)
    stack = st.Stack(stages=[stage])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    assert len(rebuilt_stack.stages) == 1
    r = rebuilt_stack.stages[0]
    assert r.kind == "custom_drt" and r.label == "My CAMier" and r.opacity == 0.9
    assert r.params["working_colourspace"] == "ARRI LogC4"
    assert r.params["contrast"] == 0.96
    assert r.params["headroom"] == 0.6
    assert r.params["blend_percentile"] == 0.95
    assert r.params["tail_width"] == 0.5
    assert r.params["desat_start"] == 0.87
    assert r.params["identity_zone"] == 0.8
    assert r.params["transition_softness"] == 0.3
    assert r.params["fullness_r"] == 0.1
    assert r.params["fullness_g"] == 0.0
    assert r.params["fullness_b"] == 0.04
    assert r.params["mastering_display"] == "HDR"
    assert r.params["final_target"] == "P3-D65"
    assert r.params["mastering_brightness"] == 1000

    original_baked = st.bake_stack_to_lut3d(st.Stack(stages=stack.stages), st.BakeSettings(size=5))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(stages=rebuilt_stack.stages), st.BakeSettings(size=5))
    assert np.allclose(original_baked.table, rebuilt_baked.table, atol=1e-9)
    print("stack_to_session_dict/stack_from_session_dict: a 'custom_drt' "
          "stage round-trips through plain JSON and bakes identically -- OK")


def test_grading_tone_stage_round_trips():
    if not grading_tone.OCIO_AVAILABLE:
        print("SKIPPED test_grading_tone_stage_round_trips -- PyOpenColorIO not installed")
        return
    midtones = (1.05, 0.98, 1.02, 1.0, 0.4, 0.6)
    stage = st.make_grading_tone_stage(
        style="video", midtones=midtones, scontrast=1.2, label="My Tone", opacity=0.8)
    stack = st.Stack(stages=[stage])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    assert len(rebuilt_stack.stages) == 1
    r = rebuilt_stack.stages[0]
    assert r.kind == "grading_tone" and r.label == "My Tone" and r.opacity == 0.8
    assert r.params["style"] == "video"
    assert tuple(r.params["midtones"]) == midtones
    assert tuple(r.params["blacks"]) == grading_tone.DEFAULT_RANGE_VALUES["blacks"]
    assert r.params["scontrast"] == 1.2

    original_baked = st.bake_stack_to_lut3d(st.Stack(stages=stack.stages), st.BakeSettings(size=5))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(stages=rebuilt_stack.stages), st.BakeSettings(size=5))
    assert np.allclose(original_baked.table, rebuilt_baked.table, atol=1e-9)
    print("stack_to_session_dict/stack_from_session_dict: a 'grading_tone' "
          "stage round-trips through plain JSON and bakes identically -- OK")


def test_grading_rgb_curve_stage_round_trips():
    if not grading_rgb_curve.OCIO_AVAILABLE:
        print("SKIPPED test_grading_rgb_curve_stage_round_trips -- PyOpenColorIO not installed")
        return
    master = ((0.0, 0.0), (0.5, 0.62), (1.0, 1.0))
    red = ((0.0, 0.0), (0.3, 0.25), (1.0, 1.0))
    stage = st.make_grading_rgb_curve_stage(
        style="video", master=master, red=red, label="My Curve", opacity=0.8)
    stack = st.Stack(stages=[stage])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    assert len(rebuilt_stack.stages) == 1
    r = rebuilt_stack.stages[0]
    assert r.kind == "grading_rgb_curve" and r.label == "My Curve" and r.opacity == 0.8
    assert r.params["style"] == "video"
    assert tuple(tuple(p) for p in r.params["master"]) == master
    assert tuple(tuple(p) for p in r.params["red"]) == red
    assert tuple(tuple(p) for p in r.params["green"]) == grading_rgb_curve.DEFAULT_CONTROL_POINTS

    original_baked = st.bake_stack_to_lut3d(st.Stack(stages=stack.stages), st.BakeSettings(size=5))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(stages=rebuilt_stack.stages), st.BakeSettings(size=5))
    assert np.allclose(original_baked.table, rebuilt_baked.table, atol=1e-9)
    print("stack_to_session_dict/stack_from_session_dict: a 'grading_rgb_curve' "
          "stage round-trips through plain JSON and bakes identically -- OK")


def test_color_warper_stage_round_trips():
    points = [
        {"h0": 35.0, "c0": 0.15, "h": 55.0, "c": 0.22},
        {"h0": 250.0, "c0": 0.1, "h": 250.0, "c": 0.02},
    ]
    stage = st.make_color_warper_stage(
        working_colourspace="ACEScg", points=points, wake=42.0, dropoff=0.4,
        chroma_wake=0.08, chroma_dropoff=0.6,
        label="My Warp", opacity=0.7)
    stack = st.Stack(stages=[stage])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    assert len(rebuilt_stack.stages) == 1
    r = rebuilt_stack.stages[0]
    assert r.kind == "color_warper" and r.label == "My Warp" and r.opacity == 0.7
    assert r.params["working_colourspace"] == "ACEScg"
    assert r.params["wake"] == 42.0
    assert r.params["dropoff"] == 0.4
    assert r.params["chroma_wake"] == 0.08
    assert r.params["chroma_dropoff"] == 0.6
    assert r.params["points"] == points

    original_baked = st.bake_stack_to_lut3d(st.Stack(stages=stack.stages), st.BakeSettings(size=5))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(stages=rebuilt_stack.stages), st.BakeSettings(size=5))
    assert np.allclose(original_baked.table, rebuilt_baked.table, atol=1e-9)
    print("stack_to_session_dict/stack_from_session_dict: a 'color_warper' "
          "stage round-trips through plain JSON and bakes identically -- OK")


def test_color_warper_stage_default_points_round_trip():
    """A freshly-added stage (no explicit points arg -- an empty list,
    no seeded defaults, per Tom) should still round-trip -- entry.get(
    "points") on load must not silently turn an empty list into None or
    something else."""
    stage = st.make_color_warper_stage(working_colourspace="ACEScct")
    stack = st.Stack(stages=[stage])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    r = rebuilt_stack.stages[0]
    assert r.params["points"] == []
    assert color_warper.points_are_identity(r.params["points"])
    print("stack_to_session_dict/stack_from_session_dict: a freshly-added "
          "'color_warper' stage's empty points list round-trips -- OK")


def test_keyer_stage_round_trips():
    stage = st.make_keyer_stage(
        working_colourspace="ACEScct",
        hue_center=210.0, hue_width=45.0, chroma_min=0.2, chroma_max=0.35,
        lightness_min=0.1, lightness_max=0.8, softness=0.6, invert=True,
        prefilter_blur=2.5, matte_blur=1.25, clip_black=0.05, clip_white=0.95,
        label="My Keyer", opacity=0.8)
    stack = st.Stack(stages=[stage])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    assert len(rebuilt_stack.stages) == 1
    r = rebuilt_stack.stages[0]
    assert r.kind == "keyer" and r.label == "My Keyer" and r.opacity == 0.8
    assert r.params["working_colourspace"] == "ACEScct"
    assert r.params["hue_center"] == 210.0
    assert r.params["hue_width"] == 45.0
    assert r.params["chroma_min"] == 0.2
    assert r.params["chroma_max"] == 0.35
    assert r.params["lightness_min"] == 0.1
    assert r.params["lightness_max"] == 0.8
    assert r.params["softness"] == 0.6
    assert r.params["invert"] is True
    assert r.params["prefilter_blur"] == 2.5
    assert r.params["matte_blur"] == 1.25
    assert r.params["clip_black"] == 0.05
    assert r.params["clip_white"] == 0.95
    print("stack_to_session_dict/stack_from_session_dict: a 'keyer' stage "
          "round-trips through plain JSON -- OK")


def test_keyer_stage_prefilter_and_clip_default_on_load_when_absent():
    """An old saved session (from before Phase 4) never wrote prefilter_
    blur/clip_black/clip_white at all -- loading it must silently fall
    back to keyer.py's own no-op defaults, not KeyError."""
    stage = st.make_keyer_stage(hue_center=10.0)
    stack = st.Stack(stages=[stage])
    data = stack_to_session_dict_round_trip_via_json(stack)
    del data["stages"][0]["prefilter_blur"]
    del data["stages"][0]["matte_blur"]
    del data["stages"][0]["clip_black"]
    del data["stages"][0]["clip_white"]

    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    r = rebuilt_stack.stages[0]
    assert r.params["prefilter_blur"] == keyer.DEFAULT_PREFILTER_BLUR
    assert r.params["matte_blur"] == keyer.DEFAULT_MATTE_BLUR
    assert r.params["clip_black"] == keyer.DEFAULT_CLIP_BLACK
    assert r.params["clip_white"] == keyer.DEFAULT_CLIP_WHITE
    print("stack_from_session_dict: an old session missing prefilter_blur/"
          "matte_blur/clip_black/clip_white falls back to keyer.py's own "
          "no-op defaults -- OK")


def test_matte_combine_stage_round_trips_via_position():
    """Like matte_source_id, matte_a_source_id/matte_b_source_id are raw
    Stage.id values -- only meaningful within THIS in-memory Stack -- so
    they round-trip via POSITION (matte_a_source_index/matte_b_source_
    index), resolving to whatever fresh id the referenced stage gets
    reconstructed with, exactly like the single-reference case already
    covered by test_matte_source_id_round_trips_via_position_not_raw_id
    just below."""
    k1 = st.make_keyer_stage(hue_center=10.0)
    k2 = st.make_keyer_stage(hue_center=200.0)
    combine = st.make_matte_combine_stage(
        matte_a_source_id=k1.id, matte_b_source_id=k2.id, op="subtract",
        label="My Combine", opacity=0.6)
    stack = st.Stack(stages=[k1, k2, combine])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    assert len(rebuilt_stack.stages) == 3
    r_k1, r_k2, r_combine = rebuilt_stack.stages
    assert r_combine.kind == "matte_combine" and r_combine.label == "My Combine" and r_combine.opacity == 0.6
    assert r_combine.params["op"] == "subtract"
    assert r_combine.params["matte_a_source_id"] == r_k1.id
    assert r_combine.params["matte_b_source_id"] == r_k2.id
    assert r_combine.params["matte_a_source_id"] != k1.id, \
        "the rebuilt reference must point at the REBUILT keyer's own fresh id, not the stale original"
    print("stack_to_session_dict/stack_from_session_dict: a 'matte_combine' "
          "stage's two inputs round-trip via stack position, resolving to "
          "the rebuilt keyers' own fresh ids -- OK")


def test_matte_shape_stage_round_trips():
    """No id-reference translation needed here -- every matte_shape
    param is a plain value (unlike matte_combine's two inputs), so this
    is a direct field-for-field round trip."""
    stage = st.make_matte_shape_stage(
        shape="rectangle", center_x=0.3, center_y=0.7, width=0.6, height=0.4,
        rotation=45.0, softness=0.5, invert=True,
        label="My Shape", opacity=0.9)
    stack = st.Stack(stages=[stage])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert warnings == []
    assert len(rebuilt_stack.stages) == 1
    r = rebuilt_stack.stages[0]
    assert r.kind == "matte_shape" and r.label == "My Shape" and r.opacity == 0.9
    assert r.params["shape"] == "rectangle"
    assert r.params["center_x"] == 0.3
    assert r.params["center_y"] == 0.7
    assert r.params["width"] == 0.6
    assert r.params["height"] == 0.4
    assert r.params["rotation"] == 45.0
    assert r.params["softness"] == 0.5
    assert r.params["invert"] is True
    print("stack_to_session_dict/stack_from_session_dict: a "
          "'matte_shape' stage round-trips through plain JSON -- OK")


def test_matte_source_id_round_trips_via_position_not_raw_id():
    """The core new mechanism (see stack_to_session_dict's own docstring):
    a raw Stage.id is meaningless after reload (ids are reassigned fresh
    every time), so the reference has to survive via POSITION instead --
    confirmed here by checking the rebuilt consumer's matte_source_id
    points at the REBUILT keyer's own (necessarily different) id, and
    that gating still functions identically end to end."""
    keyer_stage = st.make_keyer_stage(hue_center=0.0, hue_width=20.0, softness=0.1)
    consumer = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer.params["matte_source_id"] = keyer_stage.id
    stack = st.Stack(stages=[keyer_stage, consumer])

    data = stack_to_session_dict_round_trip_via_json(stack)
    assert data["stages"][0]["matte_source_index"] is None  # the keyer itself references nothing
    assert data["stages"][1]["matte_source_index"] == 0  # the keyer's own position

    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)
    assert warnings == []
    rebuilt_keyer, rebuilt_consumer = rebuilt_stack.stages
    assert rebuilt_consumer.params["matte_source_id"] == rebuilt_keyer.id
    assert rebuilt_consumer.params["matte_source_id"] != keyer_stage.id, \
        "the rebuilt reference must point at the REBUILT keyer's own fresh id, not the stale original"

    image = np.zeros((2, 4, 3), dtype=np.float32)
    image[:, :2] = (0.6, 0.2, 0.2)
    image[:, 2:] = (0.2, 0.2, 0.6)
    original_result = st.apply_stack_to_image_array(stack, image)
    rebuilt_result = st.apply_stack_to_image_array(rebuilt_stack, image)
    assert np.allclose(original_result, rebuilt_result, atol=1e-6)
    print("stack_to_session_dict/stack_from_session_dict: matte_source_id "
          "round-trips via stack position, resolving to the rebuilt "
          "keyer's own fresh id, with identical gated grading output -- OK")


def test_matte_source_index_survives_an_earlier_skipped_stage():
    """The reconstructed-position mapping must stay correct even when an
    EARLIER stage in the original list fails to reconstruct and gets
    skipped -- the keyer's own position in the REBUILT stack shifts down
    by one, and the stored index must still resolve to it correctly (not
    to whatever now-different stage happens to sit at the ORIGINAL
    index)."""
    broken = st.make_lut_stage(_make_lut3d(size=3), label="Broken")
    broken.params["source_kind"] = "file"
    broken.params["source_path"] = "/nonexistent/path/gone.cube"
    keyer_stage = st.make_keyer_stage(hue_center=0.0, hue_width=20.0)
    consumer = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    consumer.params["matte_source_id"] = keyer_stage.id
    stack = st.Stack(stages=[broken, keyer_stage, consumer])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert len(warnings) == 1 and "Broken" in warnings[0]
    assert [s.kind for s in rebuilt_stack.stages] == ["keyer", "cdl"]
    rebuilt_keyer, rebuilt_consumer = rebuilt_stack.stages
    assert rebuilt_consumer.params["matte_source_id"] == rebuilt_keyer.id
    print("stack_from_session_dict: matte_source_index still resolves "
          "correctly after an EARLIER stage was skipped and every later "
          "position shifted -- OK")


def test_matte_source_index_pointing_at_a_skipped_stage_falls_back_to_none():
    broken = st.make_lut_stage(_make_lut3d(size=3), label="Broken")
    broken.params["source_kind"] = "file"
    broken.params["source_path"] = "/nonexistent/path/gone.cube"
    consumer = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    stack = st.Stack(stages=[broken, consumer])
    data = stack_to_session_dict_round_trip_via_json(stack)
    data["stages"][1]["matte_source_index"] = 0  # points at the doomed "Broken" stage

    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)
    assert len(warnings) == 1
    assert len(rebuilt_stack.stages) == 1
    assert rebuilt_stack.stages[0].params.get("matte_source_id") is None
    print("stack_from_session_dict: matte_source_index pointing at a "
          "stage that itself failed to reconstruct falls back to no "
          "matte, no crash -- OK")


def test_matte_source_index_out_of_range_falls_back_to_none():
    consumer = st.make_cdl_stage(offset=(0.3, 0.3, 0.3))
    stack = st.Stack(stages=[consumer])
    data = stack_to_session_dict_round_trip_via_json(stack)
    data["stages"][0]["matte_source_index"] = 99  # nothing at that position

    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)
    assert warnings == []
    assert rebuilt_stack.stages[0].params.get("matte_source_id") is None
    print("stack_from_session_dict: an out-of-range matte_source_index "
          "falls back to no matte rather than raising -- OK")


def test_branch_tagged_stage_skipped_individually_when_it_cant_reconstruct():
    """A branch-tagged stage that can't reconstruct (here: a 'file'
    source pointing nowhere) is skipped individually, exactly like any
    other unreconstructable stage -- unlike the earlier single-fork-stage
    design, there's no "whole fork" unit to lose; fork_split, the surviving
    branch's stage, and fork_merge all still load fine."""
    split = st.make_fork_split_stage(label="Split")
    good_branch = _make_embedded_lut_stage(0.4, name="Good")
    good_branch.branch = "a"
    broken_branch = st.make_lut_stage(_make_lut3d(gain=2.0, size=4, name="Broken"), label="Broken")
    broken_branch.params["source_kind"] = "file"
    broken_branch.params["source_path"] = "/nonexistent/path/gone.cube"
    broken_branch.branch = "b"
    merge = st.make_fork_merge_stage(label="Merge")
    stack = st.Stack(stages=[split, good_branch, broken_branch, merge])

    data = stack_to_session_dict_round_trip_via_json(stack)
    rebuilt_stack, warnings = session_io.stack_from_session_dict(data)

    assert [s.label for s in rebuilt_stack.stages] == ["Split", "Good", "Merge"]
    assert len(warnings) == 1
    assert "Broken" in warnings[0]
    print("stack_from_session_dict: a branch-tagged stage that can't "
          "reconstruct is skipped individually with its own warning, "
          "leaving fork_split/the other branch/fork_merge intact -- OK")


def test_stack_to_session_dict_raises_loudly_for_unreconstructable_lut_stage():
    """Regression guard: this used to silently return None from
    stage_to_session_dict and have stack_to_session_dict drop the stage
    with zero indication -- asymmetric with the load side
    (stage_from_session_dict), which always reports a reconstruction
    failure via warnings rather than discarding it silently. A "lut"
    stage with no recognizable source_kind at all (not "file"/"identity"/
    "embedded" -- shouldn't happen via any real GUI path, but this is
    exactly the "some other path built it" case the docstring warns
    about) must now fail the save loudly instead of quietly losing the
    stage."""
    stage = st.make_lut_stage(_make_lut3d(size=3), label="Mystery")
    stage.params["source_kind"] = "not_a_real_source_kind"
    stack = st.Stack(stages=[stage])
    try:
        session_io.stack_to_session_dict(stack)
        raise AssertionError("expected ValueError for an unreconstructable lut stage")
    except ValueError as e:
        assert "Mystery" in str(e)
    print("stack_to_session_dict: an unreconstructable 'lut' stage raises "
          "ValueError rather than being silently dropped -- OK")


def test_load_session_with_unknown_future_stage_kind_still_loads_others():
    """A stage entry with a genuinely unrecognized kind (e.g. from a
    newer app version) should be skipped with a warning like any other
    unreconstructable stage, without taking down stages around it in the
    same session."""
    good = _make_embedded_lut_stage(1.5, name="Good", label="Good Stage")
    stack = st.Stack(stages=[good])
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test_session.ltr")
        session_io.save_session(path, stack, None)
        with open(path) as f:
            data = json.load(f)
        # Splice in a stage entry from a hypothetical future kind.
        data["stack"]["stages"].append({"kind": "some_future_kind", "label": "Future"})
        with open(path, "w") as f:
            json.dump(data, f)

        (loaded_stack, _bake_settings, _sample_image_state, _memory_library, _gallery_still_library,
         _cdl_library, _lut_library, _media_library, _undo_history_state, _canvas, _timeline,
         warnings) = session_io.load_session(path)

        assert len(loaded_stack.stages) == 1
        assert loaded_stack.stages[0].label == "Good Stage"
        assert len(warnings) == 1
        assert "Future" in warnings[0]
    print("load_session: an unrecognized future stage kind is skipped with "
          "a warning, the rest of the stack loads fine -- OK")


# ---- lut_library_to_session_list / lut_library_from_session_list ---------

def test_lut_library_session_round_trip():
    library = LUTLibrary()
    entry = library.add_entry(LUTLibraryEntry(
        "Shot LUT", _make_lut3d(gain=0.8, size=3, name="ShotLUT"),
        source_filename="shot.cube", source_path="/some/path/shot.cube"))

    serialized = json.loads(json.dumps(session_io.lut_library_to_session_list(library)))
    rebuilt_library, warnings = session_io.lut_library_from_session_list(serialized)

    assert warnings == []
    assert len(rebuilt_library.entries) == 1
    rebuilt_entry = rebuilt_library.entries[0]
    assert rebuilt_entry.name == "Shot LUT"
    assert rebuilt_entry.source_filename == "shot.cube"
    assert np.allclose(rebuilt_entry.lut.table, entry.lut.table)
    print("lut_library_to_session_list/lut_library_from_session_list: a "
          "LUT Library entry's full table and provenance round-trip "
          "through plain JSON -- OK")


def test_lut_library_from_session_list_skips_bad_entry_with_warning():
    bad_data = [{"name": "Broken", "lut": {"table": "not a real table", "ndim": 4}}]
    library, warnings = session_io.lut_library_from_session_list(bad_data)
    assert len(library.entries) == 0
    assert len(warnings) == 1
    assert "Broken" in warnings[0]
    print("lut_library_from_session_list: a malformed entry is skipped "
          "with a warning, doesn't raise -- OK")


# ---- full save_session/load_session round trip ---------------------------

def test_save_and_load_session_round_trips_lut_library_and_embedded_stage():
    lut_library = LUTLibrary()
    lut_library.add_entry(LUTLibraryEntry(
        "Library LUT", _make_lut3d(gain=1.2, size=3, name="LibraryLUT")))

    embedded_stage = st.make_lut_stage(_make_lut3d(gain=2.5, size=3, name="StageLUT"))
    embedded_stage.params["source_kind"] = "embedded"
    stack = st.Stack(stages=[embedded_stage])

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test_session.ltr")
        session_io.save_session(path, stack, None, lut_library=lut_library)

        (loaded_stack, _bake_settings, sample_image_state, memory_library, gallery_still_library,
         cdl_library, loaded_lut_library, loaded_media_library, undo_history_state,
         canvas, timeline, warnings) = session_io.load_session(path)

        assert warnings == []
        assert len(loaded_lut_library.entries) == 1
        assert loaded_lut_library.entries[0].name == "Library LUT"
        assert len(loaded_stack.stages) == 1
        assert loaded_stack.stages[0].params["source_kind"] == "embedded"
        assert np.allclose(
            loaded_stack.stages[0].params["lut"].table,
            embedded_stage.params["lut"].table)
        assert loaded_media_library.is_empty()
    print("save_session/load_session: a LUT Library entry and an "
          "'embedded' Stack stage both survive a real file round trip -- "
          "OK")


# ---- workspace is NOT part of a session (VERSION 7, per Tom) --------------
# VERSION 6 briefly embedded workspace layout (window size/panel visibility/
# splitter sizes) in .ltr files; VERSION 7 reverses that -- workspace now
# lives independently in workspace_io.py (an auto-remembered file plus
# named presets), never written into a session at all. save_session no
# longer even accepts a workspace argument -- these tests cover the two
# things that could otherwise regress silently: a fresh save never writes
# the key, and a VERSION-6 file that still has a leftover "workspace" key
# loads fine without session_io trying to do anything with it.

def test_save_session_never_writes_a_workspace_key():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test_session.ltr")
        session_io.save_session(path, st.Stack(), None)
        with open(path) as f:
            data = json.load(f)
        assert "workspace" not in data
        assert data["version"] == session_io.SESSION_VERSION
    print("save_session: never writes a 'workspace' key (VERSION 7, "
          "workspace layout lives in workspace_io.py now) -- OK")


def test_load_session_ignores_a_leftover_workspace_key_from_version_6():
    """A VERSION 6 file (the one version that ever wrote "workspace")
    still loads fine -- session_io simply never reads that key back out
    anymore, same as any other key this version's format doesn't define."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "old_session.ltr")
        session_io.save_session(path, st.Stack(), None)
        with open(path) as f:
            data = json.load(f)
        data["version"] = 6
        data["workspace"] = {"window_size": [1400, 900]}
        with open(path, "w") as f:
            json.dump(data, f)

        result = session_io.load_session(path)
        assert len(result) == 12  # no workspace element in the returned tuple
        warnings = result[-1]
        assert warnings == []
    print("load_session: a VERSION 6 file's leftover 'workspace' key is "
          "silently ignored, loads fine with no workspace in the returned "
          "tuple -- OK")


# ---- media_library_to_session_list / media_library_from_session_list -----

def test_session_io_never_imports_a_full_raster_decoder():
    """The actual point of the Media Panel rewrite (per Tom: "columned
    database referring to files on disk, no longer embed data in
    memory"): session_io.py must not even HAVE a way to do a full pixel
    decode of a Media Library entry any more -- confirms the old
    load_image_as_array import (used by the pre-VERSION-12 eager-decode-
    on-load path) is really gone, not just unused."""
    assert not hasattr(session_io, "load_image_as_array")
    assert hasattr(session_io, "get_image_native_size")
    print("session_io: no full-raster-decode import remains -- only the "
          "header-only get_image_native_size probe -- OK")


def test_media_library_session_round_trip_via_real_file():
    """Unlike the LUT Library, Media Library entries are path-referenced
    (see session_io.media_library_to_session_list's docstring) -- so a
    round trip only works if the source file is still there to header-
    probe, which this test proves by writing a real PNG to disk first.
    VERSION 12: width/height round-trip too, and no pixel array exists
    on either the original or rebuilt entry."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "swatch.png")
        Image.fromarray(
            (np.full((4, 6, 3), 128, dtype=np.uint8))
        ).save(path)

        library = MediaLibrary()
        library.add_entry(MediaEntry(
            "Swatch", path, source_colourspace="sRGB",
            source_filename="swatch.png", width=6, height=4))

        serialized = json.loads(json.dumps(session_io.media_library_to_session_list(library)))
        rebuilt_library, warnings = session_io.media_library_from_session_list(serialized)

        assert warnings == []
        assert len(rebuilt_library.entries) == 1
        rebuilt_entry = rebuilt_library.entries[0]
        assert rebuilt_entry.name == "Swatch"
        assert rebuilt_entry.source_colourspace == "sRGB"
        assert not hasattr(rebuilt_entry, "array")
        assert (rebuilt_entry.width, rebuilt_entry.height) == (6, 4)
    print("media_library_to_session_list/media_library_from_session_list: "
          "an entry backed by a real file on disk round-trips its path and "
          "width/height, no pixel data anywhere -- OK")


def test_media_library_from_session_list_reprobes_missing_width_height():
    """A pre-VERSION-12 file has no width/height keys at all -- must fall
    back to a header-only app_logic.get_image_native_size probe (never a
    full decode, see test_session_io_never_imports_a_full_raster_decoder
    above) rather than leaving them None forever."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "old_swatch.png")
        Image.fromarray(np.full((4, 6, 3), 128, dtype=np.uint8)).save(path)
        old_data = [{
            "name": "Old Swatch", "source_path": path,
            "source_filename": "old_swatch.png", "source_colourspace": None,
        }]
        library, warnings = session_io.media_library_from_session_list(old_data)
        assert warnings == []
        assert len(library.entries) == 1
        assert (library.entries[0].width, library.entries[0].height) == (6, 4)
    print("media_library_from_session_list: a pre-VERSION-12 file with no "
          "width/height keys gets them re-probed from the file's own "
          "header -- OK")


def test_media_library_metadata_round_trips_and_is_reprobed_when_missing():
    """MediaEntry.metadata (media_probe's header fields behind the Media
    Libraries columns) is saved as-is; an entry saved before it existed
    gets it from a header probe on load instead of blank columns."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "swatch.png")
        Image.fromarray(np.full((4, 6, 3), 128, dtype=np.uint8)).save(path)
        library = MediaLibrary()
        library.add_entry(MediaEntry("Swatch", path, width=6, height=4,
                                     metadata={"codec": "Saved Value", "bit_depth": 8}))
        data = session_io.media_library_to_session_list(library)
        reloaded, warnings = session_io.media_library_from_session_list(data)
        assert warnings == [] and reloaded.entries[0].metadata == {"codec": "Saved Value", "bit_depth": 8}

        del data[0]["metadata"]
        reprobed, warnings = session_io.media_library_from_session_list(data)
        assert warnings == []
        assert reprobed.entries[0].metadata["format"] == "PNG"
        assert reprobed.entries[0].metadata["bit_depth"] == 8
    print("media_library session: metadata round-trips, and a pre-metadata "
          "entry is re-probed on load -- OK")


def test_media_library_video_entry_session_round_trip():
    """Media Library Phase 2/VERSION 12: a video entry (media_kind=
    "video") persists its width/height/frame_count/frame_rate/duration/
    thumbnail, and a pre-VERSION-12 file with no saved width/height gets
    them re-probed from the real clip's own stream metadata via
    video_source.VideoSource -- with no frame decoded either way."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "clip.mp4")
        _make_test_clip(path, width=16, height=12)

        media_cache.set_cache_dir(None)  # embed-in-session mode for this test
        thumb = media_cache.store_thumbnail(
            "test-clip", thumbnail.make_thumbnail_png_bytes(
                np.zeros((12, 16, 3), dtype=np.float32)))

        library = MediaLibrary()
        library.add_entry(MediaEntry(
            "Clip", path, source_filename="clip.mp4",
            media_kind="video", frame_count=6, frame_rate=24.0,
            duration=0.25, thumbnail=thumb))  # no width/height saved -- must be re-probed

        serialized = json.loads(json.dumps(session_io.media_library_to_session_list(library)))
        rebuilt_library, warnings = session_io.media_library_from_session_list(serialized)

        assert warnings == []
        assert len(rebuilt_library.entries) == 1
        rebuilt_entry = rebuilt_library.entries[0]
        assert rebuilt_entry.is_video is True
        assert rebuilt_entry.frame_count == 6
        assert rebuilt_entry.frame_rate == 24.0
        assert rebuilt_entry.duration == 0.25
        assert rebuilt_entry.thumbnail == thumb
        assert (rebuilt_entry.width, rebuilt_entry.height) == (16, 12)
        assert not hasattr(rebuilt_entry, "array")
    print("media_library_to_session_list/media_library_from_session_list: "
          "a video entry round-trips its media_kind/metadata/thumbnail, "
          "re-probing width/height from stream metadata with no frame "
          "decode -- OK")


def test_media_library_from_session_list_defaults_media_kind_for_old_files():
    """A pre-VERSION-10 file has no media_kind key at all -- must default
    to 'still' (see session_io.py's own VERSION 10 docstring), not
    crash or misclassify as video."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "old_swatch.png")
        Image.fromarray(np.full((4, 6, 3), 128, dtype=np.uint8)).save(path)
        old_data = [{
            "name": "Old Swatch", "source_path": path,
            "source_filename": "old_swatch.png", "source_colourspace": None,
        }]
        library, warnings = session_io.media_library_from_session_list(old_data)
        assert warnings == []
        assert len(library.entries) == 1
        assert library.entries[0].media_kind == "still"
        assert library.entries[0].is_video is False
    print("media_library_from_session_list: a pre-VERSION-10 file with no "
          "media_kind key defaults to 'still' -- OK")


def test_media_library_from_session_list_skips_missing_file_with_warning():
    """An entry whose source_path no longer exists can't be reconstructed
    (unlike a LUT Library entry, which is embedded by value) -- must be
    skipped with a warning, not raise."""
    bad_data = [{
        "name": "Gone", "source_path": "/nonexistent/path/gone.png",
        "source_filename": "gone.png", "source_colourspace": None,
    }]
    library, warnings = session_io.media_library_from_session_list(bad_data)
    assert len(library.entries) == 0
    assert len(warnings) == 1
    assert "Gone" in warnings[0]
    print("media_library_from_session_list: an entry whose source file "
          "has since moved/been deleted is skipped with a warning, "
          "doesn't raise -- OK")


def test_load_session_reads_the_old_image_library_key_as_a_fallback():
    """VERSION 12 renamed the session key "image_library" -> "media_library"
    -- a session file saved by an OLDER version of this app (which only
    ever wrote "image_library") must still load its Media Library
    entries, via load_session's data.get("media_library",
    data.get("image_library", [])) fallback (see session_io.py's own
    VERSION 12 note)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        img_path = os.path.join(tmp_dir, "swatch.png")
        Image.fromarray(np.full((4, 6, 3), 128, dtype=np.uint8)).save(img_path)
        session_path = os.path.join(tmp_dir, "old_version.ltr")
        data = {
            "format": "lutier-session", "version": 11,
            "stack": {"stages": []}, "sample_image": None,
            "image_library": [{
                "name": "Old-Key Swatch", "source_path": img_path,
                "source_filename": "swatch.png", "source_colourspace": None,
                "media_kind": "still",
            }],
        }
        with open(session_path, "w") as f:
            json.dump(data, f)

        (_stack, _bake_settings, _sample_image_state, _memory_library, _gallery_still_library,
         _cdl_library, _lut_library, media_library, _undo_history_state,
         _canvas, _timeline, warnings) = session_io.load_session(session_path)

        assert warnings == []
        entries = media_library.libraries[0].entries
        assert len(entries) == 1
        assert entries[0].name == "Old-Key Swatch"
    print("load_session: a pre-VERSION-12 file's old 'image_library' key "
          "still loads via the new key's fallback -- OK")


# ---- memory_library_to_session_list / from_session_list (thumbnail) ------
# and gallery_stills_to_session_list / from_session_list (Phase 3) --------

def test_memory_library_session_round_trip_with_thumbnail():
    thumb = {"mode": "embedded", "data_b64": "Zm9v"}
    full_still = {"mode": "embedded", "data_b64": "ZnVsbA=="}
    stage = st.make_cdl_stage(slope=(1.1, 1.0, 0.9))
    stack = st.Stack(stages=[stage])
    library = st_mem.StackMemoryLibrary()
    library.save("My Grade", stack, thumbnail=thumb, full_still=full_still)

    entries, save_warnings = session_io.memory_library_to_session_list(library)
    assert save_warnings == []
    serialized = json.loads(json.dumps(entries))
    rebuilt_library, warnings = session_io.memory_library_from_session_list(serialized)

    assert warnings == []
    assert len(rebuilt_library.memories) == 1
    assert rebuilt_library.memories[0].name == "My Grade"
    assert rebuilt_library.memories[0].thumbnail == thumb
    assert rebuilt_library.memories[0].full_still == full_still
    assert len(rebuilt_library.memories[0].stack.stages) == 1
    print("memory_library_to_session_list/memory_library_from_session_list: "
          "a memory's thumbnail AND full_still both round-trip through "
          "plain JSON -- OK")


def test_memory_library_to_session_list_drops_a_broken_memory_with_a_warning():
    """Regression guard for the fix: before this, ANY one memory whose
    own stack_to_session_dict raised (see test_stack_to_session_dict_
    raises_loudly_for_unreconstructable_lut_stage above) blocked saving
    the WHOLE library -- including every other, perfectly valid memory,
    and by extension the whole session save (see save_session's own
    docstring). Now that one memory is dropped, with a warning naming
    it, and the rest save normally."""
    broken_stage = st.make_lut_stage(_make_lut3d(size=3), label="Mystery")
    broken_stage.params["source_kind"] = "not_a_real_source_kind"
    library = st_mem.StackMemoryLibrary()
    library.save("Good Grade", st.Stack(stages=[st.make_cdl_stage(slope=(1.1, 1.0, 0.9))]))
    library.save("Broken Grade", st.Stack(stages=[broken_stage]))
    library.save("Also Good", st.Stack())

    entries, warnings = session_io.memory_library_to_session_list(library)

    assert len(entries) == 2
    assert [e["name"] for e in entries] == ["Good Grade", "Also Good"]
    assert len(warnings) == 1
    assert "Broken Grade" in warnings[0]
    print("memory_library_to_session_list: a broken memory is dropped "
          "with a warning naming it, the rest save normally -- OK")


def test_memory_library_from_session_list_defaults_thumbnail_for_old_files():
    """A pre-VERSION-10 file has no thumbnail/full_still keys at all."""
    old_data = [{"name": "Old Memory", "stack": session_io.stack_to_session_dict(st.Stack())}]
    library, warnings = session_io.memory_library_from_session_list(old_data)
    assert warnings == []
    assert library.memories[0].thumbnail is None
    assert library.memories[0].full_still is None
    print("memory_library_from_session_list: a pre-VERSION-10 file with no "
          "thumbnail/full_still keys defaults both to None -- OK")


# ---- timeline_to_session_dict / timeline_from_session_dict (VERSION 11) --

def test_timeline_to_session_dict_round_trips_via_json():
    timeline = tl.Timeline(name="Reel 1", frame_rate=25.0)
    clip = tl.TimelineClip(
        "Shot 01", "/footage/shot01.mov", "video", 10, 109,
        stack=st.Stack(stages=[st.make_cdl_stage(slope=(1.1, 1.0, 0.9))]),
        thumbnail={"mode": "embedded", "data_b64": "Zm9v"})
    timeline.add_clip(clip)

    data, warnings = session_io.timeline_to_session_dict(timeline)
    assert warnings == []
    serialized = json.loads(json.dumps(data))
    rebuilt, warnings = session_io.timeline_from_session_dict(serialized)

    assert warnings == []
    assert rebuilt.name == "Reel 1"
    assert rebuilt.frame_rate == 25.0
    assert len(rebuilt.clips) == 1
    rebuilt_clip = rebuilt.clips[0]
    assert rebuilt_clip.name == "Shot 01"
    assert rebuilt_clip.source_path == "/footage/shot01.mov"
    assert rebuilt_clip.media_kind == "video"
    assert rebuilt_clip.in_frame == 10
    assert rebuilt_clip.out_frame == 109
    assert rebuilt_clip.thumbnail == {"mode": "embedded", "data_b64": "Zm9v"}
    assert rebuilt_clip.id != clip.id, "a reconstructed clip gets a fresh id, never the saved one"
    assert len(rebuilt_clip.stack.stages) == 1
    assert rebuilt_clip.stack.stages[0].kind == "cdl"
    print("timeline_to_session_dict/timeline_from_session_dict: name, "
          "frame_rate, and every clip field (including its own Stack) "
          "round-trip through plain JSON -- OK")


def test_timeline_from_session_dict_defaults_to_an_empty_timeline_when_absent():
    """None/{} (no 'timeline' key at all -- a pre-VERSION-11 file) must
    load fine as a brand-new, empty Timeline, same 'old file loads fine,
    just without the newer feature' convention every prior version bump
    in this module already follows."""
    for absent in (None, {}):
        timeline, warnings = session_io.timeline_from_session_dict(absent)
        assert warnings == []
        assert timeline.clips == []
        assert timeline.name == "Timeline"
        assert timeline.frame_rate == tl.DEFAULT_FRAME_RATE
    print("timeline_from_session_dict: None/{} loads as a brand-new, "
          "empty Timeline -- OK")


def test_timeline_to_session_dict_drops_a_broken_clip_with_a_warning():
    """Same resilience memory_library_to_session_list already has (this
    session's own earlier fix): one clip whose own Stack can't be
    serialized must be dropped, with a warning naming it, rather than
    sinking the whole save."""
    broken_stage = st.make_lut_stage(_make_lut3d(gain=1.0, size=3), label="Mystery")
    broken_stage.params["source_kind"] = "not_a_real_source_kind"
    timeline = tl.Timeline()
    timeline.add_clip(tl.TimelineClip(
        "Good Shot", "/a.mov", "video", 0, 99,
        stack=st.Stack(stages=[st.make_cdl_stage(slope=(1.1, 1.0, 0.9))])))
    timeline.add_clip(tl.TimelineClip(
        "Broken Shot", "/b.mov", "video", 0, 99,
        stack=st.Stack(stages=[broken_stage])))
    timeline.add_clip(tl.TimelineClip("Also Good", "/c.mov", "video", 0, 99))

    data, warnings = session_io.timeline_to_session_dict(timeline)

    assert [c["name"] for c in data["clips"]] == ["Good Shot", "Also Good"]
    assert len(warnings) == 1
    assert "Broken Shot" in warnings[0]
    print("timeline_to_session_dict: a broken clip is dropped with a "
          "warning naming it, the rest save normally -- OK")


def test_save_and_load_session_round_trips_a_timeline():
    """Timelines live in Media Libraries (VERSION 14); the loaded one is
    remembered by position and comes back as load_session's
    loaded_timeline, the same object that's in the pool."""
    timeline = tl.Timeline(name="Dailies", frame_rate=23.976)
    timeline.add_clip(tl.TimelineClip(
        "Shot 01", "/a.mov", "video", 0, 47,
        stack=st.Stack(stages=[st.make_cdl_stage(sat=0.8)])))
    timeline.add_clip(tl.TimelineClip("Shot 02", "/b.mov", "video", 100, 199))
    pool = MediaPool()
    pool.libraries[0].add_timeline(tl.Timeline(name="Other"))
    second = pool.add_library("Selects")
    second.add_timeline(timeline)

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test_session.ltr")
        warnings = session_io.save_session(
            path, st.Stack(), None, media_pool=pool, loaded_timeline=timeline)
        assert warnings == []

        result = session_io.load_session(path)
        loaded_pool, loaded_timeline = result[7], result[-2]
        assert [lib.name for lib in loaded_pool.libraries] == ["Media", "Selects"]
        assert loaded_timeline is loaded_pool.libraries[1].timelines[0]
        assert loaded_timeline.name == "Dailies"
        assert loaded_timeline.frame_rate == 23.976
        assert [c.name for c in loaded_timeline.clips] == ["Shot 01", "Shot 02"]
        assert len(loaded_timeline.clips[0].stack.stages) == 1
    print("save_session/load_session: libraries with their timelines, and "
          "which one was loaded, survive a real file round trip -- OK")


def test_save_session_with_nothing_loaded_loads_nothing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test_session.ltr")
        session_io.save_session(path, st.Stack(), None)
        with open(path) as f:
            data = json.load(f)
        assert "timeline" not in data and "media_library" not in data
        assert data["media_pool"]["loaded_timeline"] is None

        result = session_io.load_session(path)
        assert result[-2] is None
        assert len(result[7].libraries) == 1 and result[7].is_empty()
    print("save_session: no loaded timeline round-trips as none loaded, "
          "one empty library -- OK")


def test_load_session_maps_an_old_single_timeline_into_a_library():
    """Pre-VERSION-14 files have one "media_library" list and one
    "timeline": they become one "Media" library holding that timeline,
    loaded -- or nothing loaded when the old timeline had no clips."""
    old_timeline = tl.Timeline(name="Old Cut")
    old_timeline.add_clip(tl.TimelineClip("Shot", "/a.mov", "video", 0, 23))
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "old_session.ltr")
        session_io.save_session(path, st.Stack(), None)
        with open(path) as f:
            data = json.load(f)
        del data["media_pool"]
        data["version"] = 13
        data["media_library"] = []
        data["timeline"], _ = session_io.timeline_to_session_dict(old_timeline)
        with open(path, "w") as f:
            json.dump(data, f)
        result = session_io.load_session(path)
        pool, loaded, warnings = result[7], result[-2], result[-1]
        assert warnings == []
        assert pool.libraries[0].name == "Media"
        assert loaded is pool.libraries[0].timelines[0] and loaded.name == "Old Cut"

        data["timeline"] = {"name": "Timeline", "frame_rate": 24.0, "clips": []}
        with open(path, "w") as f:
            json.dump(data, f)
        result = session_io.load_session(path)
        assert result[-2] is None and result[7].libraries[0].timelines == []
    print("load_session: an old single timeline becomes a loaded item in a "
          "'Media' library; an empty one is dropped -- OK")

def test_gallery_stills_session_round_trip():
    thumb = {"mode": "embedded", "data_b64": "Zm9v"}
    library = st_mem.GalleryStillLibrary()
    library.add_still(st_mem.GalleryStill(
        "Ref Shot", thumb, "/tmp/ref.png", source_filename="ref.png"))

    serialized = json.loads(json.dumps(session_io.gallery_stills_to_session_list(library)))
    rebuilt_library, warnings = session_io.gallery_stills_from_session_list(serialized)

    assert warnings == []
    assert len(rebuilt_library.stills) == 1
    rebuilt_still = rebuilt_library.stills[0]
    assert rebuilt_still.name == "Ref Shot"
    assert rebuilt_still.source_path == "/tmp/ref.png"
    assert rebuilt_still.source_filename == "ref.png"
    assert rebuilt_still.thumbnail == thumb
    print("gallery_stills_to_session_list/gallery_stills_from_session_list: "
          "a gallery still round-trips through plain JSON -- OK")


def test_gallery_stills_from_session_list_skips_entry_with_no_source_path():
    bad_data = [{"name": "No Path", "thumbnail": None}]
    library, warnings = session_io.gallery_stills_from_session_list(bad_data)
    assert len(library.stills) == 0
    assert len(warnings) == 1
    assert "No Path" in warnings[0]
    print("gallery_stills_from_session_list: an entry with no source_path "
          "is skipped with a warning -- OK")


def test_save_and_load_session_round_trips_memories_and_gallery_stills():
    thumb_a = {"mode": "embedded", "data_b64": "bWVtb3J5"}
    thumb_b = {"mode": "embedded", "data_b64": "c3RpbGw="}
    memory_library = st_mem.StackMemoryLibrary()
    memory_library.save("Grade A", st.Stack(), thumbnail=thumb_a)
    gallery_library = st_mem.GalleryStillLibrary()
    gallery_library.add_still(st_mem.GalleryStill(
        "Ref A", thumb_b, "/tmp/ref_a.png", source_filename="ref_a.png"))

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test_session.ltr")
        session_io.save_session(
            path, st.Stack(), None, stack_memory_library=memory_library,
            gallery_still_library=gallery_library)

        (_stack, _bake_settings, _sample_image_state, loaded_memory_library, loaded_gallery_library,
         _cdl_library, _lut_library, _media_library, _undo_history_state, _canvas, _timeline,
         warnings) = session_io.load_session(path)

        assert warnings == []
        assert loaded_memory_library.memories[0].thumbnail == thumb_a
        assert loaded_gallery_library.stills[0].thumbnail == thumb_b
        assert loaded_gallery_library.stills[0].source_path == "/tmp/ref_a.png"
    print("save_session/load_session: memory thumbnails and gallery "
          "stills both survive a real file round trip, as SEPARATE "
          "libraries -- OK")


def test_load_session_without_gallery_stills_key_defaults_to_empty():
    """Pre-VERSION-10 files have no 'gallery_stills' key at all."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "old_session.ltr")
        session_io.save_session(path, st.Stack(), None)
        with open(path) as f:
            data = json.load(f)
        del data["gallery_stills"]
        with open(path, "w") as f:
            json.dump(data, f)

        result = session_io.load_session(path)
        gallery_still_library = result[4]
        warnings = result[-1]
        assert warnings == []
        assert gallery_still_library.stills == []
    print("load_session: a session file with no 'gallery_stills' key at "
          "all (pre-version-10) loads fine with an empty gallery -- OK")


def test_load_session_without_lut_library_key_defaults_to_empty():
    """Version 1-3 files (saved before the LUT Library existed) have no
    "lut_library" key at all -- must still load fine, per this module's
    docstring on VERSION 4."""
    stack = st.Stack()
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "old_session.ltr")
        session_io.save_session(path, stack, None)
        with open(path) as f:
            data = json.load(f)
        del data["lut_library"]
        with open(path, "w") as f:
            json.dump(data, f)

        (_stack, _bake_settings, _sample_image_state, _memory_library, _gallery_still_library,
         _cdl_library, lut_library, media_library, _undo_history_state, _canvas, _timeline,
         warnings) = session_io.load_session(path)
        assert warnings == []
        assert lut_library.entries == []
        assert media_library.is_empty()
    print("load_session: a session file with no 'lut_library' key at "
          "all (pre-version-4) loads fine with an empty LUT Library -- OK")


# ---- undo_history_to_session_list / from_session_list, and its wiring ----
# through save_session/load_session (VERSION 8, per Tom: "reset on new/
# open session but keep undo history inside of project data if possible")

def test_save_and_load_session_round_trips_undo_history():
    stack_a = st.Stack()
    lut_stage = st.make_lut_stage(_make_lut3d(size=3))
    lut_stage.params["source_kind"] = "embedded"
    stack_b = st.Stack(stages=[lut_stage])
    history = UndoHistory(stack_a.copy())
    history.push(stack_b.copy())

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test_session.ltr")
        session_io.save_session(path, stack_b, None, undo_history=history)

        (_stack, _bake_settings, _sample_image_state, _memory_library, _gallery_still_library,
         _cdl_library, _lut_library, _media_library, undo_history_state, _canvas, _timeline,
         warnings) = session_io.load_session(path)

        assert warnings == []
        assert undo_history_state is not None
        states, pointer = undo_history_state
        assert pointer == 1
        rebuilt = UndoHistory.from_states(states, pointer)
        assert len(rebuilt.states[0].stages) == 0
        assert len(rebuilt.states[1].stages) == 1
        assert rebuilt.can_undo
        assert not rebuilt.can_redo
    print("save_session/load_session: undo history (every state + the "
          "current pointer) survives a real file round trip -- OK")


def test_save_session_with_no_undo_history_omits_it_and_load_returns_none():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test_session.ltr")
        session_io.save_session(path, st.Stack(), None)  # undo_history=None (default)
        with open(path) as f:
            data = json.load(f)
        assert data["undo_history"] is None

        result = session_io.load_session(path)
        undo_history_state = result[-4]
        assert undo_history_state is None
    print("save_session: undo_history=None writes a null 'undo_history', "
          "load_session reports None (fresh history) rather than an empty "
          "one -- OK")


def test_load_session_without_undo_history_key_defaults_to_none():
    """A version < 8 file (or any file missing the key entirely) has no
    undo_history at all -- must still load fine, per this module's VERSION
    8 note, exactly like every other backward-compatible key before it."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "old_session.ltr")
        session_io.save_session(path, st.Stack(), None)
        with open(path) as f:
            data = json.load(f)
        del data["undo_history"]
        data["version"] = 7
        with open(path, "w") as f:
            json.dump(data, f)

        result = session_io.load_session(path)
        undo_history_state = result[-4]
        warnings = result[-1]
        assert undo_history_state is None
        assert warnings == []
    print("load_session: a pre-version-8 file with no 'undo_history' key "
          "loads fine, reports None (fresh history) -- OK")


def test_canvas_round_trips_through_save_and_load():
    canvas = cv.CanvasSettings(width=3840, height=2160, bit_depth=16,
                                pixel_aspect_ratio=2.0, frame_rate=23.976)
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test_session.ltr")
        session_io.save_session(path, st.Stack(), None, canvas=canvas)
        result = session_io.load_session(path)
        loaded_canvas = result[-3]
        assert loaded_canvas == canvas
    print("save_session/load_session: canvas round-trips exactly through a real file -- OK")


def test_canvas_defaults_when_absent_or_none():
    """A pre-version-9 file (no 'canvas' key at all), and a version 9+
    file explicitly saved with canvas=None, must both load as
    canvas.DEFAULT_CANVAS -- same 'old file loads fine, just without the
    newer feature' convention every prior version bump in this module
    follows (see VERSION 9 note)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "no_canvas_key.ltr")
        session_io.save_session(path, st.Stack(), None)  # canvas=None (default)
        with open(path) as f:
            data = json.load(f)
        assert data["canvas"] is None
        result = session_io.load_session(path)
        assert result[-3] == cv.DEFAULT_CANVAS

        old_path = os.path.join(tmp_dir, "old_session.ltr")
        with open(old_path, "w") as f:
            del data["canvas"]
            data["version"] = 8
            json.dump(data, f)
        old_result = session_io.load_session(old_path)
        assert old_result[-3] == cv.DEFAULT_CANVAS
    print("load_session: canvas=None and a missing 'canvas' key (pre-version-9 file) "
          "both load as DEFAULT_CANVAS -- OK")


def test_undo_history_to_session_list_caps_at_max_persisted_states():
    states = [st.Stack() for _ in range(session_io.MAX_PERSISTED_UNDO_STATES + 10)]
    history = UndoHistory(states[0])
    for state in states[1:]:
        history.push(state)
    assert history.pointer == len(states) - 1

    data, warnings = session_io.undo_history_to_session_list(history)
    assert warnings == []
    assert len(data["states"]) == session_io.MAX_PERSISTED_UNDO_STATES
    # Dropped from the oldest end -- current position (the redo-tail-free
    # end) is preserved exactly, just shifted to match the shorter list.
    assert data["pointer"] == session_io.MAX_PERSISTED_UNDO_STATES - 1
    print("undo_history_to_session_list: caps at MAX_PERSISTED_UNDO_STATES, "
          "dropping from the oldest end and shifting the pointer to match "
          "-- OK")


def _broken_stack():
    """A Stack containing exactly one stage that raises when
    stack_to_session_dict tries to serialize it -- same construction
    test_stack_to_session_dict_raises_loudly_for_unreconstructable_lut_
    stage uses above."""
    stage = st.make_lut_stage(_make_lut3d(size=3), label="Mystery")
    stage.params["source_kind"] = "not_a_real_source_kind"
    return st.Stack(stages=[stage])


def test_undo_history_to_session_list_drops_a_broken_state_and_shifts_pointer():
    """Regression guard for the fix: before this, ANY one undo state
    whose own stack_to_session_dict raised blocked saving the whole
    session, including the current, perfectly valid working stack (see
    save_session's own docstring). Now that one state is dropped, with a
    warning, and `pointer` shifts to still land on the same logical
    position."""
    good_a = st.Stack()
    good_b = st.Stack(stages=[st.make_cdl_stage(slope=(1.1, 1.0, 0.9))])
    good_c = st.Stack(stages=[st.make_cdl_stage(sat=1.2)])
    history = UndoHistory(good_a)
    history.push(_broken_stack())  # index 1 -- will be dropped
    history.push(good_b)
    history.push(good_c)  # pointer == 3 (current position)

    data, warnings = session_io.undo_history_to_session_list(history)

    assert len(data["states"]) == 3  # good_a, good_b, good_c -- the broken one dropped
    assert len(warnings) == 1
    assert "#1" in warnings[0]
    # One state dropped BEFORE the original pointer (3) -- shifts down by one.
    assert data["pointer"] == 2
    print("undo_history_to_session_list: a broken state is dropped with "
          "a warning, pointer shifts to still land on the same logical "
          "position -- OK")


def test_undo_history_to_session_list_drops_the_state_at_the_pointer_itself():
    good_a = st.Stack()
    history = UndoHistory(good_a)
    history.push(_broken_stack())  # index 1 -- the CURRENT position, and it's broken

    data, warnings = session_io.undo_history_to_session_list(history)

    assert len(data["states"]) == 1  # only good_a survives
    assert len(warnings) == 1
    assert "#1" in warnings[0]
    # Nothing left after the dropped state -- clamps onto the last survivor.
    assert data["pointer"] == 0
    print("undo_history_to_session_list: dropping the state AT the "
          "pointer clamps onto the nearest surviving state rather than "
          "an out-of-range index -- OK")


def test_save_session_succeeds_despite_a_broken_stack_memory():
    """Integration-level regression guard, end to end through the real
    file-writing path: a broken Stack Memory must not block saving the
    CURRENT, valid working stack -- exactly the failure Tom's review
    flagged."""
    library = st_mem.StackMemoryLibrary()
    library.save("Broken", _broken_stack())
    good_stack = st.Stack(stages=[st.make_cdl_stage(slope=(1.2, 0.9, 1.0))])

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test_session.ltr")
        warnings = session_io.save_session(path, good_stack, None, stack_memory_library=library)
        assert len(warnings) == 1
        assert "Broken" in warnings[0]

        (loaded_stack, *_rest) = session_io.load_session(path)
        assert len(loaded_stack.stages) == 1
        assert loaded_stack.stages[0].kind == "cdl"
    print("save_session: a broken Stack Memory doesn't block saving the "
          "current, valid working stack -- returns a warning instead of "
          "raising -- OK")


def test_save_session_still_raises_if_the_current_stack_itself_is_broken():
    """The deliberate asymmetry (see save_session's own docstring): only
    OLD data (stack_memories/undo_history) gets the resilient "drop and
    warn" treatment. If the CURRENT working stack itself can't be
    serialized, save_session must still raise immediately -- silently
    dropping part of the user's actual active work would be far worse
    than a hard, loud failure."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test_session.ltr")
        try:
            session_io.save_session(path, _broken_stack(), None)
            assert False, "must raise for a broken CURRENT stack, not silently drop it"
        except ValueError:
            pass
    print("save_session: still raises immediately if the CURRENT working "
          "stack itself is unreconstructable -- OK")


def test_bake_settings_round_trip_and_migrate_from_an_old_stack_dict():
    """LUT grid size/domain are a session-level "lut_bake" section now
    (stack.BakeSettings); an older file stored them on the stack, and
    those values must carry over so it exports exactly as before."""
    custom = st.BakeSettings(size=17, domain_min=(-0.5,) * 3, domain_max=(2.0,) * 3)
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "session.ltr")
        session_io.save_session(path, st.Stack(), None, bake_settings=custom)
        with open(path) as f:
            data = json.load(f)
        assert "size" not in data["stack"] and "domain_min" not in data["stack"]
        assert session_io.load_session(path)[1] == custom

        del data["lut_bake"]
        data["stack"].update({"size": 65, "domain_min": [0, 0, 0], "domain_max": [4, 4, 4]})
        with open(path, "w") as f:
            json.dump(data, f)
        migrated = session_io.load_session(path)[1]
        assert migrated == st.BakeSettings(size=65, domain_max=(4.0, 4.0, 4.0))

        data["stack"].update({"domain_min": [5, 5, 5]})  # invalid: min >= max
        with open(path, "w") as f:
            json.dump(data, f)
        assert session_io.load_session(path)[1] == st.DEFAULT_BAKE_SETTINGS
    print("session bake settings: round-trip, migrate from an old stack's "
          "size/domain, invalid falls back to defaults -- OK")


def test_stack_memories_no_longer_carry_size_or_domain():
    library = st_mem.StackMemoryLibrary()
    library.save("Grade", st.Stack(stages=[st.make_cdl_stage()]))
    entries, _warnings = session_io.memory_library_to_session_list(library)
    text = json.dumps(entries)
    assert '"size"' not in text and "domain_min" not in text, text
    print("Stack Memories: saved without grid size/domain -- OK")


if __name__ == "__main__":
    test_embedded_dict_round_trips_lut3d_table_exactly()
    test_embedded_dict_round_trips_lut1d_table_exactly()
    test_embedded_dict_distinguishes_1d_from_3d_by_table_ndim()
    test_embedded_lut_stage_round_trips_without_any_file()
    test_fork_split_and_merge_round_trip_with_branch_tagged_stages()
    test_grading_primary_stage_round_trips()
    test_exposure_contrast_stage_round_trips()
    test_custom_drt_stage_round_trips()
    test_grading_tone_stage_round_trips()
    test_grading_rgb_curve_stage_round_trips()
    test_color_warper_stage_round_trips()
    test_color_warper_stage_default_points_round_trip()
    test_keyer_stage_round_trips()
    test_matte_combine_stage_round_trips_via_position()
    test_matte_shape_stage_round_trips()
    test_matte_source_id_round_trips_via_position_not_raw_id()
    test_matte_source_index_survives_an_earlier_skipped_stage()
    test_matte_source_index_pointing_at_a_skipped_stage_falls_back_to_none()
    test_matte_source_index_out_of_range_falls_back_to_none()
    test_branch_tagged_stage_skipped_individually_when_it_cant_reconstruct()
    test_stack_to_session_dict_raises_loudly_for_unreconstructable_lut_stage()
    test_load_session_with_unknown_future_stage_kind_still_loads_others()
    test_lut_library_session_round_trip()
    test_lut_library_from_session_list_skips_bad_entry_with_warning()
    test_save_session_never_writes_a_workspace_key()
    test_load_session_ignores_a_leftover_workspace_key_from_version_6()
    test_session_io_never_imports_a_full_raster_decoder()
    test_media_library_session_round_trip_via_real_file()
    test_media_library_from_session_list_reprobes_missing_width_height()
    test_media_library_metadata_round_trips_and_is_reprobed_when_missing()
    test_media_library_video_entry_session_round_trip()
    test_media_library_from_session_list_defaults_media_kind_for_old_files()
    test_media_library_from_session_list_skips_missing_file_with_warning()
    test_load_session_reads_the_old_image_library_key_as_a_fallback()
    test_memory_library_session_round_trip_with_thumbnail()
    test_memory_library_to_session_list_drops_a_broken_memory_with_a_warning()
    test_memory_library_from_session_list_defaults_thumbnail_for_old_files()
    test_timeline_to_session_dict_round_trips_via_json()
    test_timeline_from_session_dict_defaults_to_an_empty_timeline_when_absent()
    test_timeline_to_session_dict_drops_a_broken_clip_with_a_warning()
    test_save_and_load_session_round_trips_a_timeline()
    test_save_session_with_nothing_loaded_loads_nothing()
    test_load_session_maps_an_old_single_timeline_into_a_library()
    test_gallery_stills_session_round_trip()
    test_gallery_stills_from_session_list_skips_entry_with_no_source_path()
    test_save_and_load_session_round_trips_memories_and_gallery_stills()
    test_load_session_without_gallery_stills_key_defaults_to_empty()
    test_save_and_load_session_round_trips_lut_library_and_embedded_stage()
    test_load_session_without_lut_library_key_defaults_to_empty()
    test_save_and_load_session_round_trips_undo_history()
    test_save_session_with_no_undo_history_omits_it_and_load_returns_none()
    test_load_session_without_undo_history_key_defaults_to_none()
    test_canvas_round_trips_through_save_and_load()
    test_canvas_defaults_when_absent_or_none()
    test_undo_history_to_session_list_caps_at_max_persisted_states()
    test_undo_history_to_session_list_drops_a_broken_state_and_shifts_pointer()
    test_undo_history_to_session_list_drops_the_state_at_the_pointer_itself()
    test_save_session_succeeds_despite_a_broken_stack_memory()
    test_save_session_still_raises_if_the_current_stack_itself_is_broken()
    test_bake_settings_round_trip_and_migrate_from_an_old_stack_dict()
    test_stack_memories_no_longer_carry_size_or_domain()
    print()
    print("All session_io tests passed.")
