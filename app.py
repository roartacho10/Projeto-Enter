"""
Advisor front end.

The screen is deliberately thin: it runs the same scripts run.py runs and shows
what they produced. No calculation happens here, so the interface can never
disagree with the MetricsPack.

The queue is the point. An advisor with 300 clients does not want to read 300
letters - they want to know which ones need them. Triage is deterministic and
free of language-model calls; the letter is generated only for the client the
advisor opens.

Hostable by design: the API key comes from the form, nothing depends on the bulk
CVM downloads at run time, and the PDF step degrades to HTML where no browser is
installed.

    streamlit run app.py
"""
from __future__ import annotations
from pathlib import Path
import json
import os
import subprocess
import sys
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

ROOT = Path(__file__).resolve().parent
SRC, OUT, REF = ROOT / "src", ROOT / "output", ROOT / "data" / "reference"

TRIAGE = [("build_prices", "Preços"), ("compute_metrics", "Métricas"),
          ("recommend", "Regras"), ("rebalance", "Rebalanceamento"), ("figures", "Cifras")]
LETTER = [("generate_letter", "Redação e verificação"), ("charts", "Gráficos"),
          ("render_pdf", "Documento")]
RENDER = [("charts", "Gráficos"), ("render_pdf", "Documento")]

st.set_page_config(page_title="Relatórios mensais — XP", page_icon="📄", layout="wide")


def brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def run(stages, client=None, key=None, model=None, log=None):
    env = dict(os.environ)
    if client:
        env["CLIENT_ID"] = client
    if key:
        env["OPENAI_API_KEY"] = key
    if model:
        env["MODEL_LETTER"] = model
    bar = st.progress(0.0)
    for i, (name, desc) in enumerate(stages):
        bar.progress(i / len(stages), text=desc)
        r = subprocess.run([sys.executable, str(SRC / f"{name}.py")], cwd=ROOT,
                           capture_output=True, text=True, env=env)
        if log is not None:
            with log:
                st.markdown(f"**{name}** — {desc}")
                st.code((r.stdout or "") + (r.stderr or ""), language="text")
        if r.returncode != 0:
            bar.progress(1.0, text="interrompido")
            return name
    bar.progress(1.0, text="concluído")
    return None


@st.cache_data(show_spinner=False)
def clients() -> pd.DataFrame:
    return pd.read_csv(REF / "clients.csv", dtype=str).fillna("")


