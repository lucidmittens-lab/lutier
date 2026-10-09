"""stack_panel/labels.py -- Qt-free text for the Stack panel: per-kind field specs
and ranges for the inspector pages, row-label formatters, kind badges, and
the Alt+hover info card text (_stage_info_text).
"""
import stack as st
import cdl
import custom_drt
import exposure_contrast as ec
import grading_primary as gpr
import grading_rgb_curve as grc
import grading_tone as gtn


PRINTER_LIGHT_RANGE = 50


# CDL SOP fields: (params key, display label, spin min, spin max, identity
# value, spin step, wheel strength). "wheel strength" is how much a
# channel's value shifts when the puck sits at the wheel's very edge along
# that channel's own axis -- see color_balance.py's rgb_from_master_and_wheel.
# 1.0 for all three keeps the wheel's full-radius swing within each field's
# own spin range around identity (e.g. offset's [-1,1] range, slope/power's
# identity-of-1.0 with headroom on both sides) without needing a separate
# per-field tuning pass -- exact numeric extremes are still reachable via
# the fine R/G/B spinboxes regardless of what the wheel's radius can reach.
CDL_SOP_FIELD_SPECS = [
    ("slope", "Slope", 0.0, 4.0, 1.0, 0.01, 1.0),
    ("offset", "Offset", -1.0, 1.0, 0.0, 0.001, 1.0),
    ("power", "Power", 0.01, 4.0, 1.0, 0.01, 1.0),
]
CDL_WHEEL_DIAMETER = 90
CDL_SAT_RANGE = 400  # slider units -> /100 -> 0.0-4.0, matching the spin range
CDL_SOP_FIELD_DEFAULTS = {fk: default for fk, _l, _lo, _hi, default, _s, _st in CDL_SOP_FIELD_SPECS}

# Which of GradingPrimary's RGBM fields are actually meaningful per style
# (see grading_primary.py's own docstring) -- _build_grading_primary_
# inspector builds a row for every field below, but only these three (per
# style) are ever shown at once, toggled by _update_grading_primary_field_
# visibility on style change. Pivot and Saturation are always shown --
# both are meaningful (or at least harmless) regardless of style.
GRADING_PRIMARY_STYLE_FIELDS = {
    "log": ("brightness", "contrast", "gamma"),
    "lin": ("offset", "exposure", "contrast"),
    "video": ("lift", "gamma", "gain"),
}
# (field_key, label, is_additive) -- additive fields (identity 0.0, e.g.
# brightness) get a different range/step than multiplicative ones
# (identity 1.0, e.g. contrast/gamma/gain) -- same "own spin range around
# identity" reasoning as CDL_SOP_FIELD_SPECS above. UNLIKE CDL, these
# fields' R/G/B/M spinboxes now ALSO get a color-balance wheel (per Tom:
# "adding ... color wheels to operators that dont have them") -- but
# GradingRGBM's 4th "master" (M) component is a REAL, independently
# stored value here, unlike CDL's wheel-computed master, which is a
# UI-only convenience derived from mean(R,G,B) and never itself stored
# (see grading_primary.py's docstring, and color_balance.
# wheel_from_rgb_given_master's own docstring for why that difference
# means this wheel's sync math can't just reuse CDL's rgb_to_master_and_
# wheel unchanged -- see _on_gpr_wheel_changed/_on_gpr_master_changed/
# _on_gpr_channel_spin_changed below). One shared strength for every
# field (unlike CDL_SOP_FIELD_SPECS' per-field column) -- simplicity over
# per-field tuning, matching how every field here already shares just two
# ranges (additive/multiplicative) rather than its own bespoke one.
GRADING_WHEEL_STRENGTH = 1.0
GRADING_PRIMARY_WHEEL_DIAMETER = 90
GRADING_PRIMARY_FIELD_SPECS = [
    ("brightness", "Brightness", True),
    ("contrast", "Contrast", False),
    ("gamma", "Gamma", False),
    ("offset", "Offset", True),
    ("exposure", "Exposure", True),
    ("lift", "Lift", True),
    ("gain", "Gain", False),
]
GRADING_PRIMARY_FIELD_DEFAULTS = {
    fk: (0.0 if is_additive else 1.0) for fk, _l, is_additive in GRADING_PRIMARY_FIELD_SPECS}
