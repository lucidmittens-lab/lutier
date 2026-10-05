"""
test_colour_pipeline.py -- run directly: python3 test_colour_pipeline.py -- Qt-free
"""
import numpy as np
import stack as st
import colour_pipeline as cp

ACES_709 = cp.DisplayTransform("aces", "Rec.1886 Rec.709 - Display", "ACES 1.0 - SDR Video")

def _kinds(stack):
    return [s.kind for s in stack.stages]

def test_nothing_set_returns_the_same_stages():
    user = st.Stack(stages=[st.make_cdl_stage()])
    eff = cp.build_effective_stack(user, "ACEScct")
    assert eff.stages == user.stages and all(eff.stages[i] is user.stages[i] for i in range(len(user.stages)))
    assert _kinds(eff) == ["cdl"]
    print("test_nothing_set_returns_the_same_stages -- OK")

def test_input_transform_leads():
    user = st.Stack(stages=[st.make_cdl_stage()])
    eff = cp.build_effective_stack(user, "ACEScct", "ARRI LogC4")
    assert _kinds(eff) == ["colorspace", "cdl"]
    assert eff.stages[0].params["src"] == "ARRI LogC4" and eff.stages[0].params["dst"] == "ACEScct"
    print("test_input_transform_leads -- OK")

def test_stage_in_another_space_is_wrapped():
    printer = st.make_printer_lights_stage(1, 0, 0, working_colourspace="ARRI LogC4")
    user = st.Stack(stages=[printer])
    eff = cp.build_effective_stack(user, "ACEScct")
    assert _kinds(eff) == ["colorspace", "printer_lights", "colorspace"]
    assert eff.stages[0].params["src"] == "ACEScct" and eff.stages[0].params["dst"] == "ARRI LogC4"
    assert eff.stages[2].params["src"] == "ARRI LogC4" and eff.stages[2].params["dst"] == "ACEScct"
    assert eff.stages[1] is printer
    print("test_stage_in_another_space_is_wrapped -- OK")

def test_stage_in_the_working_space_is_not_wrapped():
    printer = st.make_printer_lights_stage(1, 0, 0, working_colourspace="ACEScct")
    user = st.Stack(stages=[printer])
    eff = cp.build_effective_stack(user, "ACEScct")
    assert _kinds(eff) == ["printer_lights"]
    print("test_stage_in_the_working_space_is_not_wrapped -- OK")

def test_disabled_stage_is_not_wrapped():
    printer = st.make_printer_lights_stage(1, 0, 0, working_colourspace="ARRI LogC4")
    printer.enabled = False
    user = st.Stack(stages=[printer])
    eff = cp.build_effective_stack(user, "ACEScct")
    assert _kinds(eff) == ["printer_lights"]
    print("test_disabled_stage_is_not_wrapped -- OK")

def test_drt_converts_in_only_and_stops_conversion_after_it():
    drt = st.make_custom_drt_stage("ARRI LogC4")
    after = st.make_printer_lights_stage(1, 0, 0, working_colourspace="ARRI LogC4")
    user = st.Stack(stages=[drt, after])
    eff = cp.build_effective_stack(user, "ACEScct")
    assert _kinds(eff) == ["colorspace", "custom_drt", "printer_lights"]
    print("test_drt_converts_in_only_and_stops_conversion_after_it -- OK")

def test_display_transform_appended_unless_stack_has_a_drt():
    cdl = st.make_cdl_stage()
    user = st.Stack(stages=[cdl])
    eff = cp.build_effective_stack(user, "ACEScct", display=ACES_709)
    assert _kinds(eff) == ["cdl", "aces_drt"]
    assert eff.stages[-1].params["display"] == "Rec.1886 Rec.709 - Display" and eff.stages[-1].params["view"] == "ACES 1.0 - SDR Video"
    user_with_drt = st.Stack(stages=[cdl, st.make_custom_drt_stage("ACEScct")])
    eff_with_drt = cp.build_effective_stack(user_with_drt, "ACEScct", display=ACES_709)
    assert _kinds(eff_with_drt) == ["cdl", "custom_drt"]
    print("test_display_transform_appended_unless_stack_has_a_drt -- OK")

def test_wrapped_fork_branch_stages_keep_their_branch():
    printer = st.make_printer_lights_stage(1, 0, 0, working_colourspace="ARRI LogC4")
    printer.branch = "a"
    fork = [st.make_fork_split_stage(), printer, st.make_fork_merge_stage()]
    user = st.Stack(stages=fork)
    eff = cp.build_effective_stack(user, "ACEScct")
    assert _kinds(eff) == ["fork_split", "colorspace", "printer_lights", "colorspace", "fork_merge"]
    assert eff.stages[1].branch == "a" and eff.stages[3].branch == "a"
    # ...and the wrapped fork still renders (a CSC without a branch inside
    # an enabled fork region would be a structural error).
    st.check_stack_structure(eff.stages)
    print("test_wrapped_fork_branch_stages_keep_their_branch -- OK")

def test_effective_stack_renders():
    input_space = "ARRI LogC4"
    cdl = st.make_cdl_stage()
    user = st.Stack(stages=[cdl])
    eff = cp.build_effective_stack(user, "ACEScct", input_colourspace=input_space, display=ACES_709)
    img = st.apply_stack_to_image_array(eff, np.random.default_rng(0).random((8, 8, 3)))
    assert img.shape == (8, 8, 3) and np.all(np.isfinite(img))
    print("test_effective_stack_renders -- OK")

def test_suggest_input_colourspace():
    assert cp.suggest_input_colourspace({"camera": "ARRI ALEXA Mini LF"}) == "ARRI LogC3 (EI800)"
    assert cp.suggest_input_colourspace({"camera": "ARRI ALEXA 35"}) == "ARRI LogC4"
    assert cp.suggest_input_colourspace({"camera": "Sony VENICE 2"}) == "S-Log3 Venice S-Gamut3.Cine"
    assert cp.suggest_input_colourspace({}) is None
    assert cp.suggest_input_colourspace(None) is None
    assert cp.suggest_input_colourspace({"camera": "Canon EOS R5"}) is None
    print("test_suggest_input_colourspace -- OK")

def test_stage_space_note():
    printer_logc4 = st.make_printer_lights_stage(1, 0, 0, working_colourspace="ARRI LogC4")
    printer_acescct = st.make_printer_lights_stage(1, 0, 0, working_colourspace="ACEScct")
    cdl = st.make_cdl_stage()
    assert cp.stage_space_note(printer_logc4, "ACEScct") == "in ARRI LogC4"
    assert cp.stage_space_note(printer_acescct, "ACEScct") == ""
    assert cp.stage_space_note(cdl, "ACEScct") == ""
    print("test_stage_space_note -- OK")

if __name__ == "__main__":
    test_nothing_set_returns_the_same_stages()
    test_input_transform_leads()
    test_stage_in_another_space_is_wrapped()
    test_stage_in_the_working_space_is_not_wrapped()
    test_disabled_stage_is_not_wrapped()
    test_drt_converts_in_only_and_stops_conversion_after_it()
    test_display_transform_appended_unless_stack_has_a_drt()
    test_wrapped_fork_branch_stages_keep_their_branch()
    test_effective_stack_renders()
    test_suggest_input_colourspace()
    test_stage_space_note()
    print()
    print("All colour_pipeline tests passed.")
