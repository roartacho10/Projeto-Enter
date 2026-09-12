"""
L5b - Claim checking: does the prose say things the data supports?

The figure gate proves every number in the letter exists and was computed. It
says nothing about the sentences around them. A letter can quote only
authorised figures and still assert "o CDB foi resgatado" about a position that
is still held, or "recomendo aumentar a renda variavel" when the plan reduces
it. Every digit checks out; the letter is false.

This module closes that gap for a DECLARED catalogue of claim shapes. It is not
natural-language understanding and does not pretend to be: it extracts claims
whose shape is listed below and confronts each with the MetricsPack, the
RebalancePlan and the RecommendationSet. Anything outside the catalogue is
counted as unverified and reported as such, because a verifier that hides its
coverage is worse than one that has none.

The catalogue:

  A  direction  - an instrument said to have risen or fallen, against its return
  B  execution  - any past-tense trade language at all; nothing was executed
  C  action     - a trade recommended for an instrument the plan does not touch
  D  comparison - "beat the CDI", "above inflation", against the measured excess
  E  compliance - "within the limit", "suitable", while breaches are open
  F  attribution - a figure attached to the WRONG instrument

F is the deepest of the six and the reason this module exists. The figure gate
proves "18,94%" is an authorised number; it cannot prove the number belongs to
MRFG3. A letter reading "LREN3, com retorno de 18,94%" passes every digit check
and misattributes the result. F catches exactly that: a percentage in a
sentence that matches ANOTHER position's figure and none of this one's.

Deliberately conservative. A sentence naming two instruments, or carrying two
opposite directions, is ambiguous and is SKIPPED rather than guessed at: this
runs inside the generation retry loop, and a false accusation would send the
model in circles over a sentence that was fine.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import json
import re
import sys
import unicodedata

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context import OUT, REF  # noqa: E402

# --- A: direction words, by the sign they assert
UP = r"\b(?:subiu|subiram|avanç\w+|valoriz\w+|ganh\w+|alta de|cresc\w+|apreci\w+|contribuiu positivamente)\b"
DOWN = r"\b(?:caiu|caíram|cair\w*|recu\w+|desvaloriz\w+|perd\w+|queda de|baixa de|ced\w+|recuo de|contribuiu negativamente)\b"

# --- B: the letter recommends; it never reports an executed trade
EXECUTED = [
    r"\bfoi\s+(?:vendid|resgatad|aplicad|comprad)\w*", r"\bforam\s+(?:vendid|resgatad|aplicad|comprad)\w*",
    r"\bvendemos\b", r"\bcompramos\b", r"\bresgatamos\b", r"\baplicamos\b",
    r"\bexecutamos\b", r"\brealizamos a (?:venda|compra|aplicação|aplicacao)\b",
    r"\bja?\s+(?:vendid|resgatad|aplicad)\w*",
]

# --- C: recommendation verbs, mapped to the plan's action vocabulary
ACTION_WORDS = {
    "sell": r"\bvend\w+\b", "redeem": r"\bresgat\w+\b", "buy": r"\b(?:aplic\w+|compr\w+|aloc\w+)\b",
}
RECOMMENDING = r"\b(?:recomend\w+|sugir\w+|sugest\w+|propon\w+|orient\w+|indic\w+|convém|deve)\b"

# --- D
BEAT_CDI = r"super\w+ o cdi|acima do cdi|ante o cdi|melhor que o cdi|à frente do cdi"
LOST_CDI = r"abaixo do cdi|aquém do cdi|perdeu (?:do|para o) cdi|inferior ao cdi"
BEAT_INFL = r"acima da inflação|super\w+ a inflação|ganho real|retorno real positivo|protegeu o poder de compra"
LOST_INFL = r"abaixo da inflação|perdeu poder de compra|perda real|retorno real negativo"

# --- E
COMPLIANT = (r"dentro d\w+ limites?|em conformidade|aderente ao mandato|"
             r"adequad\w+ ao (?:seu )?perfil|sem pontos de atenção|nenhum ajuste necessário")

# --- F: a percentage written in the Brazilian form, with its sign if present
PCT = re.compile(r"([+-]?\d{1,3}(?:\.\d{3})*,\d+)\s*(?:%|p\.p\.)")

# Portfolio-level subjects, so a direction claim about the whole portfolio is
# checked too - that is where most of the prose actually lives.
WHOLE = {"carteira": "__TOTAL__", "patrimônio": "__TOTAL__", "patrimonio": "__TOTAL__",
         "recursos investidos": "__INVESTED__", "capital investido": "__INVESTED__"}


@dataclass
class Claim:
    kind: str
    text: str
    detail: str


def _load(name: str):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _entities() -> dict[str, str]:
    """Searchable name -> instrument_id. Longest names are matched first."""
    ins = pd.read_csv(REF / "instruments.csv", dtype=str).fillna("")
    out: dict[str, str] = {}
    for _, r in ins.iterrows():
        for n in (r["display_name"], r["ticker_statement"], r["ticker_current"],
                  r["statement_name"]):
            n = (n or "").strip()
            if len(n) >= 4 and r["instrument_id"] not in ("IBOV",):
                out[n.lower()] = r["instrument_id"]
    aliases = REF / "instrument_aliases.csv"
    if aliases.exists():
        valid_ids = set(ins["instrument_id"])
        for row in pd.read_csv(aliases, dtype=str).fillna("").to_dict("records"):
            alias, iid = row["alias"].strip().lower(), row["instrument_id"]
            if iid not in valid_ids or not alias or (alias in out and out[alias] != iid):
                raise ValueError(f"Invalid or ambiguous instrument alias: {alias}")
            out[alias] = iid
    return out


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.;!?])\s+", flat) if s.strip()]


def check_allocation_claims(text: str, target: dict) -> list[tuple[str, str, str]]:
    """Check explicit target/forecast phrases; not arbitrary natural language."""
    low = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))
    issues = []
    number = r"\s*(?:de|:|e|em)?\s*([+-]?\d+(?:,\d+)?)\s*%"
    for label, key in ((r"(?:rv|renda variavel)", "target_rv_pct"),
                       (r"(?:rf|renda fixa)", "target_rf_pct")):
        pattern = r"\balvo(?:\s+(?:de|da|em))?\s+" + label + number
        for match in re.finditer(pattern, low):
            if abs(float(match.group(1).replace(",", ".")) - target[key]) > .011:
                issues.append(("blocker", "claims_allocation", f"Alvo atribuído à classe incorreta: {match.group(0)}"))
    for label, key in ((r"(?:rv|renda variavel)", "rv_real_cumulative_pct"),
                       (r"(?:rf|renda fixa)", "rf_real_cumulative_pct")):
        pattern = r"retorno real estimado acumulado (?:da|de) " + label + number
        for match in re.finditer(pattern, low):
            if abs(float(match.group(1).replace(",", ".")) - target[key]) > .011:
                issues.append(("blocker", "claims_allocation", f"Estimativa atribuída à classe incorreta: {match.group(0)}"))
    return issues


def check(text: str) -> tuple[list[tuple[str, str, str]], dict]:
    """
    Returns (issues, coverage). Coverage is reported, never implied: it says how
    many sentences carried a checkable claim, how many were confirmed, and how
    many were skipped for ambiguity.
    """
    issues: list[tuple[str, str, str]] = []
    pack, plan, recs = _load("metrics_pack.json"), _load("rebalance_plan.json"), \
        _load("recommendations.json")
    if pack is None:
        return ([("warning", "claims_not_checked",
                  "metrics_pack.json ausente: afirmacoes nao conferidas")],
                {"checked": 0, "skipped": 0, "sentences": 0})

    ret = {m["instrument_id"]: m["return_pct"] for m in pack["positions"]}
    ents = _entities()
    ents.update(WHOLE)
    names = sorted(ents, key=len, reverse=True)
    ret["__TOTAL__"] = pack["total_return_pct"]
    ret["__INVESTED__"] = pack["invested_return_pct"]

    # Every figure each position legitimately owns, as the strings the letter
    # would write. A number in one position's sentence that belongs to another
    # position and to no portfolio-level total is a misattribution.
    def canonical_number(value: str) -> Decimal:
        return Decimal(value.replace(".", "").replace(",", "."))

    def _br(v, nd=2):
        return canonical_number(f"{v:.{nd}f}".replace(".", ","))

    owned: dict[str, set[Decimal]] = {}
    for m in pack["positions"]:
        owned[m["instrument_id"]] = {
            _br(m["return_pct"]), _br(abs(m["return_pct"])),
            _br(m["weight_end_pct"]), _br(m["weight_start_pct"]),
            _br(m["contribution_pp"]), _br(abs(m["contribution_pp"])),
            _br(m["contribution_pp"], 3), _br(abs(m["contribution_pp"]), 3),
        }
    portfolio_figs = {_br(pack[k]) for k in (
        "total_return_pct", "invested_return_pct", "cash_pct_of_total_end", "coverage_pct",
        "cdi_return_pct", "ipca_return_pct", "excess_over_cdi_pp",
        "real_return_total_pct", "ibov_return_pct")}
    portfolio_figs |= {_br(abs(pack["excess_over_cdi_pp"]))}
    planned: dict[str, set[str]] = {}
    for t in (plan or {}).get("trades", []):
        if t.get("instrument_id"):
            planned.setdefault(t["instrument_id"], set()).add(t["action"])
    open_findings = len((recs or {}).get("recommendations", []))

    sentences = _sentences(text)
    checked = skipped = 0
    low_all = " ".join(sentences).lower()
    target = _load("allocation_target.json")
    if target:
        issues.extend(check_allocation_claims(text, target))
    # Future scenarios must not be compared to realised monthly returns.
    forecast_words = r"estimad|proje[çc]|esperad|futur|\balvo\b|cenario|cenário"
    historical_text = " ".join(s for s in sentences if not re.search(forecast_words, s.lower())).lower()

    # ---------------- B: execution language, sentence-independent
    for pat in EXECUTED:
        m = re.search(pat, low_all)
        if m:
            issues.append(("blocker", "claims_execution",
                           f"a carta afirma operação já executada ('{m.group(0)}'); "
                           f"o plano é recomendação, nada foi executado"))

    # ---------------- D: comparisons against the declared references
    if re.search(BEAT_CDI, historical_text) and pack["excess_over_cdi_pp"] <= 0:
        issues.append(("blocker", "claims_comparison",
                       f"afirma ter superado o CDI, mas o excesso medido é "
                       f"{pack['excess_over_cdi_pp']:+.2f} p.p."))
        checked += 1
    elif re.search(BEAT_CDI, historical_text):
        checked += 1
    if re.search(LOST_CDI, historical_text) and pack["excess_over_cdi_pp"] >= 0:
        issues.append(("blocker", "claims_comparison",
                       f"afirma ter ficado abaixo do CDI, mas o excesso medido é "
                       f"{pack['excess_over_cdi_pp']:+.2f} p.p."))
        checked += 1
    if re.search(BEAT_INFL, historical_text) and pack["real_return_total_pct"] <= 0:
        issues.append(("blocker", "claims_comparison",
                       f"afirma ganho real, mas o retorno real medido é "
                       f"{pack['real_return_total_pct']:+.2f}%"))
        checked += 1
    elif re.search(BEAT_INFL, historical_text):
        checked += 1
    if re.search(LOST_INFL, historical_text) and pack["real_return_total_pct"] >= 0:
        issues.append(("blocker", "claims_comparison",
                       f"afirma perda de poder de compra, mas o retorno real medido é "
                       f"{pack['real_return_total_pct']:+.2f}%"))
        checked += 1

    # ---------------- E: compliance claimed while findings are open
    if re.search(COMPLIANT, low_all) and open_findings:
        issues.append(("blocker", "claims_compliance",
                       f"afirma conformidade com o mandato, mas há {open_findings} "
                       f"ponto(s) de atenção em aberto"))
        checked += 1

    # ---------------- A and C: per sentence, and only when unambiguous
    #
    # F is handled differently. A sentence that enumerates three positions with
    # a figure each is perfectly correct prose, and the first version of this
    # module flagged it: it picked one of the three entities arbitrarily and
    # accused it of owning the other two's numbers. So each figure is attributed
    # to the NEAREST ENTITY MENTIONED BEFORE IT, which is how the enumeration
    # reads, and which still catches a genuine swap inside a long sentence.
    for s_ in sentences:
        low = s_.lower()
        spans = sorted((m.start(), ents[n]) for n in names
                       for m in re.finditer(r"(?<!\w)" + re.escape(n) + r"(?!\w)", low))
        hits = {iid for _, iid in spans}
        if not hits:
            continue

        # ---- F: attribution, figure by figure
        for fm in PCT.finditer(s_):
            before = [iid for pos, iid in spans if pos < fm.start()]
            if not before:
                continue
            owner = before[-1]
            if owner not in owned:
                continue
            bare = canonical_number(fm.group(1).lstrip("+-"))
            if bare in owned[owner] or bare in portfolio_figs:
                checked += 1
                continue
            others = sorted(o for o, v in owned.items() if bare in v and o != owner)
            if others:
                issues.append(("blocker", "claims_attribution",
                               f"'{fm.group(1)}' aparece junto a {owner} mas e cifra de "
                               f"{', '.join(others)} — \"{s_[:90]}\""))
                checked += 1

        # ---- A and C need a single subject: with two, the sentence is skipped
        up, down = bool(re.search(UP, low)), bool(re.search(DOWN, low))
        wants = {a for a, pat in ACTION_WORDS.items() if re.search(pat, low)}
        if not (up or down or wants):
            continue
        if len(hits) > 1 or (up and down):
            skipped += 1
            continue
        iid = next(iter(hits))

        if up ^ down and iid in ret and not re.search(forecast_words, low):
            r = ret[iid]
            if up and r < 0:
                issues.append(("blocker", "claims_direction",
                               f"{iid}: a frase diz que subiu, o retorno medido e "
                               f"{r:+.2f}% — \"{s_[:90]}\""))
            elif down and r > 0:
                issues.append(("blocker", "claims_direction",
                               f"{iid}: a frase diz que caiu, o retorno medido e "
                               f"{r:+.2f}% — \"{s_[:90]}\""))
            checked += 1

        if wants and re.search(RECOMMENDING, low) and plan is not None:
            allowed = planned.get(iid, set())
            if (wants - allowed) and iid in ret and not iid.startswith("__"):
                issues.append(("blocker", "claims_action",
                               f"{iid}: a carta recomenda "
                               f"{'/'.join(sorted(wants - allowed))} mas o plano nao "
                               f"autoriza essa ação nesse ativo"))
            checked += 1

    return issues, {"sentences": len(sentences), "checked": checked, "skipped": skipped}
