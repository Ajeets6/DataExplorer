"""Employee workspace: record-driven navigation and authorized business workflows."""
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode
from uuid import uuid4

import httpx
import streamlit as st


@dataclass(frozen=True, slots=True)
class UiIdentity:
    user_id: str
    tenant_id: str
    groups: str
    token: str = ""

    @classmethod
    def from_environment(cls, token: str = ""):
        return cls(os.getenv("DATAEXPLORER_UI_DEV_USER", "analyst-1"),
                   os.getenv("DATAEXPLORER_UI_DEV_TENANT", "acme"),
                   os.getenv("DATAEXPLORER_UI_DEV_GROUPS", "finance,content-approvers,data-approvers"), token)

    def headers(self):
        headers = {"X-User-ID": self.user_id, "X-Tenant-ID": self.tenant_id, "X-Groups": self.groups}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


class ApiClient:
    def __init__(self, base_url: str, identity: UiIdentity):
        self.base_url = base_url.rstrip("/")
        self.identity = identity

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None):
        try:
            with httpx.Client(base_url=self.base_url, timeout=120) as client:
                response = client.request(method, path, headers=self.identity.headers(), json=payload)
        except httpx.HTTPError:
            raise RuntimeError("Cannot reach the workspace. Check your connection and try again.") from None
        if not response.is_success:
            messages = {401: "Your session has expired. Sign in again.",
                        403: "You do not have permission to complete this action.",
                        404: "This record is unavailable or you no longer have access.",
                        409: "This record has changed or is not ready. Refresh and try again.",
                        422: "Check the required fields and source references, then try again.",
                        429: "The workspace usage limit has been reached. Please try again later."}
            message = messages.get(response.status_code, "The request could not be completed. Please try again.")
            correlation = response.headers.get("X-Correlation-ID")
            if correlation:
                message += f" Support reference: {correlation}"
            raise RuntimeError(message)
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            return response.json()
        return response.text if content_type.startswith("text/") else response.content


def _load(client, path):
    try:
        return client.request("GET", path)
    except RuntimeError as error:
        st.error(str(error))
        return None


def _action(client, path, payload=None):
    try:
        with st.spinner("Saving your changes…"):
            result = client.request("POST", path, payload)
        st.toast("Changes saved")
        return result
    except RuntimeError as error:
        st.error(str(error))
        return None


def _csv(value):
    return [part.strip() for part in value.split(",") if part.strip()]


def _date(value):
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y · %H:%M UTC")
    except (ValueError, TypeError):
        return "—"


def _safe(text):
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", str(text))


def _header(title, subtitle):
    st.title(title)
    st.caption(subtitle)


def _empty(title, body):
    with st.container(border=True):
        st.subheader(title)
        st.caption(body)


def _go(name, record=None, **params):
    if record:
        params["record"] = record
    st.switch_page(st.session_state["workspace_pages"][name], query_params=params)


def _connection_panel():
    api_url = os.getenv("DATAEXPLORER_API_URL", "http://127.0.0.1:8000")
    forwarded = st.context.headers.get("Authorization", "")
    token = forwarded[7:] if forwarded.lower().startswith("bearer ") else ""
    if not token and os.getenv("DATAEXPLORER_AUTH_MODE", "development") == "jwt":
        _header("Sign in to Data Explorer", "Use your organization's secure workspace connection.")
        login_url = os.getenv("DATAEXPLORER_LOGIN_URL", "")
        if login_url.startswith("https://"):
            st.link_button("Sign in with your organization", login_url, type="primary")
        else:
            st.info("Enterprise sign-in is not configured. Ask your administrator to connect the identity gateway.")
        st.stop()
    client = ApiClient(api_url, UiIdentity.from_environment(token))
    return client, _load(client, "/v1/workspace/me")


