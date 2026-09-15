"""
L1 - Turn the client's three profile parameters into the rules the engine reads.

This is the equivalence layer, and it exists to keep two different kinds of
statement apart.

The risk-profile document says what the client is: moderate, seeking to protect
purchasing power, over a medium horizon. It contains no numbers at all. Every
limit in this system - equity ceilings, per-basket product caps, zero cash - is a
CALIBRATION, and pretending otherwise by citing a document section next to it
would claim a precision the source does not have.

So the two are separated. `client_profile.csv` holds what the document says,
quoted verbatim. The `profile_*.csv` tables hold what the house decides those
parameters mean. This stage joins them and writes the result per client, which
is also how Beatriz, Carlos and Daniela stop inheriting Albert's limits.

Risk class sets the ceiling; horizon modulates within it. The horizon is a
capacity axis, not a tolerance axis: a longer one does not make the client
braver, it makes them able to avoid selling into a drawdown. The two are
treated as independent because the classification measures willingness and the
horizon measures ability - stated here because the alternative, applying the
same fact twice, is a real and reasonable objection.

Output: output/<client>/suitability_policy.csv, same shape the engine already
reads, plus a source_type column saying where each number actually came from.

Run: python src/derive_policy.py            (CLIENT_ID picks the client)
"""
from __future__ import annotations
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context import CLIENT_ID, OUT, REF  # noqa: E402

FALLBACK = REF / "suitability_policy.csv"


def fail(msg: str) -> None:
    print(f"\nFALHOU: {msg}")
    sys.exit(1)


prof = pd.read_csv(REF / "client_profile.csv", dtype=str).fillna("")
prof = prof[prof["client_id"] == CLIENT_ID]
if prof.empty:
    fail(f"{CLIENT_ID} nao esta em client_profile.csv - rode src/extract_profile.py")
p = prof.iloc[0]

if not p["objective"] or not p["horizon"]:
    # Without a document there are no parameters to derive from. The engine
    # falls back to the shared file, and the letter says the limits are generic.
    print(f"[pula] {CLIENT_ID}: {p['note']}")
    print(f"       O motor usara {FALLBACK.relative_to(REF.parent.parent)} (limites genericos).")
    sys.exit(0)

RISK, HORIZON = p["risk_class"], p["horizon"]

horizons = pd.read_csv(REF / "profile_horizon.csv", dtype=str).set_index("horizon")
limits = pd.read_csv(REF / "profile_limits.csv", dtype=str)
families = pd.read_csv(REF / "profile_families.csv", dtype=str).fillna("")
floors = pd.read_csv(REF / "profile_rating_floor.csv", dtype=str).set_index("risk_class")
rpol = pd.read_csv(REF / "rebalance_policy.csv", dtype=str).set_index("param")

if HORIZON not in horizons.index:
    fail(f"horizonte '{HORIZON}' nao esta em profile_horizon.csv")
if RISK not in floors.index:
    fail(f"classificacao '{RISK}' nao esta em profile_rating_floor.csv")
cell = limits[(limits["risk_class"] == RISK) & (limits["horizon"] == HORIZON)]
if len(cell) != 1:
    fail(f"profile_limits.csv: {len(cell)} celulas para {RISK} x {HORIZON}, esperada 1")
cell = cell.iloc[0]

years = horizons.loc[HORIZON, "years"]
floor = floors.loc[RISK, "rating_floor"]
fam = families[families["risk_class"] == RISK]
blocked = fam[fam["allowed"] == "0"]["risk_category"].tolist()
if fam.empty:
    fail(f"profile_families.csv nao cobre a classificacao '{RISK}'")

MATRIX = f"profile_limits.csv [{RISK} x {HORIZON}]"
DOC = (f"Perfil de Risco do cliente: \"{p['risk_class_quote'][:110]}\""
       if p["risk_class_quote"] else "Perfil de Risco do cliente")

rules = [
    {"rule_id": "MAX_IDLE_CASH_PCT", "rule_type": "idle_capital",
     "threshold": rpol.loc["TARGET_CASH_PCT", "value"], "severity": "warning",
     "rationale": "Caixa-alvo zero, conforme premissa aprovada para este case.",
     "policy_source": "rebalance_policy.csv", "source_type": "equivalencia"},

    {"rule_id": "MAX_EQUITY_LOOKTHROUGH_PCT", "rule_type": "equity_lookthrough",
     "threshold": cell["max_equity_pct"], "severity": "warning",
     "rationale": (f"Teto de renda variavel sobre o patrimonio total, incluindo caixa, para o par {RISK} x horizonte {HORIZON.lower()} "
                   f"({years} anos). A classificacao fixa o teto; o horizonte modula dentro "
                   f"dele, porque prazo maior significa nao ser forcado a vender numa queda."),
     "policy_source": MATRIX, "source_type": "equivalencia"},

    {"rule_id": "MAX_SINGLE_POSITION_PCT", "rule_type": "concentration",
     "threshold": rpol.loc["MAX_PRODUCT_PCT", "value"], "severity": "warning",
     "rationale": ("Limite por produto dentro da cesta RF; por ação dentro da cesta RV "
                   "na alternativa de seleção própria. Fundo de índice Ibovespa isento."),
     "policy_source": "rebalance_policy.csv", "source_type": "equivalencia"},

    {"rule_id": "NO_MATURED_HOLDINGS", "rule_type": "matured_instrument",
     "threshold": "0", "severity": "warning",
     "rationale": "Papel vencido nao remunera e deveria ter sido reinvestido no vencimento.",
     "policy_source": "Boa pratica operacional", "source_type": "operacional"},

    {"rule_id": "RESTRICTED_CATEGORY", "rule_type": "restricted_category",
     "threshold": "|".join(blocked), "severity": "warning",
     "rationale": (f"Familias fora do mandato {RISK}: "
                   f"{', '.join(blocked) if blocked else 'nenhuma'}. "
                   f"Renda fixa admitida a partir de rating {floor}. As familias derivam da "
                   f"classificacao, nao da lista de produtos citada no documento, para que "
                   f"um descasamento entre o que o texto classifica e o que ele exemplifica "
                   f"nao entre no motor sem ser visto."),
     "policy_source": "profile_families.csv + profile_rating_floor.csv",
     "source_type": "equivalencia"},
]

df = pd.DataFrame(rules)
OUT.mkdir(parents=True, exist_ok=True)
dest = OUT / "suitability_policy.csv"
df.to_csv(dest, index=False, encoding="utf-8")

print("=" * 74)
print(f"{CLIENT_ID}   {RISK}  ·  {p['objective']}  ·  horizonte {HORIZON} ({years} anos)"
      + ("   [horizonte ambiguo, adotada a ponta curta]" if p["horizon_ambiguous"] == "True" else ""))
print("=" * 74)
print(f"  renda variavel     max {cell['max_equity_pct']}%")
print(f"  por produto/cesta  max {rpol.loc['MAX_PRODUCT_PCT', 'value']}% (indice isento)")
print(f"  caixa alvo         {rpol.loc['TARGET_CASH_PCT', 'value']}%")
print(f"  rating minimo      {floor}")
print(f"  familias vetadas   {', '.join(blocked) if blocked else '(nenhuma)'}")
print(f"\n  origem: 1 regra operacional, {sum(r['source_type'] == 'equivalencia' for r in rules)} "
      f"de equivalencia declarada, 0 lidas como numero do documento")
print(f"          (o documento de perfil nao contem nenhum numero)")
print(f"\n{dest}")
