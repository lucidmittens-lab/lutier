"""
gpu_lut_shader.py -- GPU_ACCELERATION_PLAN_2026-08-08.md, STAGE 4.

Routes a plain (non-OCIO) "lut" stage's 3D LUT through OCIO's own
Lut3DTransform GPU codegen, rather than hand-writing a 3D-texture
sampler -- OCIO already solves the half-texel/coordinate-remap
subtleties a real GPU 3D-LUT sampler needs to get right (confirmed
directly, not assumed: a GPU render through this exact path matched
colour.LUT3D.apply() -- the CPU function bake_stack_to_lut3d/
apply_stack_to_image_array actually use -- to within 5.4e-4 on a random
perturbed LUT, and OCIO's own CPU processor for the SAME Lut3DTransform
matched colour.LUT3D.apply() to 1.4e-7, confirming OCIO's default
INTERP_LINEAR is genuinely the same trilinear interpolation
colour.LUT3D.apply()'s own default interpolator uses, not just similar).

DOMAIN SUPPORT (2026-08-10 follow-up) -- any IMPLICIT domain (a plain
[min, max] pair, per colour.LUT3D.is_domain_explicit()'s own distinction
-- not a real-world edge case: LogC-range grading cubes routinely ship
with a domain like [[0,0,0], [1.79,1.79,1.79]] rather than [0,1], and
this was found live -- a real cube hit exactly this and fell back to
CPU) is handled by wrapping OCIO's own generated Lut3DTransform shader
function in one extra function that does the SAME per-channel linear
remap into [0,1] colour.LUT3D.apply()'s own domain handling does
(confirmed directly: GPU output through this wrapped shader matched
colour.LUT3D.apply() with a real LogC-shaped domain to 3.7e-4, same
tolerance as the default-domain case). Baked as GLSL literal constants
at compile time (fingerprinted on id(lut), so a different domain always
means a different LUT object anyway, no separate cache key needed).

EXACT INTERPOLATION (2026-10-05) -- OCIO's generated lookup is one
texture3D() call with GL_LINEAR filtering, i.e. the GPU's own
fixed-function trilinear. On NVIDIA (RTX 3080 Ti) that filter computes
its blend weights in ~8-bit fixed point, which put a random 9^3 LUT
1.1e-3 away from colour.LUT3D.apply() -- over this module's own 1e-3
validation bar, and a real (if usually invisible) grading error on any
LUT with steep neighbouring lattice values. Apple's GL and Mesa's
llvmpipe filter in full float, which is why it never showed before.
So the lookup itself is now this module's own GLSL (_exact_lut3d_glsl):
the SAME texture OCIO built (same layout, same .zyx axis order), but
sampled GL_NEAREST at exact lattice points and blended in shader float
math -- 8 fetches instead of 1, cheap at preview resolution. OCIO still
does the texture packing; only the sampling changed.

SCOPE LIMIT (documented, not an oversight): only a plain colour.LUT3D,
with an IMPLICIT domain (see above) and no retarget_from/retarget_to
wrapping, is GPU-eligible here -- see is_lut3d_gpu_eligible and
gpu_stack_renderer.is_stack_gpu_eligible's own "lut" special case. A
colour.LUT1D/LUT3x1D lut object, an EXPLICIT (per-sample) domain, or a
retarget wrapper (retarget_io) -- each would need its own additional
shader work this module doesn't do yet -- makes the WHOLE containing
stack fall back to the existing CPU path, same as any other GPU-
ineligible stage kind. Nothing about that fallback is new; it's the
exact behavior every "lut" stage had before this module existed, just no
longer true for the (now much wider) common case this module handles.

CACHING -- fingerprinted on id(lut), matching stack._stage_fingerprint's
own established convention for "lut" stages: "loaded/baked LUT objects
are never mutated in place anywhere in this app," so identity is a
safe, sufficient cache key -- no need to fingerprint the table's actual
VALUES (which would be expensive to hash and pointless to compare, since
identity already answers "is this really the same LUT" correctly here).
Unlike the Tier 1 dynamic-uniform kinds (gpu_ocio_dynamic.py), a LUT's
table isn't live-editable via any UI in this app -- only enabled/
opacity/retarget change per-edit, none of which touch the compiled
program or uploaded texture -- so there's no dynamic-property mechanism
needed here at all, just a compiled-program + texture cache.
"""
import numpy as np
import OpenGL.GL as gl
import PyOpenColorIO as ocio
import colour

