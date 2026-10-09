"""Tests for atomic_write.py -- run directly: python3 -m tests.test_atomic_write

Covers the actual bug this module fixes: a save that's interrupted
partway through must never destroy the previous good file at `path`.
"""
import os
import tempfile

import pytest

from atomic_write import atomic_open


def test_atomic_open_writes_the_given_content():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "out.txt")
        with atomic_open(path, "w") as f:
            f.write("hello")
        with open(path) as f:
            assert f.read() == "hello"


def test_atomic_open_leaves_no_temp_file_behind_on_success():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "out.txt")
        with atomic_open(path, "w") as f:
            f.write("hello")
        assert os.listdir(tmp_dir) == ["out.txt"]


def test_failed_write_does_not_touch_the_existing_file():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "out.txt")
        with open(path, "w") as f:
            f.write("original content")

        with pytest.raises(RuntimeError):
            with atomic_open(path, "w") as f:
                f.write("partial garbage")
                raise RuntimeError("simulated crash mid-write")

        with open(path) as f:
            assert f.read() == "original content"


def test_failed_write_removes_its_own_temp_file():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "out.txt")
        with pytest.raises(RuntimeError):
            with atomic_open(path, "w") as f:
                f.write("partial garbage")
                raise RuntimeError("simulated crash mid-write")
        assert os.listdir(tmp_dir) == []


def test_overwriting_an_existing_file_replaces_it_in_full():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "out.txt")
        with open(path, "w") as f:
            f.write("this is much longer than the replacement text")
        with atomic_open(path, "w") as f:
            f.write("short")
        with open(path) as f:
            assert f.read() == "short"
