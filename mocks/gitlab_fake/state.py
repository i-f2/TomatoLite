"""内存仓库态（拍板③：无持久化，重启即空）。

OQ-1 物理分离：入口库与资产库**两个 project**都种进去（§4.7② 误配 webhook
串库的用例素材）。提交原子性：actions 先**整批校验后整批应用**——部分应用再返
400 会把"幂等守卫"测成谎话（§4.6 核心断言点的前提）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import threading

SEED_PROJECTS = {1: "tl/apps-entry", 2: "tl/playbooks"}  # id → path（OQ-1 两库）
DEFAULT_BRANCH = "main"

DEFAULT_CONFIG = {
    "latency_ms": 0,
    "rest_status": None,          # 非空 → 数据面端点强制返回该状态码
    "webhook_timing": "none",     # pre=③ | post=① | none=不发（手动 emit_push 不受影响）
    "response_hold_ms": 0,        # ②：应用 commit 后按住响应（配合 post 即"落库→超时→webhook"）
    "auto_emit_url": None,        # 非空且 timing!=none 时，commit 成功即向该 url 发 push
    "token": "mock-gitlab-token",  # emit 默认 X-Gitlab-Token（请求级可覆盖，供坏 token 用例）
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_blob_id(content: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(content))
    h.update(content)
    return h.hexdigest()


class State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._commits = itertools.count(1)
        self._pushes = itertools.count(1)
        self.projects: dict[int, str] = {}
        self.by_path: dict[str, int] = {}
        self.files: dict[tuple[int, str, str], dict] = {}   # (pid, branch, path)
        self.commits: list[dict] = []                        # 新→旧插入
        self.deliveries: list[dict] = []
        self.config: dict = dict(DEFAULT_CONFIG)
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.files.clear()
            self.commits.clear()
            self.deliveries.clear()
            self.config = dict(DEFAULT_CONFIG)
            self.projects = dict(SEED_PROJECTS)
            self.by_path = {p: i for i, p in SEED_PROJECTS.items()}

    # ---- 项目解析（数字 id 或 urlencoded path 都认，GitLab 同口径）----

    def resolve_project(self, seg: str) -> int | None:
        seg = seg.strip("/")
        if seg.isdigit():
            pid = int(seg)
            return pid if pid in self.projects else None
        return self.by_path.get(seg)

    def next_commit_id(self) -> str:
        return hashlib.sha1(f"tl-mock-commit-{next(self._commits)}".encode()).hexdigest()

    def next_push_event_id(self) -> int:
        return next(self._pushes)

    # ---- 提交 ----

    def validate_actions(self, pid: int, branch: str, actions: list[dict]) -> str | None:
        """整批预校验，返回错误消息（None=通过）。只读不加锁——调用方持锁。"""
        for a in actions:
            act, path = a.get("action"), a.get("file_path")
            if act not in ("create", "update", "delete"):
                return f"Invalid action '{act}'"
            if not path:
                return "file_path is required"
            exists = (pid, branch, path) in self.files
            if act == "create" and exists:
                return "A file with this name already exists"
            if act in ("update", "delete") and not exists:
                return "A file with this name doesn't exist"
        return None

    def apply_actions(self, pid: int, branch: str, actions: list[dict], cid: str) -> None:
        for a in actions:
            key = (pid, branch, a["file_path"])
            if a["action"] == "delete":
                del self.files[key]
                continue
            content = a.get("content", "").encode()
            self.files[key] = {
                "content": content,
                "blob_id": git_blob_id(content),
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "commit_id": cid,
                "last_commit_id": cid,
            }

    def record_commit(self, pid: int, branch: str, message: str, actions: list[dict], cid: str) -> dict:
        commit = {
            "id": cid,
            "title": message.splitlines()[0] if message else "",
            "message": message,
            "created_at": now_iso(),
            "project_id": pid,
            "branch": branch,
            "paths": [a["file_path"] for a in actions],
        }
        self.commits.insert(0, commit)   # 新→旧
        return commit

    def public_commits(self, pid: int, path: str | None, per_page: int, page: int) -> list[dict]:
        rows = [c for c in self.commits
                if c["project_id"] == pid and (path is None or path in c["paths"])]
        start = (page - 1) * per_page
        return [{"id": c["id"], "title": c["title"], "created_at": c["created_at"]}
                for c in rows[start:start + per_page]]
