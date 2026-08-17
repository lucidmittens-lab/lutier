"""
stack.py -- Qt-free backend for a unified, ordered, non-destructive
operation stack: a single list of stages (each add a LUT, a Printer Lights
trim, an ACES DRT output transform, an ASC CDL SOP/SAT grade, an OCIO
Grading Primary grade, an OCIO Exposure/Contrast grade, an OCIO Grading
Tone grade, an OCIO Grading RGB Curve grade, or a plain non-DRT color
space conversion) that can be reordered, toggled, given
per-stage opacity, and re-baked into one flat colour.LUT3D at any time.
This is the "recontextualize as a stack you can build and reorder"
architecture -- everything else in this app (3D viewer, sample image
preview, scopes, exporters) should read from a Stack's baked/applied
output instead of tracking "the current LUT" and "the Concatenate chain"
as two separate things.

WHY THIS WAS FEASIBLE WITHOUT A REWRITE OF THE MATH: every operation this
app has ever offered (Retarget, Printer Lights, ACES DRT, ASC CDL, plain
color space conversion, and ordinary LUT application) already worked --
or, for CDL/colorspace, works the same way by construction -- by applying
to an identity grid to produce a flat colour.LUT3D -- see
app_logic.bake_retargeted_lut, printer_lights.bake_printer_lights_on_lut,
aces_drt.bake_aces_output_transform_to_lut3d, cdl.apply_cdl,
colorspace_convert.apply_colorspace_conversion, and
app_logic.concatenate_luts_with_opacity. That's precisely the shape a
stack needs: each stage is just "a function from an (H,W,3)-ish array to
another one," and Concatenate already proved out the reorderable,
opacity-aware, bake-to-LUT3D pattern for the "add another LUT" case. This
module generalizes that same pattern to the other operation kinds, so
"the stack" subsumes what used to be Concatenate plus what used to be the
one-shot Retarget/Printer Lights/ACES DRT panels.

SCOPE DECISION -- Resize and Bounds are NOT stage kinds here, on purpose.
Resize (grid resolution) and Bounds (domain metadata) were the two
LUT-Controls operations that don't fit the "pointwise array transform"
shape the other three do:
  - Resize used to mean "resample an already-baked table to a new size,"
    which is strictly LESS accurate than what the stack model gives you
    for free: since bake_stack_to_lut3d always recomputes every stage
    fresh from a plain identity grid at whatever size you ask for,
    changing Stack.size and re-baking recomputes the WHOLE chain natively
    at the new resolution -- no double interpolation. So "Resize" is just
    the Stack.size field, not a stage to insert.
  - Bounds has no per-position meaning mid-chain -- there's nothing for a
    "Bounds stage" to insert between other stages, so Stack.domain_min/
    domain_max stay stack-level fields, not a stage kind. UNLIKE
    app_logic.set_lut_domain (which relabels an already-baked, foreign
    table without recomputing it -- a legitimate metadata-only relabel,
    since it has no way to know how that table was produced), bake_stack_
    to_lut3d DOES own the whole computation, so it folds the stages over a
    grid that actually SPANS [domain_min, domain_max] before writing that
    same range onto the result's .domain -- see that function's own
    docstring for a real bug (Tom, 2026-08-12) this distinction fixed:
    an earlier version copied set_lut_domain's metadata-only SHAPE without
    the justification that made it valid there, always baking from a
    plain [0,1] grid and just relabeling the result, so an extended-domain
    export's header described a range the table was never computed for.
Retarget, by contrast, IS folded in as a per-stage option on "lut" stages
specifically (retarget_from/retarget_to params) rather than its own stage
kind -- see retarget.py's own docstring: retarget_io wraps a SPECIFIC
lut-like object to change its declared input/output space, it isn't a
general "convert array from space A to space B" tool independent of some
underlying LUT. An identity LUT stage with a retarget declaration is
exactly equivalent to what "Retarget" used to do standalone, since
wrapping an identity object degenerates to nothing extra -- so nothing is
lost, it just now composes with everything else in the stack instead of
being its own dead-end panel.

STACK FORKING -- fork_split/fork_merge are a matched PAIR of stage kinds,
the one exception to "every stage sees only the running signal after
everything before it" (per Tom, deliberately modeled as two separate
operator rows rather than one composite stage, so each side of a fork can
hold any number of ordinary stages, not just one apiece):

  - fork_split marks where the signal divides into two independent
    parallel branches. It has no transform of its own -- no meaningful
    opacity (see make_fork_split_stage) -- `enabled=False` on the split
    is the one exception, meaning "the whole region behaves as if it
    weren't here at all" (both branches AND the merge are skipped).
  - Every ordinary stage between a fork_split and its matching fork_merge
    is tagged via Stage.branch = "a" or "b" to say which branch it
    belongs to. Each branch is evaluated exactly like an ordinary
    sub-sequence of Stack stages would be -- any kind, any number of
    them, each with its own independent enabled/opacity -- just against
    that branch's own running signal instead of the shared one. A
    branch-tagged stage's kind can be anything in STAGE_KINDS except
    fork_split/fork_merge themselves (no nested forks yet, see
    _run_stages).
  - fork_merge is where the two branches rejoin (per Tom: "the inverse
    blend function goes on the merge operator"): its own `blend` param
    linearly crossfades the two branches' final results (0.0 = all
    branch "a", 1.0 = all branch "b" -- see make_fork_merge_stage), and
    its own opacity/enabled then blends THAT combined result against the
    signal exactly as it stood right before the matching fork_split --
    the same "total opacity control like every other operator row"
    (per Tom) every other stage's opacity already provides, just
    understood as applying to the whole split/merge region collapsing
    back to one number, not to a single transform.

The stage immediately after a fork_merge sees only that single rejoined
result, same as if forking never happened -- forking is entirely internal
to the region between one matched split/merge pair. Motivating use case:
blend two LMTs (or two whole multi-stage grades) against each other rather
than only ever being able to chain operations serially.

Structurally, fork_split/fork_merge/branch-tagged stages are all perfectly
ordinary entries in a Stack's own flat `stages` list -- NOT nested inside
some other stage's params -- so reordering, Stack.copy() (and therefore
undo/redo), and session_io.py's existing per-stage serialization all keep
working completely unchanged, with zero fork-specific special-casing
needed anywhere outside _run_stages itself (see that function's own
docstring for exactly how it tracks and validates an open fork region as
it walks the stage list, and what a "stray" branch tag outside any open
region means -- spoiler: nothing, it's just ignored).

MATTE CHANNELS -- deliberately OPT-IN (per Tom: "instead of the way
Baselight does it where usage is mandatory if it's in the stack"), unlike
STACK FORKING above this involves no new stage-PAIR structure at all: a
"keyer" stage (keyer.py's own OKLCh qualifier -- the first matte-
PRODUCING kind; "segment_mask" and "matte_combine" followed the same
shape, see MATTE_PRODUCING_KINDS) computes an (H,W) matte and never
touches the running RGB image itself (_apply_stage_to_array's own
"keyer" branch is a pure identity passthrough); ANY other stage can
optionally reference that matte by setting
`stage.params["matte_source_id"]` to the producing stage's own
`Stage.id` (the same id field stages already carry for
selection/reorder tracking), which gates that stage's per-stage opacity
PER PIXEL by the matte's own value instead of the usual flat scalar --
see _stage_blend_factor. `matte_source_id` absent/None, or resolving to
nothing (source removed, or sits at/after this stage's own position, so
it hasn't produced anything yet by the time this stage runs) -- all fall
back to today's exact plain-scalar-opacity behavior, no error, no special
"no matte" sentinel needed.

Letters (A, B, C... then, past 26, AA, BB, CC... per Tom's own doubling
scheme, not spreadsheet-style AB/AC) are NEVER stored anywhere -- see
matte_letters_for -- they're computed fresh, purely for UI display, by
scanning `stages` in order each time a combo box needs to show them. This
is deliberate: reordering the stack only changes which letter a keyer
DISPLAYS as, never invalidates a `matte_source_id` reference (which
points at a stable Stage.id, not a letter), and a stage_panel.py combo
that only offers keyers ABOVE the consuming stage's own position gets
"can't reference a matte that doesn't exist yet" for free, with zero
validation code needed anywhere in this module.

_run_stages accumulates a `mattes: dict[stage_id, np.ndarray]` as it
walks the stage list (see that function's own docstring) -- ONE flat dict
shared across fork branches too (a matte computed on branch "a" is
visible to branch "b" or to stages after the merge, since it's plain
Python bookkeeping keyed by stage id, not per-branch image state; the one
accepted nuance is that the qualifier only ever sampled whichever
branch's own image actually produced it).

GPU note: every MATTE_PRODUCING_KINDS kind is deliberately left out of
gpu_stack_renderer.GPU_ELIGIBLE_KINDS, so any stack containing an enabled
matte-producing stage already falls back to this module's own CPU path
in full (the same all-or-nothing rule that module already applies to
every other GPU-ineligible kind/condition) -- matte gating therefore
never needs (and doesn't have) a GPU implementation to stay correct.

Same rule as every other backend module in this app: no PySide6 import
here, fully testable without a display.
"""
import itertools

