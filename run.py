"""
One command runs the whole pipeline.

    python run.py                      # Albert, full run
    python run.py --client CARLOS      # another client
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
    ("fetch_market",    "Baixa preços e séries macro",     {"fetch", "shared"}),
    ("build_prices",    "Monta a tabela de preços",        {"shared"}),
    ("compute_metrics", "Calcula o MetricsPack",           set()),
    ("recommend",       "Aplica as regras de suitability", set()),
    ("rebalance",       "Dimensiona compras e vendas",     set()),
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

    clients = (pd.read_csv(ROOT / "data" / "reference" / "clients.csv", dtype=str)["client_id"]
               .tolist() if a.all else [a.client])

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
