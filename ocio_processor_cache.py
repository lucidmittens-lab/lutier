"""
ocio_processor_cache.py -- shared cache for OCIO CPUProcessors, keyed on
a transform's own SCALAR parameters, not the transform object itself (a
fresh ocio.XxxTransform instance every call isn't stably hashable/reusable
as a cache key).

Fixes a real perf bug (Tom, 2026-08-13, found while scoping video
export): cdl.py, grading_primary.py, exposure_contrast.py,
grading_tone.py, and grading_rgb_curve.py each called
cfg.getProcessor(transform) + .getDefaultCPUProcessor() FRESH on every
single call to their own _apply_ocio_transform, even for the exact same
params repeated back-to-back -- expensive, and the one piece those five
modules' own `lru_cache(maxsize=1)`-cached config (cdl._get_raw_config)
never covered (that only caches ocio.Config.CreateRaw() itself, not the
Processor/CPUProcessor built FROM a transform against it). A real
per-frame CPU-path video export would otherwise re-pay this cost once
per stage per frame across potentially thousands of frames.

Same rule as every other backend module in this app: no PySide6 import
here, fully testable without a display.
"""
_cache = {}


def get_cpu_processor(cfg, cache_key, build_transform):
    """Returns a cached ocio CPUProcessor for the transform
    `build_transform()` would construct against `cfg` -- `build_transform`
    is a zero-arg callable, invoked ONLY on a cache miss (so building the
    actual ocio.XxxTransform object stays deferred, not wasted work on a
    hit). `cache_key` must be a hashable value uniquely identifying that
    transform's own scalar parameters (e.g. a tuple of every arg the
    caller's own build_XXX_transform would be called with) -- this module
    has no way to verify that itself, callers own getting it right (see
    each of the five modules' own _apply_ocio_transform for the exact key
    each passes). Keyed on (cfg, cache_key) together, not cache_key
    alone -- cfg's own default identity-based hash is enough to keep
    different configs from colliding, without this module needing any
    config-specific knowledge."""
    key = (cfg, cache_key)
    cpu = _cache.get(key)
    if cpu is None:
        transform = build_transform()
        processor = cfg.getProcessor(transform)
        cpu = processor.getDefaultCPUProcessor()
        _cache[key] = cpu
    return cpu


def clear():
    """Drops every cached CPUProcessor -- tests use this between runs to
    confirm whether a given call actually built (cache miss) or reused
    (cache hit) a processor. No production code needs to call this: a
    given (cfg, cache_key) pair always means the identical transform, so
    the cache never goes stale on its own."""
    _cache.clear()
