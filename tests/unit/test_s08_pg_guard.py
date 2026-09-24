"""S0-8 全局闸（§0.6a）的自动断言：正相放行、反相咬合、接线可证、下限钉死。

§0.7 双相纪律在此的落法：守门必须**被证明会拒绝**（stub vendor=sqlite ⇒
ImproperlyConfigured），只测"PG 下能过"的守门等于没装——那是另一种假绿。
版本下限三层锁（本文件承担 ①②，③=S0-7 锁基建 vendor 前置断言已在位）：
① 框架硬下限钉版（Django 上调下限 ⇒ 此断言红 ⇒ §0.6a/台账同步修订，棘轮）；
② 活体真库版本 ≥15。启动端到端负断言（真子进程拒起）见同文件末用例。
"""

import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

from apps.core import apps as core_apps
from apps.core.dbguard import ensure_postgresql


def test_gate_passes_on_real_settings():
    """正相：真实 test settings（真 PG）下过闸无感。全套 140 用例本身就是
    此正相的重复背书——守门装在 django.setup() 必经之路上。"""
    ensure_postgresql()
    assert connection.vendor == "postgresql"


def test_gate_bites_on_non_pg_stub():
    """反相：sqlite stub 必须被点名拒绝，且消息可 grep（子进程用例定罪依据）。"""

    class _Stub:
        alias = "default"
        vendor = "sqlite"

    with pytest.raises(ImproperlyConfigured) as ei:
        ensure_postgresql(conn=_Stub())
    msg = str(ei.value)
    assert "sqlite" in msg and "0.6a" in msg


def test_ready_wires_the_gate(monkeypatch):
    """接线证明：守门不是孤儿函数——CoreConfig.ready() 真的调它。"""

    def _boom():
        raise ImproperlyConfigured("gate called from ready()")

    monkeypatch.setattr(core_apps, "ensure_postgresql", _boom)
    cfg = core_apps.CoreConfig("apps.core", importlib.import_module("apps.core"))
    with pytest.raises(ImproperlyConfigured, match="gate called"):
        cfg.ready()


@pytest.mark.django_db
def test_server_version_baseline_locked():
    """①钉版：Django 6.1.1 PG 后端硬下限 == (15,)（F-8 实据的锁定形态）；
    ②活体：真实例 major ≥ 15（本机实测 16.15）。"""
    assert connection.features.minimum_database_version == (15,)
    assert connection.get_database_version() >= (15,)


def test_startup_process_refuses_sqlite_and_allows_pg():
    """端到端（§0.7 双相）：真实子进程走完整 settings 加载 + app registry——
    sqlite 配置**起不来**（rc≠0 且守门点名），真 PG 配置照常起（rc==0）。
    不经测试内 stub 的裸 django.setup() 失败，才是台账 DoD"非 PG 直接启动
    失败"的最强证明；正相子进程同时排除"见谁咬谁"的坏闸。零副作用断言：
    被拒进程不得留下 sqlite 库文件（守门在建连之前，带病路径一步不走）。
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    stray = Path("/tmp/tl_s08_never_created.db")
    code = "import django; django.setup()"
    try:
        neg = subprocess.run(
            [sys.executable, "-c", code],
            env={
                **os.environ,
                "DJANGO_SETTINGS_MODULE": "config.settings.local",
                "DATABASE_URL": f"sqlite:///{stray}",
            },
            capture_output=True,
            text=True,
            timeout=90,
            cwd=repo,
        )
        assert neg.returncode != 0, "sqlite 配置竟启动成功——守门形同虚设"
        assert "sqlite" in neg.stderr and "0.6a" in neg.stderr, neg.stderr[-400:]
        assert not stray.exists(), "被拒进程竟建了库文件——守门必须在建连之前"

        pos = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.local"},
            capture_output=True,
            text=True,
            timeout=90,
            cwd=repo,
        )
        assert pos.returncode == 0, pos.stderr[-400:]
    finally:
        stray.unlink(missing_ok=True)
