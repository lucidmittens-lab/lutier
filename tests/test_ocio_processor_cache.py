"""Tests for ocio_processor_cache.py -- run directly:
python3 -m tests.test_ocio_processor_cache

Requires opencolorio (pip3 install opencolorio) -- skips with a clear
message rather than failing if it isn't installed, same convention as
test_aces_drt.py/test_cdl.py.
"""
import numpy as np

try:
    import PyOpenColorIO as ocio
    OCIO_AVAILABLE = True
except ImportError:
    ocio = None
    OCIO_AVAILABLE = False

import ocio_processor_cache


def _skip_if_unavailable():
    if not OCIO_AVAILABLE:
        print("SKIPPED all ocio_processor_cache tests -- PyOpenColorIO not "
              "installed (pip3 install opencolorio)")
        return True
    return False


def _build_calls(counter, slope):
    """A build_transform callable that records how many times it was
    actually invoked -- the whole point under test is that a cache HIT
    must never call this."""
    def build():
        counter.append(1)
        transform = ocio.CDLTransform(slope=slope)
        return transform
    return build


def test_cache_hit_never_calls_build_transform_again():
    ocio_processor_cache.clear()
    cfg = ocio.Config.CreateRaw()
    counter = []
    build = _build_calls(counter, (1.5, 1.0, 1.0))

    cpu1 = ocio_processor_cache.get_cpu_processor(cfg, ("key", 1), build)
    assert len(counter) == 1, "first call (a miss) must build exactly once"

    cpu2 = ocio_processor_cache.get_cpu_processor(cfg, ("key", 1), build)
    assert len(counter) == 1, "second call with the SAME key must be a hit -- no rebuild"
    assert cpu1 is cpu2, "a cache hit must return the exact same CPUProcessor object"
    print("ocio_processor_cache.get_cpu_processor: a repeated call with the "
          "same key never rebuilds -- OK")


def test_different_cache_keys_build_independently():
    ocio_processor_cache.clear()
    cfg = ocio.Config.CreateRaw()
    counter = []

    cpu_a = ocio_processor_cache.get_cpu_processor(
        cfg, ("key", "a"), _build_calls(counter, (1.5, 1.0, 1.0)))
    cpu_b = ocio_processor_cache.get_cpu_processor(
        cfg, ("key", "b"), _build_calls(counter, (0.5, 1.0, 1.0)))
    assert len(counter) == 2, "two DIFFERENT keys must each build once"
    assert cpu_a is not cpu_b

    # Re-requesting "a" must hit its own cache entry, not rebuild.
    cpu_a_again = ocio_processor_cache.get_cpu_processor(
        cfg, ("key", "a"), _build_calls(counter, (1.5, 1.0, 1.0)))
    assert len(counter) == 2
    assert cpu_a_again is cpu_a
    print("ocio_processor_cache.get_cpu_processor: different cache keys "
          "build and cache independently -- OK")


def test_different_configs_do_not_collide_on_the_same_key():
    ocio_processor_cache.clear()
    cfg1 = ocio.Config.CreateRaw()
    cfg2 = ocio.Config.CreateRaw()  # a genuinely different Config object
    counter = []

    cpu1 = ocio_processor_cache.get_cpu_processor(
        cfg1, ("key",), _build_calls(counter, (1.0, 1.0, 1.0)))
    cpu2 = ocio_processor_cache.get_cpu_processor(
        cfg2, ("key",), _build_calls(counter, (1.0, 1.0, 1.0)))
    assert len(counter) == 2, "the SAME cache_key against DIFFERENT configs must not collide"
    assert cpu1 is not cpu2
    print("ocio_processor_cache.get_cpu_processor: different Config objects "
          "never collide on an identical cache_key -- OK")


def test_cached_processor_produces_the_correct_result():
    """Not just "doesn't crash" -- the cached CPUProcessor must actually
    apply the real transform correctly, same as an uncached one would."""
    ocio_processor_cache.clear()
    cfg = ocio.Config.CreateRaw()
    transform = ocio.CDLTransform(slope=(2.0, 1.0, 1.0))
    cpu = ocio_processor_cache.get_cpu_processor(cfg, ("k",), lambda: transform)

    pixel = np.array([[0.25, 0.25, 0.25]], dtype=np.float32)
    desc = ocio.PackedImageDesc(pixel, 1, 1, 3)
    cpu.apply(desc)
    np.testing.assert_allclose(pixel[0], [0.5, 0.25, 0.25], atol=1e-6)
    print("ocio_processor_cache.get_cpu_processor: the cached CPUProcessor "
          "actually applies the correct transform -- OK")


def test_clear_forces_a_fresh_build_on_the_next_call():
    cfg = ocio.Config.CreateRaw()
    counter = []
    build = _build_calls(counter, (1.0, 1.0, 1.0))
    ocio_processor_cache.get_cpu_processor(cfg, ("k",), build)
    assert len(counter) == 1

    ocio_processor_cache.clear()
    ocio_processor_cache.get_cpu_processor(cfg, ("k",), build)
    assert len(counter) == 2, "after clear(), even the SAME key must rebuild"
    print("ocio_processor_cache.clear: forces a fresh build on the next "
          "call, even for a previously-cached key -- OK")


if __name__ == "__main__":
    if not _skip_if_unavailable():
        test_cache_hit_never_calls_build_transform_again()
        test_different_cache_keys_build_independently()
        test_different_configs_do_not_collide_on_the_same_key()
        test_cached_processor_produces_the_correct_result()
        test_clear_forces_a_fresh_build_on_the_next_call()
        print()
        print("All ocio_processor_cache tests passed.")
