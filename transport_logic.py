"""
transport_logic.py -- the Qt-free half of the professional transport
(Tom, 2026-10-05: "the transport and timeline panel need to feel
professional"): J/K/L shuttle state and typed timecode entry. The
viewers own the keys and the display; this module only decides speeds
and frame numbers, so it's testable on its own.

SHUTTLE (the Avid/Resolve convention): L plays forward, and each further
L doubles the speed (1x, 2x, 4x, 8x); J does the same in reverse; K
stops. While K is held, J or L steps a single frame instead. Pressing
the opposite direction while shuttling starts that direction at 1x.

TIMECODE ENTRY (typed into a viewer's timecode field):
  01:00:10:00 / 1:00:10:00  -> that timecode (a full HH:MM:SS:FF)
  10:00, 1000, 215          -> a PARTIAL timecode replaces the right-
                               hand digits of the CURRENT one (Avid/
                               Resolve): from 00:42:45:19, "1000" goes to
                               00:42:10:00 and "215" to 00:42:02:15
  +200 / -12 / +1:00        -> relative to the current frame (digits
                               right-aligned: +200 is +2 seconds)
Timecodes are as displayed -- the clip's own start TC included.
Separators may be ":", ";", "." or none.
"""
SHUTTLE_SPEEDS = (1, 2, 4, 8)


class Shuttle:
    """Signed shuttle speed: 0 stopped, +N forward, -N reverse."""

    def __init__(self):
        self.speed = 0

    def press_l(self, k_held=False):
        """Returns ("step", +1) while K is held, else ("speed", speed)."""
        if k_held:
            self.speed = 0
            return ("step", 1)
        self.speed = self._faster(self.speed) if self.speed > 0 else 1
        return ("speed", self.speed)

    def press_j(self, k_held=False):
        if k_held:
            self.speed = 0
            return ("step", -1)
        self.speed = -self._faster(-self.speed) if self.speed < 0 else -1
        return ("speed", self.speed)

    def press_k(self):
        self.speed = 0
        return ("speed", 0)

    @staticmethod
    def _faster(speed):
        for candidate in SHUTTLE_SPEEDS:
            if candidate > speed:
                return candidate
        return SHUTTLE_SPEEDS[-1]


def _digits_to_frames(digits, rate):
    """Right-aligned packed timecode digits ("1000" -> 10s 00f) -> frames."""
    digits = digits[-8:].rjust(8, "0")
    hours, minutes, seconds, frames = (int(digits[i:i + 2]) for i in range(0, 8, 2))
    fps = round(rate)
    return ((hours * 60 + minutes) * 60 + seconds) * fps + frames


def _fields_to_digits(text):
    """"1:00:10:00" -> "1001000" (each field after the first is 2 digits)."""
    fields = text.replace(";", ":").replace(".", ":").split(":")
    if not all(f.isdigit() for f in fields if f) or not any(fields):
        return None
    head, *rest = fields
    return head + "".join(f.rjust(2, "0") for f in rest)


def parse_timecode_entry(text, current_frame, rate, start_frame=0):
    """Frame number (0-based, in the same numbering as `current_frame`)
    for what was typed, or None if it isn't a timecode -- see the module
    docstring. `start_frame`: the absolute frame number of frame 0
    (a clip's embedded start TC), so typing a TC as displayed lands on
    it. Not clamped -- the caller clamps to its own range."""
    text = (text or "").strip()
    if not text or rate <= 0:
        return None
    sign = 0
    if text[0] in "+-":
        sign = 1 if text[0] == "+" else -1
        text = text[1:].strip()
    digits = _fields_to_digits(text)
    if not digits:
        return None
    frames = _digits_to_frames(digits, rate)
    if sign:
        return current_frame + sign * frames
    # Overlay the typed digits on the current displayed timecode.
    shown = _frames_to_digits(start_frame + current_frame, rate)
    overlaid = shown[:len(shown) - len(digits[-8:])] + digits[-8:]
    return _digits_to_frames(overlaid, rate) - start_frame


def _frames_to_digits(frame, rate):
    fps = round(rate)
    seconds, frames = divmod(max(0, frame), fps)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours % 100:02d}{minutes:02d}{seconds:02d}{frames:02d}"
