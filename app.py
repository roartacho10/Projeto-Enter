"""
Advisor front end.

Deliberately thin: it runs the same stages run.py runs and shows what they
produced. No calculation happens here, so the screen can never disagree with the
MetricsPack. Execution logs are kept - an auditable pipeline should not hide its
work - but folded away, because the person using this is an advisor with a
morning of client calls, not the engineer who built it.

Hostable by design: the API key comes from the form, nothing depends on the bulk
CVM downloads at run time, and the PDF step degrades to HTML where no browser is
available.

    streamlit run app.py
"""
from __future__ import annotations
from pathlib import Path
import base64
import json
import os
import subprocess
import sys
from src.allocation import MODEL_VERSION, fingerprint
from src.context import br_date, period_bounds, period_label
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

ROOT = Path(__file__).resolve().parent
SRC, OUT, REF, ASSETS = ROOT / "src", ROOT / "output", ROOT / "data" / "reference", ROOT / "assets"

TRIAGE = [("build_prices", "Atualizando preços"), ("compute_metrics", "Calculando resultados"),
          ("derive_policy", "Derivando os limites do perfil"),
          ("recommend", "Aplicando regras de adequação"),
          ("rebalance", "Dimensionando ajustes"), ("figures", "Preparando os números")]
LETTER = [("generate_letter", "Redigindo e verificando"), ("charts", "Desenhando gráficos"),
          ("render_pdf", "Montando o documento")]
RENDER = [("charts", "Desenhando gráficos"), ("render_pdf", "Montando o documento")]
ACTIVE = os.environ.get("DEMO_CLIENT", "ALBERT")   # the one case this build runs
DEFAULT_MODEL = "gpt-4.1"
MODELS = ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"]

