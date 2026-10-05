"""Tests for bake_settings_form.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_bake_settings_form.py
"""
import sys

from PySide6.QtWidgets import QApplication

import stack as st
from bake_settings_form import BakeSettingsForm, describe_bake_settings


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _form(stack=None):
    _app()
    form = BakeSettingsForm(lambda: stack or st.Stack())
    emitted = []
    form.settings_changed.connect(emitted.append)
    return form, emitted


def test_set_settings_shows_values_without_emitting():
    form, emitted = _form()
    custom = st.BakeSettings(size=17, domain_min=(-0.5, 0.0, 0.0), domain_max=(2.0, 1.0, 1.0))
    form.set_settings(custom)
    assert emitted == []
    assert form.current_settings() == custom
    assert not form.link_checkbox.isChecked()  # per-channel domain shows unlinked
    print("BakeSettingsForm: set_settings shows values without emitting -- OK")


def test_valid_edit_emits_and_linked_fields_follow_red():
    form, emitted = _form()
    form.set_settings(st.DEFAULT_BAKE_SETTINGS)
    r_min, r_max = form.domain_fields["R"]
    r_max.setValue(4.0)
    assert emitted[-1] == st.BakeSettings(domain_max=(4.0, 4.0, 4.0))
    form.size_combo.setCurrentText("65")
    assert emitted[-1].size == 65
    print("BakeSettingsForm: valid edits emit, linked G/B follow R -- OK")


def test_invalid_edit_shows_error_and_emits_nothing():
    form, emitted = _form()
    form.set_settings(st.DEFAULT_BAKE_SETTINGS)
    form.domain_fields["R"][0].setValue(3.0)  # min above max
    assert emitted == []
    assert not form.error_label.isHidden()
    form.size_combo.setCurrentText("abc")
    assert emitted == []
    print("BakeSettingsForm: an invalid edit shows inline and emits nothing -- OK")


def test_describe_bake_settings():
    assert describe_bake_settings(st.DEFAULT_BAKE_SETTINGS) == "33³ · domain [0, 1]"
    split = st.BakeSettings(size=17, domain_min=(-0.5, 0, 0), domain_max=(2, 1, 1))
    assert describe_bake_settings(split) == "17³ · domain R[-0.5,2] G[0,1] B[0,1]"
    print("describe_bake_settings: linked and per-channel summaries -- OK")


if __name__ == "__main__":
    test_set_settings_shows_values_without_emitting()
    test_valid_edit_emits_and_linked_fields_follow_red()
    test_invalid_edit_shows_error_and_emits_nothing()
    test_describe_bake_settings()
    print()
    print("All bake_settings_form tests passed.")
