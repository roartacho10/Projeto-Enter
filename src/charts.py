"""
L6a - Charts, drawn from the MetricsPack by code.

Design decisions worth defending:
 - Colour encodes nothing. Identity comes from the axis label, sign from the
   direction of the bar. The brand yellow failed a categorical-palette check
   (1.52:1 against white) so it is used for rules and the header band only,
   never for a mark the reader must interpret.
 - Every bar carries its own value label, so the chart is legible in print,
   in greyscale and to a colour-blind reader.

Run: python src/charts.py
"""
from __future__ import annotations
from pathlib import Path
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import MetricsPack  # noqa: E402

from context import OUT, REF  # noqa: E402

CHARTS = OUT / "charts"
CHARTS.mkdir(parents=True, exist_ok=True)

INK, MUTED, RULE = "#1A1A1A", "#6B6B6B", "#D9D9D9"


def brnum(v: float, nd: int = 1) -> str:
    return f"{v:,.{nd}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 7.4,
    "axes.edgecolor": RULE, "axes.labelcolor": INK,
    "text.color": INK, "xtick.color": MUTED, "ytick.color": INK,
    "svg.fonttype": "none",
})

BUCKETS = {
    "equity_direct": "Ações",
    "equity_fund": "Fundos de ações",
    "long_biased_fund": "Long biased",
    "multimarket_macro": "Multimercado",
    "credit_fund": "Crédito privado",
    "fidc_senior": "FIDC",
    "fixed_income_fund": "Renda fixa (fundo)",
    "fixed_income_bank": "CDB (vencido)",
    "cash": "Caixa disponível",
}


def frame(ax):
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.tick_params(length=0)
    ax.grid(False)


def allocation_chart(pack: MetricsPack, ins: pd.DataFrame, cats: pd.DataFrame) -> Path:
    """
    Part-to-whole, so a stacked bar rather than a pie: it is the recommended form
    for this job and, unlike a pie, small shares stay readable. Nine risk
    categories are folded into the four buckets the mandate is actually discussed
    in - above roughly seven classes a chart stops being readable regardless of
    type. Shade is ordered, and the idle-capital bucket carries the darkest ink
    because it is the point of the letter; colour is not doing identity work, the
    labels are.
    """
    rows = []
    for m in pack.positions:
        cat = ins.loc[m.instrument_id, "risk_category"]
        rows.append((cats.loc[cat, "letter_bucket"], m.value_end))
    df = (pd.DataFrame(rows, columns=["bucket", "v"]).groupby("bucket", as_index=False)
          .sum().sort_values("v", ascending=False))
    total = df["v"].sum()
    shade = {"Capital ocioso": "#1A1A1A", "Renda variável": "#6E6E6E",
             "Crédito privado e renda fixa": "#A8A8A8", "Multimercado": "#CFCFCF"}

    fig, ax = plt.subplots(figsize=(6.0, 1.44), dpi=200)
    left = 0.0
    for _, r in df.iterrows():
        share = r["v"] / total * 100
        ax.barh([0], [r["v"]], left=left, height=0.42,
                color=shade.get(r["bucket"], "#8A8A8A"),
                edgecolor="white", linewidth=1.6)
        if share >= 8:
            ax.text(left + r["v"] / 2, 0, f"{brnum(share)}%", va="center", ha="center",
                    fontsize=8, color="white" if r["bucket"] in
                    ("Capital ocioso", "Renda variável") else INK, fontweight="bold")
        left += r["v"]

    # Legend on two rows of two: the longest bucket name does not fit four
    # across at this width. Identity is never colour-only.
    for i, (_, r) in enumerate(df.iterrows()):
        share = r["v"] / total * 100
        x = 0.0 if i % 2 == 0 else total * 0.50
        y = -0.58 if i < 2 else -1.06
        ax.add_patch(plt.Rectangle((x, y - 0.05), total * 0.012, 0.10,
                                   color=shade.get(r["bucket"], "#8A8A8A"), clip_on=False))
        ax.text(x + total * 0.021, y, r["bucket"], va="center", ha="left",
                fontsize=7.2, color=INK)
        ax.text(x + total * 0.021, y - 0.22, f"{brnum(share)}%  ·  R$ {brnum(r['v'], 0)}",
                va="center", ha="left", fontsize=7.2, color=MUTED)

    ax.set_xlim(0, total); ax.set_ylim(-1.40, 0.35)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.grid(False)
    fig.tight_layout(pad=0.2)
    p_ = CHARTS / "allocation.svg"; fig.savefig(p_, format="svg", transparent=True); plt.close(fig)
    return p_


def contribution_chart(pack: MetricsPack, ins: pd.DataFrame) -> tuple[Path, list[str]]:
    """
    Positions that contributed nothing are named in the caption rather than
    drawn as empty bars: it says the same thing in less space and reads better.
    """
    rows = [(ins.loc[m.instrument_id, "display_name"], m.contribution_pp)
            for m in pack.positions]
    df = pd.DataFrame(rows, columns=["name", "pp"])
    idle = sorted(df.loc[df["pp"].abs() < 0.005, "name"].tolist())
    df = df[df["pp"].abs() >= 0.005].sort_values("pp")
    # Height follows the number of bars: dropping rows must shrink the figure,
    # otherwise the saved space never reaches the page.
    fig, ax = plt.subplots(figsize=(6.0, 0.145 * len(df) + 0.22), dpi=200)
    ax.barh(df["name"], df["pp"], height=0.66, color=INK)
    span = max(abs(df["pp"].min()), df["pp"].max()) or 1
    for y, v in zip(df["name"], df["pp"]):
        off = span * 0.02 if v >= 0 else -span * 0.02
        ax.text(v + off, y, ("+" if v >= 0 else "") + brnum(v, 2) + " p.p.",
                va="center", ha="left" if v >= 0 else "right", fontsize=7,
                color=INK if abs(v) > 0.001 else MUTED)
    ax.axvline(0, color=RULE, lw=1)
    ax.set_xlim(-span * 0.30, span * 1.34)
    ax.set_xticks([]); frame(ax)
    ax.spines["bottom"].set_visible(False)
    fig.tight_layout(pad=0.4)
    p = CHARTS / "contribution.svg"; fig.savefig(p, format="svg", transparent=True); plt.close(fig)
    (CHARTS / "contribution_idle.json").write_text(__import__("json").dumps(idle, ensure_ascii=False),
                                                   encoding="utf-8")
    return p, idle


if __name__ == "__main__":
    pack = MetricsPack.model_validate_json((OUT / "metrics_pack.json").read_text(encoding="utf-8"))
    ins = pd.read_csv(REF / "instruments.csv", dtype=str).fillna("").set_index("instrument_id")
    cats = pd.read_csv(REF / "risk_categories.csv", dtype=str).fillna("").set_index("risk_category")
    print("ok", allocation_chart(pack, ins, cats))
    p, idle = contribution_chart(pack, ins)
    print("ok", p)
    print("   sem contribuicao:", ", ".join(idle) or "nenhuma")
