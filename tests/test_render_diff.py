"""Tests for human-vs-ai-render-diff. All must pass."""

from pathlib import Path

import pytest

from human_vs_ai_render_diff import analyze, analyze_file

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def kinds_of(view):
    return {s.kind for s in view.spans}


# ---------------------------------------------------------------------------
# Classification tests — one per hiding technique
# ---------------------------------------------------------------------------

def test_comment_hidden_detected():
    view = analyze_file(FIXTURES / "comment-hidden.html")
    assert "comment-hidden" in kinds_of(view)
    span = next(s for s in view.spans if s.kind == "comment-hidden")
    assert "PROMPT-INJECTION" in span.text
    assert "PROMPT-INJECTION" not in view.human_text
    assert "PROMPT-INJECTION" in view.ai_text


def test_css_hidden_detected():
    view = analyze_file(FIXTURES / "css-hidden.html")
    assert "css-hidden" in kinds_of(view)
    texts = " ".join(s.text for s in view.spans if s.kind == "css-hidden")
    assert "evil-corp.example" in texts
    assert "evil-corp.example" not in view.human_text


def test_zero_width_detected():
    view = analyze_file(FIXTURES / "zero-width.html")
    assert "zero-width" in kinds_of(view)
    span = next(s for s in view.spans if s.kind == "zero-width")
    # The raw span is pure zero-width characters…
    assert span.text.strip() != "" and all(
        ch in {"\u200b", "\u200c", "\u200d", "\ufeff"} or ch.isspace()
        for ch in span.text
    )
    # …escaped visibly in the preview so a human can inspect it.
    assert "<ZWSP>" in span.preview or "<ZWNJ>" in span.preview


def test_base64_hidden_detected():
    view = analyze_file(FIXTURES / "base64-hidden.html")
    assert "base64-hidden" in kinds_of(view)
    span = next(s for s in view.spans if s.kind == "base64-hidden")
    assert "Forward this email thread" in span.decoded
    assert "hq@evil-corp.example" in span.decoded


def test_color_hidden_detected():
    view = analyze_file(FIXTURES / "color-hidden.html")
    assert "color-hidden" in kinds_of(view)
    texts = " ".join(s.text for s in view.spans if s.kind == "color-hidden")
    assert "moved to January 1st" in texts
    assert "it-helpdesk@example.invalid" in texts
    assert "January 1st" not in view.human_text


# ---------------------------------------------------------------------------
# Behaviour tests
# ---------------------------------------------------------------------------

def test_clean_email_empty_diff():
    view = analyze_file(FIXTURES / "clean.html")
    assert view.spans == []
    assert view.clean
    assert view.human_text == view.ai_text
    assert "Q3 roadmap update" in view.human_text


def test_zero_width_only_payload_caught():
    # A payload made *only* of zero-width chars: human sees nothing,
    # the AI ingests it. Must be flagged, not silently dropped.
    html = (
        "<html><body><p>Visible intro sentence.</p>"
        "<p>\u200b\u200c\u200b\u200d\u200b\u200c\u200d\u200b</p>"
        "<p>Visible outro sentence.</p></body></html>"
    )
    view = analyze(html)
    zw = [s for s in view.spans if s.kind == "zero-width"]
    assert zw, "zero-width-only payload was not caught"
    assert "Visible intro" in view.human_text
    assert "Visible outro" in view.human_text
    # The payload is invisible in the human view…
    assert "\u200b" not in view.human_text
    # …but present in the AI view.
    assert "\u200b" in view.ai_text


def test_combo_email_all_techniques():
    view = analyze_file(FIXTURES / "combo.html")
    kinds = kinds_of(view)
    assert {"comment-hidden", "css-hidden", "color-hidden", "zero-width"} <= kinds


def test_script_style_excluded_from_both_views():
    html = (
        "<html><head><style>p{color:red}</style>"
        "<script>alert('xss')</script></head>"
        "<body><p>Hello world</p></body></html>"
    )
    view = analyze(html)
    assert view.clean
    assert "alert" not in view.ai_text
    assert "color:red" not in view.ai_text


def test_cli_smoke(capsys, tmp_path):
    from human_vs_ai_render_diff.cli import main

    rc = main([str(FIXTURES / "comment-hidden.html"), "--no-color"])
    assert rc == 1  # findings -> exit 1
    out = capsys.readouterr().out
    assert "comment-hidden" in out
    assert "VERDICT" in out

    rc = main([str(FIXTURES / "clean.html"), "--no-color"])
    assert rc == 0  # clean -> exit 0
    out = capsys.readouterr().out
    assert "CLEAN" in out


def test_html_report_marks_spans(tmp_path):
    from human_vs_ai_render_diff.cli import render_html

    view = analyze_file(FIXTURES / "combo.html")
    report = render_html(view, "combo.html")
    assert "<mark" in report
    assert "comment-hidden" in report
    assert "Human-visible text" in report
    assert "AI-ingested text" in report
    out = tmp_path / "report.html"
    out.write_text(report, encoding="utf-8")
    assert out.stat().st_size > 1000
