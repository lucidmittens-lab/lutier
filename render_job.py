"""
render_job.py -- render jobs for the Render suggested layout (2026-10-09):
what to render, where, and the decode -> grade -> write loop that does it.
Qt-free; render_queue_panel.py runs jobs on a background thread and
render_settings_panel.py builds the settings.

A RenderJob is one press of "Add to Render Queue". It holds one or more
RenderItems, one per output: a movie file, or a folder of numbered
frames. Each item plays one or more Segments in order: a source file's
in..out frames and the Stack to grade them with. Rendering a timeline as
a single file gives one item with a segment per clip; rendering it as
individual clips gives one item per clip.

WHAT YOU SEE IS WHAT RENDERS. Each segment's stack is the EFFECTIVE stack
Record renders (colour_pipeline.build_effective_stack: input transform,
operator spaces, display transform around clip + Sequence grades),
snapshotted when the job is queued. Editing the grade afterwards doesn't
change a queued job, like Resolve's render queue. "Working space" output
skips the display transform (for VFX pulls).

The CPU path only (stack.apply_stack_to_image_array): the CPU is the
truth, and it runs off the UI thread. Frames are resized to the item's
size the way Export Video always has (image_io.resize_array, a stretch;
the project canvas is normally the source's aspect). The stack clips to
[0,1], so 32-bit float output is display-range too.

Image sequences are written as <dir>/<name>/<name>.<frame>.<ext>, frames
numbered from 0 (the record frame), seven digits.
"""
import os
import re
from dataclasses import dataclass, field

import image_io
import id_counter
import stack as st
import video_export
import video_source

next_job_id = id_counter.make_next_entry_id()

# Job states.
QUEUED, RENDERING, DONE, FAILED, CANCELLED = "Queued", "Rendering", "Done", "Failed", "Cancelled"

# Output formats: video presets (video_export.CODEC_PRESETS, by label) and
# image sequences. bit_depths = what each can carry, best first.
SEQUENCE_FORMATS = {
    "EXR sequence (.exr)": {"extension": ".exr", "bit_depths": ("32f",)},
    "DPX sequence (.dpx)": {"extension": ".dpx", "bit_depths": (10, 12, 16)},
    "TIFF sequence (.tif)": {"extension": ".tif", "bit_depths": (16, 8, "32f")},
    "PNG sequence (.png)": {"extension": ".png", "bit_depths": (16, 8)},
}
FRAME_DIGITS = 7

DEFAULT_NAME_TEMPLATE = "{name}"
TEMPLATE_TOKENS = ("name", "timeline", "clip", "reel", "source", "n", "date")
_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def available_formats():
    """[(label, kind)] -- kind "video" or "sequence"; video presets this
    PyAV build can encode first."""
    return ([(p["label"], "video") for p in video_export.available_codec_presets()]
            + [(label, "sequence") for label in SEQUENCE_FORMATS])


def is_sequence_format(label):
    return label in SEQUENCE_FORMATS


def format_extension(label):
    if is_sequence_format(label):
        return SEQUENCE_FORMATS[label]["extension"]
    return video_export.get_codec_preset(label)["extension"]


def format_bit_depths(label):
    """The bit depths `label` can carry, best first."""
    if is_sequence_format(label):
        return SEQUENCE_FORMATS[label]["bit_depths"]
    ceiling = video_export.get_codec_preset(label)["max_bit_depth"]
    return tuple(d for d in (12, 10, 8) if d <= ceiling)


def expand_name(template, tokens):
    """`template` with {token}s filled from `tokens`, made filename-safe.
    Unknown tokens are left as written; an empty result falls back to
    tokens["name"]."""
    def fill(match):
        key = match.group(1)
        return str(tokens[key]) if key in tokens and tokens[key] is not None else match.group(0)
    name = re.sub(r"\{(\w+)\}", fill, template or "")
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip().strip(".")
    return name or _UNSAFE_FILENAME_CHARS.sub("_", str(tokens.get("name") or "render"))


