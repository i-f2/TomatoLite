"""S0-4 断言（§0.7 反假绿）：八 app health / envelope 结构 / 软删基座 introspection /
integrations 零 Django 双重断言。**全程零 DB**（不挂 django_db）。"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APPS = ["core", "cmdb", "tickets", "channels_a", "channels_b",
        "verification", "feedback", "metrics"]


# ---- 1) 八 app 占位 health 全绿 ----

@pytest.mark.parametrize("name", APPS)
def test_app_health_200(client, name):
    r = client.get(f"/api/{name}/health/")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "app": name}


# ---- 2) 统一响应包装 ----

def test_envelope_shape():
    from apps.core.response import envelope
    e = envelope(data={"x": 1})
    assert set(e) == {"code", "message", "data", "request_id"}
    assert e["code"] == "OK" and e["data"] == {"x": 1}
    assert len(e["request_id"]) == 32            # uuid4 hex
    int(e["request_id"], 16)                      # 必须可解析为 hex


def test_exception_handler_envelopes():
    from rest_framework.exceptions import NotFound, ValidationError

    from apps.core.exceptions import AppError, app_exception_handler

    resp = app_exception_handler(
        AppError("锁被持有", code="TICKET_LOCKED", status_code=423), {})
    assert resp.status_code == 423
    assert resp.data["code"] == "TICKET_LOCKED" and resp.data["message"] == "锁被持有"
    assert resp.data["data"] is None and len(resp.data["request_id"]) == 32

    resp2 = app_exception_handler(NotFound(), {})
    assert resp2.status_code == 404 and resp2.data["code"] == "NOT_FOUND"

    resp3 = app_exception_handler(ValidationError({"f": ["必填"]}), {})
    assert resp3.status_code == 400 and resp3.data["code"] == "VALIDATION_ERROR"

    # 非 API 异常：返回 None 走 Django 500，**不吞、不伪装 envelope**
    assert app_exception_handler(ValueError("boom"), {}) is None


def test_pagination_shape_and_default_size():
    from apps.core.pagination import DefaultPagination

    assert DefaultPagination.page_size == 20  # S0-4 拍板默认


# ---- 3) AbstractBaseModel 软删基座（introspection，不触库） ----

def test_abstract_base_model_shape():
    from apps.core.models import AbstractBaseModel, SoftDeleteManager, SoftDeleteQuerySet

    assert AbstractBaseModel._meta.abstract
    names = {f.name for f in AbstractBaseModel._meta.get_fields()}
    assert {"created_at", "updated_at", "is_deleted", "deleted_at"} <= names
    # 抽象模型禁走描述符（Model.objects 会 raise），从 _meta.managers 拿声明件
    mgrs = {m.name: m for m in AbstractBaseModel._meta.managers}
    assert set(mgrs) >= {"objects", "all_objects"}             # 默认口软删 + 全量口审计
    assert type(mgrs["objects"]) is SoftDeleteManager
    assert "delete" in SoftDeleteQuerySet.__dict__             # 批量 delete = update 软删
    assert "delete" in AbstractBaseModel.__dict__              # 实例 delete 被覆写为软删
    assert hasattr(AbstractBaseModel, "soft_delete")
    assert hasattr(AbstractBaseModel, "hard_delete")


# ---- 4) integrations 零 Django 双重断言（§0.2 规则 1） ----

def test_integrations_source_free_of_django_import():
    offenders = []
    for p in (ROOT / "integrations").rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "import django" in line or "from django" in line:
                offenders.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not offenders, "integrations/ 出现 Django import：" + "; ".join(offenders)


def test_integrations_importable_without_django_subprocess():
    """干净子进程：无 DJANGO_SETTINGS_MODULE、未 setup——import 全部模块后
    'django' 不得出现在 sys.modules（防传递性引入）。"""
    code = (
        "import importlib, pathlib, sys;"
        "root = pathlib.Path('integrations');"
        "mods = sorted('.'.join(p.with_suffix('').parts) for p in root.rglob('*.py'));"
        "[importlib.import_module(m) for m in mods];"
        "leaked = [m for m in sys.modules if m == 'django' or m.startswith('django.')];"
        "assert not leaked, leaked;"
        "print('IMPORTED', len(mods))"
    )
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=ROOT, env=env, timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    assert r.stdout.startswith("IMPORTED")
