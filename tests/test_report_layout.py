"""Render the user's production letter through the real two-page layout gate."""
import json
from pathlib import Path

import pytest

from allocation import MODEL_VERSION, fingerprint
from conftest import stage_out


def test_production_letter_fits_two_pages(ran, monkeypatch):
    from playwright.sync_api import sync_playwright
    import render_pdf
    out = ran / "output/ALBERT"
    monkeypatch.setattr(render_pdf, "OUT", out)
    text = (Path(__file__).parent / "fixtures/streamlit_letter_2025_04.txt").read_text(encoding="utf-8")
    paragraphs = text.strip().split("\n\n")
    sections = {"highlights": paragraphs[:3], "greeting": paragraphs[3],
                "performance": paragraphs[4], "macro": paragraphs[5],
                "recommendations": paragraphs[6:-3], "coverage": paragraphs[-3],
                "closing": paragraphs[-2]}
    target, figures = stage_out(ran, "allocation_target.json"), stage_out(ran, "figures.json")
    paths = [out / n for n in ("letter_sections.json", "generation_log.json")]
    previous = {p: p.read_bytes() if p.exists() else None for p in paths}
    try:
        paths[0].write_text(json.dumps(sections), encoding="utf-8")
        paths[1].write_text(json.dumps({"approved": True, "model_version": MODEL_VERSION,
            "figures_fingerprint": fingerprint(figures), "allocation_fingerprint": target["fingerprint"]}), encoding="utf-8")
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:
                pytest.skip(f"Layout browser unavailable: {exc}")
            try:
                page = browser.new_page()
                attempts = []
                for fit, annex, label in render_pdf.FIT_STEPS:
                    html = render_pdf.build_html(fit, annex)
                    overflow = render_pdf.measure(page, html)
                    attempts.append((label, overflow))
                    if not overflow:
                        assert page.locator(".page").count() == 2
                        assert "Concentração em Riza Lotus" in html
                        break
                else:
                    pytest.fail(f"Production letter does not fit: {attempts}")
                page.close()
            finally:
                browser.close()
    finally:
        for p, content in previous.items():
            if content is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(content)