def main():
    st.set_page_config(page_title="Data Explorer", page_icon=":material/space_dashboard:",
                       layout="wide", initial_sidebar_state="expanded")
    _theme()
    with st.sidebar:
        st.markdown('<div class="brand"><b>Data Explorer</b><span>BUSINESS WORKSPACE</span></div>', unsafe_allow_html=True)
    client, workspace = _connection_panel()
    if workspace is None:
        _header("Workspace unavailable", "Your work has not been changed.")
        if st.button("Try again", type="primary"):
            st.rerun()
        return
    identity_key = (workspace["tenant_id"], workspace["user_id"], tuple(workspace["groups"]))
    if st.session_state.get("identity_key") != identity_key:
        for key in list(st.session_state):
            del st.session_state[key]
        st.session_state.identity_key = identity_key
    definitions = [
        ("Overview", "overview", "space_dashboard", lambda: _overview(client, workspace)),
        ("Ask", "ask", "forum", lambda: _ask(client)),
        ("Knowledge", "knowledge", "folder_open", lambda: _knowledge(client, workspace)),
        ("Analyses", "analyses", "bar_chart", lambda: _analyses(client, workspace)),
        ("Reports", "reports", "description", lambda: _reports(client, workspace)),
        ("Approvals", "approvals", "task_alt", lambda: _reports(client, workspace, approvals=True)),
    ]
    pages = {name: st.Page(fn, title=name, url_path=path, icon=f":material/{icon}:", default=name == "Overview")
             for name, path, icon, fn in definitions}
    st.session_state.workspace_pages = pages
    selected = st.navigation(list(pages.values()), position="hidden")
    with st.sidebar:
        for name, _, icon, _ in definitions:
            st.page_link(pages[name], label=name, icon=f":material/{icon}:")
        st.divider()
        st.caption("WORKSPACE")
        st.write(workspace["tenant_id"])
        st.caption(f"Signed in as {workspace['user_id']}")
        st.caption(workspace.get("environment", "development").title())
        with st.expander("Workspace information"):
            st.caption("Your organization manages access to sources and decisions.")
            if not workspace.get("persistent"):
                st.caption("Development storage: records survive page refreshes, but reset when the API restarts.")
    notice = st.session_state.pop("notice", None)
    if notice:
        st.success(notice)
    selected.run()


def _overview(client, workspace):
    _header("Overview", "Your work, decisions, and knowledge in one place.")
    summary = _load(client, "/v1/workspace/overview")
    if summary is None:
        if st.button("Refresh overview"):
            st.rerun()
        return
    cols = st.columns(4)
    for col, title, key, destination in zip(cols,
        ["Awaiting your review", "Your reports", "Saved answers", "Accessible documents"],
        ["awaiting_review", "my_reports", "saved_answers", "documents"],
        ["Approvals", "Reports", "Ask", "Knowledge"]):
        with col, st.container(border=True):
            st.metric(title, summary[key])
            if st.button("View " + title.lower(), key=key, use_container_width=True):
                _go(destination, mine="true" if key == "my_reports" else "")
    st.caption("Live workspace totals · Updated " + _date(summary["updated_at"]))
    left, right = st.columns([2.2, 1], gap="large")
    with left:
        st.subheader("Recent work")
        reports = _load(client, "/v1/artifacts?limit=8")
        answers = _load(client, "/v1/library/answer?limit=8")
        if reports is not None and answers is not None:
            rows = [{"title": r["spec"]["title"], "status": r["status"], "created": r["created_at"],
                     "id": r["artifact_id"], "page": "Reports", "owner": r["requested_by"]} for r in reports["items"]]
            rows += [{"title": r["title"], "status": "saved answer", "created": r["created_at"],
                      "id": r["record_id"], "page": "Ask", "owner": r["owner"]} for r in answers["items"]]
            rows = sorted(rows, key=lambda r: r["created"], reverse=True)[:8]
            if not rows:
                _empty("Your next piece of work starts here", "Ask a question using your knowledge library, or create a report for review.")
            for row in rows:
                with st.container(border=True):
                    a, b = st.columns([4, 1])
                    a.markdown(f"**{_safe(row['title'])}**")
                    a.caption(f"{row['status'].title()} · {row['owner']} · {_date(row['created'])}")
                    if b.button("Open", key="recent-" + row["id"], use_container_width=True):
                        _go(row["page"], row["id"])
    with right:
        st.subheader("Start something")
        with st.container(border=True):
            if st.button("Ask a question", type="primary", use_container_width=True):
                _go("Ask")
            if st.button("Add knowledge", use_container_width=True):
                _go("Knowledge", create="true")
            if st.button("Create a report", use_container_width=True):
                _go("Reports", create="true")
        st.subheader("Needs your attention")
        with st.container(border=True):
            if summary["awaiting_review"]:
                st.write(f"{summary['awaiting_review']} report(s) are waiting for your decision.")
                if st.button("Open review queue"):
                    _go("Approvals")
            else:
                st.write("You're up to date")
                st.caption("No reports currently need your review.")


