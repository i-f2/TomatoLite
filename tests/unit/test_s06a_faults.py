"""S0-6a Step 3：_control/config + §4.6 三时序故障注入。

反假绿纪律（§0.7 双相断言）：每个开关都先证"关=默认行为"、再证"开=行为改变"、
最后证"关回去=恢复"。三时序= ①commit→respond→webhook / ②commit→超时→webhook /
③commit→webhook→respond，全部由 webhook_timing × response_hold_ms × auto_emit_url
组合构造，接收器独立证明 webhook 真到了。
"""
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from mocks.gitlab_fake import make_server


class _Capturer(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.server.received.append(json.loads(self.rfile.read(n)))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


@pytest.fixture
def srv():
    httpd, state = make_server(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cap = ThreadingHTTPServer(("127.0.0.1", 0), _Capturer)
    cap.received = []
    cap.daemon_threads = True
    threading.Thread(target=cap.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{httpd.server_address[1]}",
           "recv": f"http://127.0.0.1:{cap.server_address[1]}", "cap": cap}
    httpd.shutdown(); httpd.server_close()
    cap.shutdown(); cap.server_close()


def _cfg(url, **kw):
    return httpx.post(f"{url}/_control/config", json=kw)


def _commit(url, path="a.yaml", content="x", pid="1", **kw):
    return httpx.post(f"{url}/api/v4/projects/{pid}/repository/commits",
                      json={"branch": "main", "commit_message": f"rel {path}",
                            "actions": [{"action": "create", "file_path": path,
                                         "content": content}]}, **kw)


def _deliveries(url):
    return httpx.get(f"{url}/_control/deliveries").json()["deliveries"]


def _wait_deliveries(url, n, secs=3.0):
    deadline = time.time() + secs
    while time.time() < deadline:
        if len(_deliveries(url)) >= n:
            return True
        time.sleep(0.02)
    return False


# ---- 配置面本身 ----

def test_config_roundtrip_and_validation(srv):
    d = httpx.get(f"{srv['url']}/_control/config").json()
    assert d["webhook_timing"] == "none" and d["latency_ms"] == 0      # 默认=全关
    assert d["rest_status"] is None and d["token"] == "mock-gitlab-token"
    r = _cfg(srv["url"], latency_ms=10, webhook_timing="pre")
    assert r.status_code == 200
    assert r.json()["latency_ms"] == 10 and r.json()["webhook_timing"] == "pre"
    assert httpx.get(f"{srv['url']}/_control/config").json()["latency_ms"] == 10
    assert _cfg(srv["url"], nosuchkey=1).status_code == 400            # 未知键拒
    assert _cfg(srv["url"], webhook_timing="frobnicate").status_code == 400
    assert _cfg(srv["url"], latency_ms=-5).status_code == 400
    httpx.post(f"{srv['url']}/_control/reset")                          # reset 连配置一起回默认
    assert httpx.get(f"{srv['url']}/_control/config").json()["webhook_timing"] == "none"


# ---- rest_status：数据面强制 5xx ----

def test_rest_status_dual_phase(srv):
    assert _commit(srv["url"], "f.yaml", "v1").status_code == 200
    g = httpx.get(f"{srv['url']}/api/v4/projects/1/repository/files/f.yaml?ref=main")
    assert g.status_code == 200                                        # 相①关：默认行为
    assert _cfg(srv["url"], rest_status=503).status_code == 200
    assert httpx.get(f"{srv['url']}/api/v4/projects/1"
                     ).status_code == 503                              # 相②开：读 5xx
    c = _commit(srv["url"], "f2.yaml", "v2")
    assert c.status_code == 503                                        # 写也 5xx
    assert httpx.get(f"{srv['url']}/api/v4/projects/1"
                     "/repository/files/f2.yaml?ref=main").status_code == 503
    assert httpx.get(f"{srv['url']}/_control/deliveries").status_code == 200  # 控制面不被注入
    assert _cfg(srv["url"], webhook_timing="pre").status_code == 200          # 开关仍可写
    assert _cfg(srv["url"], rest_status=None).status_code == 200        # 注入下仍可解
    assert httpx.get(f"{srv['url']}/api/v4/projects/1").status_code == 200  # 相③恢复
    # 注入期间的 commit 零副作用：f2 从未存在
    assert httpx.get(f"{srv['url']}/api/v4/projects/1"
                     "/repository/files/f2.yaml?ref=main").status_code == 404


# ---- latency_ms：真延迟 ----

def test_latency_ms_dual_phase(srv):
    t0 = time.time()
    httpx.get(f"{srv['url']}/api/v4/projects/1")
    assert time.time() - t0 < 0.15                                     # 相①关
    _cfg(srv["url"], latency_ms=250)
    t0 = time.time()
    httpx.get(f"{srv['url']}/api/v4/projects/1")
    assert time.time() - t0 >= 0.25                                    # 相②开
    _cfg(srv["url"], latency_ms=0)
    t0 = time.time()
    httpx.get(f"{srv['url']}/api/v4/projects/1")
    assert time.time() - t0 < 0.15                                     # 相③恢复


# ---- 三时序 ----

def test_timing3_pre_webhook_before_response(srv):
    _cfg(srv["url"], auto_emit_url=srv["recv"], webhook_timing="pre")
    c = _commit(srv["url"], "t3.yaml")
    assert c.status_code == 200
    # 响应回来时 webhook 必须已完事（同步前置）：deliveries 与 received 即刻可见
    assert len(_deliveries(srv["url"])) == 1
    assert len(srv["cap"].received) == 1
    p = srv["cap"].received[0]
    assert p["after"] == c.json()["id"] and p["commits"][0]["id"] == c.json()["id"]
    _cfg(srv["url"], webhook_timing="none")                            # 双相：关→不再发
    _commit(srv["url"], "t3b.yaml")
    assert len(_deliveries(srv["url"])) == 1                           # 没有新增


def test_timing1_post_respond_then_webhook(srv):
    _cfg(srv["url"], auto_emit_url=srv["recv"], webhook_timing="post")
    c = _commit(srv["url"], "t1.yaml")
    assert c.status_code == 200
    assert _wait_deliveries(srv["url"], 1), "post 时序 webhook 未到"       # 响应后异步到
    p = srv["cap"].received[0]
    assert p["object_kind"] == "push" and p["ref"] == "refs/heads/main"
    assert p["after"] == c.json()["id"]
    assert p["project"]["path_with_namespace"] == "tl/apps-entry"


def test_timing2_client_timeout_then_webhook(srv):
    """② commit→超时→webhook：hold 让 REST 响应拖过客户端读超时，落库与 webhook 照常。"""
    _cfg(srv["url"], auto_emit_url=srv["recv"], webhook_timing="post",
         response_hold_ms=400)
    with pytest.raises(httpx.TimeoutException):
        _commit(srv["url"], "t2.yaml", content="late", timeout=0.1)
    # 超时≠未落库：hold 之后仍可读回（此刻 server 可能还在 hold 里，重试到通）
    deadline = time.time() + 3
    while True:
        g = httpx.get(f"{srv['url']}/api/v4/projects/1/repository/files/t2.yaml?ref=main")
        if g.status_code == 200:
            break
        assert time.time() < deadline
        time.sleep(0.05)
    cid = httpx.get(f"{srv['url']}/api/v4/projects/1/repository/commits").json()[0]["id"]
    assert _wait_deliveries(srv["url"], 1)                             # webhook 落在超时之后
    assert srv["cap"].received[0]["after"] == cid
    assert len(_deliveries(srv["url"])) == 1                           # 且只发一次


def test_auto_emit_off_means_silent(srv):
    _cfg(srv["url"], webhook_timing="post")                            # timing 开但无 url
    c = _commit(srv["url"], "silent.yaml")
    assert c.status_code == 200
    time.sleep(0.2)
    assert _deliveries(srv["url"]) == [] and srv["cap"].received == []
    r = httpx.post(f"{srv['url']}/_control/emit_push",
                   json={"url": srv["recv"], "project": 1})            # 手动口不受开关影响
    assert r.json()["emitted"] is True
    assert _wait_deliveries(srv["url"], 1)
