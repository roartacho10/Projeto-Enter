"""
Repository hygiene check, independent of any git client.

Lists what would be published, flags anything that must never leave the machine
(credentials, bulk downloads) and reports the total size. Run it before the
first publish and before any later one.

    python check_repo.py
"""
from __future__ import annotations
from pathlib import Path
import fnmatch
import re
import sys

ROOT = Path(__file__).resolve().parent
SECRET_HINTS = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|api[_-]?key\s*[=:]\s*['\"][^'\"]{8,})", re.I)
BIG_MB = 5


def rules() -> list[str]:
    p = ROOT / ".gitignore"
    if not p.exists():
        print("!! .gitignore nao encontrado — NAO publique ainda."); sys.exit(1)
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def ignored(rel: str, pats: list[str]) -> bool:
    parts = rel.split("/")
    for pat in pats:
        p = pat.rstrip("/")
        if pat.endswith("/") and any(seg == p for seg in parts[:-1]):
            return True
        if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(parts[-1], p):
            return True
        if rel.startswith(p + "/"):
            return True
    return False


def main() -> int:
    pats = rules()
    included, skipped, total = [], 0, 0
    for f in sorted(ROOT.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(ROOT).as_posix()
        if rel.startswith(".git/"):
            continue
        if ignored(rel, pats):
            skipped += 1
            continue
        included.append((rel, f.stat().st_size))
        total += f.stat().st_size

    print(f"{len(included)} arquivos seriam publicados  ·  "
          f"{total/1024:.0f} KB  ·  {skipped} ignorados pelo .gitignore\n")

    alerts = []
    for rel, size in included:
        if size > BIG_MB * 1024 * 1024:
            alerts.append(f"ARQUIVO GRANDE  {rel}  ({size/1024/1024:.1f} MB)")
        if Path(rel).name in (".env", "secrets.toml") or rel.startswith("CSVs exportados"):
            alerts.append(f"NAO DEVE SUBIR   {rel}")
        if size < 2 * 1024 * 1024 and Path(rel).suffix in (
                ".py", ".md", ".txt", ".csv", ".json", ".html", ".toml", ".yml", ".yaml"):
            try:
                m = SECRET_HINTS.search((ROOT / rel).read_text(encoding="utf-8", errors="ignore"))
                if m:
                    alerts.append(f"PARECE CREDENCIAL  {rel}  ('{m.group(0)[:18]}...')")
            except OSError:
                pass

    by_dir: dict[str, list[int]] = {}
    for rel, size in included:
        d = rel.split("/")[0] if "/" in rel else "(raiz)"
        by_dir.setdefault(d, []).append(size)
    for d, sizes in sorted(by_dir.items(), key=lambda kv: -sum(kv[1])):
        print(f"  {d:26} {len(sizes):4} arquivos   {sum(sizes)/1024:8.0f} KB")

    print()
    if alerts:
        for a in alerts:
            print(f"  [ALERTA] {a}")
        print("\nRESOLVA OS ALERTAS ANTES DE PUBLICAR")
        return 1
    print("Nenhum alerta. Seguro para publicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
