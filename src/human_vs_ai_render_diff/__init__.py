"""human-vs-ai-render-diff: reveal what an AI sees in an HTML email that a human does not.

Built by Ingenious AI.

A single email can carry two different attacks: what the human sees versus what an
AI summarizer ingests (HTML comments, ``display:none`` CSS, zero-width characters,
invisible-color text, base64-hidden blobs). This module renders both views side by
side and highlights the delta.

Stdlib only. No third-party dependencies.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Zero-width characters attackers use to hide data in plain sight.
ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\ufeff"}

_ZW_NAMES = {
    "\u200b": "ZWSP",
    "\u200c": "ZWNJ",
    "\u200d": "ZWJ",
    "\ufeff": "ZWNBSP",
}

#: Minimum length of a base64-looking blob before we try to decode it.
B64_MIN_LEN = 40

_STYLE_DECL_RE = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;]+)")
_B64_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,3}")
_HEX3_RE = re.compile(r"#([0-9a-f]{3})\Z")
_HEX6_RE = re.compile(r"#([0-9a-f]{6})\Z")
_RGB_RE = re.compile(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)\Z")

_NAMED_COLORS = {
    "white": "#ffffff",
    "black": "#000000",
    "red": "#ff0000",
    "lime": "#00ff00",
    "green": "#008000",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "cyan": "#00ffff",
    "aqua": "#00ffff",
    "magenta": "#ff00ff",
    "fuchsia": "#ff00ff",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "maroon": "#800000",
    "navy": "#000080",
    "teal": "#008080",
    "purple": "#800080",
    "orange": "#ffa500",
}

_VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)

# Diff span classifications, in priority order for overlapping techniques.
KIND_PRIORITY = (
    "comment-hidden",
    "css-hidden",
    "color-hidden",
    "zero-width",
    "base64-hidden",
    "base64-visible",
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def escape_hidden(text: str) -> str:
    """Render invisible characters visibly so a human can inspect a diff span."""
    out = []
    for ch in text:
        name = _ZW_NAMES.get(ch)
        if name is not None:
            out.append("<%s>" % name)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return "".join(out)


def parse_style(style: str) -> dict:
    """Parse an inline ``style`` attribute into a lowercase property dict."""
    return {k.lower(): v.strip() for k, v in _STYLE_DECL_RE.findall(style or "")}


def normalize_color(value: str | None) -> str | None:
    """Normalize a CSS color to ``#rrggbb`` so equivalent colors compare equal.

    Returns ``None`` when the value is missing or not a comparable color.
    """
    if not value:
        return None
    v = value.strip().lower()
    if v in ("transparent", "inherit", "initial", "unset", "currentcolor"):
        return None
    if v in _NAMED_COLORS:
        return _NAMED_COLORS[v]
    m = _HEX3_RE.match(v)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1))
    m = _HEX6_RE.match(v)
    if m:
        return "#" + m.group(1)
    m = _RGB_RE.match(v)
    if m:
        return "#%02x%02x%02x" % tuple(min(255, int(x)) for x in m.groups())
    return None


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _text_ratio(text: str) -> float:
    """Fraction of characters that are printable or whitespace."""
    if not text:
        return 0.0
    ok = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    return ok / len(text)


# ---------------------------------------------------------------------------
# HTML walk: track provenance of every text segment
# ---------------------------------------------------------------------------

class _EmailWalk(HTMLParser):
    """Walk an HTML email, tagging every text segment with its provenance.

    Segments are ``(text, frozenset(tags))`` where tags is a subset of
    ``{"visible", "comment", "css-hidden", "color-hidden"}``.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.segments: list[tuple[str, frozenset]] = []
        self._stack: list[dict] = []
        self._skip_depth = 0  # inside <script>/<style>

    # -- stack -------------------------------------------------------------
    def _state(self) -> dict:
        if self._stack:
            return self._stack[-1]
        return {"css_hidden": False, "color": None, "bgcolor": None}

    def _push(self, attrs: list[tuple[str, str | None]]) -> None:
        parent = self._state()
        d = {k.lower(): (v or "") for k, v in attrs}
        style = parse_style(d.get("style", ""))
        css_hidden = parent["css_hidden"]
        if (
            style.get("display", "").lower() == "none"
            or style.get("visibility", "").lower() in ("hidden", "collapse")
            or "hidden" in d
        ):
            css_hidden = True
        color = style.get("color") or parent["color"]
        bgcolor = (
            style.get("background-color")
            or style.get("background")
            or parent["bgcolor"]
        )
        self._stack.append(
            {"css_hidden": css_hidden, "color": color, "bgcolor": bgcolor}
        )

    # -- HTMLParser hooks ---------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
        self._push(attrs)
        if tag in _VOID_ELEMENTS and self._stack:
            self._stack.pop()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing / void: contributes no text and no stack frame.
        pass

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data.strip():
            return
        state = self._state()
        tags: set[str] = set()
        if state["css_hidden"]:
            tags.add("css-hidden")
        fg = normalize_color(state["color"])
        bg = normalize_color(state["bgcolor"])
        if fg is not None and bg is not None and fg == bg:
            tags.add("color-hidden")
        if not tags:
            tags.add("visible")
        self.segments.append((data, frozenset(tags)))

    def handle_comment(self, data: str) -> None:
        if data.strip():
            self.segments.append((data, frozenset({"comment"})))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class DiffSpan:
    """One stretch of AI-ingested text that a human would not see."""

    kind: str            # one of KIND_PRIORITY
    text: str            # raw text as the AI would ingest it
    preview: str         # escaped for human inspection
    ai_start: int        # char offset in ai_text
    ai_end: int
    decoded: str = ""    # decoded payload, for base64 spans
    context: str = ""    # short surrounding AI text


