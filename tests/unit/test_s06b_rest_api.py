"""S0-6b Step 1：生产侧 `core/hmac.py` + §3.8 REST 数据面。

生命周期开关（queue_mode/build_mode/build_result/console_kb）经 fixture 的
**进程内 state** 设置——`_control/config` HTTP 应用面在 Step 3（届时本文件的
开关也改走 HTTP 复验）。hmac 本体先做双相断言（§0.7）：对→验得过，篡改一字节/
出窗→必拒。
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

import apps.core.hmac as tl_hmac
from mocks.jenkins_fake import make_server

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def jk():
    httpd, state = make_server(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{httpd.server_address[1]}", "state": state}
    httpd.shutdown(); httpd.server_close()


def _trigger(url, code="TL-20260924-001", sha="c0ffee" + "0" * 34, job="tl-release", **qs):
    r = httpx.post(f"{url}/job/{job}/buildWithParameters",
                   params={"TICKET_CODE": code, "COMMIT_SHA": sha, **qs})
    return r


def _qid(r):
    return r.json()["queue_id"]


def _wait_settled(url, job, n, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = httpx.get(f"{url}/job/{job}/{n}/api/json").json()
        if not d["building"]:
            return d
        time.sleep(0.03)
    raise AssertionError(f"build {job}#{n} 未在 {timeout}s 内落终态")


# ---- A. core/hmac 本体（生产模块，mock 与 M2-05 共用的单一实现）----

def test_hmac_roundtrip_and_tamper_rejection():
    secret = "s3cr3t-per-job"
    now = int(time.time())
    raw = b'{"event":"build_finished","number":3,"k": "v"}'
    h = tl_hmac.sign(secret, now, raw)
    assert h.startswith("sha256=") and len(h) == 7 + 64
    assert tl_hmac.verify(secret, raw, h, now)                      # 相①对：过
    assert not tl_hmac.verify(secret, raw + b" ", h, now)           # 字节级篡改
    assert not tl_hmac.verify(secret, raw.replace(b'"number":3', b'"number":4'), h, now)
    flipped = h[:-1] + ("a" if h[-1] != "a" else "b")
    assert not tl_hmac.verify(secret, raw, flipped, now)            # 签名篡改
    assert not tl_hmac.verify("wrong-secret", raw, h, now)          # 错 secret
    assert not tl_hmac.verify(secret, raw, h[7:], now)              # 无 sha256= 前缀
    assert not tl_hmac.verify(secret, raw, None, now)               # 缺头
    assert not tl_hmac.verify(secret, raw, h, "not-a-number")       # 坏 ts
    assert not tl_hmac.verify(secret, raw, tl_hmac.sign(secret, now - 400, raw),
                              now - 400, now=now)                   # 出窗（>300s）
    assert tl_hmac.verify(secret, raw, tl_hmac.sign(secret, now - 299, raw),
                          now - 299, now=now)                       # 窗内上沿


def test_sign_rejects_non_bytes_raw():
    """§3.3 首坑（loads→dumps 重序列化）摁在类型层：str 直接 TypeError。"""
    with pytest.raises(TypeError):
        tl_hmac.sign("k", 1, "not-bytes")


def test_hmac_zero_django_dual_assertion():
    """与 integrations/ 同口径：源码 grep + 无 settings 子进程 import。"""
    src = (ROOT / "apps/core/hmac.py").read_text()
    assert not any("import django" in l or "from django" in l   # 同 S0-4 口径：查导入语句
                   for l in src.splitlines())
    code = ("import sys; import apps.core.hmac; "
            "print('CLEAN' if 'django' not in sys.modules else 'DIRTY')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT)
    assert r.stdout.strip().endswith("CLEAN"), r


# ---- B. §3.8 端点表 ----

def test_trigger_promote_queue_mapping_and_settle(jk):
    url = jk["url"]
    jk["state"].config["build_duration_ms"] = 200
    g = httpx.get(f"{url}/job/tl-release/api/json")
    assert g.status_code == 200 and g.json()["lastBuild"]["number"] == 2   # 种子两单
    r = _trigger(url)
    assert r.status_code == 201
    loc = r.json()
    assert list(loc.keys()) == ["queue_id"]        # 201 不回报 build_no（边 31 素材）
    qid = _qid(r)
    assert r.headers["Location"].endswith(f"/queue/item/{qid}/")
    q = httpx.get(f"{url}/queue/item/{qid}/api/json").json()
    assert q["executable"]["number"] == 3 and q["cancelled"] is False
    mid = httpx.get(f"{url}/job/tl-release/3/api/json")
    d = mid.json()
    assert "result" in d and d["result"] is None and d["building"] is True  # §3.8 关键
    done = _wait_settled(url, "tl-release", 3)
    assert done["result"] == "SUCCESS" and done["duration"] >= 100
    assert done["displayName"] == "#3 TL-20260924-001"
    assert done["actions"][0]["parameters"] == [
        {"name": "TICKET_CODE", "value": "TL-20260924-001"},
        {"name": "COMMIT_SHA", "value": "c0ffee" + "0" * 34}]
    assert done["changeSet"]["items"] == [{"commitId": "c0ffee" + "0" * 34}]  # §3.4 路径③
    assert httpx.get(f"{url}/job/tl-release/api/json").json()["lastBuild"]["number"] == 3


def test_params_via_form_body(jk):
    r = httpx.post(f"{jk['url']}/job/tl-release/buildWithParameters",
                   data={"TICKET_CODE": "TL-VIA-BODY", "COMMIT_SHA": "b" * 40})
    assert r.status_code == 201
    q = httpx.get(f"{jk['url']}/queue/item/{_qid(r)}/api/json").json()
    b = httpx.get(f"{jk['url']}/job/tl-release/{q['executable']['number']}/api/json").json()
    assert any(p["value"] == "TL-VIA-BODY" for p in b["actions"][0]["parameters"])


def test_queue_hang_no_build_created(jk):
    jk["state"].config["queue_mode"] = "hang"
    r = _trigger(jk["url"])
    q = httpx.get(f"{jk['url']}/queue/item/{_qid(r)}/api/json").json()
    assert q["executable"] is None and q["why"] and q["cancelled"] is False
    assert httpx.get(f"{jk['url']}/job/tl-release/3/api/json").status_code == 404  # 无号可查


def test_queue_cancelled_and_vanish(jk):
    jk["state"].config["queue_mode"] = "cancelled"
    r = _trigger(jk["url"])
    q = httpx.get(f"{jk['url']}/queue/item/{_qid(r)}/api/json").json()
    assert q["cancelled"] is True and q["executable"] is None          # 边 31 判定素材
    jk["state"].config["queue_mode"] = "vanish"
    r = _trigger(jk["url"])
    assert r.status_code == 201
    assert httpx.get(f"{jk['url']}/queue/item/{_qid(r)}/api/json").status_code == 404


def test_build_hang_stays_building_forever(jk):
    jk["state"].config["build_mode"] = "hang"
    r = _trigger(jk["url"])
    q = httpx.get(f"{jk['url']}/queue/item/{_qid(r)}/api/json").json()
    n = q["executable"]["number"]
    time.sleep(0.25)
    d = httpx.get(f"{jk['url']}/job/tl-release/{n}/api/json").json()
    assert d["building"] is True and d["result"] is None               # 永不落锤


def test_failure_outcome_and_stage_status(jk):
    jk["state"].config["build_result"] = "FAILURE"
    r = _trigger(jk["url"])
    n = httpx.get(f"{jk['url']}/queue/item/{_qid(r)}/api/json").json()["executable"]["number"]
    done = _wait_settled(jk["url"], "tl-release", n)
    assert done["result"] == "FAILURE"
    st = httpx.get(f"{jk['url']}/job/tl-release/{n}/wfapi/describe").json()["stages"]
    assert [s["name"] for s in st] == ["Checkout", "Build", "Deploy"]
    assert st[-1]["status"] == "FAILED"


def test_stop_semantics(jk):
    jk["state"].config["build_duration_ms"] = 3000
    r = _trigger(jk["url"])
    n = httpx.get(f"{jk['url']}/queue/item/{_qid(r)}/api/json").json()["executable"]["number"]
    s = httpx.post(f"{jk['url']}/job/tl-release/{n}/stop")
    assert s.status_code == 200 and s.json()["outcome"] == "aborted"
    d = httpx.get(f"{jk['url']}/job/tl-release/{n}/api/json").json()
    assert d["result"] == "ABORTED" and d["building"] is False
    time.sleep(0.1)                                                     # 迟到的 timer 无效果
    assert httpx.get(f"{jk['url']}/job/tl-release/{n}/api/json").json()["result"] == "ABORTED"
    s2 = httpx.post(f"{jk['url']}/job/tl-release/{n}/stop")
    assert s2.status_code == 200 and s2.json()["outcome"] == "already"  # §0.4：终态 stop 不算错
    st = httpx.get(f"{jk['url']}/job/tl-release/{n}/wfapi/describe").json()["stages"]
    assert "ABORTED" in [x["status"] for x in st]
    assert httpx.post(f"{jk['url']}/job/ghost/1/stop").status_code == 404


def test_not_found_shapes(jk):
    assert httpx.get(f"{jk['url']}/job/ghost/api/json").status_code == 404
    assert httpx.get(f"{jk['url']}/job/tl-release/999/api/json").status_code == 404
    assert httpx.get(f"{jk['url']}/queue/item/7777/api/json").status_code == 404
    assert httpx.get(f"{jk['url']}/nonsense").status_code == 404


def test_console_text_size_and_wfapi_progress(jk):
    jk["state"].config["build_duration_ms"] = 4000
    jk["state"].config["console_kb"] = 64
    r = _trigger(jk["url"])
    n = httpx.get(f"{jk['url']}/queue/item/{_qid(r)}/api/json").json()["executable"]["number"]
    mid = httpx.get(f"{jk['url']}/job/tl-release/{n}/wfapi/describe").json()["stages"]
    assert [s["status"] for s in mid] == ["COMPLETED", "IN_PROGRESS", "NOT_EXECUTED"]
    jk["state"].config["build_duration_ms"] = 0                        # 落锤改由 stop 触发
    httpx.post(f"{jk['url']}/job/tl-release/{n}/stop")
    c = httpx.get(f"{jk['url']}/job/tl-release/{n}/consoleText")
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/plain")
    assert len(c.content) >= 64 * 1024
    assert b"Started by Tomatolite" in c.content[:80]
