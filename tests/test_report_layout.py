"""Render the user's production letter through the real two-page layout gate."""
import json
import os
from pathlib import Path

import pytest

from allocation import MODEL_VERSION, fingerprint
from conftest import stage_out


@pytest.mark.parametrize("fixture", ["streamlit_letter_2025_04.txt", "streamlit_letter_layout_overflow.txt"])
def test_production_letter_fits_two_pages(ran, monkeypatch, fixture):
    from playwright.sync_api import sync_playwright
    import render_pdf
    out = ran / "output/ALBERT"
    monkeypatch.setattr(render_pdf, "OUT", out)
    text = (Path(__file__).parent / "fixtures" / fixture).read_text(encoding="utf-8")
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
                for fit, annex, macro_first, label in render_pdf.layout_candidates():
                    html = render_pdf.build_html(fit, annex, macro_first)
                    overflow = render_pdf.measure(page, html)
                    attempts.append((label, overflow))
                    if not overflow:
                        assert page.locator(".page").count() == 2
                        assert page.evaluate("document.fonts.check('12px ReportSans')")
                        assert page.locator("figure svg text").evaluate_all(
                            "els => els.every(el => getComputedStyle(el).fontFamily.includes('ReportSans'))")
                        for paragraph in paragraphs[:-1]:
                            assert paragraph in page.locator("body").inner_text()
                        assert page.get_by_text(sections["macro"], exact=True).count() == 1
                        print(f"{fixture}: {label}")
                        if os.environ.get("ENTER_LAYOUT_QA_DIR"):
                            qa = Path(os.environ["ENTER_LAYOUT_QA_DIR"])
                            qa.mkdir(parents=True, exist_ok=True)
                            page.pdf(path=str(qa / f"{Path(fixture).stem}.pdf"), format="A4", print_background=True)
                            for i, sheet in enumerate(page.locator(".page").all(), 1):
                                sheet.screenshot(path=str(qa / f"{Path(fixture).stem}-{i}.png"))
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
