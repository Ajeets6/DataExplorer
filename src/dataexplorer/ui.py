import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import streamlit as st


@dataclass(frozen=True, slots=True)
class UiIdentity:
    user_id: str
    tenant_id: str
    groups: str
    token: str = ""

    @classmethod
    def from_environment(cls, token: str = "") -> "UiIdentity":
        return cls(
            user_id=os.getenv("DATAEXPLORER_UI_DEV_USER", "analyst-1"),
            tenant_id=os.getenv("DATAEXPLORER_UI_DEV_TENANT", "acme"),
            groups=os.getenv(
                "DATAEXPLORER_UI_DEV_GROUPS",
                "finance,content-approvers,data-approvers",
            ),
            token=token,
        )

    def headers(self) -> dict[str, str]:
        headers = {
            "X-User-ID": self.user_id,
            "X-Tenant-ID": self.tenant_id,
            "X-Groups": self.groups,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


class ApiClient:
    def __init__(self, base_url: str, identity: UiIdentity) -> None:
        self.base_url = base_url.rstrip("/")
        self.identity = identity

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            with httpx.Client(base_url=self.base_url, timeout=120) as client:
                response = client.request(
                    method,
                    path,
                    headers=self.identity.headers(),
                    json=payload,
                )
        except httpx.HTTPError as error:
            raise RuntimeError(f"workspace server connection failed: {error}") from error
        if not response.is_success:
            detail = response.text
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            raise RuntimeError(f"{response.status_code}: {detail}")
        content_type = response.headers.get("content-type", "")
        return response.json() if "json" in content_type else response.text


def main() -> None:
    st.set_page_config(
        page_title="Data Explorer",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _theme()
    client, workspace = _connection_panel()
    workspace_label = (
        f"{workspace.get('tenant_id')} / {workspace.get('user_id')}"
        if workspace else "Workspace unavailable"
    )
    st.markdown(
        f"""
        <div class="masthead">
          <div class="brand"><span class="brand-mark">D</span><h1>Data Explorer</h1></div>
          <div class="workspace-meta"><span>Workspace</span><strong>{workspace_label}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    ask, knowledge, publish, structured, evaluate = st.tabs(
        ["Ask", "Knowledge", "Publish", "Structured data", "Evaluate"]
    )
    with ask:
        _ask(client)
    with knowledge:
        _knowledge(client)
    with publish:
        _publish(client)
    with structured:
        _structured_data(client)
    with evaluate:
        _evaluate(client)


def _connection_panel() -> tuple[ApiClient, dict[str, Any] | None]:
    default_api = os.getenv("DATAEXPLORER_API_URL", "http://127.0.0.1:8000")
    with st.sidebar:
        st.markdown("### Secure connection")
        st.caption(default_api)
        token = st.text_input("JWT access token", type="password", help="Optional in development.")
        st.caption("User, tenant, groups, and entitlements are resolved by the server.")
        st.divider()
        st.markdown(
            "<div class='guardrail'><b>Policy boundary</b><br>Sources, classifications, "
            "approvals and provider routes remain server-controlled.</div>",
            unsafe_allow_html=True,
        )
    client = ApiClient(default_api, UiIdentity.from_environment(token))
    try:
        workspace = client.request("GET", "/v1/workspace/me")
    except RuntimeError as error:
        workspace = None
        st.warning(f"Workspace server is unavailable: {error}")
    return client, workspace


def _ask(client: ApiClient) -> None:
    left, right = st.columns([1.55, 0.65], gap="large")
    with left:
        st.markdown("## Knowledge assistant")
        st.caption("Answers are generated only from evidence you are authorized to retrieve.")
        history = st.session_state.setdefault("chat_history", [])
        for message in history:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        question = st.chat_input("Ask a policy, operational, or analytical question")
        if question:
            history.append({"role": "user", "content": question})
            try:
                result = client.request("POST", "/v1/query", {"question": question})
                answer = result["answer"]
                history.append({"role": "assistant", "content": answer})
                st.rerun()
            except RuntimeError as error:
                st.error(str(error))
    with right:
        st.markdown("### Response controls")
        _contract_item("Authorized evidence", "Tenant and group filters run before generation.")
        _contract_item("Visible provenance", "Every grounded response carries source citations.")
        _contract_item("Fail closed", "Insufficient evidence or an ineligible model route stops the answer.")


def _knowledge(client: ApiClient) -> None:
    st.markdown("## Knowledge ingestion")
    st.caption("Register clean text with explicit access, classification, trust, and version metadata.")
    with st.form("knowledge-form"):
        a, b = st.columns(2)
        document_id = a.text_input("Document ID", "policy-001")
        title = b.text_input("Title", "Travel policy")
        text = st.text_area("Document text", height=220, placeholder="Paste approved business content…")
        c, d, e = st.columns(3)
        classification = c.selectbox("Classification", ["internal", "public", "confidential", "restricted"])
        trust = d.selectbox("Trust tier", ["authoritative", "approved-reference", "working-draft", "external", "untrusted"])
        version = e.text_input("Version", "1")
        allowed = st.text_input("Allowed groups", "finance")
        submitted = st.form_submit_button("Index document", type="primary", use_container_width=True)
    if submitted:
        try:
            result = client.request("POST", "/v1/documents", {
                "document_id": document_id,
                "title": title,
                "text": text,
                "classification": classification,
                "trust_tier": trust,
                "version": version,
                "allowed_groups": _csv(allowed),
            })
            st.success(f"Indexed {result['chunk_count']} governed chunks.")
            st.json(result)
        except RuntimeError as error:
            st.error(str(error))


def _publish(client: ApiClient) -> None:
    st.markdown("## Artifact publishing")
    create, review, render = st.columns(3, gap="large")
    with create:
        st.markdown("### Draft")
        title = st.text_input("Artifact title", "Quarterly operating brief")
        summary = st.text_area("Grounded summary", "Resolution time improved across the measured quarter.")
        source_id = st.text_input("Source ID", "ops")
        locator = st.text_input("Source locator", "warehouse://operations/monthly-resolution")
        kind = st.segmented_control("Format", ["docx", "pptx"], default="docx")
        if st.button("Create approval request", type="primary", use_container_width=True):
            payload = {
                "kind": kind,
                "filename": f"quarterly-operating-brief.{kind}",
                "title": title,
                "subtitle": "Governed performance summary",
                "audience": "Executive leadership",
                "purpose": "Support an operating review",
                "classification": "internal",
                "sections": [{
                    "title": "Performance summary",
                    "summary": summary,
                    "bullets": [],
                    "source_ids": [source_id],
                }],
                "sources": [{"source_id": source_id, "label": "Approved business source", "locator": locator}],
            }
            _action(client, "POST", "/v1/artifacts", payload, "artifact_draft")
    with review:
        st.markdown("### Review")
        artifact_id = st.text_input("Artifact ID", key="review-artifact")
        reason = st.text_area("Decision rationale", "Sources and claims reconciled.")
        decision = st.segmented_control("Decision", ["Approve", "Reject"], default="Approve")
        if st.button("Record decision", use_container_width=True):
            _action(client, "POST", f"/v1/artifacts/{artifact_id}/decision", {
                "approved": decision == "Approve", "reason": reason
            }, "artifact_decision")
        st.caption("The API rejects self-approval even if this screen is used by the requester.")
    with render:
        st.markdown("### Render")
        render_id = st.text_input("Approved artifact ID", key="render-artifact")
        if st.button("Render governed output", use_container_width=True):
            _action(client, "POST", f"/v1/artifacts/{render_id}/render", None, "artifact_render")
        result = st.session_state.get("artifact_render")
        if result:
            st.success("Artifact rendered and hashed")
            st.code(result.get("output_path", ""), language=None)


def _structured_data(client: ApiClient) -> None:
    st.markdown("## Structured data")
    st.caption("SQL is proposed, independently approved, validated, cost-bounded, and executed read-only.")
    a, b, c = st.columns(3, gap="large")
    with a:
        question = st.text_area("Business question", "What is monthly revenue by region?")
        schema = st.text_input("Approved schema", "finance")
        if st.button("Propose SQL", type="primary", use_container_width=True):
            _action(client, "POST", "/v1/sql/proposals", {"question": question, "schema_name": schema}, "sql")
    with b:
        proposal_id = st.text_input("Proposal ID", key="sql-approval")
        reason = st.text_input("Review rationale", "Query matches approved purpose")
        if st.button("Approve SQL", use_container_width=True):
            _action(client, "POST", f"/v1/sql/proposals/{proposal_id}/decision", {"approved": True, "reason": reason}, "sql")
    with c:
        execute_id = st.text_input("Approved proposal ID", key="sql-execute")
        if st.button("Execute read-only", use_container_width=True):
            _action(client, "POST", f"/v1/sql/proposals/{execute_id}/execute", None, "sql_result")
    if st.session_state.get("sql"):
        st.code(st.session_state["sql"].get("sql", ""), language="sql")
    if st.session_state.get("sql_result"):
        st.dataframe(st.session_state["sql_result"].get("rows", []), use_container_width=True)


def _evaluate(client: ApiClient) -> None:
    st.markdown("## Evaluation")
    st.caption("Run a small golden set and inspect grounding, citations, latency, and provider selection.")
    questions = st.text_area(
        "Questions, one per line",
        "What travel is reimbursable?\nWhat evidence supports the answer?",
        height=130,
    )
    if st.button("Run evaluation", type="primary"):
        rows = []
        for question in [value.strip() for value in questions.splitlines() if value.strip()]:
            started = time.perf_counter()
            try:
                result = client.request("POST", "/v1/query", {"question": question})
                rows.append({
                    "question": question,
                    "grounded": result["grounded"],
                    "citations": len(result["citations"]),
                    "provider": result["model_provider"],
                    "confidence": result["retrieval_confidence"],
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                })
            except RuntimeError as error:
                rows.append({"question": question, "grounded": False, "error": str(error)})
        st.session_state["evaluation"] = rows
    rows = st.session_state.get("evaluation", [])
    if rows:
        grounded = sum(bool(row.get("grounded")) for row in rows)
        m1, m2, m3 = st.columns(3)
        m1.metric("Grounded", f"{grounded}/{len(rows)}")
        m2.metric("Citations", sum(int(row.get("citations", 0)) for row in rows))
        m3.metric("Median latency", f"{sorted(int(row.get('latency_ms', 0)) for row in rows)[len(rows)//2]} ms")
        st.dataframe(rows, use_container_width=True, hide_index=True)


def _operations(client: ApiClient) -> None:
    st.markdown("## Operations and safeguards")
    a, b, c = st.columns(3)
    if st.button("Refresh service status", type="primary"):
        try:
            st.session_state["live"] = client.request("GET", "/health/live")
            st.session_state["ready"] = client.request("GET", "/health/ready")
            st.session_state["metrics"] = client.request("GET", "/metrics")
        except RuntimeError as error:
            st.session_state["ops_error"] = str(error)
    a.metric("API process", "Live" if st.session_state.get("live") else "Unknown")
    b.metric("Model route", "Ready" if st.session_state.get("ready") else "Unknown")
    c.metric("Telemetry", "Available" if st.session_state.get("metrics") else "Unknown")
    if st.session_state.get("ops_error"):
        st.error(st.session_state["ops_error"])
    with st.expander("Prometheus metrics"):
        st.code(st.session_state.get("metrics", "Refresh status to load metrics."), language=None)
    st.markdown("### Active control layers")
    st.dataframe([
        {"Layer": "Identity", "Control": "OIDC/JWT issuer, audience and JWKS validation"},
        {"Layer": "Retrieval", "Control": "Tenant and group filters before model access"},
        {"Layer": "Generation", "Control": "Classification-aware provider routing and citations"},
        {"Layer": "Publishing", "Control": "Independent approval, source register and SHA-256"},
        {"Layer": "Data", "Control": "Read-only SQL, allowlists, timeout and plan-cost ceiling"},
    ], hide_index=True, use_container_width=True)


def _action(client: ApiClient, method: str, path: str, payload: dict[str, Any] | None, key: str) -> None:
    try:
        result = client.request(method, path, payload)
        st.session_state[key] = result
        st.success("Request completed")
        st.json(result)
    except RuntimeError as error:
        st.error(str(error))


def _contract_item(title: str, body: str) -> None:
    st.markdown(
        f"<div class='contract'><div><b>{title}</b><p>{body}</p></div></div>",
        unsafe_allow_html=True,
    )


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _theme() -> None:
    st.markdown(
        """
        <style>
        :root {
          --navy:#142b3d;
          --navy-hover:#1e3a4f;
          --ink:#17242d;
          --muted:#5e6c76;
          --surface:#ffffff;
          --canvas:#f4f6f7;
          --line:#dce2e5;
          --line-strong:#c8d1d6;
          --accent:#087f8c;
          --accent-soft:#eaf5f6;
          --success:#26734d;
        }
        html, body, .stApp, [data-testid="stAppViewContainer"] {
          background:var(--canvas);
          color:var(--ink);
          font-family:Aptos, "Segoe UI", Arial, sans-serif;
        }
        [data-testid="stHeader"] { background:transparent; height:0; }
        [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu { display:none !important; }
        [data-testid="stSidebar"] { background:var(--navy); color:#f3f7f8; border-right:0; }
        [data-testid="stSidebar"] * { color:#f3f7f8 !important; }
        [data-testid="stSidebar"] input { color:var(--ink) !important; background:#fff !important; }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color:#bdcbd2 !important; }
        .block-container { max-width:1280px; padding:1.4rem 2.5rem 4rem; }
        .masthead {
          min-height:72px;
          display:flex;
          align-items:center;
          justify-content:space-between;
          gap:2rem;
          padding:0 0 1.15rem;
          border-bottom:1px solid var(--line-strong);
          margin-bottom:.15rem;
        }
        .brand { display:flex; align-items:center; gap:.8rem; min-width:0; }
        .brand-mark {
          width:34px;
          height:34px;
          display:grid;
          place-items:center;
          flex:0 0 auto;
          border-radius:7px;
          background:var(--navy);
          color:#fff;
          font-size:.96rem;
          font-weight:750;
        }
        .masthead h1 {
          margin:0;
          color:var(--navy);
          font-size:1.45rem;
          font-weight:720;
          letter-spacing:-.025em;
          line-height:1.1;
        }
        .workspace-meta { display:flex; flex-direction:column; align-items:flex-end; line-height:1.25; }
        .workspace-meta span { color:var(--muted); font-size:.7rem; font-weight:650; text-transform:uppercase; letter-spacing:.07em; }
        .workspace-meta strong { color:var(--ink); font-size:.83rem; font-weight:650; }
        h1, h2, h3, p, label, [data-testid="stCaptionContainer"] { color:var(--ink); }
        h2 { font-size:1.7rem !important; font-weight:700 !important; letter-spacing:-.025em !important; margin-top:1.9rem !important; }
        h3 { font-size:1rem !important; font-weight:700 !important; letter-spacing:-.01em !important; }
        [data-testid="stCaptionContainer"] { color:var(--muted) !important; line-height:1.5; }
        button { border-radius:7px !important; min-height:2.55rem; font-weight:650 !important; }
        button[kind="primary"] {
          background:var(--navy) !important;
          border-color:var(--navy) !important;
          color:#fff !important;
        }
        button[kind="primary"] p, button[kind="primary"] span { color:#fff !important; }
        button[kind="primary"]:hover:not(:disabled) {
          background:var(--navy-hover) !important;
          border-color:var(--navy-hover) !important;
          color:#fff !important;
        }
        button[kind="primary"]:disabled,
        button:disabled {
          background:#e1e6e9 !important;
          border-color:#c8d1d6 !important;
          color:#485760 !important;
          opacity:1 !important;
          cursor:not-allowed !important;
        }
        button[kind="primary"]:disabled p, button[kind="primary"]:disabled span,
        button:disabled p, button:disabled span { color:#485760 !important; }
        [data-testid="stBaseButton-secondary"] {
          background:var(--surface) !important;
          border:1px solid var(--line-strong) !important;
          color:var(--navy) !important;
        }
        [data-testid="stBaseButton-secondary"] p,
        [data-testid="stBaseButton-secondary"] span { color:var(--navy) !important; }
        [data-testid="stBaseButton-secondary"]:hover:not(:disabled) {
          background:var(--accent-soft) !important;
          border-color:var(--accent) !important;
        }
        [data-testid="stButtonGroup"] button,
        [data-testid="stSegmentedControl"] button {
          min-height:2rem !important;
          background:var(--surface) !important;
          border-color:var(--line-strong) !important;
          color:var(--navy) !important;
        }
        [data-testid="stButtonGroup"] button p,
        [data-testid="stButtonGroup"] button span,
        [data-testid="stSegmentedControl"] button p,
        [data-testid="stSegmentedControl"] button span { color:var(--navy) !important; }
        [data-testid="stButtonGroup"] button:hover:not(:disabled),
        [data-testid="stSegmentedControl"] button:hover:not(:disabled) {
          background:var(--accent-soft) !important;
          border-color:var(--accent) !important;
        }
        [data-testid="stButtonGroup"] button[aria-checked="true"],
        [data-testid="stSegmentedControl"] button[aria-pressed="true"],
        [data-testid="stSegmentedControl"] button[data-selected="true"] {
          background:var(--accent-soft) !important;
          border-color:var(--accent) !important;
          box-shadow:inset 0 0 0 1px var(--accent) !important;
        }
        [data-testid="stButtonGroup"] button[aria-checked="true"] p,
        [data-testid="stButtonGroup"] button[aria-checked="true"] span,
        [data-testid="stSegmentedControl"] button[aria-pressed="true"] p,
        [data-testid="stSegmentedControl"] button[aria-pressed="true"] span,
        [data-testid="stSegmentedControl"] button[data-selected="true"] p,
        [data-testid="stSegmentedControl"] button[data-selected="true"] span {
          color:#075f68 !important;
          font-weight:700 !important;
        }
        button:focus-visible, input:focus-visible, textarea:focus-visible, [role="tab"]:focus-visible {
          outline:3px solid rgba(8,127,140,.28) !important;
          outline-offset:2px !important;
        }
        [data-baseweb="tab-list"] { gap:1.75rem; border-bottom:1px solid var(--line-strong); }
        [data-baseweb="tab"] { min-height:3.25rem; padding-left:.1rem !important; padding-right:.1rem !important; }
        [data-baseweb="tab"] p { color:var(--muted) !important; font-size:.88rem; font-weight:650; }
        [data-baseweb="tab"][aria-selected="true"] p { color:var(--navy) !important; }
        [data-baseweb="tab-highlight"], .react-aria-SelectionIndicator {
          background-color:var(--accent) !important;
          border-color:var(--accent) !important;
          height:2px !important;
        }
        [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea,
        [data-testid="stChatInput"] textarea, [data-baseweb="select"] > div {
          background:var(--surface) !important; color:var(--ink) !important;
          border-color:var(--line-strong) !important;
          border-radius:7px !important;
        }
        [data-testid="stTextInput"] input::placeholder, [data-testid="stTextArea"] textarea::placeholder,
        [data-testid="stChatInput"] textarea::placeholder { color:#707d86 !important; opacity:1; }
        [data-testid="stChatInput"] { background:var(--surface) !important; border:1px solid var(--line-strong) !important; border-radius:9px !important; }
        [data-testid="stChatInput"] > div, [data-testid="stChatInput"] div[data-baseweb="base-input"] {
          background:var(--surface) !important;
        }
        .contract { border-top:1px solid var(--line); padding:1rem 0; }
        .contract b { color:var(--navy); font-size:.9rem; }
        .contract p { color:var(--muted); line-height:1.48; margin:.3rem 0 0; font-size:.84rem; }
        .guardrail { background:#1c384b; border-left:3px solid #62b6b1; padding:1rem; color:#e5edef; font-size:.84rem; line-height:1.5; border-radius:0 7px 7px 0; }
        [data-testid="stMetric"] { background:var(--surface); border:1px solid var(--line); border-radius:9px; padding:1rem; box-shadow:0 1px 2px rgba(20,43,61,.04); }
        [data-testid="stForm"], [data-testid="stExpander"] { background:var(--surface); border:1px solid var(--line); border-radius:9px; }
        [data-testid="stForm"] { padding:1.2rem 1.25rem .35rem; }
        [data-testid="stDataFrame"] { border:1px solid var(--line); background:var(--surface); border-radius:8px; overflow:hidden; }
        [data-testid="stAlert"] { border-radius:8px; }
        hr { border-color:rgba(255,255,255,.16) !important; }
        @media (max-width: 767px) {
          .block-container { padding:1rem 1rem 3rem; }
          .masthead { min-height:60px; gap:1rem; }
          .masthead h1 { font-size:1.2rem; }
          .brand-mark { width:31px; height:31px; }
          .workspace-meta { display:none; }
          [data-baseweb="tab-list"] { gap:1.1rem; overflow-x:auto; }
          h2 { font-size:1.45rem !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
