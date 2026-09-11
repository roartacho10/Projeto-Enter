"""
L6 - Deterministic rendering.

The template is code, not model output: same data in, byte-identical layout out.
The model contributes prose only; every figure, table cell and chart comes from
the MetricsPack. Charts are inlined as SVG so the PDF has no external assets.

Requires: python -m pip install jinja2 playwright  &&  python -m playwright install chromium
Run: python src/render_pdf.py
"""
from __future__ import annotations
from pathlib import Path
import json
import sys
import base64
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import MetricsPack  # noqa: E402

from context import OUT, ROOT, client  # noqa: E402

TPL, ASSETS = ROOT / "templates", ROOT / "assets"
_c = client()


def data_uri(name: str) -> str:
    """Images are inlined so the PDF carries no external asset references."""
    p = ASSETS / name
    if not p.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def brmoney(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def brpct(v: float) -> str:
    return f"{v:.2f}".replace(".", ",") + "%"


def build_html(fit: float = 1.0, show_annex: bool = True) -> str:
    pack = MetricsPack.model_validate_json((OUT / "metrics_pack.json").read_text(encoding="utf-8"))
    figures = json.loads((OUT / "figures.json").read_text(encoding="utf-8"))
    letter = json.loads((OUT / "letter_sections.json").read_text(encoding="utf-8"))

    kpi_rows = [
        {"label": "Patrimônio total", "a": brmoney(pack.total_value_start),
         "b": brmoney(pack.total_value_end), "d": brpct(pack.total_return_pct)},
        {"label": "Recursos investidos", "a": brmoney(pack.invested_value_start),
         "b": brmoney(pack.invested_value_end), "d": brpct(pack.invested_return_pct)},
        {"label": "Saldo disponível (não remunerado)", "a": brmoney(pack.cash_value),
         "b": brmoney(pack.cash_value), "d": "—"},
        {"label": f"Parâmetro de referência ({pack.benchmark_name})", "a": "", "b": "",
         "d": brpct(pack.benchmark_return_pct)},
        {"label": "Resultado ante o parâmetro", "a": "", "b": "",
         "d": f"{pack.excess_return_pp:+.2f}".replace(".", ",") + " p.p.", "total": True},
    ]
    doc = {
        "kpi_note": f"No período: CDI {brpct(pack.cdi_return_pct)} · "
                    f"Ibovespa {brpct(pack.ibov_return_pct)}.",
        "kicker": "Relatório Mensal", "brand": "XP Investimentos",
        "period_label": "Abril", "year": "2025",
        "title": "Relatório mensal de investimentos",
        "subtitle": f"{_c['name']} · Perfil {_c['profile']} · Conta {_c['account']}",
        "advisor": _c["advisor"],
        "advisor_role": f"Assessor de investimentos · Código {_c['advisor_code']}",
        "date_start": "31/03/2025", "date_end": "30/04/2025", "run_id": pack.run_id,
    }
    charts = {n: (OUT / "charts" / f"{n}.svg").read_text(encoding="utf-8")
              for n in ("contribution", "allocation")}
    idle_path = OUT / "charts" / "contribution_idle.json"
    idle = json.loads(idle_path.read_text(encoding="utf-8")) if idle_path.exists() else []
    doc["idle_note"] = ("Sem contribuição: " + ", ".join(idle) + ".") if idle else ""

    ins = pd.read_csv(ROOT / "data" / "reference" / "instruments.csv",
                      dtype=str).fillna("").set_index("instrument_id")
    pos_rows = [
        {"name": ins.loc[m.instrument_id, "display_name"],
         "value": brmoney(m.value_end),
         "weight": brpct(m.weight_pct),
         "ret": brpct(m.return_pct) if abs(m.return_pct) > 0.004 else "—"}
        for m in sorted(pack.positions, key=lambda x: -x.value_end)]
    # Rebalance plan: sells name instruments, the purchase names a family.
    plan_p, trade_rows, plan_note = OUT / "rebalance_plan.json", [], ""
    if plan_p.exists():
        plan = json.loads(plan_p.read_text(encoding="utf-8"))
        verbo = {"sell": "Vender", "redeem": "Resgatar", "buy": "Aplicar"}
        for t_ in plan["trades"]:
            alvo = (ins.loc[t_["instrument_id"], "display_name"] if t_["instrument_id"]
                    else f"{t_['category']} — {t_['criteria']}")
            if t_.get("min_issuers"):
                alvo += f" (mín. {t_['min_issuers']} emissores)"
            trade_rows.append({"acao": verbo[t_["action"]], "alvo": alvo,
                               "valor": brmoney(t_["amount_brl"])})
        plan_note = (f"Plano dimensionado pelo motor de regras. Após estas operações, nenhuma "
                     f"das {len(plan['violations_before'])} violações de adequação permanece; "
                     f"o caixa fica em {brmoney(plan['cash_after_brl'])}.")

    half = (len(pos_rows) + 1) // 2
    pos_cols = [pos_rows[:half], pos_rows[half:]]
    assets = {"logo": data_uri("xp_logo.png"), "logo_white": data_uri("xp_logo_white.png")}
    env = Environment(loader=FileSystemLoader(TPL), autoescape=select_autoescape(["html"]))
    env.filters["safe_svg"] = lambda s: s
    tpl = env.get_template("letter.html")
    from markupsafe import Markup
    doc["fit"] = f"{fit:.3f}"
    return tpl.render(doc=doc, letter=letter, figures=figures, kpi_rows=kpi_rows,
                      pos_cols=pos_cols, assets=assets, show_annex=show_annex,
                      trade_rows=trade_rows, plan_note=plan_note,
                      charts={k: Markup(v) for k, v in charts.items()})


# Declared fit budget, tried in order. Shrinking a little is invisible to the
# reader; dropping the position annex is visible, so it comes last. Anything
# that does not fit inside this budget is a failure, not something to squeeze.
FIT_STEPS = [
    (1.000, True,  "layout cheio"),
    (0.970, True,  "reduzido 3%"),
    (0.940, True,  "reduzido 6%"),
    (0.940, False, "reduzido 6%, sem o anexo de posições"),
    (0.910, False, "reduzido 9%, sem o anexo de posições"),
]


def measure(page, html: str) -> list[dict]:
    page.set_content(html, wait_until="load")
    return page.evaluate("""() => [...document.querySelectorAll('.page')]
        .map((el, i) => ({page: i + 1,
                          over: Math.round(el.scrollHeight - el.clientHeight)}))
        .filter(x => x.over > 2)""")


if __name__ == "__main__":
    if not (OUT / "letter_sections.json").exists():
        sys.exit(f"nenhuma carta gerada para este cliente ainda "
                 f"({OUT / 'letter_sections.json'} nao existe). "
                 f"Rode generate_letter.py antes.")
    html = build_html()
    (OUT / "letter.html").write_text(html, encoding="utf-8")
    # The HTML is the source of truth; the PDF is a rendering of it. Where no
    # browser is available (a hosted environment, say) the letter still exists
    # and can be printed from the browser - but the layout gate cannot run, and
    # that is said out loud rather than passed over in silence.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("AVISO: playwright ausente - PDF nao gerado e portao de layout nao executado.")
        print(f"html -> {OUT / 'letter.html'}")
        sys.exit(0)
    try:
        pw = sync_playwright().start()
        pw.stop()
    except Exception as e:
        print(f"AVISO: navegador indisponivel ({type(e).__name__}) - PDF nao gerado "
              "e portao de layout nao executado. Rode: python -m playwright install chromium")
        print(f"html -> {OUT / 'letter.html'}")
        sys.exit(0)
    chosen, overflow = None, None
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        for fit, annex, label in FIT_STEPS:
            html = build_html(fit, annex)
            overflow = measure(pg, html)
            if not overflow:
                chosen = (fit, annex, label)
                break
            print(f"  tentativa '{label}': estouro de "
                  + ", ".join(f"{o['over']}px na pagina {o['page']}" for o in overflow))
        if chosen:
            (OUT / "letter.html").write_text(html, encoding="utf-8")
            pg.pdf(path=str(OUT / "letter.pdf"), format="A4", print_background=True,
                   margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        # Closing the page before the browser: tearing both down at once leaves
        # a pending connection task and Playwright prints a TargetClosedError
        # traceback over an otherwise successful run.
        pg.close()
        b.close()
    if not chosen:
        print("  [BLOCKER] o texto excede o orçamento de ajuste declarado.")
        print("  Encurte a carta (o gerador aceita um alvo de palavras) e rode de novo.")
        sys.exit("REPROVADO: conteudo nao cabe no layout de duas paginas")
    fit, annex, label = chosen
    print(f"html -> {OUT / 'letter.html'}")
    print(f"pdf  -> {OUT / 'letter.pdf'}")
    print(f"LAYOUT OK: duas paginas, ajuste aplicado = {label}")
    (OUT / "layout_report.json").write_text(
        json.dumps({"fit": fit, "annex": annex, "label": label}, ensure_ascii=False, indent=2),
        encoding="utf-8")
