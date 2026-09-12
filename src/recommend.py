"""
L3 - Suitability screens and recommendation engine.

Rules live in a CSV, not in this file: the client's own derived policy when a
risk-profile document produced one, the shared defaults otherwise. The
engine measures, compares against the declared threshold, and emits a
Recommendation carrying the observed fact, the limit it breached and the
clause of the client's profile document it comes from.

A language model never chooses an action here. It may later phrase one.

Run: python src/recommend.py
"""
from __future__ import annotations
from datetime import datetime, timezone, date
from pathlib import Path
import json
import sys
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import MetricsPack, Recommendation, RecommendationSet  # noqa: E402
from allocation import build_target

from context import CLIENT_ID, OUT, REF, client, policy_path  # noqa: E402

_c = client()
CLIENT, PROFILE = CLIENT_ID, _c["profile"]
RUN_ID = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")

pack = MetricsPack.model_validate_json((OUT / "metrics_pack.json").read_text(encoding="utf-8"))
instruments = pd.read_csv(REF / "instruments.csv", dtype=str).fillna("").set_index("instrument_id")
policy = pd.read_csv(policy_path(), dtype=str).fillna("").set_index("rule_id")
riskcat = pd.read_csv(REF / "risk_categories.csv", dtype=str).set_index("risk_category")
target = build_target(pack, REF, policy)
(OUT / "allocation_target.json").write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")

pos = {m.instrument_id: m for m in pack.positions}
# End of period, not start: the recommendation is about the portfolio the client
# holds now. Measuring on the opening balance would make these percentages
# disagree with the rebalance plan, which necessarily acts on the closing one.
invested = pack.invested_value_end
recs: list[Recommendation] = []


def rule(rid): return policy.loc[rid]


def add(rid, action, observed, threshold, extra="", iid=None, amount=None, obs_pct=None):
    r = rule(rid)
    recs.append(Recommendation(
        rule_id=rid, action=action, instrument_id=iid, amount_brl=amount,
        observed=observed, observed_pct=obs_pct, threshold=threshold,
        rationale=(r["rationale"] + (" " + extra if extra else "")),
        policy_source=r["policy_source"], severity=r["severity"]))


# --- exposure to equity risk, by vehicle classification (not estimation)
equity_value = sum(
    m.value_end for m in pack.positions
    if riskcat.loc[instruments.loc[m.instrument_id, "risk_category"], "is_equity_exposure"] == "1")
equity_pct = equity_value / invested * 100
direct_pct = sum(m.value_end for m in pack.positions
                 if instruments.loc[m.instrument_id, "risk_category"] == "equity_direct") / invested * 100

# --- 1. idle cash
lim = float(rule("MAX_IDLE_CASH_PCT")["threshold"])
cash_pct_end = pack.cash_value / pack.total_value_end * 100
if cash_pct_end > lim:
    monthly = pack.cash_value * pack.cdi_return_pct / 100
    add("MAX_IDLE_CASH_PCT", "allocate",
        f"R$ {pack.cash_value:,.2f} em caixa ({cash_pct_end:.1f}% do patrimonio final)",
        f"maximo {lim:.1f}%",
        f"No mes analisado, esse saldo remunerado a CDI teria rendido R$ {monthly:,.2f}.",
        "CASH_BRL", round(pack.cash_value, 2), obs_pct=round(cash_pct_end, 2))

# --- 2. matured instruments still held
for iid, ins in instruments.iterrows():
    md = ins["maturity_date"]
    if md and iid in pos and date.fromisoformat(md) <= pack.period_end:
        add("NO_MATURED_HOLDINGS", "redeem",
            f"{ins['statement_name']} venceu em {md} e segue na carteira",
            "nenhum papel vencido em posicao",
            f"Posicao de R$ {pos[iid].value_start:,.2f} sem remuneracao desde o vencimento.",
            iid, round(pos[iid].value_start, 2))

# --- 3. equity look-through vs mandate
lim = float(rule("MAX_EQUITY_LOOKTHROUGH_PCT")["threshold"])
if equity_pct > lim:
    add("MAX_EQUITY_LOOKTHROUGH_PCT", "reduce",
        f"{equity_pct:.1f}% do investido em risco de renda variavel "
        f"({direct_pct:.1f}% em acoes diretas + fundos de acoes e long biased)",
        f"maximo {lim:.1f}%",
        "O extrato reporta apenas a linha de acoes; a exposicao efetiva e maior porque "
        "parte do balde de fundos carrega risco de bolsa.", obs_pct=round(equity_pct, 2))

