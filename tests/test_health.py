from pathlib import Path

from fastapi.testclient import TestClient

from analystbench.api.app import create_app
from analystbench.config import Settings


def test_health_endpoints_are_available(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )
    with TestClient(create_app(settings)) as client:
        live = client.get("/api/v1/health/live")
        ready = client.get("/api/v1/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "database": "ready"}
    assert ready.headers["X-Request-ID"]
