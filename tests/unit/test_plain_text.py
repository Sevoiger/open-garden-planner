"""Plan text shown as TEXT (L1.3 security add-on, §8.11) — ``ui/plain_text.py``.

A plan file is untrusted, and a tooltip or an ``AutoText`` label renders markup it
finds in a string. These helpers are what stands between an item's name (or an
exception that quotes it) and such a widget. Each claim is checked against Qt's
own verdict (``Qt.mightBeRichText``) and Qt's own renderer (``QTextDocument``).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextDocument

from open_garden_planner.ui import plain_text
from open_garden_planner.ui.plain_text import (
    ELLIPSIS,
    NAME_MAX_CHARS,
    NOTICE_MAX_CHARS,
    NOTICE_MAX_LINES,
    TOOLTIP_MAX_CHARS,
    TOOLTIP_MAX_LINES,
    clip_line,
    clip_text,
    plain_tooltip,
)

HOSTILE = '<b>x</b><img src="file:///C:/Windows/win.ini">'


def _rendered(rich: str) -> str:
    """What Qt's rich-text engine makes of a string — the text a tooltip would show."""
    document = QTextDocument()
    document.setHtml(rich)
    return document.toPlainText()


class TestTooltip:
    def test_markup_in_the_text_is_shown_as_its_characters(self, qtbot) -> None:  # noqa: ARG002
        tooltip = plain_tooltip(HOSTILE)
        assert "<img" not in tooltip and "<b>" not in tooltip
        assert tooltip == ('<qt>&lt;b&gt;x&lt;/b&gt;&lt;img src=&quot;file:///C:/Windows/win.ini'
                           '&quot;&gt;</qt>')
        assert _rendered(tooltip) == HOSTILE

    def test_the_control_the_same_text_unescaped_is_markup_to_qt(self, qtbot) -> None:  # noqa: ARG002
        """Without the helper Qt takes the string for rich text and renders it: the
        bold tag is gone and an image object stands in the text."""
        assert Qt.mightBeRichText(HOSTILE) is True
        assert _rendered(HOSTILE) != HOSTILE
        assert "<b>" not in _rendered(HOSTILE)

    @pytest.mark.parametrize("text", [
        "plain words", "a < b and b > c", "Tom & Jerry", "<qt>nested</qt>", "</qt><h1>Title</h1>",
        "&lt;already escaped&gt;", "ünïcödé — „quotes“", "x" * 50,
    ])
    def test_whatever_goes_in_comes_out_of_the_renderer_unchanged(self, qtbot, text: str) -> None:  # noqa: ARG002
        assert _rendered(plain_tooltip(text)) == text

    def test_line_breaks_are_kept_as_line_breaks(self, qtbot) -> None:  # noqa: ARG002
        assert _rendered(plain_tooltip("ValueError: one\ntwo\nthree")) == "ValueError: one\ntwo\nthree"

    def test_a_huge_text_is_cut_before_it_is_looked_at(self, qtbot) -> None:  # noqa: ARG002
        tooltip = plain_tooltip("<table><tr><td>" * 100_000)
        shown = _rendered(tooltip)
        assert len(shown) == TOOLTIP_MAX_CHARS + 1 and shown.endswith(ELLIPSIS)
        assert len(tooltip) < 6 * TOOLTIP_MAX_CHARS + 20, "escaping grows a character sixfold at most"

    def test_many_lines_are_cut_too(self, qtbot) -> None:  # noqa: ARG002
        shown = _rendered(plain_tooltip("\n".join(f"line {n}" for n in range(100))))
        assert shown.count("\n") == TOOLTIP_MAX_LINES - 1 and shown.endswith("line 7" + ELLIPSIS)

    def test_no_text_no_tooltip(self) -> None:
        assert plain_tooltip("") == "" and plain_tooltip("   \n ") == ""

    def test_something_that_is_not_a_string_is_shown_as_its_text(self, qtbot) -> None:  # noqa: ARG002
        assert _rendered(plain_tooltip(ValueError("<i>bad</i>"))) == "<i>bad</i>"


class TestClipping:
    def test_a_short_text_is_returned_as_it_is(self) -> None:
        assert clip_text("Failed to initialize graphics backend for D3D11.") == (
            "Failed to initialize graphics backend for D3D11.")
        assert clip_text("<b>kept: this is for a PLAIN text widget</b>") == (
            "<b>kept: this is for a PLAIN text widget</b>")

    def test_the_caps(self) -> None:
        long = clip_text("y" * 10_000)
        assert long == "y" * NOTICE_MAX_CHARS + ELLIPSIS
        tall = clip_text("\n".join(str(n) for n in range(50)))
        assert tall.split("\n") == [*map(str, range(NOTICE_MAX_LINES - 1)),
                                    f"{NOTICE_MAX_LINES - 1}{ELLIPSIS}"]
        assert clip_text("abcdef", max_chars=3) == "abc" + ELLIPSIS
        assert clip_text("abc", max_chars=3) == "abc"

    def test_control_characters_are_not_text(self) -> None:
        assert clip_text("a\x00b\x1bc\x7fd\x9be") == "a b c d e"
        assert clip_text("one\r\ntwo\rthree\tfour") == "one\ntwo\nthree\tfour"

    def test_a_name_is_one_clipped_line(self) -> None:
        assert clip_line("North bed") == "North bed"
        assert clip_line("North\nbed\r\n\twith  breaks") == "North bed with breaks"
        assert clip_line("n" * 500) == "n" * NAME_MAX_CHARS + ELLIPSIS
        assert clip_line(HOSTILE) == HOSTILE, "a row is plain text: nothing to escape"
        assert clip_line(None) == "None" and clip_line("") == ""

    def test_the_cost_does_not_grow_with_the_input(self) -> None:
        """The cut comes first: a hundred megabytes cost what the cap costs."""
        huge = "<" * 100_000_000
        assert len(clip_text(huge)) == NOTICE_MAX_CHARS + 1
        assert len(clip_line(huge)) == NAME_MAX_CHARS + 1


def test_the_helper_needs_no_qt() -> None:
    source = Path(plain_text.__file__).read_text(encoding="utf-8")
    imported = {alias.name.split(".")[0] for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert imported <= {"__future__", "html", "typing"}, imported
