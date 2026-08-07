"""
stack.py -- Qt-free backend for a unified, ordered, non-destructive
operation stack: a single list of stages (each add a LUT, a Printer Lights
trim, an ACES DRT output transform, an ASC CDL SOP/SAT grade, or a plain
non-DRT color space conversion) that can be reordered, toggled, given
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
  - Bounds (app_logic.set_lut_domain) was already metadata-only in the
    original implementation -- it never resampled or recomputed the
    table, just relabeled what input range the existing table represents
    (see that function's own docstring). There's nothing for a "Bounds
    stage" to recompute mid-chain; it's a property of the final baked
    object. So Stack.domain_min/domain_max are stack-level fields applied
    once, after baking, exactly like set_lut_domain always did.
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
import colorspace_convert

STAGE_KINDS = ("lut", "printer_lights", "aces_drt", "cdl", "colorspace",
               "fork_split", "fork_merge")

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
    stage's own input, not some other blend mode). `branch` is None for
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

    if stage.kind in ("fork_split", "fork_merge"):  # pragma: no cover -- see docstring above
        raise AssertionError(
            f"{stage.kind} stages are handled structurally by _run_stages "
            "and should never reach _apply_stage_to_array directly.")

    raise ValueError(f"Unknown stage kind: {stage.kind!r}")  # pragma: no cover -- Stage() already validates


def _apply_single_stage_with_opacity(stage, running):
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
    an exact no-op at opacity=0.0)."""
    if not stage.enabled:
        return running
    stage_output = _apply_stage_to_array(stage, running)
    opacity = stage.opacity
    if opacity >= 1.0:
        return stage_output
    if opacity <= 0.0:
        return running
    return running * (1.0 - opacity) + stage_output * opacity


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


def _run_stages(stages, running):
    """Shared fold used by both bake_stack_to_lut3d and
    apply_stack_to_image_array. Ordinary stages just go through
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
      split with no merge after it, raises."""
    fork_stack = []
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
            opacity = stage.opacity
            if opacity >= 1.0:
                running = combined
            elif opacity <= 0.0:
                pass
            else:
                running = running * (1.0 - opacity) + combined * opacity
            continue

        if fork_stack:
            context = fork_stack[-1]
            if context is None:
                continue  # inside a disabled fork region -- every interior stage is skipped
            if stage.branch == "a":
                context["running_a"] = _apply_single_stage_with_opacity(
                    stage, context["running_a"])
            elif stage.branch == "b":
                context["running_b"] = _apply_single_stage_with_opacity(
                    stage, context["running_b"])
            else:
                raise ValueError(
                    f"Stage {stage.label!r} is inside a fork region but has no "
                    "branch assignment (expected 'a' or 'b').")
            continue

        running = _apply_single_stage_with_opacity(stage, running)

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
    contents: bake_stack_to_lut3d passes `stack.size` (an int -- see
    _get_identity_grid, which hands back the same cached array object for
    an unchanged size, so this key alone is enough). apply_stack_to_image_
    array's caller supplies whatever it wants two calls sharing "the same
    underlying image" to agree on -- SampleImagePanel uses a generation
    counter bumped only when it actually decodes/re-derives a new working
    array, so an unrelated Stack edit never looks like a new image. A
    changed input_key invalidates everything -- there's nothing left to
    diff against."""

    def __init__(self):
        self._input_key = None
        self._block_fingerprints = []
        self._block_outputs = []

    def run(self, stages, running, input_key):
        if input_key != self._input_key:
            self._input_key = input_key
            self._block_fingerprints = []
            self._block_outputs = []

        blocks = _partition_into_blocks(stages)
        new_fingerprints = [tuple(_stage_fingerprint(s) for s in block) for block in blocks]

        reuse = 0
        for old_fp, new_fp in zip(self._block_fingerprints, new_fingerprints):
            if old_fp != new_fp:
                break
            reuse += 1

        current = self._block_outputs[reuse - 1] if reuse > 0 else running
        outputs = self._block_outputs[:reuse]
        for block in blocks[reuse:]:
            current = _run_stages(block, current)
            outputs.append(current)

        self._block_fingerprints = new_fingerprints
        self._block_outputs = outputs
        return current

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
    identity_grid = _get_identity_grid(stack.size)
    if cache is not None:
        running = cache.run(stack.stages, identity_grid, input_key=stack.size)
    else:
        running = _run_stages(stack.stages, identity_grid)

    # np.array, NOT np.asarray -- this must always copy. `running` may be
    # a checkpoint array a StageCache is still holding onto (or, for an
    # empty/fully-disabled stack, even _get_identity_grid's own shared,
    # memoized array), and colour.LUT3D takes ownership of `table` without
    # necessarily copying it. Without forcing a copy here, some future
    # in-place edit to the returned LUT's .table could silently corrupt a
    # cached checkpoint (or the shared identity grid) that a LATER bake
    # would then read back out as if it were still correct.
    table = np.array(running, dtype=float)
    lut = colour.LUT3D(table=table, size=stack.size, name=name or stack.name)

    lo = np.broadcast_to(np.asarray(stack.domain_min, dtype=float), (3,))
    hi = np.broadcast_to(np.asarray(stack.domain_max, dtype=float), (3,))
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
