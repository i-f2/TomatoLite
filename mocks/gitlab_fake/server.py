"""HTTP 假服务（§6 第 1 条：必须真 HTTP——sweeper/worker 进程内 mock 覆盖不到）。

标准库实现（拍板②零新依赖）。路由按 §4.6 端点表；`_control` 面 Step 2/3 增量。
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .state import DEFAULT_BRANCH, DEFAULT_CONFIG, State

# webhook 三时序（§4.6）：pre=③ commit→webhook→respond；post=①/② respond(或超时)→webhook
WEBHOOK_TIMINGS = ("none", "pre", "post")

ZERO_SHA = "0" * 40

_FILES_RE = re.compile(r"^/api/v4/projects/([^/]+)/repository/files/(.+)$")
_COMMITS_RE = re.compile(r"^/api/v4/projects/([^/]+)/repository/commits$")
_PROJECT_RE = re.compile(r"^/api/v4/projects/([^/]+)$")


class MockGitLabHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, state: State, *args, **kwargs):
        self.state = state
        super().__init__(*args, **kwargs)

    # ---- 基建 ----

    def log_message(self, *args):  # 静音
        pass

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    # ---- GET ----

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        qs = urllib.parse.parse_qs(query)
        if path == "/_control/deliveries":
            with self.state.lock:
                self._json(200, {"deliveries": list(self.state.deliveries)})
            return
        if path == "/_control/config":
            with self.state.lock:
                self._json(200, dict(self.state.config))
            return
        # 故障注入只打数据面——_control 面永不被注入，否则开关关不回去
        if path.startswith("/api/") and self._fault_gate():
            return
        m = _PROJECT_RE.match(path)
        if m:
            pid = self.state.resolve_project(urllib.parse.unquote(m.group(1)))
            if pid is None:
                self._json(404, {"message": "404 Project Not Found"})
                return
            self._json(200, {"id": pid, "path_with_namespace": self.state.projects[pid]})
            return
        m = _COMMITS_RE.match(path)
        if m:
            self._get_commits(m.group(1), qs)
            return
        m = _FILES_RE.match(path)
        if m:
            self._get_file(m.group(1), urllib.parse.unquote(m.group(2)), qs)
            return
        self._json(404, {"message": "404 Not Found"})

    def _resolve_or_404(self, seg: str):
        pid = self.state.resolve_project(urllib.parse.unquote(seg))
        if pid is None:
            self._json(404, {"message": "404 Project Not Found"})
        return pid

    def _get_file(self, seg: str, file_path: str, qs: dict) -> None:
        pid = self._resolve_or_404(seg)
        if pid is None:
            return
        ref = (qs.get("ref") or [DEFAULT_BRANCH])[0]
        with self.state.lock:
            f = self.state.files.get((pid, ref, file_path))
        if f is None:
            self._json(404, {"message": "404 File Not Found"})
            return
        self._json(
            200,
            {
                "file_name": file_path.rsplit("/", 1)[-1],
                "path": file_path,
                "size": len(f["content"]),
                "encoding": "base64",
                "content": base64.b64encode(f["content"]).decode(),
                "content_sha256": f["content_sha256"],
                "ref": ref,
                "blob_id": f["blob_id"],
                "commit_id": f["commit_id"],
                "last_commit_id": f["last_commit_id"],
            },
        )

    def _get_commits(self, seg: str, qs: dict) -> None:
        pid = self._resolve_or_404(seg)
        if pid is None:
            return
        path = (qs.get("path") or [None])[0]
        per_page = int((qs.get("per_page") or ["20"])[0])
        page = max(int((qs.get("page") or ["1"])[0]), 1)
        with self.state.lock:
            rows = [
                c
                for c in self.state.commits
                if c["project_id"] == pid and (path is None or path in c["paths"])
            ]
        window = rows[(page - 1) * per_page : page * per_page]
        self._json(
            200,
            [{"id": c["id"], "title": c["title"], "created_at": c["created_at"]} for c in window],
        )

    # ---- POST ----

    def do_POST(self) -> None:
        body = self._read_body()  # 一律先排空请求体——keep-alive 下漏读会串到下一响应
        path = self.path.partition("?")[0]
        if path == "/_control/reset":
            self.state.reset()
            self._json(200, {"ok": True})
            return
        if path == "/_control/emit_push":
            self._emit_push(body)
            return
        if path == "/_control/config":
            self._post_config(body)
            return
        if path.startswith("/api/") and self._fault_gate():
            return  # 注入生效：请求体已排空，keep-alive 不串
        m = _COMMITS_RE.match(path)
        if m:
            self._post_commits(m.group(1), body)
            return
        self._json(404, {"message": "404 Not Found"})

    # ---- 故障注入控制面（§4.6 开关；_control 面永不被注入，否则解不开）----

    def _post_config(self, body: dict) -> None:
        unknown = sorted(set(body) - set(DEFAULT_CONFIG))
        if unknown:
            self._json(400, {"message": f"unknown config keys: {unknown}"})
            return
        bad = []
        for k in ("latency_ms", "response_hold_ms"):
            v = body.get(k)
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                bad.append(k)
        rs = body.get("rest_status", None)
        if "rest_status" in body and rs is not None and not isinstance(rs, int):
            bad.append("rest_status")
        wt = body.get("webhook_timing", None)
        if wt is not None and wt not in WEBHOOK_TIMINGS:
            bad.append("webhook_timing")
        if bad:
            self._json(400, {"message": f"bad values for: {sorted(set(bad))}"})
            return
        with self.state.lock:
            self.state.config.update(body)
            out = dict(self.state.config)
        self._json(200, out)

    def _fault_gate(self) -> bool:
        """True = 数据面请求已被注入截停（零业务副作用）。"""
        cfg = self.state.config
        lat = cfg.get("latency_ms") or 0
        if lat:
            time.sleep(lat / 1000)
        status = cfg.get("rest_status")
        if status:
            self._json(int(status), {"message": f"mock injected {status}"})
            return True
        return False

    # ---- webhook 发送器（§4.6：构造 push payload + X-Gitlab-Token 推送到接收器）----

    def _push_payload(self, pid: int, branch: str, commits: list[dict], before: str) -> dict:
        return {
            "object_kind": "push",
            "event_name": "push",
            "before": before,
            "after": commits[-1]["id"],
            "ref": f"refs/heads/{branch}",
            "checkout_sha": commits[-1]["id"],
            "user_id": 1,
            "user_name": "tl-mock",
            "user_email": "tl-mock@example.com",
            "project_id": pid,
            "project": {
                "id": pid,
                "name": self.state.projects[pid].rsplit("/", 1)[-1],
                "path_with_namespace": self.state.projects[pid],
                "web_url": f"http://mockgitlab/{self.state.projects[pid]}",
                "git_http_url": f"http://mockgitlab/{self.state.projects[pid]}.git",
                "default_branch": DEFAULT_BRANCH,
            },
            "commits": [
                {
                    "id": c["id"],
                    "message": c["message"],
                    "timestamp": c["created_at"],
                    "url": f"http://mockgitlab/{self.state.projects[pid]}/commit/{c['id']}",
                    "author": {"name": "tl-mock", "email": "tl-mock@example.com"},
                    "added": [p for p in c["paths"]],
                    "modified": [],
                    "removed": [],
                }
                for c in commits
            ],
            "total_commits_count": len(commits),
        }

    def _emit_push(self, body: dict) -> None:
        pid = self.state.resolve_project(str(body.get("project", "1")))
        if pid is None:
            self._json(400, {"message": "unknown project"})
            return
        branch = body.get("branch") or DEFAULT_BRANCH
        target = body.get("url")  # 缺省=不推送，仅返回 payload（本地断言用）
        token = body.get("token") or self.state.config["token"]
        with self.state.lock:
            hist = [
                c for c in self.state.commits if c["project_id"] == pid and c["branch"] == branch
            ]  # 新→旧
            wanted = body.get("commits")
            if wanted is None:
                if not hist:
                    self._json(400, {"message": f"no commit on {branch}"})
                    return
                picks = [hist[0]]
            else:
                by_id = {c["id"]: c for c in hist}
                missing = [w for w in wanted if w not in by_id]
                if missing:
                    self._json(400, {"message": f"unknown commit ids: {missing}"})
                    return
                picks = [by_id[w] for w in wanted]  # 给定顺序
            first_idx = hist.index(picks[0])
            before = hist[first_idx + 1]["id"] if first_idx + 1 < len(hist) else ZERO_SHA
            payload = self._push_payload(pid, branch, picks, before)
        delivered = None
        if target:
            delivered = self._deliver(target, payload, token)
            with self.state.lock:
                self.state.deliveries.append(delivered)
        self._json(200, {"emitted": bool(target), "delivery": delivered, "payload": payload})

    def _deliver(self, target: str, payload: dict, token: str) -> dict:
        """POST 一个 push payload 到 target，返回 delivery 记录（不抛异常——
        接收器的错也要如实记进 deliveries）。"""
        try:
            req = urllib.request.Request(
                target,
                data=json.dumps(payload).encode(),
                method="POST",
                headers={"Content-Type": "application/json", "X-Gitlab-Token": token},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return {"url": target, "status": resp.status}
        except Exception as e:  # noqa: BLE001
            return {"url": target, "status": getattr(e, "code", 0), "error": str(e)}

    def _auto_emit(self, pid: int, branch: str, commit: dict) -> None:
        """commit 成功后的自动推送（§4.6 三时序之 ①②③ 的 webhook 腿）。"""
        cfg = self.state.config
        with self.state.lock:
            hist = [
                c for c in self.state.commits if c["project_id"] == pid and c["branch"] == branch
            ]
            idx = hist.index(commit)
            before = hist[idx + 1]["id"] if idx + 1 < len(hist) else ZERO_SHA
            payload = self._push_payload(pid, branch, [commit], before)
        delivery = self._deliver(cfg["auto_emit_url"], payload, cfg["token"])
        with self.state.lock:
            self.state.deliveries.append(delivery)

    def _post_commits(self, seg: str, body: dict) -> None:
        pid = self._resolve_or_404(seg)
        if pid is None:
            return
        branch = body.get("branch") or DEFAULT_BRANCH
        message = body.get("commit_message") or ""
        actions = body.get("actions")
        if not message or not isinstance(actions, list) or not actions:
            self._json(400, {"message": "commit_message and actions are required"})
            return
        with self.state.lock:
            err = self.state.validate_actions(pid, branch, actions)
            if err:
                self._json(400, {"message": err})  # 整批拒，零应用
                return
            cid = self.state.next_commit_id()
            self.state.apply_actions(pid, branch, actions, cid)
            commit = self.state.record_commit(pid, branch, message, actions, cid)
            timing = self.state.config["webhook_timing"]
            hold = self.state.config["response_hold_ms"] or 0
            auto = bool(self.state.config["auto_emit_url"])
        # §4.6 三时序：pre=③（webhook 先于响应）；post=①（响应后 webhook，hold=0）
        # 或 ②（hold 拉长 → 客户端超时，webhook 落在超时之后）
        if timing == "pre" and auto:
            self._auto_emit(pid, branch, commit)
        if hold:
            time.sleep(hold / 1000)
        try:
            self._json(
                200,
                {
                    "id": commit["id"],
                    "title": commit["title"],
                    "message": commit["message"],
                    "created_at": commit["created_at"],
                    "web_url": f"http://{self.headers.get('Host', '')}/tl-mock/commit/{commit['id']}",
                },
            )
        except OSError:
            pass  # ②：客户端先挂了——响应写不出去不影响 webhook 腿继续
        if timing == "post" and auto:
            self._auto_emit(pid, branch, commit)


def make_server(host: str = "127.0.0.1", port: int = 0, state: State | None = None):
    """返回 (httpd, state)。port=0 → 随机可用端口（测试隔离用）。"""
    st = state or State()
    handler = lambda *args, **kwargs: MockGitLabHandler(st, *args, **kwargs)  # noqa: E731
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    httpd.state = st
    return httpd, st
