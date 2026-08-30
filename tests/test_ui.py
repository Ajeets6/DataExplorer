from pathlib import Path

from streamlit.testing.v1 import AppTest

from dataexplorer.ui import UiIdentity, _csv


def test_ui_identity_supports_development_and_bearer_auth() -> None:
    headers = UiIdentity("analyst", "acme", "finance", "token").headers()
    assert headers["X-Tenant-ID"] == "acme"
    assert headers["Authorization"] == "Bearer token"
    assert _csv("finance, operations, ") == ["finance", "operations"]


def test_streamlit_workspace_renders_without_runtime_errors() -> None:
    ui_path = Path(__file__).parents[1] / "src" / "dataexplorer" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=10).run()
    assert not app.exception
    assert any("Data Explorer" in markdown.value for markdown in app.markdown)
    assert len(app.tabs) == 5


def test_streamlit_observability_console_renders_without_runtime_errors() -> None:
    ui_path = Path(__file__).parents[1] / "src" / "dataexplorer" / "ui_admin.py"
    app = AppTest.from_file(str(ui_path), default_timeout=10).run()
    assert not app.exception
    assert any("LLM observability" in markdown.value for markdown in app.markdown)
