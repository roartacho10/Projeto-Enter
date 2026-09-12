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
import os
import sys
import base64
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import MetricsPack  # noqa: E402

from context import (OUT, ROOT, client, period_id, period_bounds,  # noqa: E402
                     period_label, br_date)

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


PERIOD = period_id()
_start, _end = period_bounds(PERIOD)
_label, _year = period_label(PERIOD)


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
        "period_label": _label, "year": _year,
        "title": "Relatório mensal de investimentos",
        "subtitle": f"{_c['name']} · Perfil {_c['profile']} · Conta {_c['account']}",
        "advisor": _c["advisor"],
        "advisor_role": f"Assessor de investimentos · Código {_c['advisor_code']}",
        "date_start": br_date(_start), "date_end": br_date(_end), "run_id": pack.run_id,
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
        # Client-facing wording. The mechanism behind the plan - the rules
        # engine, the count of breaches it cleared - belongs to the audit
        # trail in rebalance_plan.json, not to the person reading the letter.
        plan_note = (f"Executadas as operações acima, a carteira volta aos limites do seu "
                     f"perfil e o saldo em conta fica em {brmoney(plan['cash_after_brl'])}.")

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
    """
    Two ways the layout can fail, and the second one is invisible to the first.

    `over` is content taller than the page. But the signature and the
    disclaimer sit in an absolutely positioned footer, which does not grow
    scrollHeight - so prose can slide silently underneath it and the page still
    measures as fitting. `under` catches that by comparing the bottom of the
    last flowed element against the top of the footer.
    """
    page.set_content(html, wait_until="load")
    return page.evaluate("""() => [...document.querySelectorAll('.page')]
        .map((el, i) => {
          const over = Math.round(el.scrollHeight - el.clientHeight);
          let under = 0;
          const foot = el.querySelector('.pagefoot');
          const fit = el.querySelector('.fit');
          if (foot && fit) {
            const flow = [...fit.children].filter(c => c !== foot);
            if (flow.length) {
              const last = flow[flow.length - 1].getBoundingClientRect();
              under = Math.round(last.bottom - foot.getBoundingClientRect().top);
            }
          }
          return {page: i + 1, over, under};
        })
        .filter(x => x.over > 2 || x.under > 2)""")


if __name__ == "__main__":
    if not (OUT / "letter_sections.json").exists():
        sys.exit(f"nenhuma carta gerada para este cliente ainda "
                 f"({OUT / 'letter_sections.json'} nao existe). "
                 f"Rode generate_letter.py antes.")
    html = build_html()
    (OUT / "letter.html").write_text(html, encoding="utf-8")
    # The HTML is the source of truth; the PDF is a rendering of it. Three ways
    # to get a browser, tried in order: Playwright's own download, a browser
    # installed by the system (which is how this works on a host that forbids
    # the download - see packages.txt), or none, in which case the letter still
    # exists as HTML and the run says the layout gate did not execute.
    SYSTEM_BROWSERS = [
        "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable", os.environ.get("CHROME_PATH", ""),
    ]

    def launch(pw):
        try:
            return pw.chromium.launch(), "navegador do playwright"
        except Exception:
            pass
        for exe in SYSTEM_BROWSERS:
            if exe and Path(exe).exists():
                try:
                    return pw.chromium.launch(executable_path=exe), f"navegador do sistema ({exe})"
                except Exception:
                    continue
        return None, None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("AVISO: playwright ausente - PDF nao gerado e portao de layout nao executado.")
        print(f"html -> {OUT / 'letter.html'}")
        (OUT / "letter.html").write_text(build_html(), encoding="utf-8")
        sys.exit(0)

    chosen, overflow = None, None
    with sync_playwright() as p:
        b, how = launch(p)
        if b is None:
            (OUT / "letter.html").write_text(build_html(), encoding="utf-8")
            print("AVISO: nenhum navegador disponivel - PDF nao gerado e portao de "
                  "layout nao executado. Instale um Chromium do sistema (packages.txt) "
                  "ou rode: python -m playwright install chromium")
            print(f"html -> {OUT / 'letter.html'}")
            sys.exit(0)
        print(f"  usando {how}")
        pg = b.new_page()
        for fit, annex, label in FIT_STEPS:
            html = build_html(fit, annex)
            overflow = measure(pg, html)
            if not overflow:
                chosen = (fit, annex, label)
                break
            print(f"  tentativa '{label}': estouro de "
                  + ", ".join(
                      f"{max(o['over'], o['under'])}px na pagina {o['page']}"
                      + (" (invade o rodape)" if o["under"] > o["over"] else "")
                      for o in overflow))
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
