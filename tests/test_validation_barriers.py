"""Adversarial checks of the published validation guarantees."""
import csv
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal

import pytest
import verify


@pytest.mark.parametrize("token", ["12.345,67", "1234567,89", "4567", "456789", "-4,07%", "−4,07%"])
def test_unapproved_numeric_encodings_are_blocked(monkeypatch, token):
    monkeypatch.setattr(verify, "allowed_strings", lambda: {"4,07", "25,00", "-3,03"})
    monkeypatch.setattr(verify, "check_claims", lambda _: ([], dict(checked=0, sentences=1, skipped=0)))
    issues = verify.verify(f"Albert, o valor foi {token}.")
    assert any(i[1] == "ungrounded_number" for i in issues)


@pytest.mark.parametrize("token", ["+4,070%", "4,07%", "−3,030%", "-3,03%", "25,0%", "1.234,50", "1234,500"])
def test_approved_signed_and_equivalent_formats_pass(monkeypatch, token):
    monkeypatch.setattr(verify, "allowed_strings", lambda: {"4,07", "25,00", "-3,03", "1.234,50"})
    monkeypatch.setattr(verify, "check_claims", lambda _: ([], dict(checked=0, sentences=1, skipped=0)))
    assert not any(i[1] == "ungrounded_number" for i in verify.verify(f"Albert, o valor foi {token}."))


@pytest.mark.parametrize("row,column", [
    ("LREN3", "value_start"), ("LREN3", "value_end"), ("LREN3", "return_pct"),
    ("LREN3", "contribution_pp"), ("__TOTAL_PATRIMONIO__", "value_end"),
    ("__TOTAL_INVESTIDO__", "value_end"), ("FUND_BRAVE_I", "value_end"),
    ("LREN3", "id"),
])
def test_corrupted_key_stops_stage_without_publishing(ran, tmp_path, row, column):
    repo = tmp_path / "repo"
    shutil.copytree(ran, repo)
    path = repo / "data/reference/golden_portfolio_2025-04.csv"
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f); fields = reader.fieldnames; rows = list(reader)
    next(r for r in rows if r["id"] == row)[column] = "999999.99"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    output = repo / "output/ALBERT/metrics_pack.json"
    original = output.read_bytes()
    result = subprocess.run([sys.executable, str(repo / "src/compute_metrics.py")],
                            cwd=repo, env={**os.environ, "CLIENT_ID": "ALBERT"},
                            capture_output=True, text=True, timeout=120)
    assert result.returncode != 0, result.stdout
    assert "VALIDACAO FALHOU" in result.stdout
    assert output.read_bytes() == original


def test_brave_matches_source_fields_and_closing_total(ran):
    ref = ran / "data/reference"
    rows = list(csv.DictReader((ref / "brave_cvm_reconciliation.csv").open(encoding="utf-8")))
    quotas = [Decimal(r["net_asset_value"]) / Decimal(r["quota_count"]) for r in rows]
    key = list(csv.DictReader((ref / "golden_quotas.csv").open(encoding="utf-8")))
    for row, quota in zip(rows, quotas):
        expected = next(r for r in key if r["instrument_id"] == "FUND_BRAVE_I" and r["date"] == row["date"])
        assert abs(Decimal(expected["quota"]) / quota - 1) < Decimal("1e-12")
    pack = json.loads((ran / "output/ALBERT/metrics_pack.json").read_text())
    brave = next(p for p in pack["positions"] if p["instrument_id"] == "FUND_BRAVE_I")
    assert Decimal(str(brave["value_end"])) == (Decimal("72567.43") * quotas[1] / quotas[0]).quantize(Decimal("0.01"))
    assert pack["total_value_end"] == 410705.27
    assert sum(Decimal(str(p["value_end"])) for p in pack["positions"]) == Decimal("410705.27")


def test_equity_ceiling_uses_total_wealth(ran):
    out = ran / "output/ALBERT"
    rec = json.loads((out / "recommendations.json").read_text())
    assert rec["equity_lookthrough_pct"] == 24.15
    assert "MAX_EQUITY_LOOKTHROUGH_PCT" not in [f["rule_id"] for f in rec["recommendations"]]
