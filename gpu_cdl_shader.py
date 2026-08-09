"""
gpu_cdl_shader.py -- GPU_ACCELERATION_PLAN_2026-08-08.md, STAGE 3.

A hand-written, uniform-driven GLSL implementation of ASC CDL SOP+SAT,
used INSTEAD OF routing CDL through OCIO's own GPU codegen the way
aces_drt/colorspace stages do (see gpu_shader_cache.py). This is a
deliberate, explicit choice made in Stage 2 (see
GPU_ACCELERATION_PLAN_2026-08-08.md's Stage 2 findings), not a shortcut:

- `getDefaultGPUProcessor()` bakes CDL's slope/offset/power/sat as
  literal GLSL constants (measured 0 uniforms) -- fine for a stage whose
  parameters are picked once (ACES DRT's working-colourspace/display/
  view), wrong for CDL, whose parameters come from this app's own
  continuously-dragged sliders (see stack_panel.py's
  enable_drag_to_scrub usage on the CDL spin boxes). Measured cost of
  regenerating+recompiling per edit: ~0.95ms/edit (see
  GPU_ACCELERATION_PLAN_2026-08-08.md) -- not catastrophic, but a real,
  fully avoidable cost on the one control in this app already built for
  smooth continuous scrubbing.
- The alternative OCIO offers for real-time-editable GPU parameters,
  `ocio.GradingPrimaryTransform.makeDynamic()`, uses a DIFFERENT
  parameterization (pivot/contrast/gamma) than classic ASC CDL's
  slope/offset/power -- not confirmed mathematically equivalent, so
  routing CDL's GPU path through it risks the CPU and GPU paths computing
  genuinely different results, not just different precision. Per Tom:
  CDL's role in this app is specifically to support standard/interchange
  ASC CDL adjustments (not to be the primary/preferred grading tool), so
  preserving its exact, well-known formula matters more here than it
  would for an app-original grading feature -- GradingPrimaryTransform is
  a real candidate for a FUTURE, separate, additional stage kind of its
  own (flagged in GPU_ACCELERATION_PLAN_2026-08-08.md's backlog), not a
  replacement for this one.

THE FORMULA BELOW IS COPIED, NOT REINVENTED: both branches (CDL_NO_CLAMP
== cdl.py's "no_clip" style, CDL_ASC == "asc") are transcribed verbatim
from OCIO 2.5.2's own `getDefaultGPUProcessor()` output for a CDLTransform
(inspected directly via gpu_shader_prototype.py's `_extract_ocio_glsl`,
see that module's docstring/history) -- lumaWeights is OCIO's own
Rec.709 luma constant, not a value this app chooses, and is therefore
baked as a literal here exactly as OCIO bakes it (it's never a per-stage
parameter, unlike slope/offset/power/sat). `test_gpu_cdl_shader.py`
renders both this hand-written shader and OCIO's own generated one for
the same random parameter sets, for both styles, and confirms the two
match to float32 precision -- if this file's formula and OCIO's ever
drift apart (e.g. a future OCIO version changes CDL's GPU codegen), that
test is what will catch it, not a one-time eyeball check.
"""

CDL_SHADER_SOURCE = """
uniform vec3 uCdlSlope;
uniform vec3 uCdlOffset;
uniform vec3 uCdlPower;
uniform float uCdlSaturation;
uniform bool uCdlClamped;  // true = "asc" style, false = "no_clip" style

vec4 OCIOMain(vec4 inPixel)
{
    vec3 lumaWeights = vec3(0.212599993, 0.715200007, 0.0722000003);
    vec4 outColor = inPixel;

    outColor.rgb = outColor.rgb * uCdlSlope;
    outColor.rgb = outColor.rgb + uCdlOffset;

    if (uCdlClamped) {
        // CDL_ASC ("asc" style): intermediate clamp before power, same
        // order OCIO's own "Fwd" (not "FwdNoClamp") codegen uses.
        outColor.rgb = clamp(outColor.rgb, 0.0, 1.0);
        outColor.rgb = pow(outColor.rgb, uCdlPower);
    } else {
        // CDL_NO_CLAMP ("no_clip" style, this app's default): power
        // applied to abs(x) then re-signed, so negative input (routine
        // in ACES-family working spaces) doesn't hit pow()'s undefined
        // negative-base behavior -- OCIO's own "FwdNoClamp" trick.
        vec3 posPix = step(0.0, outColor.rgb);
        vec3 pixPower = pow(abs(outColor.rgb), uCdlPower);
        outColor.rgb = mix(outColor.rgb, pixPower, posPix);
    }

    float luma = dot(outColor.rgb, lumaWeights);
    outColor.rgb = luma + uCdlSaturation * (outColor.rgb - luma);

    if (uCdlClamped) {
        outColor.rgb = clamp(outColor.rgb, 0.0, 1.0);
    }

    return outColor;
}
"""


def set_cdl_uniforms(gl, program, slope, offset, power, saturation, style):
    """Push one CDL stage's current values onto an already-linked,
    already-in-use `program` containing CDL_SHADER_SOURCE -- the whole
    point of this file: this is the ONLY thing that needs to happen on a
    slider edit, no shader source regeneration or recompilation involved.
    `gl` is the PyOpenGL `OpenGL.GL` module (passed in rather than
    imported here so this module stays importable/testable without a
    live GL context, same reasoning as every other Qt-free backend module
    in this app -- only the caller, which already has a current context,
    needs the real `GL` bindings). `style` is this app's own style key
    ("no_clip"/"asc", see cdl.AVAILABLE_STYLES), not an OCIO enum."""
    if style not in ("no_clip", "asc"):
        raise ValueError(f"Unknown CDL style: {style!r} (expected 'no_clip' or 'asc')")
    gl.glUniform3f(gl.glGetUniformLocation(program, "uCdlSlope"), *slope)
    gl.glUniform3f(gl.glGetUniformLocation(program, "uCdlOffset"), *offset)
    gl.glUniform3f(gl.glGetUniformLocation(program, "uCdlPower"), *power)
    gl.glUniform1f(gl.glGetUniformLocation(program, "uCdlSaturation"), saturation)
    gl.glUniform1i(gl.glGetUniformLocation(program, "uCdlClamped"), style == "asc")
