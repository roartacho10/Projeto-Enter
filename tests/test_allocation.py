"""Independent arithmetic, scenario sensitivity, and executable cash-flow checks."""
from datetime import date
from types import SimpleNamespace
import copy
import json

import pandas as pd
import pytest

from allocation import estimate, macro_factor, MODEL_VERSION
from rebalance import build_plan
from conftest import stage_out


def projections():
    return [{"variable_id": k, "year": y, "value": v, "is_projection": True}
            for y, vals in [(2025, (2, 6.1, 15.5)), (2026, (1, 4.5, 12.5))]
            for k, v in zip(("pib", "ipca", "selic"), vals)]


def test_hand_calculated_cumulative_returns_and_target():
    a = estimate(projections(), date(2025, 4, 30), 25, 5, 10, date(2026, 12, 31))
    rv = (1.07 ** (8 / 12) * 1.06 - 1) * 100
    rf = ((1.155 / 1.061) ** (8 / 12) * (1.125 / 1.045) - 1) * 100
    assert a["rv_real_cumulative_pct"] == pytest.approx(rv)
    assert a["rf_real_cumulative_pct"] == pytest.approx(rf)
    assert a["target_rv_pct"] == pytest.approx(25 * (0.5 + (rv - rf) / 20))
    assert a["start"] == "2025-05-01"
    assert [r["months"] for r in a["annual"]] == [8, 12]
    assert sum(a[k] for k in ("target_rv_pct", "target_rf_pct", "target_cash_pct")) == pytest.approx(100)


@pytest.mark.parametrize("spread,factor", [(-100, 0), (-10, 0), (-5, .25), (0, .5), (5, .75), (10, 1), (100, 1)])
def test_approved_calibration(spread, factor):
    assert macro_factor(spread, 10) == factor


def test_macro_changes_direction_without_breaking_any_profile_ceiling():
    base = projections()
    optimistic = [{**r, "value": r["value"] + 2 if r["variable_id"] == "pib" else r["value"]} for r in base]
    high_rates = [{**r, "value": r["value"] + 2 if r["variable_id"] == "selic" else r["value"]} for r in base]
    for ceiling in (0, 5, 10, 15, 25, 40, 70, 85):
        results = [estimate(p, date(2025, 4, 30), ceiling, 5, 10, date(2026, 12, 31))["target_rv_pct"]
                   for p in (high_rates, base, optimistic)]
        assert 0 <= results[0] <= results[1] <= results[2] <= ceiling
        if ceiling:
            assert results[0] < results[1] < results[2]


@pytest.mark.parametrize("mode", ["missing", "duplicate", "nan", "historical"])
def test_invalid_macro_never_silently_falls_back(mode):
    p = projections()
    if mode == "missing": p.pop()
    elif mode == "duplicate": p.append(dict(p[0]))
    elif mode == "nan": p[0]["value"] = float("nan")
    else: p[0]["is_projection"] = False
    with pytest.raises(ValueError):
        estimate(p, date(2025, 4, 30), 25, 5, 10, date(2026, 12, 31))


def test_window_boundaries_and_missing_forecast_horizon():
    a = estimate(projections(), date(2025, 12, 31), 25, 5, 10, date(2026, 12, 31))
    assert a["start"] == "2026-01-01"
    assert len(a["annual"]) == 1 and a["annual"][0]["months"] == 12
    with pytest.raises(ValueError):
        estimate(projections(), date(2026, 12, 31), 25, 5, 10, date(2026, 12, 31))
    with pytest.raises(ValueError):
        macro_factor(2, 0)


def test_claim_gate_does_not_swap_class_targets_or_forecasts():
    from claims import check_allocation_claims
    target = {"target_rv_pct": 8, "target_rf_pct": 92,
              "rv_real_cumulative_pct": 11, "rf_real_cumulative_pct": 14}
    assert not check_allocation_claims("Alvo de RV de 8,00%. Alvo de renda fixa: 92,00%.", target)
    assert check_allocation_claims("Alvo de RV de 92,00%.", target)[0][1] == "claims_allocation"
    assert check_allocation_claims("Retorno real estimado acumulado da RV de 14,00%.", target)


