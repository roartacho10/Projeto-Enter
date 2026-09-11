"""
L0 - Read the client's risk-profile document into three parameters.

The document is prose and contains no numbers at all - no percentage, no band,
no allocation target. So this stage does not try to read limits out of it. It
reads exactly three things, each constrained to a closed set:

    risk_class  in  Conservador | Moderado | Arrojado
    objective   in  preservacao | poder_de_compra | crescimento
    horizon     in  Curto | Medio | Longo

Everything else the engine needs - which product families are acceptable, what
rating floor applies, how much equity is tolerable - is DERIVED from those three
by src/derive_policy.py, not read from the text. A document is free to describe
a "moderate" client and then list products a moderate client should not hold;
deriving keeps the mandate internally consistent, and any disagreement between
the document's own prose and the derived rules becomes visible instead of being
silently inherited.

Each parameter is recorded with the verbatim sentence that produced it, so the
policy's provenance is a quotation rather than a paraphrase.

Ambiguity is resolved, not hidden. Albert's document says "horizonte de
investimento de medio a longo prazo", which spans two of the three options. The
shorter one wins - a shorter horizon produces tighter constraints, so erring
short errs safe - and the ambiguity is written into the register.

Run: python src/extract_profile.py
"""
from __future__ import annotations
from pathlib import Path
import re
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "data" / "reference"
INPUTS = ROOT / "data" / "raw" / "inputs"
OUT_CSV = REF / "client_profile.csv"

# The document the case supplies is Albert's, under a generic name.
DEFAULT_DOC_CLIENT = "ALBERT"
DEFAULT_DOC = "risk_profile.txt"

RISK_CLASSES = ["Conservador", "Moderado", "Arrojado"]

# Ordered least to most risk-bearing: when a text spans two, the first wins.
HORIZONS = ["Curto", "Medio", "Longo"]

# Patterns are deliberately narrow. A phrase that merely gestures at an option
# must not match it - the document compares itself to "investimentos totalmente
# conservadores" and seeks "retornos superiores" to them, and neither of those
# is a statement of this client's objective.
OBJECTIVE_PATTERNS = {
    "preservacao": [r"não perder capital", r"mínimo de risco possível",
                    r"preservação (?:do|de) capital"],
    "poder_de_compra": [r"preservar (?:o |seu )?poder de compra e incrementá-lo",
                        r"preservar (?:o |seu )?poder de compra e incrementar"],
    "crescimento": [r"ganhos? acima (?:dos?|de) benchmarks?",
                    r"retornos? acima (?:dos?|de) benchmarks?",
                    r"superar (?:os?|o) benchmarks?"],
}
HORIZON_PATTERNS = {
    "Curto": [r"horizonte (?:de investimento )?(?:de )?curto prazo", r"\bcurto prazo\b(?= como)"],
    "Medio": [r"médio (?:a|e) longo prazo", r"horizonte (?:de investimento )?(?:de )?médio prazo",
              r"\bmédio prazo\b"],
    "Longo": [r"médio (?:a|e) longo prazo", r"horizonte (?:de investimento )?(?:de )?longo prazo",
              r"\blongo prazo\b"],
}

issues: list[str] = []


def log(msg: str) -> None:
    issues.append(msg)
    print(f"  [warning] {msg}")


def fail(msg: str) -> None:
    print(f"\nFALHOU: {msg}")
    sys.exit(1)


def quote(text: str, m: re.Match) -> str:
    """The sentence the match sits in, trimmed - evidence, not a paraphrase."""
    a = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
    b = text.find(".", m.end())
    b = len(text) if b == -1 else b
    return re.sub(r"\s+", " ", text[a:b]).strip()[:260]


def find_all(text: str, options: dict[str, list[str]]) -> list[tuple[str, re.Match]]:
    hits = []
    for name, pats in options.items():
        for p in pats:
            m = re.search(p, text, re.I)
            if m:
                hits.append((name, m))
                break
    return hits


