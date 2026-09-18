"""Tests for colorspace.py -- run directly:
python3 test_colorspace.py

Previously the single most significant test-coverage gap in the repo
(found in code review): colorspace.py is the shared color-space lookup
used by color_warper.py, printer_lights.py, and volumetric_scope.py, but
had zero direct tests -- everything about it was only ever exercised
incidentally through those callers.

Qt-free, no real GL/OCIO context needed -- built directly on colour-
science's own registry.
"""
import numpy as np
import colour

import colorspace as cs


def test_lookup_colourspace_resolves_a_known_builtin_name():
    result = cs.lookup_colourspace("ACEScct")
    assert result is colour.RGB_COLOURSPACES["ACEScct"]
    assert result.name == "ACEScct"
    print("lookup_colourspace: resolves a known built-in colour-science "
          "name to its real RGB_Colourspace object -- OK")


def test_lookup_colourspace_raises_keyerror_for_unknown_name():
    try:
        cs.lookup_colourspace("not a real colourspace")
        raise AssertionError("expected KeyError for an unknown colourspace name")
    except KeyError as e:
        assert "not a real colourspace" in str(e)
    print("lookup_colourspace: raises KeyError (naming the bad value) "
          "for an unknown colourspace, not a silent None/crash -- OK")


def test_lookup_colourspace_checks_custom_registry_first():
    """CUSTOM_COLOURSPACES entries must be able to override a built-in
    colour-science name of the same key, per this module's own docstring
    ('checking the custom registry first so local definitions can
    override or extend the built-in colour-science set')."""
    fake_override = object()
    cs.CUSTOM_COLOURSPACES["ACEScct"] = fake_override
    try:
        assert cs.lookup_colourspace("ACEScct") is fake_override
    finally:
        del cs.CUSTOM_COLOURSPACES["ACEScct"]  # never leak state into other tests
    # And the real built-in is resolved again once the override is gone.
    assert cs.lookup_colourspace("ACEScct") is colour.RGB_COLOURSPACES["ACEScct"]
    print("lookup_colourspace: a CUSTOM_COLOURSPACES entry overrides a "
          "built-in name of the same key -- OK")


def test_lookup_colourspace_resolves_a_pure_custom_name():
    fake_space = object()
    cs.CUSTOM_COLOURSPACES["My Custom Space"] = fake_space
    try:
        assert cs.lookup_colourspace("My Custom Space") is fake_space
    finally:
        del cs.CUSTOM_COLOURSPACES["My Custom Space"]
    print("lookup_colourspace: resolves a name that exists ONLY in "
          "CUSTOM_COLOURSPACES, not in colour-science's own registry -- OK")


def test_convert_matches_colour_rgb_to_rgb_directly():
    """convert() must be a thin, faithful wrapper -- the exact same
    numbers colour.RGB_to_RGB itself would give for the same two real
    colourspaces."""
    rng = np.random.default_rng(0)
    values = rng.uniform(0.05, 0.95, size=(4, 4, 3))
    result = cs.convert(values, "ACEScct", "sRGB")
    expected = colour.RGB_to_RGB(
        values, colour.RGB_COLOURSPACES["ACEScct"], colour.RGB_COLOURSPACES["sRGB"],
        chromatic_adaptation_transform="CAT02",
        apply_cctf_decoding=True, apply_cctf_encoding=True)
    assert np.allclose(result, expected)
    print("convert: matches colour.RGB_to_RGB exactly for the equivalent "
          "direct call -- OK")


def test_convert_decode_input_and_encode_output_flags_actually_change_the_result():
    """decode_input/encode_output=False must skip that side's own cctf --
    a real, visible numeric difference for a log space like ACEScct,
    not a silently-ignored flag."""
    rng = np.random.default_rng(1)
    values = rng.uniform(0.05, 0.95, size=(4, 4, 3))
    decoded = cs.convert(values, "ACEScct", "sRGB", decode_input=True, encode_output=False)
    not_decoded = cs.convert(values, "ACEScct", "sRGB", decode_input=False, encode_output=False)
    assert not np.allclose(decoded, not_decoded), (
        "decode_input=False must skip ACEScct's own log decode, giving a different result")
    encoded = cs.convert(values, "ACEScct", "sRGB", decode_input=False, encode_output=True)
    not_encoded = cs.convert(values, "ACEScct", "sRGB", decode_input=False, encode_output=False)
    assert not np.allclose(encoded, not_encoded), (
        "encode_output=False must skip sRGB's own eotf_inverse encode, giving a different result")
    print("convert: decode_input/encode_output actually gate their own "
          "cctf step, each producing a visibly different result -- OK")


def test_convert_same_space_both_ways_is_identity():
    rng = np.random.default_rng(2)
    values = rng.uniform(0.05, 0.95, size=(3, 3, 3))
    result = cs.convert(values, "ACEScct", "ACEScct")
    assert np.allclose(result, values, atol=1e-9)
    print("convert: from a space to itself is an exact identity -- OK")


def test_cctf_short_name_none_is_linear():
    assert cs._cctf_short_name(None) == "Linear"
    print("_cctf_short_name: None (a linear space's own cctf) labels as "
          "'Linear' -- OK")


