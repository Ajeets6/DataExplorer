from pathlib import Path

from streamlit.testing.v1 import AppTest

from dataexplorer.ui import UiIdentity, _csv


def test_ui_identity_supports_development_and_bearer_auth() -> None:
    headers = UiIdentity("analyst", "acme", "finance", "token").headers()
    assert headers["X-Tenant-ID"] == "acme"
    assert headers["Authorization"] == "Bearer token"
    assert _csv("finance, operations, ") == ["finance", "operations"]


def test_streamlit_workspace_renders_without_runtime_errors(monkeypatch) -> None:
    from dataexplorer.ui import ApiClient
    def request(self, method, path, payload=None):
        if path == "/v1/workspace/me":
            return {"tenant_id": "acme", "user_id": "analyst", "groups": ["finance"], "persistent": False}
        if path == "/v1/workspace/overview":
            return {"my_reports": 0, "awaiting_review": 0, "saved_answers": 0, "documents": 0, "updated_at": "2026-09-10T12:00:00+00:00"}
        return {"items": [], "has_more": False}
    monkeypatch.setattr(ApiClient, "request", request)
    ui_path = Path(__file__).parents[1] / "src" / "dataexplorer" / "ui.py"
    app = AppTest.from_string("from dataexplorer.ui import main\nmain()", default_timeout=10).run()
    assert not app.exception
    assert any("Data Explorer" in markdown.value for markdown in app.markdown)
    assert any(title.value == "Overview" for title in app.title)
    assert len(app.metric) == 4
    assert len(app.tabs) == 0


def test_streamlit_observability_console_renders_without_runtime_errors(monkeypatch) -> None:
    from dataexplorer.ui import ApiClient
    from dataexplorer.telemetry import dashboard
    def request(self, method, path, payload=None):
        if path == "/v1/workspace/me":
            return {"tenant_id": "acme", "user_id": "admin", "groups": [], "can_observe": True, "environment": "development"}
        return {**dashboard([]), "previous": dashboard([]), "traces": [], "updated_at": "2026-09-10T12:00:00+00:00"}
    monkeypatch.setattr(ApiClient, "request", request)
    ui_path = Path(__file__).parents[1] / "src" / "dataexplorer" / "ui_admin.py"
    app = AppTest.from_file(str(ui_path), default_timeout=10).run()
    assert not app.exception
    assert any(title.value == "LLM observability" for title in app.title)
    assert app.metric[-1].value == "—"


def test_failed_overview_does_not_render_zero_metrics(monkeypatch):
    from dataexplorer.ui import ApiClient
    def request(self, method, path, payload=None):
        if path == "/v1/workspace/me":
            return {"tenant_id": "acme", "user_id": "analyst", "groups": []}
        raise RuntimeError("Service unavailable")
    monkeypatch.setattr(ApiClient, "request", request)
    app = AppTest.from_string("from dataexplorer.ui import main\nmain()").run()
    assert not app.exception
    assert len(app.metric) == 0
    assert any("Service unavailable" in error.value for error in app.error)