@dataclass
class RenderView:
    human_text: str
    ai_text: str
    spans: list[DiffSpan] = field(default_factory=list)

    @property
    def kinds(self) -> set[str]:
        return {s.kind for s in self.spans}

    @property
    def clean(self) -> bool:
        return not self.spans


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _find_base64_blobs(ai_text: str) -> list[tuple[int, int, str, str]]:
    """Find base64-looking blobs that decode to readable text.

    Returns ``(start, end, blob, decoded_text)`` tuples.
    """
    found = []
    for m in _B64_RE.finditer(ai_text):
        blob = m.group(0)
        if len(blob) < B64_MIN_LEN or len(blob) % 4 == 1:
            continue
        try:
            raw = base64.b64decode(blob, validate=True)
        except Exception:
            continue
        if len(raw) < 12:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _text_ratio(text) < 0.85:
            continue
        found.append((m.start(), m.end(), blob, text))
    return found


def analyze(html: str) -> RenderView:
    """Render both views of an HTML email and diff them.

    * ``human_text`` — what a person sees: comments stripped, ``display:none`` /
      ``visibility:hidden`` / ``hidden`` elements removed, zero-width characters
      removed, same-color-as-background text removed, scripts/styles removed.
    * ``ai_text`` — the raw text an AI summarizer would ingest: everything above
      included (comments included as text, hidden spans included).
    * ``spans`` — AI-only stretches, each classified as ``comment-hidden``,
      ``css-hidden``, ``color-hidden``, ``zero-width``, ``base64-hidden`` (or
      ``base64-visible`` for decodable blobs a human could also see).
    """
    walk = _EmailWalk()
    walk.feed(html)
    walk.close()

    human_parts: list[str] = []
    ai_parts: list[str] = []
    spans: list[DiffSpan] = []
    ai_offset = 0

    for text, tags in walk.segments:
        piece = normalize_ws(text)
        if not piece:
            continue
        start = ai_offset
        end = start + len(piece)
        ai_offset = end + 1  # +1 for the joining space
        ai_parts.append(piece)

        if "comment" in tags:
            kind = "comment-hidden"
        elif "css-hidden" in tags:
            kind = "css-hidden"
        elif "color-hidden" in tags:
            kind = "color-hidden"
        else:
            kind = None

        if kind is not None:
            spans.append(DiffSpan(kind, piece, escape_hidden(piece), start, end))
        else:
            has_zw = any(ch in ZERO_WIDTH for ch in piece)
            cleaned = "".join(ch for ch in piece if ch not in ZERO_WIDTH)
            if cleaned.strip():
                human_parts.append(cleaned)
            if has_zw:
                spans.append(
                    DiffSpan("zero-width", piece, escape_hidden(piece), start, end)
                )

    ai_text = " ".join(ai_parts)
    human_text = " ".join(human_parts)

    # Base64-hidden payloads: scan the whole AI view, classify by visibility.
    for start, end, blob, decoded in _find_base64_blobs(ai_text):
        kind = "base64-hidden" if blob not in human_text else "base64-visible"
        if kind == "base64-hidden" and blob not in human_text:
            pass
        ctx_start = max(0, start - 40)
        ctx_end = min(len(ai_text), end + 40)
        spans.append(
            DiffSpan(
                kind,
                blob,
                escape_hidden(blob[:60] + ("…" if len(blob) > 60 else "")),
                start,
                end,
                decoded=decoded,
                context=normalize_ws(ai_text[ctx_start:ctx_end]),
            )
        )

    spans.sort(key=lambda s: (s.ai_start, KIND_PRIORITY.index(s.kind)))
    return RenderView(human_text=human_text, ai_text=ai_text, spans=spans)


def analyze_file(path: str | Path) -> RenderView:
    """Analyze an HTML email file on disk."""
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    return analyze(html)