import numpy as np
import colour

from retarget import retarget_io
from printer_lights import apply_printer_lights_in_working_space
import aces_drt
import cdl
import color_warper
import colorspace_convert
import custom_drt
import exposure_contrast
import grading_primary
import grading_rgb_curve
import grading_tone
import keyer
import matte_combine
import matte_shape
import segmentation

STAGE_KINDS = ("lut", "printer_lights", "aces_drt", "custom_drt", "cdl", "colorspace",
               "grading_primary", "exposure_contrast", "grading_tone",
               "grading_rgb_curve", "color_warper", "keyer", "segment_mask", "matte_combine",
               "matte_shape", "fork_split", "fork_merge")

# Every stage kind that PRODUCES a matte rather than grading the image --
# see this module's docstring, "MATTE CHANNELS", matte_letters_for, and
# _maybe_populate_matte. Every one of these currently identity-
# passthroughs its own RGB output entirely (see _apply_stage_to_array)
# and is deliberately left out of gpu_stack_renderer.GPU_ELIGIBLE_KINDS,
# so any stack using any of them already falls back to this module's own
# CPU path in full via that module's existing all-or-nothing eligibility
# rule -- no GPU
# work needed for any of them to stay correct. "matte_combine" is the
# odd one out: it never reads the image's own RGB at all, only OTHER
# already-computed mattes (see matte_combine.py's own docstring).
MATTE_PRODUCING_KINDS = frozenset({"keyer", "segment_mask", "matte_combine", "matte_shape"})

_id_counter = itertools.count(1)


def next_stage_id():
    """Monotonically increasing int, unique for the life of the process --
    used as a stage's identity for GUI selection/reorder tracking, since
    list index alone breaks the moment stages get reordered or removed."""
    return next(_id_counter)


class Stage:
    """One entry in a Stack. `kind` is one of STAGE_KINDS; `params` holds
    whatever that kind's apply function needs (see _apply_stage_to_array).
    `enabled=False` makes a stage an exact no-op without removing it or
    losing its params -- same "toggle without losing your settings" idea
    as a mute button on a mixer channel. `opacity` in [0,1] is the same
    "LUT strength" linear crossfade concatenate_luts_with_opacity already
    established (this stage's fully-applied output blended against this
    stage's own input, not some other blend mode) -- UNLESS this stage's
    own params hold a `"matte_source_id"` referencing a "keyer" stage
    elsewhere in the same Stack, in which case that scalar becomes a
    per-pixel multiplier instead (see this module's docstring, "MATTE
    CHANNELS", and _stage_blend_factor) -- a plain params key, not a
    first-class field here, since gating-by-matte is an opt-in property
    of what a stage's own params say, not a universal concept every
    stage kind needs a dedicated slot for. `branch` is None for
    an ordinary stage, or "a"/"b" for a stage meant to sit between a
    fork_split/fork_merge pair (see this module's docstring, "STACK
    FORKING") -- inert (silently ignored, see _run_stages) anywhere a
    stage with a branch tag isn't actually inside an open fork region,
    e.g. right after being dragged out of one in the UI."""

    def __init__(self, kind, label=None, params=None, enabled=True, opacity=1.0,
                 stage_id=None, branch=None):
        if kind not in STAGE_KINDS:
            raise ValueError(f"Unknown stage kind: {kind!r} (expected one of {STAGE_KINDS})")
        if branch not in (None, "a", "b"):
            raise ValueError(f"branch must be None, 'a', or 'b', not {branch!r}")
        self.id = stage_id if stage_id is not None else next_stage_id()
        self.kind = kind
        self.label = label or kind
        self.params = dict(params or {})
        self.enabled = enabled
        self.opacity = float(opacity)
        self.branch = branch

    def copy(self):
        return Stage(self.kind, self.label, dict(self.params), self.enabled,
                     self.opacity, stage_id=self.id, branch=self.branch)

    def __repr__(self):
        branch_bit = f", branch={self.branch!r}" if self.branch else ""
        return (f"Stage(id={self.id}, kind={self.kind!r}, label={self.label!r}, "
                f"enabled={self.enabled}, opacity={self.opacity}{branch_bit})")


class Stack:
    """An ordered list of Stages plus the two former "LUT Controls"
    settings that turned out to be global bake properties rather than
    stages (see module docstring): `size` (grid resolution to bake at) and
    `domain_min`/`domain_max` (metadata written onto the final LUT3D,
    matching app_logic.set_lut_domain's always-metadata-only behavior --
    does not affect the computed table)."""

    def __init__(self, stages=None, size=33, domain_min=(0.0, 0.0, 0.0),
                 domain_max=(1.0, 1.0, 1.0), name="Stack"):
        self.stages = list(stages) if stages is not None else []
        self.size = size
        self.domain_min = tuple(domain_min)
        self.domain_max = tuple(domain_max)
        self.name = name

    # ---- editing -----------------------------------------------------

    def add_stage(self, stage, index=None):
        """Insert `stage` at `index` (default: append at the end)."""
        if index is None:
            self.stages.append(stage)
        else:
            self.stages.insert(index, stage)
        return stage

    def remove_stage(self, stage_id):
        before = len(self.stages)
        self.stages = [s for s in self.stages if s.id != stage_id]
        if len(self.stages) == before:
            raise KeyError(f"No stage with id={stage_id}")

    def find_stage(self, stage_id):
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise KeyError(f"No stage with id={stage_id}")

    def move_stage(self, stage_id, new_index):
        """Reorder: pull the stage with `stage_id` out and reinsert it at
        `new_index` in the remaining list -- e.g. move_stage(id, 0) makes
        it first regardless of where it was."""
        idx = next((i for i, s in enumerate(self.stages) if s.id == stage_id), None)
        if idx is None:
            raise KeyError(f"No stage with id={stage_id}")
        stage = self.stages.pop(idx)
        new_index = max(0, min(new_index, len(self.stages)))
        self.stages.insert(new_index, stage)

    def move_stage_up(self, stage_id):
        idx = next((i for i, s in enumerate(self.stages) if s.id == stage_id), None)
        if idx is None:
            raise KeyError(f"No stage with id={stage_id}")
        if idx > 0:
            self.move_stage(stage_id, idx - 1)

    def move_stage_down(self, stage_id):
        idx = next((i for i, s in enumerate(self.stages) if s.id == stage_id), None)
        if idx is None:
            raise KeyError(f"No stage with id={stage_id}")
        if idx < len(self.stages) - 1:
            self.move_stage(stage_id, idx + 1)

    def copy(self):
        new = Stack(
            stages=[s.copy() for s in self.stages],
            size=self.size, domain_min=self.domain_min, domain_max=self.domain_max,
            name=self.name,
        )
        return new


# ---- stage constructors (kept out of Stage.__init__ so callers get a
# clear, kind-specific signature instead of a bag of untyped params) ------

def make_lut_stage(lut, label=None, retarget_from=None, retarget_to=None,
                    enabled=True, opacity=1.0):
    """A stage that multiplies the running stack by `lut` (any object with
    .apply(RGB) -- a loaded file, a create_identity_lut() pass-through, or
    a previously-baked LUT3D). If `retarget_from`/`retarget_to` are both
    given, `lut` is wrapped with retarget_io first (see this module's
    docstring on why Retarget is a per-lut-stage option, not its own stage
    kind) -- both must be given together or not at all."""
    if (retarget_from is None) != (retarget_to is None):
        raise ValueError("retarget_from and retarget_to must be given together")
    params = {"lut": lut, "retarget_from": retarget_from, "retarget_to": retarget_to}
    default_label = getattr(lut, "name", None) or "LUT"
    if retarget_from is not None and retarget_to is not None:
        default_label += f" (retarget {retarget_from} -> {retarget_to})"
    return Stage("lut", label or default_label, params, enabled, opacity)


def make_printer_lights_stage(r_points=0.0, g_points=0.0, b_points=0.0,
                               working_colourspace="ACEScct",
                               label=None, enabled=True, opacity=1.0):
    """A stage that applies a printer-lights R/G/B point trim, same math
    as printer_lights.apply_printer_lights_in_working_space (which itself
    prefers the OCIO registry when available -- see that module)."""
    params = {
        "r_points": r_points, "g_points": g_points, "b_points": b_points,
        "working_colourspace": working_colourspace,
    }
    return Stage("printer_lights", label or "Printer Lights", params, enabled, opacity)


def make_aces_drt_stage(working_colourspace, display, view,
                         config_name=None, label=None, enabled=True, opacity=1.0):
    """A stage that applies a real ACES Output Transform (RRT+ODT / ACES
    2.0's unified transform), same as aces_drt.apply_aces_output_transform."""
    config_name = config_name or aces_drt.DEFAULT_CONFIG_NAME
    params = {
        "working_colourspace": working_colourspace, "display": display,
        "view": view, "config_name": config_name,
    }
    default_label = f"ACES DRT ({working_colourspace} -> {display}/{view})"
    return Stage("aces_drt", label or default_label, params, enabled, opacity)