def load(cid: str, name: str):
    p = OUT / cid / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def queue() -> pd.DataFrame:
    """
    One row per client, always with the same columns. A client with no metrics
    yet gets empty cells rather than a shorter row: a partially populated queue
    is the normal state on a fresh install, and the table must survive it.
    """
    cols = ["Cliente", "_id", "Perfil", "Patrimônio", "Retorno", "Caixa %", "Achados", "Carta"]
    rows = []
    for _, c in clients().iterrows():
        cid = c["client_id"]
        row = dict.fromkeys(cols)
        row |= {"Cliente": c["name"], "_id": cid, "Perfil": c["profile"],
                "Carta": "pronta" if (OUT / cid / "letter.pdf").exists() else "pendente"}
        pack, rec = load(cid, "metrics_pack.json"), load(cid, "recommendations.json")
        if pack:
            row |= {"Patrimônio": pack["total_value_end"],
                    "Retorno": pack["total_return_pct"],
                    "Caixa %": pack["cash_pct_of_total"],
                    "Achados": len(rec["recommendations"]) if rec else 0}
        rows.append(row)
    df = pd.DataFrame(rows, columns=cols)
    return df.sort_values(["Achados", "Caixa %"], ascending=False, na_position="last")


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.subheader("Configuração")
    key = st.text_input("Chave da OpenAI", type="password", placeholder="sk-...",
                        help="Usada apenas nesta sessão. Vazia, usa OPENAI_API_KEY do ambiente.")
    model = st.selectbox("Modelo da carta", ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"], index=0)
    st.divider()
    st.caption("Período de referência: **abril de 2025**")
    st.caption("Assessor: **Antonio Bicudo** · A7699")

st.title("Relatórios mensais")
st.caption("Os números são calculados em código; o modelo apenas redige, e nenhuma cifra "
           "chega à carta sem constar da folha de cifras autorizada.")

# ---------------------------------------------------------------- queue
st.subheader("Fila de atendimento")
c1, c2 = st.columns([1, 3])
if c1.button("Atualizar fila", use_container_width=True):
    log = st.expander("Registro", expanded=False)
    for _, c in clients().iterrows():
        failed = run(TRIAGE, c["client_id"], log=log)
        if failed:
            st.error(f"Falhou em {failed} para {c['name']}."); st.stop()
    st.cache_data.clear()
    st.success("Fila atualizada.")
c2.caption("A triagem roda as regras para todos os clientes sem chamar o modelo. "
           "A carta é gerada só para quem o assessor abrir.")

q = queue()
if q["Achados"].isna().all():
    st.info("Nenhuma métrica calculada ainda. Clique em **Atualizar fila**.")
else:
    st.dataframe(
        q.drop(columns=["_id"]), use_container_width=True, hide_index=True,
        column_config={
            "Patrimônio": st.column_config.NumberColumn(format="R$ %.2f"),
            "Retorno": st.column_config.NumberColumn(format="%.2f%%"),
            "Caixa %": st.column_config.NumberColumn(format="%.2f%%"),
            "Achados": st.column_config.NumberColumn(
                help="Desvios de adequação encontrados pelo motor de regras"),
        })

# ---------------------------------------------------------------- detail
st.divider()
st.subheader("Cliente")
opts = q.dropna(subset=["Achados"])["_id"].tolist() or clients()["client_id"].tolist()
labels = dict(zip(clients()["client_id"], clients()["name"]))
cid = st.selectbox("Selecione", opts, format_func=lambda x: labels.get(x, x))

pack, rec = load(cid, "metrics_pack.json"), load(cid, "recommendations.json")
plan, rep = load(cid, "rebalance_plan.json"), load(cid, "verification_report.json")

if pack:
    k = st.columns(4)
    k[0].metric("Patrimônio", brl(pack["total_value_end"]), f"{pack['total_return_pct']:.2f}%")
    k[1].metric("Recursos investidos", f"{pack['invested_return_pct']:.2f}%")
    k[2].metric(f"Parâmetro ({pack['benchmark_name']})", f"{pack['benchmark_return_pct']:.2f}%",
                f"{pack['excess_return_pp']:+.2f} p.p.")
    k[3].metric("Cobertura de marcação", f"{pack['coverage_pct']:.2f}%")

    if rec and rec["recommendations"]:
        with st.expander(f"Desvios de adequação — {len(rec['recommendations'])}", expanded=True):
            for r in rec["recommendations"]:
                st.markdown(f"**{r['rule_id']}** · {r['action']}  \n"
                            f"{r['observed']} — limite: {r['threshold']}  \n"
                            f"<span style='color:#666'>{r['policy_source']}</span>",
                            unsafe_allow_html=True)
    elif rec:
        st.success("Nenhum desvio de adequação. Carteira aderente ao mandato.")

    if plan and plan["trades"]:
        with st.expander(f"Plano de rebalanceamento — {len(plan['trades'])} operações"):
            verbo = {"sell": "Vender", "redeem": "Resgatar", "buy": "Aplicar"}
            st.dataframe(pd.DataFrame([{
                "Operação": verbo[t["action"]],
                "Ativo ou destino": t["instrument_id"] or f"{t['category']} — {t['criteria']}",
                "Valor": brl(t["amount_brl"])} for t in plan["trades"]]),
                use_container_width=True, hide_index=True)
            st.caption("Violações antes: "
                       f"{len(plan['violations_before'])} · depois: {len(plan['violations_after'])}")

    if pack.get("issues"):
        with st.expander(f"Qualidade de dados — {len(pack['issues'])}"):
            for i in pack["issues"]:
                st.write(f"`{i['severity']}` **{i['code']}** — {i['message']}")

# ---------------------------------------------------------------- letter
st.divider()
if st.button(f"Gerar carta de {labels.get(cid, cid)}", type="primary",
             use_container_width=True):
    if not (key or os.environ.get("OPENAI_API_KEY")):
        st.error("Informe a chave da OpenAI na barra lateral."); st.stop()
    log = st.expander("Registro de execução", expanded=True)
    failed = run(LETTER, cid, key=key, model=model, log=log)
    if failed:
        st.error(f"Pipeline interrompido em **{failed}**. O registro mostra o motivo.")
    else:
        st.success("Carta gerada.")
        st.cache_data.clear()

if rep is not None:
    blockers = [r for r in rep if r["severity"] == "blocker"]
    if blockers:
        st.error(f"Verificação reprovou a carta: {len(blockers)} bloqueio(s).")
        for r in blockers:
            st.write(f"**{r['code']}** — {r['message']}")
    else:
        st.info("Verificação aprovada: toda cifra da carta consta da folha autorizada.")

pdf_p, html_p = OUT / cid / "letter.pdf", OUT / cid / "letter.html"
if pdf_p.exists() or html_p.exists():
    cols = st.columns(2)
    if pdf_p.exists():
        cols[0].download_button("Baixar PDF", pdf_p.read_bytes(),
                                f"relatorio_{cid.lower()}.pdf", "application/pdf",
                                use_container_width=True)
    elif cols[0].button("Gerar PDF", use_container_width=True):
        # Re-runs only the rendering stages: the letter text is already written
        # and verified, so this never calls the model and costs nothing.
        log = st.expander("Registro da renderização", expanded=True)
        failed = run(RENDER, cid, log=log)
        if failed:
            st.warning("Não foi possível gerar o PDF neste ambiente. "
                       "Use o HTML e imprima pelo navegador (Ctrl+P → Salvar como PDF): "
                       "o template já traz as regras de impressão em A4.")
        else:
            st.rerun()
    if html_p.exists():
        cols[1].download_button("Baixar HTML", html_p.read_bytes(),
                                f"relatorio_{cid.lower()}.html", "text/html",
                                use_container_width=True)
        with st.expander("Pré-visualizar", expanded=not pdf_p.exists()):
            st_html(html_p.read_text(encoding="utf-8"), height=900, scrolling=True)
