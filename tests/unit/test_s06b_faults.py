"""S0-6b Step 3：_control/config 全开关 HTTP 口 + §3.8 故障模式清单收口。

双相纪律（§0.7）：每个开关 关=默认 → 开=行为改变 → 关回=恢复；
`callback_drop_rate` 用 seeded RNG——同 seed 同剧本，CI 可复现（拍板⑤）。
"""

import threading
import time

import httpx
import pytest

from mocks.jenkins_fake import make_server


class _Sink:
    """计数接收器：只数到达条数（验签归 Step 2 文件，这里不重复）。"""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                outer.n += 1
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.n = 0
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/cb"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def jk():
    httpd, state = make_server(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    sink = _Sink()
    yield {
        "url": f"http://127.0.0.1:{httpd.server_address[1]}",
        "state": state,
        "recv": sink.url,
        "sink": sink,
    }
    httpd.shutdown()
    httpd.server_close()
    sink.close()


def _cfg(url, **kw):
    return httpx.post(f"{url}/_control/config", json=kw)


def _get_cfg(url):
    return httpx.get(f"{url}/_control/config").json()


def _trigger(url, timeout=None):
    return (
        httpx.post(
            f"{url}/job/tl-release/buildWithParameters",
            params={"TICKET_CODE": "TL-FAULT", "COMMIT_SHA": "d" * 40},
            timeout=timeout,
        )
        if timeout
        else httpx.post(
            f"{url}/job/tl-release/buildWithParameters",
            params={"TICKET_CODE": "TL-FAULT", "COMMIT_SHA": "d" * 40},
        )
    )


def _callbacks(url):
    return httpx.get(f"{url}/_control/callbacks").json()["callbacks"]


def _wait(pred, timeout=3.0, msg=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"超时: {msg}")


# ---- 配置面 ----


def test_config_roundtrip_validation_and_reset(jk):
    d = _get_cfg(jk["url"])
    assert d["queue_mode"] == "promote" and d["callback_drop_rate"] == 0.0
    assert d["notify_url"] is None and d["rest_status"] is None
    assert _cfg(jk["url"], latency_ms=50, queue_mode="hang").status_code == 200
    assert _get_cfg(jk["url"])["queue_mode"] == "hang"
    assert _cfg(jk["url"], nosuch=1).status_code == 400  # 未知键
    assert _cfg(jk["url"], queue_mode="nope").status_code == 400  # 坏枚举
    assert _cfg(jk["url"], callback_drop_rate=1.5).status_code == 400  # 越界概率
    assert _cfg(jk["url"], callback_repeat=0).status_code == 400
    assert _cfg(jk["url"], rest_status=42).status_code == 400  # 非 HTTP 码
    assert _get_cfg(jk["url"])["queue_mode"] == "hang"  # 拒=零写入
    httpx.post(f"{jk['url']}/_control/reset")
    assert _get_cfg(jk["url"])["queue_mode"] == "promote"  # reset 连配置


def test_lifecycle_switch_via_http(jk):
    _cfg(jk["url"], queue_mode="cancelled")
    r = _trigger(jk["url"])
    q = httpx.get(f"{jk['url']}/queue/item/{r.json()['queue_id']}/api/json").json()
    assert q["cancelled"] is True  # HTTP 口与进程内同效


# ---- REST 故障面 ----


def test_rest_status_dual_phase(jk):
    assert httpx.get(f"{jk['url']}/job/tl-release/api/json").status_code == 200  # 相①关
    _cfg(jk["url"], rest_status=503)
    assert httpx.get(f"{jk['url']}/job/tl-release/api/json").status_code == 503
    assert _trigger(jk["url"]).status_code == 503  # 写也拒
    assert httpx.get(f"{jk['url']}/queue/item/999/api/json").status_code == 503
    assert httpx.get(f"{jk['url']}/_control/callbacks").status_code == 200  # 控制面免疫
    _cfg(jk["url"], rest_status=None)  # 注入下可解
    b = httpx.get(f"{jk['url']}/job/tl-release/api/json").json()
    assert [x["number"] for x in b["builds"]] == [1, 2]  # 注入期零副作用
    assert b["lastBuild"]["number"] == 2  # 相③恢复
    _trigger(jk["url"])
    assert httpx.get(f"{jk['url']}/job/tl-release/api/json").json()["lastBuild"]["number"] == 3


def test_latency_ms_dual_phase(jk):
    t0 = time.time()
    httpx.get(f"{jk['url']}/job/tl-release/api/json")
    assert time.time() - t0 < 0.15
    _cfg(jk["url"], latency_ms=250)
    t0 = time.time()
    httpx.get(f"{jk['url']}/job/tl-release/api/json")
    assert time.time() - t0 >= 0.25
    _cfg(jk["url"], latency_ms=0)
    t0 = time.time()
    httpx.get(f"{jk['url']}/job/tl-release/api/json")
    assert time.time() - t0 < 0.15


def test_stop_status_does_not_abort(jk):
    _cfg(jk["url"], build_duration_ms=5000, stop_status=500)
    _trigger(jk["url"])
    q = httpx.get(f"{jk['url']}/queue/item/100/api/json").json()
    n = q["executable"]["number"]
    assert httpx.post(f"{jk['url']}/job/tl-release/{n}/stop").status_code == 500
    d = httpx.get(f"{jk['url']}/job/tl-release/{n}/api/json").json()
    assert d["building"] is True  # 500=中止未发生
    _cfg(jk["url"], stop_status=None)
    assert httpx.post(f"{jk['url']}/job/tl-release/{n}/stop").json()["outcome"] == "aborted"
    assert httpx.get(f"{jk['url']}/job/tl-release/{n}/api/json").json()["result"] == "ABORTED"


# ---- 回调故障面 ----


def test_callback_drop_endpoints_dual_phase(jk):
    _cfg(jk["url"], notify_url=jk["recv"], callback_drop_rate=1.0)
    _trigger(jk["url"])
    _wait(lambda: len(_callbacks(jk["url"])) == 1, msg="DROPPED 记录")
    assert jk["sink"].n == 0  # 一条未达
    rec = _callbacks(jk["url"])[0]
    assert rec["status"] == "DROPPED" and rec["dropped"] is True
    d = httpx.get(f"{jk['url']}/job/tl-release/3/api/json").json()  # sweeper 前提：
    assert d["result"] == "SUCCESS"  # REST 面照样如实
    _cfg(jk["url"], callback_drop_rate=0.0)  # 相③恢复
    _trigger(jk["url"])
    _wait(lambda: jk["sink"].n == 1, msg="drop=0 送达")
    assert [c["status"] for c in _callbacks(jk["url"])] == ["DROPPED", 200]


def test_drop_rate_seeded_reproducible(jk):
    _cfg(jk["url"], notify_url=jk["recv"], callback_drop_rate=0.5, callback_seed=7)
    for _ in range(4):
        _trigger(jk["url"])
    _wait(lambda: len(_callbacks(jk["url"])) == 4, msg="4 次投递判定")
    pattern = [c["status"] == "DROPPED" for c in _callbacks(jk["url"])]
    httpx.post(f"{jk['url']}/_control/reset")  # 同 seed 重演一遍
    _cfg(jk["url"], notify_url=jk["recv"], callback_drop_rate=0.5, callback_seed=7)
    for _ in range(4):
        _trigger(jk["url"])
    _wait(lambda: len(_callbacks(jk["url"])) == 4, msg="重演 4 次")
    assert [c["status"] == "DROPPED" for c in _callbacks(jk["url"])] == pattern


def test_manual_emit_not_subject_to_drop(jk):
    _cfg(jk["url"], notify_url=jk["recv"], callback_drop_rate=1.0)
    _trigger(jk["url"])
    _wait(lambda: len(_callbacks(jk["url"])) == 1)
    r = httpx.post(f"{jk['url']}/_control/emit_callback", json={"job": "tl-release", "number": 3})
    assert r.json()["delivery"]["status"] == 200  # 手动口必达
    assert jk["sink"].n == 1


def test_callback_precedes_trigger_response(jk):
    """§3.8 收口：hold 让 201 拖过客户端读超时，回调**先于**触发响应到达。"""
    _cfg(jk["url"], notify_url=jk["recv"], build_duration_ms=0, trigger_response_hold_ms=400)
    with pytest.raises(httpx.TimeoutException):
        _trigger(jk["url"], timeout=0.1)
    _wait(lambda: jk["sink"].n == 1, msg="回调先于(失去的)响应")  # 客户端已放弃
    q = httpx.get(f"{jk['url']}/queue/item/100/api/json").json()
    assert q["executable"]["number"] == 3  # 队列映射照常存在


def test_build_hang_never_settles_never_callbacks(jk):
    """Spec Step 3 验收：hang 开关下 永不到终态 **且** 永不回调（不是静默丢，是没到触发点）。"""
    _cfg(jk["url"], notify_url=jk["recv"], build_mode="hang")
    _trigger(jk["url"])
    time.sleep(0.3)
    d = httpx.get(f"{jk['url']}/job/tl-release/3/api/json").json()
    assert d["building"] is True and d["result"] is None  #  REST 面如实
    assert _callbacks(jk["url"]) == [] and jk["sink"].n == 0  #  没发过
    _cfg(jk["url"], build_mode="finish")  # 相③开关关回：
    _trigger(jk["url"])  # 新触发必须自愈落锤
    _wait(lambda: jk["sink"].n == 1, msg="解除 hang 后新构建自动落终态并回调")
    assert _callbacks(jk["url"])[0]["build_number"] == 4
    d3 = httpx.get(f"{jk['url']}/job/tl-release/3/api/json").json()
    assert d3["building"] is True  # 旧 hang 单不受牵连


def test_console_kb_large_response(jk):
    _cfg(jk["url"], console_kb=64)
    _trigger(jk["url"])
    _wait(
        lambda: not httpx.get(f"{jk['url']}/job/tl-release/3/api/json").json()["building"],
        msg="settle",
    )
    c = httpx.get(f"{jk['url']}/job/tl-release/3/consoleText")
    assert c.status_code == 200 and len(c.content) >= 64 * 1024
    assert b"Finished: SUCCESS" in c.content[-24:]