def make_custom_drt_stage(working_colourspace,
                           contrast=custom_drt.DEFAULT_CONTRAST,
                           headroom=custom_drt.DEFAULT_HEADROOM,
                           blend_percentile=custom_drt.DEFAULT_BLEND_PERCENTILE,
                           tail_width=custom_drt.DEFAULT_TAIL_WIDTH,
                           desat_start=custom_drt.DEFAULT_DESAT_START,
                           desat_end=custom_drt.DEFAULT_DESAT_END,
                           identity_zone=custom_drt.DEFAULT_IDENTITY_ZONE,
                           transition_softness=custom_drt.DEFAULT_TRANSITION_SOFTNESS,
                           fullness_r=custom_drt.DEFAULT_FULLNESS_R,
                           fullness_g=custom_drt.DEFAULT_FULLNESS_G,
                           fullness_b=custom_drt.DEFAULT_FULLNESS_B,
                           mastering_display=custom_drt.DEFAULT_MASTERING_DISPLAY,
                           final_target=None,
                           mastering_brightness=custom_drt.DEFAULT_MASTERING_BRIGHTNESS,
                           config_name=None, label=None, enabled=True, opacity=1.0):
    """A stage that applies LUTier's own DRT (Rec.2020 rendering space,
    hyperbolic+sigmoid-tail tonescale, highlight desaturation, radial
    gamut compression to the chosen mastering display's own gamut, then a
    direct Final Target conversion + EOTF encode), same math as
    custom_drt.apply_custom_drt -- see that module's docstring for the
    full pipeline and why every default here is a deliberately validated
    value, not an arbitrary starting point."""
    config_name = config_name or custom_drt.DEFAULT_CONFIG_NAME
    if final_target is None:
        final_target = custom_drt.DEFAULT_FINAL_TARGET_FOR_DISPLAY[mastering_display]
    params = {
        "working_colourspace": working_colourspace, "contrast": float(contrast),
        "headroom": float(headroom), "blend_percentile": float(blend_percentile),
        "tail_width": float(tail_width), "desat_start": float(desat_start),
        "desat_end": float(desat_end), "identity_zone": float(identity_zone),
        "transition_softness": float(transition_softness),
        "fullness_r": float(fullness_r), "fullness_g": float(fullness_g),
        "fullness_b": float(fullness_b), "mastering_display": mastering_display,
        "final_target": final_target,
        "mastering_brightness": int(mastering_brightness), "config_name": config_name,
    }
    default_label = f"CAMier DRT ({working_colourspace} -> {mastering_display}/{final_target})"
    return Stage("custom_drt", label or default_label, params, enabled, opacity)


def make_cdl_stage(slope=cdl.IDENTITY_SLOPE, offset=cdl.IDENTITY_OFFSET,
                    power=cdl.IDENTITY_POWER, sat=cdl.IDENTITY_SAT,
                    style=cdl.DEFAULT_STYLE, sop_enabled=True, sat_enabled=True,
                    label=None, enabled=True, opacity=1.0):
    """A stage that applies an ASC CDL SOP+SAT grade, same math as
    cdl.apply_cdl (see that module's docstring for the independent
    sop_enabled/sat_enabled bypass toggles and the asc/no_clip style
    choice)."""
    params = {
        "slope": tuple(slope), "offset": tuple(offset), "power": tuple(power),
        "sat": float(sat), "style": style,
        "sop_enabled": sop_enabled, "sat_enabled": sat_enabled,
    }
    return Stage("cdl", label or "ASC CDL", params, enabled, opacity)


def make_grading_primary_stage(style=grading_primary.DEFAULT_STYLE,
                                brightness=grading_primary.IDENTITY_ADD,
                                contrast=grading_primary.IDENTITY_MULT,
                                gamma=grading_primary.IDENTITY_MULT,
                                offset=grading_primary.IDENTITY_ADD,
                                exposure=grading_primary.IDENTITY_ADD,
                                lift=grading_primary.IDENTITY_ADD,
                                gain=grading_primary.IDENTITY_MULT,
                                pivot=grading_primary.DEFAULT_PIVOT,
                                saturation=grading_primary.DEFAULT_SATURATION,
                                label=None, enabled=True, opacity=1.0):
    """A stage that applies an OCIO GradingPrimary grade, same math as
    grading_primary.apply_grading_primary -- see that module's docstring
    for the log/lin/video style choice and why every field is stored
    regardless of which style is active (switching styles later doesn't
    lose whatever was already dialed in on the other fields)."""
    params = {
        "style": style, "brightness": tuple(brightness), "contrast": tuple(contrast),
        "gamma": tuple(gamma), "offset": tuple(offset), "exposure": tuple(exposure),
        "lift": tuple(lift), "gain": tuple(gain), "pivot": float(pivot),
        "saturation": float(saturation),
    }
    return Stage("grading_primary", label or "Grading Primary", params, enabled, opacity)


def make_exposure_contrast_stage(style=exposure_contrast.DEFAULT_STYLE,
                                  exposure=exposure_contrast.IDENTITY_EXPOSURE,
                                  contrast=exposure_contrast.IDENTITY_CONTRAST,
                                  gamma=exposure_contrast.IDENTITY_GAMMA,
                                  label=None, enabled=True, opacity=1.0):
    """A stage that applies an OCIO Exposure/Contrast grade, same math as
    exposure_contrast.apply_exposure_contrast -- see that module's
    docstring for the linear/log/video style choice and why pivot is
    never a stage param (not a dynamic GPU property, would force a
    shader recompile on every edit)."""
    params = {
        "style": style, "exposure": float(exposure), "contrast": float(contrast),
        "gamma": float(gamma),
    }
    return Stage("exposure_contrast", label or "Exposure/Contrast", params, enabled, opacity)


def make_grading_tone_stage(style=grading_tone.DEFAULT_STYLE,
                             blacks=None, shadows=None, midtones=None,
                             highlights=None, whites=None,
                             scontrast=grading_tone.DEFAULT_SCONTRAST,
                             label=None, enabled=True, opacity=1.0):
    """A stage that applies an OCIO GradingTone grade, same math as
    grading_tone.apply_grading_tone -- see that module's docstring for
    the 5 tone ranges' own defaults (each `None` range argument here
    becomes that range's own OCIO default, not a shared/generic one)."""
    def _range_or_default(value, key):
        return tuple(value) if value is not None else grading_tone.DEFAULT_RANGE_VALUES[key]
    params = {
        "style": style,
        "blacks": _range_or_default(blacks, "blacks"),
        "shadows": _range_or_default(shadows, "shadows"),
        "midtones": _range_or_default(midtones, "midtones"),
        "highlights": _range_or_default(highlights, "highlights"),
        "whites": _range_or_default(whites, "whites"),
        "scontrast": float(scontrast),
    }
    return Stage("grading_tone", label or "Grading Tone", params, enabled, opacity)


def make_grading_rgb_curve_stage(style=grading_rgb_curve.DEFAULT_STYLE,
                                  red=None, green=None, blue=None, master=None,
                                  label=None, enabled=True, opacity=1.0):
    """A stage that applies an OCIO GradingRGBCurve grade, same math as
    grading_rgb_curve.apply_grading_rgb_curve -- see that module's
    docstring for the log/lin/video style choice and the control-point
    shape (each of red/green/blue/master defaults to the identity curve,
    DEFAULT_CONTROL_POINTS, when None)."""
    def _points_or_default(points):
        return tuple(tuple(p) for p in points) if points is not None else grading_rgb_curve.DEFAULT_CONTROL_POINTS
    params = {
        "style": style,
        "red": _points_or_default(red), "green": _points_or_default(green),
        "blue": _points_or_default(blue), "master": _points_or_default(master),
    }
    return Stage("grading_rgb_curve", label or "Grading RGB Curve", params, enabled, opacity)


def make_color_warper_stage(working_colourspace=color_warper.DEFAULT_WORKING_COLOURSPACE,
                             points=None, wake=color_warper.DEFAULT_WAKE,
                             dropoff=color_warper.DEFAULT_DROPOFF,
                             chroma_wake=color_warper.DEFAULT_CHROMA_WAKE,
                             chroma_dropoff=color_warper.DEFAULT_CHROMA_DROPOFF,
                             label=None, enabled=True, opacity=1.0):
    """A stage that applies WARPier's perceptually-uniform (OKLCh)
    hue/chroma warp, same math as
    color_warper.apply_color_warper_in_working_space -- see that module's
    docstring for the control-point shape and the wake/dropoff falloff.
    `chroma_wake`/`chroma_dropoff` are the independent CHROMA-axis
    falloff (per Tom: a point's own reach should depend on both hue AND
    chroma distance, not hue alone) -- their defaults are wide-open
    no-ops (see color_warper.DEFAULT_CHROMA_WAKE's own comment), so an
    existing session loaded without these keys behaves identically to
    before this axis existed. `points` defaults to an empty list (no
    seeded anchors, per Tom), so a freshly-added stage is an exact no-op
    until the user actually adds a point (points_are_identity is
    vacuously true for an empty list)."""
    resolved_points = [dict(p) for p in points] if points is not None else []
    params = {
        "working_colourspace": working_colourspace,
        "points": resolved_points,
        "wake": float(wake), "dropoff": float(dropoff),
        "chroma_wake": float(chroma_wake), "chroma_dropoff": float(chroma_dropoff),
    }
    return Stage("color_warper", label or "WARPier", params, enabled, opacity)


