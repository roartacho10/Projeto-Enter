"""
L3b - Sized buy/sell proposal.

Computes the smallest set of trades that clears every suitability breach, then
re-applies the rules to the resulting portfolio and reports what is left. The
plan is only published as resolved if that second pass finds nothing: the
system proves its own recommendation instead of asserting it.

Sells name instruments, because the rules identify exactly which positions
breach. Buys name a category and the criterion it must satisfy - naming a
specific product would require a research source this system does not have,
and inventing one is the failure mode the whole pipeline exists to avoid.

Run: python src/rebalance.py
"""
from __future__ import annotations
from datetime import datetime, timezone, date
from pathlib import Path
import math
import sys
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import MetricsPack, RebalancePlan, Trade  # noqa: E402

from context import OUT, REF, policy_path  # noqa: E402
RUN_ID = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")

pack = MetricsPack.model_validate_json((OUT / "metrics_pack.json").read_text(encoding="utf-8"))
ins = pd.read_csv(REF / "instruments.csv", dtype=str).fillna("").set_index("instrument_id")
cats = pd.read_csv(REF / "risk_categories.csv", dtype=str).fillna("").set_index("risk_category")
pol = pd.read_csv(policy_path(), dtype=str).fillna("").set_index("rule_id")
rpol = pd.read_csv(REF / "rebalance_policy.csv", dtype=str).set_index("param")

MAX_EQUITY = float(pol.loc["MAX_EQUITY_LOOKTHROUGH_PCT", "threshold"])
MAX_SINGLE = float(pol.loc["MAX_SINGLE_POSITION_PCT", "threshold"])
TARGET_CASH = float(rpol.loc["TARGET_CASH_PCT", "value"])
DEST = rpol.loc["DESTINATION_CATEGORY", "value"]
DEST_CRIT = rpol.loc["DESTINATION_CRITERIA", "value"]
MIN_TICKET = float(rpol.loc["MIN_TICKET_BRL", "value"])

total = pack.total_value_end
value = {m.instrument_id: m.value_end for m in pack.positions}
is_equity = {i: cats.loc[ins.loc[i, "risk_category"], "is_equity_exposure"] == "1" for i in value}

# Limits are expressed against the invested base the portfolio will have once
# cash sits at its target - otherwise the ceilings move as we trade.
invested_now = sum(v for k, v in value.items() if ins.loc[k, "type"] != "cash")
cash_target = total * TARGET_CASH / 100
invested_target = total - cash_target
cap_single = invested_target * MAX_SINGLE / 100
cap_equity = invested_target * MAX_EQUITY / 100


def violations(vals: dict[str, float], cash: float) -> list[str]:
    inv = sum(v for k, v in vals.items() if ins.loc[k, "type"] != "cash")
    out = []
    if cash / total * 100 > float(pol.loc["MAX_IDLE_CASH_PCT", "threshold"]):
        out.append(f"caixa em {cash/total*100:.2f}% do patrimônio")
    for k, v in vals.items():
        if ins.loc[k, "type"] == "cash":
            continue
        md = ins.loc[k, "maturity_date"]
        if md and date.fromisoformat(md) < pack.period_end and v > 0:
            out.append(f"{ins.loc[k,'display_name']} vencido e ainda em carteira")
        if v / inv * 100 > MAX_SINGLE + 1e-6:
            out.append(f"{ins.loc[k,'display_name']} em {v/inv*100:.2f}% do investido")
    eq = sum(v for k, v in vals.items() if is_equity[k])
    if eq / inv * 100 > MAX_EQUITY + 1e-6:
        out.append(f"renda variável em {eq/inv*100:.2f}% do investido")
    return out


before = violations(value, value["CASH_BRL"])
after_vals = dict(value)
trades: list[Trade] = []
proceeds = 0.0

# --- 1. matured paper: redeem in full
for i in value:
    md = ins.loc[i, "maturity_date"]
    if md and date.fromisoformat(md) < pack.period_end and after_vals[i] > 0:
        amt = after_vals[i]; after_vals[i] = 0.0; proceeds += amt
        trades.append(Trade(action="redeem", instrument_id=i, amount_brl=round(amt, 2),
                            reason=f"Vencido em {md}; deixou de ser remunerado.",
                            rule_id="NO_MATURED_HOLDINGS"))

# --- 2. single-position ceiling
for i in sorted(after_vals, key=lambda k: -after_vals[k]):
    if ins.loc[i, "type"] == "cash":
        continue
    excess = after_vals[i] - cap_single
    if excess > 1:
        after_vals[i] -= excess; proceeds += excess
        trades.append(Trade(action="sell", instrument_id=i, amount_brl=round(excess, 2),
                            reason=(f"Reduz de {value[i]/invested_now*100:.2f}% do investido para "
                                    f"o teto de {MAX_SINGLE:.2f}% medido sobre a carteira "
                                    f"após o rebalanceamento."),
                            rule_id="MAX_SINGLE_POSITION_PCT"))