def classify(cid: str, text: str) -> dict:
    row = {"client_id": cid, "document": "", "risk_class": "", "risk_class_quote": "",
           "objective": "", "objective_quote": "", "horizon": "", "horizon_quote": "",
           "horizon_ambiguous": False, "note": ""}

    # --- risk class: the document states it outright
    found = [c for c in RISK_CLASSES
             if re.search(rf"Classificação do Perfil de Investimento:\s*{c}", text, re.I)]
    if len(found) != 1:
        found = [c for c in RISK_CLASSES if re.search(rf"perfil\s+{c.lower()}", text, re.I)]
    if len(found) != 1:
        fail(f"{cid}: classificacao de risco indeterminada (encontradas: {found or 'nenhuma'})")
    row["risk_class"] = found[0]
    m = re.search(rf"{found[0]}", text, re.I)
    row["risk_class_quote"] = quote(text, m)

    # --- objective: exactly one must match
    hits = find_all(text, OBJECTIVE_PATTERNS)
    if len(hits) == 0:
        fail(f"{cid}: nenhum objetivo reconhecido no documento")
    if len(hits) > 1:
        fail(f"{cid}: objetivo ambiguo, {len(hits)} opcoes casaram "
             f"({', '.join(n for n, _ in hits)}) - padroes precisam ser mais estreitos")
    row["objective"], m = hits[0]
    row["objective_quote"] = quote(text, m)

    # --- horizon: more than one may match, and that is expected
    hits = find_all(text, HORIZON_PATTERNS)
    if not hits:
        fail(f"{cid}: horizonte nao reconhecido no documento")
    names = [n for n, _ in hits]
    chosen = min(names, key=HORIZONS.index)
    row["horizon"] = chosen
    row["horizon_quote"] = quote(text, dict(hits)[chosen])
    if len(hits) > 1:
        row["horizon_ambiguous"] = True
        row["note"] = (f"O documento abrange {' e '.join(sorted(names, key=HORIZONS.index))}. "
                       f"Adotado o mais curto ({chosen}): horizonte menor gera restricao mais "
                       f"apertada, entao o erro fica do lado seguro.")
        log(f"{cid}: horizonte ambiguo ({'/'.join(names)}) - adotado {chosen}")
    return row


clients = pd.read_csv(REF / "clients.csv", dtype=str).fillna("")
rows = []
for _, c in clients.iterrows():
    cid = c["client_id"]
    doc = INPUTS / f"risk_profile_{cid.lower()}.txt"
    if not doc.exists() and cid == DEFAULT_DOC_CLIENT:
        doc = INPUTS / DEFAULT_DOC
    if not doc.exists():
        # No document is a fact about the client, not a reason to invent one.
        rows.append({"client_id": cid, "document": "", "risk_class": c["profile"],
                     "risk_class_quote": "", "objective": "", "objective_quote": "",
                     "horizon": "", "horizon_quote": "", "horizon_ambiguous": False,
                     "note": "Sem documento de perfil. Classificacao vinda do cadastro; "
                             "objetivo e horizonte nao definidos."})
        log(f"{cid}: sem documento de perfil em data/raw/inputs/")
        continue
    r = classify(cid, doc.read_text(encoding="utf-8"))
    r["document"] = doc.name
    rows.append(r)

df = pd.DataFrame(rows)
df.to_csv(OUT_CSV, index=False, encoding="utf-8")

print("\n" + "=" * 74)
for _, r in df.iterrows():
    if not r["document"]:
        print(f"  {r['client_id']:9} {r['risk_class']:12} (cadastro, sem documento)")
        continue
    amb = "  <- ambiguo" if r["horizon_ambiguous"] else ""
    print(f"  {r['client_id']:9} {r['risk_class']:12} {r['objective']:16} "
          f"horizonte {r['horizon']}{amb}")
    print(f"            \"{r['objective_quote'][:88]}\"")
print("=" * 74)
print(f"{OUT_CSV.relative_to(ROOT)}   ·   {len(issues)} ressalva(s)")
