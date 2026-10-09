"""Tests for render_job.py -- run directly: python3 -m tests.test_render_job

Qt-free. Renders real (tiny) movies and sequences into a temp dir and
reads them back.
"""
import os
import tempfile

import av
import numpy as np

import image_io
import render_job as rj
import stack as st
import video_source

W, H = 40, 30


def _make_clip(path, frame_count=8, rate=24):
    container = av.open(path, mode="w")
    stream = container.add_stream("libx264", rate=rate)
    stream.width, stream.height, stream.pix_fmt = W, H, "yuv420p"
    for i in range(frame_count):
        frame = av.VideoFrame.from_ndarray(
            np.full((H, W, 3), (min(255, i * 30), 128, 64), dtype=np.uint8), format="rgb24")
        frame.pts = i
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def _segment(path, name="A001C001", in_frame=0, out_frame=3, kind="video", stack=None, reel="A001"):
    return rj.Segment(name, path, kind, in_frame, out_frame, stack or st.Stack(), reel=reel,
                      frame_rate=24.0, source_size=(W, H))


def _settings(directory, label="H.264 (.mp4)", depth=8, **kw):
    kw.setdefault("width", W)
    kw.setdefault("height", H)
    return rj.RenderSettings(label, depth, directory, **kw)


def test_expand_name_fills_tokens_and_makes_names_safe():
    tokens = {"name": "Reel 1", "clip": "A001C002", "reel": "A001", "n": "002", "date": "2026-10-09"}
    assert rj.expand_name("{reel}_{clip}_{date}", tokens) == "A001_A001C002_2026-10-09"
    assert rj.expand_name("a/b:{name}?", tokens) == "a_b_Reel 1_"
    assert rj.expand_name("{nope}", tokens) == "{nope}", "unknown tokens stay as written"
    assert rj.expand_name("", tokens) == "Reel 1"
    print("render_job: name templates -- OK")


def test_plan_single_file_vs_individual_clips_and_collisions():
    d = tempfile.mkdtemp()
    segs = [_segment("/m/a.mov", "A"), _segment("/m/b.mov", "B"), _segment("/m/c.mov", "A")]
    single = rj.plan_items("Reel 1", segs, _settings(d), "2026-10-09", timeline_frame_rate=25.0)
    assert len(single) == 1 and single[0].frame_count == 12
    assert single[0].output_path == os.path.join(d, "Reel 1.mp4")
    assert single[0].frame_rate == 25.0, "a timeline renders at its own rate"

    each = rj.plan_items("Reel 1", segs, _settings(d, single_file=False), "x", timeline_frame_rate=25.0)
    assert [i.name for i in each] == ["A", "B", "A_2"], "colliding names get a suffix"
    assert each[0].frame_rate == 24.0, "a clip on its own keeps its own rate"

    seq = rj.plan_items("R", segs[:1], _settings(d, "DPX sequence (.dpx)", 10), "x")
    assert seq[0].output_path == os.path.join(d, "R", "R.0000000.dpx")
    assert seq[0].frame_path(12) == os.path.join(d, "R", "R.0000012.dpx")

    sized = rj.plan_items("R", [_segment("/m/a.mov")], _settings(d, use_source_size=True,
                                                                  width=1920, height=1080), "x")
    assert (sized[0].width, sized[0].height) == (W, H)
    assert rj.format_bit_depths("H.264 (.mp4)") == (8,)
    assert rj.format_bit_depths("ProRes 4444 (.mov)") == (12, 10, 8)
    print("render_job: single file / individual clips / sequences / sizes -- OK")


def test_run_job_renders_a_graded_movie_across_two_segments():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "src.mp4")
    _make_clip(src)
    darken = st.Stack(stages=[st.make_cdl_stage(slope=(0.5, 0.5, 0.5))])
    segs = [_segment(src, "A", 0, 2), _segment(src, "B", 4, 6, stack=darken)]
    job = rj.RenderJob("Reel", rj.plan_items("Reel", segs, _settings(d), "x"))
    seen = []
    assert rj.run_job(job, progress=lambda done, total: seen.append((done, total))) == rj.DONE
    assert seen[-1] == (6, 6)
    out = video_source.VideoSource(job.items[0].output_path)
    try:
        assert out.frame_count == 6
        plain = out.seek_to_frame(0)
        graded = out.seek_to_frame(3)
    finally:
        out.close()
    source = video_source.VideoSource(src)
    try:
        ungraded_4 = source.seek_to_frame(4)
    finally:
        source.close()
    assert abs(float(plain[..., 1].mean()) - 128 / 255) < 0.05
    assert float(graded[..., 1].mean()) < float(ungraded_4[..., 1].mean()) * 0.7, "segment B is graded"
    print("render_job: two segments, each with its own grade, in one movie -- OK")


def test_run_job_writes_a_sequence_and_holds_a_still():
    d = tempfile.mkdtemp()
    still = os.path.join(d, "still.png")
    image_io.write_image(np.full((H, W, 3), 0.25, dtype=np.float32), still, 16)
    seg = _segment(still, "Still", 0, 2, kind="still")
    job = rj.RenderJob("S", rj.plan_items("S", [seg], _settings(d, "PNG sequence (.png)", 16), "x"))
    assert rj.run_job(job) == rj.DONE
    item = job.items[0]
    assert sorted(os.listdir(os.path.dirname(item.output_path))) == [
        "S.0000000.png", "S.0000001.png", "S.0000002.png"]
    back, depth = image_io.read_image_as_array(item.frame_path(2))
    assert depth == 16 and abs(float(back.mean()) - 0.25) < 1e-3
    assert rj.existing_outputs(job.items) == [os.path.dirname(item.output_path)]
    print("render_job: still held across a PNG sequence -- OK")


def test_run_job_cancel_and_failure():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "src.mp4")
    _make_clip(src)
    job = rj.RenderJob("C", rj.plan_items("C", [_segment(src, out_frame=7)], _settings(d), "x"))
    assert rj.run_job(job, should_cancel=lambda: job.frames_done >= 2) == rj.CANCELLED
    assert job.frames_done == 2 and os.path.exists(job.items[0].output_path)

    missing = rj.RenderJob("M", rj.plan_items("M", [_segment(os.path.join(d, "gone.mov"))],
                                              _settings(d), "x"))
    assert rj.run_job(missing) == rj.FAILED and missing.error
    print("render_job: cancel keeps what was written; a missing source fails the job -- OK")


if __name__ == "__main__":
    test_expand_name_fills_tokens_and_makes_names_safe()
    test_plan_single_file_vs_individual_clips_and_collisions()
    test_run_job_renders_a_graded_movie_across_two_segments()
    test_run_job_writes_a_sequence_and_holds_a_still()
    test_run_job_cancel_and_failure()
    print("\nAll render_job tests passed.")
