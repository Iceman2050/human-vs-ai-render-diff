"""Command-line interface for human-vs-ai-render-diff.

Built by Ingenious AI.

Usage:
    hvaird email.html
    hvaird email.html --html report.html
    hvaird email.html --json report.json --no-color

Exit codes: 0 = clean (no hidden content), 1 = hidden payloads detected,
2 = usage / file error.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import sys
from pathlib import Path

from . import RenderView, analyze_file

KIND_LABELS = {
    "comment-hidden": "HTML comment",
    "css-hidden": "CSS-hidden (display:none / visibility:hidden / hidden)",
    "color-hidden": "color-hidden (text matches background)",
    "zero-width": "zero-width characters",
    "base64-hidden": "base64-hidden payload",
    "base64-visible": "base64 payload (visible to humans too)",
}

_KIND_COLORS = {
    "comment-hidden": "\033[33m",   # yellow
    "css-hidden": "\033[31m",       # red
    "color-hidden": "\033[35m",     # magenta
    "zero-width": "\033[36m",       # cyan
    "base64-hidden": "\033[91m",    # bright red
    "base64-visible": "\033[90m",   # grey
}
_RESET = "\033[0m"
_BOLD = "\033[1m"


def _badge(kind: str, use_color: bool) -> str:
    if use_color:
        return f"{_KIND_COLORS.get(kind, '')}{_BOLD}[{kind}]{_RESET}"
    return f"[{kind}]"


def render_terminal(view: RenderView, source: str, use_color: bool = True) -> str:
    """Render the diff as terminal-friendly text."""
    L: list[str] = []
    L.append("=" * 70)
    L.append("HUMAN vs AI RENDER DIFF")
    L.append(f"Source: {source}")
    L.append("=" * 70)
    L.append("")
    L.append(f"HUMAN-VISIBLE TEXT ({len(view.human_text)} chars)")
    L.append("-" * 70)
    L.append(view.human_text or "(nothing visible)")
    L.append("")
    L.append(f"AI-INGESTED TEXT ({len(view.ai_text)} chars)")
    L.append("-" * 70)
    L.append(view.ai_text or "(nothing ingested)")
    L.append("")
    L.append(f"DIFF — {len(view.spans)} span(s) the AI sees that the human does not")
    L.append("-" * 70)
    if not view.spans:
        L.append("No hidden content. Both views are identical.")
    for i, span in enumerate(view.spans, 1):
        L.append(f"{i}. {_badge(span.kind, use_color)} "
                 f"({KIND_LABELS.get(span.kind, span.kind)})")
        L.append(f"   AI chars {span.ai_start}-{span.ai_end}")
        L.append(f'   "{span.preview}"')
        if span.decoded:
            L.append(f"   decodes to: \"{span.decoded[:200]}\"")
    L.append("")
    if view.clean:
        L.append("VERDICT: CLEAN — human and AI see the same text.")
    else:
        L.append(f"VERDICT: {len(view.spans)} hidden payload(s) detected — "
                 "the AI ingests content the human never sees.")
    return "\n".join(L)


_HTML_CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#111}
h1{font-size:1.4rem} h2{font-size:1.1rem;margin-top:2rem}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
.col{border:1px solid #ddd;border-radius:8px;padding:1rem;background:#fafafa;white-space:pre-wrap;word-break:break-word;font-size:.9rem}
.badge{display:inline-block;font-size:.7rem;font-weight:700;padding:.1rem .5rem;border-radius:999px;margin-right:.4rem;color:#111}
.kind-comment-hidden{background:#fef3c7}.kind-css-hidden{background:#fecaca}
.kind-color-hidden{background:#e9d5ff}.kind-zero-width{background:#c7d2fe}
.kind-base64-hidden{background:#fca5a5}.kind-base64-visible{background:#e5e7eb}
table{width:100%;border-collapse:collapse;font-size:.85rem;margin-top:1rem}
th,td{text-align:left;padding:.5rem;border-bottom:1px solid #eee;vertical-align:top}
.verdict{font-weight:700;padding:.8rem 1rem;border-radius:8px;margin-top:1.5rem}
.verdict.clean{background:#d1fae5}.verdict.dirty{background:#fee2e2}
footer{margin-top:2rem;font-size:.8rem;color:#666}
"""