@pytest.mark.parametrize("rv_target", [0, 0.001, 8.27, 25, 85, 100])
def test_simulation_balances_to_cents_and_caps_each_rf_product(rv_target):
    vals = {"STOCK": 1300.01, "FUND": 300.03, "RF1": 3000.02, "RF2": 2300.04,
            "OLD": 500.01, "CASH": 800.01}
    pack = SimpleNamespace(run_id="test", client_id="ALBERT", period_end=date(2025, 4, 30),
                           total_value_end=sum(vals.values()),
                           positions=[SimpleNamespace(instrument_id=k, value_end=v) for k, v in vals.items()])
    ins = {k: {"type": "cash" if k == "CASH" else "fund",
               "risk_category": "equity" if k in ("STOCK", "FUND") else "fixed",
               "maturity_date": "2024-09-05" if k == "OLD" else ""} for k in vals}
    cats = {"equity": {"is_equity_exposure": "1"}, "fixed": {"is_equity_exposure": "0"}}
    target = {"metrics_run_id": "test", "model_version": MODEL_VERSION,
              "target_rv_pct": rv_target, "fingerprint": "test"}
    params = {"TARGET_CASH_PCT": "0", "MAX_PRODUCT_PCT": "25",
              "DESTINATION_CATEGORY": "RF", "DESTINATION_CRITERIA": "BB+"}
    snapshot = copy.deepcopy(ins)
    plan = build_plan(pack, ins, cats, target, params, SimpleNamespace(recommendations=[]))
    assert plan.resolved and not plan.violations_after
    assert ins == snapshot
    cash = round(vals["CASH"] * 100)
    ledger = {k: round(v * 100) for k, v in vals.items() if k != "CASH"}
    for trade in plan.trades:
        amount = round(trade.amount_brl * 100)
        assert amount > 0
        if trade.action == "buy":
            cash -= amount
            assert trade.instrument_id is None and trade.min_issuers is None
            assert trade.min_products >= 1
        else:
            ledger[trade.instrument_id] -= amount
            cash += amount
    assert cash == 0 and all(v >= 0 for v in ledger.values())
    assert ledger["STOCK"] == ledger["FUND"] == ledger["OLD"] == 0
    rf = [p for p in plan.post_positions if p["asset_class"] == "RF"]
    total_rf = sum(round(p["value_brl"] * 100) for p in rf)
    assert all(round(p["value_brl"] * 100) <= total_rf / 4 for p in rf)
    rv = [p for p in plan.post_positions if p["asset_class"] == "RV"]
    assert all(p["index_fund"] for p in rv)
    assert sum(round(p["value_brl"] * 100) for p in plan.post_positions) == round(pack.total_value_end * 100)


def test_real_case_uses_new_target_and_preserves_base(ran):
    from conftest import ROOT
    assert (ran / "data/reference/positions.csv").read_bytes() == (ROOT / "data/reference/positions.csv").read_bytes()
    a, p = stage_out(ran, "allocation_target.json"), stage_out(ran, "rebalance_plan.json")
    assert 0 < a["target_rv_pct"] < a["rv_ceiling_pct"] == 25
    assert p["allocation_fingerprint"] == a["fingerprint"]
    assert p["pending_reviews"], "Retained restricted RF products must remain visible for review"
    f = stage_out(ran, "figures.json")
    assert "alocacao_target_rv_pct" in f and "alocacao_rf_real_cumulative_pct" in f
    assert p["cash_after_brl"] == 0


