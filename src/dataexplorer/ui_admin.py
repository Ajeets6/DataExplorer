"""Operational dashboard with explicit metric scope and no fabricated zeroes."""
import csv
import io
import os
from datetime import UTC, datetime, time, timedelta
from urllib.parse import urlencode

import streamlit as st

from dataexplorer.ui import ApiClient, UiIdentity, _theme, _load, _header, _empty, _date


def _admin_client():
    forwarded = st.context.headers.get("Authorization", "")
    token = forwarded[7:] if forwarded.lower().startswith("bearer ") else ""
    if not token and os.getenv("DATAEXPLORER_AUTH_MODE", "development") == "jwt":
        _header("Sign in to administration", "Use your organization's protected administration connection.")
        login_url = os.getenv("DATAEXPLORER_LOGIN_URL", "")
        if login_url.startswith("https://"):
            st.link_button("Sign in with your organization", login_url, type="primary")
        else:
            st.info("Ask your administrator to configure the identity gateway.")
        st.stop()
    identity = UiIdentity(os.getenv("DATAEXPLORER_ADMIN_DEV_USER", "platform-admin"),
        os.getenv("DATAEXPLORER_ADMIN_DEV_TENANT", "acme"),
        os.getenv("DATAEXPLORER_ADMIN_DEV_GROUPS", "observability-admins,platform-admins"), token)
    return ApiClient(os.getenv("DATAEXPLORER_API_URL", "http://127.0.0.1:8000"), identity)


def _percentage(value):
    return "—" if value is None else f"{value * 100:.1f}%"


