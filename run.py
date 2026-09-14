"""
One command runs the whole pipeline.

    python run.py                      # Albert, full run
    python run.py --client ALBERT      # explicitly select the complete case
    python run.py --all                # every client in clients.csv
    python run.py --all --no-llm       # refresh metrics and rules only (the triage pass)
    python run.py --fetch              # re-download market data first (needs network)

Stages are ordinary scripts; each validates its own output and exits non-zero on
failure, so the first broken stage stops the run instead of letting a bad number
reach the letter.
"""
from __future__ import annotations
from pathlib import Path
import argparse
import os
import subprocess
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

# "shared" stages price the whole universe once, not once per client
STAGES = [
    ("extract_macro",   "Lê as projeções do relatório",    {"shared"}),
    ("extract_profile", "Lê os perfis de risco",           {"shared"}),
    ("fetch_market",    "Baixa preços e séries macro",     {"fetch", "shared"}),
    ("fetch_history",   "Baixa 24 meses de histórico",     {"fetch", "shared"}),
    ("build_prices",    "Monta a tabela de preços",        {"shared"}),
    ("compute_metrics", "Calcula o MetricsPack",           set()),
    ("derive_policy",   "Deriva os limites do perfil",     set()),
    ("recommend",       "Aplica as regras de suitability", set()),
    ("rebalance",       "Dimensiona compras e vendas",     set()),
    ("history",         "Monta a trajetória mensal",       set()),
    ("figures",         "Publica a folha de cifras",       set()),
    ("generate_letter", "Escreve e verifica a carta",      {"llm"}),
    ("charts",          "Desenha os gráficos",             set()),
    ("render_pdf",      "Renderiza o PDF",                 set()),
]


def run_stage(name: str, client: str | None = None) -> int:
    env = dict(os.environ)
    if client:
        env["CLIENT_ID"] = client
    t0 = time.time()
    print(f"\n\033[1m▶ {name}{' · ' + client if client else ''}\033[0m")
    r = subprocess.run([sys.executable, str(SRC / f"{name}.py")], cwd=ROOT, env=env)
    print(f"  ({time.time() - t0:.1f}s)")
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default=os.environ.get("CLIENT_ID", "ALBERT"))
    ap.add_argument("--all", action="store_true", help="todos os clientes do cadastro")
    ap.add_argument("--fetch", action="store_true", help="re-baixa dados de mercado")
    ap.add_argument("--no-llm", action="store_true", help="pula a geração da carta")
    a = ap.parse_args()

    roster = pd.read_csv(ROOT / "data" / "reference" / "clients.csv", dtype=str)["client_id"].tolist()
    clients = roster if a.all else [a.client]
    # Only clients with holdings have anything to compute. The others exist on
    # the roster so the interface can show the book this was designed for.
    held = set(pd.read_csv(ROOT / "data" / "reference" / "positions.csv",
                           dtype=str)["client_id"])
    skipped = [c for c in clients if c not in held]
    clients = [c for c in clients if c in held]
    if skipped:
        print(f"(sem posicoes, fora desta execucao: {', '.join(skipped)})")
    if not clients:
        print("Nenhum cliente com posicoes.")
        return 1

    def wanted(tags):
        return not (("fetch" in tags and not a.fetch) or ("llm" in tags and a.no_llm))

    shared = [(n, d) for n, d, t in STAGES if wanted(t) and "shared" in t]
    per_client = [(n, d) for n, d, t in STAGES if wanted(t) and "shared" not in t]

    print(f"Clientes: {', '.join(clients)}")
    for name, desc in shared:
        if run_stage(name) != 0:
            print(f"\n\033[1mFALHOU em {name} ({desc}).\033[0m")
            return 1
    NEEDS_LETTER = {"charts", "render_pdf"}
    for c in clients:
        has_letter = (ROOT / "output" / c / "letter_sections.json").exists()
        for name, desc in per_client:
            # A triage pass (--no-llm) over a client with no letter yet is a
            # normal state, not a failure: there is simply nothing to render.
            if name == "render_pdf" and a.no_llm:
                print(f"\n  (pulando {name}: gere uma nova carta para os dados atualizados)")
                continue
            if name in NEEDS_LETTER and a.no_llm and not has_letter:
                print(f"\n  (pulando {name} · {c}: nenhuma carta gerada ainda)")
                continue
            if run_stage(name, c) != 0:
                print(f"\n\033[1mFALHOU em {name} ({desc}) para {c}.\033[0m")
                return 1
    print("\n\033[1m✓ Pipeline concluído.\033[0m")
    for c in clients:
        pdf = ROOT / "output" / c / "letter.pdf"
        if pdf.exists():
            print(f"   {c:10} output/{c}/letter.pdf  ({pdf.stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
