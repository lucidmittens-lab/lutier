"""Tests for scopes.py -- run directly: python3 -m tests.test_scopes"""
import numpy as np

from scopes import (
    compute_luma, compute_histogram, compute_waveform,
    compute_chroma_plane, compute_vectorscope, LUMA_COEFFICIENTS_REC709,
    pq_encode, pq_graticule_stops, PQ_PEAK_NITS, PQ_GRATICULE_NITS,
    mv_graticule_stops, MV_FULL_SCALE, MV_GRATICULE_MILLIVOLTS,
    percent_graticule_stops, PERCENT_GRATICULE_VALUES,
)


def test_luma_of_white_is_one():
    white = np.ones((4, 4, 3))
    luma = compute_luma(white)
    assert np.allclose(luma, 1.0), "luma of pure white should be 1.0"
    print("compute_luma: white -> 1.0 -- OK")


def test_luma_of_black_is_zero():
    black = np.zeros((4, 4, 3))
    luma = compute_luma(black)
    assert np.allclose(luma, 0.0), "luma of pure black should be 0.0"
    print("compute_luma: black -> 0.0 -- OK")


def test_luma_coefficients_sum_to_one():
    assert abs(sum(LUMA_COEFFICIENTS_REC709) - 1.0) < 1e-9
    print("LUMA_COEFFICIENTS_REC709 sums to 1.0 -- OK")


def test_histogram_all_pixels_counted():
    rng = np.random.default_rng(0)
    img = rng.random((30, 40, 3))
    hist = compute_histogram(img, bins=50)
    for ch in ("r", "g", "b", "luma"):
        assert hist[ch].sum() == 30 * 40, f"{ch} histogram should count every pixel exactly once"
    assert len(hist["bin_edges"]) == 51
    print("compute_histogram: every pixel counted exactly once per channel -- OK")


def test_histogram_solid_color_lands_in_one_bin():
    img = np.full((5, 5, 3), 0.75)
    hist = compute_histogram(img, bins=100, value_range=(0.0, 1.0))
    nonzero_bins_r = np.flatnonzero(hist["r"])
    assert len(nonzero_bins_r) == 1, "a solid-color image should land entirely in one bin"
    # 0.75 in [0,1) over 100 bins -> bin index 75
    assert nonzero_bins_r[0] == 75
    print("compute_histogram: solid 0.75 image lands in bin 75/100 -- OK")


def test_histogram_out_of_range_values_excluded_like_numpy():
    # compute_histogram is a thin np.histogram wrapper -- confirm it
    # inherits numpy's documented "outside range is dropped" behavior
    # rather than clipping, since that's what a caller reading this code
    # would expect from range= being passed straight through.
    img = np.array([[[1.5, 1.5, 1.5], [0.5, 0.5, 0.5]]])
    hist = compute_histogram(img, bins=10, value_range=(0.0, 1.0))
    assert hist["r"].sum() == 1, "the 1.5 pixel should be excluded, only the 0.5 one counted"
    print("compute_histogram: out-of-range values dropped (numpy semantics) -- OK")


def test_waveform_shape_and_total_count():
    rng = np.random.default_rng(1)
    img = rng.random((20, 60, 3))
    wf = compute_waveform(img, levels=128)
    for ch in ("r", "g", "b", "luma"):
        assert wf[ch].shape == (128, 60)
        assert wf[ch].sum() == 20 * 60, f"{ch} waveform should count every pixel exactly once"
    print("compute_waveform: shape (levels, width) and full pixel count per channel -- OK")


def test_waveform_solid_column_lands_in_one_row():
    # A column that's entirely one value should produce a waveform column
    # with all its mass in a single row (brightness level).
    img = np.zeros((10, 3, 3))
    img[:, 0, :] = 0.2  # column 0 solid dark
    img[:, 1, :] = 0.8  # column 1 solid bright
    img[:, 2, :] = 0.5  # column 2 mid
    wf = compute_waveform(img, levels=10, value_range=(0.0, 1.0))
    col0_row = np.flatnonzero(wf["r"][:, 0])
    col1_row = np.flatnonzero(wf["r"][:, 1])
    assert len(col0_row) == 1 and col0_row[0] == 2   # 0.2 * 10 = bin 2
    assert len(col1_row) == 1 and col1_row[0] == 8   # 0.8 * 10 = bin 8
    assert wf["r"][col0_row[0], 0] == 10  # all 10 rows of that column counted
    print("compute_waveform: solid column concentrates in the correct single row -- OK")


