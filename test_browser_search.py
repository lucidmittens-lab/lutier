"""
test_browser_search.py -- tests for scan_lut_folder's wildcard-aware
name_filter (app_logic._name_matches_filter) and its extra_extensions
param (browser_panel.py feeding in cdl_file_io.CDL_EXTENSIONS so the
Browser can list ASC CDL files alongside LUT files, per Tom). Fully
Qt-free.

Runs against the REAL samples/ folder rather than a synthetic tmp
directory, since it already has a realistic mix of real production
filenames and multiple extensions (some scannable, some not -- .3dl/.cub
files in there are export-only formats, not in SUPPORTED_EXTENSIONS, which
this also incidentally verifies) -- plus samples/cdl/ (single.cc,
multi.cdl, multi.ccc, see test_cdl_file_io.py) for the extra_extensions
coverage below.
"""
from pathlib import Path

from app_logic import scan_lut_folder, _name_matches_filter
from cdl_file_io import CDL_EXTENSIONS

SAMPLES_DIR = Path(__file__).parent / "samples"


def _names(results):
    return sorted(p.name for p in results)


def test_no_filter_returns_all_supported_extensions_only():
    results = _names(scan_lut_folder(SAMPLES_DIR, name_filter=None))
    # .3dl and .cub files exist in samples/ (from format_writers testing)
    # but aren't in SUPPORTED_EXTENSIONS -- confirms they're correctly
    # excluded regardless of the name filter feature being tested here.
    assert all(name.endswith((".cube", ".clf")) for name in results), results
    assert "roundtrip_check.cube" in results
    assert not any(name.endswith(".3dl") for name in results)
    assert not any(name.endswith(".cub") for name in results)
    print(f"no filter: {len(results)} supported-extension files found, .3dl/.cub correctly excluded -- OK")


def test_default_call_excludes_cdl_files():
    """Without extra_extensions, samples/cdl/*.cc/.cdl/.ccc (real files,
    recursively scanned since recursive=True by default) must NOT show up
    -- confirms this new param is purely additive/opt-in, existing callers
    are unaffected."""
    results = _names(scan_lut_folder(SAMPLES_DIR, name_filter=None))
    assert not any(name.endswith((".cc", ".cdl", ".ccc")) for name in results), results
    print("scan_lut_folder with no extra_extensions: CDL files correctly excluded -- OK")


def test_extra_extensions_includes_cdl_files_alongside_lut_files():
    results = _names(scan_lut_folder(
        SAMPLES_DIR, name_filter=None, extra_extensions=CDL_EXTENSIONS))
    assert "single.cc" in results
    assert "multi.cdl" in results
    assert "multi.ccc" in results
    # LUT files are still there too -- this is additive, not a replacement.
    assert "roundtrip_check.cube" in results
    print("scan_lut_folder with extra_extensions=CDL_EXTENSIONS: CDL files "
          "found alongside LUT files -- OK")


def test_extra_extensions_respects_name_filter_too():
    results = _names(scan_lut_folder(
        SAMPLES_DIR, name_filter="multi", extra_extensions=CDL_EXTENSIONS))
    assert results == ["multi.ccc", "multi.cdl"], results
    print("extra_extensions + name_filter combine correctly -- OK")


def test_plain_substring_filter_still_works_no_wildcard_syntax_needed():
    """The original behavior -- typing a plain partial name with no
    wildcard characters -- must keep working exactly as before, since
    that's the common case and shouldn't require learning glob syntax."""
    results = _names(scan_lut_folder(SAMPLES_DIR, name_filter="roundtrip"))
    assert results == ["roundtrip_check.cube"], results
    print("plain substring filter 'roundtrip' -- OK")


def test_plain_substring_filter_is_case_insensitive():
    results = _names(scan_lut_folder(SAMPLES_DIR, name_filter="ROUNDTRIP"))
    assert results == ["roundtrip_check.cube"], results
    print("plain substring filter is case-insensitive -- OK")


def test_wildcard_star_extension_filter():
    results = _names(scan_lut_folder(SAMPLES_DIR, name_filter="*.cube"))
    assert results == ["retargeted_ap0linear.cube", "roundtrip_check.cube"], results
    print("wildcard '*.cube' matches only .cube files -- OK")


