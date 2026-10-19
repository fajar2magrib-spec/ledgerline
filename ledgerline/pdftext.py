"""Right-to-left text for PDF invoices (Urdu, Arabic ...), with no extra packages.

ReportLab draws characters left to right and does not join Arabic letters. ``shape()`` therefore
1. joins the letters (isolated / initial / medial / final forms, taken from Unicode's own tables) and
2. puts the text into *visual* order (right-to-left words, but numbers and Latin names stay readable).
It handles ordinary invoice text well. Very long right-to-left notes that wrap over several lines can
come out in the wrong line order: keep notes short or use the English PDF for those.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).parent / "assets" / "fonts"
_LAM_ALEF = {"\u0622": ("\uFEF5", "\uFEF6"), "\u0623": ("\uFEF7", "\uFEF8"),
             "\u0625": ("\uFEF9", "\uFEFA"), "\u0627": ("\uFEFB", "\uFEFC")}   # (isolated, final)
_TRANSPARENT = set(range(0x064B, 0x0660)) | {0x0670} | set(range(0x06D6, 0x06DD)) | set(range(0x06DF, 0x06E5)) | {0x06E7, 0x06E8} | set(range(0x06EA, 0x06EE))


@lru_cache(maxsize=1)
def _forms() -> dict[str, dict[str, str]]:
    table: dict[str, dict[str, str]] = {}
    for cp in list(range(0xFB50, 0xFDFC)) + list(range(0xFE70, 0xFEFD)):
        decomposition = unicodedata.decomposition(chr(cp))
        if not decomposition.startswith("<"):
            continue
        tag, _, rest = decomposition.partition(" ")
        parts = rest.split()
        if len(parts) != 1 or tag not in ("<isolated>", "<initial>", "<medial>", "<final>"):
            continue
        base = chr(int(parts[0], 16))
        table.setdefault(base, {}).setdefault(tag[1:-1], chr(cp))
    return table


def is_rtl_char(ch: str) -> bool:
    cp = ord(ch)
    return 0x0590 <= cp <= 0x08FF or 0xFB1D <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF


def _join(text: str) -> str:
    forms = _forms()
    chars = list(text)
    out: list[str] = []
    n = len(chars)

    def neighbour(i: int, step: int):
        j = i + step
        while 0 <= j < n and ord(chars[j]) in _TRANSPARENT:
            j += step
        return chars[j] if 0 <= j < n else None

    for i, ch in enumerate(chars):
        if ch not in forms or ord(ch) in _TRANSPARENT:
            out.append(ch)
            continue
        prev, nxt = neighbour(i, -1), neighbour(i, +1)
        prev_joins = prev is not None and "initial" in forms.get(prev, {}) and prev not in _LAM_ALEF
        can_join_next = "initial" in forms[ch]
        next_joins = can_join_next and nxt is not None and "final" in forms.get(nxt, {})
        if ch == "\u0644" and nxt in _LAM_ALEF:                  # lam + alef ligature
            out.append(_LAM_ALEF[nxt][1 if prev_joins else 0])
            chars[chars.index(nxt, i + 1)] = "\x00"
            continue
        if ch == "\x00":
            continue
        if prev_joins and next_joins:
            form = "medial"
        elif prev_joins:
            form = "final"
        elif next_joins:
            form = "initial"
        else:
            form = "isolated"
        out.append(forms[ch].get(form) or forms[ch].get("isolated") or ch)
    return "".join(c for c in out if c != "\x00")


_MIRROR = str.maketrans("()[]{}<>", ")(][}{><")


def _visual(text: str) -> str:
    """Logical -> visual order for a right-to-left paragraph."""
    kinds = []
    for ch in text:
        if is_rtl_char(ch):
            kinds.append("R")
        elif ch.isalnum():
            kinds.append("L")
        else:
            kinds.append("N")
    # neutrals between two strong characters of the same kind take that kind; otherwise they follow the paragraph (R)
    resolved = list(kinds)
    i = 0
    while i < len(kinds):
        if kinds[i] != "N":
            i += 1
            continue
        j = i
        while j < len(kinds) and kinds[j] == "N":
            j += 1
        left = kinds[i - 1] if i > 0 else "R"
        right = kinds[j] if j < len(kinds) else "R"
        fill = left if left == right else "R"
        # a number followed by a neutral like "." or "," stays with the number
        for k in range(i, j):
            resolved[k] = fill
        i = j
    runs: list[tuple[str, str]] = []
    for ch, kind in zip(text, resolved):
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + ch)
        else:
            runs.append((kind, ch))
    pieces = [run[::-1].translate(_MIRROR) if kind == "R" else run for kind, run in reversed(runs)]
    return "".join(pieces)


# Glyphs the bundled font lacks -> the closest letter it does have (Urdu heh goal / yeh barree / full stop)
_SUBSTITUTE = {0x06C1: "\u0647", 0x06BE: "\u0647", 0x06D2: "\u0649", 0x06D3: "\u0649", 0x06D4: "."}


def shape(text: str) -> str:
    """Text ready to be drawn left-to-right by ReportLab (joined letters, visual order)."""
    if not text or not any(is_rtl_char(c) for c in text):
        return text
    text = text.translate(_SUBSTITUTE)                      # before joining, so the replacement letters join properly
    return "\n".join(_visual(_join(line)) for line in text.split("\n"))


def register_fonts() -> tuple[str, str]:
    """Register the bundled DejaVu Sans (Latin, Cyrillic, Greek, Arabic) once; returns (regular, bold) names."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if "LLSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("LLSans", str(FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("LLSans-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    return "LLSans", "LLSans-Bold"