def test_chroma_plane_neutral_is_zero():
    # Any neutral gray (R=G=B) should land at Cb=Cr=0 regardless of level,
    # since Y equals the pixel value and B-Y = R-Y = 0.
    gray = np.full((4, 4, 3), 0.42)
    cb, cr = compute_chroma_plane(gray)
    assert np.allclose(cb, 0.0) and np.allclose(cr, 0.0)
    print("compute_chroma_plane: neutral gray -> Cb=Cr=0 -- OK")


def test_chroma_plane_pure_red_vs_pure_blue_opposite_signs():
    red = np.zeros((2, 2, 3)); red[..., 0] = 1.0
    blue = np.zeros((2, 2, 3)); blue[..., 2] = 1.0
    cb_red, cr_red = compute_chroma_plane(red)
    cb_blue, cr_blue = compute_chroma_plane(blue)
    # Pure red: high Cr, near-zero-ish Cb (Y from red alone via coefficients).
    # Pure blue: high Cb, low Cr. The two channels should clearly separate.
    assert cr_red.mean() > 0.3, "pure red should read strongly positive on Cr"
    assert cb_blue.mean() > 0.3, "pure blue should read strongly positive on Cb"
    assert cb_red.mean() < cb_blue.mean(), "red should sit far from blue's Cb position"
    print("compute_chroma_plane: red/blue separate clearly on Cb/Cr -- OK")


def test_vectorscope_density_shape_and_total():
    rng = np.random.default_rng(2)
    img = rng.random((15, 15, 3))
    vs = compute_vectorscope(img, bins=64)
    assert vs["density"].shape == (64, 64)
    assert vs["density"].sum() == 15 * 15
    assert vs["chroma_range"] == (-0.5, 0.5)
    print("compute_vectorscope: shape and total pixel count correct -- OK")


def test_vectorscope_neutral_image_concentrates_at_center():
    gray = np.full((10, 10, 3), 0.5)
    vs = compute_vectorscope(gray, bins=100, chroma_range=(-0.5, 0.5))
    density = vs["density"]
    center = density.shape[0] // 2
    # All mass should be in the single bin nearest (0,0).
    nonzero = np.argwhere(density > 0)
    assert len(nonzero) == 1
    r, c = nonzero[0]
    assert abs(r - center) <= 1 and abs(c - center) <= 1
    print("compute_vectorscope: neutral gray image concentrates at the center -- OK")


def test_pq_encode_endpoints():
    assert abs(float(pq_encode(0.0))) < 1e-3, "0 nits should encode near 0.0"
    assert abs(float(pq_encode(PQ_PEAK_NITS)) - 1.0) < 1e-9, "peak nits (10,000) must encode to exactly 1.0"
    print("pq_encode: 0 nits -> ~0.0, 10,000 nits (PQ_PEAK_NITS) -> 1.0 -- OK")


def test_pq_encode_monotonically_increasing():
    nits = np.linspace(0.0, PQ_PEAK_NITS, 1000)
    code_values = pq_encode(nits)
    assert np.all(np.diff(code_values) >= 0), "PQ OETF must be monotonically increasing"
    print("pq_encode: monotonically increasing across the full 0..10,000 nit range -- OK")


def test_pq_encode_clamps_above_peak():
    # PQ isn't defined beyond its own peak -- values above PQ_PEAK_NITS
    # should clamp to the same code value the peak itself gives, not
    # extrapolate past 1.0 or raise.
    at_peak = float(pq_encode(PQ_PEAK_NITS))
    above_peak = float(pq_encode(PQ_PEAK_NITS * 2))
    assert at_peak == above_peak == 1.0
    print("pq_encode: values above PQ_PEAK_NITS clamp to 1.0, same as the peak itself -- OK")


