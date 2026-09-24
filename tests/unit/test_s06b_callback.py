"""S0-6b Step 2：HMAC 签名回调发送器 + callbacks 日志 + emit_callback 手动口。

反假绿主证据：**捕获接收器用生产侧 `apps.core.hmac.verify` 对收到的原始字节重算
验签**——签名路径被真验过一次，而不是 mock 说"我签了"。坏签名开关证明的是
"验签会挂且挂在密钥上、不挂在格式上"。config 先经 fixture 进程内设置
（`_control/config` HTTP 口在 Step 3）。
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

import apps.core.hmac as tl_hmac
from mocks.jenkins_fake import make_server
from mocks.jenkins_fake.server import WRONG_SECRET


class _Capturer(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        self.server.received.append({
            "raw": raw,
            "sig": self.headers.get(tl_hmac.SIG_HEADER),
            "ts": self.headers.get(tl_hmac.TS_HEADER),
        })
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


@pytest.fixture
def jk():
    httpd, state = make_server(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cap = ThreadingHTTPServer(("127.0.0.1", 0), _Capturer)
    cap.received = []
    cap.daemon_threads = True
    threading.Thread(target=cap.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{httpd.server_address[1]}", "state": state,
           "recv": f"http://127.0.0.1:{cap.server_address[1]}", "cap": cap}
    httpd.shutdown(); httpd.server_close()
    cap.shutdown(); cap.server_close()


def _trigger(url, **params):
    p = {"TICKET_CODE": "TL-20260924-100", "COMMIT_SHA": "a" * 40}
    p.update(params)
    r = httpx.post(f"{url}/job/tl-release/buildWithParameters", params=p)
    return r.json()["queue_id"]


def _callbacks(url):
    return httpx.get(f"{url}/_control/callbacks").json()["callbacks"]


def _wait_got(pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("等待回调超时")


def _verify_with(state, got):
    return tl_hmac.verify(state.config["secret"], got["raw"], got["sig"], got["ts"])


def test_terminal_build_sends_verifiable_signed_callback(jk):
    jk["state"].config["notify_url"] = jk["recv"]
    _trigger(jk["url"])
    _wait_got(lambda: len(jk["cap"].received) == 1)
    got = jk["cap"].received[0]
    assert _verify_with(jk["state"], got)                    # 原始字节上真验得过
    p = json.loads(got["raw"])
    assert p["event"] == "build_finished"
    assert p["job_full_name"] == "tl-release" and p["build_number"] == 3
    assert p["result"] == "SUCCESS" and p["building"] is False
    assert p["displayName"] == "#3 TL-20260924-100"          # §3.4 归属路径①素材
    assert p["actions"][0]["parameters"][0]["name"] == "TICKET_CODE"
    assert p["changeSet"]["items"] == [{"commitId": "a" * 40}]  # 路径③素材
    assert p["timestamp"] > 1_700_000_000_000                # epoch ms（§3.6）
    assert abs(int(time.time()) - int(got["ts"])) <= 30      # ts 在窗内
    log = _callbacks(jk["url"])
    assert len(log) == 1 and log[0]["status"] == 200


def test_callback_repeat_delivers_identical_bytes(jk):
    """同 build 回调重复投递 N 次：dedup 双键的输入必须逐字节一致。"""
    jk["state"].config.update(notify_url=jk["recv"], callback_repeat=3)
    _trigger(jk["url"])
    _wait_got(lambda: len(jk["cap"].received) == 3)
    raws = [g["raw"] for g in jk["cap"].received]
    assert raws[0] == raws[1] == raws[2]
    sigs = {g["sig"] for g in jk["cap"].received}
    assert len(sigs) == 1
    assert all(_verify_with(jk["state"], g) for g in jk["cap"].received)
    assert len(_callbacks(jk["url"])) == 3


def test_bad_signature_two_phase(jk):
    """关=验得过；开=挂在密钥上（用错 secret 反而验得过——证明签的机制没坏）。"""
    jk["state"].config["notify_url"] = jk["recv"]
    _trigger(jk["url"])
    _wait_got(lambda: len(jk["cap"].received) == 1)
    assert _verify_with(jk["state"], jk["cap"].received[0])
    jk["state"].config["bad_signature"] = True
    _trigger(jk["url"])
    _wait_got(lambda: len(jk["cap"].received) == 2)
    bad = jk["cap"].received[1]
    assert not _verify_with(jk["state"], bad)
    assert tl_hmac.verify(WRONG_SECRET, bad["raw"], bad["sig"], bad["ts"])
    jk["state"].config["bad_signature"] = False
    _trigger(jk["url"])
    _wait_got(lambda: len(jk["cap"].received) == 3)
    assert _verify_with(jk["state"], jk["cap"].received[2])


def test_emit_callback_manual_replay_and_shapes(jk):
    _trigger(jk["url"])
    _wait_got(lambda: httpx.get(f"{jk['url']}/job/tl-release/3/api/json"
                                ).json()["building"] is False)
    assert _callbacks(jk["url"]) == []                        # 未配 notify_url=静默
    r = httpx.post(f"{jk['url']}/_control/emit_callback",
                   json={"job": "tl-release", "number": 3, "url": jk["recv"]})
    assert r.status_code == 200 and r.json()["emitted"] is True
    got = jk["cap"].received[0]
    assert _verify_with(jk["state"], got)
    p = json.loads(got["raw"])
    assert p["build_number"] == 3 and p["result"] == "SUCCESS"
    httpx.post(f"{jk['url']}/_control/emit_callback",
               json={"job": "tl-release", "number": 3, "url": jk["recv"]})
    assert jk["cap"].received[1]["raw"] == got["raw"]          # 重放逐字节一致
    r3 = httpx.post(f"{jk['url']}/_control/emit_callback",
                    json={"job": "tl-release", "number": 3, "url": jk["recv"],
                          "event": "stage_finished", "stage_name": "Build"})
    p3 = json.loads(jk["cap"].received[2]["raw"])
    assert p3["event"] == "stage_finished" and p3["stage_name"] == "Build"
    assert r3.json()["delivery"]["status"] == 200
    assert httpx.post(f"{jk['url']}/_control/emit_callback",
                      json={"job": "tl-release", "number": 99}).status_code == 400
    assert httpx.post(f"{jk['url']}/_control/emit_callback",
                      json={"number": 3}).status_code == 400   # 无处可发也 400


def test_receiver_failure_recorded_not_swallowed(jk):
    jk["state"].config["notify_url"] = "http://127.0.0.1:1/dead"
    _trigger(jk["url"])
    _wait_got(lambda: len(_callbacks(jk["url"])) == 1)
    rec = _callbacks(jk["url"])[0]
    assert rec["status"] == 0 and rec["error"]
    d = httpx.get(f"{jk['url']}/job/tl-release/3/api/json").json()
    assert d["result"] == "SUCCESS"                            # 回调失败不弄坏构建


def test_stop_abort_notifies(jk):
    jk["state"].config.update(notify_url=jk["recv"], build_duration_ms=5000)
    _trigger(jk["url"])
    httpx.post(f"{jk['url']}/job/tl-release/3/stop")
    _wait_got(lambda: len(jk["cap"].received) == 1)
    p = json.loads(jk["cap"].received[0]["raw"])
    assert p["result"] == "ABORTED" and p["building"] is False
    assert _verify_with(jk["state"], jk["cap"].received[0])


def test_notify_delay_ms_defers_delivery(jk):
    jk["state"].config.update(notify_url=jk["recv"], notify_delay_ms=400)
    _trigger(jk["url"])
    time.sleep(0.15)
    assert _callbacks(jk["url"]) == []                         # 相①：未到
    _wait_got(lambda: len(_callbacks(jk["url"])) == 1)         # 相②：延迟后到
    assert len(jk["cap"].received) == 1
