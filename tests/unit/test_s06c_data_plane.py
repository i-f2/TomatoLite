"""S0-6c Step 1：app HTTP / 裸 TCP / 控制面 / 真日志文件。

双相纪律（§0.7）逐开关：关=默认 → 开=行为改变 → 关回=恢复。
本步特有的两条"不撒谎"断言：
· `refuse`/恢复必须**同端口**（换端口=谎报另一台机器，探活 URL 是按端口钉的）；
· `process_alive=False` 联动 app 端口拒连（进程死端口活=mock 自相矛盾）。
"""
import os
import socket
import time

import httpx
import pytest

from mocks.host_fake import HostSim


@pytest.fixture
def h():
    host = HostSim()
    yield host
    host.close()


def _cfg(host, **kw):
    return httpx.post(f"{host.control_url}/_control/config", json=kw)


def _get_cfg(host):
    return httpx.get(f"{host.control_url}/_control/config").json()


def _info(host):
    return httpx.get(f"{host.control_url}/_control/info").json()


def _app(host, timeout=3.0, path="/"):
    return httpx.get(f"{host.app_url}{path}", timeout=timeout)


def _ctl_ok(host):
    """控制面活着（任何数据面注入下都必须活着）。"""
    assert httpx.get(f"{host.control_url}/_control/log").status_code == 200


def _connect(port, timeout=1.0):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.close()


def _refused(fn):
    """拒连的统一判定：内核 RST（不是超时、不是 DNS）。"""
    try:
        fn()
    except (httpx.ConnectError, ConnectionRefusedError, OSError) as e:
        name = type(e).__name__
        errno_ = getattr(e, "errno", None) or getattr(getattr(e, "__cause__", None), "errno", None)
        assert errno_ in (None, 61) or "Refused" in str(e), f"不是拒连而是 {name}: {e}"
        return True
    return False


# ---- 配置面 ----

def test_config_defaults_validation_and_reset(h):
    i = _info(h)
    assert i["app_listening"] is True and i["effective_app_mode"] == "normal"
    assert os.path.exists(i["log_path"]) and os.path.exists(i["version_path"])
    d = _get_cfg(h)
    assert d == {"http_status": 200, "http_body": '{"status":"ok"}', "http_mode": "normal",
                 "tcp_listen": True, "version": "1.2.3", "process_alive": True}
    assert _cfg(h, http_status=503).status_code == 200 and _get_cfg(h)["http_status"] == 503
    for bad in ({"nosuch": 1}, {"http_status": 99}, {"http_status": True},
                {"http_mode": "off"}, {"version": ""}, {"tcp_listen": "yes"}):
        assert _cfg(h, **bad).status_code == 400
    assert _get_cfg(h)["http_status"] == 503                      # 拒绝=零写入
    httpx.post(f"{h.control_url}/_control/reset")
    assert _get_cfg(h) == d                                        # reset 连配置
    assert httpx.post(f"{h.control_url}/_control/config", content=b"not json").status_code == 400


# ---- app HTTP 面 ----

def test_http_status_and_body_dual_phase(h):
    r = _app(h)                                                  # 相①默认
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    _cfg(h, http_status=503, http_body="DEGRADED: pool exhausted")
    r = _app(h)                                                  # 相②注入
    assert r.status_code == 503 and "DEGRADED" in r.text and "pool" in r.text
    _cfg(h, http_status=200, http_body='{"status":"ok"}')
    assert _app(h).status_code == 200                            # 相③关回


def test_version_endpoint_and_file_same_source(h):
    assert _app(h, path="/version").json() == {"version": "1.2.3"}
    _cfg(h, version="9.9.9")
    assert _app(h, path="/version").json() == {"version": "9.9.9"}
    with open(h.state.version_path) as f:                        # 文件是出处不是副本
        assert f.read() == "9.9.9"
    httpx.post(f"{h.control_url}/_control/reset")
    assert _app(h, path="/version").json() == {"version": "1.2.3"}


def test_http_mode_refuse_same_port_dual_phase(h):
    port = h.app_port
    assert _app(h).status_code == 200                            # 相①
    _cfg(h, http_mode="refuse")
    assert _refused(lambda: _app(h, timeout=1.0))                # 相②真 RST
    _ctl_ok(h)                                                   # 控制面独立存活
    assert h.info()["effective_app_mode"] == "refuse"
    _cfg(h, http_mode="normal")
    assert h.app_port == port                                    # 恢复=同端口
    assert _app(h).status_code == 200                            # 相③


