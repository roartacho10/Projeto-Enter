"""
Invariants of a full deterministic run, executed in a clean checkout.

These are the promises the architecture makes. Each one has either been broken
during development or is the reason a reviewer should trust the output.
"""
from __future__ import annotations
import json
import subprocess
import sys

import pandas as pd
import pytest

from conftest import CLIENT, stage_out


# ---------------------------------------------------------------- portability
def test_ran_in_offline_mode(ran):
    """
    The clean checkout has no CVM bulk files, so the pipeline must have read the
    versioned extract. If this ever fails, the repository stopped being
    self-sufficient and a fresh clone would not run.
    """
    log = pd.read_csv(ran / "data" / "processed" / "data_quality_log.csv").fillna("")
    assert "offline_extract" in set(log["code"]), \
        "esperado modo offline no checkout limpo (sem os arquivos volumosos da CVM)"


# ---------------------------------------------------------------- the numbers
def test_metrics_match_the_answer_key(ran):
    pack = stage_out(ran, "metrics_pack.json")
    golden = pd.read_csv(
        ran / "data" / "reference" / "golden_portfolio_2025-04.csv").set_index("id")
    assert pack["total_value_start"] == pytest.approx(
        float(golden.loc["__TOTAL_PATRIMONIO__", "value_start"]), abs=0.05)
    assert pack["total_return_pct"] == pytest.approx(
        float(golden.loc["__TOTAL_PATRIMONIO__", "return_pct"]), abs=0.02)
    assert pack["invested_return_pct"] == pytest.approx(
        float(golden.loc["__TOTAL_INVESTIDO__", "return_pct"]), abs=0.02)


def test_every_position_priced_by_a_declared_basis(ran):
    pack = stage_out(ran, "metrics_pack.json")
    pos = pd.read_csv(ran / "data" / "reference" / "positions.csv")
    pos = pos[pos["client_id"] == CLIENT]
    assert len(pack["positions"]) == len(pos), "posicao perdida entre o cadastro e o pack"
    for m in pack["positions"]:
        assert m["value_start"] >= 0 and m["value_end"] >= 0


def test_contributions_reconcile_with_the_total(ran):
    """The attribution must add up: sum of contributions = total return."""
    pack = stage_out(ran, "metrics_pack.json")
    soma = sum(m["contribution_pp"] for m in pack["positions"])
    assert soma == pytest.approx(pack["total_return_pct"], abs=0.02)


# ---------------------------------------------------------------- the profile
def test_profile_reads_three_parameters_with_evidence(ran):
    d = pd.read_csv(ran / "data" / "reference" / "client_profile.csv").fillna("")
    a = d[d["client_id"] == CLIENT].iloc[0]
    assert a["risk_class"] == "Moderado"
    assert a["objective"] == "poder_de_compra"
    assert a["horizon"] == "Medio"
    # the ambiguity in "medio a longo prazo" is resolved short AND recorded
    assert str(a["horizon_ambiguous"]) == "True"
    assert a["objective_quote"], "parametro sem a frase que o produziu"


def test_derived_policy_matches_the_matrix(ran):
    pol = pd.read_csv(ran / "output" / CLIENT / "suitability_policy.csv").set_index("rule_id")
    lim = pd.read_csv(ran / "data" / "reference" / "profile_limits.csv")
    cell = lim[(lim.risk_class == "Moderado") & (lim.horizon == "Medio")].iloc[0]
    assert float(pol.loc["MAX_EQUITY_LOOKTHROUGH_PCT", "threshold"]) == float(cell.max_equity_pct)
    assert float(pol.loc["MAX_SINGLE_POSITION_PCT", "threshold"]) == float(cell.max_single_pct)
    assert float(pol.loc["MAX_IDLE_CASH_PCT", "threshold"]) == float(cell.max_cash_pct)
    blocked = set(pol.loc["RESTRICTED_CATEGORY", "threshold"].split("|"))
    fam = pd.read_csv(ran / "data" / "reference" / "profile_families.csv").fillna("")
    expected = set(fam[(fam.risk_class == "Moderado") & (fam.allowed == 0)]["risk_category"])
    assert blocked == expected


def test_no_limit_claims_to_come_from_the_document(ran):
    """
    The risk-profile document contains no numbers. No rule may cite it as the
    source of one: numeric limits are declared equivalences.
    """
    pol = pd.read_csv(ran / "output" / CLIENT / "suitability_policy.csv").fillna("")
    for _, r in pol.iterrows():
        if r["rule_id"].startswith("MAX_"):
            assert r["source_type"] == "equivalencia", \
                f"{r['rule_id']} declara origem '{r['source_type']}'"