def make_keyer_stage(working_colourspace=keyer.DEFAULT_WORKING_COLOURSPACE,
                      hue_center=keyer.DEFAULT_HUE_CENTER, hue_width=keyer.DEFAULT_HUE_WIDTH,
                      chroma_min=keyer.DEFAULT_CHROMA_MIN, chroma_max=keyer.DEFAULT_CHROMA_MAX,
                      lightness_min=keyer.DEFAULT_LIGHTNESS_MIN, lightness_max=keyer.DEFAULT_LIGHTNESS_MAX,
                      softness=keyer.DEFAULT_SOFTNESS, invert=keyer.DEFAULT_INVERT,
                      label=None, enabled=True, opacity=1.0):
    """A stage that produces a MATTE (see this module's docstring, "MATTE
    CHANNELS") via keyer.compute_matte's OKLCh qualifier -- this stage's
    own RGB output is always an exact identity passthrough (see
    _apply_stage_to_array's "keyer" branch), so `opacity` here doesn't
    blend an image the way it does for every other stage kind; it's
    repurposed as "matte amount" -- the computed matte is scaled by it
    before being offered to any downstream consumer (see _run_stages),
    the same free, already-understood control every other row already
    has rather than a redundant dedicated slider."""
    params = {
        "working_colourspace": working_colourspace,
        "hue_center": float(hue_center), "hue_width": float(hue_width),
        "chroma_min": float(chroma_min), "chroma_max": float(chroma_max),
        "lightness_min": float(lightness_min), "lightness_max": float(lightness_max),
        "softness": float(softness), "invert": bool(invert),
    }
    return Stage("keyer", label or "Keyer", params, enabled, opacity)


def make_segment_mask_stage(points=None, tolerance=segmentation.DEFAULT_TOLERANCE,
                             feather=segmentation.DEFAULT_FEATHER,
                             invert=segmentation.DEFAULT_INVERT,
                             label=None, enabled=True, opacity=1.0):
    """A stage that produces a MATTE (see this module's docstring, "MATTE
    CHANNELS") via segmentation.compute_mask_from_points' seeded region-
    growing -- see that module's own docstring for why this is classical-
    CV GROUNDWORK (per Tom) rather than a real Segment-Anything model
    yet. Like "keyer", this stage's own RGB output is always an exact
    identity passthrough (see _apply_stage_to_array's "segment_mask"
    branch), so `opacity` is repurposed as "matte amount" the same way
    make_keyer_stage's own docstring explains.

    `points` defaults to an empty list (no seeded point -- same "opt-in,
    no-op until the user actually does something" convention
    make_color_warper_stage's own empty points list establishes),
    populated by clicking the Viewer once that interaction is actually
    wired up (see wipe_compare_widget.widget_pos_to_image_fraction --
    added as groundwork for exactly this, not yet connected to any
    mouse handler, and this stage kind is deliberately not yet exposed
    in stack_panel.py's own Add-stage menu either, for the same reason:
    a stage with no way to populate its own points from the UI yet
    would just be a confusing dead end to discover there)."""
    resolved_points = [dict(p) for p in points] if points is not None else []
    params = {
        "points": resolved_points,
        "tolerance": float(tolerance), "feather": float(feather), "invert": bool(invert),
    }
    return Stage("segment_mask", label or "Segment Mask", params, enabled, opacity)


def make_matte_combine_stage(matte_a_source_id=None, matte_b_source_id=None,
                              op=matte_combine.DEFAULT_OP,
                              label=None, enabled=True, opacity=1.0):
    """A stage that produces a MATTE (see this module's docstring, "MATTE
    CHANNELS") by combining TWO already-computed upstream mattes -- see
    matte_combine.py's own docstring for the op vocabulary and why this
    kind reads no image RGB at all, only other mattes via `mattes[...]`
    entries `_run_stages` has already accumulated by this stage's own
    position (same "unresolved -> gracefully treated as empty" fallback
    every other `matte_source_id`-style reference in this app already
    uses -- see _compute_raw_matte's own "matte_combine" branch). Like
    "keyer"/"segment_mask", this stage's own RGB output is always an
    exact identity passthrough (see _apply_stage_to_array's "matte_combine"
    branch), so `opacity` is repurposed as "matte amount" the same way
    make_keyer_stage's own docstring explains.

    `matte_a_source_id`/`matte_b_source_id` default to None (no seeded
    reference -- same "opt-in, no-op until the user actually does
    something" convention every other matte reference in this app
    starts at); a freshly-added stage with both unresolved combines two
    empty mattes, which for every op except "union" (0 OR 0 -> still 0)
    is itself an all-zero (empty) matte -- never an error."""
    params = {
        "matte_a_source_id": matte_a_source_id, "matte_b_source_id": matte_b_source_id,
        "op": op,
    }
    return Stage("matte_combine", label or "Matte Combine", params, enabled, opacity)


def make_matte_shape_stage(shape=matte_shape.DEFAULT_SHAPE,
                            center_x=matte_shape.DEFAULT_CENTER_X, center_y=matte_shape.DEFAULT_CENTER_Y,
                            width=matte_shape.DEFAULT_WIDTH, height=matte_shape.DEFAULT_HEIGHT,
                            rotation=matte_shape.DEFAULT_ROTATION, softness=matte_shape.DEFAULT_SOFTNESS,
                            invert=matte_shape.DEFAULT_INVERT,
                            label=None, enabled=True, opacity=1.0):
    """A stage that produces a MATTE (see this module's docstring, "MATTE
    CHANNELS") -- a classic "Power Window" ellipse/rectangle garbage
    matte, same math as matte_shape.compute_shape_matte. Reads no image
    RGB at all, only the array's own (H, W) (see _compute_raw_matte's own
    "matte_shape" branch). Like every other matte-producing kind, this
    stage's own RGB output is always an exact identity passthrough (see
    _apply_stage_to_array's "matte_shape" branch), so `opacity` is
    repurposed as "matte amount" the same way make_keyer_stage's own
    docstring explains.

    Unlike keyer/matte_combine, a freshly-added shape does NOT start as
    a no-op -- there's no "no points yet" state a shape can occupy (see
    matte_shape.DEFAULT_WIDTH's own comment), so every param here
    defaults to a concrete, immediately-visible ellipse centered in
    frame."""
    params = {
        "shape": shape, "center_x": float(center_x), "center_y": float(center_y),
        "width": float(width), "height": float(height), "rotation": float(rotation),
        "softness": float(softness), "invert": bool(invert),
    }
    return Stage("matte_shape", label or "Matte Shape", params, enabled, opacity)


def make_colorspace_stage(src, dst, config_name=None, label=None, enabled=True, opacity=1.0):
    """A stage that applies a plain (non-DRT) color space conversion, same
    math as colorspace_convert.apply_colorspace_conversion -- see that
    module's docstring for why this is deliberately NOT the same thing as
    an aces_drt stage (no rendering step, just a direct src->dst
    conversion via that config's own ColorSpaceTransform)."""
    config_name = config_name or colorspace_convert.DEFAULT_CONFIG_NAME
    params = {"src": src, "dst": dst, "config_name": config_name}
    default_label = f"Color Space ({src} -> {dst})"
    return Stage("colorspace", label or default_label, params, enabled, opacity)


def make_fork_split_stage(label=None, enabled=True):
    """Marks where the signal divides into two parallel branches -- see
    this module's docstring, "STACK FORKING". Every stage between this
    and its matching make_fork_merge_stage should have its own `branch`
    set to "a" or "b" (e.g. `stage.branch = "a"`) to say which side it
    belongs to. `enabled=False` treats the WHOLE region -- both branches
    and the merge -- as if it weren't there at all (see _run_stages). No
    `opacity` param here -- a split point has no output of its own to
    blend against anything; only the matching merge does."""
    return Stage("fork_split", label or "Fork Split", enabled=enabled)


def make_fork_merge_stage(blend=0.5, label=None, enabled=True, opacity=1.0):
    """Rejoins the two branches opened by the nearest preceding
    make_fork_split_stage back into one signal -- see this module's
    docstring, "STACK FORKING". `blend` linearly crossfades the two
    branches' own final results (0.0 = all branch "a", 1.0 = all branch
    "b", 0.5 = an even mix) -- deliberately one float, not two independent
    weights that could drift out of summing to 1.0: the "inverse
    relationship" a two-slider UI wants (moving one control moves the
    other the opposite way) is just two different views onto this one
    number (a's percentage = 1-blend, b's = blend), not two separate
    pieces of state to keep in sync. This stage's own `opacity` (per Tom:
    "a total opacity control like every other operator row") blends that
    combined result against the signal exactly as it stood right before
    the matching split -- the same "fully-applied output blended against
    this stage's own input" rule every other stage's opacity already
    follows."""
    return Stage("fork_merge", label or "Fork Merge",
                 {"blend": float(blend)}, enabled, opacity)


# ---- evaluation ----------------------------------------------------------

