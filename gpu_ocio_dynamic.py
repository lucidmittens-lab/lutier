"""
gpu_ocio_dynamic.py -- OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md, STAGE 1.

Shared GPU plumbing for OCIO's "dynamic" transform family --
GradingPrimaryTransform, GradingRGBCurveTransform, GradingToneTransform,
ExposureContrastTransform (see that plan doc's own "Ground truth"
section for how each was validated against a real GPU render before this
module was written). Unlike CDLTransform (gpu_cdl_shader.py, hand-written
because OCIO bakes CDL's params as literal GLSL constants by default),
every one of these four -- once makeDynamic()'d (or, for
ExposureContrastTransform, one or more of makeExposureDynamic()/
makeContrastDynamic()/makeGammaDynamic()) before building a processor --
emits OCIO's OWN generated GLSL with REAL named uniforms. So there is
exactly one generic mechanism needed here, not four bespoke shader files:
compile the dynamic GLSL once per (kind, style), then push its CURRENT
uniform values before every render by mutating the live DynamicProperty
handle(s) OCIO itself created during that one compile.

UNIFORM TYPE COVERAGE -- push_dynamic_uniforms below dispatches on every
ocio.UniformDataType this plan's own survey found across all four kinds:
UNIFORM_DOUBLE (float scalars), UNIFORM_BOOL (bypass flags),
UNIFORM_FLOAT3 (GradingPrimary's brightness/contrast/gamma),
UNIFORM_VECTOR_INT/UNIFORM_VECTOR_FLOAT (GradingRGBCurve's fixed-size
knot/coefficient arrays). No fifth type exists in this survey; a new kind
added later that produces one would need a new branch here, not a
silent fallback -- see the AssertionError below.

MULTIPLE DYNAMIC PROPERTIES PER TRANSFORM -- most of these four have
exactly one ocio.DynamicPropertyType (DYNAMIC_PROPERTY_GRADING_PRIMARY/
_TONE/_RGBCURVE), but ExposureContrastTransform has THREE independent
ones (DYNAMIC_PROPERTY_EXPOSURE/_CONTRAST/_GAMMA, one per makeXDynamic()
call) -- OcioDynamicGpuStageCache's entries hold a
{DynamicPropertyType: DynamicProperty} dict, not a single handle, so both
shapes are the same code path.

CACHING FINGERPRINT -- keyed on (kind, style) only, NEVER on live
parameter values: the whole point of makeDynamic() is that the compiled
shader's STRUCTURE (and therefore its uniform declarations) doesn't
change when a slider moves, only the VALUES pushed into those uniforms
do -- caching on live values would recompile on every edit and
reintroduce exactly the cost GPU_ACCELERATION_PLAN_2026-08-08.md's Stage
3 eliminated for CDL/ACES DRT. The fingerprint itself is the caller's
responsibility (a (kind, style) tuple, or whatever else genuinely affects
shader STRUCTURE for that kind) -- this module only knows how to build
and cache against whatever key it's given.
"""
import numpy as np
import OpenGL.GL as gl
import PyOpenColorIO as ocio

import cdl
import gpu_shader_prototype as gsp


def build_dynamic_ocio_shader(transform, function_name="OCIOMain"):
    """`transform` must already have its dynamic flag(s) set -- see module
    docstring. Returns (shader_text, shader_desc, gpu_processor).
    `shader_desc` must be kept alive by the caller (see
    OcioDynamicGpuStageCache) -- it, not the text, is what push_dynamic_
    uniforms and getDynamicProperty read from on every subsequent edit.

    `gpu_processor` must ALSO be kept alive for exactly as long as
    `shader_desc` is used -- a real, deliberate defensive choice, not
    unused-looking dead weight: OCIO's C++ API builds shader_desc's
    DynamicProperty handles by extracting them from this processor's own
    internal transform graph (extractGpuShaderInfo), and nothing in the
    Python binding layer guarantees shader_desc holds a strong reference
    back to it. Discarding gpu_processor right after this call returns
    (as an earlier version of this function did, keeping only shader_
    desc) risks a real use-after-free once Python's refcounting frees it
    -- one that wouldn't necessarily crash immediately or in a short-
    lived test process (freed memory can look valid for a while), which
    is exactly the kind of bug that surfaces later, intermittently, in a
    long-running app instead. Cheap to keep alive, no reason not to."""
    cfg = cdl._get_raw_config()  # bare config -- none of these transforms reference a named colorspace
    gpu_processor = cfg.getProcessor(transform).getDefaultGPUProcessor()
    shader_desc = ocio.GpuShaderDesc.CreateShaderDesc(language=ocio.GPU_LANGUAGE_GLSL_1_2)
    shader_desc.setFunctionName(function_name)
    gpu_processor.extractGpuShaderInfo(shader_desc)
    return shader_desc.getShaderText(), shader_desc, gpu_processor


