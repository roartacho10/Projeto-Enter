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
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

ROOT = Path(__file__).resolve().parent
SRC, OUT, REF, ASSETS = ROOT / "src", ROOT / "output", ROOT / "data" / "reference", ROOT / "assets"

TRIAGE = [("build_prices", "Atualizando preços"), ("compute_metrics", "Calculando resultados"),
          ("recommend", "Aplicando regras de adequação"),
          ("rebalance", "Dimensionando ajustes"), ("figures", "Preparando os números")]
LETTER = [("generate_letter", "Redigindo e verificando"), ("charts", "Desenhando gráficos"),
          ("render_pdf", "Montando o documento")]
RENDER = [("charts", "Desenhando gráficos"), ("render_pdf", "Montando o documento")]
DEFAULT_MODEL = "gpt-4.1"
MODELS = ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"]

st.set_page_config(page_title="Relatórios mensais · XP", page_icon="📄", layout="wide")

st.markdown("""
<style>
  #MainMenu, footer, header {visibility: hidden;}
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
  .card .nm {font-weight:600; font-size:.95rem; margin:0 0 .1rem; line-height:1.25;}
  .card .pf {color:#7A7A7A; font-size:.78rem; margin:0 0 .55rem;}
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


def load(cid: str, name: str):
    p = OUT / cid / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


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
    cols = ["Cliente", "_id", "Perfil", "Patrimônio", "Retorno", "Caixa", "Achados", "Carta"]
    rows = []
    for _, c in clients().iterrows():
        cid = c["client_id"]
        row = dict.fromkeys(cols)
        row |= {"Cliente": c["name"], "_id": cid, "Perfil": c["profile"],
                "Carta": (OUT / cid / "letter.pdf").exists()
                or (OUT / cid / "letter.html").exists()}
        pack, rec = load(cid, "metrics_pack.json"), load(cid, "recommendations.json")
        if pack:
            row |= {"Patrimônio": pack["total_value_end"], "Retorno": pack["total_return_pct"],
                    "Caixa": pack["cash_pct_of_total"],
                    "Achados": len(rec["recommendations"]) if rec else 0}
        rows.append(row)
    return pd.DataFrame(rows, columns=cols).sort_values(
        ["Achados", "Caixa"], ascending=False, na_position="last")


# ---------------------------------------------------------------- header
st.markdown(f"""
<div class="band">
  <div>
    <p class="eyebrow">XP Investimentos</p>
    <h1>Relatórios mensais</h1>
    <p class="sub">Antonio Bicudo · Código A7699 · Período de referência: abril de 2025</p>
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
                                     label_visibility="collapsed",
                                     help="Vale só para esta sessão do navegador.")
            key = override or key
    else:
        key = st.text_input("Chave da OpenAI", type="password", placeholder="sk-...",
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
pend = q["Achados"].dropna()

# ---------------------------------------------------------------- client menu
h = st.columns([3, 1])
h[0].markdown('<p class="sectitle">Carteira de clientes</p>', unsafe_allow_html=True)
with h[1]:
    st.write("")
    if st.button("Atualizar carteira", width="stretch"):
        for _, c in clients().iterrows():
            failed, log = run(TRIAGE, c["client_id"])
            if failed:
                st.error(f"Falhou em {failed} para {c['name']}.")
                st.session_state["log"] = log
                st.stop()
        st.cache_data.clear()
        st.rerun()

if pend.empty:
    st.info("Nenhum cliente processado ainda. Use **Atualizar carteira** para revisar todos.")
else:
    st.caption(f"{len(pend)} clientes revisados · {int((pend > 0).sum())} pedem atenção · "
               f"{int((pend == 0).sum())} em ordem. Selecione um cliente para ver o caso.")

if "sel" not in st.session_state:
    ids = list(q["_id"])
    st.session_state["sel"] = "ALBERT" if "ALBERT" in ids else ids[0]

cards = st.columns(len(q))
for col, (_, r) in zip(cards, q.iterrows()):
    cid_r = r["_id"]
    is_sel = cid_r == st.session_state["sel"]
    achados = int(r["Achados"]) if pd.notna(r["Achados"]) else None
    valor = brl(r["Patrimônio"]) if pd.notna(r["Patrimônio"]) else "—"
    with col:
        st.markdown(f'''<div class="card{" sel" if is_sel else ""}">
  <p class="nm">{r["Cliente"]}</p>
  <p class="pf">{r["Perfil"]} · {valor}</p>
  {chip(achados)}
</div>''', unsafe_allow_html=True)
        if st.button("Em análise" if is_sel else "Selecionar", key=f"sel_{cid_r}",
                     type="primary" if is_sel else "secondary",
                     disabled=is_sel, width="stretch"):
            st.session_state["sel"] = cid_r
            st.rerun()

# ---------------------------------------------------------------- detail
cid = st.session_state["sel"]
name = clients().set_index("client_id").loc[cid, "name"]
st.markdown(f'<p class="sectitle">{name}</p>', unsafe_allow_html=True)

pack, rec = load(cid, "metrics_pack.json"), load(cid, "recommendations.json")
plan, rep = load(cid, "rebalance_plan.json"), load(cid, "verification_report.json")

if not pack:
    st.info("Cliente ainda não processado. Use **Atualizar** acima.")
else:
    k = st.columns(4)
    k[0].metric("Patrimônio", brl(pack["total_value_end"]),
                f"{pack['total_return_pct']:.2f}%".replace(".", ","))
    k[1].metric("Recursos investidos", f"{pack['invested_return_pct']:.2f}%".replace(".", ","))
    k[2].metric("Ante o parâmetro", f"{pack['benchmark_return_pct']:.2f}%".replace(".", ","),
                f"{pack['excess_return_pp']:+.2f}".replace(".", ",") + " p.p.")
    k[3].metric("Cobertura de marcação", f"{pack['coverage_pct']:.2f}%".replace(".", ","))

    if rec and rec["recommendations"]:
        st.markdown("**Pontos de atenção**")
        for r in rec["recommendations"]:
            st.markdown(f'<div class="finding"><b>{r["observed"]}</b>'
                        f'<span>Limite: {r["threshold"]} · {r["policy_source"]}</span></div>',
                        unsafe_allow_html=True)
    elif rec:
        st.success("Carteira aderente ao mandato. Nenhum ajuste necessário.")

    if plan and plan["trades"]:
        st.markdown("**Ajustes sugeridos**")
        verbo = {"sell": "Vender", "redeem": "Resgatar", "buy": "Aplicar"}
        st.dataframe(pd.DataFrame([{
            "Operação": verbo[t["action"]],
            "Ativo ou destino": t["instrument_id"] or f"{t['category']} — {t['criteria']}",
            "Valor": brl(t["amount_brl"])} for t in plan["trades"]]),
            width="stretch", hide_index=True)
        st.caption(f"Estes ajustes resolvem os {len(plan['violations_before'])} pontos de "
                   f"atenção acima. Caixa após: {brl(plan['cash_after_brl'])}.")

# ---------------------------------------------------------------- letter
st.markdown('<p class="sectitle">Relatório do cliente</p>', unsafe_allow_html=True)
pdf_p, html_p = OUT / cid / "letter.pdf", OUT / cid / "letter.html"
a = st.columns([2, 1, 1])

if a[0].button(f"Gerar relatório de {name.split()[0]}", type="primary",
               width="stretch", disabled=not pack):
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

if pdf_p.exists():
    a[1].download_button("Baixar PDF", pdf_p.read_bytes(), f"relatorio_{cid.lower()}.pdf",
                         "application/pdf", width="stretch")
elif html_p.exists() and a[1].button("Gerar PDF", width="stretch"):
    failed, log = run(RENDER, cid)
    st.session_state["log"] = log
    if failed:
        st.warning("Este ambiente não dispõe de navegador para impressão. "
                   "Baixe o HTML e use Ctrl+P → Salvar como PDF: o documento já traz "
                   "as regras de impressão em A4.")
    else:
        st.rerun()
if html_p.exists():
    a[2].download_button("Baixar HTML", html_p.read_bytes(), f"relatorio_{cid.lower()}.html",
                         "text/html", width="stretch")

if rep is not None:
    blockers = [r for r in rep if r["severity"] == "blocker"]
    if blockers:
        st.error(f"O relatório não passou na verificação: {len(blockers)} pendência(s). "
                 "Não deve ser enviado ao cliente.")
    else:
        st.caption("Verificado: toda cifra do relatório consta da lista autorizada.")

if html_p.exists():
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