def _apply_stage_to_array(stage, array):
    """Pure function: this stage's kind + params, applied to `array` --
    the one place that dispatches on stage.kind, reused identically by
    both bake_stack_to_lut3d (grid-based) and apply_stack_to_image_array
    (direct-pixel), so the two evaluation paths can never disagree about
    what a given stage actually does. fork_split/fork_merge (see this
    module's docstring, "STACK FORKING") are NEVER dispatched here --
    _run_stages intercepts and handles both structurally before a stage
    of either kind would ever reach this function."""
    if stage.kind == "lut":
        lut = stage.params["lut"]
        retarget_from = stage.params.get("retarget_from")
        retarget_to = stage.params.get("retarget_to")
        if retarget_from is not None and retarget_to is not None:
            wrapped = retarget_io(lut, from_space=retarget_from, to_space=retarget_to)
            return np.asarray(wrapped(array), dtype=np.float64)
        return np.asarray(lut.apply(array), dtype=np.float64)

    if stage.kind == "printer_lights":
        p = stage.params
        return apply_printer_lights_in_working_space(
            array, p["working_colourspace"],
            p.get("r_points", 0.0), p.get("g_points", 0.0), p.get("b_points", 0.0),
        )

    if stage.kind == "aces_drt":
        p = stage.params
        return aces_drt.apply_aces_output_transform(
            array, p["working_colourspace"], p["display"], p["view"],
            config_name=p.get("config_name", aces_drt.DEFAULT_CONFIG_NAME),
        )

    if stage.kind == "custom_drt":
        p = stage.params
        return custom_drt.apply_custom_drt(
            array, p["working_colourspace"],
            contrast=p.get("contrast", custom_drt.DEFAULT_CONTRAST),
            headroom=p.get("headroom", custom_drt.DEFAULT_HEADROOM),
            blend_percentile=p.get("blend_percentile", custom_drt.DEFAULT_BLEND_PERCENTILE),
            tail_width=p.get("tail_width", custom_drt.DEFAULT_TAIL_WIDTH),
            desat_start=p.get("desat_start", custom_drt.DEFAULT_DESAT_START),
            desat_end=p.get("desat_end", custom_drt.DEFAULT_DESAT_END),
            identity_zone=p.get("identity_zone", custom_drt.DEFAULT_IDENTITY_ZONE),
            transition_softness=p.get("transition_softness", custom_drt.DEFAULT_TRANSITION_SOFTNESS),
            fullness_r=p.get("fullness_r", custom_drt.DEFAULT_FULLNESS_R),
            fullness_g=p.get("fullness_g", custom_drt.DEFAULT_FULLNESS_G),
            fullness_b=p.get("fullness_b", custom_drt.DEFAULT_FULLNESS_B),
            mastering_display=p.get("mastering_display", custom_drt.DEFAULT_MASTERING_DISPLAY),
            final_target=p.get("final_target"),
            mastering_brightness=p.get(
                "mastering_brightness", custom_drt.DEFAULT_MASTERING_BRIGHTNESS),
            config_name=p.get("config_name", custom_drt.DEFAULT_CONFIG_NAME),
        )

    if stage.kind == "cdl":
        p = stage.params
        return cdl.apply_cdl(
            array,
            slope=p.get("slope", cdl.IDENTITY_SLOPE),
            offset=p.get("offset", cdl.IDENTITY_OFFSET),
            power=p.get("power", cdl.IDENTITY_POWER),
            sat=p.get("sat", cdl.IDENTITY_SAT),
            style=p.get("style", cdl.DEFAULT_STYLE),
            sop_enabled=p.get("sop_enabled", True),
            sat_enabled=p.get("sat_enabled", True),
        )

    if stage.kind == "colorspace":
        p = stage.params
        return colorspace_convert.apply_colorspace_conversion(
            array, p["src"], p["dst"],
            config_name=p.get("config_name", colorspace_convert.DEFAULT_CONFIG_NAME),
        )

    if stage.kind == "grading_primary":
        p = stage.params
        return grading_primary.apply_grading_primary(
            array,
            style=p.get("style", grading_primary.DEFAULT_STYLE),
            brightness=p.get("brightness", grading_primary.IDENTITY_ADD),
            contrast=p.get("contrast", grading_primary.IDENTITY_MULT),
            gamma=p.get("gamma", grading_primary.IDENTITY_MULT),
            offset=p.get("offset", grading_primary.IDENTITY_ADD),
            exposure=p.get("exposure", grading_primary.IDENTITY_ADD),
            lift=p.get("lift", grading_primary.IDENTITY_ADD),
            gain=p.get("gain", grading_primary.IDENTITY_MULT),
            pivot=p.get("pivot", grading_primary.DEFAULT_PIVOT),
            saturation=p.get("saturation", grading_primary.DEFAULT_SATURATION),
        )

    if stage.kind == "exposure_contrast":
        p = stage.params
        return exposure_contrast.apply_exposure_contrast(
            array,
            style=p.get("style", exposure_contrast.DEFAULT_STYLE),
            exposure=p.get("exposure", exposure_contrast.IDENTITY_EXPOSURE),
            contrast=p.get("contrast", exposure_contrast.IDENTITY_CONTRAST),
            gamma=p.get("gamma", exposure_contrast.IDENTITY_GAMMA),
        )

    if stage.kind == "grading_tone":
        p = stage.params
        return grading_tone.apply_grading_tone(
            array,
            style=p.get("style", grading_tone.DEFAULT_STYLE),
            blacks=p.get("blacks"), shadows=p.get("shadows"), midtones=p.get("midtones"),
            highlights=p.get("highlights"), whites=p.get("whites"),
            scontrast=p.get("scontrast", grading_tone.DEFAULT_SCONTRAST),
        )

    if stage.kind == "grading_rgb_curve":
        p = stage.params
        return grading_rgb_curve.apply_grading_rgb_curve(
            array,
            style=p.get("style", grading_rgb_curve.DEFAULT_STYLE),
            red=p.get("red"), green=p.get("green"), blue=p.get("blue"), master=p.get("master"),
        )

    if stage.kind == "color_warper":
        p = stage.params
        return color_warper.apply_color_warper_in_working_space(
            array, p["working_colourspace"], p.get("points", []),
            wake=p.get("wake", color_warper.DEFAULT_WAKE),
            dropoff=p.get("dropoff", color_warper.DEFAULT_DROPOFF),
            chroma_wake=p.get("chroma_wake", color_warper.DEFAULT_CHROMA_WAKE),
            chroma_dropoff=p.get("chroma_dropoff", color_warper.DEFAULT_CHROMA_DROPOFF),
        )

    if stage.kind == "keyer":
        # Never grades the image itself -- see this module's docstring,
        # "MATTE CHANNELS", and make_keyer_stage. _run_stages computes
        # this stage's actual matte separately, from the SAME `array`
        # this identity passthrough is handed.
        return array

    if stage.kind == "segment_mask":
        # Same identity-passthrough contract as "keyer" just above --
        # see make_segment_mask_stage.
        return array

    if stage.kind == "matte_combine":
        # Same identity-passthrough contract as "keyer"/"segment_mask" --
        # see make_matte_combine_stage. Never reads `array`'s own values
        # at all (its own matte comes from OTHER mattes, see
        # _compute_raw_matte), but still hands the image straight through
        # unchanged, same as every other matte-producing kind.
        return array

    if stage.kind == "matte_shape":
        # Same identity-passthrough contract as every other matte-
        # producing kind -- see make_matte_shape_stage. Never reads
        # `array`'s own values at all (pure geometry, see
        # _compute_raw_matte), only hands the image straight through.
        return array

    if stage.kind in ("fork_split", "fork_merge"):  # pragma: no cover -- see docstring above
        raise AssertionError(
            f"{stage.kind} stages are handled structurally by _run_stages "
            "and should never reach _apply_stage_to_array directly.")

    raise ValueError(f"Unknown stage kind: {stage.kind!r}")  # pragma: no cover -- Stage() already validates


def matte_letters_for(stages):
    """Maps each MATTE_PRODUCING_KINDS stage's own Stage.id to its
    current display letter (A, B, C... then, past 26, Tom's own doubling
    scheme -- AA, BB, CC... rather than spreadsheet-style AB/AC) --
    computed FRESH by scanning `stages` in order, never stored (see this
    module's docstring, "MATTE CHANNELS", on why letters are display-
    only). Every matte-producing kind shares ONE letter sequence (a
    "keyer" and a "segment_mask" interleaved in the stack still just get
    A, B, C... in stack order, not separate per-kind sequences) since
    they're interchangeable from a consuming stage's own dropdown --
    what produced the matte doesn't matter to how it's referenced. Does
    NOT skip disabled stages -- a temporarily-disabled one keeps its own
    stable letter rather than every subsequent letter shifting down
    while it's toggled off; a downstream stage still referencing it just
    gets no gating effect at render time (see _stage_blend_factor), same
    as any other unresolved matte_source_id."""
    letters = {}
    ordinal = 0
    for stage in stages:
        if stage.kind not in MATTE_PRODUCING_KINDS:
            continue
        letters[stage.id] = _ordinal_to_letter(ordinal)
        ordinal += 1
    return letters