GRADING_PRIMARY_ADDITIVE_RANGE = (-2.0, 2.0, 0.01)  # lo, hi, step
# Floor is 0.01, not 0.0 -- ocio.GradingPrimary.validate() requires
# contrast/gamma to stay strictly positive (confirmed via probing, see
# grading_primary.py's own _POSITIVE_FIELD_FLOOR comment); gain doesn't
# strictly need this floor but shares this range constant for simplicity.
GRADING_PRIMARY_MULT_RANGE = (0.01, 4.0, 0.01)
GRADING_TONE_WHEEL_DIAMETER = 60  # smaller than CDL/Grading Primary's 90 --
# 5 ranges here (vs CDL's 3, Grading Primary's 3-visible-at-once) means 5
# wheels stacked vertically at 90px each would meaningfully bloat this
# already-the-largest inspector (see _build_grading_tone_inspector's own
# docstring on "largest of the four Tier 1 inspectors"); 60px keeps the
# per-range row compact while still being genuinely drag-usable.


def _format_grading_primary_label(params):
    """Stage-row label for a 'grading_primary' stage -- shows the active
    style (same "the row tells you what it actually does" convention
    _format_cdl_label follows), not a static "Grading Primary". Doesn't
    dump every RGBM field's values the way CDL's label does -- there are
    7 possible fields here vs. CDL's fixed 3, so a full dump would be
    unreadable; the style name plus the inspector (which shows only the
    fields that style actually uses) is enough context."""
    style = params.get("style", gpr.DEFAULT_STYLE)
    style_label = dict(gpr.list_available_styles()).get(style, style)
    # Style labels are long/descriptive (good for a combo box); the row
    # label just wants the short name before the first " (".
    short_style = style_label.split(" (")[0]
    return f"Grading Primary ({short_style})"


def _format_exposure_contrast_label(params):
    """Stage-row label for an 'exposure_contrast' stage -- shows its live
    exposure/contrast/gamma values (same "the row tells you what it
    actually does" convention _format_cdl_label/_format_grading_primary_
    label follow) -- only 3 scalar fields here, so (unlike grading_
    primary's style-only label) a full value dump fits comfortably."""
    exposure = params.get("exposure", ec.IDENTITY_EXPOSURE)
    contrast = params.get("contrast", ec.IDENTITY_CONTRAST)
    gamma = params.get("gamma", ec.IDENTITY_GAMMA)
    return f"Exposure/Contrast  Exp {exposure:+.2f}  Con {contrast:.2f}  Gam {gamma:.2f}"


# Human-readable labels for grading_tone.RANGE_KEYS, in display order
# (matches OCIO's own blacks->shadows->midtones->highlights->whites
# ordering, dark to light).
GRADING_TONE_RANGE_LABELS = {
    "blacks": "Blacks", "shadows": "Shadows", "midtones": "Midtones",
    "highlights": "Highlights", "whites": "Whites",
}


def _format_grading_tone_label(params):
    """Stage-row label for a 'grading_tone' stage -- shows the active
    style only (same reasoning as _format_grading_primary_label: 5
    ranges x 6 fields is far too much to dump into one row label, the
    inspector is where the actual values live)."""
    style = params.get("style", gtn.DEFAULT_STYLE)
    style_label = dict(gtn.list_available_styles()).get(style, style)
    short_style = style_label.split(" (")[0]
    return f"Grading Tone ({short_style})"


def _format_grading_rgb_curve_label(params):
    """Stage-row label for a 'grading_rgb_curve' stage -- shows the
    active style only (same reasoning as _format_grading_primary_label/
    _format_grading_tone_label: 4 curves x an arbitrary number of control
    points each is far too much to dump into one row label, the inspector
    -- the curve editor itself -- is where the actual shape lives)."""
    style = params.get("style", grc.DEFAULT_STYLE)
    style_label = dict(grc.list_available_styles()).get(style, style)
    short_style = style_label.split(" (")[0]
    return f"Grading RGB Curve ({short_style})"


