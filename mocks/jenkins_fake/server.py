"""HTTP 假 Jenkins（§6 第 1 条：必须真 HTTP；§3.8 端点表）。

标准库实现（拍板②）。终态→notify 走签名回调发送器（`_on_settled`/`emit_callback`
同一实现；签名 import `apps.core.hmac`，§6 第 3 条）；`_control/config` 逐键校验，
注入只打数据面（/job、/queue），**_control 面永不被注入**——否则开关关不回去。
Jenkins 认证被模拟为"来者不拒"（真实凭据差异归 S0-9 人工层）。
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from apps.core import hmac as tl_hmac  # §6 第 3 条：签名单一实现来自生产侧
from .state import DEFAULT_CONFIG, State

WRONG_SECRET = "WRONG-SECRET"  # bad_signature 开关的错密钥（不是随机字节垃圾）


def _v_int0(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _v_http(v):
    return v is None or (isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 599)


# _control/config 逐键校验器——错误值拒之门外，绝不允许半生效状态
_VALIDATORS = {
    "queue_mode": lambda v: v in ("promote", "hang", "cancelled", "vanish"),
    "build_mode": lambda v: v in ("finish", "hang"),
    "build_result": lambda v: v in ("SUCCESS", "FAILURE"),
    "build_duration_ms": _v_int0,
    "latency_ms": _v_int0,
    "trigger_response_hold_ms": _v_int0,
    "notify_delay_ms": _v_int0,
    "console_kb": lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 < v <= 4096,
    "rest_status": _v_http,
    "stop_status": _v_http,
    "notify_url": lambda v: v is None or isinstance(v, str),
    "callback_drop_rate": lambda v: (
        isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1
    ),
    "callback_seed": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "callback_repeat": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1,
    "bad_signature": lambda v: isinstance(v, bool),
    "secret": lambda v: isinstance(v, str) and v != "",
}

_JOB_RE = re.compile(r"^/job/([^/]+)/api/json/?$")
_BUILD_RE = re.compile(r"^/job/([^/]+)/(\d+)/(api/json|consoleText|wfapi/describe)/?$")
_TRIGGER_RE = re.compile(r"^/job/([^/]+)/buildWithParameters/?$")
_QUEUE_RE = re.compile(r"^/queue/item/(\d+)/api/json/?$")
_STOP_RE = re.compile(r"^/job/([^/]+)/(\d+)/stop/?$")

CONSOLE_CHUNK = 65536


class MockJenkinsHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, state: State, *args, **kwargs):
        self.state = state
        super().__init__(*args, **kwargs)

    # ---- 基建 ----

    def log_message(self, *args):
        pass

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        for i in range(0, len(body), CONSOLE_CHUNK):  # 分块写——大响应不憋一坨
            self.wfile.write(body[i : i + CONSOLE_CHUNK])

    def _read_raw(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    # ---- GET ----

    def do_GET(self) -> None:
        path = self.path.partition("?")[0]
        if path == "/_control/callbacks":
            with self.state.lock:
                self._json(200, {"callbacks": list(self.state.callbacks)})
            return
        if path == "/_control/config":
            with self.state.lock:
                self._json(200, dict(self.state.config))
            return
        if (path.startswith("/job/") or path.startswith("/queue/")) and self._fault_gate():
            return  # 注入只打数据面，_control 面永不中
        m = _JOB_RE.match(path)
        if m:
            self._get_job(urllib.parse.unquote(m.group(1)))
            return
        m = _QUEUE_RE.match(path)
        if m:
            self._get_queue(int(m.group(1)))
            return
        m = _BUILD_RE.match(path)
        if m:
            self._get_build(urllib.parse.unquote(m.group(1)), int(m.group(2)), m.group(3))
            return
        self._json(404, {"message": "No such endpoint"})

    def _get_job(self, job: str) -> None:
        with self.state.lock:
            builds = self.state.builds.get(job)
            if builds is None:
                self._json(404, {"message": f"No such job: {job}"})
                return
            last = builds[-1]
            self._json(
                200,
                {
                    "name": job,
                    "url": f"http://{self.headers.get('Host', '')}/job/{job}/",
                    "lastBuild": {"number": last["number"], "url": last["url"]},
                    "builds": [{"number": b["number"], "url": b["url"]} for b in builds],
                },
            )

    def _get_queue(self, qid: int) -> None:
        with self.state.lock:
            q = self.state.queue_item(qid)
            if q is None:  # vanish / 未知 / 被清出
                self._json(404, {"message": f"No such queue item: {qid}"})
                return
            self._json(
                200,
                {
                    "id": q["id"],
                    "why": q["why"],
                    "cancelled": q["cancelled"],
                    "task": {"name": q["job"]},
                    "inQueueSince": q["in_queue_since"],
                    # 队列期 executable=null 是显式契约（§3.8），不是缺字段
                    "executable": (
                        None
                        if q["exec"] is None
                        else {
                            "number": q["exec"],
                            "url": f"http://{self.headers.get('Host', '')}/job/{q['job']}/{q['exec']}/",
                        }
                    ),
                },
            )

    def _get_build(self, job: str, number: int, view: str) -> None:
        with self.state.lock:
            b = self.state.get_build(job, number)
            if b is None:
                self._json(404, {"message": f"No such build: {job}#{number}"})
                return
            if view == "api/json":
                self._json(
                    200,
                    {
                        "number": b["number"],
                        "url": b["url"],
                        "building": b["building"],
                        # 关键区分（§3.8）：building=true 时 result=null——显式在，非缺键
                        "result": None if b["building"] else b["result"],
                        "duration": b["duration"],
                        "timestamp": b["timestamp"],
                        "displayName": self.state.display_name(b),
                        "actions": self.state.actions(b),
                        "changeSet": {
                            "items": [{"commitId": b["commit_sha"]}] if b["commit_sha"] else []
                        },
                    },
                )
            elif view == "consoleText":
                text = self.state.console_text(b).encode()
                self._bytes(200, text, "text/plain; charset=utf-8")
            else:  # wfapi/describe
                self._json(200, {"id": f"{job}#{number}", "stages": self.state.stages(b)})

    # ---- POST ----

    def do_POST(self) -> None:
        raw = self._read_raw()  # 一律先排空（keep-alive 纪律，同 gitlab_fake）
        path, _, query = self.path.partition("?")
        if path in ("/_control/reset", "/_control/reset/"):
            self.state.reset()
            self._json(200, {"ok": True})
            return
        if path == "/_control/emit_callback":
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            self._emit_callback(body if isinstance(body, dict) else {})
            return
        if path == "/_control/config":
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            self._post_config(body if isinstance(body, dict) else {})
            return
        if (path.startswith("/job/") or path.startswith("/queue/")) and self._fault_gate():
            return  # 请求体已排空，keep-alive 不串
        m = _TRIGGER_RE.match(path)
        if m:
            self._trigger(urllib.parse.unquote(m.group(1)), raw, query)
            return
        m = _STOP_RE.match(path)
        if m:
            self._stop(urllib.parse.unquote(m.group(1)), int(m.group(2)))
            return
        self._json(404, {"message": "No such endpoint"})

    # ---- 故障注入（§3.8 故障模式清单；_control 面永不被注入，否则关不回去）----

    def _post_config(self, body: dict) -> None:
        unknown = sorted(set(body) - set(DEFAULT_CONFIG))
        if unknown:
            self._json(400, {"message": f"unknown config keys: {unknown}"})
            return
        bad = [k for k, v in body.items() if not _VALIDATORS[k](v)]
        if bad:
            self._json(400, {"message": f"bad values for: {sorted(bad)}"})
            return
        with self.state.lock:
            self.state.config.update(body)
            if "callback_seed" in body:
                self.state.rng.seed(self.state.config["callback_seed"])
            out = dict(self.state.config)
        self._json(200, out)

    def _fault_gate(self) -> bool:
        cfg = self.state.config
        lat = cfg.get("latency_ms") or 0
        if lat:
            time.sleep(lat / 1000)
        status = cfg.get("rest_status")
        if status:
            self._json(int(status), {"message": f"mock injected {status}"})
            return True
        return False

    def _trigger(self, job: str, raw: bytes, query: str) -> None:
        # buildWithParameters 的参数：query 为准，urlencoded body 补充（Jenkins 两种都吃）
        body_p = urllib.parse.parse_qs(raw.decode("utf-8", "replace"))
        query_p = urllib.parse.parse_qs(query)
        params = {**{k: v[-1] for k, v in body_p.items()}, **{k: v[-1] for k, v in query_p.items()}}
        out = self.state.trigger(job, params)
        if out == "no_job":
            self._json(404, {"message": f"No such job: {job}"})
            return
        qid, exec_no = out
        # 落锤调度：queue promote 且非 hang 才建定时器；hang 永不落锤（无回调无终态）
        if exec_no is not None and self.state.config["build_mode"] != "hang":
            delay = self.state.config["build_duration_ms"] / 1000
            t = threading.Timer(delay, self._settle, args=(job, exec_no))
            t.daemon = True
            t.start()
        # 先调度后持响应：hold 期间构建照样走完→回调可**抢在 201 之前**到达（§4.4 同型竞态）
        hold = self.state.config["trigger_response_hold_ms"] or 0
        if hold:
            time.sleep(hold / 1000)
        try:
            self.send_response(201)
            self.send_header("Location", f"http://{self.headers.get('Host', '')}/queue/item/{qid}/")
            body = json.dumps({"queue_id": qid}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # hold 超客户端超时=先挂：队列/构建/回调照常，201 写不出去不作紧
        # 201 不回报 build_no——§3.6 边 31 的"绝不猜 build_no"在 mock 层同步成立，
        # 唯一出处是 Location 的 queue_id，映射只能走 /queue/item/{id}/api/json

    def _settle(self, job: str, number: int) -> None:
        if self.state.finish(job, number):
            self._on_settled(job, number)

    # ---- 签名回调发送器（§3.3 口径；签名 import 生产侧 core/hmac，§6 第 3 条）----

    def _finished_payload(self, job: str, number: int) -> dict | None:
        with self.state.lock:
            b = self.state.get_build(job, number)
            if b is None:
                return None
            return {
                "event": "build_finished",
                "job_full_name": job,
                "build_number": number,
                "result": b["result"],
                "building": b["building"],
                "duration": b["duration"],
                "timestamp": b["timestamp"],  # epoch ms（§3.6：只用它算 duration）
                "displayName": self.state.display_name(b),
                "actions": self.state.actions(b),
                "changeSet": {"items": [{"commitId": b["commit_sha"]}] if b["commit_sha"] else []},
            }

    def _stop(self, job: str, number: int) -> None:
        forced = self.state.config["stop_status"]
        if forced:  # M2-34 "stop 失败不阻塞终止"素材：500 且**不执行中止**
            self._json(int(forced), {"message": f"mock stop injected {forced}"})
            return
        outcome = self.state.stop(job, number)
        if outcome in ("no_job", "no_build"):
            self._json(404, {"message": f"cannot stop {job}#{number}: {outcome}"})
            return
        if outcome == "aborted":
            self._on_settled(job, number)  # 中止同样出终态事件
        self._json(200, {"ok": True, "outcome": outcome})  # 'already' 也 200（§0.4 裁定）

    def _on_settled(self, job: str, number: int) -> None:
        """终态落锤钩子：notify_url 已配则出签投递（可延迟、可重复）。"""
        cfg = self.state.config
        url = cfg["notify_url"]
        if not url:
            return
        delay = (cfg["notify_delay_ms"] or 0) / 1000
        if delay:
            t = threading.Timer(delay, self._send_finished, args=(job, number))
            t.daemon = True
            t.start()
        else:
            self._send_finished(job, number)

    def _send_finished(self, job: str, number: int) -> None:
        payload = self._finished_payload(job, number)
        if payload is None:
            return
        secret = WRONG_SECRET if self.state.config["bad_signature"] else self.state.config["secret"]
        drop = self.state.config["callback_drop_rate"]
        for _ in range(max(1, self.state.config["callback_repeat"])):
            if drop > 0:
                with self.state.lock:
                    hit = self.state.rng.random() < drop
                if hit:  # 丢了也要如实记——否则 drop 开关自己就成了不可断言的黑洞
                    with self.state.lock:
                        self.state.callbacks.append(
                            {
                                "url": self.state.config["notify_url"],
                                "event": "build_finished",
                                "job_full_name": job,
                                "build_number": number,
                                "result": payload["result"],
                                "status": "DROPPED",
                                "dropped": True,
                            }
                        )
                    continue
            self._deliver_signed(self.state.config["notify_url"], payload, secret)

    def _deliver_signed(self, target: str, payload: dict, secret: str) -> dict:
        """对**原始字节**出签并 POST（签名与被签的是同一个 bytes 对象——
        §3.3 首坑在实现层就不可能出现）。每次投递都进 callbacks 日志。"""
        raw = json.dumps(payload).encode()
        ts = int(time.time())
        sig = tl_hmac.sign(secret, ts, raw)
        try:
            req = urllib.request.Request(
                target,
                data=raw,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    tl_hmac.SIG_HEADER: sig,
                    tl_hmac.TS_HEADER: str(ts),
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                delivery = {"status": resp.status}
        except Exception as e:  # noqa: BLE001 —— 接收器的错如实记，不吞
            delivery = {"status": getattr(e, "code", 0), "error": str(e)}
        rec = {
            "url": target,
            "event": payload.get("event"),
            "job_full_name": payload.get("job_full_name"),
            "build_number": payload.get("build_number"),
            "result": payload.get("result"),
            "ts": ts,
            "sig": sig,
            **delivery,
        }
        with self.state.lock:
            self.state.callbacks.append(rec)
        return rec

    def _emit_callback(self, body: dict) -> None:
        """手动投递口（重复投递/无 notify_url 定向测试用）；自动口走同一实现。"""
        job = urllib.parse.unquote(str(body.get("job", "tl-release")))
        try:
            number = int(body.get("number", -1))
        except (TypeError, ValueError):
            self._json(400, {"message": "number is required"})
            return
        payload = self._finished_payload(job, number)
        if payload is None:
            self._json(400, {"message": f"no such build: {job}#{number}"})
            return
        if body.get("event"):
            payload["event"] = body["event"]
        if body.get("stage_name") is not None:
            payload["stage_name"] = body["stage_name"]
        target = body.get("url") or self.state.config["notify_url"]
        if not target:
            self._json(400, {"message": "no target url (body.url or config.notify_url)"})
            return
        secret = WRONG_SECRET if body.get("bad_signature") else self.state.config["secret"]
        rec = self._deliver_signed(target, payload, secret)
        self._json(200, {"emitted": True, "delivery": rec, "payload": payload})


def make_server(host: str = "127.0.0.1", port: int = 0, state: State | None = None):
    st = state or State()
    handler = lambda *args, **kwargs: MockJenkinsHandler(st, *args, **kwargs)  # noqa: E731
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    httpd.state = st
    return httpd, st