def _ordinal_to_letter(ordinal):
    if ordinal < 26:
        return chr(ord("A") + ordinal)
    doubled = ordinal - 26
    if doubled < 26:
        return chr(ord("A") + doubled) * 2
    # Past ZZ (52 matte channels) -- shouldn't come up in practice (per
    # Tom: "shouldn't have to"), but degrade to a numeric label instead
    # of raising so an extreme stack never hard-crashes over a display
    # string.
    return f"M{ordinal + 1}"


def _stage_blend_factor(stage, mattes):
    """The value _apply_single_stage_with_opacity (and fork_merge's own
    inline blend) actually use as the crossfade factor: `stage.opacity`
    unchanged (a plain float, same as always) UNLESS this stage's own
    params reference a matte that's actually available in `mattes` (see
    this module's docstring, "MATTE CHANNELS"), in which case the result
    is an (H,W,1) per-pixel array instead -- `stage.opacity` scaling the
    matte's own value, so the row's existing opacity control still acts
    as an overall strength knob on top of the key. `mattes` may be None
    (evaluation contexts that never populate it, if any) or simply not
    contain this stage's own reference yet/anymore -- both fall back to
    the plain scalar, no error."""
    matte_source_id = stage.params.get("matte_source_id")
    if matte_source_id is not None and mattes is not None:
        matte = mattes.get(matte_source_id)
        if matte is not None:
            return stage.opacity * matte[..., np.newaxis]
    return stage.opacity


def _blend_running_toward(running, stage_output, factor):
    """running*(1-factor) + stage_output*factor, with the same >=1.0/
    <=0.0 float-error/no-op shortcuts _apply_single_stage_with_opacity
    always took, now shared with fork_merge's own inline blend -- taken
    only when `factor` is a plain scalar (np.isscalar), since a per-pixel
    matte array has no single "is it exactly 1 or 0" answer to shortcut
    on."""
    if np.isscalar(factor):
        if factor >= 1.0:
            return stage_output
        if factor <= 0.0:
            return running
    return running * (1.0 - factor) + stage_output * factor


def _apply_single_stage_with_opacity(stage, running, mattes=None):
    """One stage's full enabled+apply+opacity treatment against `running`
    -- the single fold step _run_stages applies once per stage in a Stack,
    factored out here so a fork stage's two branches (each a standalone
    Stage that's never part of a Stack's own stages list) get the EXACT
    same enabled/opacity semantics a top-level stage would, via one shared
    implementation rather than a second copy of this logic living in
    _apply_stage_to_array's fork branch. disabled -> exact no-op (return
    `running` unchanged); opacity follows the same >=1.0 / <=0.0 /
    linear-blend rule as concatenate_luts_with_opacity, for the same
    reason (avoid needless float error at the common opacity=1.0 case, and
    an exact no-op at opacity=0.0) -- UNLESS this stage opts into a matte
    (see _stage_blend_factor), in which case the blend is per-pixel
    instead. `mattes`: dict[stage_id, (H,W) array] accumulated so far by
    _run_stages -- see this module's docstring, "MATTE CHANNELS"."""
    if not stage.enabled:
        return running
    stage_output = _apply_stage_to_array(stage, running)
    factor = _stage_blend_factor(stage, mattes)
    return _blend_running_toward(running, stage_output, factor)


def _blend_two(a, b, blend):
    """Plain linear crossfade -- 0.0 = all `a`, 1.0 = all `b`. Shared by
    fork_merge's branch-combining step (the only caller so far), factored
    out mainly so that piece of math has one obviously-correct, tested-
    by-itself home rather than being inlined where it'd be easy to get
    the two halves backwards."""
    if blend >= 1.0:
        return b
    if blend <= 0.0:
        return a
    return a * (1.0 - blend) + b * blend


def _compute_raw_matte(stage, running, mattes):
    """Dispatches to whichever matte-producing kind's own compute
    function `stage.kind` is (see MATTE_PRODUCING_KINDS) -- the one
    place that needs to know every kind's own function signature, so
    _maybe_populate_matte itself stays kind-agnostic. `mattes`: the SAME
    dict[stage_id, (H,W) array] _run_stages has already accumulated by
    this stage's own position -- unused by "keyer"/"segment_mask" (they
    read `running`'s own pixel values instead), but "matte_combine"
    reads ONLY this, never `running`'s values (see matte_combine.py's
    own docstring). "matte_shape" reads neither -- pure geometry, only
    `running.shape` (see matte_shape.py's own docstring)."""
    if stage.kind == "keyer":
        return keyer.compute_matte(running, stage.params)
    if stage.kind == "segment_mask":
        p = stage.params
        return segmentation.compute_mask_from_points(
            running, p.get("points") or [],
            tolerance=p.get("tolerance", segmentation.DEFAULT_TOLERANCE),
            feather=p.get("feather", segmentation.DEFAULT_FEATHER),
            invert=p.get("invert", segmentation.DEFAULT_INVERT),
        )
    if stage.kind == "matte_combine":
        p = stage.params
        shape = running.shape[:2]
        matte_a = mattes.get(p.get("matte_a_source_id")) if mattes else None
        matte_b = mattes.get(p.get("matte_b_source_id")) if mattes else None
        if matte_a is None:
            matte_a = np.zeros(shape, dtype=np.float64)
        if matte_b is None:
            matte_b = np.zeros(shape, dtype=np.float64)
        return matte_combine.compute_combined_matte(
            matte_a, matte_b, op=p.get("op", matte_combine.DEFAULT_OP))
    if stage.kind == "matte_shape":
        # Pure geometry -- reads only `running`'s own (H, W), never its
        # pixel values (see matte_shape.py's own docstring).
        return matte_shape.compute_shape_matte(running.shape[:2], stage.params)
    raise AssertionError(  # pragma: no cover -- _maybe_populate_matte already checked
        f"{stage.kind!r} is not in MATTE_PRODUCING_KINDS")


def _maybe_populate_matte(stage, running, mattes):
    """If `stage` is an enabled matte-producing kind (see
    MATTE_PRODUCING_KINDS), computes its matte -- from `running` AS IT
    STANDS RIGHT BEFORE this stage for "keyer"/"segment_mask", or from
    OTHER already-computed entries in `mattes` for "matte_combine" (see
    _compute_raw_matte) -- and stores it into `mattes[stage.id]` (see
    this module's docstring, "MATTE CHANNELS"). Called at every point
    _run_stages is about to apply a stage (top-level and inside either
    fork branch), so a matte-producing stage inside a fork branch still
    populates the SAME shared `mattes` dict (a deliberate, documented
    simplification -- see the docstring). A disabled stage populates
    nothing, so any downstream reference to it resolves to "no matte,"
    same as an entirely-removed one would."""
    if stage.kind in MATTE_PRODUCING_KINDS and stage.enabled:
        mattes[stage.id] = _compute_raw_matte(stage, running, mattes) * stage.opacity