st.set_page_config(page_title="Relatórios mensais · XP", page_icon="📄", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown("""
<style>
  /* Chrome removal, measured in a real browser rather than guessed.
     stHeader is a 60px opaque strip: made transparent and zero-height, or it
     reads as a white band above the page. The toolbar inside it is hidden -
     but stExpandSidebarButton, the control that brings a collapsed sidebar
     back, is a CHILD of that toolbar and inherits the hidden visibility. Two
     earlier attempts failed exactly there, so it is un-hidden explicitly. */
  #MainMenu, footer {visibility: hidden;}
  [data-testid="stHeader"] {background: transparent;}   /* transparente, NAO height:0 -
     o header e um overlay e nao empurra conteudo; zera-lo levava o botao de
     expandir para top=-14px, cortado pela metade. Medido: 16px com esta regra,
     a mesma altura do botao de recolher quando a lateral esta aberta. */
  [data-testid="stToolbar"] {visibility: hidden; height: 0;}
  [data-testid="stExpandSidebarButton"],
  [data-testid="stExpandSidebarButton"] * {visibility: visible !important;}
  .block-container {padding-top: 1.2rem; max-width: 1150px;}
  .band {background:#231F20; margin:0 0 1.4rem; padding:1.1rem 1.6rem; border-radius:6px;
         display:flex; align-items:center; justify-content:space-between;}
  .band .eyebrow {color:#FFC700; font-size:.95rem; margin:0 0 .15rem;}
  .band h1 {color:#fff; font-size:1.55rem; font-weight:400; margin:0; line-height:1.2;}
  .band .sub {color:#B9B9B9; font-size:.82rem; margin:.3rem 0 0;}
  .band img {height:44px;}
  .card {border:1px solid #E4E4E2; border-top:3px solid #E4E4E2; border-radius:4px;
         background:#FCFCFB; padding:.7rem .8rem .75rem; min-height:106px;
         margin-bottom:.45rem;}
  .card.sel {background:#fff; border-color:#DCDCDA; border-top-color:#FFC700;
             box-shadow:0 1px 5px rgba(0,0,0,.07);}
  .card.ghost {background:#FAFAF9; border-color:#EFEFED; border-top-color:#EFEFED;
               opacity:.45; filter:grayscale(1); user-select:none;}
  .card .nm {font-weight:600; font-size:.95rem; margin:0 0 .1rem; line-height:1.25;}
  .card .pf {color:#7A7A7A; font-size:.78rem; margin:0 0 .55rem;}
  .ghostnote {font-size:.7rem; letter-spacing:.06em; text-transform:uppercase;
              color:#B0B0AE; margin:.1rem 0 0; text-align:center;}
  .ghostnote.sel {color:#8A7A36; font-weight:600;}
  .chip {display:inline-block; padding:.12rem .55rem; border-radius:999px;
         font-size:.74rem; font-weight:600; white-space:nowrap;}
  .chip.ok   {background:#E8F3EC; color:#1E6B3A;}
  .chip.warn {background:#FDF3D0; color:#7A5B00;}
  .chip.crit {background:#FBE9E7; color:#8C2F1E;}
  .chip.none {background:#F0F0EE; color:#6B6B6B;}
  .sectitle {font-size:1.05rem; font-weight:700; margin:1.6rem 0 .4rem;
             padding-bottom:.35rem; border-bottom:1px solid #E4E4E2;}
  .finding {border-left:3px solid #FFC700; background:#FCFCFB; padding:.5rem .8rem;
            margin:.35rem 0; font-size:.88rem;}
  .finding b {display:block;}
  .finding span {color:#6B6B6B; font-size:.8rem;}
  .state {display:grid; grid-template-columns:1fr 1fr; gap:0 1.8rem; margin:.2rem 0 .4rem;}
  .state.one {grid-template-columns:1fr;}
  .state .r {display:flex; justify-content:space-between; align-items:baseline; gap:1rem;
             border-bottom:1px solid #F1F1EF; padding:.3rem .1rem; font-size:.86rem;}
  .state .r .k {color:#5A5A5A;}
  .state .r .v {white-space:nowrap; color:#5A5A5A;}
  .state .r .v em {font-style:normal; font-weight:700; color:#1A1A1A;}
  .chart {margin:.5rem 0 .3rem;}
  .chart svg {width:100%; height:auto; max-width:900px;}
  div[data-testid="stMetricValue"] {font-size:1.35rem;}
</style>
""", unsafe_allow_html=True)


def logo_uri() -> str:
    p = ASSETS / "xp_logo_white.png"
    return ("data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()) if p.exists() else ""


def brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


@st.cache_data(show_spinner=False)
def clients() -> pd.DataFrame:
    return pd.read_csv(REF / "clients.csv", dtype=str).fillna("")


def secret(name: str) -> str:
    """st.secrets first, then the environment. Neither is ever committed."""
    try:
        v = st.secrets.get(name, "")
    except Exception:          # no secrets file at all - normal off Cloud
        v = ""
    return str(v or os.environ.get(name, "")).strip()


def ambient_key() -> tuple[str, str]:
    """The key already configured for this deployment, and where it came from."""
    try:
        if st.secrets.get("OPENAI_API_KEY", ""):
            return str(st.secrets["OPENAI_API_KEY"]).strip(), "secrets do app"
    except Exception:
        pass
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return os.environ["OPENAI_API_KEY"].strip(), "variável de ambiente"
    return "", ""


@st.cache_data(show_spinner=False)
def instruments() -> pd.DataFrame:
    return pd.read_csv(REF / "instruments.csv", dtype=str).fillna("").set_index("instrument_id")


def limits(cid: str) -> dict[str, str]:
    """Thresholds in force for this client, from the policy derive_policy wrote."""
    p = OUT / cid / "suitability_policy.csv"
    if not p.exists():
        p = REF / "suitability_policy.csv"
    if not p.exists():
        return {}
    d = pd.read_csv(p, dtype=str).fillna("")
    return dict(zip(d["rule_id"], d["threshold"]))


def pct(v) -> str:
    """Two decimals, like figures.py. One decimal here and two in the letter
    meant the same measure was rounded differently on screen and on paper."""
    return f"{float(v):.2f}%".replace(".", ",")


def pp(v) -> str:
    """Two decimals: contributions run to hundredths, and 0,04 rounded to one
    decimal reads as 0,0 - which says the position did nothing."""
    return f"{float(v):+.2f}".replace(".", ",") + " p.p."


def load(cid: str, name: str):
    p = OUT / cid / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# Every pack field this screen reads. A run saved by an earlier build lacks
# some of them - a deployed container keeps output/ across a code update - and
# the app must offer to rebuild rather than crash. This was a literal set
# buried in the detail section, so the client queue, which runs first and
# reads the pack too, still died on a stale file. One list, checked once.
PACK_FIELDS = {"total_value_end", "total_return_pct", "cash_pct_of_total_end",
               "invested_return_pct", "cdi_return_pct", "ipca_return_pct",
               "excess_over_cdi_pp", "real_return_total_pct",
               "coverage_pct", "ibov_return_pct", "positions"}


def current_pack(cid: str):
    """The saved MetricsPack when this build can read it, otherwise None."""
    pack = load(cid, "metrics_pack.json")
    return pack if pack and PACK_FIELDS.issubset(pack) else None


def run(stages, client=None, key=None, model=None) -> tuple[str | None, str]:
    """Runs the stages, returning the first failure and the full transcript."""
    env = dict(os.environ)
    if client:
        env["CLIENT_ID"] = client
    if key:
        env["OPENAI_API_KEY"] = key
    if model:
        env["MODEL_LETTER"] = model
    transcript = []
    with st.status("Processando...", expanded=False) as status:
        for name, desc in stages:
            status.update(label=desc)
            r = subprocess.run([sys.executable, str(SRC / f"{name}.py")], cwd=ROOT,
                               capture_output=True, text=True, env=env)
            transcript.append(f"$ {name}.py\n{(r.stdout or '') + (r.stderr or '')}")
            if r.returncode != 0:
                status.update(label=f"Interrompido em {desc.lower()}", state="error")
                return name, "\n".join(transcript)
        status.update(label="Concluído", state="complete")
    return None, "\n".join(transcript)


def chip(n) -> str:
    if n is None:
        return '<span class="chip none">sem dados</span>'
    if n == 0:
        return '<span class="chip ok">em ordem</span>'
    if n <= 3:
        return f'<span class="chip warn">{n} pontos de atenção</span>'
    return f'<span class="chip crit">{n} pontos de atenção</span>'


def queue() -> pd.DataFrame:
    """One row per client. Only the active one is priced - see the note below."""
    cols = ["Cliente", "_id", "Perfil", "Patrimônio", "Retorno", "Caixa", "Achados", "Carta"]
    rows = []
    for _, c in clients().iterrows():
        cid = c["client_id"]
        row = dict.fromkeys(cols)
        row |= {"Cliente": c["name"], "_id": cid, "Perfil": c["profile"], "Carta": False}
        if cid == ACTIVE:
            row["Carta"] = ((OUT / cid / "letter.pdf").exists()
                            or (OUT / cid / "letter.html").exists())
            pack, rec = current_pack(cid), load(cid, "recommendations.json")
            if pack:
                row |= {"Patrimônio": pack["total_value_end"],
                        "Retorno": pack["total_return_pct"],
                        "Caixa": pack["cash_pct_of_total_end"],
                        "Achados": len(rec["recommendations"]) if rec else 0}
        rows.append(row)
    df = pd.DataFrame(rows, columns=cols)
    df["_active"] = df["_id"] == ACTIVE
    return df.sort_values(["_active", "Cliente"], ascending=[False, True])


# ---------------------------------------------------------------- header
# Advisor, code and reference month come from clients.csv, the same row
# render_pdf reads. They were written into this file by hand, so changing the
# month in the registry made the screen contradict the letter it produced.
_crow = clients().set_index("client_id").loc[ACTIVE]
_period = str(_crow["period"]).strip()
_mes, _ano = period_label(_period)
_ini, _fim = period_bounds(_period)

st.markdown(f"""
<div class="band">
  <div>
    <p class="eyebrow">XP Investimentos</p>
    <h1>Relatórios mensais</h1>
    <p class="sub">{_crow["advisor"]} · Código {_crow["advisor_code"]} · Período de
       referência: {_mes.lower()} de {_ano}</p>
  </div>
  <img src="{logo_uri()}" alt="XP">
</div>""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("#### Configuração")
    key, key_src = ambient_key()
    if key:
        st.caption(f"Chave da OpenAI: configurada ({key_src}).")
        with st.expander("Usar outra chave"):
            override = st.text_input("Chave da OpenAI", type="password", placeholder="sk-...",
                                     label_visibility="collapsed", key="key_override",
                                     help="Vale só para esta sessão do navegador.")
            key = override or key
    else:
        key = st.text_input("Chave da OpenAI", type="password", placeholder="sk-...",
                            key="key_sidebar",
                            help="Para não precisar digitar de novo, configure "
                                 "OPENAI_API_KEY no ambiente ou em .streamlit/secrets.toml.")

    model = secret("MODEL_LETTER") or DEFAULT_MODEL
    st.caption(f"Modelo de redação: **{model}**")
    with st.expander("Trocar modelo"):
        opts = [model] + [m for m in MODELS if m != model]
        model = st.selectbox("Modelo de redação", opts, index=0, label_visibility="collapsed")

    st.divider()
    st.caption("Os números são calculados em código. O modelo apenas redige, e nenhuma "
               "cifra chega à carta sem constar da lista verificada.")

q = queue()
active = q[q["_active"]].iloc[0]

# ---------------------------------------------------------------- client menu
h = st.columns([3, 1])
h[0].markdown('<p class="sectitle">Carteira de clientes</p>', unsafe_allow_html=True)
with h[1]:
    st.write("")
    if st.button("Atualizar dados", width="stretch"):
        failed, log = run(TRIAGE, ACTIVE)
        st.session_state["log"] = log
        if failed:
            st.error(f"Falhou em {failed}. Veja os detalhes técnicos ao final.")
            st.stop()
        st.cache_data.clear()
        st.rerun()

st.caption("A plataforma foi desenhada para a carteira inteira do assessor — a triagem, "
           "as regras e a carta rodam por cliente. Esta demonstração processa apenas o caso "
           f"de {active['Cliente'].split()[0]}; os demais aparecem para mostrar a escala "
           "pretendida.")

cards = st.columns(len(q))
for col, (_, r) in zip(cards, q.iterrows()):
    with col:
        if not r["_active"]:
            st.markdown(f'''<div class="card ghost">
  <p class="nm">{r["Cliente"]}</p>
  <p class="pf">{r["Perfil"]}</p>
  <span class="chip none">não processado</span>
</div>
<p class="ghostnote">fora desta demonstração</p>''', unsafe_allow_html=True)
            continue
        achados = int(r["Achados"]) if pd.notna(r["Achados"]) else None
        valor = brl(r["Patrimônio"]) if pd.notna(r["Patrimônio"]) else "—"
        st.markdown(f'''<div class="card sel">
  <p class="nm">{r["Cliente"]}</p>
  <p class="pf">{r["Perfil"]} · {valor}</p>
  {chip(achados)}
</div>
<p class="ghostnote sel">em análise</p>''', unsafe_allow_html=True)

# ---------------------------------------------------------------- detail
cid = ACTIVE
name = active["Cliente"]
st.markdown(f'<p class="sectitle">{name}</p>', unsafe_allow_html=True)

pack, rec = current_pack(cid), load(cid, "recommendations.json")
plan, rep = load(cid, "rebalance_plan.json"), load(cid, "verification_report.json")
if pack is None and load(cid, "metrics_pack.json"):
    st.info("Os cálculos salvos usam um formato antigo. Use Atualizar dados para reconstruí-los.")
allocation = load(cid, "allocation_target.json")
current_plan = bool(pack and allocation and plan and plan.get("model_version") == MODEL_VERSION
                    and allocation.get("metrics_run_id") == pack.get("run_id")
                    and plan.get("allocation_fingerprint") == allocation.get("fingerprint"))
if not current_plan:
    plan, rec, allocation = None, None, None
    if pack:
        st.info("Use Atualizar dados para calcular a alocação com o cenário macroeconômico.")

if not pack:
    st.info("Cliente ainda não processado. Use **Atualizar** acima.")
else:
    k = st.columns(4)
    k[0].metric("Patrimônio", brl(pack["total_value_end"]),
                f"{pack['total_return_pct']:.2f}%".replace(".", ","))
    k[1].metric("Recursos investidos", f"{pack['invested_return_pct']:.2f}%".replace(".", ","))
    # CDI and IPCA side by side, never blended: one says whether the risk paid,
    # the other whether the money held its purchasing power.
    k[2].metric("CDI no período", f"{pack['cdi_return_pct']:.2f}%".replace(".", ","),
                f"{pack['excess_over_cdi_pp']:+.2f}".replace(".", ",") + " p.p. no investido")
    k[3].metric("IPCA no período", f"{pack['ipca_return_pct']:.2f}%".replace(".", ","),
                f"{pack['real_return_total_pct']:+.2f}%".replace(".", ",") + " real")
    k2 = st.columns(4)
    k2[0].metric("Cobertura de marcação", f"{pack['coverage_pct']:.2f}%".replace(".", ","))
    k2[1].metric("Ibovespa no período", f"{pack['ibov_return_pct']:.2f}%".replace(".", ","),
                 help="Referência de mercado, não parâmetro da carteira.")

    # Eight findings as eight stacked blocks pushed everything else below the
    # fold. The advisor needs the state of the portfolio and what moved since
    # last month; the case for each finding is one click away.
    lim = limits(cid)
    if rec and rec["recommendations"]:
        by_rule: dict[str, list] = {}
        for r in rec["recommendations"]:
            by_rule.setdefault(r["rule_id"], []).append(r)

        def worst(rid):
            v = [x["observed_pct"] for x in by_rule.get(rid, []) if x.get("observed_pct")]
            return max(v) if v else None

        rows = []
        if "MAX_IDLE_CASH_PCT" in by_rule:
            # Read, never recomputed: the screen must not be able to disagree
            # with the MetricsPack, which is the point of this file being thin.
            rows.append(("Caixa", f'<em>{pct(pack["cash_pct_of_total_end"])}</em> '
                                  f'· limite {pct(lim.get("MAX_IDLE_CASH_PCT", 0))}'))
        if "MAX_EQUITY_LOOKTHROUGH_PCT" in by_rule:
            rows.append(("Renda variável",
                         f'<em>{pct(rec["equity_lookthrough_pct"])}</em> '
                         f'· limite {pct(lim.get("MAX_EQUITY_LOOKTHROUGH_PCT", 0))}'))
        if "MAX_SINGLE_POSITION_PCT" in by_rule:
            n = len(by_rule["MAX_SINGLE_POSITION_PCT"])
            rows.append(("Concentração",
                         f'<em>{n} {"posição" if n == 1 else "posições"}</em> acima de '
                         f'{pct(lim.get("MAX_SINGLE_POSITION_PCT", 0))} da respectiva cesta '
                         f'· maior {pct(worst("MAX_SINGLE_POSITION_PCT") or 0)}'))
        if "NO_MATURED_HOLDINGS" in by_rule:
            n = len(by_rule["NO_MATURED_HOLDINGS"])
            rows.append(("Papel vencido", f'<em>{n}</em> em carteira, sem remuneração'))
        for rid in ("RESTRICTED_CATEGORY", "RESTRICTED_CATEGORY_FIDC"):
            if rid in by_rule:
                n = len(by_rule[rid])
                rows.append(("Fora do mandato",
                             f'<em>{n} {"posição" if n == 1 else "posições"}</em> '
                             f'em famílias não previstas'))

        st.markdown("**Estado da carteira**")
        st.markdown('<div class="state">' + "".join(
            f'<div class="r"><span class="k">{k}</span><span class="v">{v}</span></div>'
            for k, v in rows) + "</div>", unsafe_allow_html=True)
    elif rec:
        st.success("Carteira aderente ao mandato. Nenhum ajuste necessário.")

    # ---- what actually changed since last month
    names = instruments()["display_name"].to_dict()
    movers = sorted(pack["positions"], key=lambda m: -abs(m["contribution_pp"]))
    movers = [m for m in movers if abs(m["contribution_pp"]) >= 0.01][:5]
    if movers:
        st.markdown("**Principais mudanças no mês**")
        st.markdown('<div class="state one">' + "".join(
            f'<div class="r"><span class="k">{names.get(m["instrument_id"], m["instrument_id"])}'
            f'</span><span class="v">'
            f'{("+" if m["return_pct"] >= 0 else "")}{pct(m["return_pct"])} no ativo'
            f' · <em>{pp(m["contribution_pp"])}</em> na carteira</span></div>'
            for m in movers) + "</div>", unsafe_allow_html=True)
        st.caption("Contribuição de cada posição para o resultado do patrimônio, "
                   f"de {br_date(_ini)} a {br_date(_fim)}.")

    if rec and rec["recommendations"]:
        with st.expander(f"Base de cada ponto de atenção ({len(rec['recommendations'])})"):
            for r in rec["recommendations"]:
                st.markdown(f'<div class="finding"><b>{r["observed"]}</b>'
                            f'<span>Limite: {r["threshold"]} · {r["policy_source"]}</span></div>',
                            unsafe_allow_html=True)

    if allocation:
        st.markdown("**Alocação-alvo pelo modelo**")
        st.caption("Estimativas reais por classe até " + allocation["end"] +
                   ". Cenário e premissas do case, sem impostos e custos.")
        ac = st.columns(3)
        ac[0].metric("Renda variável · alvo", f"{allocation['target_rv_pct']:.2f}%".replace(".", ","))
        ac[1].metric("Renda fixa · alvo", f"{allocation['target_rf_pct']:.2f}%".replace(".", ","))
        ac[2].metric("Caixa · alvo", "0,00%")
        st.dataframe(pd.DataFrame([
            {"Classe": "RV · exposição ao Ibovespa", "Retorno real estimado acumulado": pct(allocation["rv_real_cumulative_pct"])},
            {"Classe": "RF · aproximação por Selic/IPCA", "Retorno real estimado acumulado": pct(allocation["rf_real_cumulative_pct"])},
        ]), hide_index=True, width="stretch")
        st.caption("Sugestão: vender as posições atuais de RV e migrar para fundo de índice Ibovespa. "
                   "O índice pode ocupar toda a cesta RV e mantém risco de mercado. Se preferir escolher ações, "
                   "cada ação deve ficar em até 25% da cesta RV. Em RF, o limite é 25% da cesta por produto.")
        with st.expander("Como o cenário define o alvo"):
            st.write(f"Teto de RV pelo perfil/horizonte: {pct(allocation['rv_ceiling_pct'])}. "
                     f"Diferença de retornos reais: {pp(allocation['spread_pp'])}. "
                     f"Fator macro: {allocation['macro_factor']:.4f}.")
            st.write("Alvo RV = teto × limitar(0,5 + diferença / (2 × sensibilidade), entre 0 e 1). "
                     f"Sensibilidade: {allocation['sensitivity_pp']:g} p.p. acumulados.")
            st.dataframe(pd.DataFrame([{
                "Ano": r["year"], "Meses considerados": r["months"],
                "PIB real": pct(r["pib"]), "IPCA": pct(r["ipca"]), "Selic": pct(r["selic"]),
                "RV real anual estimada": pct(r["rv_real_pct"]), "RF real anual estimada": pct(r["rf_real_pct"]),
            } for r in allocation["annual"]]), hide_index=True, width="stretch")
            st.write(f"Dividend yield assumido: {pct(allocation['dividend_yield_pct'])} ao ano.")
            for assumption in allocation["assumptions"]:
                st.write(assumption)

    if plan and plan["trades"]:
        st.markdown("**Ajustes sugeridos**")
        verbo = {"sell": "Vender", "redeem": "Resgatar", "buy": "Aplicar"}
        st.dataframe(pd.DataFrame([{
            "Operação": verbo[t["action"]],
            "Ativo ou destino": t["instrument_id"] or f"{t['category']} — {t['criteria']}",
            "Valor": brl(t["amount_brl"]),
            "Produtos distintos (mín.)": str(t.get("min_products") or "—")} for t in plan["trades"]]),
            width="stretch", hide_index=True)
        st.caption("Sugestões, sem execução de operações. "
                   + ("Alvos e limites por produto conferidos na simulação. " if plan["resolved"]
                      else "A simulação ainda apresenta pendências. ")
                   + f"Caixa após: {brl(plan['cash_after_brl'])}.")
        for pending in plan.get("pending_reviews", []):
            st.warning("Revisão de categoria ainda necessária: " + pending)
        with st.expander("Carteira simulada e premissas das operações"):
            st.dataframe(pd.DataFrame(plan["post_positions"]), hide_index=True, width="stretch")
            for assumption in plan["assumptions"]:
                st.write(assumption)

# ---------------------------------------------------------------- history
hist = load(cid, "history.json")
if hist:
    st.markdown('<p class="sectitle">Trajetória dos ativos</p>', unsafe_allow_html=True)
    st.caption(hist["assumption"])
    svg = OUT / cid / "charts" / "trajectory.svg"
    if svg.exists():
        st.markdown(f'<div class="chart">{svg.read_text(encoding="utf-8")}</div>',
                    unsafe_allow_html=True)

    pos = [s_ for s_ in hist["series"] if s_["kind"] == "position"]
    short = [s_ for s_ in pos if not s_["complete"]]
    if short or hist["excluded"]:
        n = len(hist["excluded"])
        st.caption(f"{len(pos)} posições com série publicada, {len(short)} delas com janela "
                   f"incompleta{f'; {n} sem série nenhuma' if n else ''}. "
                   f"Nada é interpolado — o detalhe está abaixo.")
    with st.expander("Cobertura da série, ativo a ativo"):
        st.dataframe(pd.DataFrame([{
            "Ativo": s_["display_name"],
            "Meses": f"{s_['months_covered']} de {hist['months_requested']}",
            "De": s_["first_month"], "Até": s_["last_month"],
            "No período": f"{s_['total_return_pct']:.2f}%".replace(".", ","),
            "Base": {"quota_month_end": "cota de fim de mês",
                     "close_month_end": "fechamento de fim de mês",
                     "reported_monthly_return": "rentabilidade publicada"}.get(s_["basis"],
                                                                               s_["basis"]),
        } for s_ in pos]), width="stretch", hide_index=True)
        for s_ in pos:
            if s_["gap_note"]:
                st.markdown(f'<div class="finding"><b>{s_["display_name"]}</b>'
                            f'<span>{s_["gap_note"]}</span></div>', unsafe_allow_html=True)
        for e in hist["excluded"]:
            st.markdown(f'<div class="finding"><b>{e["display_name"]} — sem trajetória</b>'
                        f'<span>{e["reason"]}</span></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------- letter
st.markdown('<p class="sectitle">Relatório do cliente</p>', unsafe_allow_html=True)

pdf_p, html_p = OUT / cid / "letter.pdf", OUT / cid / "letter.html"
generation = load(cid, "generation_log.json") or {}
current_figures = load(cid, "figures.json") or {}
layout = load(cid, "layout_report.json") or {}
current_letter = bool(current_plan and generation.get("approved")
                      and generation.get("model_version") == MODEL_VERSION
                      and generation.get("allocation_fingerprint") == allocation.get("fingerprint")
                      and generation.get("figures_fingerprint") == fingerprint(current_figures)
                      and layout.get("generation_run_id") == generation.get("run_id")
                      and layout.get("html_ready"))
if (pdf_p.exists() or html_p.exists()) and not current_letter:
    st.info("O relatório salvo é anterior aos dados/modelo atuais. Gere outro relatório para visualizar ou baixar.")
a = st.columns([2, 1, 1])

if a[0].button(f"Gerar relatório de {name.split()[0]}", type="primary",
               width="stretch", disabled=not current_plan):
    if not key:
        st.error("Informe a chave da OpenAI na barra lateral.")
    else:
        failed, log = run(LETTER, cid, key=key, model=model)
        st.session_state["log"] = log
        if failed:
            st.error("Não foi possível concluir. Veja os detalhes técnicos ao final.")
        else:
            st.cache_data.clear()
            st.rerun()

if current_letter and layout.get("pdf_ready") and pdf_p.exists():
    a[1].download_button("Baixar PDF", pdf_p.read_bytes(), f"relatorio_{cid.lower()}.pdf",
                         "application/pdf", width="stretch")
elif current_letter and html_p.exists() and a[1].button("Gerar PDF", width="stretch"):
    failed, log = run(RENDER, cid)
    st.session_state["log"] = log
    if failed:
        st.warning("Este ambiente não dispõe de navegador para impressão. "
                   "Baixe o HTML e use Ctrl+P → Salvar como PDF: o documento já traz "
                   "as regras de impressão em A4.")
    else:
        st.rerun()
if current_letter and html_p.exists():
    a[2].download_button("Baixar HTML", html_p.read_bytes(), f"relatorio_{cid.lower()}.html",
                         "text/html", width="stretch")

if current_letter and rep is not None:
    blockers = [r for r in rep if r["severity"] == "blocker"]
    if blockers:
        st.error(f"O relatório não passou na verificação: {len(blockers)} pendência(s). "
                 "Não deve ser enviado ao cliente.")
    else:
        st.caption("Verificado: toda cifra do relatório consta da lista autorizada.")

if current_letter and html_p.exists():
    with st.expander("Pré-visualizar relatório"):
        st_html(html_p.read_text(encoding="utf-8"), height=900, scrolling=True)

# ---------------------------------------------------------------- audit trail
with st.expander("Detalhes técnicos"):
    st.caption("O registro completo da execução fica disponível para auditoria, "
               "mas fora do caminho de quem só quer o relatório.")
    if pack and pack.get("issues"):
        st.markdown("**Qualidade de dados**")
        for i in pack["issues"]:
            st.write(f"`{i['severity']}` **{i['code']}** — {i['message']}")
    if st.session_state.get("log"):
        st.code(st.session_state["log"], language="text")