def _paged_library(client, kind):
    search = st.text_input("Search " + ("documents" if kind == "document" else "saved answers"), key="search-" + kind)
    page = st.number_input("Page", min_value=1, step=1, key="page-" + kind)
    return _load(client, f"/v1/library/{kind}?" + urlencode({"search": search, "offset": (page - 1) * 25, "limit": 25}))


def _ask(client):
    _header("Ask your knowledge", "Evidence-backed answers from the sources you can access.")
    left, right = st.columns([2.1, 1], gap="large")
    with left:
        question = st.chat_input("What would you like to understand?")
        if question:
            with st.spinner("Searching authorized sources and preparing your answer…"):
                try:
                    result = client.request("POST", "/v1/query", {"question": question})
                    st.session_state.latest_answer = {"question": question, "response": result}
                    st.query_params.pop("record", None)
                except RuntimeError as error:
                    st.error(str(error))
        selected = st.query_params.get("record")
        record = _load(client, f"/v1/library/answer/{quote(selected, safe='')}") if selected else None
        payload = record["payload"] if record else (st.session_state.get("latest_answer") if not selected else None)
        if payload:
            st.markdown("#### " + _safe(payload["question"]))
            response = payload["response"]
            with st.chat_message("assistant"):
                st.markdown(response["answer"])
            if not response["grounded"]:
                st.info("There was not enough supporting evidence. Try a more specific question or add a relevant document.")
            st.subheader("Sources")
            for citation in response.get("citations", []):
                with st.expander(citation["title"]):
                    source = _load(client, "/v1/library/document/" + quote(citation["document_id"], safe=""))
                    if source:
                        st.caption(f"Version {source['payload']['version']} · {source['payload']['classification'].title()}")
                        st.text(source["payload"]["text"])
        elif not selected:
            _empty("Start with a business question", "For example: What does our travel policy allow? Answers and source references are saved to your workspace.")
    with right:
        st.subheader("Saved answers")
        library = _paged_library(client, "answer")
        if library is not None:
            if not library["items"]:
                st.caption("No saved answers match this view.")
            for row in library["items"]:
                if st.button(row["title"], key="answer-" + row["record_id"], use_container_width=True):
                    _go("Ask", row["record_id"])
            if library["has_more"]:
                st.caption("More answers are available on the next page.")