# --- 4. concentration
lim = float(rule("MAX_SINGLE_POSITION_PCT")["threshold"])
for m in sorted(pack.positions, key=lambda x: -x.value_end):
    if instruments.loc[m.instrument_id, "type"] == "cash":
        continue
    is_equity = riskcat.loc[instruments.loc[m.instrument_id, "risk_category"], "is_equity_exposure"] == "1"
    if is_equity and instruments.loc[m.instrument_id, "type"] != "stock":
        continue  # fund holdings are migrated to the preferred index route
    basket = equity_value if is_equity else invested - equity_value
    p = m.value_end / basket * 100 if basket else 0
    if p > lim:
        add("MAX_SINGLE_POSITION_PCT", "review",
            f"{instruments.loc[m.instrument_id, 'statement_name']} representa {p:.1f}% da cesta {'RV' if is_equity else 'RF'}",
            f"maximo {lim:.1f}% da respectiva cesta", "", m.instrument_id, round(m.value_end, 2),
            obs_pct=round(p, 2))

# --- 5. restricted categories
# The blocked families are derived from the client's classification, not read
# as a list of products out of the profile document - see src/derive_policy.py.
_rid = "RESTRICTED_CATEGORY" if "RESTRICTED_CATEGORY" in policy.index else "RESTRICTED_CATEGORY_FIDC"
blocked = {c for c in str(rule(_rid)["threshold"]).split("|") if c}
for m in pack.positions:
    if instruments.loc[m.instrument_id, "risk_category"] in blocked:
        add(_rid, "review",
            f"{instruments.loc[m.instrument_id, 'statement_name']} e um "
            f"{instruments.loc[m.instrument_id, 'risk_category']} "
            f"({m.value_end / invested * 100:.1f}% do investido)",
            "familia compativel com o mandato", "", m.instrument_id,
            round(m.value_end, 2), obs_pct=round(m.value_end / invested * 100, 2))

current_rv_total = equity_value / pack.total_value_end * 100
recs.append(Recommendation(
    rule_id="MACRO_ALLOCATION", action="review", observed_pct=round(current_rv_total, 2),
    observed=f"RV atual: {current_rv_total:.2f}% do patrimônio final; alvo pelo modelo: {target['target_rv_pct']:.2f}%.",
    threshold=f"RV {target['target_rv_pct']:.2f}%; RF {target['target_rf_pct']:.2f}%; caixa zero",
    rationale="Perfil e horizonte fixam o teto; diferença de retornos reais estimados modula o alvo dentro dele.",
    policy_source=target["source"], severity="info"))
if equity_value > 0:
    recs.append(Recommendation(
        rule_id="INDEX_MIGRATION", action="review", amount_brl=round(equity_value, 2),
        observed="Posições atuais de RV: sugestão de venda e migração para fundo de índice Ibovespa.",
        threshold="Fundo de índice pode ocupar toda a cesta RV; seleção própria limita cada ação a 25% da cesta.",
        rationale="Preferência aprovada para o case, sem escolha de ações e sem execução automática.",
        policy_source="User-approved index preference", severity="info"))

idle = pack.cash_value + sum(
    pos[i].value_end for i, ins in instruments.iterrows()
    if ins["maturity_date"] and i in pos and date.fromisoformat(ins["maturity_date"]) <= pack.period_end)

rs = RecommendationSet(
    run_id=RUN_ID, client_id=CLIENT, profile=PROFILE, recommendations=recs,
    equity_lookthrough_pct=round(equity_pct, 2), equity_reported_pct=round(direct_pct, 2),
    idle_capital_brl=round(idle, 2), idle_capital_pct=round(idle / pack.total_value_end * 100, 2))
(OUT / "recommendations.json").write_text(rs.model_dump_json(indent=2), encoding="utf-8")

print(f"Perfil: {PROFILE}   |   {len(recs)} recomendacoes geradas\n")
print(f"Exposicao a renda variavel (look-through): {rs.equity_lookthrough_pct:.1f}% do investido")
print(f"  dos quais em acoes diretas:             {rs.equity_reported_pct:.1f}%")
print(f"Capital ocioso (caixa + vencido):         R$ {rs.idle_capital_brl:,.2f} "
      f"({rs.idle_capital_pct:.1f}% do patrimonio)\n")
for r in recs:
    print(f"[{r.severity.upper():7}] {r.rule_id}  ->  {r.action.upper()}")
    print(f"    observado : {r.observed}")
    print(f"    limite    : {r.threshold}")
    print(f"    porque    : {r.rationale}")
    print(f"    fonte     : {r.policy_source}\n")
print(f"recommendations.json -> {OUT / 'recommendations.json'}")
