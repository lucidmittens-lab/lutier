"""
colour_pipeline.py -- Baselight-style color management for the whole
image path (Tom, 2026-10-05: "take the color management and order of
operations luxury of baselight to a simple and free tool").

    decoded file -[input]-> project working space -> Stack -[display]-> viewer

Rather than teaching every renderer about color spaces, this builds the
EFFECTIVE stack those renderers already know how to run (CPU, GPU, bake,
export, Scopes):

  1. INPUT: a leading "colorspace" stage, the source's input color space
     -> the project working space -- when the source has one set (None
     means "already in the working space", the default for anything not
     recognized, per Tom).
  2. OPERATOR SPACES: every stage with its own "working_colourspace" that
     differs from the space flowing into it is wrapped -- converted into
     its space, applied, and converted back (Tom chose "convert
     everything": this replaces the old "assume the input is already in
     that space"). DRT stages convert in only: their output is display-
     referred, and nothing after a DRT is converted any more.
  3. DISPLAY: a trailing DRT (the project's display transform) -- unless
     it's off (the default) or the Stack already contains a DRT.

The user's own Stage objects are reused as-is (matte references are by
stage id, so they keep resolving); only the conversion and display stages
are new, and StageCache fingerprints stages by value, so rebuilding this
every frame still reuses cached results.

Qt-free -- same as stack.py.
"""
from collections import namedtuple

import aces_drt
import stack as st

# Stages whose "working_colourspace" param means "operate in this space".
_CONVERTING_KINDS = {"printer_lights", "color_warper", "keyer"}
# ...and these are display renderers: convert in, never back.
_DRT_KINDS = {"aces_drt", "custom_drt"}

# drt: "none" or "aces"; display/view: OCIO display + view names.
DisplayTransform = namedtuple("DisplayTransform", "drt display view")
NO_DISPLAY_TRANSFORM = DisplayTransform("none", "", "")


def _csc(src, dst, branch=None, label=None):
    stage = st.make_colorspace_stage(src, dst, label=label or f"{src} -> {dst}")
    stage.branch = branch
    return stage


def build_effective_stack(user_stack, working_colourspace, input_colourspace=None,
                          display=NO_DISPLAY_TRANSFORM):
    """The Stack to actually render -- see the module docstring. With no
    input space, every stage in the working space and no display
    transform, it's just `user_stack`'s own stages (same order, same
    objects) -- so today's results are unchanged until a space is set."""
    stages = []
    if input_colourspace and input_colourspace != working_colourspace:
        stages.append(_csc(input_colourspace, working_colourspace, label="Input transform"))

    display_referred = False   # past a DRT: stop converting
    for stage in user_stack.stages:
        space = stage.params.get("working_colourspace") if stage.enabled else None
        wrap = (not display_referred and space and space != working_colourspace
                and stage.kind in _CONVERTING_KINDS | _DRT_KINDS)
        if wrap:
            stages.append(_csc(working_colourspace, space, branch=stage.branch))
        stages.append(stage)
        if wrap and stage.kind in _CONVERTING_KINDS:
            stages.append(_csc(space, working_colourspace, branch=stage.branch))
        if stage.enabled and stage.kind in _DRT_KINDS and stage.branch is None:
            display_referred = True

    if (display.drt == "aces" and display.display and display.view
            and not any(s.enabled and s.kind in _DRT_KINDS for s in user_stack.stages)):
        stages.append(st.make_aces_drt_stage(
            working_colourspace, display.display, display.view, label="Display transform"))
    return st.Stack(stages=stages, name=user_stack.name)


def stage_space_note(stage, working_colourspace):
    """Short note for the Stack row of a stage that works in a different
    space than the project's ("in ARRI LogC4"), or "" -- see
    build_effective_stack for what that means."""
    space = stage.params.get("working_colourspace")
    if space and space != working_colourspace and stage.kind in _CONVERTING_KINDS | _DRT_KINDS:
        return f"in {space}"
    return ""


# ---- input color space suggestions -------------------------------------------

# (substring of the camera tag, lower-case) -> the config's color space.
# The first match wins, so more specific models come first.
_CAMERA_SPACES = (
    ("alexa 35", "ARRI LogC4"),
    ("arri", "ARRI LogC3 (EI800)"),
    ("venice", "S-Log3 Venice S-Gamut3.Cine"),
    ("sony", "S-Log3 S-Gamut3.Cine"),
    ("red", "Log3G10 REDWideGamutRGB"),
    ("blackmagic", "BMDFilm WideGamut Gen5"),
    ("panasonic", "V-Log V-Gamut"),
)


def suggest_input_colourspace(metadata):
    """A suggested input color space for media with this media_probe
    metadata, or None (= already in the working space -- Tom's chosen
    default for anything not recognized). Only camera-tagged log
    footage is recognized for now, and only from spaces the config has."""
    camera = (metadata or {}).get("camera", "").lower()
    if not camera:
        return None
    available = set(available_input_colourspaces())
    for needle, space in _CAMERA_SPACES:
        if needle in camera and space in available:
            return space
    return None


def available_input_colourspaces():
    if not aces_drt.OCIO_AVAILABLE:
        return []
    return aces_drt.list_working_colourspaces()


def available_displays():
    """[(display, [views...])] from the config -- for Project Settings."""
    if not aces_drt.OCIO_AVAILABLE:
        return []
    cfg = aces_drt._get_config(aces_drt.DEFAULT_CONFIG_NAME)
    return [(d, list(cfg.getViews(d))) for d in cfg.getDisplays()]
