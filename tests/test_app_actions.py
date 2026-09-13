"""A single submitted click must execute exactly one pipeline, without real API calls."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.parametrize("action", ["enter", "letter"])
@pytest.mark.parametrize("fails", [False, True])
def test_first_click_executes_once_and_rerender_never_repeats(ran, monkeypatch, action, fails):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    app = AppTest.from_file(str(ran / "app.py"))
    if action == "letter":
        app.session_state["entrou"] = True
    app.run(timeout=45)
    assert not app.exception
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((Path(cmd[1]).stem, kwargs["env"]))
        return SimpleNamespace(returncode=1 if fails else 0, stdout="simulated stage", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    if action == "enter":
        app.text_input(key="entrada_assessor").set_value("Assessor de teste")
        button = next(b for b in app.button if b.label == "Acessar")
    else:
        app.text_input(key="key_sidebar").set_value("test-only-new-key")
        app.selectbox(key="model_letter").set_value("gpt-4.1-mini")
        button = app.button(key="generate_letter")
    button.click().run(timeout=45)
    assert not app.exception, [e.message for e in app.exception]
    expected = 1 if fails else 6 if action == "enter" else 3
    assert len(calls) == expected
    if action == "letter":
        assert calls[0][1]["OPENAI_API_KEY"] == "test-only-new-key"
        assert calls[0][1]["MODEL_LETTER"] == "gpt-4.1-mini"
    if fails:
        assert app.error
    else:
        assert app.success
        if action == "enter":
            assert app.session_state["entrou"]
            assert app.button(key="generate_letter")
    assert not app.session_state["processing_action"]
    app.run(timeout=45)
    assert len(calls) == expected


def test_adjustments_use_the_same_display_names_as_month_table(ran):
    app = AppTest.from_file(str(ran / "app.py"))
    app.session_state["entrou"] = True
    app.run(timeout=45)
    assert not app.exception
    table = next(m.value for m in app.markdown if '<table class="mtable adjustments">' in m.value)
    assert "Riza Lotus Plus Advisory" in table
    assert "Truxt Long Bias" in table
    assert "CDB Banco C6 (vencido)" in table
    assert "FUND_RIZA_LOTUS" not in table and "CDB_C6_SET2024" not in table