def _knowledge(client, workspace):
    _header("Knowledge library", "Browse the business sources available to your workspace.")
    if st.toggle("Add a document", value=st.query_params.get("create") == "true"):
        with st.form("document-form"):
            st.subheader("Add business knowledge")
            title = st.text_input("Document title", placeholder="Travel and expense policy")
            uploaded = st.file_uploader("Upload a text or Markdown document", type=["txt", "md"])
            text = st.text_area("Or paste document content", height=180)
            a, b = st.columns(2)
            classification = a.selectbox("Classification", ["internal", "public", "confidential", "restricted"])
            trust = b.selectbox("Source status", ["approved-reference", "working-draft", "authoritative", "external", "untrusted"])
            allowed = st.multiselect("Groups that can access this document", workspace["groups"], default=workspace["groups"][:1])
            st.caption("Select at least one group. Upload supports UTF-8 text and Markdown, up to 1 MB.")
            submitted = st.form_submit_button("Add document", type="primary")
        if submitted:
            if uploaded:
                if uploaded.size > 1_000_000:
                    st.error("Choose a file smaller than 1 MB.")
                    return
                try:
                    text = uploaded.getvalue().decode("utf-8-sig")
                except UnicodeDecodeError:
                    st.error("Choose a UTF-8 text document.")
                    return
            if not title.strip() or not text.strip() or not allowed:
                st.error("Enter a title and content, and select an access group.")
            else:
                result = _action(client, "/v1/documents", {"document_id": str(uuid4()), "title": title.strip(),
                    "text": text, "classification": classification, "trust_tier": trust,
                    "allowed_groups": allowed, "version": "1"})
                if result:
                    st.session_state.notice = "Document indexed and ready to use."
                    _go("Knowledge", result["document_id"])
    library = _paged_library(client, "document")
    if library is None:
        return
    if not library["items"]:
        _empty("No documents in this view", "Add a document or adjust your search. Only sources you can access appear here.")
    for row in library["items"]:
        with st.container(border=True):
            a, b = st.columns([5, 1])
            a.markdown("**" + _safe(row["title"]) + "**")
            a.caption(f"Added by {row['owner']} · {_date(row['created_at'])}")
            if b.button("View", key="doc-" + row["record_id"], use_container_width=True):
                _go("Knowledge", row["record_id"])
    if library["has_more"]:
        st.caption("More documents are available on the next page.")
    selected = st.query_params.get("record")
    if selected:
        record = _load(client, "/v1/library/document/" + quote(selected, safe=""))
        if record:
            doc = record["payload"]
            st.subheader(doc["title"])
            st.caption(f"{doc['classification'].title()} · {doc['trust_tier']} · Version {doc['version']} · Indexed")
            with st.container(border=True):
                st.text(doc["text"])


def _reports(client, workspace, approvals=False):
    _header("Approvals" if approvals else "Reports", "Review the evidence and record your decision." if approvals else "Create, review, and download your business reports.")
    if approvals and not workspace.get("can_review_reports"):
        _empty("No review access", "Your workspace role does not include report approval.")
        return
    if not approvals and st.toggle("Create a report", value=st.query_params.get("create") == "true"):
        _create_report(client, workspace)
    search_col, status_col = st.columns([3, 1])
    search = search_col.text_input("Search reports", key="report-search")
    status = "pending" if approvals else status_col.selectbox("Status", ["All", "pending", "approved", "rejected", "rendered"])
    page = st.number_input("Page", min_value=1, step=1, key="report-page")
    mine = not approvals and st.checkbox("Only my reports", value=st.query_params.get("mine") == "true")
    response = _load(client, "/v1/artifacts?" + urlencode({"search": search, "status": "" if status == "All" else status,
        "offset": (page-1)*25, "mine": str(bool(mine)).lower(), "review_queue": str(approvals).lower()}))
    if response is None:
        return
    rows = response["items"]
    if not rows:
        _empty("No reports in this view", "Try another filter or create your first report." if not approvals else "No matching reports need your decision.")
    else:
        st.dataframe([{"Report": r["spec"]["title"], "Status": r["status"].title(), "Owner": r["requested_by"],
                       "Format": r["spec"]["kind"].upper(), "Created": _date(r["created_at"])} for r in rows],
                     use_container_width=True, hide_index=True)
        choices = {r["artifact_id"]: r for r in rows}
        chosen = st.selectbox("Open a report", list(choices), index=None,
                              format_func=lambda key: f"{choices[key]['spec']['title']} · {choices[key]['requested_by']}")
        if chosen and st.button("Open report"):
            _go("Approvals" if approvals else "Reports", chosen)
    if response["has_more"]:
        st.caption("More reports are available on the next page.")
    selected = st.query_params.get("record")
    if selected:
        draft = _load(client, f"/v1/artifacts/{quote(selected, safe='')}")
        if draft:
            _report_detail(client, workspace, draft)


