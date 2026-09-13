"""
L4 - Letter generation, wrapped by L5.

The model receives the authorised figure sheet and is told to quote it
verbatim. It is never asked to compute, infer or round a number. After each
attempt the letter goes through verify.py; if the gate blocks it, the failures
are fed back and the model tries again. Verification is therefore not only a
gate but a control loop.

Model tiering is deliberate: the cheap model summarises, the better model
writes the text that reaches the client and touches figures.

Requires OPENAI_API_KEY in the environment. Never hard-code the key.
Run: python src/generate_letter.py
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify import verify  # noqa: E402
from allocation import MODEL_VERSION, fingerprint
from letter_content import parse_sections, flatten_sections

try:
    from openai import OpenAI
except ImportError:
    sys.exit("faltando a biblioteca: python -m pip install openai")

from context import OUT, ROOT, client  # noqa: E402

INP = ROOT / "data" / "raw" / "inputs"
_c = client()
# Model tiering, overridable without touching code:
#   the cheap model condenses research, the better one writes what reaches
#   the client and touches figures. Both are configurable so the cost/quality
#   trade-off can be revisited without a code change.
MODEL_DRAFT = os.environ.get("MODEL_DRAFT", "gpt-4.1-mini")
MODEL_LETTER = os.environ.get("MODEL_LETTER", "gpt-4.1")
TEMPERATURE = 0.2          # v1 used 0.5 on client-facing financial content
MAX_ATTEMPTS = 3
RUN_ID = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")

if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("OPENAI_API_KEY nao esta definida. No PowerShell:\n"
             '  setx OPENAI_API_KEY "sua-chave"\n'
             "  (feche e reabra o PowerShell depois)")
# Named `oai`, not `client`: `client` is the function imported from context
# that returns the client's registry row. Binding the OpenAI handle to that
# name shadowed it for the rest of the module and only worked because _c was
# taken first.
oai = OpenAI()
usage_log: list[dict] = []


def ask(model: str, system: str, user: str) -> str:
    """
    Chat call that adapts to the model's parameter surface. Newer reasoning
    models reject `temperature` and renamed `max_tokens`, so rather than
    pinning one shape we drop whatever the API objects to and retry. This
    keeps the pipeline working when the model is swapped via env var.
    """
    kwargs = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": TEMPERATURE,
        "max_completion_tokens": 2000,
    }
    for _ in range(4):
        try:
            r = oai.chat.completions.create(**kwargs)
            break
        except Exception as e:
            msg = str(e).lower()
            if "max_completion_tokens" in msg and "max_tokens" not in kwargs:
                kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
            elif "max_tokens" in msg and "max_completion_tokens" not in kwargs:
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
            elif "temperature" in msg and "temperature" in kwargs:
                kwargs.pop("temperature")
            else:
                raise
            print(f"      (ajustando parametros para {model}: {list(kwargs)[2:]})")
    else:
        raise RuntimeError(f"nao consegui uma combinacao de parametros aceita por {model}")
    usage_log.append({"model": model, "prompt_tokens": r.usage.prompt_tokens,
                      "completion_tokens": r.usage.completion_tokens})
    return (r.choices[0].message.content or "").strip()


def read(name: str) -> str:
    p = INP / name
    if not p.exists():
        print(f"  aviso: {p.name} nao encontrado em data/raw/inputs/ - seguindo sem ele")
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


figures = json.loads((OUT / "figures.json").read_text(encoding="utf-8"))
pack = json.loads((OUT / "metrics_pack.json").read_text(encoding="utf-8"))
recs = json.loads((OUT / "recommendations.json").read_text(encoding="utf-8"))
plan_p = OUT / "rebalance_plan.json"
plan = json.loads(plan_p.read_text(encoding="utf-8")) if plan_p.exists() else None
allocation = json.loads((OUT / "allocation_target.json").read_text(encoding="utf-8"))
if (not plan or plan.get("model_version") != MODEL_VERSION or not plan.get("resolved")
        or plan.get("allocation_fingerprint") != allocation.get("fingerprint")
        or allocation.get("metrics_run_id") != pack["run_id"]):
    sys.exit("Atualize os dados antes de gerar a carta: plano ausente, antigo ou inconsistente.")
macro_raw, profile_raw = read("macro_analysis.txt"), read("risk_profile.txt")

# ---------------------------------------------------------- stage 1: macro
print(f"[1/2] resumindo o macro com {MODEL_DRAFT}...")
macro_summary = ask(
    MODEL_DRAFT,
    "You are a macro analyst at a Brazilian investment firm.",
    "Summarise the research below in ONE paragraph in Brazilian Portuguese, for a "
    "middle-market individual investor. Cover only what bears on portfolio decisions: "
    "interest rates, inflation, currency, growth and the main risks. Do NOT invent "
    "figures; use only figures that appear in the text. Formal, analytical tone.\n\n"
    f"{macro_raw[:24000]}") if macro_raw else ""

# ---------------------------------------------------------- stage 2: letter
fig_block = "\n".join(f"  {k} = {v}" for k, v in figures.items())
def trade_line(t):
    alvo = t["instrument_id"] or f"{t['category']} ({t['criteria']})"
    extra = f", distribuído em pelo menos {t['min_products']} produtos distintos" if t.get("min_products") else ""
    return f"  - {t['action'].upper()} {alvo}: R$ {t['amount_brl']:,.2f}{extra} | {t['reason']}"


plan_block = ""
if plan:
    plan_block = ("\n".join(trade_line(t) for t in plan["trades"])
                  + "\n  Numerical class targets and product caps pass the simulation. "
                    "This is a suggestion, no trade has been executed. Pending reviews: "
                  + json.dumps(plan.get("pending_reviews", []), ensure_ascii=False))

# The allocation model used to reach the prompt as the whole allocation_target
# JSON: dozens of raw floats the figure sheet never authorised, handed to a
# model told to quote only authorised figures. The gate caught anything it
# copied, but the discipline is cheaper to keep than to enforce - so the model
# now sees only the horizon, the forecast years and the assumptions in words.
# Every number it may write is already in the alocacao_* keys above.
alloc_block = "\n".join(
    [f"  janela de projeção: de {allocation['start']} a {allocation['end']}",
     "  anos projetados: " + ", ".join(str(r["year"]) for r in allocation["annual"]),
     "  premissas:"]
    + [f"    - {a}" for a in allocation["assumptions"]])

rec_block = "\n".join(
    f"  - [{r['action']}] {r['observed']} | limite: {r['threshold']} | motivo: {r['rationale']}"
    for r in recs["recommendations"])

SYSTEM = (
    "You are an experienced investment advisor at a Brazilian brokerage writing the "
    "monthly letter for a middle-market client. You write in Brazilian Portuguese.\n\n"
    "ABSOLUTE RULE ON FIGURES: you may only state a number if it appears in the "
    "AUTHORISED FIGURES list, and you must copy it character for character "
    "(including the comma as decimal separator and the R$ prefix). Never compute, "
    "round, convert, annualise or estimate any number. If a figure you would like "
    "to mention is not on the list, describe it in words instead. Every number you "
    "write is checked against that list and the letter is rejected if one does not "
    "match.\n\n"
    "Never use language implying guaranteed returns or absence of risk."
)

USER_TEMPLATE = f"""Write the monthly investment letter for the client.

