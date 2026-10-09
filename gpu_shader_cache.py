"""
gpu_shader_cache.py -- docs/archive/GPU_ACCELERATION_PLAN_2026-08-08.md, STAGE 3.

Caches compiled GPU shader programs per distinct stage configuration, so
editing one stage doesn't force regenerating/recompiling every stage's
shader on every single edit -- the GPU-side equivalent of stack.py's own
StageCache (block-level result memoization for the CPU path), mirroring
its fingerprint-based "only redo what actually changed" design rather
than inventing a separate caching philosophy. Also implements the
disk-persisted "system library folder" of generated shader SOURCE (not
compiled binaries -- see WHY NOT COMPILED BINARIES below) Tom asked for,
per his own Resolve reference.

TWO DIFFERENT CACHING NEEDS, because Stage 2 found the two OCIO-backed
stage kinds this app uses interactively behave differently under OCIO's
GPU codegen (see docs/archive/GPU_ACCELERATION_PLAN_2026-08-08.md's Stage 2 findings):

- ACES DRT (and, by the same reasoning, plain colorspace stages): OCIO
  bakes working-colourspace/display/view as GLSL constants, and those
  values are PICKED, not continuously dragged -- exactly the shape
  StageCache/aces_drt.py's own lru_cache'd processors already assume.
  `OcioGpuStageCache` below fingerprints on those config values, and
  regenerates+recompiles only when the fingerprint actually changes.
- CDL: per Stage 2/gpu_cdl_shader.py, this app hand-writes CDL's shader
  with real uniforms specifically so slope/offset/power/sat/style edits
  NEVER need a fingerprint check or a recompile at all -- one compiled
  program, reused forever, values pushed via glUniform* every render.
  See `CdlGpuProgram` below -- deliberately not built on
  `OcioGpuStageCache`, since "cache by fingerprint" doesn't apply to a
  shader that's already constant.

WHY NOT COMPILED BINARIES ON DISK -- Tom's Resolve reference was
specifically about caching compiled shaders across sessions, not just
within one. This module caches generated GLSL SOURCE to disk, not
compiled GL program binaries, on purpose: `glGetProgramBinary`/
`glProgramBinary` exist, but the binary format is opaque, driver- and
GPU-specific, and support is inconsistent across drivers -- doubly so on
macOS, where OpenGL itself is deprecated in favor of Metal and Apple's
GL driver stack gets materially less investment than Windows/Linux
vendors'. Persisting compiled binaries here without first confirming
`glGetProgramBinary` actually round-trips reliably under this app's
exact GL context setup would be building on an unvalidated assumption.
Caching SOURCE still eliminates the OCIO-codegen step
(`getDefaultGPUProcessor()` -> `extractGpuShaderInfo()`, measured
~0.14ms in Stage 2) across restarts; the GL compile step (~0.95ms) still
has to happen at least once per process regardless. Revisit true binary
caching as a follow-up once/if the compile cost is shown to actually
matter in practice (see Stage 5's real benchmark under load) rather than
assumed to.
"""
import hashlib
from pathlib import Path

import gpu_shader_prototype as gsp
from workspace_io import DEFAULT_WORKSPACE_DIR

SHADER_CACHE_DIR = DEFAULT_WORKSPACE_DIR / "gpu_shader_cache"


def _fingerprint_key(fingerprint):
    """`fingerprint` (a plain tuple of strings/numbers, e.g. (config_name,
    working_colourspace, display, view)) -> a filesystem-safe cache key.
    Hashed rather than used as a literal path component since some of
    these values (an OCIO display/view name) can contain spaces/slashes
    ("sRGB - Display", "ACES 1.0 - SDR Video") that make for an awkward
    or unsafe filename verbatim."""
    raw = repr(fingerprint).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


class OcioGpuStageCache:
    """Per-(stage-kind) cache of compiled GPU programs for a stage kind
    whose shader is entirely determined by a small, rarely-changing
    fingerprint (ACES DRT: config/working-colourspace/display/view;
    colorspace: config/src/dst) -- see this module's docstring for why
    CDL is NOT one of these. One instance per stage kind (own set of
    compiled programs, own disk subfolder), same "independent caches for
    independent concerns" reasoning as stack.StageCache's own per-
    evaluation-path instances.

    `generate_glsl_fn(*fingerprint)` is supplied by the caller (e.g. a
    thin wrapper around aces_drt._get_output_transform_processor +
    gpu_shader_prototype._extract_ocio_glsl) -- this class has no OCIO
    knowledge of its own, only caching policy, same separation stack.py's
    StageCache keeps from the actual stage-apply functions it caches
    results of."""

    def __init__(self, cache_subdir, generate_glsl_fn):
        self._programs = {}  # fingerprint -> compiled GL program
        self._generate_glsl_fn = generate_glsl_fn
        self._disk_dir = SHADER_CACHE_DIR / cache_subdir

    def get_program(self, fingerprint):
        """Returns a compiled, ready-to-use GL program for `fingerprint`
        (whatever tuple this cache's owner considers this stage kind's
        shader-identity -- NOT including any value that's pushed as a
        uniform instead, since there are none for this stage kind, see
        module docstring). Compiles at most once per distinct fingerprint
        for this cache instance's lifetime; every repeat call for an
        already-seen fingerprint returns the SAME program object,
        uncompiled again."""
        if fingerprint in self._programs:
            return self._programs[fingerprint]

        glsl = self._read_disk_cache(fingerprint)
        if glsl is None:
            glsl = self._generate_glsl_fn(*fingerprint)
            self._write_disk_cache(fingerprint, glsl)

        program = gsp.compile_ocio_program(glsl)
        self._programs[fingerprint] = program
        return program

    def _read_disk_cache(self, fingerprint):
        path = self._disk_dir / f"{_fingerprint_key(fingerprint)}.glsl"
        try:
            return path.read_text()
        except OSError:
            return None

    def _write_disk_cache(self, fingerprint, glsl):
        """Best-effort, same "never let a cache write interrupt real
        work" convention as theme_prefs.save_accent_color/workspace_io's
        own save helpers -- a permissions error or full disk here should
        cost the next launch a ~0.14ms OCIO regeneration, not crash the
        stage that triggered it."""
        try:
            self._disk_dir.mkdir(parents=True, exist_ok=True)
            path = self._disk_dir / f"{_fingerprint_key(fingerprint)}.glsl"
            path.write_text(glsl)
        except OSError:
            pass

    def clear(self):
        """Drop every compiled program -- e.g. if the GL context itself
        is ever torn down/recreated (a compiled program handle is only
        valid for the context that compiled it, same reason StageCache.
        clear() exists for a wholesale Stack replacement). Does NOT touch
        the disk source cache -- that's still valid GLSL regardless of
        which GL context eventually compiles it again."""
        self._programs.clear()


class CdlGpuProgram:
    """The CDL equivalent of OcioGpuStageCache, but trivial by
    construction: gpu_cdl_shader.CDL_SHADER_SOURCE never changes (see
    that module's docstring), so there is nothing to fingerprint --
    compile once, lazily, on first use, and hand back the same program
    forever. Editing slope/offset/power/sat/style calls
    gpu_cdl_shader.set_cdl_uniforms on this same program every render;
    this class is not involved in that at all past handing out the
    program once."""

    def __init__(self):
        self._program = None

    def get_program(self):
        if self._program is None:
            import gpu_cdl_shader
            self._program = gsp.compile_ocio_program(gpu_cdl_shader.CDL_SHADER_SOURCE)
        return self._program

    def clear(self):
        self._program = None