def test_pq_encode_accepts_scalar_and_array():
    scalar_result = pq_encode(100)
    array_result = pq_encode(np.array([100.0]))
    assert np.isclose(float(scalar_result), float(array_result[0]))
    print("pq_encode: scalar and single-element array give the same result -- OK")


def test_pq_graticule_stops_match_nits_list_and_pq_encode():
    stops = pq_graticule_stops()
    assert [nits for nits, _code_value in stops] == list(PQ_GRATICULE_NITS)
    for nits, code_value in stops:
        assert code_value == float(pq_encode(nits)), (
            "pq_graticule_stops must derive its code values from pq_encode "
            "directly, not a separately-maintained table"
        )
    # Higher nits must always mean a higher (or equal) code value, since
    # scopes_panel.py positions each graticule line by this exact value.
    code_values = [code_value for _nits, code_value in stops]
    assert code_values == sorted(code_values)
    print("pq_graticule_stops: nits list matches PQ_GRATICULE_NITS, code values "
          "come straight from pq_encode, and are in increasing order -- OK")


def test_mv_graticule_stops_match_millivolts_list_and_full_scale():
    stops = mv_graticule_stops()
    assert [mv for mv, _code_value in stops] == list(MV_GRATICULE_MILLIVOLTS)
    for mv, code_value in stops:
        assert code_value == mv / MV_FULL_SCALE
    assert stops[0] == (0, 0.0), "0mV must be code value 0.0"
    assert stops[-1] == (700, 1.0), "700mV (MV_FULL_SCALE) must be code value 1.0"
    code_values = [code_value for _mv, code_value in stops]
    assert code_values == sorted(code_values)
    print("mv_graticule_stops: millivolts list matches MV_GRATICULE_MILLIVOLTS, "
          "0mV->0.0, 700mV->1.0, increasing order -- OK")


def test_percent_graticule_stops_match_values_list():
    stops = percent_graticule_stops()
    assert [pct for pct, _code_value in stops] == list(PERCENT_GRATICULE_VALUES)
    for pct, code_value in stops:
        assert code_value == pct / 100.0
    assert stops[0] == (0, 0.0)
    assert stops[-1] == (100, 1.0)
    print("percent_graticule_stops: percent list matches PERCENT_GRATICULE_VALUES, "
          "0%->0.0, 100%->1.0 -- OK")


def test_against_real_sample_image():
    from app_logic import load_image_as_array
    import glob
    candidates = glob.glob("samples/*.png") + glob.glob("samples/*.jpg")
    if not candidates:
        print("test_against_real_sample_image: skipped, no sample image in samples/")
        return
    # No real sample stills are checked in (samples/ has LUTs, not images),
    # so this just confirms the code path doesn't blow up if one shows up.
    img = load_image_as_array(candidates[0])
    compute_histogram(img)
    compute_waveform(img)
    compute_vectorscope(img)
    print("test_against_real_sample_image: ran against", candidates[0], "-- OK")


if __name__ == "__main__":
    test_luma_of_white_is_one()
    test_luma_of_black_is_zero()
    test_luma_coefficients_sum_to_one()
    test_histogram_all_pixels_counted()
    test_histogram_solid_color_lands_in_one_bin()
    test_histogram_out_of_range_values_excluded_like_numpy()
    test_waveform_shape_and_total_count()
    test_waveform_solid_column_lands_in_one_row()
    test_chroma_plane_neutral_is_zero()
    test_chroma_plane_pure_red_vs_pure_blue_opposite_signs()
    test_vectorscope_density_shape_and_total()
    test_vectorscope_neutral_image_concentrates_at_center()
    test_pq_encode_endpoints()
    test_pq_encode_monotonically_increasing()
    test_pq_encode_clamps_above_peak()
    test_pq_encode_accepts_scalar_and_array()
    test_pq_graticule_stops_match_nits_list_and_pq_encode()
    test_mv_graticule_stops_match_millivolts_list_and_full_scale()
    test_percent_graticule_stops_match_values_list()
    test_against_real_sample_image()
    print()
    print("All scopes tests passed.")