CLIENT: {_c["name"]}, addressed by first name. Risk profile: {_c["profile"]}. Advisor signing the letter: {_c["advisor"]}.
PERIOD: from {pack['period_start']} to {pack['period_end']}.

AUTHORISED FIGURES (quote verbatim, never alter):
{fig_block}

SUITABILITY FINDINGS from the deterministic rule engine. Phrase these as advice;
do not add findings of your own and do not soften them away. When you quote a
figure from a finding, use the authorised figure whose name contains that
finding's rule id (pct_medido_... or valor_regra_...). Do NOT substitute a
similar-looking figure from elsewhere in the list: the percentages are measured
against different bases and swapping them changes the meaning.
{rec_block}

PROPOSED REBALANCE from the same rule engine. Explain the concrete next
steps without repeating amounts already printed in the table. Say plainly that sells
name instruments while the purchase names a product family and a credit criterion,
because the specific product is chosen with the client. Never claim that every
suitability finding is cleared; any pending category reviews still need assessment:
{plan_block or "(sem plano)"}

CLASS ALLOCATION MODEL. The figures are already in the AUTHORISED list above
under the alocacao_* keys; the model's own assumptions, in the firm's words:
{alloc_block}
Explain that this is a model target, not a mathematically optimal portfolio or a
return promise. Compare cumulative real class estimates over the SAME forecast
window, not annual estimates with cumulative estimates. INTERNAL CONTEXT ONLY: GDP + assumed dividend
yield estimates RV; Selic deflated by IPCA estimates RF. Year-end Selic is used
as an annual proxy. Estimates exclude taxes and costs and are NOT forecasts for
individual positions, funds or stocks. The RF grouping includes credit and
multimarket funds with different risks.
Do not print these formulas, their abbreviations or the term dividend yield.
Write for a client with no investment training. Always spell out renda fixa and
renda variável; never use RF or RV. Explain any other acronym on first use,
including Selic (taxa básica de juros), IPCA (medida de inflação) and CDI
(referência de juros para investimentos). Prefer crescimento da economia to PIB.
Translate retorno real as ganho acima da inflação; explain technical concepts
through their practical effect on the client's savings. Use client-facing product
names, never internal identifiers, category codes, suitability or lookthrough.
Suggest selling current RV positions and using an unspecified Ibovespa index
fund for the target RV basket. Do not name an ETF ticker or individual stocks to
buy. An index fund may occupy the entire RV basket and remains exposed to market
risk. If the client prefers stock picking, each stock is limited to the authorised
MAX_SINGLE_POSITION_PCT percentage OF THE RV BASKET. The same cap applies per RF
PRODUCT OF THE RF BASKET, not per issuer or total wealth. Target cash is zero.
Explain that an index fund diversifies exposure across companies and reduces
single-company concentration, without eliminating market risk. Individual stocks
remain an alternative chosen by the client, subject to both the per-stock cap
and the overall RV ceiling for their risk profile and horizon.