def test_cctf_short_name_uses_curated_label_for_a_known_curve():
    encoding_fn = colour.RGB_COLOURSPACES["ACEScct"].cctf_encoding
    assert cs._cctf_short_name(encoding_fn) == "ACEScct"
    encoding_fn2 = colour.RGB_COLOURSPACES["ARRI Wide Gamut 3"].cctf_encoding
    assert cs._cctf_short_name(encoding_fn2) == "LogC3"
    print("_cctf_short_name: uses the curated _CCTF_LABELS entry for a "
          "known curve (ACEScct -> 'ACEScct', ARRILogC3 -> 'LogC3') -- OK")


def test_cctf_short_name_falls_back_to_regex_stripped_name_for_an_unrecognized_curve():
    """Per this module's own docstring: an unrecognized curve should look
    UNFAMILIAR, not be silently dropped -- confirms the regex fallback
    actually strips the common colour-science prefix rather than just
    handing back the raw function name."""
    def log_encoding_TotallyMadeUpCurve(x):  # noqa: N802 -- matching colour-science's own naming
        return x
    assert cs._cctf_short_name(log_encoding_TotallyMadeUpCurve) == "TotallyMadeUpCurve"
    print("_cctf_short_name: an unrecognized curve falls back to its own "
          "name with the common colour-science prefix stripped -- OK")


def test_cctf_short_name_unknown_prefix_falls_back_to_raw_name():
    def a_function_with_no_recognized_prefix_at_all(x):
        return x
    result = cs._cctf_short_name(a_function_with_no_recognized_prefix_at_all)
    assert result == "a_function_with_no_recognized_prefix_at_all"
    print("_cctf_short_name: a function matching no known prefix at all "
          "falls back to its own raw name, not 'Unknown' -- OK")


def test_list_colourspace_choices_every_name_resolves():
    choices = cs.list_colourspace_choices()
    assert len(choices) > 0
    for label, name in choices:
        assert isinstance(label, str) and label
        resolved = cs.lookup_colourspace(name)  # must not raise
        assert resolved is not None
    print(f"list_colourspace_choices: {len(choices)} entries, every "
          f"colourspace_name resolves via lookup_colourspace -- OK")


def test_list_colourspace_choices_is_sorted_by_label():
    choices = cs.list_colourspace_choices()
    labels = [label for label, _name in choices]
    assert labels == sorted(labels, key=str.lower)
    print("list_colourspace_choices: sorted case-insensitively by label -- OK")


def test_list_colourspace_choices_appends_curve_when_not_redundant():
    choices = dict((name, label) for label, name in cs.list_colourspace_choices())
    assert choices["ARRI Wide Gamut 3"] == "ARRI Wide Gamut 3 — LogC3"
    print("list_colourspace_choices: appends the curve label when it's "
          "not already implied by the gamut name (e.g. 'ARRI Wide Gamut "
          "3 — LogC3') -- OK")


def test_list_colourspace_choices_skips_redundant_curve_label():
    choices = dict((name, label) for label, name in cs.list_colourspace_choices())
    assert choices["sRGB"] == "sRGB", (
        "sRGB's own curve label ('sRGB') is already implied by the name -- must not double up")
    print("list_colourspace_choices: skips appending the curve label "
          "when it's already implied by the name (e.g. plain 'sRGB', "
          "not 'sRGB — sRGB') -- OK")


def test_list_colourspace_choices_includes_custom_colourspaces():
    fake_space = colour.RGB_COLOURSPACES["sRGB"]  # any real RGB_Colourspace works as a stand-in
    cs.CUSTOM_COLOURSPACES["My Test Space"] = fake_space
    try:
        names = {name for _label, name in cs.list_colourspace_choices()}
        assert "My Test Space" in names
    finally:
        del cs.CUSTOM_COLOURSPACES["My Test Space"]
    print("list_colourspace_choices: includes an entry registered in "
          "CUSTOM_COLOURSPACES, same as the built-in registry -- OK")


if __name__ == "__main__":
    test_lookup_colourspace_resolves_a_known_builtin_name()
    test_lookup_colourspace_raises_keyerror_for_unknown_name()
    test_lookup_colourspace_checks_custom_registry_first()
    test_lookup_colourspace_resolves_a_pure_custom_name()
    test_convert_matches_colour_rgb_to_rgb_directly()
    test_convert_decode_input_and_encode_output_flags_actually_change_the_result()
    test_convert_same_space_both_ways_is_identity()
    test_cctf_short_name_none_is_linear()
    test_cctf_short_name_uses_curated_label_for_a_known_curve()
    test_cctf_short_name_falls_back_to_regex_stripped_name_for_an_unrecognized_curve()
    test_cctf_short_name_unknown_prefix_falls_back_to_raw_name()
    test_list_colourspace_choices_every_name_resolves()
    test_list_colourspace_choices_is_sorted_by_label()
    test_list_colourspace_choices_appends_curve_when_not_redundant()
    test_list_colourspace_choices_skips_redundant_curve_label()
    test_list_colourspace_choices_includes_custom_colourspaces()
    print()
    print("All colorspace tests passed.")
