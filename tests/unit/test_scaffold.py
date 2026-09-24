"""S0-3 冒烟：骨架本身活着（无 DB 依赖；集成/并发用例自 S0-5 起，§0.1 tests/ 三分目录）。"""

# 刻意**不挂** django_db——S0-3 的 DoD 不允许依赖 PG（DB 用例自 M1-01 起，§0.6a）。


def test_healthz(client):
    r = client.get("/healthz/")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "scaffold": "S0-3"}


def test_openapi_schema(client):
    r = client.get("/api/schema/")
    assert r.status_code == 200
    assert b"openapi:3" in r.content.replace(b" ", b"")  # 3.0.3/3.1.0 皆容


def test_settings_skeletons_importable():
    from django.conf import settings

    assert settings.CELERY_BROKER_URL.startswith("redis://")
    assert settings.CHANNEL_LAYERS["default"]["BACKEND"].endswith("InMemoryChannelLayer")
