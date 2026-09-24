"""S0-6a Step 1：§4.6 数据面四端点（项目解析 / 读文件 / 提交 / 查历史）。

核心反假绿点：**create 撞已存在 → 400 且整批零应用**（§4.6 幂等测试的断言点，
部分应用会让幂等守卫测成谎话）。每测试起独立随机端口服务，互不串态。
"""
import base64
import hashlib
import threading

import httpx
import pytest

from mocks.gitlab_fake import make_server


@pytest.fixture
def url():
    httpd, _state = make_server(port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _commit(url, actions, message="rel: test", branch="main", pid="1"):
    return httpx.post(f"{url}/api/v4/projects/{pid}/repository/commits",
                      json={"branch": branch, "commit_message": message, "actions": actions})


# ---- 项目解析 ----

def test_project_resolve_by_id_and_path(url):
    r = httpx.get(f"{url}/api/v4/projects/1")
    assert r.status_code == 200 and r.json()["path_with_namespace"] == "tl/apps-entry"
    r2 = httpx.get(f"{url}/api/v4/projects/tl%2Fplaybooks")   # OQ-1 两库并存
    assert r2.status_code == 200 and r2.json()["id"] == 2
    assert httpx.get(f"{url}/api/v4/projects/no%2Fsuch").status_code == 404


# ---- 提交 + 读回 ----

def test_create_then_read_fields(url):
    content = "app: web\nversion: 1.2.3\n"
    r = _commit(url, [{"action": "create", "file_path": "releases/TL-0001.yaml",
                       "content": content}], message="release(TL-0001): v1.2.3")
    assert r.status_code == 200
    cid = r.json()["id"]
    assert len(cid) == 40 and r.json()["title"] == "release(TL-0001): v1.2.3"

    g = httpx.get(f"{url}/api/v4/projects/1/repository/files/releases%2FTL-0001.yaml?ref=main")
    assert g.status_code == 200
    d = g.json()
    assert base64.b64decode(d["content"]).decode() == content        # 逐字节回读
    assert d["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert d["commit_id"] == d["last_commit_id"] == cid
    assert d["size"] == len(content.encode()) and d["encoding"] == "base64"
    assert len(d["blob_id"]) == 40


def test_read_missing_variants_404(url):
    assert httpx.get(f"{url}/api/v4/projects/1/repository/files/nope?ref=main").status_code == 404
    _commit(url, [{"action": "create", "file_path": "a.txt", "content": "x"}], branch="main")
    # 存在 main 不存在 dev：按 ref 隔离
    assert httpx.get(f"{url}/api/v4/projects/1/repository/files/a.txt?ref=dev").status_code == 404


# ---- 400 断言点（幂等核心）----

def test_duplicate_create_400_original_untouched(url):
    _commit(url, [{"action": "create", "file_path": "x.yaml", "content": "first"}])
    r = _commit(url, [{"action": "create", "file_path": "x.yaml", "content": "second"}])
    assert r.status_code == 400
    assert "already exists" in r.json()["message"]                   # GitLab 原文措辞
    g = httpx.get(f"{url}/api/v4/projects/1/repository/files/x.yaml?ref=main").json()
    assert base64.b64decode(g["content"]).decode() == "first"        # 拒 = 零副作用


def test_mixed_batch_all_or_nothing(url):
    _commit(url, [{"action": "create", "file_path": "exists.txt", "content": "1"}])
    r = _commit(url, [{"action": "create", "file_path": "new.txt", "content": "2"},
                      {"action": "create", "file_path": "exists.txt", "content": "3"}])
    assert r.status_code == 400
    assert httpx.get(f"{url}/api/v4/projects/1/repository/files/new.txt?ref=main").status_code == 404
    kept = httpx.get(f"{url}/api/v4/projects/1/repository/files/exists.txt?ref=main").json()
    assert base64.b64decode(kept["content"]).decode() == "1"         # 批量里也没被应用


def test_update_requires_existing_delete_removes(url):
    assert _commit(url, [{"action": "update", "file_path": "ghost", "content": "x"}]).status_code == 400
    _commit(url, [{"action": "create", "file_path": "app.yaml", "content": "v1"}])
    r = _commit(url, [{"action": "update", "file_path": "app.yaml", "content": "v2"}])
    cid = r.json()["id"]
    g = httpx.get(f"{url}/api/v4/projects/1/repository/files/app.yaml?ref=main").json()
    assert base64.b64decode(g["content"]).decode() == "v2"
    assert g["last_commit_id"] == cid and g["commit_id"] == cid
    assert _commit(url, [{"action": "delete", "file_path": "app.yaml"}]).status_code == 200
    assert httpx.get(f"{url}/api/v4/projects/1/repository/files/app.yaml?ref=main").status_code == 404
    assert _commit(url, [{"action": "delete", "file_path": "app.yaml"}]).status_code == 400


def test_bad_request_shapes(url):
    r = httpx.post(f"{url}/api/v4/projects/1/repository/commits",
                   json={"branch": "main", "commit_message": "", "actions": []})
    assert r.status_code == 400
    assert _commit(url, [{"action": "frobnicate", "file_path": "p"}]).status_code == 400


# ---- 按路径查历史 ----

def test_commits_list_filter_and_page(url):
    _commit(url, [{"action": "create", "file_path": "a.txt", "content": "1"}], message="one")
    _commit(url, [{"action": "create", "file_path": "b.txt", "content": "2"}], message="two")
    _commit(url, [{"action": "update", "file_path": "a.txt", "content": "3"}], message="three")
    ra = httpx.get(f"{url}/api/v4/projects/1/repository/commits?path=a.txt").json()
    assert [c["title"] for c in ra] == ["three", "one"]              # 新→旧 + path 过滤
    allc = httpx.get(f"{url}/api/v4/projects/1/repository/commits").json()
    assert [c["title"] for c in allc] == ["three", "two", "one"]
    for c in allc:
        assert set(c) >= {"id", "title", "created_at"}
    p2 = httpx.get(f"{url}/api/v4/projects/1/repository/commits?per_page=2&page=2").json()
    assert [c["title"] for c in p2] == ["one"]                       # 分页窗口