def _run_stages(stages, running, mattes=None):
    """Shared fold used by both bake_stack_to_lut3d and
    apply_stack_to_image_array (directly, or via StageCache.run -- see
    that class's own docstring for why `mattes` is a parameter here
    rather than always starting fresh: StageCache calls this once PER
    CACHE BLOCK, so a keyer in one block and its consumer in a later one
    -- the common case, since an ordinary stage is its own block, see
    _partition_into_blocks -- need the SAME `mattes` dict passed across
    those separate calls, mutated in place, to see each other at all).
    `mattes=None` (every direct, non-cached caller) creates a fresh local
    dict, unchanged from this function's original behavior. Ordinary
    stages just go through
    _apply_single_stage_with_opacity, in order, one at a time, exactly as
    always. fork_split/fork_merge (see this module's docstring, "STACK
    FORKING") are the one structural exception, tracked on `fork_stack` --
    a plain list used here as a stack of open regions, NOT this app's own
    Stack class -- rather than recursion, so open-region state can be
    validated as stages are walked in one linear pass:

    - fork_split while fork_stack is already non-empty -> nesting isn't
      supported yet, raises.
    - fork_split with enabled=False pushes a None sentinel. Every
      interior stage until the matching merge is then skipped outright
      (see below), and since the SHARED `running` is never touched while
      a region is open regardless of whether it's disabled (branch-tagged
      stages only ever mutate running_a/running_b, never `running`
      itself), it reaches the merge completely unchanged either way --
      "whole region is a no-op" falls out of that for free, no extra
      code needed for the disabled case specifically.
    - A stage reached while fork_stack is non-empty (and it isn't itself
      fork_split/fork_merge) is routed to running_a or running_b per its
      OWN .branch, never touching `running`. A stage in this position
      with no valid branch ("a"/"b") raises -- there's no reasonable side
      to silently default it to. This is also exactly what lets
      fork_merge below read `running` back out as "the signal exactly as
      it stood right before the split," with no separate bookkeeping
      needed for that either.
    - fork_merge with an EMPTY fork_stack -> no matching split, raises.
      Otherwise pops the open region, crossfades running_a/running_b via
      its own `blend` (_blend_two), then blends THAT against `running`
      via its own opacity -- the same math _apply_single_stage_with_
      opacity uses, just inlined here since fork_merge needs three inputs
      (running_a, running_b, running) instead of the usual one, so it
      can't just call that helper directly the way every other stage
      does.
    - Any stage.branch is otherwise simply IGNORED while fork_stack is
      EMPTY -- a stray tag left over from a stage that's been dragged out
      of a fork region in the UI is inert, not an error.
    - fork_stack still non-empty after the whole loop -> an unmatched
      split with no merge after it, raises.

    MATTE CHANNELS (see this module's docstring): a single `mattes` dict
    accumulates as this loop walks the stage list, regardless of whether
    a given stage is top-level or inside a fork branch -- populated by
    _maybe_populate_matte right before each "keyer" stage would
    otherwise be applied (using `running`/`running_a`/`running_b` AS IT
    STANDS at that exact point), then handed to every
    _apply_single_stage_with_opacity call (and used directly for
    fork_merge's own inline blend) so any stage's own `matte_source_id`
    can resolve against whatever's been produced so far."""
    fork_stack = []
    if mattes is None:
        mattes = {}
    for stage in stages:
        if stage.kind == "fork_split":
            if fork_stack:
                raise ValueError(
                    f"fork_split {stage.label!r}: nested fork regions aren't "
                    "supported yet.")
            fork_stack.append(
                {"running_a": running, "running_b": running} if stage.enabled else None)
            continue

        if stage.kind == "fork_merge":
            if not fork_stack:
                raise ValueError(
                    f"fork_merge {stage.label!r}: no matching fork_split before it.")
            context = fork_stack.pop()
            if context is None:
                continue  # the matching split was disabled -- whole region is a no-op
            if not stage.enabled:
                continue  # same net effect as opacity=0.0 below -- `running` stays untouched
            combined = _blend_two(
                context["running_a"], context["running_b"],
                float(stage.params.get("blend", 0.5)))
            factor = _stage_blend_factor(stage, mattes)
            running = _blend_running_toward(running, combined, factor)
            continue

        if fork_stack:
            context = fork_stack[-1]
            if context is None:
                continue  # inside a disabled fork region -- every interior stage is skipped
            if stage.branch == "a":
                _maybe_populate_matte(stage, context["running_a"], mattes)
                context["running_a"] = _apply_single_stage_with_opacity(
                    stage, context["running_a"], mattes)
            elif stage.branch == "b":
                _maybe_populate_matte(stage, context["running_b"], mattes)
                context["running_b"] = _apply_single_stage_with_opacity(
                    stage, context["running_b"], mattes)
            else:
                raise ValueError(
                    f"Stage {stage.label!r} is inside a fork region but has no "
                    "branch assignment (expected 'a' or 'b').")
            continue

        _maybe_populate_matte(stage, running, mattes)
        running = _apply_single_stage_with_opacity(stage, running, mattes)

    if fork_stack:
        raise ValueError("fork_split with no matching fork_merge after it.")
    return running


_identity_grid_cache = {}


def _get_identity_grid(size):
    """A plain [0,1]-domain identity grid at `size`, memoized per size so
    repeated bakes at an unchanged Stack.size hand back the exact same
    array OBJECT, not just an equal one -- this is what lets StageCache
    (below) recognize "the input didn't change" with a cheap identity
    check instead of hashing or comparing array contents on every single
    edit. Safe to share: every stage-apply function this module dispatches
    to (see _apply_stage_to_array's callees) returns a NEW array rather
    than mutating its input, so handing out this same cached object
    repeatedly never lets one bake's math leak into another's."""
    grid = _identity_grid_cache.get(size)
    if grid is None:
        grid = colour.LUT3D(size=size).table
        _identity_grid_cache[size] = grid
    return grid


def _stage_fingerprint(stage):
    """Everything about `stage` that can change what it computes, as one
    hashable/comparable tuple -- StageCache's way of telling "this exact
    stage, unchanged" between two runs without re-running it. Plain
    immutable param values (float/str/bool/tuple/None) compare by value;
    anything else -- namely a 'lut' stage's `lut` object -- compares by
    identity. That's safe because loaded/baked LUT objects are never
    mutated in place anywhere in this app (same guarantee _get_identity_
    grid leans on above): two edits hand back the same lut object only
    when it really is the same, unchanged LUT."""
    def fp(value):
        if value is None or isinstance(value, (int, float, str, bool, tuple)):
            return value
        return id(value)
    params_fp = tuple(sorted((k, fp(v)) for k, v in stage.params.items()))
    return (stage.kind, stage.enabled, stage.opacity, stage.branch, params_fp)


def _partition_into_blocks(stages):
    """Split a flat stage list into StageCache's cache units: an ordinary
    stage is its own one-stage block; a fork_split through its matching
    fork_merge (see this module's docstring, STACK FORKING) is cached as
    ONE block rather than per interior stage, since _run_stages' fork
    bookkeeping (running_a/running_b, the open-region validation) only
    makes sense evaluated together -- forks are the one exception to
    "one stage, one block" here, same as they're the one exception
    everywhere else in this module. An unmatched fork_split (no merge
    found before the list ends) becomes a block running to the end of the
    list -- _run_stages raises its own clear error when that block
    actually runs, no need to duplicate that validation here."""
    blocks = []
    i, n = 0, len(stages)
    while i < n:
        if stages[i].kind == "fork_split":
            j = i + 1
            while j < n and stages[j].kind != "fork_merge":
                j += 1
            end = min(j + 1, n)
            blocks.append(stages[i:end])
            i = end
        else:
            blocks.append([stages[i]])
            i += 1
    return blocks


def find_enclosing_fork_region(stages, insert_index):
    """If inserting a new stage at `insert_index` -- a GAP position in
    `stages`, one of 0..len(stages), same convention Stack.add_stage's
    own `index` uses -- would land it strictly between some fork_split
    and its matching fork_merge, return that pair's (split_index,
    merge_index) into `stages`. Returns None if insert_index isn't
    inside any fork region: before the nearest split, after the nearest
    merge, or there's no fork in the stack at all. Same "no nesting"
    assumption _run_stages itself makes (see this module's docstring,
    STACK FORKING) -- only one region can ever be open at a time, so
    there's never an ambiguous "which region" to resolve.

    Used by StackPanel to auto-assign a newly added/dropped stage's
    branch instead of always requiring the user to pick Branch A/B by
    hand afterward (see fork_region_branch_counts, just below)."""
    open_split = None
    for i, stage in enumerate(stages):
        if stage.kind == "fork_split":
            open_split = i
        elif stage.kind == "fork_merge":
            if open_split is not None and open_split < insert_index <= i:
                return (open_split, i)
            open_split = None
    return None


def fork_region_branch_counts(stages, split_index, merge_index):
    """How many stages strictly between `stages[split_index]` (a
    fork_split) and `stages[merge_index]` (its matching fork_merge)
    currently carry each branch tag -- `{"a": count, "b": count}`.
    Paired with find_enclosing_fork_region so a caller can tell "both
    sides empty" apart from "one side already has stages" without
    re-deriving the region's boundaries itself."""
    counts = {"a": 0, "b": 0}
    for stage in stages[split_index + 1:merge_index]:
        if stage.branch in counts:
            counts[stage.branch] += 1
    return counts


def find_fork_partner(stages, marker_stage_id):
    """Given the id of a stage that's a fork_split or fork_merge, finds
    its matching other half and every stage currently sitting between
    them. Returns `(split_id, merge_id, interior_ids)`, or None if
    `marker_stage_id` isn't a fork marker, or has no matching partner (an
    already-corrupted/hand-edited stack -- same "no nesting, only one
    open region at a time" assumption find_enclosing_fork_region and
    _run_stages both already make, so a plain forward/backward scan for
    the nearest opposite marker is unambiguous).

    Used by StackPanel to delete a fork_split/fork_merge pair together
    (per Tom: "they should also be linked so they delete together")
    rather than leaving an unmatched half the way a plain
    Stack.remove_stage(marker_stage_id) would."""
    index = next((i for i, s in enumerate(stages) if s.id == marker_stage_id), None)
    if index is None or stages[index].kind not in ("fork_split", "fork_merge"):
        return None
    if stages[index].kind == "fork_split":
        split_index = index
        merge_index = next(
            (i for i in range(index + 1, len(stages)) if stages[i].kind == "fork_merge"), None)
    else:
        merge_index = index
        split_index = next(
            (i for i in range(index - 1, -1, -1) if stages[i].kind == "fork_split"), None)
    if split_index is None or merge_index is None:
        return None
    interior_ids = [s.id for s in stages[split_index + 1:merge_index]]
    return (stages[split_index].id, stages[merge_index].id, interior_ids)


