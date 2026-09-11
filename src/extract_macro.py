"""
L0 - Read the house macro report and register its projections.

The research note is mostly prose, and the prose is not an input to anything.
What the engine needs is the "Projeções XP" table: one number per variable per
year. This stage extracts that table and nothing else.

No language model touches this. The table survives PDF-to-text as a clean
sequence - a label followed by exactly nine values - so it is parsed
deterministically and the same file always yields the same numbers. That keeps
the project's rule intact at the source: a model may later narrate these
figures, but it never produced one.

Three checks run on every extraction, because a number nobody verified is worth
no more than a number nobody computed:

  1. The table is printed twice in the document. Both copies are parsed and
     must agree, value by value.
  2. Every row must carry exactly nine values, one per year in the header.
  3. Figures the narrative also states are compared against the table, and a
     disagreement is recorded rather than resolved silently.

Run: python src/extract_macro.py
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC_TXT = ROOT / "data" / "raw" / "inputs" / "macro_analysis.txt"
OUT_CSV = ROOT / "data" / "reference" / "macro_projections.csv"

TABLE_START = "Projeções XP"
TABLE_END = "* Exclui precatórios"
YEARS = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
PROJECTED_FROM = 2024          # the header marks 2024, 2025 and 2026 as (P)
NUM = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d+)?$")

# Rows worth carrying forward, with a stable id. Anything else in the table is
# extracted too but marked as not portfolio-relevant, so adding a rule later
# never means re-reading the PDF.
IDS = {
    "Crescimento do PIB": ("pib", "% a.a.", True),
    "Taxa de desemprego": ("desemprego", "%", False),
    "IPCA": ("ipca", "% a.a.", True),
    "SELIC": ("selic", "% a.a.", True),
    "Taxa de Câmbio": ("cambio", "R$/US$", True),
    "Dívida bruta": ("divida_bruta", "% PIB", False),
    "Resultado primário do governo central": ("primario_central", "% PIB", False),
    "Conta Corrente (% PIB)": ("conta_corrente", "% PIB", False),
}

# Figures the narrative also states, in its own words. Declared here rather
# than inferred, so the check is auditable: pattern -> (variable, year).
#
# GAP is the key detail. The PDF's sidebar - author names, contact address -
# is interleaved into the body text by the text extractor, so a sentence reads
# "Projetamos avanço de 2,0% para Rodolfo Margato o PIB de 2025". Each pattern
# therefore tolerates a bounded run of non-digits between the figure and the
# phrase that identifies it. Bounded, so it can never reach across the document
# and match an unrelated number.
GAP = r"[^\d]{0,40}"
PROSE_CHECKS = [
    (rf"Projetamos avanço de ([\d,]+)% para{GAP}o PIB de 2025", "pib", 2025),
    (rf"[Pp]revemos alta de ([\d,]+)%{GAP}para o PIB de 2026", "pib", 2026),
    (rf"Mantivemos a projeção de ([\d,]+)%{GAP}para o IPCA de 2025", "ipca", 2025),
    (r"Projetamos inflação \(IPCA\) de ([\d,]+)% em 2026", "ipca", 2026),
    (r"taxa Selic terminal em ([\d,]+)%", "selic", 2025),
    (rf"([\d,]+) reais por dólar (?:para o|no) final de 2025", "cambio", 2025),
    (rf"final de 2025{GAP}e ([\d,]+) para o final de 2026", "cambio", 2026),
    (r"([\d,]+) reais por dólar no final de 2026", "cambio", 2026),
    (r"DBGG e PIB atingirá ([\d,]+)% em 2025", "divida_bruta", 2025),
    (r"atingirá [\d,]+% em 2025 e ([\d,]+)% em 2026", "divida_bruta", 2026),
    (r"de aproximadamente ([\d,]+)% no final de 2024", "desemprego", 2024),
    (r"para ([\d,]+)% no final de 2025", "desemprego", 2025),
]

issues: list[tuple[str, str]] = []


def log(sev: str, msg: str) -> None:
    issues.append((sev, msg))
    print(f"  [{sev}] {msg}")


def fail(msg: str) -> None:
    print(f"\nFALHOU: {msg}")
    sys.exit(1)


def to_float(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def parse_table(block: str) -> dict[str, list[str]]:
    """
    Label, then nine values. The header years sit before the first label and the
    projection-year header is glued to the first projected value, so each token
    is reduced to its last line before being classified.
    """
    rows: dict[str, list[str]] = {}
    label: str | None = None
    for raw in block.split("\n\n"):
        tok = raw.strip().split("\n")[-1].strip()
        if not tok:
            continue
        if NUM.match(tok) or tok == "--":
            if label is not None and len(rows[label]) < len(YEARS):
                rows[label].append(tok)
        else:
            label = tok
            rows[label] = []
    return {k: v for k, v in rows.items() if v}


if not SRC_TXT.exists():
    fail(f"fonte ausente: {SRC_TXT.relative_to(ROOT)}")

text = SRC_TXT.read_text(encoding="utf-8")

# ---------------------------------------------------- 1. both copies of the table
spans = [m.start() for m in re.finditer(re.escape(TABLE_START), text)]
if not spans:
    fail(f"bloco '{TABLE_START}' nao encontrado no documento")
copies = []
for s in spans:
    e = text.find(TABLE_END, s)
    if e == -1:
        continue
    copies.append(parse_table(text[s:e]))
copies = [c for c in copies if len(c) > 3]
print(f"[tabela] {len(copies)} copia(s) da tabela de projecoes encontradas")
if not copies:
    fail("nenhuma copia parseavel da tabela")

base = copies[0]
for n, other in enumerate(copies[1:], start=2):
    if other != base:
        diff = [k for k in set(base) | set(other) if base.get(k) != other.get(k)]
        fail(f"copia {n} da tabela diverge da copia 1 em: {', '.join(sorted(diff))}")
if len(copies) > 1:
    print(f"[valida] as {len(copies)} copias da tabela sao identicas")
else:
    log("warning", "tabela aparece uma unica vez: sem conferencia cruzada interna")

# ---------------------------------------------------- 2. shape of every row
for label, vals in base.items():
    if len(vals) != len(YEARS):
        fail(f"'{label}': {len(vals)} valores, esperados {len(YEARS)}")
print(f"[valida] {len(base)} linhas, {len(YEARS)} valores cada")

# ---------------------------------------------------- 3. build the register
rows = []
for label, vals in base.items():
    vid, unit, relevant = next(
        ((v, u, r) for k, (v, u, r) in IDS.items() if label.startswith(k)),
        (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40], "", False))
    for year, raw in zip(YEARS, vals):
        if raw == "--":
            continue
        rows.append({"variable_id": vid, "label": label, "unit": unit, "year": year,
                     "value_raw": raw, "value": to_float(raw),
                     "is_projection": year >= PROJECTED_FROM,
                     "portfolio_relevant": relevant,
                     "source": "Projeções XP (tabela)", "prose_check": "", "note": ""})
df = pd.DataFrame(rows)

# ---------------------------------------------------- 4. table against the prose
prose = text
for s in spans:
    e = text.find(TABLE_END, s)
    if e != -1:
        prose = prose.replace(text[s:e + len(TABLE_END)], " ")

# Line breaks fall wherever the PDF put them, so the narrative is matched on
# whitespace-normalised text and the patterns above are written with single
# spaces. Without this, a check silently never fires - which reads like
# agreement and is the worst possible failure for a verification step.
prose = re.sub(r"\s+", " ", prose)
checked = diverged = 0
for pat, vid, year in PROSE_CHECKS:
    m = re.search(pat, prose, re.S)
    if not m:
        # A check that never fires looks exactly like a check that passed.
        log("warning", f"{vid} {year}: padrao de conferencia nao encontrou "
                       f"correspondencia no texto - figura NAO conferida")
        continue
    said = m.group(1)
    sel = (df["variable_id"] == vid) & (df["year"] == year)
    if not sel.any():
        continue
    tabela = df.loc[sel, "value"].iloc[0]
    checked += 1
    if abs(to_float(said) - tabela) < 1e-9:
        df.loc[sel, "prose_check"] = f"confere com o texto ({said})"
    else:
        diverged += 1
        df.loc[sel, "prose_check"] = f"DIVERGE do texto ({said})"
        df.loc[sel, "note"] = (f"A tabela traz {df.loc[sel, 'value_raw'].iloc[0]} e o texto "
                               f"{said}. Valor da tabela mantido; divergencia declarada.")
        log("warning", f"{vid} {year}: tabela {df.loc[sel, 'value_raw'].iloc[0]} "
                       f"vs texto {said}")
print(f"[valida] {checked} figuras conferidas contra o texto, {diverged} divergentes")

OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(OUT_CSV, index=False, encoding="utf-8")

# ---------------------------------------------------- report
print("\n" + "=" * 72)
print(f"{len(df)} observacoes  ·  {df['variable_id'].nunique()} variaveis  ·  "
      f"{int(df['is_projection'].sum())} projecoes")
print("=" * 72)
proj = df[df["is_projection"] & df["portfolio_relevant"]]
for vid, g in proj.groupby("variable_id"):
    cells = "   ".join(f"{int(r['year'])}: {r['value_raw']:>6}" for _, r in g.iterrows())
    unit = g["unit"].iloc[0]
    print(f"  {vid:14} {cells}   {unit}")
    for _, r in g.iterrows():
        if r["prose_check"].startswith("DIVERGE"):
            print(f"     ! {int(r['year'])}: {r['note']}")
print(f"\n{OUT_CSV.relative_to(ROOT)}")
print(f"{len(issues)} ressalva(s)" if issues else "sem ressalvas")
