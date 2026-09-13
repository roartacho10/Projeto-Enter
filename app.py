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
from html import escape
from src.email_report import send_report
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
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
  #MainMenu, footer {visibility:hidden;}
  [data-testid="stHeader"] {background:transparent;}
  [data-testid="stToolbar"] {visibility:hidden; height:0;}
  [data-testid="stExpandSidebarButton"], [data-testid="stSidebar"] {display:none !important;}
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
  .chip {display:inline-block; padding:.12rem .55rem; border-radius:999px;
         font-size:.74rem; font-weight:600; white-space:nowrap;}
  .chip.ok   {background:#E8F3EC; color:#1E6B3A;}
  .chip.warn {background:#FDF3D0; color:#7A5B00;}
  .chip.crit {background:#FBE9E7; color:#8C2F1E;}
  .chip.none {background:#F0F0EE; color:#6B6B6B;}
  .sectitle {font-size:1.25rem; font-weight:700; margin:1.6rem 0 .4rem;
             padding-bottom:.35rem; border-bottom:1px solid #E4E4E2;}
  .finding {border-left:3px solid #FFC700; background:#FCFCFB; padding:.5rem .8rem;
            margin:.35rem 0; font-size:.88rem;}
  .finding b {display:block;}
  .finding span {color:#6B6B6B; font-size:.8rem;}
  .state {display:grid; grid-template-columns:1fr 1fr; gap:0 1.8rem; margin:.2rem 0 .4rem;}
  .state.one {grid-template-columns:1fr;}
  .state .r {display:flex; justify-content:space-between; align-items:baseline; gap:1rem;
             border-bottom:1px solid #F1F1EF; padding:.3rem .1rem; font-size:.86rem;}
  .state .r .k {color:#5A5A5A; white-space:nowrap;}
  .state .r .v {min-width:0;}
  .adjustments {table-layout:fixed;}
  .mtable.adjustments th, .mtable.adjustments td {white-space:normal; overflow-wrap:anywhere;}
  .adjustments th:nth-child(2), .adjustments td:nth-child(2) {width:60%; text-align:left;}
  .state .r .v {white-space:nowrap; color:#5A5A5A;}
  .state .r .v em {font-style:normal; font-weight:700; color:#1A1A1A;}
  /* One table for the month: two dated columns and the variation between
     them, for the totals, every position and the references. */
  .mtable {width:100%; border-collapse:collapse; font-size:.86rem; margin:1rem 0 .3rem;}
  .mtable th {text-align:right; font-weight:600; color:#7A7A7A; font-size:.72rem;
              text-transform:uppercase; letter-spacing:.05em; white-space:nowrap;
              border-bottom:1px solid #E4E4E2; padding:.3rem .55rem .35rem;}
  .mtable th:first-child {text-align:left;}
  .mtable td {text-align:right; padding:.3rem .55rem; white-space:nowrap;
              border-bottom:1px solid #F1F1EF; color:#1A1A1A;}
  .mtable td:first-child {text-align:left; color:#5A5A5A; white-space:normal;}
  .mtable td.na {color:#C9C9C7;}
  .mtable .sub {font-size:.72rem; color:#8A8A88; font-weight:400; margin-top:.05rem;}
  .mtable tr.tot td {font-weight:700; background:#FAFAF9;}
  .mtable tr.tot td:first-child {color:#1A1A1A;}
  .mtable tr.grp td {border-bottom:none; padding:.8rem .55rem .15rem; color:#7A7A7A;
                     font-size:.72rem; text-transform:uppercase; letter-spacing:.06em;}
  /* Direction is carried by the arrow and the sign as well as by the colour,
     so the table still reads without colour vision. */
  .up {color:#1E6B3A; font-weight:600;}
  .dn {color:#8C2F1E; font-weight:600;}
  .fl {color:#9A9A98;}
  /* A distance to a target is not a gain or a loss: neutral on purpose. */
  .gap {color:#5A5A5A; font-weight:600;}
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
def profiles() -> pd.DataFrame:
    """What extract_profile read out of the client's risk document, with the
    sentence that produced each parameter."""
    return pd.read_csv(REF / "client_profile.csv", dtype=str).fillna("").set_index("client_id")


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


def trend(v) -> str:
    """A variation as arrow, sign and colour. Never computed here - every value
    passed in already exists on the MetricsPack."""
    n = f"{float(v):+.2f}".replace(".", ",") + "%"
    if abs(float(v)) < 0.005:
        return f'<span class="fl">– {n[1:]}</span>'
    cls, arrow = ("up", "▲") if float(v) > 0 else ("dn", "▼")
    return f'<span class="{cls}">{arrow} {n}</span>'


def contrib(v) -> str:
    """Contribution in percentage points, same colour language."""
    if abs(float(v)) < 0.005:
        return '<span class="fl">–</span>'
    return f'<span class="{"up" if float(v) > 0 else "dn"}">{pp(v)}</span>'


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
# The table reads the positions too, so a field added to PositionMetric has to
# be declared here as well or a pack from an older build gets past the check
# and dies one line later.
POSITION_FIELDS = {"instrument_id", "value_start", "value_end", "return_pct",
                   "contribution_pp"}


def current_pack(cid: str):
    """The saved MetricsPack when this build can read it, otherwise None."""
    pack = load(cid, "metrics_pack.json")
    if not pack or not PACK_FIELDS.issubset(pack):
        return None
    if any(not POSITION_FIELDS.issubset(m) for m in pack["positions"]):
        return None
    return pack


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
    with st.spinner("Processando... Aguarde."):
        progress = st.empty()
        for name, desc in stages:
            progress.caption(desc)
            r = subprocess.run([sys.executable, str(SRC / f"{name}.py")], cwd=ROOT,
                               capture_output=True, text=True, env=env)
            transcript.append(f"$ {name}.py\n{(r.stdout or '') + (r.stderr or '')}")
            if r.returncode != 0:
                progress.empty()
                return name, "\n".join(transcript)
        progress.empty()
    return None, "\n".join(transcript)


def process_action(action: str, cid: str) -> None:
    """Read the submitted widgets after rendering them, outside the callback."""
    kwargs = {}
    if action == "letter":
        api_key = ambient_key()[0] or st.session_state.get("key_sidebar", "").strip()
        if not api_key:
            st.session_state["action_feedback"] = ("error", "Informe a chave da OpenAI na configuração da carta.")
            return
        kwargs = {"key": api_key,
                  "model": st.session_state.get("model_letter") or secret("MODEL_LETTER") or DEFAULT_MODEL}
    failed, log = run(TRIAGE if action == "enter" else LETTER, cid, **kwargs)
    st.session_state["log"] = log
    if failed:
        st.session_state["action_feedback"] = ("error", f"Não foi possível concluir a etapa {failed}. Veja os detalhes técnicos.")
    else:
        if action == "enter":
            st.session_state["entrou"] = True
        st.cache_data.clear()
        st.session_state["action_feedback"] = (
            "success", "Carteira carregada." if action == "enter" else "Carta gerada. Os arquivos estão disponíveis abaixo.")


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


def band(sub: str = "") -> None:
    """The brand strip, shared by the entry screen and the desk."""
    st.markdown(f"""
<div class="band">
  <div>
    <p class="eyebrow">XP Investimentos</p>
    <h1>Relatórios mensais</h1>
    {f'<p class="sub">{sub}</p>' if sub else ''}
  </div>
  <img src="{logo_uri()}" alt="XP">
</div>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- entry
# A screen before the desk. There is no password and none is implied: this is
# a demonstration, and a login that verifies nothing should not dress itself
# up as one that does. What the button actually does is the work - it runs the
# same triage the desk's "Atualizar dados" runs, so the portfolio that opens
# has been priced and re-checked in front of the user rather than served from
# whatever a previous session happened to leave on disk.
# Retire flags left by the former callback queue, including interrupted sessions.
st.session_state.pop("pending_action", None)
st.session_state.pop("processing_action", None)
feedback = st.session_state.pop("action_feedback", None)
if feedback:
    getattr(st, feedback[0])(feedback[1])

if not st.session_state.get("entrou"):
    band()
    # The card spans the same column as the brand strip above it, so the two
    # share one left and right edge. Boxed in a narrower centre column it read
    # as a widget dropped on the page rather than as the page itself.
    with st.form("access_form", border=True):
        st.markdown("##### Acesso do assessor")
        f = st.columns([2, 1, 1], vertical_alignment="bottom")
        f[0].text_input("Assessor", value=str(_crow["advisor"]), key="entrada_assessor")
        f[1].text_input("Código", value=str(_crow["advisor_code"]), disabled=True,
                        key="entrada_codigo")
        enter_clicked = f[2].form_submit_button("Acessar", type="primary", width="stretch")
    if enter_clicked:
        process_action("enter", ACTIVE)
        st.rerun()
    if st.session_state.get("log"):
        with st.expander("Detalhes técnicos"):
            st.code(st.session_state["log"], language="text")
    st.stop()

band(f'{_crow["advisor"]} · Código {_crow["advisor_code"]} · '
     f'Período de referência: {_mes.lower()} de {_ano}')

top_title, top_sync = st.columns([20, 1], vertical_alignment="center")
top_title.markdown('<p class="sectitle">Carteira de clientes</p>', unsafe_allow_html=True)
atualizar = top_sync.button("", icon=":material/sync:", help="Atualizar dados")

if atualizar:
    failed, log = run(TRIAGE, ACTIVE)
    st.session_state["log"] = log
    if failed:
        st.error(f"Falhou em {failed}. Veja os detalhes técnicos ao final.")
        st.stop()
    st.cache_data.clear()
    st.rerun()

q = queue()
active = q[q["_active"]].iloc[0]

# ---------------------------------------------------------------- client menu

cards = st.columns(len(q))
for col, (_, r) in zip(cards, q.iterrows()):
    with col:
        if not r["_active"]:
            st.markdown(f'''<div class="card ghost">
  <p class="nm">{r["Cliente"]}</p>
  <p class="pf">{r["Perfil"]}</p>
  <span class="chip none">não processado</span>
</div>''', unsafe_allow_html=True)
            continue
        achados = int(r["Achados"]) if pd.notna(r["Achados"]) else None
        valor = brl(r["Patrimônio"]) if pd.notna(r["Patrimônio"]) else "—"
        st.markdown(f'''<div class="card sel">
  <p class="nm">{r["Cliente"]}</p>
  <p class="pf">{r["Perfil"]} · {valor}</p>
  {chip(achados)}
</div>''', unsafe_allow_html=True)

# ---------------------------------------------------------------- detail
# No heading with the client's name: the selected card above already says
# whose numbers these are, and repeating it read as a second selection.
cid = ACTIVE
name = active["Cliente"]

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
    # ---- the whole month in one table
    # Totals first, then every position, on the two dates the period is
    # defined by. Nothing here is calculated: each variation is the return the
    # MetricsPack already carries for that line, so a row cannot disagree with
    # the letter.
    names = instruments()["display_name"].to_dict()

    def linha(rotulo, v0, v1, var, classe=""):
        return (f'<tr class="{classe}"><td>{rotulo}</td><td>{brl(v0)}</td>'
                f'<td>{brl(v1)}</td><td>{trend(var)}</td></tr>')

    def linha_ref(rotulo, var):
        """
        A reference is a return over the period, not a balance held on either
        date, so those two cells stay empty instead of being filled with a
        level no source declares. Only the variation column is comparable -
        which is the whole reason for putting these rows in the same table.
        """
        return (f'<tr><td>{rotulo}</td><td class="na">—</td><td class="na">—</td>'
                f'<td>{trend(var)}</td></tr>')

    corpo = linha("Patrimônio", pack["total_value_start"], pack["total_value_end"],
                  pack["total_return_pct"], classe="tot")
    corpo += linha("Recursos investidos", pack["invested_value_start"],
                   pack["invested_value_end"], pack["invested_return_pct"])
    # Grouped by what the instrument IS, from the security master - not by a
    # list kept here, which would drift the first time a holding is added.
    tipos = instruments()["type"].to_dict()
    GRUPOS = [("Ações", {"stock", "index"}),
              ("Fundos", {"fund"}),
              ("CDBs e caixa", {"fixed_income", "cash"})]
    posicoes = sorted(pack["positions"], key=lambda x: -x["value_end"])
    vistas = set()
    for titulo, tipos_do_grupo in GRUPOS:
        doo = [m for m in posicoes if tipos.get(m["instrument_id"]) in tipos_do_grupo]
        if not doo:
            continue
        vistas.update(m["instrument_id"] for m in doo)
        corpo += f'<tr class="grp"><td colspan="4">{titulo}</td></tr>'
        for m in doo:
            corpo += linha(names.get(m["instrument_id"], m["instrument_id"]),
                           m["value_start"], m["value_end"], m["return_pct"])
    # A holding whose type matches no group is shown, not dropped: a position
    # missing from the table would be a silent omission from the client's book.
    restantes = [m for m in posicoes if m["instrument_id"] not in vistas]
    if restantes:
        corpo += '<tr class="grp"><td colspan="4">Outros</td></tr>'
        for m in restantes:
            corpo += linha(names.get(m["instrument_id"], m["instrument_id"]),
                           m["value_start"], m["value_end"], m["return_pct"])
    corpo += '<tr class="grp"><td colspan="4">Referências do mês</td></tr>'
    corpo += linha_ref("CDI", pack["cdi_return_pct"])
    corpo += linha_ref("IPCA", pack["ipca_return_pct"])

    st.markdown(
        '<table class="mtable"><thead><tr><th>Métrica</th>'
        f'<th>{br_date(_ini)}</th><th>{br_date(_fim)}</th>'
        '<th>Variação</th></tr></thead>'
        f'<tbody>{corpo}</tbody></table>', unsafe_allow_html=True)
    st.caption(f"Variação de cada linha entre {br_date(_ini)} e {br_date(_fim)}. "
               "CDI e IPCA são retornos do mês, comparáveis à coluna de variação, "
               "e não saldos em carteira.")

# --------------------------------------------------------- profile and basis
if pack and allocation:
    st.markdown('<p class="sectitle">Perfil e base da análise</p>', unsafe_allow_html=True)
    prof = profiles().loc[cid] if cid in profiles().index else None
    linhas_perfil = [
        ("Perfil e horizonte",
         f'<em>{prof["risk_class"]}</em> · horizonte {prof["horizon"].lower()}'
         if prof is not None else f'<em>{active["Perfil"]}</em>'),
    ]
    if prof is not None and prof["objective_quote"]:
        linhas_perfil.append(("Objetivo declarado", f'“{prof["objective_quote"]}”'))
    st.markdown('<div class="state">' + "".join(
        f'<div class="r"><span class="k">{k}</span><span class="v">{v}</span></div>'
        for k, v in linhas_perfil) + "</div>", unsafe_allow_html=True)

# --------------------------------------------------------- current vs target
if pack and plan and plan.get("class_mix"):
    st.markdown('<p class="sectitle">Carteira hoje e alvo</p>', unsafe_allow_html=True)

    def col_alvo(hoje, alvo):
        """
        The gap, in points, between where a class is and where it should be -
        deliberately NOT green or red. Green and red mean better and worse
        everywhere else on this screen, and here they would mean nothing of
        the kind: taking cash from 18,18% to zero is the whole point of the
        plan and would have shown up in red. The sign carries the direction.
        """
        if abs(alvo - hoje) < 0.005:
            return '<span class="fl">–</span>'
        return f'<span class="gap">{pp(alvo - hoje)}</span>'

    corpo = ""
    for r in plan["class_mix"]:
        nota = ""
        corpo += (f'<tr><td>{r["label"]}{nota}</td><td>{brl(r["before_brl"])}</td>'
                  f'<td>{pct(r["before_pct"])}</td><td>{pct(r["target_pct"])}</td>'
                  f'<td>{col_alvo(r["before_pct"], r["target_pct"])}</td></tr>')
    st.markdown(
        '<table class="mtable"><thead><tr><th>Classe</th><th>Hoje</th>'
        '<th>Hoje %</th><th>Alvo %</th><th>Distância</th></tr></thead>'
        f'<tbody>{corpo}</tbody></table>', unsafe_allow_html=True)
    st.caption(f"Percentuais sobre o patrimônio de {br_date(_fim)}; alvo = carteira após o ajuste.")

    if plan.get("concentration"):
        def contra_limite(valor, limite):
            """Here colour DOES mean better or worse: over the cap is a breach."""
            if valor is None:
                return '<span class="fl">–</span>'
            classe = "dn" if valor > limite + 0.005 else "up"
            return f'<span class="{classe}">{pct(valor)}</span>'

        corpo = ""
        for c in plan["concentration"]:
            nota = f'<div class="sub">{c["note"]}</div>' if c.get("note") else ""
            corpo += (f'<tr><td>{c["label"]}{nota}</td>'
                      f'<td>{contra_limite(c["before_pct"], c["limit_pct"])}</td>'
                      f'<td>{pct(c["limit_pct"])}</td>'
                      f'<td>{contra_limite(c["after_pct"], c["limit_pct"])}</td></tr>')
        st.markdown("**Concentração dentro de cada cesta**")
        st.markdown(
            '<table class="mtable"><thead><tr><th>Medida</th><th>Hoje</th>'
            '<th>Limite</th><th>Depois do ajuste</th></tr></thead>'
            f'<tbody>{corpo}</tbody></table>', unsafe_allow_html=True)

    if rec and rec["recommendations"]:
        with st.expander(f"Base de cada ponto de atenção ({len(rec['recommendations'])})"):
            for r in rec["recommendations"]:
                st.markdown(f'<div class="finding"><b>{r["observed"]}</b>'
                            f'<span>Limite: {r["threshold"]} · {r["policy_source"]}</span></div>',
                            unsafe_allow_html=True)
    elif rec:
        st.success("Carteira aderente ao mandato. Nenhum ajuste necessário.")

if pack:
    if plan and plan["trades"]:
        st.markdown("**Ajustes sugeridos**")
        verbo = {"sell": "Vender", "redeem": "Resgatar", "buy": "Aplicar"}
        rows = "".join(
            "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in (
                verbo[t["action"]],
                names.get(t["instrument_id"], t["instrument_id"]) if t["instrument_id"]
                else f"{t['category']} — {t['criteria']}",
                brl(t["amount_brl"]), t.get("min_products") or "—")) + "</tr>"
            for t in plan["trades"])
        st.markdown('<table class="mtable adjustments"><thead><tr><th>Operação</th>'
                    '<th>Ativo ou destino</th><th>Valor</th><th>Produtos mín.</th>'
                    f'</tr></thead><tbody>{rows}</tbody></table>', unsafe_allow_html=True)
        st.caption("Sugestões, sem execução de operações. "
                   + ("Alvos e limites por produto conferidos na simulação. " if plan["resolved"]
                      else "A simulação ainda apresenta pendências. ")
                   + f"Caixa após: {brl(plan['cash_after_brl'])}.")
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
st.markdown('<p class="sectitle">Carta do cliente</p>', unsafe_allow_html=True)

with st.form("letter_form", border=False):
    with st.expander("Configura\u00e7\u00e3o do relat\u00f3rio"):
        st.markdown("#### Configuração")
        key, key_src = ambient_key()
        if key:
            st.caption("OpenAI configurada. A chave salva é usada automaticamente em cada relatório.")
        else:
            key = st.text_input("Chave da OpenAI", type="password", placeholder="sk-...",
                                key="key_sidebar",
                                help="Para não precisar digitar de novo, configure "
                                     "OPENAI_API_KEY no ambiente ou em .streamlit/secrets.toml.")
            st.caption("Para salvar uma vez só, acrescente OPENAI_API_KEY nos Secrets do Streamlit, "
                       "junto das configurações do Gmail. O campo acima é apenas para uso temporário.")

        model = secret("MODEL_LETTER") or DEFAULT_MODEL
        st.caption(f"Modelo de redação: **{model}**")
        with st.expander("Trocar modelo"):
            opts = [model] + [m for m in MODELS if m != model]
            model = st.selectbox("Modelo de redação", opts, index=0, label_visibility="collapsed", key="model_letter")
    generate_clicked = st.form_submit_button(f"Gerar carta de {name.split()[0]}",
        type="primary", key="generate_letter", width="stretch", disabled=not current_plan)

if generate_clicked:
    process_action("letter", cid)
    st.rerun()

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
a = st.columns(2)

if current_letter and layout.get("pdf_ready") and pdf_p.exists():
    a[0].download_button("Baixar PDF", pdf_p.read_bytes(), f"relatorio_{cid.lower()}.pdf",
                         "application/pdf", width="stretch")
elif current_letter and html_p.exists() and a[0].button("Gerar PDF", width="stretch"):
    failed, log = run(RENDER, cid)
    st.session_state["log"] = log
    if failed:
        st.warning("Este ambiente não dispõe de navegador para impressão. "
                   "Baixe o HTML e use Ctrl+P → Salvar como PDF: o documento já traz "
                   "as regras de impressão em A4.")
    else:
        st.rerun()
if current_letter and html_p.exists():
    a[1].download_button("Baixar HTML", html_p.read_bytes(), f"relatorio_{cid.lower()}.html",
                         "text/html", width="stretch")

if current_letter and html_p.exists():
    email_col, send_col = st.columns([3, 1], vertical_alignment="bottom")
    recipient = email_col.text_input("E-mail do destinatário", placeholder="cliente@exemplo.com")
    settings = {k: secret(k) for k in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")}
    settings["SMTP_HOST"] = settings["SMTP_HOST"] or "smtp.gmail.com"
    settings["SMTP_FROM"] = settings["SMTP_FROM"] or settings["SMTP_USER"]
    configured = bool(settings["SMTP_USER"] and settings["SMTP_PASSWORD"])
    if send_col.button("Enviar por e-mail", width="stretch", disabled=not configured):
        attachments = [(f"relatorio_{cid.lower()}.html", html_p.read_bytes(), "text/html")]
        if layout.get("pdf_ready") and pdf_p.exists():
            attachments.insert(0, (f"relatorio_{cid.lower()}.pdf", pdf_p.read_bytes(), "application/pdf"))
        try:
            with st.spinner("Enviando relatório..."):
                send_report(recipient.strip(), attachments, settings,
                            client_name=name, advisor_name=str(_crow["advisor"]))
            st.success("Relatório enviado ao servidor de e-mail.")
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error("Não foi possível confirmar o envio. Confira a conta remetente antes de tentar novamente.")
    if not configured:
        st.caption("Para habilitar o envio pelo Gmail, configure SMTP_USER e SMTP_PASSWORD nos Secrets do Streamlit.")

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
