"""
L5 - Verification gate.

Runs after generation and before any human sees the letter. Every figure in
the text must appear, character for character, in the authorised figure sheet
produced by figures.py. Exact matching rather than numeric tolerance is what
makes this hard to fool: a plausible-looking invented number has no chance of
coinciding with an authorised string.

This is the layer the first version of the workflow did not have, and the one
that would have caught its three invented figures and its wrong client name.

Run: python src/verify.py [path/to/letter.txt]
"""
from __future__ import annotations
from pathlib import Path
import json
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context import OUT, client  # noqa: E402

_c = client()
CLIENT_NAME = _c["name"].split()[0]
ADVISOR_NAME = _c["advisor"]
MAX_WORDS = 700                       # ~2 pages in Portuguese
FORBIDDEN = [                         # language a regulated letter must not use
    "garantido", "garantia de retorno", "sem risco", "risco zero",
    "lucro certo", "rentabilidade garantida", "com certeza vai",
]
PLACEHOLDERS = ["prezado joão", "joão", "[", "xxx", "lorem"]

# A digit glued to letters is part of a ticker (HAPV3, AZZA3), not a claim.
NUM = re.compile(r"(?<![\w/.,])\d{1,3}(?:\.\d{3})*(?:,\d+)?(?![\w/])")
DATEISH = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}|\b(?:19|20)\d{2}\b")


def allowed_strings() -> set[str]:
    """The only numeric strings the letter may contain, from figures.json."""
    fig = json.loads((OUT / "figures.json").read_text(encoding="utf-8"))
    out: set[str] = set()
    for v in fig.values():
        out.add(v)
        out.add(v.replace("R$ ", "").replace("%", "").replace(" p.p.", "").lstrip("+"))
    return out


def verify(text: str) -> list[tuple[str, str, str]]:
    """Returns a list of (severity, code, message)."""
    issues: list[tuple[str, str, str]] = []
    allowed = allowed_strings()
    masked = DATEISH.sub(" ", text)          # dates and years are not claims

    for token in NUM.findall(masked):
        if "," not in token and token.isdigit() and 1900 <= int(token) <= 2100:
            continue                          # bare year
        if token in allowed:
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
