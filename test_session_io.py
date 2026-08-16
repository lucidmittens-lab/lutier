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
import exposure_contrast
import grading_primary
import grading_rgb_curve
import grading_tone
import session_io
import stack as st
import stack_memory as st_mem
import thumbnail
import media_cache
from lut_library import LUTLibraryEntry, LUTLibrary
from image_library import ImageLibraryEntry, ImageLibrary
from undo_history import UndoHistory


def _make_test_clip(path, frame_count=6, frame_rate=24, width=16, height=12):
    """Same tiny-real-encoded-clip fixture convention as
    test_video_source.py's own _make_test_clip -- a genuinely decodable
    file, not a mock, so image_library_from_session_list's video branch
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
    original_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=stack.stages))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=rebuilt_stack.stages))
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

    original_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=stack.stages))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=rebuilt_stack.stages))
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

    original_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=stack.stages))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=rebuilt_stack.stages))
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

    original_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=stack.stages))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=rebuilt_stack.stages))
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

    original_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=stack.stages))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=rebuilt_stack.stages))
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

    original_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=stack.stages))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=rebuilt_stack.stages))
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
    assert r.params["points"] == points

    original_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=stack.stages))
    rebuilt_baked = st.bake_stack_to_lut3d(st.Stack(size=5, stages=rebuilt_stack.stages))
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

        (loaded_stack, _sample_image_state, _memory_library, _gallery_still_library,
         _cdl_library, _lut_library, _image_library, _undo_history_state, _canvas,
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

        (loaded_stack, sample_image_state, memory_library, gallery_still_library,
         cdl_library, loaded_lut_library, loaded_image_library, undo_history_state,
         canvas, warnings) = session_io.load_session(path)

        assert warnings == []
        assert len(loaded_lut_library.entries) == 1
        assert loaded_lut_library.entries[0].name == "Library LUT"
        assert len(loaded_stack.stages) == 1
        assert loaded_stack.stages[0].params["source_kind"] == "embedded"
        assert np.allclose(
            loaded_stack.stages[0].params["lut"].table,
            embedded_stage.params["lut"].table)
        assert loaded_image_library.entries == []
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
        assert len(result) == 10  # no workspace element in the returned tuple
        warnings = result[-1]
        assert warnings == []
    print("load_session: a VERSION 6 file's leftover 'workspace' key is "
          "silently ignored, loads fine with no workspace in the returned "
          "tuple -- OK")


# ---- image_library_to_session_list / image_library_from_session_list -----

def test_image_library_session_round_trip_via_real_file():
    """Unlike the LUT Library, Image Library entries are path-referenced
    (see session_io.image_library_to_session_list's docstring) -- so a
    round trip only works if the source file is still there to re-decode,
    which this test proves by writing a real PNG to disk first."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "swatch.png")
        Image.fromarray(
            (np.full((4, 6, 3), 128, dtype=np.uint8))
        ).save(path)

        library = ImageLibrary()
        library.add_entry(ImageLibraryEntry(
            "Swatch", load_image_array_for_test(path),
            source_colourspace="sRGB", source_filename="swatch.png",
            source_path=path))

        serialized = json.loads(json.dumps(session_io.image_library_to_session_list(library)))
        rebuilt_library, warnings = session_io.image_library_from_session_list(serialized)

        assert warnings == []
        assert len(rebuilt_library.entries) == 1
        rebuilt_entry = rebuilt_library.entries[0]
        assert rebuilt_entry.name == "Swatch"
        assert rebuilt_entry.source_colourspace == "sRGB"
        assert rebuilt_entry.array.shape[:2] == (4, 6)
    print("image_library_to_session_list/image_library_from_session_list: "
          "an entry backed by a real file on disk round-trips (re-decoded "
          "fresh, not embedded) -- OK")


def test_image_library_video_entry_session_round_trip():
    """Media Library Phase 2: a video entry (media_kind="video") persists
    its frame_count/frame_rate/duration/thumbnail and reconstructs its
    first-frame array by reopening the real clip via video_source, not
    load_image_as_array."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "clip.mp4")
        _make_test_clip(path)

        media_cache.set_cache_dir(None)  # embed-in-session mode for this test
        array = np.zeros((12, 16, 3), dtype=np.float32)
        thumb = media_cache.store_thumbnail(
            "test-clip", thumbnail.make_thumbnail_png_bytes(array))

        library = ImageLibrary()
        library.add_entry(ImageLibraryEntry(
            "Clip", array, source_filename="clip.mp4", source_path=path,
            media_kind="video", frame_count=6, frame_rate=24.0,
            duration=0.25, thumbnail=thumb))

        serialized = json.loads(json.dumps(session_io.image_library_to_session_list(library)))
        rebuilt_library, warnings = session_io.image_library_from_session_list(serialized)

        assert warnings == []
        assert len(rebuilt_library.entries) == 1
        rebuilt_entry = rebuilt_library.entries[0]
        assert rebuilt_entry.is_video is True
        assert rebuilt_entry.frame_count == 6
        assert rebuilt_entry.frame_rate == 24.0
        assert rebuilt_entry.duration == 0.25
        assert rebuilt_entry.thumbnail == thumb
        assert rebuilt_entry.array.shape[:2] == (12, 16)
    print("image_library_to_session_list/image_library_from_session_list: "
          "a video entry round-trips its media_kind/metadata/thumbnail, "
          "reconstructing its first frame via video_source -- OK")


def test_image_library_from_session_list_defaults_media_kind_for_old_files():
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
        library, warnings = session_io.image_library_from_session_list(old_data)
        assert warnings == []
        assert len(library.entries) == 1
        assert library.entries[0].media_kind == "still"
        assert library.entries[0].is_video is False
    print("image_library_from_session_list: a pre-VERSION-10 file with no "
          "media_kind key defaults to 'still' -- OK")


def load_image_array_for_test(path):
    """Local stand-in for app_logic.load_image_as_array -- avoids pulling
    in the full app_logic import chain just to build a fixture array for
    this one test; session_io itself still uses the real loader."""
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0


def test_image_library_from_session_list_skips_missing_file_with_warning():
    """An entry whose source_path no longer exists can't be reconstructed
    (unlike a LUT Library entry, which is embedded by value) -- must be
    skipped with a warning, not raise."""
    bad_data = [{
        "name": "Gone", "source_path": "/nonexistent/path/gone.png",
        "source_filename": "gone.png", "source_colourspace": None,
    }]
    library, warnings = session_io.image_library_from_session_list(bad_data)
    assert len(library.entries) == 0
    assert len(warnings) == 1
    assert "Gone" in warnings[0]
    print("image_library_from_session_list: an entry whose source file "
          "has since moved/been deleted is skipped with a warning, "
          "doesn't raise -- OK")


# ---- memory_library_to_session_list / from_session_list (thumbnail) ------
# and gallery_stills_to_session_list / from_session_list (Phase 3) --------

def test_memory_library_session_round_trip_with_thumbnail():
    thumb = {"mode": "embedded", "data_b64": "Zm9v"}
    full_still = {"mode": "embedded", "data_b64": "ZnVsbA=="}
    stage = st.make_cdl_stage(slope=(1.1, 1.0, 0.9))
    stack = st.Stack(stages=[stage])
    library = st_mem.StackMemoryLibrary()
    library.save("My Grade", stack, thumbnail=thumb, full_still=full_still)

    serialized = json.loads(json.dumps(session_io.memory_library_to_session_list(library)))
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


def test_memory_library_from_session_list_defaults_thumbnail_for_old_files():
    """A pre-VERSION-10 file has no thumbnail/full_still keys at all."""
    old_data = [{"name": "Old Memory", "stack": session_io.stack_to_session_dict(st.Stack())}]
    library, warnings = session_io.memory_library_from_session_list(old_data)
    assert warnings == []
    assert library.memories[0].thumbnail is None
    assert library.memories[0].full_still is None
    print("memory_library_from_session_list: a pre-VERSION-10 file with no "
          "thumbnail/full_still keys defaults both to None -- OK")


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

        (_stack, _sample_image_state, loaded_memory_library, loaded_gallery_library,
         _cdl_library, _lut_library, _image_library, _undo_history_state, _canvas,
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
        gallery_still_library = result[3]
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

        (_stack, _sample_image_state, _memory_library, _gallery_still_library,
         _cdl_library, lut_library, image_library, _undo_history_state, _canvas,
         warnings) = session_io.load_session(path)
        assert warnings == []
        assert lut_library.entries == []
        assert image_library.entries == []
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

        (_stack, _sample_image_state, _memory_library, _gallery_still_library,
         _cdl_library, _lut_library, _image_library, undo_history_state, _canvas,
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
        undo_history_state = result[-3]
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
        undo_history_state = result[-3]
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
        loaded_canvas = result[-2]
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
        assert result[-2] == cv.DEFAULT_CANVAS

        old_path = os.path.join(tmp_dir, "old_session.ltr")
        with open(old_path, "w") as f:
            del data["canvas"]
            data["version"] = 8
            json.dump(data, f)
        old_result = session_io.load_session(old_path)
        assert old_result[-2] == cv.DEFAULT_CANVAS
    print("load_session: canvas=None and a missing 'canvas' key (pre-version-9 file) "
          "both load as DEFAULT_CANVAS -- OK")


def test_undo_history_to_session_list_caps_at_max_persisted_states():
    states = [st.Stack() for _ in range(session_io.MAX_PERSISTED_UNDO_STATES + 10)]
    history = UndoHistory(states[0])
    for state in states[1:]:
        history.push(state)
    assert history.pointer == len(states) - 1

    data = session_io.undo_history_to_session_list(history)
    assert len(data["states"]) == session_io.MAX_PERSISTED_UNDO_STATES
    # Dropped from the oldest end -- current position (the redo-tail-free
    # end) is preserved exactly, just shifted to match the shorter list.
    assert data["pointer"] == session_io.MAX_PERSISTED_UNDO_STATES - 1
    print("undo_history_to_session_list: caps at MAX_PERSISTED_UNDO_STATES, "
          "dropping from the oldest end and shifting the pointer to match "
          "-- OK")


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
    test_branch_tagged_stage_skipped_individually_when_it_cant_reconstruct()
    test_stack_to_session_dict_raises_loudly_for_unreconstructable_lut_stage()
    test_load_session_with_unknown_future_stage_kind_still_loads_others()
    test_lut_library_session_round_trip()
    test_lut_library_from_session_list_skips_bad_entry_with_warning()
    test_save_session_never_writes_a_workspace_key()
    test_load_session_ignores_a_leftover_workspace_key_from_version_6()
    test_image_library_session_round_trip_via_real_file()
    test_image_library_video_entry_session_round_trip()
    test_image_library_from_session_list_defaults_media_kind_for_old_files()
    test_image_library_from_session_list_skips_missing_file_with_warning()
    test_memory_library_session_round_trip_with_thumbnail()
    test_memory_library_from_session_list_defaults_thumbnail_for_old_files()
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
    print()
    print("All session_io tests passed.")