def test_http_mode_blackhole_holds_connection(h):
    assert _app(h).status_code == 200
    _cfg(h, http_mode="blackhole")
    t0 = time.time()
    with pytest.raises(httpx.ReadTimeout):                       # 收了永不回
        _app(h, timeout=0.4)
    assert time.time() - t0 >= 0.4                               # 是真等死不是拒连快败
    _ctl_ok(h)                                                   # 黑洞吞不掉控制面
    _cfg(h, http_mode="normal")
    assert _app(h).status_code == 200


def test_process_alive_linkage_and_precedence(h):
    assert _app(h).status_code == 200
    _cfg(h, process_alive=False)                                 # 显式 mode 仍是 normal，但…
    assert _refused(lambda: _app(h, timeout=1.0))                # 进程死⇒端口必拒
    assert h.info()["effective_app_mode"] == "refuse"
    _ctl_ok(h)
    _cfg(h, process_alive=True)                                  # 复活⇒回显式 mode
    assert h.info()["effective_app_mode"] == "normal"
    assert _app(h).status_code == 200
    _cfg(h, http_mode="refuse", process_alive=False)             # 双来源同指 refuse
    assert _refused(lambda: _app(h, timeout=1.0))
    _cfg(h, process_alive=True)                                  # 只解联动，显式 refuse 仍在
    assert _refused(lambda: _app(h, timeout=1.0))
    _cfg(h, http_mode="normal")


# ---- TCP 面 ----

def test_tcp_port_open_close_dual_phase(h):
    port = h.tcp_port
    _connect(port)                                               # 相①开
    _cfg(h, tcp_listen=False)
    assert _refused(lambda: _connect(port))                      # 相②关=M2-12 失败素材
    _ctl_ok(h)
    _cfg(h, tcp_listen=True)
    assert h.tcp_port == port                                    # 同端口回来
    _connect(port)                                               # 相③


# ---- 日志文件面（§5.7③ 素材）----

def test_log_append_replace_read(h):
    seed = httpx.get(f"{h.control_url}/_control/log").json()["text"]
    assert "ERROR" in seed and "BEFORE deploy" in seed           # 历史错误行=假阳性源头
    httpx.post(f"{h.control_url}/_control/log/append",
               json={"lines": ["2026-09-24T01:00:00 INFO deploy v9.9.9 start",
                               "2026-09-24T01:00:02 INFO healthy"]})
    txt = httpx.get(f"{h.control_url}/_control/log").json()["text"]
    assert txt.endswith("INFO healthy\n") and txt.count("\n") == 4
    httpx.post(f"{h.control_url}/_control/log/lines", json={"lines": ["only line"]})
    assert httpx.get(f"{h.control_url}/_control/log").json()["text"] == "only line\n"
    assert httpx.post(f"{h.control_url}/_control/log/append",
                      json={"lines": "notalist"}).status_code == 400
    assert httpx.post(f"{h.control_url}/_control/log/append", json={}).status_code == 400


def test_log_rotation_real_inode_change(h):
    before = os.stat(h.state.log_path)
    old_text = h.state.read_log()
    r = httpx.post(f"{h.control_url}/_control/log/rotate")
    assert r.status_code == 200
    after = os.stat(h.state.log_path)
    assert after.st_ino != before.st_ino                         # 真 inode，不是布尔位
    assert r.json()["inode"] == after.st_ino and after.st_size == 0
    with open(h.state.log_rotated_path) as f:                    # 历史（含 ERROR 行）随 .1 离场
        assert f.read() == old_text
    assert h.state.read_log() == ""
    httpx.post(f"{h.control_url}/_control/reset")                # reset 清轮转产物+种子行
    assert "BEFORE deploy" in h.state.read_log()
    assert not os.path.exists(h.state.log_rotated_path)


def test_control_immunity_across_all_injections(h):
    _cfg(h, http_mode="blackhole", tcp_listen=False, process_alive=False)
    assert h.info()["effective_app_mode"] == "refuse"
    _ctl_ok(h)
    assert _cfg(h, version="0.0.1").status_code == 200           # 写也照常
    assert httpx.get(f"{h.control_url}/_control/config").status_code == 200
    _cfg(h, http_mode="normal", tcp_listen=True, process_alive=True)
    _connect(h.tcp_port)
    assert _app(h).status_code == 200


def test_control_404_shapes(h):
    assert httpx.get(f"{h.control_url}/_control/nope").status_code == 404
    assert httpx.post(f"{h.control_url}/_control/nope", json={}).status_code == 404
    assert httpx.get(f"{h.control_url}/").status_code == 404     # app 语义不在控制面


def test_two_hosts_are_isolated(h):
    h2 = HostSim()
    try:
        assert h2.app_port != h.app_port and h2.tcp_port != h.tcp_port
        _cfg(h2, process_alive=False)
        assert _app(h).status_code == 200                        # 各自状态互不污染
        assert h2.state.log_path != h.state.log_path
    finally:
        h2.close()
