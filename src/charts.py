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


def trajectory_chart(hist: dict) -> Path | None:
    """
    Ten positions is far past the point where colour can carry identity, so it
    does not try: one small panel per position, the name above it, a single ink
    line inside. The CDI sits in every panel as a dashed grey reference, which
    is the comparison an advisor actually makes.

    Every panel spans the whole window even when the series does not. A holding
    with six months of published history draws a short line on a wide axis, and
    the empty stretch is the honest reading - far better than a line pulled
    across months nobody published. The coverage is also written out, because a
    reader should not have to infer it from the shape.
    """
    months = [d.strftime("%Y-%m") for d in
              pd.period_range(hist["window_start"], hist["window_end"], freq="M").to_timestamp()]
    xof = {m: i for i, m in enumerate(months)}
    pos = [s for s in hist["series"] if s["kind"] == "position"]
    if not pos:
        return None
    pos.sort(key=lambda s: -s["total_return_pct"])
    cdi = next((s for s in hist["series"] if s["instrument_id"] == "MACRO_CDI"), None)

    ncol = 3
    nrow = -(-len(pos) // ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(6.6, 1.34 * nrow + 0.30), dpi=200,
                             sharex=True, sharey=False, squeeze=False)
    axes = axes.ravel()

    # One holding ran +232% while the funds moved 25-30%. On a shared axis that
    # single series flattens every other panel into a straight line, so each
    # panel gets its own vertical scale - and MIN_SPAN stops the reverse error,
    # where a fund oscillating one percent is drawn as a mountain range.
    MIN_SPAN = 25.0

    def ref(series) -> list[tuple[int, float]]:
        """
        The CDI restricted to the months this holding actually covers, and
        rebased to 100 at its first one. Drawing the full-window CDI next to a
        six-month series would compare two different periods - the reader would
        see the Brave FIDC "losing" to a CDI measured over nineteen months it
        was never in.
        """
        if not cdi:
            return []
        span = {pt["month"] for pt in series["points"]}
        pts = [pt for pt in cdi["points"] if pt["month"] in span]
        if len(pts) < 2:
            return []
        base = pts[0]["index"]
        return [(xof[pt["month"]], pt["index"] / base * 100) for pt in pts]

    def limits(series) -> tuple[float, float]:
        vals = [pt["index"] for pt in series["points"]] + [100.0]
        vals += [v for _, v in ref(series)]
        lo_, hi_ = min(vals), max(vals)
        if hi_ - lo_ < MIN_SPAN:
            mid = (hi_ + lo_) / 2
            lo_, hi_ = mid - MIN_SPAN / 2, mid + MIN_SPAN / 2
        pad_ = (hi_ - lo_) * 0.20
        return lo_ - pad_, hi_ + pad_

    for i, (ax, s) in enumerate(zip(axes, pos)):
        r = ref(s)
        if r:
            ax.plot([x for x, _ in r], [v for _, v in r],
                    color=RULE, lw=1.0, ls=(0, (3, 2)), zorder=1)
        xs = [xof[p["month"]] for p in s["points"]]
        ys = [p["index"] for p in s["points"]]
        ax.plot(xs, ys, color=INK, lw=1.3, zorder=3,
                marker="o" if len(xs) < 7 else None, ms=2.0)
        ax.axhline(100, color="#ECECEC", lw=0.7, zorder=0)
        ax.set_title(s["display_name"][:27], fontsize=7.2, loc="left", pad=4)
        cov = "" if s["complete"] else f"{s['months_covered']}/{len(months)} meses"
        if cov:
            ax.text(1.0, 1.03, cov, transform=ax.transAxes, ha="right", va="bottom",
                    fontsize=6.2, color=MUTED)
        ylo, yhi = limits(s)
        ax.set_ylim(ylo, yhi)
        # Park the figure wherever the line is not: high if the series ends low,
        # low if it ends high. A label under the last point is unreadable.
        ends_high = (ys[-1] - ylo) / (yhi - ylo) > 0.5
        ax.text(0.98, 0.06 if ends_high else 0.80,
                ("+" if s["total_return_pct"] >= 0 else "")
                + brnum(s["total_return_pct"], 1) + "%",
                transform=ax.transAxes, ha="right", va="bottom",
                fontsize=7.4, fontweight="bold", color=INK,
                bbox=dict(boxstyle="square,pad=0.22", fc="white", ec="none"))
        frame(ax)
        ax.set_yticks([])

    for ax in axes[len(pos):]:
        ax.set_visible(False)

    axes[0].set_xlim(-0.6, len(months) - 0.4)
    ticks = sorted({0, len(months) // 2, len(months) - 1})
    labels = [months[t] for t in ticks]
    # sharex hides labels off the bottom row; put them back on the last panel
    # actually drawn in each column, otherwise most panels lose their axis.
    for col in range(ncol):
        last = max((i for i in range(len(pos)) if i % ncol == col), default=None)
        if last is None:
            continue
        axes[last].tick_params(labelbottom=True)
        axes[last].set_xticks(ticks)
        axes[last].set_xticklabels(labels, fontsize=6.2)

    fig.suptitle("Base 100 no primeiro mês de cada série · tracejado: CDI no mesmo período · "
                 "escala vertical própria em cada painel",
                 fontsize=6.6, color=MUTED, y=0.997, x=0.010, ha="left")
    fig.tight_layout(pad=0.35, h_pad=1.5, w_pad=1.0, rect=(0, 0, 1, 0.975))
    p_ = CHARTS / "trajectory.svg"
    fig.savefig(p_, format="svg", transparent=True); plt.close(fig)
    return p_


if __name__ == "__main__":
    pack = MetricsPack.model_validate_json((OUT / "metrics_pack.json").read_text(encoding="utf-8"))
    ins = pd.read_csv(REF / "instruments.csv", dtype=str).fillna("").set_index("instrument_id")
    cats = pd.read_csv(REF / "risk_categories.csv", dtype=str).fillna("").set_index("risk_category")
    print("ok", allocation_chart(pack, ins, cats))
    p, idle = contribution_chart(pack, ins)
    print("ok", p)
    print("   sem contribuicao:", ", ".join(idle) or "nenhuma")
    hp = OUT / "history.json"
    if hp.exists():
        import json
        t = trajectory_chart(json.loads(hp.read_text(encoding="utf-8")))
        print("ok", t) if t else print("   sem trajetoria para desenhar")
    else:
        print("   (sem history.json: rode src/history.py para o grafico de trajetoria)")