def test_streamlit_displays_the_new_targets_and_hides_old_reports(ran):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ran / "app.py")).run(timeout=45)
    assert not app.exception, [e.message for e in app.exception]
    labels = {m.label: m.value for m in app.metric}
    assert labels["Caixa · alvo"] == "0,00%"
    assert "Renda variável · alvo" in labels and "Renda fixa · alvo" in labels
    button = next(b for b in app.button if b.label.startswith("Gerar relatório de"))
    assert not button.disabled


def test_streamlit_can_open_legacy_results_and_request_refresh(ran):
    from streamlit.testing.v1 import AppTest
    from conftest import CLIENT
    path = ran / "output" / CLIENT / "metrics_pack.json"
    original = path.read_bytes()
    try:
        old = json.loads(original)
        del old["ipca_return_pct"]
        path.write_text(json.dumps(old), encoding="utf-8")
        app = AppTest.from_file(str(ran / "app.py")).run(timeout=45)
        assert not app.exception, [e.message for e in app.exception]
        assert any("formato antigo" in i.value for i in app.info)
        assert next(b for b in app.button if b.label.startswith("Gerar relatório de")).disabled
    finally:
        path.write_bytes(original)


def test_rejected_generation_returns_failure_without_real_api_calls(ran, tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    repo = tmp_path / "rejected-letter"
    shutil.copytree(ran, repo)
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "openai.py").write_text('''from types import SimpleNamespace
import json
class OpenAI:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self, **kwargs):
        content = json.dumps({"greeting": "Albert, valor inventado R$ 12.345,67.",
                              "highlights": ["Resumo."], "performance": "Resultado.",
                              "macro": "Contexto.", "recommendations": ["Sugestões."],
                              "coverage": "Cobertura.", "closing": "Até breve."})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
''', encoding="utf-8")
    env = dict(os.environ, CLIENT_ID="ALBERT", OPENAI_API_KEY="test",
               PYTHONPATH=str(stub) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    result = subprocess.run([sys.executable, str(repo / "src/generate_letter.py")], cwd=repo,
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1, result.stdout + result.stderr
    log = json.loads((repo / "output/ALBERT/generation_log.json").read_text(encoding="utf-8"))
    assert not log["approved"] and log["attempts"] == 3
    report = json.loads((repo / "output/ALBERT/verification_report.json").read_text(encoding="utf-8"))
    assert any(r["code"] == "ungrounded_number" for r in report)


def test_renderer_rejects_legacy_letter_then_builds_matching_summary(ran, monkeypatch):
    import render_pdf
    from allocation import fingerprint
    from conftest import CLIENT
    out = ran / "output" / CLIENT
    monkeypatch.setattr(render_pdf, "OUT", out)
    sections = {"highlights": ["Albert, segue a proposta para discussão."], "greeting": "Albert,",
                "performance": "Resultados do período.", "macro": "Cenário conforme o documento.",
                "recommendations": ["Sugestões sujeitas à aprovação."],
                "coverage": "Cobertura conforme o quadro.", "closing": "Podemos conversar sobre os ajustes."}
    paths = [out / n for n in ("letter_sections.json", "generation_log.json")]
    previous = {p: p.read_bytes() if p.exists() else None for p in paths}
    try:
        paths[0].write_text(json.dumps(sections), encoding="utf-8")
        paths[1].write_text(json.dumps({"approved": True}), encoding="utf-8")
        with pytest.raises(ValueError, match="Carta antiga"):
            render_pdf.build_html()
        figures, target = stage_out(ran, "figures.json"), stage_out(ran, "allocation_target.json")
        paths[1].write_text(json.dumps({"approved": True, "model_version": MODEL_VERSION,
            "figures_fingerprint": fingerprint(figures), "allocation_fingerprint": target["fingerprint"]}), encoding="utf-8")
        html = render_pdf.build_html(show_annex=False)
        assert "Alvo pelo modelo" in html and "Todas as posições atuais de RV" in html
        assert "mín. 1 produtos" in html and "25% da cesta por produto" in html
        assert "Executadas as operações acima" not in html
    finally:
        for p, data in previous.items():
            if data is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(data)
