"""验证启动期演示数据初始化（seed）可用且幂等。"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_seed_idempotent_and_queryable(tmp_path, monkeypatch):
    db_path = str(tmp_path / "seed.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("SEED_DEMO_DATA", "true")

    from app import config
    from app.seed import seed

    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(config, "SEED_DEMO_DATA", True)

    assert seed() is True       # 首次写入
    assert seed() is False      # 库非空，跳过

    from app.main import app

    with TestClient(app) as client:
        versions = [v["versionId"] for v in client.get("/versions").json()]
        assert versions == ["ds-v1", "ds-v2", "ds-v3"]

        resp = client.get(
            "/check",
            params={
                "versionId": "ds-v3",
                "use": "research",
                "at": "2026-03-01T00:00:00+00:00",
            },
        )
        assert resp.json()["allowed"] is True

        # 启动时 lifespan 再次执行 seed，不应产生重复或报错
        changes = client.get("/license-changes").json()
        ids = [c["changeId"] for c in changes]
        assert len(ids) == len(set(ids)) == 4