# ---------------------------------------------------------------- the plan
def test_rebalance_clears_every_violation(ran):
    plan = stage_out(ran, "rebalance_plan.json")
    assert plan["violations_before"], "nada a corrigir: o teste perdeu o sentido"
    assert plan["violations_after"] == [], \
        f"plano nao resolve: {plan['violations_after']}"


def test_no_trade_below_the_minimum_ticket(ran):
    """Seven orders of R$59 once reached the letter. Never again."""
    plan = stage_out(ran, "rebalance_plan.json")
    rpol = pd.read_csv(ran / "data" / "reference" / "rebalance_policy.csv").set_index("param")
    minimo = float(rpol.loc["MIN_TICKET_BRL", "value"])
    small = [t for t in plan["trades"] if t["amount_brl"] < minimo]
    assert not small, f"ordens abaixo do ticket minimo: {small}"


def test_buy_orders_name_a_family_not_a_product(ran):
    plan = stage_out(ran, "rebalance_plan.json")
    for t in plan["trades"]:
        if t["action"] == "buy":
            assert not t["instrument_id"], "compra nomeando produto especifico"
            assert t["category"] and t["criteria"]


# ---------------------------------------------------------------- the figures
def test_figure_sheet_is_all_strings_and_non_empty(ran):
    fig = stage_out(ran, "figures.json")
    assert len(fig) > 50
    for k, v in fig.items():
        assert isinstance(v, str) and v.strip(), f"cifra vazia ou nao-string: {k}"


def test_macro_figures_come_from_the_table_not_the_prose(ran):
    """
    The report contradicts itself on gross debt: the table says 80,3 and the
    narrative 79,9. The table is the authority, and the divergence is recorded.
    """
    fig = stage_out(ran, "figures.json")
    assert fig["macro_divida_bruta_2025"] == "80,3%"
    proj = pd.read_csv(ran / "data" / "reference" / "macro_projections.csv").fillna("")
    row = proj[(proj.variable_id == "divida_bruta") & (proj.year == 2025)].iloc[0]
    assert "DIVERGE" in row["prose_check"]


# ---------------------------------------------------------------- the gate
def _verify(repo, text: str) -> subprocess.CompletedProcess:
    p = repo / "output" / CLIENT / "_teste_carta.txt"
    p.write_text(text, encoding="utf-8")
    import os
    return subprocess.run([sys.executable, str(repo / "src" / "verify.py"), str(p)],
                          cwd=repo, env=dict(os.environ, CLIENT_ID=CLIENT),
                          capture_output=True, text=True)


def _base_letter(repo) -> str:
    fig = json.loads((repo / "output" / CLIENT / "figures.json").read_text(encoding="utf-8"))
    return (f"Albert, sua carteira encerrou o periodo em {fig['patrimonio_fim']}, "
            f"com retorno de {fig['retorno_patrimonio']}. A cobertura de marcacao a "
            f"mercado foi de {fig['cobertura_marcacao']}. Atenciosamente, Antonio Bicudo.")


def test_gate_passes_a_letter_with_only_authorised_figures(ran):
    r = _verify(ran, _base_letter(ran))
    assert r.returncode == 0, r.stdout


def test_gate_rejects_an_invented_number(ran):
    r = _verify(ran, _base_letter(ran) + " O resultado somou R$ 12.345,67 no periodo.")
    assert r.returncode == 1
    assert "ungrounded_number" in r.stdout
    assert "12.345,67" in r.stdout


def test_gate_does_not_flag_digits_inside_a_ticker(ran):
    r = _verify(ran, _base_letter(ran) + " As posicoes em HAPV3 e AZZA3 foram mantidas.")
    assert r.returncode == 0, r.stdout


def test_gate_rejects_forbidden_language(ran):
    r = _verify(ran, _base_letter(ran) + " Este investimento tem rentabilidade garantida.")
    assert r.returncode == 1
    assert "forbidden_language" in r.stdout


def test_gate_rejects_a_letter_missing_the_client_name(ran):
    r = _verify(ran, _base_letter(ran).replace("Albert, ", ""))
    assert r.returncode == 1
    assert "client_name_missing" in r.stdout