import gpu_shader_prototype as gsp


def is_lut3d_gpu_eligible(lut):
    """True for a plain colour.LUT3D with an IMPLICIT domain (any [min,
    max] pair, not just the default [0,1] -- see module docstring's
    DOMAIN SUPPORT). False for a LUT1D/LUT3x1D, or an EXPLICIT (per-
    sample) domain -- see SCOPE LIMIT for what's still excluded and why."""
    if not isinstance(lut, colour.LUT3D):
        return False
    return not lut.is_domain_explicit()


def _exact_lut3d_glsl(sampler_name, size):
    """OCIORawLut3D with float-precision trilinear interpolation -- see
    module docstring's EXACT INTERPOLATION. Same contract as OCIO's own
    generated function: input already normalized to [0,1] (out-of-range
    clamps to the edge, as GL_CLAMP_TO_EDGE did before), texture axes in
    .zyx order."""
    n = float(size)
    return f"""
uniform sampler3D {sampler_name};

vec4 OCIORawLut3D(vec4 inPixel) {{
    const float N = {n!r};
    vec3 p = clamp(inPixel.zyx, 0.0, 1.0) * (N - 1.0);
    vec3 i0 = min(floor(p), vec3(N - 2.0));  // keep i0+1 in range; p == N-1 gives f == 1
    vec3 f = p - i0;
    vec3 a = (i0 + 0.5) / N;  // lattice point centers
    vec3 b = (i0 + 1.5) / N;
    vec3 c000 = texture3D({sampler_name}, vec3(a.x, a.y, a.z)).rgb;
    vec3 c100 = texture3D({sampler_name}, vec3(b.x, a.y, a.z)).rgb;
    vec3 c010 = texture3D({sampler_name}, vec3(a.x, b.y, a.z)).rgb;
    vec3 c110 = texture3D({sampler_name}, vec3(b.x, b.y, a.z)).rgb;
    vec3 c001 = texture3D({sampler_name}, vec3(a.x, a.y, b.z)).rgb;
    vec3 c101 = texture3D({sampler_name}, vec3(b.x, a.y, b.z)).rgb;
    vec3 c011 = texture3D({sampler_name}, vec3(a.x, b.y, b.z)).rgb;
    vec3 c111 = texture3D({sampler_name}, vec3(b.x, b.y, b.z)).rgb;
    vec3 c00 = mix(c000, c100, f.x);
    vec3 c10 = mix(c010, c110, f.x);
    vec3 c01 = mix(c001, c101, f.x);
    vec3 c11 = mix(c011, c111, f.x);
    vec3 c = mix(mix(c00, c10, f.y), mix(c01, c11, f.y), f.z);
    return vec4(c, inPixel.a);
}}
"""


def _domain_wrapper_glsl(domain):
    """The GLSL 'OCIOMain' _FRAGMENT_SHADER_TEMPLATE actually calls --
    remaps into [0,1] via `domain`, then delegates to OCIO's own
    OCIORawLut3D. `domain` is a colour.LUT3D's own 2-row [min, max]
    array (validated implicit by the caller, see is_lut3d_gpu_eligible)
    -- baked as literal float constants, not uniforms, since a different
    domain always means a different `lut` object (and therefore a
    different cache fingerprint/compile) anyway, see module docstring's
    CACHING section."""
    domain = np.asarray(domain, dtype=np.float64)
    # float(...) -- NOT just indexing -- numpy >=2.0's own scalar repr()
    # is "np.float64(0.0)", not valid GLSL; plain Python float()s repr()
    # the way this f-string needs.
    dmin = [float(v) for v in domain[0]]
    dmax = [float(v) for v in domain[1]]
    return f"""
vec4 OCIOMain(vec4 inPixel) {{
    vec3 domainMin = vec3({dmin[0]!r}, {dmin[1]!r}, {dmin[2]!r});
    vec3 domainMax = vec3({dmax[0]!r}, {dmax[1]!r}, {dmax[2]!r});
    vec3 normalized = (inPixel.rgb - domainMin) / (domainMax - domainMin);
    return OCIORawLut3D(vec4(normalized, inPixel.a));
}}
"""