@dataclass
class Segment:
    """`source_path`'s frames in_frame..out_frame (inclusive), graded by
    `stack`. A still is one decoded image held for frame_count frames."""
    name: str
    source_path: str
    media_kind: str
    in_frame: int
    out_frame: int
    stack: st.Stack
    reel: str = ""
    frame_rate: float = 24.0
    source_size: tuple = None   # (width, height), when known

    @property
    def frame_count(self):
        return max(0, self.out_frame - self.in_frame + 1)


@dataclass
class RenderSettings:
    format_label: str
    bit_depth: object
    directory: str
    name_template: str = DEFAULT_NAME_TEMPLATE
    single_file: bool = True          # several segments -> one output, or one each
    use_source_size: bool = False     # else the project canvas size
    width: int = 1920                 # the project canvas
    height: int = 1080
    pixel_aspect_ratio: float = 1.0


@dataclass
class RenderItem:
    name: str
    output_path: str                  # the movie file, or the sequence's first frame
    segments: list
    width: int
    height: int
    frame_rate: float
    format_label: str
    bit_depth: object
    pixel_aspect_ratio: float = 1.0

    @property
    def frame_count(self):
        return sum(s.frame_count for s in self.segments)

    @property
    def is_sequence(self):
        return is_sequence_format(self.format_label)

    def frame_path(self, frame):
        """A sequence item's path for record frame `frame`."""
        folder, first = os.path.split(self.output_path)
        stem = first[: -len(format_extension(self.format_label))].rsplit(".", 1)[0]
        return os.path.join(folder, f"{stem}.{frame:0{FRAME_DIGITS}d}{format_extension(self.format_label)}")


@dataclass
class RenderJob:
    name: str
    items: list
    id: int = field(default_factory=next_job_id)
    status: str = QUEUED
    frames_done: int = 0
    error: str = ""

    @property
    def frames_total(self):
        return sum(item.frame_count for item in self.items)

    @property
    def is_finished(self):
        return self.status in (DONE, FAILED, CANCELLED)

    def output_paths(self):
        return [item.output_path for item in self.items]

    def summary(self):
        """One line: what, how big, how long, in what."""
        first = self.items[0] if self.items else None
        if first is None:
            return "Nothing to render"
        outputs = f"{len(self.items)} files" if len(self.items) > 1 else "1 file"
        depth = f"{first.bit_depth}-bit" if first.bit_depth != "32f" else "32-bit float"
        return (f"{outputs} · {self.frames_total} frames · {first.width}x{first.height} · "
                f"{first.format_label.split(' (')[0]} {depth}")


def plan_items(name, segments, settings, date_text, timeline_frame_rate=None):
    """The RenderItems for `segments` under `settings`. `name` is the
    timeline's (or the single clip's) name; `date_text` fills {date}.
    One item for all segments when settings.single_file, else one per
    segment. Output names that collide get _2, _3..."""
    if not segments:
        return []
    groups = [segments] if settings.single_file else [[s] for s in segments]
    extension = format_extension(settings.format_label)
    sequence = is_sequence_format(settings.format_label)
    items, used = [], set()
    for index, group in enumerate(groups, start=1):
        first = group[0]
        tokens = {
            "name": name if settings.single_file else first.name,
            "timeline": name,
            "clip": first.name,
            "reel": first.reel,
            "source": os.path.splitext(os.path.basename(first.source_path))[0],
            "n": f"{index:03d}",
            "date": date_text,
        }
        stem = expand_name(settings.name_template, tokens)
        unique, suffix = stem, 2
        while unique.lower() in used:
            unique, suffix = f"{stem}_{suffix}", suffix + 1
        used.add(unique.lower())
        if sequence:
            output = os.path.join(settings.directory, unique, f"{unique}.{0:0{FRAME_DIGITS}d}{extension}")
        else:
            output = os.path.join(settings.directory, unique + extension)
        width, height = settings.width, settings.height
        if settings.use_source_size and first.source_size:
            width, height = first.source_size
        # A timeline plays at its own rate; a clip on its own keeps its own.
        rate = timeline_frame_rate if (settings.single_file and timeline_frame_rate) else first.frame_rate
        items.append(RenderItem(
            name=unique, output_path=output, segments=list(group), width=int(width),
            height=int(height), frame_rate=float(rate), format_label=settings.format_label,
            bit_depth=settings.bit_depth, pixel_aspect_ratio=settings.pixel_aspect_ratio))
    return items


