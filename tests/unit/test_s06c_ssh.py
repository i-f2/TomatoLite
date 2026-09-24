"""S0-6c Step 2：真 SSH-2 服务端（asyncssh）+ coreutils 方言解释器。

反假绿主证据：线路层断言全走 **asyncssh 客户端真握手**（§5.2 钦定的同一库，
连不上就是连不上）；`tail/stat/cat` 经 SSH 看到的是 HTTP 控制面写的**同一个
真文件**——方言、文件、注入三面在此汇合。错钥/无钥断"快"（<2s 被拒），
不但不挂，还必须不挂（§1132 BatchMode 首坑的反面）。
"""
import asyncio
import time

import asyncssh
import httpx
import pytest

from mocks.host_fake import HostSim
from mocks.host_fake.ssh import APP_PID, gen_key, run_command, write_authorized_keys


@pytest.fixture
def env(tmp_path):
    host_key = gen_key(str(tmp_path / "host_ed25519"))
    client_key = gen_key(str(tmp_path / "client_ed25519"))
    other_key = gen_key(str(tmp_path / "other_ed25519"))
    authk = str(tmp_path / "authorized_keys")
    write_authorized_keys([client_key], authk)
    host = HostSim(ssh_authorized_keys_path=authk,
                   ssh_host_key_path=str(tmp_path / "host_ed25519"))
    yield {"host": host, "client_key": client_key, "other_key": other_key,
           "port": host.ssh.port}
    host.close()


def _ssh(env, *cmds, username="ops", client_keys=None):
    async def go():
        async with asyncssh.connect("127.0.0.1", env["port"],
                                    client_keys=client_keys if client_keys is not None
                                    else [env["client_key"]],
                                    known_hosts=None, username=username) as conn:
            results = []
            for c in cmds:
                r = await conn.run(c, check=False)
                results.append((r.exit_status, r.stdout or "", r.stderr or ""))
            return results
    # 挂起即失败：wait_for 把"永不返回"变 TimeoutError（反挂起，也护住快拒断言）
    return asyncio.run(asyncio.wait_for(go(), timeout=15))


def _run(env, cmd):
    return _ssh(env, cmd)[0]


def _cfg(h, **kw):
    return httpx.post(f"{h.control_url}/_control/config", json=kw)


# ---- 方言契约（纯函数，不经线路）----

def test_interpreter_table():
    h = HostSim()
    try:
        st = h.state
        assert run_command(st, ["pgrep", "-f", "tlapp"]) == (0, f"{APP_PID}\n", "")
        assert run_command(st, ["pgrep", "tlapp"])[0] == 0            # 无 -f 也行名匹配
        assert run_command(st, ["pgrep", "-f", "nginx"]) == (1, "", "")
        assert run_command(st, ["systemctl", "is-active", "tlapp"]) == (0, "active\n", "")
        assert run_command(st, ["kill", "-0", str(APP_PID)]) == (0, "", "")
        assert run_command(st, ["kill", "-0", "99999"])[0] == 1       # 死 pid
        assert run_command(st, ["kill", "-0", "abc"])[0] == 2         # 用法错
        assert run_command(st, ["cat", "/etc/passwd"])[0] == 1        # 越界=不存在
        assert run_command(st, ["cat", "../etc/hosts"])[0] == 1       # 相对逃逸也拦
        assert run_command(st, ["curl", "http://x"])[0] == 127        # §5.3 命令不存在
        assert "command not found" in run_command(st, ["top"])[2]
        assert run_command(st, []) == (0, "", "")
        _cfg(h, process_alive=False)
        assert run_command(st, ["pgrep", "-f", "tlapp"]) == (1, "", "")
        assert run_command(st, ["systemctl", "is-active", "tlapp"]) == (3, "inactive\n", "")
        assert run_command(st, ["kill", "-0", str(APP_PID)])[0] == 1
    finally:
        h.close()


# ---- 线路层：登录与认证 ----

def test_ssh_login_and_exec_over_wire(env):
    assert _run(env, "pgrep -f tlapp") == (0, f"{APP_PID}\n", "")
    assert _run(env, "systemctl is-active tlapp") == (0, "active\n", "")


def test_wrong_key_denied_not_hanged(env):
    t0 = time.time()
    with pytest.raises(asyncssh.PermissionDenied):
        _ssh(env, client_keys=[env["other_key"]])
    assert time.time() - t0 < 2.0                                    # 快拒=BatchMode 安全的前提


def test_no_key_no_password_prompt(env):
    """password 方法在本 mock 上只会立刻失败，绝不交互式发问（§1132 首坑反面）。"""
    t0 = time.time()
    with pytest.raises(asyncssh.PermissionDenied):
        _ssh(env, client_keys=[])
    assert time.time() - t0 < 2.0


# ---- 三面汇合：SSH 看见的=HTTP 控制面写的=同一个真文件 ----

def test_tail_over_ssh_sees_control_plane_writes(env):
    h = env["host"]
    rc, out, _ = _run(env, "tail -n 1 app.log")
    assert "BEFORE deploy" in out                                    # 种子=历史假阳性源头
    _cfg(h, version="8.8.8")
    httpx.post(f"{h.control_url}/_control/log/append",
               json={"lines": ["2026-09-24T02:00:00 INFO deploy v8.8.8 ok"]})
    rc, out, _ = _run(env, "tail -n 1 app.log")
    assert "v8.8.8 ok" in out
    assert _run(env, "cat VERSION") == (0, "8.8.8", "")               # 版本同文件同出处
    assert _run(env, "cat /etc/passwd")[0] == 1                       # 线路上也拦越界


def test_rotation_visible_via_stat_inode_over_ssh(env):
    h = env["host"]
    _, before, _ = _run(env, "stat -c '%i %s %n' app.log")
    inode_before = int(before.split()[0])
    httpx.post(f"{h.control_url}/_control/log/rotate")
    _, after, _ = _run(env, "stat -c '%i %s %n' app.log")
    inode_after, size_after = int(after.split()[0]), int(after.split()[1])
    assert inode_after != inode_before and size_after == 0            # §5.7③：inode 变=回退 tail 的信号
    _, out, _ = _run(env, "cat app.log.1")                            # 历史随 .1 离场仍可查
    assert "BEFORE deploy" in out


def test_process_death_flips_ssh_view(env):
    _cfg(env["host"], process_alive=False)
    assert _run(env, "pgrep -f tlapp")[0] == 1                        # M2-13 失败判定素材
    assert _run(env, "systemctl is-active tlapp")[0] == 3
    _cfg(env["host"], process_alive=True)
    assert _run(env, "pgrep -f tlapp")[0] == 0                        # 双相：关回即恢复


def test_ssh_port_in_info(env):
    i = httpx.get(f"{env['host'].control_url}/_control/info").json()
    assert i["ssh_port"] == env["port"]