def _create_report(client, workspace):
    sources = _load(client, "/v1/library/document?limit=100")
    if sources is None:
        return
    docs = {row["record_id"]: row["title"] for row in sources["items"]}
    with st.form("new-report"):
        st.subheader("New report")
        title = st.text_input("Report title", placeholder="Quarterly operating brief")
        a, b = st.columns(2)
        audience = a.text_input("Audience", placeholder="Operations leadership")
        purpose = b.text_input("Purpose", placeholder="Support the quarterly review")
        summary = st.text_area("Report content", height=180, placeholder="Write the summary your evidence supports.")
        selected = st.multiselect("Supporting documents", list(docs), format_func=docs.get)
        kind = st.selectbox("Format", workspace.get("report_formats", ["docx"]), format_func=str.upper)
        classification = st.selectbox("Report classification", ["internal", "public", "confidential", "restricted"])
        st.caption("The report goes to an independent reviewer. Check every claim against the selected sources.")
        submit = st.form_submit_button("Submit for review", type="primary")
    if submit:
        if not all(v.strip() for v in [title, audience, purpose, summary]) or not selected:
            st.error("Complete the report details and choose at least one supporting document.")
            return
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "business-report"
        draft = _action(client, "/v1/artifacts", {"kind": kind, "filename": f"{slug}.{kind}", "title": title,
            "audience": audience, "purpose": purpose, "classification": classification,
            "sections": [{"title": "Summary", "summary": summary, "source_ids": selected}],
            "sources": [{"source_id": key, "label": docs[key], "locator": "document://" + key} for key in selected]})
        if draft:
            st.session_state.notice = "Report submitted for independent review."
            _go("Reports", draft["artifact_id"])


def _report_detail(client, workspace, draft):
    spec = draft["spec"]
    st.divider()
    st.subheader(spec["title"])
    st.caption(f"{draft['status'].title()} · {spec['classification'].title()} · Requested by {draft['requested_by']} · {_date(draft['created_at'])}")
    left, right = st.columns([2, 1], gap="large")
    with left, st.container(border=True):
        st.caption("CONTENT PREVIEW")
        st.write(f"For {spec['audience']}")
        st.caption(spec["purpose"])
        for section in spec["sections"]:
            st.subheader(section["title"])
            st.write(section["summary"])
            for bullet in section.get("bullets", []):
                st.markdown("- " + _safe(bullet))
            if section.get("table"):
                table = section["table"]
                st.dataframe([dict(zip(table["columns"], row)) for row in table["rows"]], hide_index=True)
        st.caption("Content preview. Download the approved file to inspect its final document layout.")
    with right:
        with st.container(border=True):
            st.subheader("Supporting evidence")
            for source in spec["sources"]:
                with st.expander(source["label"]):
                    if source["locator"].startswith("document://"):
                        record = _load(client, "/v1/library/document/" + quote(source["locator"][11:], safe=""))
                        if record:
                            st.text(record["payload"]["text"])
                    else:
                        st.text(source["locator"])
        with st.container(border=True):
            st.subheader("Review & delivery")
            if draft.get("approved_by"):
                st.write(f"Decision by {draft['approved_by']}")
                st.caption(draft.get("approval_reason", ""))
            can_review = workspace.get("can_review_reports") and draft["requested_by"] != workspace["user_id"]
            if draft["status"] == "pending" and can_review:
                with st.form("decision-" + draft["artifact_id"]):
                    inspected = st.checkbox("I have reviewed the content and supporting evidence")
                    decision = st.selectbox("Decision", ["Choose a decision", "Approve", "Reject"])
                    reason = st.text_area("Reason for your decision", max_chars=1000)
                    submit = st.form_submit_button("Record decision", type="primary")
                if submit:
                    if not inspected or decision == "Choose a decision" or len(reason.strip()) < 3:
                        st.error("Review the evidence, choose a decision, and explain your reason.")
                    elif _action(client, f"/v1/artifacts/{draft['artifact_id']}/decision", {"approved": decision == "Approve", "reason": reason.strip()}):
                        st.rerun()
            elif draft["status"] == "pending":
                st.caption("Waiting for an independent reviewer. You cannot review your own report.")
            if draft["status"] == "approved" and st.button("Prepare download", type="primary"):
                with st.spinner("Preparing the approved document…"):
                    if _action(client, f"/v1/artifacts/{draft['artifact_id']}/render"):
                        st.rerun()
            if draft["status"] == "rendered" and st.button("Load approved file", type="primary"):
                file = _load(client, f"/v1/artifacts/{draft['artifact_id']}/download")
                if file is not None:
                    st.download_button("Download " + spec["kind"].upper(), file, file_name=spec["filename"], type="primary")
            if draft["status"] == "rejected":
                st.caption("Create a revised report using the reviewer's feedback and submit it for a new decision.")


