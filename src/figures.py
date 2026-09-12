"""
The figure sheet: every number the letter is allowed to contain, already
formatted as the exact string it must appear as.

This is the mechanism behind "the model never produces a number". The prompt
receives these strings and is told to quote them verbatim; verification is
then an exact string membership test rather than a fuzzy numeric comparison,
which is what makes the check hard to fool.

Run: python src/figures.py
"""
from __future__ import annotations
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import MetricsPack, RecommendationSet  # noqa: E402

from context import OUT, REF, policy_path  # noqa: E402


FIRST_MACRO_YEAR = 2025   # 2024 in the report is a closed year, not a forecast


def money(v: float) -> str:
    s = f"{v:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"R$ {s}"


def pct(v: float) -> str:
    return f"{v:.2f}".replace(".", ",") + "%"


def pp(v: float) -> str:
    return f"{v:+.2f}".replace(".", ",") + " p.p."


def build() -> dict[str, str]:
    import csv as _csv
    pack = MetricsPack.model_validate_json((OUT / "metrics_pack.json").read_text(encoding="utf-8"))
    f: dict[str, str] = {
        "patrimonio_inicio": money(pack.total_value_start),
        "patrimonio_fim": money(pack.total_value_end),
        "retorno_patrimonio": pct(pack.total_return_pct),
        "investido_inicio": money(pack.invested_value_start),
        "investido_fim": money(pack.invested_value_end),
        "retorno_investido": pct(pack.invested_return_pct),
        "caixa_valor": money(pack.cash_value),
        "caixa_pct": pct(pack.cash_pct_of_total_end),
        "cdi_periodo": pct(pack.cdi_return_pct),
        "ipca_periodo": pct(pack.ipca_return_pct),
        "excesso_sobre_cdi": pp(pack.excess_over_cdi_pp),
        "retorno_real_patrimonio": pct(pack.real_return_total_pct),
        "ibovespa_periodo": pct(pack.ibov_return_pct),
        "cobertura_marcacao": pct(pack.coverage_pct),
    }
    for m in pack.positions:
        f[f"retorno_{m.instrument_id}"] = pct(m.return_pct)
        f[f"valor_{m.instrument_id}"] = money(m.value_end)
        # The closing weight, so that valor_X and peso_X describe the same
        # instant. The opening weight is not published: it exists for the
        # attribution arithmetic, and a letter quoting it beside a closing
        # value states two dates as if they were one.
        f[f"peso_{m.instrument_id}"] = pct(m.weight_end_pct)
        f[f"contrib_{m.instrument_id}"] = pp(m.contribution_pp)
    rp = OUT / "recommendations.json"
    if rp.exists():
        rs = RecommendationSet.model_validate_json(rp.read_text(encoding="utf-8"))
        f["exposicao_rv_lookthrough"] = pct(rs.equity_lookthrough_pct)
        f["exposicao_rv_reportada"] = pct(rs.equity_reported_pct)
        f["capital_ocioso_valor"] = money(rs.idle_capital_brl)
        f["capital_ocioso_pct"] = pct(rs.idle_capital_pct)
        # Figures measured by the rule engine. Without these the model,
        # blocked from quoting them, reaches for the nearest authorised number
        # and silently swaps the denominator.
        for r in rs.recommendations:
            tag = f"{r.rule_id}_{r.instrument_id or 'geral'}"
            if r.amount_brl is not None:
                f[f"valor_regra_{tag}"] = money(r.amount_brl)
            if r.observed_pct is not None:
                f[f"pct_medido_{tag}"] = pct(r.observed_pct)   # base varies per rule; the rule text states it
    # Trades carry amounts the letter must be able to quote verbatim.
    rb = OUT / "rebalance_plan.json"
    if rb.exists():
        from contracts import RebalancePlan
        plan = RebalancePlan.model_validate_json(rb.read_text(encoding="utf-8"))
        f["rebalance_recursos_liberados"] = money(plan.proceeds_brl)
        f["rebalance_valor_a_aplicar"] = money(plan.deployable_brl)
        f["rebalance_caixa_final"] = money(plan.cash_after_brl)
        for i, t in enumerate(plan.trades, 1):
            tag = t.instrument_id or "destino"
            f[f"trade_{i}_{t.action}_{tag}"] = money(t.amount_brl)
            if t.min_products:
                f[f"trade_{i}_min_produtos"] = str(t.min_products)

    ap = OUT / "allocation_target.json"
    if ap.exists():
        allocation = json.loads(ap.read_text(encoding="utf-8"))
        for key in ("target_rv_pct", "target_rf_pct", "target_cash_pct", "rv_ceiling_pct",
                    "rv_real_cumulative_pct", "rf_real_cumulative_pct", "dividend_yield_pct"):
            f[f"alocacao_{key}"] = pct(allocation[key])
        f["alocacao_spread_pp"] = pp(allocation["spread_pp"])
        f["alocacao_sensibilidade_pp"] = pp(allocation["sensitivity_pp"])
        for row in allocation["annual"]:
            for key in ("rv_real_pct", "rf_real_pct"):
                f[f"alocacao_{key}_{row['year']}"] = pct(row[key])

    # Macro projections, extracted from the report by src/extract_macro.py.
    # The projections TABLE is the authority. Where the report's own narrative
    # states a different number - it does, twice, for gross debt - the extractor
    # records the divergence and keeps the table's value, so the only macro
    # figure the letter can quote is the published one.
    mp = REF / "macro_projections.csv"
    if mp.exists():
        for row in _csv.DictReader(mp.open(encoding="utf-8")):
            if row["is_projection"] != "True" or int(row["year"]) < FIRST_MACRO_YEAR:
                continue
            if not row["unit"]:          # rows without a stable id stay unpublished
                continue
            suffix = "" if row["unit"] == "R$/US$" else "%"
            f[f"macro_{row['variable_id']}_{row['year']}"] = row["value_raw"] + suffix

    # Policy limits are declared numbers too: without them the letter can only
    # say "above the recommended limit", which tells the client less.
    pp_ = policy_path()
    if pp_.exists():
        for row in _csv.DictReader(pp_.open(encoding="utf-8")):
            t = row["threshold"]
            try:
                f[f"limite_{row['rule_id']}"] = pct(float(t))
            except ValueError:
                pass
    return f


if __name__ == "__main__":
    fig = build()
    (OUT / "figures.json").write_text(json.dumps(fig, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(fig)} cifras autorizadas -> {OUT / 'figures.json'}\n")
    for k, v in list(fig.items())[:16]:
        print(f"  {k:34} {v}")
    print("  ...")