# --- 3. equity ceiling, reduced proportionally: choosing which stock to keep is
#        a call the system has no basis to make, so it does not make one.
eq_now = sum(v for k, v in after_vals.items() if is_equity[k])
eq_before_pct = sum(v for k, v in value.items() if is_equity[k]) / invested_now * 100
if eq_now - cap_equity > 1:
    cut = eq_now - cap_equity
    holders = sorted([k for k in after_vals if is_equity[k] and after_vals[k] > 0],
                     key=lambda k: -after_vals[k])
    # Proportional in spirit, but a proportional split of a small cut produces
    # orders of a few tens of reais, where costs swamp the benefit. Anything
    # under the declared minimum ticket is folded into the largest holding.
    planned = {k: cut * after_vals[k] / eq_now for k in holders}
    keep = {k: v for k, v in planned.items() if v >= MIN_TICKET}
    residual = cut - sum(keep.values())
    if not keep:
        keep = {holders[0]: cut}; residual = 0.0
    elif residual > 0:
        big = max(keep, key=lambda k: keep[k]); keep[big] += residual
    for i, amt in keep.items():
        after_vals[i] -= amt; proceeds += amt
        trades.append(Trade(action="sell", instrument_id=i, amount_brl=round(amt, 2),
                            reason=(f"Reduz o balde de renda variável de {eq_before_pct:.2f}% "
                                    f"para o teto de {MAX_EQUITY:.2f}% do investido."),
                            rule_id="MAX_EQUITY_LOOKTHROUGH_PCT"))

# --- 4. deploy: everything above the cash target goes to the one product family
#        the client's own profile document names as compatible
cash_now = after_vals["CASH_BRL"] + proceeds
deployable = cash_now - cash_target
if deployable > 1:
    min_issuers = max(1, math.ceil(deployable / cap_single))
    trades.append(Trade(action="buy", category=DEST, criteria=DEST_CRIT,
                        amount_brl=round(deployable, 2), min_issuers=min_issuers,
                        reason=(f"Com os recursos liberados o caixa chegaria a "
                                f"{cash_now/total*100:.2f}% do patrimônio; a aplicação o leva a "
                                f"{TARGET_CASH:.2f}%. Distribuir em pelo menos "
                                f"{min_issuers} emissores para não violar o teto de "
                                f"{MAX_SINGLE:.2f}% por instrumento."),
                        rule_id="MAX_IDLE_CASH_PCT"))
    after_vals["CASH_BRL"] = cash_target
    # the new holdings, split to respect the ceiling
    for n in range(min_issuers):
        after_vals[f"NOVO_RF_{n+1}"] = deployable / min_issuers
        ins.loc[f"NOVO_RF_{n+1}"] = ins.loc["FUND_TREND_INB"].copy()
        ins.loc[f"NOVO_RF_{n+1}", "display_name"] = f"{DEST} — emissor {n+1}"
        ins.loc[f"NOVO_RF_{n+1}", "maturity_date"] = ""
        is_equity[f"NOVO_RF_{n+1}"] = False
else:
    after_vals["CASH_BRL"] = cash_now

after = violations(after_vals, after_vals["CASH_BRL"])
plan = RebalancePlan(
    run_id=RUN_ID, client_id=pack.client_id, trades=trades,
    proceeds_brl=round(proceeds, 2), deployable_brl=round(max(deployable, 0), 2),
    cash_after_brl=round(after_vals["CASH_BRL"], 2),
    violations_before=before, violations_after=after, resolved=not after)
(OUT / "rebalance_plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")


def br(v):
    return f"{v:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


print(f"Violações antes ({len(before)}):")
for v in before:
    print(f"   • {v}")
print(f"\nPlano — {len(trades)} operações, R$ {br(proceeds)} liberados:")
for t in trades:
    alvo = ins.loc[t.instrument_id, "display_name"] if t.instrument_id else \
        f"{t.category} ({t.criteria})"
    extra = f", em ≥{t.min_issuers} emissores" if t.min_issuers else ""
    print(f"   [{t.action.upper():6}] {alvo:44} R$ {br(t.amount_brl):>14}{extra}")
    print(f"            {t.reason}")
print(f"\nCaixa após: R$ {br(plan.cash_after_brl)} ({plan.cash_after_brl/total*100:.2f}% do patrimônio)")
print(f"\nViolações depois ({len(after)}):")
for v in after:
    print(f"   • {v}")
print("\n" + ("PLANO RESOLVE TODAS AS VIOLAÇÕES" if plan.resolved
              else "ATENÇÃO: o plano não resolve tudo — ver acima"))