def test_wildcard_star_prefix():
    results = _names(scan_lut_folder(SAMPLES_DIR, name_filter="retargeted*"))
    assert results == ["retargeted_ap0linear.clf", "retargeted_ap0linear.cube"], results
    print("wildcard 'retargeted*' prefix match -- OK")


def test_wildcard_question_mark():
    # "roundt?ip" -- the ? stands in for exactly one character ('r').
    results = _names(scan_lut_folder(SAMPLES_DIR, name_filter="roundt?ip*"))
    assert results == ["roundtrip_check.cube"], results
    print("wildcard '?' single-character match -- OK")


def test_wildcard_middle_pattern_matches_despite_trailing_content():
    """Reproduces the reported bug: a wildcard in the MIDDLE of the
    pattern (part1*part2) must still match filenames that have more
    content after part2 (extension, version suffix, etc). Old behavior
    (fnmatch anchored to the whole string) only worked when the second
    part happened to be at the very end of the filename -- which made it
    indistinguishable from a plain prefix search, exactly what was
    reported as "effectively doing nothing.\""""
    # Real file: rec709_to_p3d65pq_constrain709_v173.clf -- "constrain709"
    # is NOT at the end; "_v173.clf" comes after it.
    results = _names(scan_lut_folder(SAMPLES_DIR, name_filter="rec709*constrain709"))
    assert results == ["rec709_to_p3d65pq_constrain709_v173.clf"], results
    print("wildcard 'part*part' matches despite trailing content after the second part -- OK")


def test_wildcard_middle_pattern_direct_unit_check():
    # Same bug, isolated from folder scanning -- the exact shape the user
    # described: "1 part of the name, *, then a later part of the name."
    assert _name_matches_filter(
        "rec709_to_p3d65pq_constrain709_v173.clf", "rec709*constrain709")
    assert _name_matches_filter("K_022_C001_final.mov", "K_022*final")
    assert not _name_matches_filter("K_022_C001_final.mov", "K_099*final")
    print("wildcard 'part*part' unit-level check -- OK")


def test_wildcard_no_match_returns_empty():
    results = scan_lut_folder(SAMPLES_DIR, name_filter="*.nonexistent_ext")
    assert results == [], results
    print("wildcard with no matches returns empty list -- OK")


def test_wildcard_is_case_insensitive():
    results = _names(scan_lut_folder(SAMPLES_DIR, name_filter="*.CUBE"))
    assert results == ["retargeted_ap0linear.cube", "roundtrip_check.cube"], results
    print("wildcard matching is case-insensitive -- OK")


def test_name_matches_filter_unit_level():
    # Direct unit tests of the matcher itself, not just via the folder scan.
    assert _name_matches_filter("K_022_C001.mov", "K_0??_*")
    assert not _name_matches_filter("K_022_C001.mov", "K_1??_*")
    assert _name_matches_filter("Rec709_to_P3.cube", "rec709*")  # case-insensitive
    assert _name_matches_filter("anything_rec709_here.cube", "rec709")  # substring fallback
    print("_name_matches_filter unit-level checks -- OK")


if __name__ == "__main__":
    tests = [
        test_no_filter_returns_all_supported_extensions_only,
        test_default_call_excludes_cdl_files,
        test_extra_extensions_includes_cdl_files_alongside_lut_files,
        test_extra_extensions_respects_name_filter_too,
        test_plain_substring_filter_still_works_no_wildcard_syntax_needed,
        test_plain_substring_filter_is_case_insensitive,
        test_wildcard_star_extension_filter,
        test_wildcard_star_prefix,
        test_wildcard_question_mark,
        test_wildcard_middle_pattern_matches_despite_trailing_content,
        test_wildcard_middle_pattern_direct_unit_check,
        test_wildcard_no_match_returns_empty,
        test_wildcard_is_case_insensitive,
        test_name_matches_filter_unit_level,
    ]
    for t in tests:
        t()
    print("\nAll browser search tests passed.")
