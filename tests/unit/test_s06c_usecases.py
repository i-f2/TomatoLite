"""S0-6c Step 3：三类用例收口——证明 mock 供得起 §5.4.1/§5.7③/§5.3 的消费方判料。

本文件的纪律是**判料，不实施**：用 mock 的四个面跑一遍生产用例（M2-11 上修
判定、M2-13 部署后探活、M2-10 日志扫描）将遇到的完整判定链，证明每种素材
**可判、且互不混淆**——`CONNECT_REFUSED`（应用挂）≠ `TIMEOUT_*`（平台够不著）
≠ `STATUS_MISMATCH`（应答了但不对）是 M2-11 上修结论的唯一依据（§5.4.1），
混淆任何两者都是不可接受的判定质量。M2-10 `SshTransport` 生产实现属
Non-Goal：此处以 asyncssh 客户端最小复现代替。

TIMEOUT_CONNECT 由消费方用 RFC5737 `192.0.2.1` 构造（Spec 拍板③：本机内核
backlog 会先完成握手，mock 造不出"SYN 无应答"，也绝不假装造得出）。
"""

import asyncio
import os
import socket

import asyncssh
import httpx
import pytest

from mocks.host_fake import HostSim
from mocks.host_fake.ssh import APP_PID, gen_key, write_authorized_keys

# ---- 探活判料器（§5.4.1 四形状互斥）----


def _is_refused(e: BaseException) -> bool:
    """httpcore 内部 `raise exc from None` 会断 __cause__ 链，两路都走。"""
    while e is not None:
        if isinstance(e, ConnectionRefusedError) or "refused" in str(e).lower():
            return True
        e = e.__cause__ or e.__context__
    return False


def _classify_get(url: str, timeout: float = 1.0) -> str:
    """HTTP 探活的 error_class 判定：成功/四失败形状，返回值互斥。

    trust_env=False 是实据逼出的纪律（Step 3 当场撞见）：开发 shell 带
    HTTP_PROXY 时，httpx 默认经代理外连，`192.0.2.1` 的 **connect-timeout
    被伪装成 read-timeout**（连代理必成）——§5.4.1 两类形状在消费方侧
    混淆，正是本任务要防的误判。裸 TCP 探活语义上绝不该过 HTTP 代理。
    """
    try:
        with httpx.Client(trust_env=False, timeout=timeout) as c:
            r = c.get(url)
    except httpx.ConnectError as e:
        if _is_refused(e):
            return "CONNECT_REFUSED"  # 内核 RST：端口死=应用挂
        raise
    except httpx.ConnectTimeout:
        return "TIMEOUT_CONNECT"  # SYN 无应答：平台够不著
    except httpx.ReadTimeout:
        return "TIMEOUT_READ"  # 连上了永不回：应用吊死
    return "OK" if r.status_code == 200 else "STATUS_MISMATCH"


