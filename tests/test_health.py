"""阶段 0 冒烟测试：应用可启动，健康检查可用，运行模式如实回显。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "AegisFlow"
    assert body["run_mode"] in {"mock", "real"}
    assert body["environment"] in {"local", "test", "ci"}