class _CacheEntry:
    __slots__ = ("program", "gl_texture", "sampler_name", "gpu_processor")

    def __init__(self, program, gl_texture, sampler_name, gpu_processor):
        self.program = program
        self.gl_texture = gl_texture
        self.sampler_name = sampler_name
        # Kept alive alongside the compiled program for the same reason
        # gpu_ocio_dynamic.py keeps its own gpu_processor alive -- cheap
        # insurance against relying on undocumented Python/C++ object
        # lifetime guarantees in the OCIO bindings.
        self.gpu_processor = gpu_processor


class Lut3DGpuCache:
    """Compiled-program + uploaded-3D-texture cache for "lut" stages,
    fingerprinted on id(lut) -- see module docstring's CACHING section.
    Meant to live for the lifetime of one GL context, same convention as
    GpuStackRenderer's other per-kind caches (gpu_shader_cache.py,
    gpu_ocio_dynamic.py) -- see clear_gl_resources for teardown."""

    def __init__(self):
        self._entries = {}

    def get_entry(self, lut):
        fingerprint = id(lut)
        entry = self._entries.get(fingerprint)
        if entry is not None:
            return entry

        size = lut.size
        table = np.ascontiguousarray(lut.table, dtype=np.float64)

        ocio_lut = ocio.Lut3DTransform(gridSize=size)
        ocio_lut.setInterpolation(ocio.INTERP_LINEAR)  # matches colour.LUT3D.apply()'s own trilinear default
        for ri in range(size):
            for gi in range(size):
                for bi in range(size):
                    r, g, b = table[ri, gi, bi]
                    ocio_lut.setValue(ri, gi, bi, float(r), float(g), float(b))

        cfg = ocio.Config.CreateRaw()  # bare config -- Lut3DTransform never references a named colorspace
        gpu_processor = cfg.getProcessor(ocio_lut).getDefaultGPUProcessor()
        shader_desc = ocio.GpuShaderDesc.CreateShaderDesc(language=ocio.GPU_LANGUAGE_GLSL_1_2)
        # Named OCIORawLut3D, not OCIOMain -- this module's own OCIOMain
        # (appended below) wraps it with the domain-normalization step
        # colour.LUT3D.apply() itself does before indexing (see module
        # docstring's DOMAIN SUPPORT) -- OCIO's own Lut3DTransform codegen
        # has no domain concept at all, it only ever samples an input
        # already normalized to [0,1].
        shader_desc.setFunctionName("OCIORawLut3D")
        gpu_processor.extractGpuShaderInfo(shader_desc)

        tex3d_list = list(shader_desc.get3DTextures())
        assert len(tex3d_list) == 1, (  # pragma: no cover -- OCIO's own Lut3DTransform codegen shape
            f"expected exactly one 3D texture for a single Lut3DTransform, got {len(tex3d_list)}")
        tex3d = tex3d_list[0]
        values = np.asarray(tex3d.getValues(), dtype=np.float32)
        edge = tex3d.edgeLen

        # OCIO's texture packing, this module's own lookup -- see module
        # docstring's EXACT INTERPOLATION for why its generated shader
        # text isn't used.
        shader_text = _exact_lut3d_glsl(tex3d.samplerName, edge) + _domain_wrapper_glsl(lut.domain)
        program = gsp.compile_ocio_program(shader_text)

        gl_texture = gl.glGenTextures(1)
        gl.glBindTexture(gl.GL_TEXTURE_3D, gl_texture)
        # NEAREST: the shader fetches exact lattice points and blends
        # them itself (EXACT INTERPOLATION).
        gl.glTexParameteri(gl.GL_TEXTURE_3D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(gl.GL_TEXTURE_3D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(gl.GL_TEXTURE_3D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_3D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_3D, gl.GL_TEXTURE_WRAP_R, gl.GL_CLAMP_TO_EDGE)
        gl.glTexImage3D(gl.GL_TEXTURE_3D, 0, gl.GL_RGB32F, edge, edge, edge, 0,
                         gl.GL_RGB, gl.GL_FLOAT, values)
        gl.glBindTexture(gl.GL_TEXTURE_3D, 0)

        entry = _CacheEntry(program, gl_texture, tex3d.samplerName, gpu_processor)
        self._entries[fingerprint] = entry
        return entry

    def clear(self):
        """Call before this cache's owning GL context goes away -- every
        compiled program/texture held here is only valid for that one
        context."""
        for entry in self._entries.values():
            gl.glDeleteProgram(entry.program)
            gl.glDeleteTextures([entry.gl_texture])
        self._entries.clear()
