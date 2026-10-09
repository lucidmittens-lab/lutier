"""Tests for help_text.py -- run directly: python3 -m tests.test_help_text

help_text.USER_MANUAL_MARKDOWN is the manual's single source;
docs/USER_MANUAL.md is generated from it (`python3 help_text.py`).
"""
from pathlib import Path

import help_text

DOCS_MANUAL = Path(__file__).parent.parent / "docs" / "USER_MANUAL.md"


def test_docs_manual_matches_in_app_manual():
    assert DOCS_MANUAL.read_text(encoding="utf-8") == help_text.USER_MANUAL_MARKDOWN, (
        "docs/USER_MANUAL.md has drifted from help_text.USER_MANUAL_MARKDOWN -- "
        "edit help_text.py, then run `python3 help_text.py` to regenerate it")
    print("docs/USER_MANUAL.md matches help_text.USER_MANUAL_MARKDOWN -- OK")


if __name__ == "__main__":
    test_docs_manual_matches_in_app_manual()
    print("\nAll help_text tests passed.")