def push_dynamic_uniforms(program, shader_desc, location_cache=None):
    """Pushes shader_desc's CURRENT uniform values (i.e. whatever was set
    on its DynamicProperty handle(s) most recently -- see
    OcioDynamicGpuStageCache's own dynamic_properties dict) into
    `program`'s actual GL uniform locations. Call this after
    glUseProgram(program), same convention as
    gpu_cdl_shader.set_cdl_uniforms and every other set_uniforms hook
    gpu_stack_renderer.py's chain already uses.

    `location_cache`, if given (a plain dict the caller owns -- see
    OcioDynamicGpuStageCache._CacheEntry.uniform_locations), memoizes
    glGetUniformLocation by name so repeated calls against the SAME
    compiled program (the normal case: this runs on every single edit
    tick during a live slider drag) don't re-pay that lookup every time.
    A kind like grading_tone has 32 uniforms -- 32 string-keyed driver
    lookups per frame, every frame of a drag, was real, measurable
    overhead (confirmed as a contributor to a real "feels slow" report)
    for something that never changes once a program is linked."""
    if location_cache is None:
        location_cache = {}
    for name, data in shader_desc.getUniforms():
        loc = location_cache.get(name)
        if loc is None:
            loc = gl.glGetUniformLocation(program, name)
            location_cache[name] = loc
        uniform_type = data.type
        if uniform_type == ocio.UNIFORM_DOUBLE:
            gl.glUniform1f(loc, data.getDouble())
        elif uniform_type == ocio.UNIFORM_BOOL:
            gl.glUniform1i(loc, 1 if data.getBool() else 0)
        elif uniform_type == ocio.UNIFORM_FLOAT3:
            gl.glUniform3f(loc, *data.getFloat3())
        elif uniform_type == ocio.UNIFORM_VECTOR_INT:
            values = np.asarray(data.getVectorInt(), dtype=np.int32)
            gl.glUniform1iv(loc, len(values), values)
        elif uniform_type == ocio.UNIFORM_VECTOR_FLOAT:
            values = np.asarray(data.getVectorFloat(), dtype=np.float32)
            gl.glUniform1fv(loc, len(values), values)
        else:
            raise AssertionError(  # pragma: no cover -- see module docstring, "UNIFORM TYPE COVERAGE"
                f"Unhandled OCIO uniform type {uniform_type!r} for {name!r} -- "
                "a dynamic OCIO transform exposed a uniform shape this "
                "module doesn't know how to push yet.")


class _CacheEntry:
    __slots__ = ("program", "shader_desc", "gpu_processor", "dynamic_properties", "uniform_locations")

    def __init__(self, program, shader_desc, gpu_processor, dynamic_properties):
        self.program = program
        self.shader_desc = shader_desc
        # Kept alive alongside shader_desc for its whole lifetime -- see
        # build_dynamic_ocio_shader's own docstring for why this isn't
        # unused-looking dead weight.
        self.gpu_processor = gpu_processor
        self.dynamic_properties = dynamic_properties
        # Passed to push_dynamic_uniforms as its location_cache -- valid
        # for `program`'s whole lifetime (uniform locations never change
        # after linking), so this dict only ever grows, never needs
        # invalidating short of the program itself being deleted (see
        # OcioDynamicGpuStageCache.clear(), which drops the whole entry).
        self.uniform_locations = {}


class OcioDynamicGpuStageCache:
    """Compiled-program cache for one dynamic OCIO transform KIND (e.g.
    "grading_primary"), fingerprinted on whatever the caller passes as
    `fingerprint` -- see module docstring's "CACHING FINGERPRINT" (should
    be (kind, style) or equivalent, NEVER live parameter values).
    `dynamic_property_types` is the tuple of ocio.DynamicPropertyType this
    kind's transform exposes (one entry for GradingPrimary/GradingTone/
    GradingRGBCurve, three for ExposureContrast) -- every cached entry's
    dynamic_properties dict has exactly these keys, always, regardless of
    how many the kind actually has."""

    def __init__(self, label, dynamic_property_types):
        self._label = label
        self._dynamic_property_types = tuple(dynamic_property_types)
        self._entries = {}

    def get_entry(self, fingerprint, build_dynamic_transform):
        """`build_dynamic_transform` is called ONLY on a cache miss -- a
        zero-arg callable returning a fresh transform with its dynamic
        flag(s) already set (see build_dynamic_ocio_shader's own
        docstring for what that means per kind). Returns the (possibly
        newly built) _CacheEntry; caller sets values on
        entry.dynamic_properties[...] and calls push_dynamic_uniforms
        after glUseProgram(entry.program)."""
        entry = self._entries.get(fingerprint)
        if entry is not None:
            return entry
        transform = build_dynamic_transform()
        shader_text, shader_desc, gpu_processor = build_dynamic_ocio_shader(transform)
        program = gsp.compile_ocio_program(shader_text)
        dynamic_properties = {
            dpt: shader_desc.getDynamicProperty(dpt) for dpt in self._dynamic_property_types
        }
        entry = _CacheEntry(program, shader_desc, gpu_processor, dynamic_properties)
        self._entries[fingerprint] = entry
        return entry

    def clear(self):
        """Call before this cache's owning GL context goes away -- every
        compiled program held here is only valid for that one context."""
        for entry in self._entries.values():
            gl.glDeleteProgram(entry.program)
        self._entries.clear()
