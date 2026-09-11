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
client = OpenAI()
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
            r = client.chat.completions.create(**kwargs)
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
    extra = f", distribuído em pelo menos {t['min_issuers']} emissores" if t.get("min_issuers") else ""
    return f"  - {t['action'].upper()} {alvo}: R$ {t['amount_brl']:,.2f}{extra} | {t['reason']}"


plan_block = ""
if plan:
    plan_block = ("\n".join(trade_line(t) for t in plan["trades"])
                  + f"\n  Após essas operações, nenhuma das {len(plan['violations_before'])} "
                    "violações de suitability permanece.")

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
PERIOD: April 2025 (from {pack['period_start']} to {pack['period_end']}).

AUTHORISED FIGURES (quote verbatim, never alter):
{fig_block}

SUITABILITY FINDINGS from the deterministic rule engine. Phrase these as advice;
do not add findings of your own and do not soften them away. When you quote a
figure from a finding, use the authorised figure whose name contains that
finding's rule id (pct_medido_... or valor_regra_...). Do NOT substitute a
similar-looking figure from elsewhere in the list: the percentages are measured
against different bases and swapping them changes the meaning.
{rec_block}

PROPOSED REBALANCE from the same rule engine. Present these as the concrete next
steps, with their amounts, in the recommendations section. Say plainly that sells
name instruments while the purchase names a product family and a credit criterion,
because the specific issuer is chosen with the client. State that the plan clears
every breach:
{plan_block or "(sem plano)"}

MACROECONOMIC CONTEXT (already summarised, do not add figures of your own).
Use the authorised macro_* figures to make this section concrete rather than
generic - state the projections instead of describing them in adjectives:
{macro_summary or "(indisponivel)"}

CLIENT RISK PROFILE (verbatim source document):
{profile_raw[:4000]}

OUTPUT FORMAT: reply with a single JSON object and nothing else. The document
template supplies all headings, tables and charts, so write prose only - no
headings, no bullets, no markdown, no figures beyond the authorised ones.

{{
  "highlights": [three one-sentence takeaways, the three things the client must not
                 miss; each may contain authorised figures],
  "greeting":   "one short paragraph: purpose of the letter, addressed to the client by first name",
  "performance":"one paragraph: the month's result, what drove it, the positions
                 that contributed most. Be explicit that the outperformance came
                 from equity exposure in a strong month for the stock market and
                 not from manager skill",
  "macro":      "one paragraph: the backdrop and what it means for positioning
                 from here, stating the authorised macro_* projections",
  "recommendations": [two paragraphs: the first states the breaches and what they
                 cost the client, the second explains the logic and priority of
                 the plan WITHOUT restating its amounts, which the table carries],
  "coverage":   "one sentence declaring the mark-to-market coverage",
  "closing":    "one short paragraph offering to discuss - do NOT sign, the
                 template adds the signature"
}}

Total length across all fields: about 450 words. Keep it tight; the layout is
fixed at two pages and long prose breaks it."""

def parse_sections(raw: str) -> dict:
    """The model sometimes wraps JSON in a code fence; tolerate that, nothing more."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        s = s[4:] if s.lower().startswith("json") else s
    return json.loads(s.strip())


def flatten(sec: dict) -> str:
    """The verification gate reads plain text, so the sections are concatenated."""
    parts = list(sec.get("highlights", [])) + [
        sec.get("greeting", ""), sec.get("performance", ""), sec.get("macro", "")]
    parts += list(sec.get("recommendations", []))
    parts += [sec.get("coverage", ""), sec.get("closing", ""), ADVISOR]
    return "\n\n".join(p for p in parts if p)


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
    letter = flatten(sections)
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