MACROECONOMIC CONTEXT (already summarised, do not add figures of your own).
Use the authorised macro_* figures to make this section concrete rather than
generic - state the projections instead of describing them in adjectives:
{macro_summary or "(indisponivel)"}
Give this section about 80 words. Outline the market trends described in the
supplied case in everyday language, not current news outside the exercise.
Explain why the projected high interest rates can make interest-linked renda fixa
attractive, and how interest earned above inflation can increase purchasing power.
Relate this to the model's expected gains above inflation and to the client's
risk profile. Keep conditional language and the specified forecast period.
Discuss lower risk only for appropriately selected products; never describe the
entire renda fixa basket as risk-free or very low risk. Credit and multimarket
funds have distinct risks. Do not imply every product earns Selic, has guaranteed
returns, principal protection or insurance. Do not invent macro events or figures.

CLIENT RISK PROFILE (verbatim source document):
{profile_raw[:4000]}

OUTPUT FORMAT: reply with a single valid JSON object and nothing else.
Use exactly the keys and types below. Both highlights and recommendations MUST
be arrays of strings, never a single string or an object. All other fields MUST
be nonempty strings. Escape quotation marks and line breaks inside JSON strings.
Replace the English placeholders with concise Brazilian Portuguese prose.
The template supplies headings and tables. Use **bold** around one important
sentence or short clause per performance, macro and recommendation paragraph.
Emphasis must highlight measured performance, future macro implications or
adjustments consistent with the client's risk profile and supplied research.
No other Markdown or HTML is allowed. Never emphasise entire paragraphs.
Write the three highlights as connected sentences forming ONE flowing executive
summary: first measured performance, then the future impact of market events,
then adjustments aligned with the risk profile and the supplied research.
Do not turn all three highlights into performance figures or compliance findings.

