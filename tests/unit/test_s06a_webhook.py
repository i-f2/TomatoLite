"""S0-6a Step 2：emit_push 发送器 + deliveries 断言口。
捕获接收器（真 HTTP，带记录）证明"推送**送达且可解析**"，不是只看 mock 自说。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from mocks.gitlab_fake import make_server
from mocks.gitlab_fake.server import ZERO_SHA


class _Capturer(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n))
        self.server.received.append(
            {"token": self.headers.get("X-Gitlab-Token"), "payload": payload})
        self.send_response(self.server.status)  # 每实例配置，测试间不串类属性
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


@pytest.fixture
def srv():
    httpd, state = make_server(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cap = ThreadingHTTPServer(("127.0.0.1", 0), _Capturer)
    cap.received = []
    cap.status = 200
    cap.daemon_threads = True
    threading.Thread(target=cap.serve_forever, daemon=True).start()
    yield {
        "url": f"http://127.0.0.1:{httpd.server_address[1]}",
        "recv": f"http://127.0.0.1:{cap.server_address[1]}",
        "cap": cap, "state": state, "httpd": httpd,
    }
    httpd.shutdown(); httpd.server_close()
    cap.shutdown(); cap.server_close()


def _commit(url, path, message, pid="1"):
    return httpx.post(f"{url}/api/v4/projects/{pid}/repository/commits",
                      json={"branch": "main", "commit_message": message,
                            "actions": [{"action": "create", "file_path": path,
                                         "content": f"c-{path}"}]}).json()


def test_emit_push_delivers_parseable_payload_with_token(srv):
    c = _commit(srv["url"], "releases/TL-0002.yaml", "release(TL-0002): v1")
    r = httpx.post(f"{srv['url']}/_control/emit_push",
                   json={"url": srv["recv"], "project": 1, "branch": "main"})
    assert r.status_code == 200 and r.json()["emitted"] is True
    assert len(srv["cap"].received) == 1
    got = srv["cap"].received[0]
    assert got["token"] == "mock-gitlab-token"            # 默认 = config.token
    p = got["payload"]
    assert p["object_kind"] == "push" and p["event_name"] == "push"
    assert p["ref"] == "refs/heads/main"
    assert p["before"] == ZERO_SHA and p["after"] == c["id"]   # 首推 before=全零（非 after）
    assert p["checkout_sha"] == c["id"]
    assert p["project_id"] == 1
    assert p["project"]["path_with_namespace"] == "tl/apps-entry"
    assert [x["id"] for x in p["commits"]] == [c["id"]]
    assert p["commits"][0]["added"] == ["releases/TL-0002.yaml"]
    assert p["total_commits_count"] == 1


def test_token_override_for_bad_token_cases(srv):
    """M1-24 坏 token 用例的素材：token 必须可请求级覆盖（错误值如实送达）。"""
    _commit(srv["url"], "a", "m1")
    r = httpx.post(f"{srv['url']}/_control/emit_push",
                   json={"url": srv["recv"], "project": 1, "token": "WRONG-TOKEN"})
    assert r.status_code == 200
    assert srv["cap"].received[0]["token"] == "WRONG-TOKEN"


def test_multi_commit_push_id_not_after(srv):
    """§4.7③：commits[].id 与 after 是两个键——一次 push 两 commit，去重键素材齐。"""
    c1 = _commit(srv["url"], "a", "first")
    c2 = _commit(srv["url"], "b", "second")
    r = httpx.post(f"{srv['url']}/_control/emit_push",
                   json={"url": srv["recv"], "project": 1, "commits": [c1["id"], c2["id"]]})
    p = r.json()["payload"]
    assert [x["id"] for x in p["commits"]] == [c1["id"], c2["id"]]
    assert p["after"] == c2["id"] and p["before"] == ZERO_SHA    # 段首的父=零（历史首推）
    assert p["total_commits_count"] == 2
    # 逆序指定：mock 不重排不拦止，before/after 按字面规则退化（before=段首之父，after=末位）
    r2 = httpx.post(f"{srv['url']}/_control/emit_push",
                    json={"url": srv["recv"], "project": 1, "commits": [c2["id"], c1["id"]]})
    assert [x["id"] for x in r2.json()["payload"]["commits"]] == [c2["id"], c1["id"]]
    assert r2.json()["payload"]["after"] == r2.json()["payload"]["before"] == c1["id"]


def test_before_is_parent_of_first_pick(srv):
    c1 = _commit(srv["url"], "a", "first")
    c2 = _commit(srv["url"], "b", "second")
    r = httpx.post(f"{srv['url']}/_control/emit_push",
                   json={"url": srv["recv"], "project": 1, "commits": [c2["id"]]})
    assert r.json()["payload"]["before"] == c1["id"]      # 单 commit 非首推：before=父


def test_deliveries_recorded_including_failures(srv):
    _commit(srv["url"], "a", "m")
    httpx.post(f"{srv['url']}/_control/emit_push", json={"url": srv["recv"], "project": 1})
    srv["cap"].status = 500
    httpx.post(f"{srv['url']}/_control/emit_push", json={"url": srv["recv"], "project": 1})
    dead = "http://127.0.0.1:1/nowhere"                    # 必然拒连
    r3 = httpx.post(f"{srv['url']}/_control/emit_push", json={"url": dead, "project": 1})
    assert r3.json()["delivery"]["error"]
    d = httpx.get(f"{srv['url']}/_control/deliveries").json()["deliveries"]
    assert len(d) == 3
    assert [x["status"] for x in d[:2]] == [200, 500]      # 对端 5xx 如实记
    assert d[2]["url"] == dead and "error" in d[2]


def test_emit_validations(srv):
    assert httpx.post(f"{srv['url']}/_control/emit_push",
                      json={"project": 99}).status_code == 400          # 未知 project
    assert httpx.post(f"{srv['url']}/_control/emit_push",
                      json={"project": 1}).status_code == 400           # 库上无 commit
    c = _commit(srv["url"], "a", "m")
    assert httpx.post(f"{srv['url']}/_control/emit_push",
                      json={"project": 1, "commits": ["f" * 40]}).status_code == 400
    r = httpx.post(f"{srv['url']}/_control/emit_push", json={"project": 1})  # 无 url=只回 payload
    assert r.status_code == 200 and r.json()["emitted"] is False
    assert r.json()["payload"]["after"] == c["id"]
    assert httpx.get(f"{srv['url']}/_control/deliveries").json()["deliveries"] == []
