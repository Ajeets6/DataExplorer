import os
from typing import Any

import streamlit as st

from dataexplorer.ui import ApiClient, UiIdentity, _theme


def main() -> None:
    st.set_page_config(
        page_title="Data Explorer · Observability",
        page_icon="◉",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _theme()
    client = _admin_client()
    context = _load(client, "/v1/workspace/me")
    st.markdown(
        """
        <div class="masthead">
          <div><span class="eyebrow">CONTROL PLANE</span>
          <h1>LLM observability</h1></div>
          <div class="status-pill"><span></span> Sanitized telemetry</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not context:
        st.error("The workspace context server could not be reached.")
        return
    st.caption(
        f"Tenant {context['tenant_id']} · signed in as {context['user_id']} · "
        "prompt and response bodies are not retained"
    )
    if not context.get("can_observe"):
        st.error("Your server-resolved identity does not have observability access.")
        return

    summary = _load(client, "/v1/observability/summary") or {}
    top = st.columns(5)
    top[0].metric("LLM requests", summary.get("requests", 0))
    top[1].metric("Active users", summary.get("users", 0))
    top[2].metric("Tokens", f"{summary.get('total_tokens', 0):,}")
    top[3].metric("Estimated cost", f"${summary.get('estimated_cost_usd', 0):,.4f}")
    top[4].metric("Grounded", f"{summary.get('grounded_rate', 0) * 100:.1f}%")
    if summary.get("unpriced_requests"):
        st.info(
            f"{summary['unpriced_requests']} request(s) are unpriced. Configure the provider/model "
            "price catalog to include them in estimated cost."
        )

    traces, audit, users = st.tabs(["Model traces", "Audit log", "User activity"])
    with traces:
        st.markdown("## Model behaviour")
        st.caption("Provider, model, grounding, retries, tokens, latency, and estimated API cost.")
        rows = _load(client, "/v1/observability/traces?limit=250") or []
        _table_or_empty(rows, "No model traces yet. Run a grounded query in the workspace client.")
    with audit:
        st.markdown("## Policy and workflow events")
        rows = _load(client, "/v1/observability/audit?limit=250") or []
        _table_or_empty(rows, "No audit events yet for this tenant.")
    with users:
        st.markdown("## Tenant user activity")
        rows = _load(client, "/v1/observability/users") or []
        _table_or_empty(rows, "No user activity has been observed yet.")


def _admin_client() -> ApiClient:
    api_url = os.getenv("DATAEXPLORER_API_URL", "http://127.0.0.1:8000")
    with st.sidebar:
        st.markdown("### Admin connection")
        st.caption(api_url)
        token = st.text_input("JWT access token", type="password")
        st.caption("The API independently enforces tenant scope and admin groups.")
    identity = UiIdentity(
        user_id=os.getenv("DATAEXPLORER_ADMIN_DEV_USER", "platform-admin"),
        tenant_id=os.getenv("DATAEXPLORER_ADMIN_DEV_TENANT", "acme"),
        groups=os.getenv(
            "DATAEXPLORER_ADMIN_DEV_GROUPS",
            "observability-admins,platform-admins",
        ),
        token=token,
    )
    return ApiClient(api_url, identity)


def _load(client: ApiClient, path: str) -> Any | None:
    try:
        return client.request("GET", path)
    except RuntimeError as error:
        st.warning(str(error))
        return None


def _table_or_empty(rows: list[dict[str, Any]], message: str) -> None:
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info(message)


if __name__ == "__main__":
    main()