def existing_outputs(items):
    """Outputs that already exist on disk (a sequence counts when its
    folder holds any of its frames)."""
    found = []
    for item in items:
        if item.is_sequence:
            folder = os.path.dirname(item.output_path)
            prefix = os.path.basename(item.frame_path(0)).rsplit(".", 2)[0] + "."
            if os.path.isdir(folder) and any(f.startswith(prefix) for f in os.listdir(folder)):
                found.append(folder)
        elif os.path.exists(item.output_path):
            found.append(item.output_path)
    return found


def native_size(path, media_kind):
    """(width, height) of a source file, or None if it can't be read."""
    try:
        if media_kind == "video":
            source = video_source.VideoSource(path)
            try:
                return source.width, source.height
            finally:
                source.close()
        return image_io.get_native_size(path)
    except Exception:  # noqa: BLE001 -- unknown size just means "use the project's"
        return None


class _SequenceWriter:
    def __init__(self, item):
        self._item = item
        self._frame = 0
        os.makedirs(os.path.dirname(item.output_path), exist_ok=True)

    def write_frame(self, array):
        image_io.write_image(array, self._item.frame_path(self._frame), self._item.bit_depth)
        self._frame += 1

    def close(self):
        pass


def _open_writer(item):
    if item.is_sequence:
        return _SequenceWriter(item)
    directory = os.path.dirname(item.output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    return video_export.VideoEncoder(
        item.output_path, item.width, item.height, item.frame_rate,
        video_export.get_codec_preset(item.format_label), bit_depth=item.bit_depth,
        pixel_aspect_ratio=item.pixel_aspect_ratio)


def _fit(array, item):
    if array.shape[1] != item.width or array.shape[0] != item.height:
        array = image_io.resize_array(array, item.width, item.height)
    return array


def _segment_frames(segment, item):
    """Yields each of `segment`'s graded frames, sized for `item`."""
    if segment.media_kind == "video":
        source = video_source.VideoSource(segment.source_path)
        try:
            for i in range(segment.frame_count):
                array = (source.seek_to_frame(segment.in_frame) if i == 0
                         else source.decode_next_frame())
                if array is None:
                    raise RuntimeError(
                        f"{segment.name}: the source ended at frame {segment.in_frame + i}")
                yield st.apply_stack_to_image_array(segment.stack, _fit(array, item))
        finally:
            source.close()
    else:
        array, _depth = image_io.read_image_as_array(segment.source_path)
        graded = st.apply_stack_to_image_array(segment.stack, _fit(array, item))
        for _ in range(segment.frame_count):
            yield graded


def run_job(job, progress=None, should_cancel=None):
    """Renders every item of `job`, in order, and sets job.status (DONE,
    CANCELLED or FAILED with job.error). `progress(frames_done,
    frames_total)` after each frame; `should_cancel()` is checked before
    each. A cancelled or failed movie is still closed, so what was
    written so far plays. Safe to call from any thread."""
    job.status, job.frames_done, job.error = RENDERING, 0, ""
    total = job.frames_total
    try:
        for item in job.items:
            writer = _open_writer(item)
            try:
                for segment in item.segments:
                    for graded in _segment_frames(segment, item):
                        if should_cancel is not None and should_cancel():
                            job.status = CANCELLED
                            return job.status
                        writer.write_frame(graded)
                        job.frames_done += 1
                        if progress is not None:
                            progress(job.frames_done, total)
            finally:
                writer.close()
    except Exception as e:  # noqa: BLE001 -- the queue shows any failure on the job
        job.status, job.error = FAILED, str(e) or type(e).__name__
        return job.status
    job.status = DONE
    return job.status
