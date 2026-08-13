"""
gpu_aces_drt_shader.py -- GPU path for "aces_drt" stages, replacing the
plain gpu_shader_cache.OcioGpuStageCache this kind used to share with
"colorspace".

REAL BUG FOUND AND FIXED (2026-08-11, Task #31: "HDR output color spaces
in ACES DRT operator broken... very milky in viewer, scope seems
correct"): every SDR display/view this app's own tests happened to
exercise needs ZERO GPU textures (confirmed directly -- pure baked-
constant curve/matrix math, e.g. 'sRGB - Display'/'Rec.1886 Rec.709 -
Display'), but every HDR display/view (ST2084-P3-D65, Rec.2100-PQ,
Rec.2100-HLG -- confirmed directly, all three) needs ONE real GPU
texture: a 4096x17 single-channel 2D texture OCIO packs its internal 1D
shaper LUT into ('ocio_lut1d_1Sampler', sampled via a helper function
OCIO's own codegen emits -- `ocio_lut1d_1_computePos`). gpu_stack_
renderer.py's aces_drt handling never bound ANY texture for this stage
kind (aces_drt/colorspace both went through gpu_shader_cache.
OcioGpuStageCache, which has no texture concept at all) -- so the shader
silently sampled an unbound (all-zero) texture for every HDR view,
producing severely compressed, nearly-uniform-gray output (confirmed
directly on a real image: GPU min/max/mean 0.204/0.518/0.467 vs. CPU's
correct 0.0/0.694/0.329) -- exactly the "very milky... almost 1 uniform
gray with no details" symptom reported, and exactly why Scopes (CPU-
computed, see sample_image_panel.py's own SCOPES_MAX_DIMENSION recompute)
looked correct while the Viewer (GPU-rendered) didn't -- two entirely
separate code paths, only one of them missing this texture bind.

Deliberately its OWN cache (NOT sharing gpu_shader_cache.
OcioGpuStageCache with "colorspace", which stays exactly as it was --
colorspace transforms have never been observed to need a texture in this
app's own builtin configs, confirmed directly, including via printer_
lights.py's own decode/encode ColorSpaceTransform pairs) -- same
"different GPU shape needs a different cache" precedent gpu_lut_shader.py
/gpu_printer_lights_shader.py already established elsewhere in this
project. No disk source-caching here either (unlike OcioGpuStageCache) --
texture DATA can't be meaningfully disk-cached without real complexity
for a documented ~0.14ms saving (see gpu_shader_cache.py's own docstring
on why that number is small enough not to chase), so this cache just
always regenerates fresh from OCIO on a cold IN-MEMORY miss, uploading
whatever textures (zero or more) that particular display/view's shader
actually needs.

FINGERPRINT -- (config_name, working_colourspace, display, view), same
as the fingerprint gpu_stack_renderer.py's own aces_drt handling already
used against the old OcioGpuStageCache -- these are PICKED values (a
combo box), not continuously dragged, so recompiling on a genuine change
and reusing otherwise is the right cost/benefit here, same reasoning
OcioGpuStageCache's own docstring gives for this stage kind.
"""
import numpy as np
import OpenGL.GL as gl

import gpu_shader_prototype as gsp


class _CacheEntry:
    __slots__ = ("program", "gl_textures")

    def __init__(self, program, gl_textures):
        self.program = program
        # [(gl_texture, sampler_name), ...] -- empty for every SDR
        # display/view confirmed so far, one entry for every HDR one (see
        # module docstring). Bound starting at GL_TEXTURE1 every render --
        # unit 0 is always uInput, see gpu_stack_renderer.py's own
        # _render_stage_pass.
        self.gl_textures = gl_textures


def _upload_2d_texture(values, width, height):
    """`values`: a (height, width) float32 ndarray, single channel (see
    gpu_shader_prototype._extract_ocio_glsl_with_2d_textures) -- uploaded
    as GL_R32F/GL_RED, matching the single-channel `.r` sample OCIO's own
    generated GLSL always reads back (confirmed directly: `texture2D(...)
    .r` in every case seen)."""
    values = np.ascontiguousarray(values, dtype=np.float32)
    gl_texture = gl.glGenTextures(1)
    gl.glBindTexture(gl.GL_TEXTURE_2D, gl_texture)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
    gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_R32F, width, height, 0, gl.GL_RED, gl.GL_FLOAT, values)
    gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
    return gl_texture


class AcesDrtGpuCache:
    """Compiled-program + uploaded-texture(s) cache for "aces_drt"
    stages, fingerprinted on (config_name, working_colourspace, display,
    view). Meant to live for the lifetime of one GL context, same
    convention as GpuStackRenderer's other per-kind caches -- see
    clear_gl_resources for teardown."""

    def __init__(self):
        self._entries = {}

    def get_entry(self, fingerprint, build_processor_fn):
        """`build_processor_fn`: a zero-arg callable returning a fresh
        ocio.Processor for `fingerprint` -- called ONLY on a cache miss
        (same "only pay the OCIO codegen cost once per distinct
        fingerprint" convention every other GPU cache in this app
        follows)."""
        entry = self._entries.get(fingerprint)
        if entry is not None:
            return entry

        processor = build_processor_fn()
        shader_text, textures_info = gsp._extract_ocio_glsl_with_2d_textures(processor)
        program = gsp.compile_ocio_program(shader_text)
        gl_textures = [(_upload_2d_texture(t["values"], t["width"], t["height"]), t["sampler_name"])
                       for t in textures_info]

        entry = _CacheEntry(program, gl_textures)
        self._entries[fingerprint] = entry
        return entry

    def clear(self):
        """Call before this cache's owning GL context goes away -- every
        compiled program/texture held here is only valid for that one
        context."""
        for entry in self._entries.values():
            gl.glDeleteProgram(entry.program)
            for gl_texture, _sampler_name in entry.gl_textures:
                gl.glDeleteTextures([gl_texture])
        self._entries.clear()