def _analyses(client, workspace):
    _header("Analyses", "Ask business data questions, review proposed queries, and explore approved results.")
    schemas = _load(client, "/v1/sql/schemas")
    if schemas is None:
        return
    if not schemas:
        _empty("No data sources are connected", "Your administrator must configure an approved schema before analyses can run.")
        return
    policies = {s["name"]: s for s in schemas}
    with st.expander("Start an analysis"):
        with st.form("analysis"):
            question = st.text_area("Business question", placeholder="What is monthly revenue by region?")
            schema = st.selectbox("Data source", list(policies))
            submit = st.form_submit_button("Prepare analysis", type="primary")
        if submit:
            if not question.strip():
                st.error("Enter a business question.")
            else:
                result = _action(client, "/v1/sql/proposals", {"question": question, "schema_name": schema})
                if result:
                    _go("Analyses", result["proposal_id"])
    rows = _load(client, "/v1/sql/proposals")
    if rows is None:
        return
    search = st.text_input("Search analyses")
    rows = [r for r in rows if search.casefold() in r["question"].casefold()]
    st.caption("Latest 100 analyses available to your data-source permissions.")
    if not rows:
        _empty("No analyses in this view", "Start an analysis or adjust your search.")
        return
    by_id = {r["proposal_id"]: r for r in rows}
    st.dataframe([{"Question": r["question"], "Status": r["status"].title(), "Owner": r["requested_by"],
                   "Data source": r["schema_name"]} for r in rows], hide_index=True, use_container_width=True)
    chosen = st.selectbox("Open analysis", list(by_id), index=None, format_func=lambda key: by_id[key]["question"])
    if chosen and st.button("Open analysis details"):
        _go("Analyses", chosen)
    selected = st.query_params.get("record")
    if selected not in by_id:
        return
    proposal = by_id[selected]
    st.subheader(proposal["question"])
    st.caption(f"{proposal['status'].title()} · {proposal['schema_name']} · Requested by {proposal['requested_by']}")
    with st.expander("Query and execution limits", expanded=True):
        st.code(proposal["sql"], language="sql")
        st.caption(f"Read-only · Up to {policies[proposal['schema_name']]['maximum_rows']:,} rows")
    can_review = policies[proposal["schema_name"]]["can_approve"] and proposal["requested_by"] != workspace["user_id"]
    if proposal["status"] == "pending" and can_review:
        with st.form("sql-decision"):
            decision = st.selectbox("Decision", ["Choose a decision", "Approve", "Reject"])
            reason = st.text_area("Decision rationale")
            submit = st.form_submit_button("Record decision", type="primary")
        if submit:
            if decision == "Choose a decision" or len(reason.strip()) < 3:
                st.error("Choose a decision and provide a reason.")
            elif _action(client, f"/v1/sql/proposals/{selected}/decision", {"approved": decision == "Approve", "reason": reason}):
                st.rerun()
    elif proposal["status"] == "pending":
        st.info("Waiting for independent review.")
    if proposal.get("approval_reason"):
        st.caption(f"Review by {proposal['approved_by']}: {proposal['approval_reason']}")
    if proposal["status"] == "approved" and st.button("Run approved analysis", type="primary"):
        if _action(client, f"/v1/sql/proposals/{selected}/execute"):
            st.rerun()
    if proposal["status"] == "executed":
        record = _load(client, "/v1/library/result/" + quote(selected, safe=""))
        if record:
            result = record["payload"]
            st.caption(f"{result['row_count']:,} rows · Executed {_date(result['executed_at'])}")
            st.dataframe(result["rows"], use_container_width=True, hide_index=True)
            if result["truncated"]:
                st.info("Results were limited to the approved row limit.")


def _theme():
    from dataexplorer.ui_theme import STYLE
    st.markdown(STYLE, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
