"""
L5 - Verification gate.

Runs after generation. Every numeric value must exist in the authorised sheet.
Decimal equality accepts grouping and trailing-zero formatting differences
(25,0 and 25,00), without rounding or numerical tolerance. Attribution and
direction are checked separately by claims.py.

This is the layer the first version of the workflow did not have, and the one
that would have caught its three invented figures and its wrong client name.

Run: python src/verify.py [path/to/letter.txt]
"""
from __future__ import annotations
from pathlib import Path
import json
import re
import sys
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context import OUT, client, clients  # noqa: E402
from claims import check as check_claims  # noqa: E402

_c = client()
CLIENT_NAME = _c["name"].split()[0]
ADVISOR_NAME = _c["advisor"]
MAX_WORDS = 700                       # ~2 pages in Portuguese
FORBIDDEN = [                         # language a regulated letter must not use
    "garantido", "garantia de retorno", "sem risco", "risco zero",
    "lucro certo", "rentabilidade garantida", "com certeza vai",
]
# A placeholder is template text that survived, not a name that happens to be
# common. The old list blocked the literal string "joão", so a client actually
# called João could never receive a letter, and blocked "[", so any bracket in
# the prose failed the gate. What is checked instead: an unfilled bracketed
# slot, obvious filler, and the first name of ANY OTHER client on the roster -
# which catches "Prezado João" generically and also catches the real risk in a
# multi-client run, a letter addressed to the previous client.
PLACEHOLDERS = ["xxx", "lorem ipsum", "nome do cliente", "seu nome aqui"]
BRACKETED = re.compile(r"\[[^\]\n]{1,60}\]")

# A digit glued to letters is part of a ticker (HAPV3, AZZA3), not a claim.
NUMBER = r"[+\-−]?\d+(?:\.\d{3})*(?:,\d+)?"
NUM = re.compile(r"(?<![\w/.,+\-−])" + NUMBER + r"(?![\w/]|[.,]\d)")
DATEISH = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}|\b(?:19|20)\d{2}\b")


def other_client_first_names() -> set[str]:
    """First names on the roster that are not this client's, lowercased."""
    mine = CLIENT_NAME.lower()
    return {str(n).split()[0].lower() for n in clients()["name"]
            if str(n).strip() and str(n).split()[0].lower() != mine}


def allowed_strings() -> set[str]:
    """The only numeric strings the letter may contain, from figures.json."""
    fig = json.loads((OUT / "figures.json").read_text(encoding="utf-8"))
    out: set[str] = set()
    for v in fig.values():
        out.add(v)
        out.add(v.replace("R$ ", "").replace("%", "").replace(" p.p.", "").lstrip("+"))
    return out


def exact_number(token: str) -> Decimal:
    """Ignore grouping and trailing zeros, never round or use a tolerance."""
    return Decimal(token.replace("−", "-").replace(".", "").replace(",", "."))


def verify(text: str) -> list[tuple[str, str, str]]:
    """Returns a list of (severity, code, message)."""
    issues: list[tuple[str, str, str]] = []
    allowed = allowed_strings()
    allowed_values = {exact_number(value) for value in allowed if re.fullmatch(NUMBER, value)}
    masked = DATEISH.sub(" ", text)          # dates and years are not claims

    for token in NUM.findall(masked):
        if "," not in token and token.isdigit() and 1900 <= int(token) <= 2100:
            continue                          # bare year
        if token in allowed or exact_number(token) in allowed_values:
            continue
        issues.append(("blocker", "ungrounded_number",
                       f"'{token}' nao esta na folha de cifras autorizadas (figures.json)"))

    low = re.sub(r"\s+", " ", text.lower())   # newlines must not hide a phrase
    if CLIENT_NAME.lower() not in low:
        issues.append(("blocker", "client_name_missing",
                       f"a carta nao menciona o cliente '{CLIENT_NAME}'"))
    for p in PLACEHOLDERS:
        if p in low:
            issues.append(("blocker", "placeholder_leak",
                           f"texto de exemplo ou placeholder vazou: '{p}'"))
    for m in BRACKETED.findall(text):
        issues.append(("blocker", "placeholder_leak",
                       f"campo de modelo nao preenchido: '{m}'"))
    for other in other_client_first_names():
        if re.search(rf"\b{re.escape(other)}\b", low):
            issues.append(("blocker", "wrong_client_name",
                           f"a carta menciona '{other.capitalize()}', que e outro cliente "
                           f"do cadastro, e nao '{CLIENT_NAME}'"))
    if ADVISOR_NAME.lower() not in low:
        issues.append(("warning", "advisor_missing",
                       f"a carta nao assina como '{ADVISOR_NAME}'"))
    for f in FORBIDDEN:
        if f in low:
            issues.append(("blocker", "forbidden_language",
                           f"linguagem vedada em material do cliente: '{f}'"))
    n = len(text.split())
    if n > MAX_WORDS:
        issues.append(("blocker", "too_long", f"{n} palavras, limite {MAX_WORDS} (~2 paginas)"))
    if not re.search(r"cobertura|marcad", low):
        issues.append(("warning", "coverage_not_declared",
                       "a carta nao declara a cobertura de marcacao a mercado"))

    # Numbers are not claims. The figure gate above proves every digit was
    # computed; this confronts what the sentences ASSERT with the same data.
    claim_issues, cov = check_claims(text)
    issues.extend(claim_issues)
    issues.append(("info", "claims_coverage",
                   f"{cov['checked']} afirmacao(oes) conferida(s) em {cov['sentences']} "
                   f"frases; {cov['skipped']} ignorada(s) por ambiguidade"))
    return issues


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "letter.txt"
    if not path.exists():
        print(f"carta nao encontrada: {path}"); sys.exit(2)
    text = path.read_text(encoding="utf-8")
    issues = verify(text)
    blockers = [i for i in issues if i[0] == "blocker"]
    print(f"verificando: {path.name}  ({len(text.split())} palavras)\n")
    if not issues:
        print("  nenhum problema encontrado")
    for sev, code, msg in issues:
        print(f"  [{sev.upper():7}] {code:22} {msg}")
    (OUT / "verification_report.json").write_text(
        json.dumps([{"severity": s, "code": c, "message": m} for s, c, m in issues],
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + ("APROVADA" if not blockers else f"REPROVADA: {len(blockers)} bloqueios"))
    sys.exit(0 if not blockers else 1)
