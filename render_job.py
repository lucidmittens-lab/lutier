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

GPU OR CPU. run_job grades on the GPU when handed a
gpu_frame_renderer.GpuFrameRenderer and the segment's stack is
GPU-eligible, and on the CPU (stack.apply_stack_to_image_array, the
truth the GPU is tested against) otherwise. Either way decode runs a few
frames ahead on its own thread and writing a few frames behind on
another, so decode, grade and encode overlap. Frames are resized to the
item's size before grading, the way Export Video always has
(image_io.resize_array, a stretch; the project canvas is normally the
source's aspect; the GPU's Lanczos matches it). Output is clipped to
[0,1], so 32-bit float output is display-range too.

Image sequences are written as <dir>/<name>/<name>.<frame>.<ext>, frames
numbered from 0 (the record frame), seven digits.
"""
import os
import queue
import re
import threading
from dataclasses import dataclass, field

import id_counter
import image_io
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
    engine: str = ""   # "GPU", "CPU" or "GPU + CPU", once rendering
    prefer_gpu: bool = True

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


# Frames decoded ahead of grading, and graded frames waiting to be
# written: enough to keep decode, grade and encode all busy at once
# without holding much memory (a 4K float frame is ~100 MB).
PIPELINE_DEPTH = 3
_END = object()


def _decoded_frames(segment, raw):
    """Yields `segment`'s source frames, in order (a still: its one image,
    once). `raw` = the decoder's uint16 samples, for the GPU."""
    if segment.media_kind != "video":
        yield image_io.read_image_as_array(segment.source_path)[0]
        return
    source = video_source.VideoSource(segment.source_path)
    try:
        for i in range(segment.frame_count):
            array = (source.seek_to_frame(segment.in_frame, raw=raw) if i == 0
                     else source.decode_next_frame(raw=raw))
            if array is None:
                raise RuntimeError(
                    f"{segment.name}: the source ended at frame {segment.in_frame + i}")
            yield array
    finally:
        source.close()


def _prefetch(frames, depth=PIPELINE_DEPTH):
    """Runs the `frames` generator on its own thread, `depth` frames
    ahead; re-raises its errors here. Closing this generator stops it."""
    out = queue.Queue(maxsize=depth)
    stop = threading.Event()

    def produce():
        try:
            for frame in frames:
                while not stop.is_set():
                    try:
                        out.put(frame, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                if stop.is_set():
                    break
            item = _END
        except BaseException as e:  # noqa: BLE001 -- handed to the consumer
            item = e
        finally:
            frames.close()
        while not stop.is_set():
            try:
                out.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    thread = threading.Thread(target=produce, name="render-decode", daemon=True)
    thread.start()
    try:
        while True:
            item = out.get()
            if item is _END:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        thread.join()


class _BackgroundWriter:
    """Writes frames on its own thread, `depth` behind; write errors are
    raised from the next put() or finish()."""

    def __init__(self, writer, depth=PIPELINE_DEPTH):
        self._writer = writer
        self._queue = queue.Queue(maxsize=depth)
        self._error = None
        self._thread = threading.Thread(target=self._run, name="render-encode", daemon=True)
        self._thread.start()

    def _run(self):
        while True:
            frame = self._queue.get()
            if frame is _END:
                return
            if self._error is None:
                try:
                    self._writer.write_frame(frame)
                except BaseException as e:  # noqa: BLE001 -- raised on the render thread
                    self._error = e

    def put(self, frame):
        if self._error is not None:
            raise self._error
        self._queue.put(frame)

    def finish(self):
        """Waits for every queued frame, then closes the writer."""
        self._queue.put(_END)
        self._thread.join()
        self._writer.close()
        if self._error is not None:
            raise self._error


def run_job(job, progress=None, should_cancel=None, gpu=None):
    """Renders every item of `job`, in order, and sets job.status (DONE,
    CANCELLED or FAILED with job.error). `progress(frames_done,
    frames_total)` after each frame; `should_cancel()` is checked before
    each. A cancelled or failed movie is still closed, so what was
    written so far plays.

    `gpu` (a gpu_frame_renderer.GpuFrameRenderer, current on THIS thread)
    grades every segment whose stack it can_render; the rest, or all of
    it with no `gpu`, grade on the CPU. job.engine says which ran. Decode
    and writing run on threads of their own either way, so the three
    overlap. Call from any one thread (the GPU's, with `gpu`)."""
    job.status, job.frames_done, job.error = RENDERING, 0, ""
    engines = []
    total = job.frames_total
    try:
        for item in job.items:
            output = _BackgroundWriter(_open_writer(item))
            try:
                for segment in item.segments:
                    on_gpu = gpu is not None and gpu.can_render(segment.stack)
                    engine = "GPU" if on_gpu else "CPU"
                    if engine not in engines:
                        engines.append(engine)
                        job.engine = " + ".join(engines)
                    held = None   # a still is graded once and held
                    frames = _prefetch(_decoded_frames(segment, raw=on_gpu))
                    try:
                        for index in range(segment.frame_count):
                            if should_cancel is not None and should_cancel():
                                job.status = CANCELLED
                                return job.status
                            if held is None:
                                frame = next(frames, None)
                                if frame is None:
                                    raise RuntimeError(f"{segment.name}: no frame {index}")
                                if on_gpu:
                                    graded = gpu.render(segment.stack, frame, item.width, item.height)
                                else:
                                    graded = st.apply_stack_to_image_array(segment.stack, _fit(frame, item))
                                if segment.media_kind != "video":
                                    held = graded
                            else:
                                graded = held
                            output.put(graded)
                            job.frames_done += 1
                            if progress is not None:
                                progress(job.frames_done, total)
                    finally:
                        frames.close()
            finally:
                output.finish()
    except Exception as e:  # noqa: BLE001 -- the queue shows any failure on the job
        job.status, job.error = FAILED, str(e) or type(e).__name__
        return job.status
    job.status = DONE
    return job.status