{{
  "highlights": ["First takeaway.", "Second takeaway.", "Third takeaway."],
  "greeting": "Purpose of the letter, addressed to the client by first name.",
  "performance": "Monthly result, main contributors and separate comparisons with CDI and IPCA.",
  "macro": "Relevant macro projections and their meaning for positioning.",
  "recommendations": ["Main findings and pending reviews.", "Rationale for suggested adjustments and index preference."],
  "coverage": "Mark-to-market coverage using the authorised figure.",
  "closing": "Offer to discuss the suggestions. Do not sign."
}}

Keep the whole letter near 310 words: highlights about 40, greeting 20,
performance 55, macro 80, recommendations 90 in total, coverage 10 and closing 15.
In performance, do not blend CDI and IPCA. Ibovespa is market context only.
Do not attribute outperformance to manager skill without evidence.

The deterministic template already prints class targets, cumulative estimates,
product caps and trade amounts. Explain their rationale without duplicating
these numbers in prose. Total length across all fields: about 310 words. Keep it tight; the layout is
fixed at two pages and long prose breaks it."""

ADVISOR = _c["advisor"]
sections, letter, attempts = {}, "", []
for attempt in range(1, MAX_ATTEMPTS + 1):
    print(f"[2/2] gerando a carta com {MODEL_LETTER} (tentativa {attempt}/{MAX_ATTEMPTS})...")
    prompt = USER_TEMPLATE
    if attempts:
        prompt += ("\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED by the verification gate for the "
                   "reasons below. Fix every one of them. Numbers not on the authorised list "
                   "must be removed or replaced by the correct authorised figure.\n"
                   + "\n".join(f"  - {m}" for _, _, m in attempts[-1]))
    raw = ask(MODEL_LETTER, SYSTEM, prompt)
    try:
        sections = parse_sections(raw)
    except json.JSONDecodeError as e:
        print(f"      resposta nao veio em JSON valido ({e}); nova tentativa")
        attempts.append([("blocker", "invalid_json", f"reply was not valid JSON: {e}")])
        continue
    except ValueError as e:
        print(f"      estrutura da carta invalida ({e}); nova tentativa")
        attempts.append([("blocker", "invalid_sections", f"Invalid letter structure: {e}")])
        continue
    letter = flatten_sections(sections, ADVISOR)
    issues = verify(letter)
    blockers = [i for i in issues if i[0] == "blocker"]
    attempts.append(issues)
    print(f"      verificacao: {len(blockers)} bloqueios, "
          f"{len([i for i in issues if i[0] == 'warning'])} avisos")
    for sev, code, msg in issues:
        print(f"        [{sev}] {code}: {msg}")
    if not blockers:
        break

(OUT / "letter.txt").write_text(letter, encoding="utf-8")
(OUT / "letter_sections.json").write_text(
    json.dumps(sections, ensure_ascii=False, indent=2), encoding="utf-8")
(OUT / "generation_log.json").write_text(json.dumps({
    "run_id": RUN_ID, "model_draft": MODEL_DRAFT, "model_letter": MODEL_LETTER,
    "temperature": TEMPERATURE, "attempts": len(attempts),
    "approved": not [i for i in attempts[-1] if i[0] == "blocker"],
    "model_version": MODEL_VERSION,
    "figures_fingerprint": fingerprint(figures),
    "allocation_fingerprint": allocation["fingerprint"],
    "usage": usage_log,
    "total_prompt_tokens": sum(u["prompt_tokens"] for u in usage_log),
    "total_completion_tokens": sum(u["completion_tokens"] for u in usage_log),
}, ensure_ascii=False, indent=2), encoding="utf-8")

print("\n" + "=" * 70)
print(letter)
print("=" * 70)
print(f"secoes -> {OUT / 'letter_sections.json'}")
ok = not [i for i in attempts[-1] if i[0] == "blocker"]
print(f"\ncarta -> {OUT / 'letter.txt'}   ({len(letter.split())} palavras, "
      f"{len(attempts)} tentativa(s))")
print("APROVADA PELA VERIFICACAO" if ok else "REPROVADA apos todas as tentativas")
(OUT / "verification_report.json").write_text(json.dumps(
    [{"severity": sev, "code": code, "message": msg} for sev, code, msg in attempts[-1]],
    ensure_ascii=False, indent=2), encoding="utf-8")
if not ok:
    sys.exit(1)