# ---------------------------------------------------------------- benchmarks
def test_no_blended_benchmark_survives(ran):
    """
    The composite "75% CDI + 25% Ibovespa" was invented here and no input file
    declared it - and it averaged a cash rate with an equity index into one
    number describing neither. It must not come back.
    """
    pack = stage_out(ran, "metrics_pack.json")
    for gone in ("benchmark_name", "benchmark_return_pct", "excess_return_pp"):
        assert gone not in pack, f"campo do benchmark composto reapareceu: {gone}"
    assert "cdi_return_pct" in pack and "ipca_return_pct" in pack


def test_excess_over_cdi_uses_the_invested_basis(ran):
    pack = stage_out(ran, "metrics_pack.json")
    assert pack["excess_over_cdi_pp"] == pytest.approx(
        pack["invested_return_pct"] - pack["cdi_return_pct"], abs=1e-4), \
        "excesso sobre o CDI precisa ser medido sobre os recursos investidos"


def test_real_return_is_a_deflation_not_a_subtraction(ran):
    """
    Real return is (1+r)/(1+i)-1. Writing r - i and calling it "retorno real"
    is wrong arithmetic; this fails if anyone simplifies it back.
    """
    pack = stage_out(ran, "metrics_pack.json")
    t, i = pack["total_return_pct"] / 100, pack["ipca_return_pct"] / 100
    geometrico = ((1 + t) / (1 + i) - 1) * 100
    aritmetico = pack["total_return_pct"] - pack["ipca_return_pct"]
    assert pack["real_return_total_pct"] == pytest.approx(geometrico, abs=1e-3)
    # and the two are genuinely different at this magnitude, so the check bites
    assert abs(geometrico - aritmetico) > 1e-3, \
        "inflacao muito baixa para distinguir as duas formulas: teste sem poder"


def test_figure_sheet_exposes_both_references(ran):
    fig = stage_out(ran, "figures.json")
    for k in ("cdi_periodo", "ipca_periodo", "excesso_sobre_cdi", "retorno_real_patrimonio"):
        assert k in fig, f"cifra ausente na folha autorizada: {k}"
    assert "benchmark_retorno" not in fig and "excesso_sobre_benchmark" not in fig


# ---------------------------------------------------------------- claim gate
# Numbers are not claims. Each case below quotes ONLY authorised figures, so it
# passes the figure gate; what separates them is whether the sentence is true.
CLAIMS = [
    ("MRFG3 subiu no periodo.", None),
    ("HAPV3 recuou no periodo.", "claims_direction"),
    ("Houve queda de LREN3 no mes.", "claims_direction"),
    ("A carteira superou o CDI no periodo.", None),
    ("O resultado ficou abaixo do CDI.", "claims_comparison"),
    ("Houve ganho real no periodo.", None),
    ("A carteira perdeu poder de compra.", "claims_comparison"),
    ("A carteira esta aderente ao mandato.", "claims_compliance"),
    ("O CDB foi resgatado conforme o plano.", "claims_execution"),
    ("Vendemos parte da posicao em MRFG3.", "claims_execution"),
    ("Recomendo vender parte de MRFG3.", None),
    ("Recomendo vender LREN3.", "claims_action"),
    # two subjects with opposite directions: ambiguous, skipped, not accused
    ("Apesar da queda de HAPV3, MRFG3 subiu com forca.", None),
    # the deepest one: an authorised figure attached to the wrong position
    ("MRFG3 rendeu 18,94% no mes.", None),
    ("MRFG3 rendeu 19,46% no mes.", "claims_attribution"),
    ("MRFG3 ajudou o patrimonio a render 4,07%.", None),
]


@pytest.mark.parametrize("frase, codigo", CLAIMS)
def test_claim_gate(ran, frase, codigo):
    r = _verify(ran, _base_letter(ran) + " " + frase)
    if codigo is None:
        assert r.returncode == 0, f"acusou uma frase verdadeira:\n{r.stdout}"
    else:
        assert r.returncode == 1, f"deixou passar uma frase falsa:\n{r.stdout}"
        assert codigo in r.stdout, f"esperado {codigo}, veio:\n{r.stdout}"


def test_claim_coverage_is_reported_not_implied(ran):
    """
    The gate checks a declared catalogue of claim shapes, not arbitrary prose.
    It must say how much it covered - a verifier that hides its reach is worse
    than one that has none.
    """
    r = _verify(ran, _base_letter(ran) + " MRFG3 subiu no periodo.")
    assert "claims_coverage" in r.stdout
    assert "afirmacao" in r.stdout and "frases" in r.stdout
