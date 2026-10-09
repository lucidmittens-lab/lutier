"""
gpu_printer_lights_shader.py -- GPU path for "printer_lights" stages,
following docs/archive/GPU_ACCELERATION_PLAN_2026-08-08.md's own established pattern
for a stage kind that needs work beyond a single OCIO-generated shader.

WHAT THIS BUILDS: printer_lights.py's own apply_printer_lights_in_working_
space is decode-to-linear -> per-channel R/G/B gain -> encode-from-linear
(see that module's docstring for why the gain has to happen in linear
light). There's no single OCIO transform that does all three steps with a
LIVE-EDITABLE gain (ocio.MatrixTransform has no dynamic-property support
at all -- confirmed by probing PyOpenColorIO directly, unlike
GradingPrimary/GradingRGBCurve/ExposureContrast/GradingTone, which all
have makeXDynamic() methods this app's gpu_ocio_dynamic.py already
exploits). So this module composes TWO separately-extracted OCIO shaders
(decode: working_colourspace -> its native linear sibling; encode: the
reverse) with a small HAND-WRITTEN wrapper in between that multiplies by
a real GLSL uniform -- same "OCIO for the parts that need OCIO's own
reference math, hand-written uniforms for the part that needs to be
continuously live-editable without recompiling" split gpu_cdl_shader.py
already established for CDL.

RESOURCE PREFIXES -- confirmed by probing PyOpenColorIO directly that
GpuShaderDesc.setResourcePrefix exists specifically for composing
multiple OCIO-generated shaders into one program without symbol
collisions; used here (see gpu_shader_prototype._extract_ocio_glsl's own
docstring) even though the two ColorSpaceTransform shaders this module
generates happen not to declare any textures/uniforms of their own
(confirmed directly: a real ACEScct<->ACEScg pair extracts to 0 textures,
0 3D textures, 0 uniforms on both sides -- these are pure baked-constant
curve math, same "no texture, no uniform" shape aces_drt/colorspace GPU
shaders already have) -- cheap insurance against a future working space
whose native-linear conversion DOES need a texture/uniform of its own.

FINGERPRINT -- (config_name, working_colourspace) ONLY, same "the shader
STRUCTURE doesn't change when a slider moves" reasoning gpu_ocio_dynamic.py
uses for the Tier 1 GradingX kinds, just via a hand-written uniform
instead of an OCIO dynamic property: r_points/g_points/b_points are pushed
as a plain vec3 gain uniform every render (see set_printer_lights_
uniforms in gpu_stack_renderer.py), never touching the compiled program.

GPU ELIGIBILITY REQUIRES OCIO -- printer_lights.py itself has a CPU-only
colour-science fallback (apply_printer_lights_in_colourspace) for when
PyOpenColorIO isn't installed, but this GPU path has no equivalent: OCIO's
own GPU codegen is the only source of the decode/encode shaders here, and
OCIO's own working-space registry is also the one printer_lights.py
itself prefers whenever it's available (see that module's docstring on
the Cinema-Gamut-style ambiguity the colour-science registry has) -- so
requiring aces_drt.OCIO_AVAILABLE for GPU eligibility doesn't lose any
correctness the CPU path wouldn't already prefer to avoid anyway. Without
OCIO, printer_lights stages simply stay on the existing CPU fallback,
same as every other OCIO-backed GPU stage kind in this app.

is_printer_lights_gpu_eligible ACTUALLY BUILDS the two shaders (via the
cache, so this is never wasted work) to decide eligibility, same
"eligibility checks do real inspection" precedent gpu_lut_shader.
is_lut3d_gpu_eligible already set -- a working color space whose native-
linear sibling can't be found unambiguously (aces_drt.
_find_native_linear_colourspace's own "raise rather than silently guess
wrong" rule) or whose decode/encode shader needs a texture this module
doesn't wire up falls the WHOLE stack back to CPU, same as any other
GPU-ineligible case, not a crash.
"""
import OpenGL.GL as gl
import PyOpenColorIO as ocio

import aces_drt
import gpu_shader_prototype as gsp

_UNIFORM_NAME = "uPrinterLightsGain"


class _CacheEntry:
    __slots__ = ("program", "decode_gpu_processor", "encode_gpu_processor")

    def __init__(self, program, decode_gpu_processor, encode_gpu_processor):
        self.program = program
        # Kept alive alongside the compiled program -- same "don't rely
        # on undocumented Python/C++ object lifetime guarantees in the
        # OCIO bindings" insurance every other GPU cache in this app takes
        # (gpu_lut_shader.py/gpu_ocio_dynamic.py).
        self.decode_gpu_processor = decode_gpu_processor
        self.encode_gpu_processor = encode_gpu_processor


