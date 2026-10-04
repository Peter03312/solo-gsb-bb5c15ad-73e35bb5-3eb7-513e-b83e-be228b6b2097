"""API 集成测试：使用 TestClient + 临时 SQLite 文件，互不依赖演示数据。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("SEED_DEMO_DATA", "false")

    # 延迟导入，确保读取到被 monkeypatch 改写后的配置
    from app import config
    from app import database

    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(config, "SEED_DEMO_DATA", False)
    database.init_db(db_path)

    from app.main import app

    with TestClient(app) as c:
        yield c


def _reg(client, vid, parents=None):
    return client.post(
        "/versions", json={"versionId": vid, "parentIds": parents or []}
    )


def _lc(client, cid, vid, at, uses):
    return client.post(
        "/license-changes",
        json={
            "changeId": cid,
            "versionId": vid,
            "effectiveAt": at,
            "allowedUses": uses,
        },
    )


def _check(client, vid, use, at):
    return client.get("/check", params={"versionId": vid, "use": use, "at": at})


# ---------------------------------------------------------------------------
# 版本登记
# ---------------------------------------------------------------------------

def test_register_versions_and_closure(client):
    assert _reg(client, "v1").status_code == 201
    assert _reg(client, "v2", ["v1"]).status_code == 201
    # 多父：v4 引用 v2 与 v3，形成菱形依赖（共同祖先 v1 只应出现一次）
    assert _reg(client, "v3", ["v1"]).status_code == 201
    assert _reg(client, "v4", ["v2", "v3"]).status_code == 201

    detail = client.get("/versions/v4").json()
    assert detail["parentIds"] == ["v2", "v3"]
    assert detail["ancestorIds"].count("v1") == 1
    assert set(detail["ancestorIds"]) == {"v1", "v2", "v3"}


def test_register_unknown_parent_rejected(client):
    resp = _reg(client, "x", ["ghost"])
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


def test_register_self_parent_rejected(client):
    # 全新版本不能引用尚未创建的自身（父版本必须已存在，因此为 422）
    resp = _reg(client, "v1", ["v1"])
    assert resp.status_code == 422


def test_reregister_with_self_parent_conflicts(client):
    # 已存在的 v1（无父）改成引用自身：父引用不同 → 409，且引用不可变
    _reg(client, "v1")
    resp = _reg(client, "v1", ["v1"])
    assert resp.status_code == 409


def test_reregister_same_parents_is_idempotent(client):
    _reg(client, "v1")
    _reg(client, "v2", ["v1"])
    resp = _reg(client, "v2", ["v1"])
    assert resp.status_code == 200
    assert resp.json()["replayed"] is True


def test_reregister_different_parents_conflicts(client):
    _reg(client, "v1")
    _reg(client, "v2")
    _reg(client, "v3", ["v1"])
    resp = _reg(client, "v3", ["v1", "v2"])
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "CONFLICT"
    assert body["details"]["existingParentIds"] == ["v1"]


# ---------------------------------------------------------------------------
# 许可变更：幂等与冲突
# ---------------------------------------------------------------------------

def test_change_idempotent_same_content(client):
    _reg(client, "v1")
    body = {
        "changeId": "C1",
        "versionId": "v1",
        "effectiveAt": "2026-01-01T00:00:00+00:00",
        "allowedUses": ["research", "commercial"],
    }
    r1 = client.post("/license-changes", json=body)
    assert r1.status_code == 201
    assert r1.json()["replayed"] is False

    # 用途换序属于同一内容（集合语义），仍幂等
    body["allowedUses"] = ["commercial", "research"]
    r2 = client.post("/license-changes", json=body)
    assert r2.status_code == 200
    assert r2.json()["replayed"] is True
    assert r2.json()["contentHash"] == r1.json()["contentHash"]


def test_change_conflict_same_id_different_content(client):
    _reg(client, "v1")
    _lc(client, "C1", "v1", "2026-01-01T00:00:00+00:00", ["research"])
    resp = _lc(client, "C1", "v1", "2026-01-01T00:00:00+00:00", ["research", "commercial"])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


def test_change_conflict_same_version_same_time_different_change(client):
    _reg(client, "v1")
    _lc(client, "C1", "v1", "2026-01-01T00:00:00+00:00", ["research"])
    resp = _lc(client, "C2", "v1", "2026-01-01T00:00:00+00:00", ["commercial"])
    assert resp.status_code == 409


def test_change_for_unknown_version_is_404(client):
    resp = _lc(client, "C1", "ghost", "2026-01-01T00:00:00+00:00", ["research"])
    assert resp.status_code == 404


def test_naive_datetime_rejected(client):
    _reg(client, "v1")
    resp = client.post(
        "/license-changes",
        json={
            "changeId": "C1",
            "versionId": "v1",
            "effectiveAt": "2026-01-01T00:00:00",  # 无时区
            "allowedUses": ["research"],
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 许可判定
# ---------------------------------------------------------------------------

def _build_chain(client):
    _reg(client, "v1")
    _reg(client, "v2", ["v1"])
    _reg(client, "v3", ["v2"])
    _lc(client, "C1", "v1", "2026-01-01T00:00:00+00:00", ["research", "commercial"])
    _lc(client, "C2", "v2", "2026-01-01T00:00:00+00:00", ["research", "education"])
    _lc(client, "C3", "v3", "2026-01-01T00:00:00+00:00", ["research"])


def test_check_allowed_uses_intersection(client):
    _build_chain(client)
    resp = _check(client, "v3", "research", "2026-03-01T00:00:00+00:00")
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is True
    assert body["blockingVersions"] == []
    assert body["consideredVersions"] == ["v3", "v2", "v1"]
    assert body["intersectionAllowedUses"] == ["research"]
    assert {b["changeId"] for b in body["basisChanges"]} == {"C1", "C2", "C3"}


def test_check_denied_lists_all_blocking_versions(client):
    _build_chain(client)
    resp = _check(client, "v3", "commercial", "2026-03-01T00:00:00+00:00")
    body = resp.json()
    assert body["allowed"] is False
    blockers = {b["versionId"]: b["reason"] for b in body["blockingVersions"]}
    assert blockers == {"v2": "USE_NOT_ALLOWED", "v3": "USE_NOT_ALLOWED"}
    # v1 仍提供依据
    assert any(b["versionId"] == "v1" for b in body["basisChanges"])


def test_check_latest_change_not_later_than_at(client):
    _build_chain(client)
    _lc(client, "C2B", "v2", "2026-06-01T00:00:00+00:00", ["commercial"])
    # 收紧前：research 允许
    before = _check(client, "v3", "research", "2026-05-31T23:59:59+00:00").json()
    assert before["allowed"] is True

    # 收紧后：v2 只允许 commercial，research 在 v2/v3 被阻断
    after = _check(client, "v3", "research", "2026-06-01T00:00:00+00:00").json()
    assert after["allowed"] is False
    v2_basis = next(b for b in after["basisChanges"] if b["versionId"] == "v2")
    assert v2_basis["changeId"] == "C2B"


def test_check_denied_when_version_has_no_effective_license(client):
    _build_chain(client)
    # 在任何变更生效之前查询：三个版本都没有有效许可
    resp = _check(client, "v3", "research", "2025-12-31T00:00:00+00:00")
    body = resp.json()
    assert body["allowed"] is False
    assert {b["versionId"] for b in body["blockingVersions"]} == {"v1", "v2", "v3"}
    assert all(b["reason"] == "NO_EFFECTIVE_LICENSE" for b in body["blockingVersions"])
    assert body["basisChanges"] == []


def test_check_unknown_version_404(client):
    resp = _check(client, "ghost", "research", "2026-03-01T00:00:00+00:00")
    assert resp.status_code == 404


def test_check_accepts_timezone_offset_and_normalizes(client):
    _build_chain(client)
    # 2026-03-01T08:00:00+08:00 == 2026-03-01T00:00:00Z
    resp = _check(client, "v3", "research", "2026-03-01T08:00:00+08:00")
    assert resp.status_code == 200
    assert resp.json()["at"].endswith("+00:00")
    assert resp.json()["allowed"] is True


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