def _tcp_probe_ok(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        s = socket.create_connection((host, port), timeout)
    except ConnectionRefusedError:
        return False
    s.close()
    return True


def _cfg(h: HostSim, **kw):
    return httpx.post(f"{h.control_url}/_control/config", json=kw)


# ---- SSH 最小复现代（同 test_s06c_ssh 模式：挂起=失败）----


@pytest.fixture
def env(tmp_path):
    gen_key(str(tmp_path / "host_ed25519"))  # 文件是目的；HostSim 按路径加载
    client_key = gen_key(str(tmp_path / "client_ed25519"))
    authk = str(tmp_path / "authorized_keys")
    write_authorized_keys([client_key], authk)
    host = HostSim(ssh_authorized_keys_path=authk, ssh_host_key_path=str(tmp_path / "host_ed25519"))
    yield {"host": host, "client_key": client_key, "port": host.ssh.port}
    host.close()


def _ssh(env, *cmds):
    async def go():
        async with asyncssh.connect(
            "127.0.0.1",
            env["port"],
            client_keys=[env["client_key"]],
            known_hosts=None,
            username="ops",
        ) as conn:
            return [await conn.run(c, check=False) for c in cmds]

    return asyncio.run(asyncio.wait_for(go(), timeout=15))


# ---- 用例①：§5.4.1 三形状互不混淆（+ 第四形状由消费方构造）----


def test_http_probe_shapes_never_conflated():
    h = HostSim()
    try:
        url = f"{h.app_url}/healthz"
        verdicts = [_classify_get(url)]  # OK 基线
        _cfg(h, http_status=503)
        verdicts.append(_classify_get(url))  # 应答了但不对
        _cfg(h, http_status=200, http_mode="refuse")
        verdicts.append(_classify_get(url))  # 应用挂（RST）
        _cfg(h, http_mode="blackhole")
        verdicts.append(_classify_get(url, timeout=0.5))  # 吊死（连上不回）
        verdicts.append(_classify_get("http://192.0.2.1:8099/", timeout=1.0))  # SYN 无应答
        assert verdicts == [
            "OK",
            "STATUS_MISMATCH",
            "CONNECT_REFUSED",
            "TIMEOUT_READ",
            "TIMEOUT_CONNECT",
        ]
        assert len(set(verdicts)) == len(verdicts)  # 互斥=可判（冗余强化）
        # 全等证明之外：refuse/blackhole 期间控制面照常（开关关得回去）
        assert _cfg(h, http_mode="normal").status_code == 200
        assert _classify_get(url) == "OK"  # 双相收口：可回
    finally:
        h.close()


# ---- 用例②：TCP 面关闭判失败（端口号恒定，判料不漂移）----


def test_tcp_closed_is_failure_not_something_else():
    h = HostSim()
    try:
        port0 = h.tcp_port
        assert _tcp_probe_ok(h.addr, port0)  # 开=握手即成（关了即断）
        _cfg(h, tcp_listen=False)
        assert h.tcp_port == port0  # 端口恒定=不谎报另一台机器
        assert not _tcp_probe_ok(h.addr, port0)  # 关=速拒，不是超时
        _cfg(h, tcp_listen=True)
        assert _tcp_probe_ok(h.addr, port0)  # 双相：关得掉也关得回
    finally:
        h.close()


# ---- 用例③：进程检查失败判定 + 与 HTTP 面联动的唯一结论 ----


def test_process_death_verdict_unique_across_planes(env):
    h = env["host"]
    probes = ["pgrep -f tlapp", "systemctl is-active tlapp", f"kill -0 {APP_PID}"]
    assert [r.exit_status for r in _ssh(env, *probes)] == [0, 0, 0]  # 活：三口全 0
    assert _classify_get(f"{h.app_url}/healthz") == "OK"
    _cfg(h, process_alive=False)
    rc = [r.exit_status for r in _ssh(env, *probes)]
    assert rc == [1, 3, 1]  # 死：pgrep/systemd/kill 惯例三口
    # 联动面：进程死 ⇒ 应用端口同判 CONNECT_REFUSED（进程+HTTP 两料同向=可下结论）
    assert _classify_get(f"{h.app_url}/healthz") == "CONNECT_REFUSED"
    _cfg(h, process_alive=True)
    assert [r.exit_status for r in _ssh(env, *probes)] == [0, 0, 0]  # 双相收口
    assert _classify_get(f"{h.app_url}/healthz") == "OK"


# ---- 用例④：§5.7③ 日志扫描假阳性双相 + 轮转回退 ----


def test_offset_scan_beats_tail_window_on_baseline_error(env):
    """部署基线里已有历史 ERROR：offset 增量判绿，tail 全窗判红——两料本就该不同。"""
    st = env["host"].state
    before = os.stat(st.log_path)
    with open(st.log_path) as f:
        assert "ERROR" in f.read()  # 种子=历史假阳性源头
    httpx.post(
        f"{env['host'].control_url}/_control/log/append",
        json={"lines": ["2026-09-24T03:00:00 INFO deploy v9.0.0 ok"]},
    )
    after = os.stat(st.log_path)
    assert after.st_ino == before.st_ino  # 未轮转 inode 不变=续读合法
    with open(st.log_path) as f:  # offset 消费方：只读新增段
        f.seek(before.st_size)
        inc = f.read()
    assert "ERROR" not in inc and "deploy v9.0.0 ok" in inc  # 判绿（正确）
    win = _ssh(env, "tail -n 50 app.log")[0].stdout  # 全窗消费方
    assert "legacy crash loop" in win  # 判红（假阳性）——两料分岔即证据


def test_rotation_inode_change_forces_tail_fallback(env):
    """inode 变=偏移续读失效的正确信号：不回退则部署后新 ERROR 被吞（假绿）。"""
    h = env["host"]
    st = h.state
    before = os.stat(st.log_path)
    r = httpx.post(f"{h.control_url}/_control/log/rotate")
    assert r.status_code == 200
    after = os.stat(st.log_path)
    assert r.json()["inode"] == after.st_ino != before.st_ino
    assert after.st_size == 0
    with open(st.log_path) as f:  # 天真续读：旧 offset 读空文件
        f.seek(before.st_size)
        assert f.read() == ""  # ← 假绿素材，无 inode 校验就会吞
    httpx.post(
        f"{h.control_url}/_control/log/append",
        json={"lines": ["2026-09-24T03:05:00 ERROR post-rotate boom"]},
    )
    r = _ssh(env, "tail -n 50 app.log")[0]  # 正确行为：回退 tail 全窗
    assert r.exit_status == 0 and "post-rotate boom" in r.stdout  # 判红（正确）
    r = _ssh(env, "cat app.log.1")[0]
    assert r.exit_status == 0 and "BEFORE deploy" in r.stdout  # 历史随 .1 离场仍可查，不丢料