def _format_cdl_label(params):
    """Stage-row label for a 'cdl' stage -- shows its actual SOP/SAT
    values instead of a static "ASC CDL" (per Tom: same "the row tells you
    what it actually does" convention the 'colorspace' stage's dynamic
    "Color Space (src -> dst)" label already follows). Appends "[SOP off]"/
    "[SAT off]" when the corresponding bypass toggle is off, so a bypassed
    half is obvious at a glance in the stage list, not just in the
    inspector. Shared by _on_add_cdl_clicked (initial identity label),
    _apply_cdl_live (every live edit), and apply_cdl_entry (recalling a
    CDL Library entry) so all three ways a 'cdl' stage's params can change
    keep the row's label in sync the same way."""
    r, g, b = params.get("slope", cdl.IDENTITY_SLOPE)
    ro, go, bo = params.get("offset", cdl.IDENTITY_OFFSET)
    rp, gp, bp = params.get("power", cdl.IDENTITY_POWER)
    sat = params.get("sat", cdl.IDENTITY_SAT)
    label = (
        f"ASC CDL  S({r:.2f},{g:.2f},{b:.2f}) "
        f"O({ro:.2f},{go:.2f},{bo:.2f}) "
        f"P({rp:.2f},{gp:.2f},{bp:.2f}) Sat {sat:.2f}"
    )
    if not params.get("sop_enabled", True):
        label += " [SOP off]"
    if not params.get("sat_enabled", True):
        label += " [SAT off]"
    return label


def _format_fork_merge_label(params):
    """Stage-row label for a 'fork_merge' stage -- shows its live A/B
    blend split instead of a static "Fork Merge" (same "the row tells you
    what it actually does" convention _format_cdl_label/the 'colorspace'
    stage's dynamic label already follow). blend=0.0 is 100% branch A,
    blend=1.0 is 100% branch B -- see stack.py's make_fork_merge_stage/
    _blend_two. Shared by _on_add_fork_clicked (initial 50/50 label) and
    _apply_fork_merge_live (every live slider edit)."""
    blend = params.get("blend", 0.5)
    a_pct = round((1.0 - blend) * 100)
    b_pct = round(blend * 100)
    return f"Fork Merge  (A {a_pct}% / B {b_pct}%)"


# Short, fixed-width badge text per stage.kind -- a quick-scan visual cue
# in each row (per Tom, "snappier and more interactive" pass) so "what's
# in my stack" is readable at a glance without parsing every label's
# prose. Plain monospace-friendly abbreviations rather than pictographic
# icons -- this app has no icon/image asset pipeline anywhere else (every
# existing per-purpose glyph, the ☰ menu button, the ●/○ enabled toggle,
# the × delete button, is a plain Unicode character styled through QSS),
# so a text badge is the one approach consistent with that. Falls back to
# the kind string itself (upper, truncated) for anything not listed here
# rather than showing nothing.
STAGE_KIND_BADGES = {
    "lut": "LUT",
    "printer_lights": "PL",
    "aces_drt": "DRT",
    "custom_drt": "CDR",
    "cdl": "CDL",
    "colorspace": "CS",
    "grading_primary": "GP",
    "exposure_contrast": "EC",
    "grading_tone": "GT",
    "grading_rgb_curve": "RGB",
    "color_warper": "WRP",
    "keyer": "KEY",
    "matte_combine": "CMB",
    "matte_shape": "SHP",
    "fork_split": "FRK",
    "fork_merge": "MRG",
}


def _stage_kind_badge_text(kind):
    return STAGE_KIND_BADGES.get(kind, kind[:3].upper())


