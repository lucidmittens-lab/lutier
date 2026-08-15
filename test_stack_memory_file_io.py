"""Tests for stack_memory_file_io.py -- run directly:
python3 test_stack_memory_file_io.py

Covers exporting a single StackMemory to its own .ltm file and importing
it back, independent of a whole .ltr session -- see that module's
docstring.
"""
import os
import tempfile

import numpy as np
import colour

import stack as st
from stack_memory import StackMemory
import stack_memory_file_io as smf


def _make_lut3d(gain=2.0, size=3, name="test_lut"):
    identity = colour.LUT3D(size=size).table
    return colour.LUT3D(table=identity * gain, size=size, name=name)


def _make_sample_stack():
    lut_stage = st.make_lut_stage(_make_lut3d(gain=1.4, size=3, name="MemLUT"))
    lut_stage.params["source_kind"] = "embedded"
    cdl_stage = st.make_cdl_stage(slope=(1.1, 1.0, 0.95), offset=(0.01, 0.0, -0.01),
                                   power=(1.0, 1.0, 1.0), sat=1.05)
    return st.Stack(stages=[lut_stage, cdl_stage], size=17)


def test_export_then_import_round_trips_name_and_stack():
    memory = StackMemory("My Grade", _make_sample_stack())
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "my_grade.ltm")
        smf.export_memory(memory, path)
        assert os.path.exists(path)

        imported, warnings = smf.import_memory(path)

        assert warnings == []
        assert imported.name == "My Grade"
        assert len(imported.stack.stages) == 2
        assert imported.stack.size == 17
        assert imported.stack.stages[0].kind == "lut"
        assert imported.stack.stages[0].params["source_kind"] == "embedded"
        assert np.allclose(
            imported.stack.stages[0].params["lut"].table,
            memory.stack.stages[0].params["lut"].table)
        assert imported.stack.stages[1].kind == "cdl"
        assert imported.stack.stages[1].params["slope"] == (1.1, 1.0, 0.95)
    print("export_memory/import_memory: name, stage order/kinds, and an "
          "embedded LUT stage's table all round-trip through a real .ltm "
          "file -- OK")


def test_imported_memory_gets_a_fresh_id():
    memory = StackMemory("Original", _make_sample_stack())
    original_id = memory.id
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "mem.ltm")
        smf.export_memory(memory, path)
        imported, _warnings = smf.import_memory(path)
        assert imported.id != original_id
    print("import_memory: the imported StackMemory gets its own fresh "
          "id, never reuses the exporting session's id -- OK")


def test_import_rejects_a_non_stack_memory_file():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "not_a_memory.json")
        with open(path, "w") as f:
            f.write('{"format": "something-else", "version": 1}')
        try:
            smf.import_memory(path)
            raise AssertionError("expected ValueError for a non-Stack-Memory file")
        except ValueError:
            pass
    print("import_memory: raises ValueError for a file that isn't a "
          "LUTier Stack Memory file at all -- OK")


def test_export_writes_valid_json_with_expected_format_tag():
    import json
    memory = StackMemory("Tagged", _make_sample_stack())
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "tagged.ltm")
        smf.export_memory(memory, path)
        with open(path) as f:
            data = json.load(f)
        assert data["format"] == "lutier-stack-memory"
        assert data["version"] == 2
        assert data["name"] == "Tagged"
        assert "stack" in data
    print("export_memory: writes plain JSON tagged with the "
          "'lutier-stack-memory' format/version, independent of the .ltr "
          "session format -- OK")


def test_export_then_import_round_trips_thumbnail_and_full_still():
    thumb = {"mode": "embedded", "data_b64": "dGh1bWI="}
    full_still = {"mode": "embedded", "data_b64": "ZnVsbA=="}
    memory = StackMemory("With Still", _make_sample_stack(), thumbnail=thumb, full_still=full_still)
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "with_still.ltm")
        smf.export_memory(memory, path)
        imported, warnings = smf.import_memory(path)
        assert warnings == []
        assert imported.thumbnail == thumb
        assert imported.full_still == full_still
    print("export_memory/import_memory: thumbnail AND full_still both "
          "round-trip through a real .ltm file -- OK")


def test_import_defaults_thumbnail_and_full_still_for_a_version_1_file():
    """A pre-VERSION-2 .ltm file has no thumbnail/full_still keys at all
    -- must still load fine, defaulting both to None."""
    import json
    memory = StackMemory("Old Style", _make_sample_stack())
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "old.ltm")
        smf.export_memory(memory, path)
        with open(path) as f:
            data = json.load(f)
        data["version"] = 1
        del data["thumbnail"]
        del data["full_still"]
        with open(path, "w") as f:
            json.dump(data, f)

        imported, warnings = smf.import_memory(path)
        assert warnings == []
        assert imported.thumbnail is None
        assert imported.full_still is None
    print("import_memory: a VERSION 1 .ltm file with no thumbnail/"
          "full_still keys loads fine, defaulting both to None -- OK")


if __name__ == "__main__":
    test_export_then_import_round_trips_name_and_stack()
    test_imported_memory_gets_a_fresh_id()
    test_import_rejects_a_non_stack_memory_file()
    test_export_writes_valid_json_with_expected_format_tag()
    test_export_then_import_round_trips_thumbnail_and_full_still()
    test_import_defaults_thumbnail_and_full_still_for_a_version_1_file()
    print()
    print("All stack_memory_file_io tests passed.")
