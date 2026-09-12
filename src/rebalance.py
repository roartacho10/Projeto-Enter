"""Suggested transition to the macro class target; no orders are executed.

Sell existing RV into an unspecified Ibovespa index fund. Keep eligible RF
products up to the within-RF cap, trim largest holdings if RF exceeds its
target, and split new RF money across unspecified products. Integer cents
preserve wealth and ensure that published amounts reproduce the simulation.
This is a deterministic transition, not a cost or return optimisation.
"""
from __future__ import annotations
from datetime import date, datetime, timezone
import json
import math

from allocation import MODEL_VERSION
from contracts import RebalancePlan, Trade


def build_plan(pack, instruments, categories, target, params, recommendations) -> RebalancePlan:
    if target.get("metrics_run_id") != pack.run_id or target.get("model_version") != MODEL_VERSION:
        raise ValueError("Stale allocation target: run recommendations again")
    if not 0 <= float(target["target_rv_pct"]) <= 100:
        raise ValueError("Invalid RV allocation target")
    if float(params["TARGET_CASH_PCT"]) != 0:
        raise ValueError("This model requires zero target cash")
    product_pct = float(params["MAX_PRODUCT_PCT"])
    if not 0 < product_pct <= 100:
        raise ValueError("Invalid within-basket product limit")
    cents = lambda v: int(round(v * 100))
    positions = {}
    for m in pack.positions:
        ins = instruments[m.instrument_id]
        kind = "CASH" if ins["type"] == "cash" else (
            "RV" if str(categories[ins["risk_category"]]["is_equity_exposure"]) == "1" else "RF")
        positions[m.instrument_id] = {
            "instrument_id": m.instrument_id, "asset_class": kind, "cents": cents(m.value_end),
            "matured": bool(ins["maturity_date"] and date.fromisoformat(ins["maturity_date"]) <= pack.period_end),
            "index_fund": False,
        }
    total = sum(p["cents"] for p in positions.values())
    if any(p["cents"] < 0 for p in positions.values()):
        raise ValueError("This model requires nonnegative positions")
    if total <= 0 or abs(total - cents(pack.total_value_end)) > 1:
        raise ValueError("Position values do not reconcile with total wealth")
    rv_target = round(total * target["target_rv_pct"] / 100)
    rf_target = total - rv_target
    rf_cap = math.floor(rf_target * product_pct / 100)
    if rf_target and rf_cap == 0:
        raise ValueError("RF target too small to split into valid cent-denominated products")
    current_rv = sum(p["cents"] for p in positions.values() if p["asset_class"] == "RV")
    initial_cash = sum(p["cents"] for p in positions.values() if p["asset_class"] == "CASH")

    # Snapshot before anything is mutated: `positions` becomes the simulated
    # book below, so the current mix has to be read here or not at all.
    def _by_class(pos):
        return {k: sum(p["cents"] for p in pos.values() if p["asset_class"] == k)
                for k in ("RV", "RF", "CASH")}

    def _largest(pos, kind, basket_cents):
        """
        Largest single product as a share of its own basket. Index funds are
        skipped because the per-product cap does not apply to them: after the
        migration the RV basket is one index fund, and reporting it as 100% of
        the basket against a 25% limit would show a breach that the policy
        explicitly exempts. None means the basket holds nothing the cap binds.
        """
        vals = [p["cents"] for p in pos.values()
                if p["asset_class"] == kind and p["cents"] > 0 and not p["index_fund"]]
        if not vals or basket_cents <= 0:
            return None
        return round(max(vals) / basket_cents * 100, 2)

    before_cents = _by_class(positions)
    largest_before = {k: _largest(positions, k, before_cents[k]) for k in ("RV", "RF")}
    trades, proceeds = [], 0
    before = []
    if initial_cash:
        before.append("Caixa acima do alvo zero")
    if abs(current_rv - rv_target) > 1:
        before.append("Distribuição entre classes diferente do alvo macro")
    if current_rv:
        before.append("Migração sugerida das posições atuais de RV para índice")

    # Each original product receives at most one consolidated sell/redemption.
    original = {k: p["cents"] for k, p in positions.items()}
    for p in positions.values():
        if p["asset_class"] == "CASH":
            p["cents"] = 0
        elif p["matured"] or p["asset_class"] == "RV":
            p["cents"] = 0
        else:
            p["cents"] = min(p["cents"], rf_cap)
    excess_rf = max(0, sum(p["cents"] for p in positions.values()
                          if p["asset_class"] == "RF") - rf_target)
    for k in sorted(positions, key=lambda k: (-positions[k]["cents"], k)):
        p = positions[k]
        if p["asset_class"] == "RF" and excess_rf:
            cut = min(p["cents"], excess_rf)
            p["cents"] -= cut
            excess_rf -= cut
    for k, p in positions.items():
        amount = original[k] - p["cents"]
        if p["asset_class"] == "CASH" or amount <= 0:
            continue
        if p["matured"]:
            reason, rule, action = "Sugestão de resgate de produto vencido.", "NO_MATURED_HOLDINGS", "redeem"
            before.append(f"{k}: produto vencido")
        elif p["asset_class"] == "RV":
            reason, rule, action = "Sugestão de migrar a posição para exposição ao Ibovespa via fundo de índice.", "INDEX_MIGRATION", "sell"
        else:
            reason, rule, action = "Ajuste ao alvo RF e ao limite por produto dentro da cesta RF.", "RF_TARGET_AND_PRODUCT_CAP", "sell"
            before.append(f"{k}: ajuste à cesta RF")
        trades.append(Trade(action=action, instrument_id=k, amount_brl=amount / 100,
                            asset_class=p["asset_class"], reason=reason, rule_id=rule))
        proceeds += amount

    def buy(kind, amount, category, criteria, count):
        if not amount:
            return
        trades.append(Trade(action="buy", category=category, criteria=criteria,
                            amount_brl=amount / 100, min_products=count, asset_class=kind,
                            reason="Sugestão para atingir a alocação-alvo pelo modelo; depende de aprovação do cliente.",
                            rule_id="MACRO_ALLOCATION"))
        unit, remainder = divmod(amount, count)
        for n in range(count):
            key = f"PROPOSED_{kind}_{n + 1}"
            positions[key] = {"instrument_id": key, "asset_class": kind,
                              "cents": unit + (n < remainder), "matured": False,
                              "index_fund": kind == "RV"}

    buy("RV", rv_target, "Fundo de índice Ibovespa",
        "exposição ao Ibovespa, sem selecionar fundo ou ticker específico", 1)
    retained_rf = sum(p["cents"] for p in positions.values() if p["asset_class"] == "RF")
    rf_buy = rf_target - retained_rf
    buy("RF", rf_buy, params["DESTINATION_CATEGORY"], params["DESTINATION_CRITERIA"],
        max(1, math.ceil(rf_buy / rf_cap)) if rf_cap else 1)
    invested_buy = rv_target + rf_buy
    if invested_buy != initial_cash + proceeds:
        raise ValueError("Trade cash flows do not reconcile")
    after = []
    if sum(p["cents"] for p in positions.values()) != total:
        after.append("Patrimônio não preservado")
    if any(p["cents"] < 0 or (p["matured"] and p["cents"]) for p in positions.values()):
        after.append("Posição inválida ou vencida na simulação")
    for kind, goal in (("RV", rv_target), ("RF", rf_target), ("CASH", 0)):
        if sum(p["cents"] for p in positions.values() if p["asset_class"] == kind) != goal:
            after.append(f"Alvo {kind} não atingido")
    for p in positions.values():
        if p["asset_class"] == "RF" and p["cents"] > rf_cap:
            after.append(f"{p['instrument_id']}: concentração RF")
        if p["asset_class"] == "RV" and p["cents"] and not p["index_fund"]:
            after.append("Migração para índice incompleta")
    pending = [r.observed for r in recommendations.recommendations
               if r.rule_id.startswith("RESTRICTED_CATEGORY") and r.instrument_id
               and positions[r.instrument_id]["cents"] > 0]
    post = [{**{k: v for k, v in p.items() if k != "cents"}, "value_brl": p["cents"] / 100}
            for p in positions.values() if p["cents"]]

    after_cents = _by_class(positions)
    largest_after = {k: _largest(positions, k, after_cents[k]) for k in ("RV", "RF")}
    alvo_cents = {"RV": rv_target, "RF": rf_target, "CASH": 0}
    rotulos = {"RV": "Renda variável", "RF": "Renda fixa", "CASH": "Caixa"}
    # One base for all three columns: the closing patrimony, which the
    # simulation preserves, so the shares are comparable line by line.
    mix = [{"asset_class": k, "label": rotulos[k],
            "before_brl": before_cents[k] / 100, "before_pct": round(before_cents[k] / total * 100, 2),
            "target_brl": alvo_cents[k] / 100, "target_pct": round(alvo_cents[k] / total * 100, 2),
            "after_brl": after_cents[k] / 100, "after_pct": round(after_cents[k] / total * 100, 2)}
           for k in ("RV", "RF", "CASH")]
    conc = []
    for k in ("RV", "RF"):
        if largest_before[k] is None and largest_after[k] is None:
            continue
        nota = ""
        if largest_after[k] is None and after_cents[k]:
            nota = "cesta migrada para fundo de índice, isento do limite por produto"
        conc.append({"asset_class": k, "label": f"Maior produto da cesta {rotulos[k].lower()}",
                     "before_pct": largest_before[k], "after_pct": largest_after[k],
                     "limit_pct": product_pct, "note": nota})
    return RebalancePlan(
        run_id=datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ"), client_id=pack.client_id,
        trades=trades, proceeds_brl=proceeds / 100, deployable_brl=invested_buy / 100,
        cash_after_brl=0, violations_before=before, violations_after=after, resolved=not after,
        model_version=MODEL_VERSION, allocation_fingerprint=target["fingerprint"],
        post_positions=post, class_mix=mix, concentration=conc, pending_reviews=pending,
        assumptions=["Simulação em valores monetários, sem impostos, custos, liquidez ou lotes negociáveis.",
                     "Sem ticket mínimo: atingir o alvo e caixa zero pode exigir ajustes pequenos.",
                     "Limite por produto, não por emissor; compras RF são produtos distintos hipotéticos.",
                     "Aprovação dos alvos numéricos não elimina revisões de categoria, produto e rating.",
                     "Alternativa de seleção própria: cada ação limitada a 25% da cesta RV; nenhuma ação recomendada."])


def main():
    import pandas as pd
    from context import OUT, REF
    from contracts import MetricsPack, RecommendationSet
    pack = MetricsPack.model_validate_json((OUT / "metrics_pack.json").read_text(encoding="utf-8"))
    target = json.loads((OUT / "allocation_target.json").read_text(encoding="utf-8"))
    recs = RecommendationSet.model_validate_json((OUT / "recommendations.json").read_text(encoding="utf-8"))
    ins = pd.read_csv(REF / "instruments.csv", dtype=str).fillna("").set_index("instrument_id").to_dict("index")
    cats = pd.read_csv(REF / "risk_categories.csv", dtype=str).fillna("").set_index("risk_category").to_dict("index")
    params = pd.read_csv(REF / "rebalance_policy.csv", dtype=str).set_index("param")["value"].to_dict()
    plan = build_plan(pack, ins, cats, target, params, recs)
    (OUT / "rebalance_plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    print(f"RV alvo: {target['target_rv_pct']:.2f}%; RF: {target['target_rf_pct']:.2f}%; caixa zero")
    print(f"{len(plan.trades)} sugestões; alvos numéricos conferidos: {plan.resolved}; revisões pendentes: {len(plan.pending_reviews)}")
    if not plan.resolved:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
