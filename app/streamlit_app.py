"""Streamlit UI.  streamlit run app/streamlit_app.py"""

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rca_agent.collectors.spark import extract_evidence  # noqa: E402
from rca_agent.graph import RCAAgent  # noqa: E402
from rca_agent.loader import list_incidents, load_incident  # noqa: E402
from rca_agent.models import Incident  # noqa: E402
from rca_agent.report import render_markdown  # noqa: E402

st.set_page_config(page_title="Pipeline RCA Agent", page_icon="◆", layout="wide")

SEV_COLOUR = {"SEV1": "🔴", "SEV2": "🟠", "SEV3": "🟡"}


@st.cache_resource
def load_agent() -> RCAAgent:
    return RCAAgent()


agent = load_agent()

st.title("Pipeline RCA Agent")
st.caption("Reads Airflow metadata and Spark logs, classifies the failure, and writes the incident summary.")

with st.sidebar:
    st.subheader("Runtime")
    st.write(f"**Graph** `{agent.runtime}`")
    st.write(f"**Narrator** `{agent.llm.name}`")
    st.write(f"**Incident history** {len(agent.kb.incidents)} past incidents")
    st.write(f"**Runbooks** {len(agent.kb.runbooks)}")
    if agent.llm.name == "template":
        st.info(
            "No LLM key configured. Classification, severity and remediation are "
            "rule-based and unaffected — only the prose summary uses a template."
        )

tab_fixture, tab_paste = st.tabs(["Sample incidents", "Paste a log"])

with tab_fixture:
    fixtures = list_incidents()
    choice = st.selectbox("Incident", [p.name for p in fixtures])
    if choice:
        incident = load_incident(next(p for p in fixtures if p.name == choice))
        diagnosis = agent.diagnose(incident)
        cls = diagnosis.classification

        a, b, c, d = st.columns(4)
        a.metric("Class", cls.failure_class.value)
        b.metric("Confidence", f"{cls.confidence:.0%}")
        c.metric("Severity", f"{SEV_COLOUR.get(diagnosis.severity.value,'')} {diagnosis.severity.value}")
        d.metric("Latency", f"{diagnosis.latency_ms} ms")

        st.markdown(diagnosis.summary)
        if cls.rationale:
            st.caption(cls.rationale)

        left, right = st.columns([3, 2])
        with left:
            st.subheader("Recommended actions")
            for i, step in enumerate(diagnosis.remediation, 1):
                st.write(f"{i}. {step}")

            st.subheader("Evidence")
            if incident.evidence:
                st.dataframe(
                    [{"line": e.line_no, "signal": e.signal, "weight": e.weight, "excerpt": e.line[:90]}
                     for e in incident.evidence],
                    use_container_width=True, hide_index=True,
                )
            else:
                st.write("No log signals matched.")

        with right:
            st.subheader("Score breakdown")
            st.json(cls.scores)
            st.subheader("Similar past incidents")
            for sim in diagnosis.similar_incidents:
                with st.expander(f"{sim['id']} · {sim['failure_class']} · {sim['score']}"):
                    st.write(sim["summary"])
                    st.success(sim["resolution"])

        with st.expander("Full incident report (markdown)"):
            st.code(render_markdown(diagnosis), language="markdown")
        with st.expander("Raw log"):
            st.code(incident.log_excerpt or "(empty)", language="log")

with tab_paste:
    st.write("Paste a Spark driver or Airflow task log and the agent will classify it.")
    dag = st.text_input("DAG id", "my_pipeline")
    task = st.text_input("Failed task", "transform")
    regulatory = st.checkbox("Feeds regulatory reporting (raises severity to SEV1)")
    log_text = st.text_area("Log", height=240, placeholder="paste stack trace here…")

    if st.button("Diagnose", type="primary") and log_text.strip():
        incident = Incident(
            dag_id=dag, run_id="manual", failed_task=task,
            task_instances=[], evidence=extract_evidence(log_text),
            log_excerpt=log_text, dataset="", is_regulatory=regulatory,
        )
        diagnosis = agent.diagnose(incident)
        cls = diagnosis.classification
        if cls.failure_class.value == "unknown":
            st.warning("No known signal matched. Add a pattern to collectors/spark.py.")
        else:
            st.metric("Classification", f"{cls.failure_class.value} ({cls.confidence:.0%})")
            st.markdown(diagnosis.summary)
            for i, step in enumerate(diagnosis.remediation, 1):
                st.write(f"{i}. {step}")