class StageCache:
    """Bounded memoization for _run_stages. One instance per independent
    evaluation path -- bake_stack_to_lut3d's grid bake and
    apply_stack_to_image_array's live preview each keep their own (see
    StackPanel/SampleImagePanel) -- since they start from different
    `running` inputs and there's no reason a bake edit should invalidate
    the preview's cache or vice versa.

    Stores exactly ONE checkpoint array per cache block (see
    _partition_into_blocks) from the most recent run, replaced wholesale
    every call -- never accumulated across edits -- so memory stays
    proportional to the stack's own size (one array per stage, the same
    order of magnitude as the stack itself), not to how many edits have
    happened. On each call, this walks the new stage list's block
    fingerprints against the previous run's, reuses every checkpoint up
    to the first block that changed, and re-runs _run_stages only on the
    changed suffix. Nudging a single opacity slider on stage k of an
    N-stage stack is then O(1) real work (just that one block) plus a
    cheap O(N) fingerprint comparison, instead of re-running all N stages
    from scratch on every tick -- which is what made this app feel
    sluggish as a stack grew, since both the 3D-viewer bake and the live
    sample-image preview independently re-ran the WHOLE stack on every
    single edit, including live slider drags.

    `input_key` identifies the starting array without hashing its
    contents: bake_stack_to_lut3d passes `(stack.size, stack.domain_min,
    stack.domain_max)` -- size alone used to be enough back when the grid
    was always plain [0,1] (see _get_identity_grid, which still hands back
    the same cached array object for an unchanged size), but the grid is
    now domain-scaled, so a domain-only change (same size) has to
    invalidate the cache too or it'd hand back a checkpoint computed for
    the OLD domain. apply_stack_to_image_array's caller supplies whatever
    it wants two calls sharing "the same underlying image" to agree on --
    SampleImagePanel uses a generation counter bumped only when it
    actually decodes/re-derives a new working array, so an unrelated Stack
    edit never looks like a new image. A changed input_key invalidates
    everything -- there's nothing left to diff against.

    MATTE CHANNELS (see this module's docstring): since _run_stages here
    gets called ONCE PER BLOCK -- and an ordinary stage is its own block
    (_partition_into_blocks), so a "keyer" and the stage consuming its
    matte are almost always in DIFFERENT blocks -- a fresh per-call
    `mattes` dict (this class's own _run_stages calls used to just let
    that function default to one) would never let a later block see an
    earlier one's matte. `_block_mattes` mirrors `_block_outputs`
    exactly (one FULL cumulative-mattes-dict snapshot per block position,
    not just that block's own delta) so a reused checkpoint prefix hands
    back its accumulated mattes state alongside its image checkpoint,
    and a newly-run suffix keeps mutating one shared dict across all of
    it -- a keyer stage's own edit re-runs its block AND, by the existing
    "changed suffix" rule below, everything after it, so a stale matte
    from before the edit is never handed to a downstream consumer."""

    def __init__(self):
        self._input_key = None
        self._block_fingerprints = []
        self._block_outputs = []
        self._block_mattes = []

    def run(self, stages, running, input_key):
        if input_key != self._input_key:
            self._input_key = input_key
            self._block_fingerprints = []
            self._block_outputs = []
            self._block_mattes = []

        blocks = _partition_into_blocks(stages)
        new_fingerprints = [tuple(_stage_fingerprint(s) for s in block) for block in blocks]

        reuse = 0
        for old_fp, new_fp in zip(self._block_fingerprints, new_fingerprints):
            if old_fp != new_fp:
                break
            reuse += 1

        current = self._block_outputs[reuse - 1] if reuse > 0 else running
        mattes = dict(self._block_mattes[reuse - 1]) if reuse > 0 else {}
        outputs = self._block_outputs[:reuse]
        mattes_history = self._block_mattes[:reuse]
        for block in blocks[reuse:]:
            current = _run_stages(block, current, mattes)
            outputs.append(current)
            mattes_history.append(dict(mattes))

        self._block_fingerprints = new_fingerprints
        self._block_outputs = outputs
        self._block_mattes = mattes_history
        return current

    def last_mattes(self):
        """dict[stage_id, (H,W) array] -- every matte a matte-producing
        stage (see MATTE_PRODUCING_KINDS) actually contributed during
        the MOST RECENT run() call, e.g. for a Highlight-mode-style
        preview that wants to show a specific stage's own key overlaid
        on the graded result. `{}` if run() has never been called.
        Returns a COPY (dict(...)) so a caller can't mutate this cache's
        own internal state; the values themselves (numpy arrays) are
        still shared by reference with whatever _block_mattes is
        holding, same as _block_outputs' own checkpoints already are --
        safe as long as nothing downstream mutates a matte array in
        place, which nothing in this codebase does."""
        return dict(self._block_mattes[-1]) if self._block_mattes else {}

    def clear(self):
        """Drop every checkpoint -- for a wholesale Stack replacement
        (session load, undo/redo, Stack Memory recall: see StackPanel.
        set_stack) where the incoming stages are fresh Stage objects with
        their own ids. Nothing would actually fingerprint-match the old
        checkpoints anyway, so this is purely about releasing that memory
        immediately rather than one block at a time as edits happen to
        touch each position."""
        self._input_key = None
        self._block_fingerprints = []
        self._block_outputs = []
        self._block_mattes = []


def bake_stack_to_lut3d(stack, name=None, cache=None):
    """Bake the whole stack -- every enabled stage, in order, at
    `stack.size` -- into one flat colour.LUT3D, by folding each stage over
    a plain [0,1]-domain identity grid (same "trust each stage's own
    verified apply, don't hand-roll interpolation" rule as every other
    baking helper in this app). `stack.domain_min`/`domain_max` are
    written onto the result as metadata afterward (see this module's
    docstring on why that's correct and doesn't need to touch the table).
    An empty stack (no stages) bakes to a plain identity LUT3D at
    `stack.size`, i.e. Stack() with nothing added IS "load an identity
    LUT," not a special case.

    From this point the result is just another LUT to the rest of the
    app, same integration strategy as every prior baking function here.

    `cache`, if given, is a StageCache reused across repeated bakes of
    the same live Stack (see StackPanel) -- lets an edit to one stage
    reuse every OTHER stage's already-computed result instead of
    re-running the whole stack from scratch (see StageCache's own
    docstring). Omit it (default) for a one-shot bake -- e.g. export --
    where there's nothing to reuse next time anyway."""
    lo = np.broadcast_to(np.asarray(stack.domain_min, dtype=float), (3,))
    hi = np.broadcast_to(np.asarray(stack.domain_max, dtype=float), (3,))
    # Fold the stages over a grid that actually SPANS [domain_min,
    # domain_max], not the fixed [0,1] identity grid -- reduces to that
    # plain grid exactly when domain is the default (0,0,0)-(1,1,1), so
    # there's no separate code path to keep in sync for the common case.
    # Getting this wrong is exactly the bug this comment is guarding
    # against: an extended-domain export (Tom, 2026-08-12) that LABELED
    # its table with the real domain via lut.domain below but had actually
    # computed that table from the WRONG [0,1] slice of the stack's
    # stages, so any domain-aware reader (this app's own re-import,
    # Lattice) sampled a table that didn't match its own header --
    # visually a much-too-dark result, since most of the real extended
    # input range was being pulled from table entries that were never
    # actually computed for it.
    identity_grid = lo + _get_identity_grid(stack.size) * (hi - lo)
    if cache is not None:
        # input_key must reflect domain too, not just size -- StageCache
        # trusts a matching input_key to mean "the starting array is
        # unchanged" and will hand back a stale (wrong-domain) checkpoint
        # otherwise, even though `identity_grid` above is a brand new
        # array (see StageCache.run's own docstring on what input_key is
        # for).
        running = cache.run(stack.stages, identity_grid, input_key=(stack.size, stack.domain_min, stack.domain_max))
    else:
        running = _run_stages(stack.stages, identity_grid)

    # np.array, NOT np.asarray -- this must always copy. `running` may be
    # a checkpoint array a StageCache is still holding onto, and
    # colour.LUT3D takes ownership of `table` without necessarily copying
    # it. Without forcing a copy here, some future in-place edit to the
    # returned LUT's .table could silently corrupt a cached checkpoint
    # that a LATER bake would then read back out as if it were still
    # correct.
    table = np.array(running, dtype=float)
    lut = colour.LUT3D(table=table, size=stack.size, name=name or stack.name)
    lut.domain = np.array([lo, hi], dtype=float)
    return lut


def apply_stack_to_image_array(stack, image_array, cache=None, input_key=None):
    """Apply every enabled stage directly to real image pixels, in order,
    with no intermediate grid -- for exact live preview of an
    in-progress stack, mirroring app_logic.apply_lut_chain_with_opacity's
    relationship to concatenate_luts_with_opacity. Clips to [0,1] for
    on-screen display, same convention as apply_lut_to_image_array.
    Stack.size/domain have no effect here (they're properties of the
    BAKED LUT3D object, not the direct-pixel path) -- an empty stack
    returns the image unchanged.

    `cache`/`input_key` mirror bake_stack_to_lut3d's -- a StageCache
    reused across repeated applies of the same live Stack against the
    same underlying image (see SampleImagePanel), and a hashable key the
    caller uses to say "this is still the same image as last time" (see
    StageCache's own docstring on why that can't just be image_array
    itself). Omit both for a one-shot apply -- e.g. exporting a full-
    resolution still -- where nothing will be reused afterward. If
    `cache` is given without `input_key`, falls back to id(image_array),
    which is only safe for a caller that itself keeps image_array alive
    (and unchanged) for as long as it reuses this cache."""
    result = np.asarray(image_array, dtype=np.float32)
    if cache is not None:
        result = cache.run(stack.stages, result,
                            input_key=input_key if input_key is not None else id(image_array))
    else:
        result = _run_stages(stack.stages, result)
    return np.clip(result, 0.0, 1.0).astype(np.float32)