def _export(rows):
    if not rows:
        return ""
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        # Neutralize spreadsheet formula prefixes in untrusted metadata.
        writer.writerow({k: "'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v for k, v in row.items()})
    return stream.getvalue()


def main():
    st.set_page_config(page_title="Data Explorer · Administration", page_icon=":material/monitoring:",
                       layout="wide", initial_sidebar_state="expanded")
    _theme()
    client = _admin_client()
    context = _load(client, "/v1/workspace/me")
    with st.sidebar:
        st.markdown('<div class="brand"><b>Data Explorer</b><span>ADMINISTRATION</span></div>', unsafe_allow_html=True)
    if context is None:
        _header("Administration unavailable", "We could not load your workspace. No metrics are being displayed.")
        if st.button("Try again"):
            st.rerun()
        return
    if not context.get("can_observe"):
        _header("Administration", "This area requires an administrator role.")
        st.error("Your account does not have access to operational data.")
        return
    with st.sidebar:
        section = st.radio("Administration", ["Overview", "Requests", "Usage & cost", "Audit", "User activity", "Quality"], label_visibility="collapsed")
        st.divider()
        st.caption("ENVIRONMENT")
        st.write(context["environment"].title())
        st.caption(f"Workspace: {context['tenant_id']}")
        st.caption(f"Signed in as {context['user_id']}")
        st.caption("Prompts and document bodies are excluded from telemetry.")
    _header("LLM observability" if section == "Overview" else section,
            "Understand service performance, investigate requests, and track usage.")
    a, b, c, d = st.columns([2, 1.4, 1.4, .8])
    today = datetime.now(UTC).date()
    dates = a.date_input("Date range · UTC", (today - timedelta(days=6), today), max_value=today)
    provider = b.text_input("Provider", placeholder="All providers")
    model = c.text_input("Model", placeholder="All models")
    d.write("")
    d.write("")
    if d.button("Refresh", use_container_width=True):
        st.rerun()
    if not isinstance(dates, (tuple, list)) or len(dates) != 2:
        st.info("Choose both the start and end date.")
        return
    start = datetime.combine(dates[0], time.min, UTC)
    end = datetime.combine(dates[1] + timedelta(days=1), time.min, UTC)
    if (end-start).days > 93:
        st.warning("Choose a range of up to 93 days.")
        return
    search, page = "", 1
    if section == "Requests":
        search = st.text_input("Search requests", placeholder="Correlation ID, user, status, provider, or model")
        page = st.number_input("Page", min_value=1, step=1)
    params = {"start": start.isoformat(), "end": end.isoformat(), "provider": provider.strip(),
              "model": model.strip(), "search": search, "offset": (page-1)*25}
    result = _load(client, "/v1/observability/dashboard?" + urlencode(params))
    if result is None:
        st.caption("Metrics are unavailable. Refresh to try again; zeroes are not substituted for failed requests.")
        return
    st.caption(f"Updated {_date(result['updated_at'])} · All matching records in the selected UTC period · Comparison: preceding equal-length period")
    if section == "Overview":
        _overview(result)
    elif section == "Requests":
        _requests(result)
    elif section == "Usage & cost":
        _cost(result)
    elif section == "User activity":
        st.caption("Model-generation activity only. Active users have at least one recorded generation attempt in this period.")
        _table_or_empty(result["users"], "No model activity in this period.")
    elif section == "Audit":
        _audit(client, start, end)
    else:
        _quality(client, result)


def _overview(result):
    cols = st.columns(5)
    previous = result["previous"]
    with cols[0], st.container(border=True, key="admin-kpi-0"):
        st.metric("Requests", f"{result['requests']:,}", f"{result['requests'] - previous['requests']:+,} vs previous")
    with cols[1], st.container(border=True, key="admin-kpi-1"):
        st.metric("Failed requests", _percentage(result["failure_rate"]))
        st.caption(f"{result['failed_requests']} of {result['requests']} requests")
    with cols[2], st.container(border=True, key="admin-kpi-2"):
        latency = result["p95_latency_ms"]
        st.metric("P95 response time", "—" if latency is None else f"{latency / 1000:.2f} s")
        st.caption(f"{result['latency_samples']} completed RAG responses")
    with cols[3], st.container(border=True, key="admin-kpi-3"):
        value = result["estimated_cost_usd"]
        st.metric("Estimated API cost", "—" if value is None else f"${value:,.4f}")
        st.caption("USD · Priced attempts only")
    with cols[4], st.container(border=True, key="admin-kpi-4"):
        st.metric("Grounded responses", _percentage(result["grounded_rate"]))
        st.caption(f"{result['grounded_samples']} eligible responses" if result["grounded_samples"] else "No eligible responses")
    if not result["requests"]:
        _empty("No model activity in this period", "Choose a different date range, or return after your team has used the knowledge assistant.")
        return
    if result["unpriced_attempts"]:
        st.info(f"{result['unpriced_attempts']} attempt(s) have no cost estimate. Totals exclude unknown usage or missing prices.")
    left, right = st.columns([2, 1], gap="large")
    with left:
        st.subheader("Generation activity")
        import pandas as pd
        data = pd.DataFrame(result["series"]).set_index("date")
        st.line_chart(data[["attempts", "failures"]], color=["#087e83", "#b34a4a"])
        st.caption("Generation attempts and failed attempts per UTC day. Retries count as separate attempts.")
    with right:
        st.subheader("Usage at a glance")
        with st.container(border=True):
            st.metric("Active users", result["active_users"])
            st.metric("Recorded tokens", f"{result['tokens']:,}")
            st.caption(f"{result['attempts']:,} attempts · {result['priced_attempts']:,} priced")
    st.subheader("Recent failed attempts")
    failed = [r for r in result["traces"] if r["status"] == "failed"]
    _table_or_empty(_trace_rows(failed), "No failed attempts among the latest 25 matching records. Open Requests to search the full period.")


def _trace_rows(traces):
    return [{"Time (UTC)": _date(r["occurred_at"]), "User": r["user_id"], "Operation": r["operation"],
             "Status": r["status"], "Provider": r["provider"], "Model": r["model"],
             "Tokens": r["total_tokens"], "Latency (ms)": r["latency_ms"],
             "Estimated USD": r["estimated_cost_usd"], "Correlation ID": r["correlation_id"]} for r in traces]


def _requests(result):
    st.caption(f"{result['trace_count']:,} matching records. Request summaries and generation attempts are labeled separately.")
    rows = _trace_rows(result["traces"])
    _table_or_empty(rows, "No requests match these filters.")
    if rows:
        st.download_button("Export this page", _export(rows), "requests.csv", "text/csv")
        traces = {r["trace_id"]: r for r in result["traces"]}
        selected = st.selectbox("Inspect request record", list(traces), index=None,
                               format_func=lambda key: f"{traces[key]['user_id']} · {traces[key]['operation']} · {_date(traces[key]['occurred_at'])}")
        if selected:
            trace = traces[selected]
            with st.container(border=True):
                st.subheader("Request details")
                st.write(f"{trace['provider']} / {trace['model']} · {trace['status'].title()}")
                st.caption("Correlation ID: " + trace["correlation_id"])
                st.write(f"Input tokens: {trace['input_tokens']:,} · Output tokens: {trace['output_tokens']:,}")
                st.write(f"Citations: {trace['citation_count']} · Reflection retries: {trace['reflection_attempts']}")
                st.caption("No prompt or response body is retained in this record.")
    if result["has_more"]:
        st.caption("More records are available on the next page.")


def _cost(result):
    a, b, c = st.columns(3)
    amount = result["estimated_cost_usd"]
    a.metric("Estimated API cost · USD", "—" if amount is None else f"${amount:,.4f}")
    b.metric("Recorded tokens", f"{result['tokens']:,}")
    c.metric("Unpriced attempts", result["unpriced_attempts"])
    st.caption("Estimates use configured provider/model prices. Missing token counts or prices remain unpriced. Local model API cost excludes hosting and infrastructure.")
    if result["series"]:
        import pandas as pd
        st.bar_chart(pd.DataFrame(result["series"]).set_index("date")[["estimated_cost_usd"]], color="#087e83")
    else:
        _empty("No cost data in this period", "No model attempts were recorded for these filters.")
    _table_or_empty(result["users"], "No user cost breakdown is available.")


def _audit(client, start, end):
    search = st.text_input("Search audit events", placeholder="User, action, outcome, or correlation ID")
    page = st.number_input("Audit page", min_value=1, step=1)
    response = _load(client, "/v1/observability/events?" + urlencode({"start": start.isoformat(), "end": end.isoformat(), "search": search, "offset": (page-1)*25}))
    if response is None:
        return
    rows = [{"Time (UTC)": _date(r["occurred_at"]), "Action": r["action"], "Decision": r["decision"],
             "User": r["user_id"], "Correlation ID": r["correlation_id"]} for r in response["items"]]
    _table_or_empty(rows, "No audit events match this period and search.")
    if rows:
        st.download_button("Export audit page", _export(rows), "audit.csv", "text/csv")
    if response["has_more"]:
        st.caption("More events are available on the next page.")


def _quality(client, result):
    st.metric("Grounded response rate", _percentage(result["grounded_rate"]))
    st.caption(f"Based on {result['grounded_samples']} eligible completed responses. Grounding is not an independent accuracy score.")
    with st.expander("Run a question set"):
        questions = st.text_area("Questions, one per line", height=140)
        if st.button("Run quality check", type="primary"):
            entries = [q.strip() for q in questions.splitlines() if q.strip()]
            if not entries or len(entries) > 20:
                st.error("Enter between 1 and 20 questions.")
                return
            rows = []
            progress = st.progress(0, text="Checking questions…")
            for index, question in enumerate(entries):
                try:
                    answer = client.request("POST", "/v1/query", {"question": question})
                    rows.append({"Question": question, "Grounded": answer["grounded"], "Citations": len(answer["citations"]), "Status": "Completed"})
                except RuntimeError:
                    rows.append({"Question": question, "Grounded": None, "Citations": None, "Status": "Unavailable"})
                progress.progress((index+1)/len(entries))
            st.dataframe(rows, use_container_width=True, hide_index=True)


def _table_or_empty(rows, message):
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        _empty("Nothing to display", message)


if __name__ == "__main__":
    main()
