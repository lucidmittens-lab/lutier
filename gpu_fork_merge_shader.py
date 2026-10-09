"""
gpu_fork_merge_shader.py -- docs/archive/GPU_ACCELERATION_PLAN_2026-08-08.md, STAGE 6.

The one hand-written (non-OCIO) shader gpu_stack_renderer.py's fork
support needs. fork_merge is a pure texture blend -- OCIO has no
transform for "combine two already-computed branches" at all, so this
mirrors the CPU reference directly (stack.py's _run_stages, the
fork_merge branch) instead of routing through gpu_shader_cache.py's
OCIO-shader-extraction machinery -- same "hand-written uniform-driven
GLSL when there's no OCIO transform to wrap" precedent
gpu_printer_lights_shader.py already set for its own gain uniform.

CPU reference (stack.py):
    combined = _blend_two(running_a, running_b, blend)   # a*(1-blend) + b*blend, clamped at the ends
    running = running * (1 - opacity) + combined * opacity  # opacity in (0, 1)
    # opacity <= 0 -> running unchanged; opacity >= 1 -> running = combined

Both of those are exactly what GLSL's own mix() computes for blend/
opacity already clamped to [0, 1] by the UI (the opacity spin's own
0-100% range, the blend slider's own 0-100% range) -- no branching
needed in the shader the way _blend_two/the opacity check branch on the
CPU (those only exist there to sidestep float edge cases at the exact
0.0/1.0 boundaries, not because the underlying math differs). Reproduced
here as ONE fragment shader taking three input textures (the pre-split
signal, and each branch's own final result) and two uniforms, rather
than two separate render passes -- cheaper (one draw instead of two) and
avoids needing an extra intermediate render target for the
branch-combine step alone.

Does NOT go through compile_ocio_program/the shared OCIOMain(vec4)->vec4
template every other stage kind's shader uses (see gpu_shader_prototype.
py's _FRAGMENT_SHADER_TEMPLATE) -- that template hard-codes exactly ONE
input texture (uInput) blended against ONE transform's output via
uStageOpacity, which doesn't fit a THREE-texture blend at all. Compiled
via gpu_shader_prototype.link_custom_program instead, the sanctioned
escape hatch for exactly this "not the OCIOMain shape" case.
"""
import OpenGL.GL as gl

import gpu_shader_prototype as gsp

_FRAGMENT_SHADER = """
#version 120
varying vec2 vUV;
uniform sampler2D uRunning;
uniform sampler2D uBranchA;
uniform sampler2D uBranchB;
uniform float uBlend;
uniform float uOpacity;

void main() {
    vec4 running = texture2D(uRunning, vUV);
    vec4 a = texture2D(uBranchA, vUV);
    vec4 b = texture2D(uBranchB, vUV);
    vec4 combined = mix(a, b, uBlend);
    gl_FragColor = mix(running, combined, uOpacity);
}
"""


def compile_fork_merge_program():
    """One static program, no fingerprinting/caching layer needed --
    unlike every OCIO-backed stage kind (whose shader SOURCE varies with
    style/config/src/dst), this shader's source never changes at all,
    only its uniforms do. gpu_stack_renderer.py compiles this exactly
    once in GpuStackRenderer.__init__ and reuses it for the lifetime of
    the GL context -- same single-compile-forever shape gpu_shader_cache.
    CdlGpuProgram already has for the same reason (CDL's shader source is
    also fixed; its values are uniform-driven too)."""
    return gsp.link_custom_program(gsp.FULLSCREEN_QUAD_VERTEX_SHADER, _FRAGMENT_SHADER)


def render_fork_merge_pass(program, running_texture, branch_a_texture, branch_b_texture,
                            blend, opacity, target_fbo, w, h, vbo):
    """One draw call, blending `branch_a_texture`/`branch_b_texture` via
    `blend` and then that result against `running_texture` via `opacity`
    (see this module's own docstring for the exact CPU reference this
    reproduces), rendered into `target_fbo`. Caller (gpu_stack_renderer.
    py's render_chain) owns framebuffer-binding restoration afterward,
    same as its own _render_stage_pass does for an ordinary stage --
    this leaves target_fbo bound on return, nothing else."""
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, target_fbo)
    gl.glViewport(0, 0, w, h)
    gl.glUseProgram(program)

    gl.glActiveTexture(gl.GL_TEXTURE0)
    gl.glBindTexture(gl.GL_TEXTURE_2D, running_texture)
    gl.glUniform1i(gl.glGetUniformLocation(program, "uRunning"), 0)

    gl.glActiveTexture(gl.GL_TEXTURE1)
    gl.glBindTexture(gl.GL_TEXTURE_2D, branch_a_texture)
    gl.glUniform1i(gl.glGetUniformLocation(program, "uBranchA"), 1)

    gl.glActiveTexture(gl.GL_TEXTURE2)
    gl.glBindTexture(gl.GL_TEXTURE_2D, branch_b_texture)
    gl.glUniform1i(gl.glGetUniformLocation(program, "uBranchB"), 2)

    gl.glUniform1f(gl.glGetUniformLocation(program, "uBlend"), float(blend))
    gl.glUniform1f(gl.glGetUniformLocation(program, "uOpacity"), float(opacity))

    aPos_loc = gsp.bind_fullscreen_quad(vbo, program)
    gl.glDrawArrays(gl.GL_TRIANGLE_STRIP, 0, 4)
    gsp.unbind_fullscreen_quad(aPos_loc)