def _generate_combined_shader_text(config_name, working_colourspace):
    """The GL-CONTEXT-FREE half of building a cache entry -- config
    lookup, native-linear-space lookup, and OCIO's own shader-info codegen
    (pure string generation; extractGpuShaderInfo never touches a real GL
    driver call, unlike gsp.compile_ocio_program below). Deliberately
    split out from _build_entry so is_printer_lights_gpu_eligible can call
    ONLY this half: a real, reproduced SIGSEGV (exit code 139) came from
    an earlier revision that called gsp.compile_ocio_program -- a REAL
    glCreateShader/glCompileShader call -- from an eligibility check with
    no current OpenGL context, the same class of "uncontexted GL call"
    crash this app already hit once elsewhere this session (see
    WipeCompareWidget.paintGL's own docstring on the Shiboken crash).
    gpu_lut_shader.is_lut3d_gpu_eligible avoids this the same way, just
    trivially (a plain isinstance/domain check needs no OCIO codegen at
    all); this module's eligibility question genuinely does need to run
    OCIO's shader generation to answer "does this need a texture", so it
    can't skip that part -- only the GL compile.

    Raises (an OCIO ocio.Exception, or an explicit RuntimeError for the
    "needs a texture" case this module doesn't support) if
    `working_colourspace` isn't actually GPU-eligible."""
    cfg = aces_drt._get_config(config_name)
    linear_space = aces_drt._find_native_linear_colourspace(cfg, working_colourspace)

    decode_processor = cfg.getProcessor(ocio.ColorSpaceTransform(src=working_colourspace, dst=linear_space))
    encode_processor = cfg.getProcessor(ocio.ColorSpaceTransform(src=linear_space, dst=working_colourspace))

    decode_text, decode_tex, decode_tex3d, _decode_uni = gsp._extract_ocio_glsl(
        decode_processor, function_name="OCIODecodeToLinear", resource_prefix="PLDecode_")
    encode_text, encode_tex, encode_tex3d, _encode_uni = gsp._extract_ocio_glsl(
        encode_processor, function_name="OCIOEncodeFromLinear", resource_prefix="PLEncode_")
    if decode_tex or decode_tex3d or encode_tex or encode_tex3d:
        # Not reachable for any working space in this app's own configs as
        # of this module's writing (probed directly -- see module
        # docstring), but a future config/space could in principle need
        # one; this module has no texture-binding plumbing, so surface it
        # as an eligibility failure rather than silently producing a
        # shader missing a texture bind.
        raise RuntimeError(
            f"printer_lights GPU path doesn't support a working colourspace "
            f"({working_colourspace!r}) whose decode/encode needs a texture")

    combined_shader_text = f"""
{decode_text}

{encode_text}

uniform vec3 {_UNIFORM_NAME};

vec4 OCIOMain(vec4 inPixel) {{
    vec4 linearColor = OCIODecodeToLinear(inPixel);
    linearColor.rgb *= {_UNIFORM_NAME};
    return OCIOEncodeFromLinear(linearColor);
}}
"""
    return combined_shader_text, decode_processor, encode_processor


def _build_entry(config_name, working_colourspace):
    """The full build, INCLUDING the real GL compile -- only call this
    with a current OpenGL context (see _generate_combined_shader_text's
    own docstring). Used by PrinterLightsGpuCache.get_entry, which is
    only ever called from within GpuStackRenderer under a live context,
    same convention every other GPU cache in this app relies on."""
    combined_shader_text, decode_processor, encode_processor = _generate_combined_shader_text(
        config_name, working_colourspace)
    program = gsp.compile_ocio_program(combined_shader_text)
    return _CacheEntry(program, decode_processor.getDefaultGPUProcessor(), encode_processor.getDefaultGPUProcessor())


def is_printer_lights_gpu_eligible(config_name, working_colourspace):
    """True if a "printer_lights" stage using this (config_name,
    working_colourspace) can render on the GPU -- see module docstring.
    Safe to call with NO current GL context (see
    _generate_combined_shader_text's own docstring) -- deliberately does
    NOT compile a GL program, only generates and inspects the GLSL text
    that a real build would compile."""
    if not aces_drt.OCIO_AVAILABLE:
        return False
    try:
        _generate_combined_shader_text(config_name, working_colourspace)
        return True
    except Exception:
        return False


class PrinterLightsGpuCache:
    """Compiled-program cache for "printer_lights" stages, fingerprinted
    on (config_name, working_colourspace) ONLY -- see module docstring's
    FINGERPRINT section for why r_points/g_points/b_points never touch
    this cache. Meant to live for the lifetime of one GL context, same
    convention as GpuStackRenderer's other per-kind caches -- see
    clear_gl_resources for teardown."""

    def __init__(self):
        self._entries = {}

    def get_entry(self, config_name, working_colourspace):
        fingerprint = (config_name, working_colourspace)
        entry = self._entries.get(fingerprint)
        if entry is not None:
            return entry
        entry = _build_entry(config_name, working_colourspace)
        self._entries[fingerprint] = entry
        return entry

    def clear(self):
        """Call before this cache's owning GL context goes away -- every
        compiled program held here is only valid for that one context."""
        for entry in self._entries.values():
            gl.glDeleteProgram(entry.program)
        self._entries.clear()
