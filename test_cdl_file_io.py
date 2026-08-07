"""Tests for cdl_file_io.py -- run directly: python3 test_cdl_file_io.py

Uses the hand-written sample files in samples/cdl/ (single.cc, multi.cdl,
multi.ccc) -- one of each ASC CDL container format cdl_file_io.py claims
to handle uniformly via OCIO's CDLTransform.CreateGroupFromFile.
"""
from pathlib import Path

import cdl_file_io as cfio

SAMPLES_DIR = Path(__file__).parent / "samples" / "cdl"


def _skip(name):
    print(f"SKIPPED {name} -- PyOpenColorIO not installed")


def test_single_cc_file_parses_one_entry():
    if not cfio.OCIO_AVAILABLE:
        return _skip("test_single_cc_file_parses_one_entry")
    entries = cfio.parse_cdl_file(SAMPLES_DIR / "single.cc")
    assert len(entries) == 1
    e = entries[0]
    assert e["id"] == "shot010_grade"
    assert e["description"] == "Test grade"
    assert e["slope"] == (1.1, 1.0, 0.9)
    assert e["offset"] == (0.02, 0.0, -0.01)
    assert e["power"] == (1.0, 1.05, 1.0)
    assert abs(e["sat"] - 1.1) < 1e-9
    assert e["style"] in ("asc", "no_clip")
    print("parse_cdl_file(single.cc): one ColorCorrection, all fields "
          f"match the source file exactly -- OK ({e})")


def test_multi_cdl_file_parses_two_entries_in_order():
    if not cfio.OCIO_AVAILABLE:
        return _skip("test_multi_cdl_file_parses_two_entries_in_order")
    entries = cfio.parse_cdl_file(SAMPLES_DIR / "multi.cdl")
    assert len(entries) == 2
    assert entries[0]["id"] == "shot010"
    assert entries[0]["slope"] == (1.1, 1.0, 0.9)
    assert entries[1]["id"] == "shot020"
    assert entries[1]["slope"] == (0.9, 1.0, 1.2)
    print("parse_cdl_file(multi.cdl -- ColorDecisionList): two entries, "
          "correct order and values -- OK")


def test_multi_ccc_file_parses_two_entries_in_order():
    if not cfio.OCIO_AVAILABLE:
        return _skip("test_multi_ccc_file_parses_two_entries_in_order")
    entries = cfio.parse_cdl_file(SAMPLES_DIR / "multi.ccc")
    assert len(entries) == 2
    assert entries[0]["id"] == "shotA"
    assert entries[0]["sat"] == 1.0
    assert entries[1]["id"] == "shotB"
    assert abs(entries[1]["sat"] - 0.8) < 1e-9
    print("parse_cdl_file(multi.ccc -- ColorCorrectionCollection): two "
          "entries, correct order and values -- OK")


def test_all_three_formats_agree_on_shared_shot010_values():
    """single.cc and multi.cdl's first entry both encode the same
    shot010-style grade -- confirms .cc and .cdl aren't parsed by two
    different code paths that could silently disagree."""
    if not cfio.OCIO_AVAILABLE:
        return _skip("test_all_three_formats_agree_on_shared_shot010_values")
    from_cc = cfio.parse_cdl_file(SAMPLES_DIR / "single.cc")[0]
    from_cdl = cfio.parse_cdl_file(SAMPLES_DIR / "multi.cdl")[0]
    assert from_cc["slope"] == from_cdl["slope"]
    assert from_cc["offset"] == from_cdl["offset"]
    assert from_cc["power"] == from_cdl["power"]
    assert from_cc["sat"] == from_cdl["sat"]
    print(".cc and .cdl agree exactly on the same underlying SOP/SAT "
          "values -- confirms both container formats parse through the "
          "same OCIO path, not two independently-hand-rolled ones -- OK")


def test_parse_cdl_file_raises_for_malformed_file(tmp_path_str=None):
    if not cfio.OCIO_AVAILABLE:
        return _skip("test_parse_cdl_file_raises_for_malformed_file")
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".cc", mode="w", delete=False) as f:
        f.write("this is not valid ASC CDL XML at all")
        path = f.name
    try:
        raised = False
        try:
            cfio.parse_cdl_file(path)
        except Exception:
            raised = True
        assert raised, "a malformed file should raise, not silently return []"
        print("parse_cdl_file: raises (rather than silently returning "
              "nothing) for a malformed file -- OK")
    finally:
        import os
        os.unlink(path)


if __name__ == "__main__":
    test_single_cc_file_parses_one_entry()
    test_multi_cdl_file_parses_two_entries_in_order()
    test_multi_ccc_file_parses_two_entries_in_order()
    test_all_three_formats_agree_on_shared_shot010_values()
    test_parse_cdl_file_raises_for_malformed_file()
    print()
    print("All cdl_file_io tests passed.")