def render_html(view: RenderView, source: str) -> str:
    """Render a side-by-side HTML report with highlighted hidden spans."""
    ai_escaped = html_lib.escape(view.ai_text)
    # Highlight spans in the AI column, from last to first so offsets hold.
    highlighted = ai_escaped
    for span in sorted(view.spans, key=lambda s: s.ai_start, reverse=True):
        # Offsets were computed on the unescaped text; escape() only changes
        # length for <, >, & — re-derive by escaping piecewise instead.
        pre = html_lib.escape(view.ai_text[: span.ai_start])
        mid = html_lib.escape(view.ai_text[span.ai_start: span.ai_end])
        post = html_lib.escape(view.ai_text[span.ai_end:])
        highlighted = (
            f"{pre}<mark class=\"kind-{span.kind}\" "
            f"title=\"{span.kind}\">{mid}</mark>{post}"
        )

    rows = []
    for i, span in enumerate(view.spans, 1):
        decoded = html_lib.escape(span.decoded[:200]) if span.decoded else "—"
        rows.append(
            f"<tr><td>{i}</td>"
            f"<td><span class=\"badge kind-{span.kind}\">{span.kind}</span></td>"
            f"<td>{html_lib.escape(span.preview[:300])}</td>"
            f"<td>{decoded}</td></tr>"
        )

    verdict_cls = "clean" if view.clean else "dirty"
    verdict_txt = (
        "CLEAN — human and AI see the same text."
        if view.clean
        else f"{len(view.spans)} hidden payload(s) detected — the AI ingests "
        "content the human never sees."
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Human vs AI Render Diff — {html_lib.escape(source)}</title>
<style>{_HTML_CSS}</style></head>
<body>
<h1>Human vs AI Render Diff</h1>
<p>Source: <code>{html_lib.escape(source)}</code></p>
<div class="cols">
<div><h2>👁 Human-visible text ({len(view.human_text)} chars)</h2>
<div class="col">{html_lib.escape(view.human_text) or "(nothing visible)"}</div></div>
<div><h2>🤖 AI-ingested text ({len(view.ai_text)} chars)</h2>
<div class="col">{highlighted or "(nothing ingested)"}</div></div>
</div>
<h2>Diff — {len(view.spans)} span(s) the AI sees that the human does not</h2>
<table><tr><th>#</th><th>Classification</th><th>Content (escaped)</th>
<th>Decoded payload</th></tr>
{''.join(rows) or '<tr><td colspan="4">No hidden content.</td></tr>'}
</table>
<div class="verdict {verdict_cls}">VERDICT: {verdict_txt}</div>
<footer>Generated by <strong>human-vs-ai-render-diff</strong> — Built by Ingenious AI.</footer>
</body></html>
"""


def view_to_json(view: RenderView, source: str) -> dict:
    return {
        "source": source,
        "human_text": view.human_text,
        "ai_text": view.ai_text,
        "clean": view.clean,
        "spans": [
            {
                "kind": s.kind,
                "text": s.text,
                "preview": s.preview,
                "ai_start": s.ai_start,
                "ai_end": s.ai_end,
                "decoded": s.decoded,
                "context": s.context,
            }
            for s in view.spans
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hvaird",
        description="Show what an AI sees in an HTML email that a human does not.",
    )
    p.add_argument("input", help="Path to the HTML email file to analyze.")
    p.add_argument("--html", metavar="OUT.html",
                   help="Write a side-by-side HTML report to OUT.html.")
    p.add_argument("--json", metavar="OUT.json",
                   help="Write the diff as JSON to OUT.json.")
    p.add_argument("--no-color", action="store_true",
                   help="Disable ANSI colors in terminal output.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.input)
    if not path.is_file():
        print(f"hvaird: error: file not found: {args.input}", file=sys.stderr)
        return 2
    try:
        view = analyze_file(path)
    except OSError as exc:
        print(f"hvaird: error: cannot read {args.input}: {exc}", file=sys.stderr)
        return 2

    print(render_terminal(view, str(path), use_color=not args.no_color))

    if args.html:
        Path(args.html).write_text(render_html(view, str(path)), encoding="utf-8")
        print(f"\nHTML report written to {args.html}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(view_to_json(view, str(path)), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nJSON report written to {args.json}")

    return 0 if view.clean else 1


if __name__ == "__main__":
    sys.exit(main())
