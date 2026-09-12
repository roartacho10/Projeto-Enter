"""
Shared fixtures.

The pipeline fixture does something deliberate: instead of testing the working
directory, it copies out ONLY the files git would publish and runs there. So a
green suite also proves the repository is self-sufficient - no dependency on
the 230 MB of CVM bulk downloads, on data/processed, or on anything left over
from a previous run. The portability test and the test suite are the same act.

Nothing here touches the network, a language model or a browser.
"""
from __future__ import annotations
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Deterministic stages only: no download, no model, no browser.
STAGES = ["extract_macro", "extract_profile", "build_prices", "compute_metrics",
          "derive_policy", "recommend", "rebalance", "history", "figures", "charts"]
CLIENT = "ALBERT"


@pytest.fixture(scope="session")
def clean_repo(tmp_path_factory) -> Path:
    """A checkout containing exactly what git publishes."""
    from check_repo import ignored, rules
    dest = tmp_path_factory.mktemp("checkout")
    pats = rules()
    n = 0
    for f in sorted(ROOT.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(ROOT).as_posix()
        if rel.startswith(".git/") or ignored(rel, pats):
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest / rel)
        n += 1
    assert n > 30, f"copia suspeita: {n} arquivos"
    return dest


@pytest.fixture(scope="session")
def ran(clean_repo) -> Path:
    """Runs the deterministic stages in the clean checkout. Any non-zero exits."""
    env = dict(os.environ, CLIENT_ID=CLIENT)
    env.pop("PERIOD", None)
    for stage in STAGES:
        r = subprocess.run([sys.executable, str(clean_repo / "src" / f"{stage}.py")],
                           cwd=clean_repo, env=env, capture_output=True, text=True)
        assert r.returncode == 0, (
            f"{stage} falhou (exit {r.returncode}) no checkout limpo:\n"
            f"{r.stdout[-2500:]}\n{r.stderr[-2500:]}")
    return clean_repo


def stage_out(repo: Path, name: str):
    import json
    p = repo / "output" / CLIENT / name
    assert p.exists(), f"{name} nao foi produzido"
    return json.loads(p.read_text(encoding="utf-8"))
