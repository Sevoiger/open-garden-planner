"""Showing text from a plan file as TEXT (L1.3 security add-on; §8.11).

A plan file is untrusted input, and several Qt widgets decide by LOOKING at a
string whether it is rich text (``Qt.mightBeRichText``): tooltips, ``QLabel``
with its default ``AutoText`` format, ``QMessageBox`` texts, "What's This". An
item name, a species string or an exception message that carries one —
``<img src="file:///…">``, a table a hundred thousand cells long — is then
MARKUP there: it is rendered, it can pull a local file into the layout, it can
imitate the app's own wording, and laying it out can stall the GUI thread.

The rule (§8.11): text that can come from a plan, a file path or an exception
never reaches such a widget as it is. Either the widget is told the format —
``label.setTextFormat(Qt.TextFormat.PlainText)`` — and gets ``clip_text``; or,
where a widget has no such switch (a tooltip), it gets ``plain_tooltip``.

Standard library only: these functions are the same in a test without Qt.
"""

from __future__ import annotations

import html
from typing import Final

#: A tooltip shows at most this much of a plan-derived text …
TOOLTIP_MAX_CHARS: Final = 400
TOOLTIP_MAX_LINES: Final = 8
#: … a notice or message label this much …
NOTICE_MAX_CHARS: Final = 600
NOTICE_MAX_LINES: Final = 6
#: … and a name inside a row or a sentence this much, on one line.
NAME_MAX_CHARS: Final = 80
ELLIPSIS: Final = "…"

#: Control characters are not text: C0 (but tab and newline), DEL and C1.
_CONTROL: Final = {
    code: " " for code in (*range(0x00, 0x20), *range(0x7F, 0xA0)) if code not in (0x09, 0x0A)}


def clip_text(text: object, max_chars: int = NOTICE_MAX_CHARS,
              max_lines: int = NOTICE_MAX_LINES) -> str:
    """``text`` as plain text of bounded size: control characters become spaces, at
    most ``max_lines`` lines and ``max_chars`` characters are kept, and an ellipsis
    says that something was cut. For a widget whose format is PLAIN text.

    The cut happens before anything else looks at the string, so a text of any
    length costs no more than its first ``max_chars`` characters.
    """
    raw = text if isinstance(text, str) else str(text)
    cut = len(raw) > max_chars
    kept = raw[:max_chars].replace("\r\n", "\n").replace("\r", "\n").translate(_CONTROL)
    lines = kept.split("\n")
    if len(lines) > max_lines:
        lines, cut = lines[:max_lines], True
    result = "\n".join(lines).rstrip()
    return result + ELLIPSIS if cut else result


def clip_line(text: object, max_chars: int = NAME_MAX_CHARS) -> str:
    """``text`` on ONE line (line breaks and tabs become spaces), clipped — a name
    inside a list row or a sentence."""
    raw = text if isinstance(text, str) else str(text)
    cut = len(raw) > max_chars
    kept = " ".join(raw[:max_chars].translate(_CONTROL).split())
    return kept + ELLIPSIS if cut else kept


def plain_tooltip(text: object, max_chars: int = TOOLTIP_MAX_CHARS,
                  max_lines: int = TOOLTIP_MAX_LINES) -> str:
    """A tooltip that shows ``text`` LITERALLY, whatever it contains.

    A tooltip has no format switch: Qt looks at the string. So the answer is rich
    text on purpose — the clipped text with every markup character escaped, in an
    explicit ``<qt>`` element — which Qt renders as the characters given (runs of
    blanks collapse, as in any rich text). An empty text gives an empty tooltip
    (none is shown).
    """
    clipped = clip_text(text, max_chars, max_lines)
    if not clipped:
        return ""
    return "<qt>" + html.escape(clipped, quote=True).replace("\n", "<br>") + "</qt>"