def _matte_options_by_index(stages, letters):
    """options[i] -- every MATTE_PRODUCING_KINDS stage (currently
    "keyer"/"segment_mask") at a position STRICTLY BEFORE index i in
    `stages`, as (letter, stage_id) pairs in stack order (see stack.py's
    docstring, "MATTE CHANNELS"). `letters` is the SAME dict
    stack.matte_letters_for(stages) already produced for this same
    stages list -- passed in rather than recomputed here so
    _refresh_list only calls that function once per rebuild. Computed
    once per _refresh_list() call rather than per row: each row's own
    "which matte channels can I reference" only depends on its OWN
    original position in the flat stages list, so a single upfront pass
    lets every row -- including ones rendered inside a fork region's
    two-column table, where visual row order and original stack-index
    order can differ (see _flush_fork_grid's own docstring on why cells
    are paired by POSITION WITHIN a branch, not by original index) --
    look its own options up in O(1) by that original index, with no
    fork-aware bookkeeping needed here at all."""
    options = []
    seen_so_far = []
    for stage in stages:
        options.append(list(seen_so_far))
        if stage.kind in st.MATTE_PRODUCING_KINDS:
            seen_so_far.append((letters[stage.id], stage.id))
    return options





def _stage_info_text(stage):
    """Alt+hover info card content (see hover_inspector.py) for one
    Stage -- a per-kind summary of the params the Parameters panel would
    otherwise make you select the stage and look at. Deliberately not
    exhaustive for every kind's every param (e.g. skips config_name when
    it's just the default) -- enough to answer "what is this stage
    actually doing" at a glance, not a full dump."""
    p = stage.params
    lines = [stage.label, f"[{stage.kind}]"]
    if not stage.enabled:
        lines.append("Disabled")
    if stage.opacity < 1.0:
        lines.append(f"Opacity: {stage.opacity:.0%}")
    if stage.branch:
        lines.append(f"Branch: {stage.branch.upper()}")

    if stage.kind == "lut":
        source_kind = p.get("source_kind")
        if source_kind == "file":
            lines.append(f"Source: {p.get('source_path') or '?'}")
        elif source_kind == "identity":
            lines.append(f"Identity, size {p.get('identity_size', '?')}")
        elif source_kind == "embedded":
            lines.append("Source: LUT Library (embedded)")
        retarget_from, retarget_to = p.get("retarget_from"), p.get("retarget_to")
        if retarget_from is not None and retarget_to is not None:
            lines.append(f"Retarget: {retarget_from} -> {retarget_to}")
    elif stage.kind == "printer_lights":
        lines.append(
            f"R {p.get('r_points', 0.0):+.1f}  G {p.get('g_points', 0.0):+.1f}  "
            f"B {p.get('b_points', 0.0):+.1f} points")
        lines.append(f"Working space: {p.get('working_colourspace', '?')}")
    elif stage.kind == "aces_drt":
        lines.append(f"{p.get('working_colourspace', '?')} -> {p.get('display', '?')}/{p.get('view', '?')}")
    elif stage.kind == "custom_drt":
        mastering_display = p.get("mastering_display", custom_drt.DEFAULT_MASTERING_DISPLAY)
        final_target = p.get(
            "final_target", custom_drt.DEFAULT_FINAL_TARGET_FOR_DISPLAY[mastering_display])
        lines.append(f"{p.get('working_colourspace', '?')} -> {mastering_display} -> {final_target}")
        if mastering_display == "HDR":
            lines.append(
                f"PQ, mastering {p.get('mastering_brightness', custom_drt.DEFAULT_MASTERING_BRIGHTNESS)} nits")
        elif mastering_display == "SDR":
            lines.append("Gamma 2.4" if final_target != "sRGB" else "sRGB")
        else:
            lines.append("Gamma 2.6")
        lines.append(
            f"Contrast {p.get('contrast', custom_drt.DEFAULT_CONTRAST):.2f}  "
            f"Headroom {p.get('headroom', custom_drt.DEFAULT_HEADROOM):.1f} stops")
        lines.append(
            f"Identity zone {p.get('identity_zone', custom_drt.DEFAULT_IDENTITY_ZONE):.0%}  "
            f"Transition {p.get('transition_softness', custom_drt.DEFAULT_TRANSITION_SOFTNESS):.0%}")
        lines.append(
            f"Fullness R{p.get('fullness_r', custom_drt.DEFAULT_FULLNESS_R):.2f} "
            f"G{p.get('fullness_g', custom_drt.DEFAULT_FULLNESS_G):.2f} "
            f"B{p.get('fullness_b', custom_drt.DEFAULT_FULLNESS_B):.2f}")
    elif stage.kind == "cdl":
        r, g, b = p.get("slope", cdl.IDENTITY_SLOPE)
        ro, go, bo = p.get("offset", cdl.IDENTITY_OFFSET)
        rp, gp, bp = p.get("power", cdl.IDENTITY_POWER)
        lines.append(f"S({r:.3f},{g:.3f},{b:.3f}) O({ro:.3f},{go:.3f},{bo:.3f}) P({rp:.3f},{gp:.3f},{bp:.3f})")
        lines.append(f"Sat {p.get('sat', cdl.IDENTITY_SAT):.3f}  Style: {p.get('style', cdl.DEFAULT_STYLE)}")
        if not p.get("sop_enabled", True):
            lines.append("SOP: bypassed")
        if not p.get("sat_enabled", True):
            lines.append("SAT: bypassed")
    elif stage.kind == "colorspace":
        lines.append(f"{p.get('src', '?')} -> {p.get('dst', '?')}")
    elif stage.kind == "grading_primary":
        style = p.get("style", gpr.DEFAULT_STYLE)
        lines.append(f"Style: {style}")
        for field_key in GRADING_PRIMARY_STYLE_FIELDS.get(style, ()):
            default = gpr.IDENTITY_ADD if field_key in ("brightness", "offset", "lift") else gpr.IDENTITY_MULT
            r, g, b, m = p.get(field_key, default)
            label = dict((k, lbl) for k, lbl, _add in GRADING_PRIMARY_FIELD_SPECS)[field_key]
            lines.append(f"{label}: R{r:.3f} G{g:.3f} B{b:.3f} M{m:.3f}")
        lines.append(f"Pivot {p.get('pivot', gpr.DEFAULT_PIVOT):.3f}  "
                     f"Sat {p.get('saturation', gpr.DEFAULT_SATURATION):.3f}")
    elif stage.kind == "exposure_contrast":
        lines.append(f"Style: {p.get('style', ec.DEFAULT_STYLE)}")
        lines.append(
            f"Exposure {p.get('exposure', ec.IDENTITY_EXPOSURE):+.3f}  "
            f"Contrast {p.get('contrast', ec.IDENTITY_CONTRAST):.3f}  "
            f"Gamma {p.get('gamma', ec.IDENTITY_GAMMA):.3f}")
    elif stage.kind == "grading_tone":
        style = p.get("style", gtn.DEFAULT_STYLE)
        lines.append(f"Style: {style}")
        for range_key in gtn.RANGE_KEYS:
            r, g, b, m, start, width = p.get(range_key) or gtn.DEFAULT_RANGE_VALUES[range_key]
            lines.append(
                f"{GRADING_TONE_RANGE_LABELS[range_key]}: R{r:.2f} G{g:.2f} B{b:.2f} M{m:.2f}  "
                f"(start {start:.2f}, width {width:.2f})")
        lines.append(f"S-Contrast {p.get('scontrast', gtn.DEFAULT_SCONTRAST):.3f}")
    elif stage.kind == "grading_rgb_curve":
        style = p.get("style", grc.DEFAULT_STYLE)
        lines.append(f"Style: {style}")
        for channel in grc.RGB_CHANNELS:
            points = p.get(channel) or grc.DEFAULT_CONTROL_POINTS
            lines.append(f"{channel.capitalize()}: {len(points)} points")
    elif stage.kind == "fork_merge":
        lines.append(f"Blend: {p.get('blend', 0.5):.0%}")

    return "\n".join(lines)
