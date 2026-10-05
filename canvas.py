"""
canvas.py -- LUTier's project-level "canvas": the working resolution, bit
depth, pixel aspect ratio, and frame rate a session targets, independent
of whatever still/video happens to be loaded (Tom, 2026-08-13: "Canvas
settings (Resolution / Bit Depth / Pixel Aspect)").

Deliberately NOT auto-synced to the loaded media's own dimensions after
the first load -- see canvas_from_first_media's own docstring. Follows
this codebase's established "plain tuple of constants, not enum.Enum"
convention (see stack.STAGE_KINDS, custom_drt.MASTERING_DISPLAYS) rather
than introducing a new idiom for the first time here.

Same rule as every other backend module in this app: no PySide6 import
here, fully testable without a display.
"""
from dataclasses import dataclass, replace

# Bit depth: the four real integer sample depths a professional pipeline
# actually delivers (8/10/12/16), plus "32f" for float (EXR-class,
# unbounded HDR-capable) -- NOT an enum.Enum (see module docstring).
# Kept as ints/str rather than a dict key, so canvas.bit_depth == 16
# reads naturally at every call site.
BIT_DEPTHS = (8, 10, 12, 16, "32f")

BIT_DEPTH_LABELS = {
    8: "8-bit",
    10: "10-bit",
    12: "12-bit",
    16: "16-bit",
    "32f": "32-bit float",
}

# A fixed picklist of standard broadcast/film/web rates -- backs an
# EDITABLE combo in the UI (Tom: canvas frame rate is part of Canvas
# Settings), not a hard restriction: any positive float is a valid
# CanvasSettings.frame_rate, this is just what's offered by default.
FRAME_RATE_PRESETS = (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0)

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_BIT_DEPTH = 8
DEFAULT_PIXEL_ASPECT_RATIO = 1.0
DEFAULT_FRAME_RATE = 24.0
# The project's working color space (Tom, 2026-10-05: project setup asks
# for "the edit rate, resolution, and working color space"). Not a
# conversion applied to media -- sources are still used as decoded --
# it's the space every new color-aware stage (Printer Lights, ACES/
# CAMier DRT, WARPier, Keyer) starts out assuming its input is in, so
# that choice is made once per project instead of once per stage. A name
# from aces_drt.list_working_colourspaces(); ACEScct matches the old
# per-stage defaults.
DEFAULT_WORKING_COLOURSPACE = "ACEScct"
# The project's display transform (colour_pipeline.py): applied after the
# Stack in the Record viewer. Off by default (per Tom); "aces" uses the
# ACES output transform for display/view below.
DEFAULT_DISPLAY_DRT = "none"
DISPLAY_DRTS = ("none", "aces")
DEFAULT_DISPLAY_NAME = "Rec.1886 Rec.709 - Display"
DEFAULT_DISPLAY_VIEW = "ACES 1.0 - SDR Video"


@dataclass(frozen=True)
class CanvasSettings:
    """Immutable -- callers that want to change one field do
    `dataclasses.replace(canvas, width=...)` (re-exported below as
    `replace_canvas`), same discipline stack.py's own Stage/Stack
    objects use for their own reassign-don't-mutate identity contract
    (see wipe_compare_widget._array_fingerprint's docstring on why that
    discipline matters for this app's id()-based change detection)."""
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    bit_depth: object = DEFAULT_BIT_DEPTH
    pixel_aspect_ratio: float = DEFAULT_PIXEL_ASPECT_RATIO
    frame_rate: float = DEFAULT_FRAME_RATE
    working_colourspace: str = DEFAULT_WORKING_COLOURSPACE
    display_drt: str = DEFAULT_DISPLAY_DRT
    display_name: str = DEFAULT_DISPLAY_NAME
    display_view: str = DEFAULT_DISPLAY_VIEW

    def __post_init__(self):
        if self.bit_depth not in BIT_DEPTHS:
            raise ValueError(f"Unknown bit_depth {self.bit_depth!r} (expected one of {BIT_DEPTHS})")
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"width/height must be positive, got {self.width}x{self.height}")
        if self.pixel_aspect_ratio <= 0:
            raise ValueError(f"pixel_aspect_ratio must be positive, got {self.pixel_aspect_ratio}")
        if self.frame_rate <= 0:
            raise ValueError(f"frame_rate must be positive, got {self.frame_rate}")

    @property
    def bit_depth_label(self):
        return BIT_DEPTH_LABELS[self.bit_depth]


DEFAULT_CANVAS = CanvasSettings()

# Re-exported so callers don't need a separate `from dataclasses import
# replace` alongside `from canvas import CanvasSettings` -- this module
# is the one place that should know CanvasSettings is a dataclass at all.
replace_canvas = replace


def canvas_from_first_media(width, height, frame_rate=None,
                            working_colourspace=DEFAULT_WORKING_COLOURSPACE):
    """The one auto-sync carve-out (see module docstring): called only
    when the FIRST still/video loads in a session AND the canvas is
    still untouched (`== DEFAULT_CANVAS`) -- seeds width/height/
    frame_rate from that source once, then the canvas is fully
    independent for the rest of the session. `frame_rate=None` (stills
    have none) leaves DEFAULT_FRAME_RATE in place rather than forcing a
    value that was never actually observed."""
    return CanvasSettings(
        width=width, height=height,
        frame_rate=frame_rate if frame_rate is not None else DEFAULT_FRAME_RATE,
        working_colourspace=working_colourspace)


def canvas_to_session_dict(canvas):
    return {
        "width": canvas.width, "height": canvas.height, "bit_depth": canvas.bit_depth,
        "pixel_aspect_ratio": canvas.pixel_aspect_ratio, "frame_rate": canvas.frame_rate,
        "working_colourspace": canvas.working_colourspace,
        "display_drt": canvas.display_drt,
        "display_name": canvas.display_name,
        "display_view": canvas.display_view,
    }


def canvas_from_session_dict(data):
    """`data=None` (older session files, or a missing key) -> DEFAULT_CANVAS,
    same "old file loads fine, just without the newer feature" convention
    session_io.py already follows for every prior version bump."""
    if not data:
        return DEFAULT_CANVAS
    return CanvasSettings(
        width=data.get("width", DEFAULT_WIDTH), height=data.get("height", DEFAULT_HEIGHT),
        bit_depth=data.get("bit_depth", DEFAULT_BIT_DEPTH),
        pixel_aspect_ratio=data.get("pixel_aspect_ratio", DEFAULT_PIXEL_ASPECT_RATIO),
        frame_rate=data.get("frame_rate", DEFAULT_FRAME_RATE),
        working_colourspace=data.get("working_colourspace", DEFAULT_WORKING_COLOURSPACE),
        display_drt=data.get("display_drt", DEFAULT_DISPLAY_DRT),
        display_name=data.get("display_name", DEFAULT_DISPLAY_NAME),
        display_view=data.get("display_view", DEFAULT_DISPLAY_VIEW),
    )
