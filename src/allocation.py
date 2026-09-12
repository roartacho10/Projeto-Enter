"""User-calibrated class allocation from the supplied macro projection table.

Pure calculations never modify the immutable positions or source TXT files.
Rates use percentage units (7 means 7%), not fractional rates.
"""
from __future__ import annotations
from datetime import date
import hashlib
import json
import math

MODEL_VERSION = "macro-allocation-v1"


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str).encode("utf-8")).hexdigest()


def macro_factor(spread_pp: float, sensitivity_pp: float) -> float:
    if not math.isfinite(spread_pp) or not math.isfinite(sensitivity_pp) or sensitivity_pp <= 0:
        raise ValueError("The spread must be finite and sensitivity strictly positive")
    return max(0.0, min(1.0, 0.5 + spread_pp / (2 * sensitivity_pp)))


def estimate(projections, period_end: date, ceiling_pct: float, dividend_yield_pct: float,
             sensitivity_pp: float, forecast_end: date) -> dict:
    """Compound months AFTER the measured month through the forecast end.

    GDP+DY estimates real RV; year-end Selic approximates annual nominal RF,
    deflated by annual IPCA. No individual-asset forecasts, taxes or fees.
    """
    if forecast_end.month != 12 or forecast_end.day != 31 or period_end >= forecast_end:
        raise ValueError("Forecast must end on December 31 after the measured period")
    if not 0 <= ceiling_pct <= 100 or not math.isfinite(dividend_yield_pct):
        raise ValueError("Invalid equity ceiling or dividend yield")
    rows, rv_factor, rf_factor = [], 1.0, 1.0
    for year in range(period_end.year, forecast_end.year + 1):
        months = 12 - period_end.month if year == period_end.year else 12
        if not months:
            continue
        values = {}
        for variable in ("pib", "ipca", "selic"):
            matches = [r for r in projections if r["variable_id"] == variable
                       and int(r["year"]) == year and str(r["is_projection"]).lower() == "true"]
            if len(matches) != 1:
                raise ValueError(f"Expected one projection for {variable}/{year}, got {len(matches)}")
            values[variable] = float(matches[0]["value"])
            if not math.isfinite(values[variable]):
                raise ValueError(f"Non-finite projection: {variable}/{year}")
        rv = (values["pib"] + dividend_yield_pct) / 100
        if values["ipca"] <= -100 or values["selic"] <= -100 or rv <= -1:
            raise ValueError("Rates must have positive gross growth factors")
        rf = (1 + values["selic"] / 100) / (1 + values["ipca"] / 100) - 1
        rv_factor *= (1 + rv) ** (months / 12)
        rf_factor *= (1 + rf) ** (months / 12)
        rows.append({"year": year, "months": months, **values,
                     "rv_real_pct": rv * 100, "rf_real_pct": rf * 100})
    spread = (rv_factor - rf_factor) * 100
    factor = macro_factor(spread, sensitivity_pp)
    start = date(period_end.year + (period_end.month == 12), period_end.month % 12 + 1, 1)
    return {
        "model_version": MODEL_VERSION, "start": start.isoformat(),
        "end": forecast_end.isoformat(), "annual": rows,
        "dividend_yield_pct": dividend_yield_pct, "sensitivity_pp": sensitivity_pp,
        "rv_real_cumulative_pct": (rv_factor - 1) * 100,
        "rf_real_cumulative_pct": (rf_factor - 1) * 100,
        "spread_pp": spread, "macro_factor": factor, "rv_ceiling_pct": ceiling_pct,
        "target_rv_pct": ceiling_pct * factor, "target_rf_pct": 100 - ceiling_pct * factor,
        "target_cash_pct": 0.0,
        "assumptions": [
            "Estimativas por classe, não por ativo; não são promessa de retorno.",
            "RV real = PIB real + dividend yield anual constante, premissa do case.",
            "RF real = (1 + Selic) / (1 + IPCA) - 1; Selic de fim de ano usada como aproximação anual.",
            "Capitalização proporcional aos meses restantes; sem impostos, custos ou trajetória intranual.",
            "Fator macro limitado entre zero e um; empate utiliza metade do teto de RV.",
            "RF agrupa investimentos não classificados como RV, inclusive crédito e multimercados; os riscos diferem.",
            "Índice reduz concentração por empresa, mas mantém o risco de mercado.",
        ],
    }


def build_target(pack, ref, policy) -> dict:
    import csv
    params = {r["param"]: r["value"] for r in csv.DictReader(
        (ref / "allocation_policy.csv").read_text(encoding="utf-8").splitlines())}
    projections = list(csv.DictReader((ref / "macro_projections.csv").read_text(
        encoding="utf-8").splitlines()))
    target = estimate(projections, pack.period_end,
                      float(policy.loc["MAX_EQUITY_LOOKTHROUGH_PCT", "threshold"]),
                      float(params["DIVIDEND_YIELD_PCT"]), float(params["SENSITIVITY_PP"]),
                      date.fromisoformat(params["FORECAST_END"]))
    target["client_id"] = pack.client_id
    target["metrics_run_id"] = pack.run_id
    target["source"] = "macro_projections.csv; profile_limits.csv; allocation_policy.csv"
    target["fingerprint"] = fingerprint(target)
    return target
