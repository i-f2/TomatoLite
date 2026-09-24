"""S0-9 临时联调栈 bootstrap（§0.6b）：对真 GitLab/Jenkins 做种子化与可达性取证。

定位纪律（§6 第 7 条）：本脚本只服务临时栈的人工联调——**任何 pytest/CI 不得依赖
它**，栈随时可 down -v。它干的是"假设验货"：通道 A 的 登录→建令牌→建仓→挂
webhook→API 代提交 这条 REST 链，每一步的真实状态码就是设计口径的实据。

密钥纪律：口令/令牌只读自 .env（gitignored）、PAT 只回写 .env，全程不落 stdout。

webhook 目标（拍板②挂账）：/api/channels_a/webhook/gitlab/ 为**暂定路径**，M1-24
落地时以代码为准并回来对齐本脚本；此刻 tl-app 对该路径的 404 正是本步的证据本体
（"GitLab 能打到平台端口"，而非"平台已能收单"）。

两个子命令都幂等可复跑（down -v 重建后直接再跑一遍即可恢复种子态）。
Jenkins 侧凭据链（unlock→s09ops→API token→entry-deploy 构建→HMAC 回调）的
实测流形与四五六课，见各函数 docstring。

用法（compose 栈需 healthy）：
  .venv/bin/python scripts/s0_9_bootstrap.py gitlab
  .venv/bin/python scripts/s0_9_bootstrap.py jenkins
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"

# GitLab 出站 webhook 打宿主平台：容器视角下 tl-app 在 host.docker.internal:8000
# （Step 1 实据：tl-gitlab/tl-jenkins 两容器 curl healthz 皆 200）。
PLATFORM_BASE = "http://host.docker.internal:8000"
HOOK_PATH = "/api/channels_a/webhook/gitlab/"  # 暂定路径，挂账 M1-24（见 docstring）

GROUP_PATH = "tl"
# (仓库 path, 演示文件, 内容)——apps-entry=制品仓（manifest 代提交面），
# playbooks=自动化仓（§4 OQ-1 双仓假设的落地对象）。
REPOS = [
    ("apps-entry", "release/v1/manifest.yml", "version: 1\nchannel_a: s09-provision\n"),
    ("playbooks", "deploy/entry.sh", "#!/bin/sh\necho s09-provision\n"),
]


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in ENV_FILE.read_text().splitlines():
        if m := re.match(r"^([A-Z0-9_]+)=(.*)$", ln):
            out[m.group(1)] = m.group(2)
    return out


def update_env(**kv: str) -> None:
    """原子回写 .env 指定键（只回写键位，值不落 stdout——密钥纪律）。
    栈的一次性 seed 键若 .env 尚不存在则补写一行（槽位以 .env.example 为准）。"""
    text = ENV_FILE.read_text()
    for k, v in kv.items():
        pat = re.compile(rf"^{k}=.*$", re.M)
        if pat.search(text):
            text = pat.sub(f"{k}={v}", text)
        else:
            text = text.rstrip("\n") + f"\n{k}={v}\n"
    tmp = ENV_FILE.with_suffix(".env.tmp")
    tmp.write_text(text)
    os.replace(tmp, ENV_FILE)


def _req(
    client: httpx.Client, method: str, url: str, *, ok: tuple[int, ...], **kw
) -> httpx.Response:
    r = client.request(method, url, **kw)
    if r.status_code not in ok:
        raise RuntimeError(f"{method} {url} -> {r.status_code}: {r.text[:300]}")
    return r


def _csrf(client: httpx.Client, html: str) -> str:
    if m := re.search(r'name="csrf-token" content="([^"]+)"', html):
        return m.group(1)
    raise RuntimeError("页面里找不到 csrf-token meta——GitLab 登录面假设破裂，如实上报")


def gitlab_session(base: str, root_password: str) -> httpx.Client:
    """root 登录拿会话（"人在看"要用的同一入口；PAT 经此会话签发）。"""
    client = httpx.Client(base_url=base, follow_redirects=False, timeout=20)
    r = _req(client, "GET", "/users/sign_in", ok=(200,))
    tok = _csrf(client, r.text)
    r = _req(
        client,
        "POST",
        "/users/sign_in",
        ok=(201, 302),
        headers={"Origin": base, "Referer": f"{base}/users/sign_in"},
        data={
            "authenticity_token": tok,
            "user[login]": "root",
            "user[password]": root_password,
            "user[remember_me]": "0",
        },
    )
    loc = r.headers.get("location", "")
    if r.status_code != 302 or "/users/sign_in" in loc:
        # 实据：成功=302 且不回 sign_in——root 首登会被送进 17.3+ 的首建群组向导
        # （/admin/registrations/groups/new），同算认证成功；口令错则 302 回 sign_in。
        raise RuntimeError(f"root 登录失败：status={r.status_code} loc={loc}")
    print(f"login: 302 → {loc.split(':8929')[-1]}（认证成立）")
    r = _req(client, "GET", "/dashboard/projects", ok=(200, 302))
    if r.status_code == 302:
        raise RuntimeError(f"登录后被重定向到 {r.headers.get('location')}——疑似强制 2FA，Hard Stop")
    return client


def gitlab_pat() -> str:
    """签发 api 域 PAT，7 天有效——栈随 down -v 作废。

    实据第四课：/api/v4/personal_access_tokens/self 只有 GET/DELETE/rotate，**没有**
    POST 创建端点（UI 走的是非 API 表单路由）——API 化签发没有旁路，退回官方
    rails runner 配方：t.token 仅创建瞬间可得明文。stdout 里带 TOKEN: 前缀提取，
    明文只进返回值与 .env，不落脚本输出。"""
    out = subprocess.run(
        [
            "docker",
            "exec",
            "tl-gitlab",
            "gitlab-rails",
            "runner",
            "u = User.find_by_username('root');"
            " t = PersonalAccessToken.new(name: 's09-bootstrap', user: u,"
            " scopes: [:api], expires_at: Date.current + 7);"
            " t.save!;"
            " puts 'TOKEN:' + t.token",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("TOKEN:")), None)
    if out.returncode != 0 or line is None:
        raise RuntimeError(f"PAT 签发失败 rc={out.returncode}: {(out.stderr or out.stdout)[-300:]}")
    return line.removeprefix("TOKEN:")


def whitelist_platform_host() -> None:
    """实据第三课：GitLab 默认封禁 webhook 出站打内网/本地地址，且 omnibus 无属性可配
    （容器内 grep 遍 cookbook attributes 零命中）——开关只在 DB 面：
    ApplicationSetting.outbound_local_requests_whitelist。save! 即刻生效，无 reconfigure。"""
    out = subprocess.run(
        [
            "docker",
            "exec",
            "tl-gitlab",
            "gitlab-rails",
            "runner",
            "s = ApplicationSetting.current;"
            " s.outbound_local_requests_whitelist = ['host.docker.internal'];"
            " s.save!;"
            " puts s.outbound_local_requests_whitelist.inspect",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if out.returncode != 0:
        raise RuntimeError(f"outbound 白名单写入失败：{out.stderr[-300:]}")
    print(f"whitelist: {out.stdout.strip()}")


def ensure_group_pid(client: httpx.Client) -> int:
    r = client.get(f"/api/v4/groups/{GROUP_PATH}")
    if r.status_code == 200:
        return r.json()["id"]
    _req(
        client,
        "POST",
        "/api/v4/groups",
        ok=(201, 400),
        json={"name": GROUP_PATH, "path": GROUP_PATH, "visibility": "internal"},
    )
    return _req(client, "GET", f"/api/v4/groups/{GROUP_PATH}", ok=(200,)).json()["id"]


def ensure_project(client: httpx.Client, group_id: int, path: str) -> dict:
    r = client.get(f"/api/v4/projects/{GROUP_PATH}%2F{path}")
    if r.status_code == 200:
        return r.json()
    _req(
        client,
        "POST",
        "/api/v4/projects",
        ok=(201, 400),
        json={
            "name": path,
            "path": path,
            "namespace_id": group_id,
            "visibility": "internal",
            "initialize_with_readme": False,
        },
    )
    return _req(client, "GET", f"/api/v4/projects/{GROUP_PATH}%2F{path}", ok=(200,)).json()


def ensure_hook(client: httpx.Client, pid: int, secret: str) -> dict:
    url = PLATFORM_BASE + HOOK_PATH
    hooks = _req(client, "GET", f"/api/v4/projects/{pid}/hooks", ok=(200,)).json()
    for h in hooks:
        if h["url"] == url:
            return h
    return _req(
        client,
        "POST",
        f"/api/v4/projects/{pid}/hooks",
        ok=(201,),
        json={
            "url": url,
            "token": secret,
            "push_events": True,
            "enable_ssl_verification": False,
        },
    ).json()


def ensure_commit(client: httpx.Client, pid: int, file_path: str, content: str) -> str:
    """§4 幂等预检同口径：GET files 探在否 → create/update，API 代提交返回 sha。"""
    q = client.get(f"/api/v4/projects/{pid}/repository/files/{quote(file_path, safe='')}?ref=main")
    action = "update" if q.status_code == 200 else "create"
    if q.status_code not in (200, 404):
        raise RuntimeError(f"files 预检非 200/404：{q.status_code} {q.text[:200]}")
    r = _req(
        client,
        "POST",
        f"/api/v4/projects/{pid}/repository/commits",
        ok=(201, 400),
        json={
            "branch": "main",
            "commit_message": f"S0-9 联调栈演示提交（{action}）",
            "actions": [{"action": action, "file_path": file_path, "content": content}],
        },
    )
    if r.status_code == 400 and "already exists" not in r.text:
        raise RuntimeError(f"commits 400：{r.text[:300]}")
    return r.json()["id"]


def hook_events(client: httpx.Client, pid: int, hook_id: int) -> list | str:
    r = client.get(f"/api/v4/projects/{pid}/hooks/{hook_id}/events")
    if r.status_code != 200:
        return f"HTTP {r.status_code}（events 端点或非 CE 可用——如实记录，双相证据靠平台侧）"
    return r.json()


def platform_side_receipts(timeout: float = 60.0) -> dict:
    """平台侧实据：tl-app 访问日志里对本 hook 路径的 POST（此刻应为 404——挂账语义）。
    第七课（重建复跑撞出）：GitLab 冷启动时 sidekiq 投递慢，取证必须**轮询**而非定点看。"""
    deadline = time.time() + timeout
    lines: list[str] = []
    while time.time() < deadline:
        out = subprocess.run(
            ["docker", "logs", "--since", "5m", "tl-app"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines = [ln for ln in (out.stderr + out.stdout).splitlines() if HOOK_PATH in ln]
        if lines:
            break
        time.sleep(5)
    return {"hook_log_lines": len(lines), "last": (lines[-1] if lines else None)}


def bootstrap_gitlab() -> int:
    env = load_env()
    base = env.get("GITLAB_BASE_URL", "http://localhost:8929")
    client = gitlab_session(base, env["TL_S09_GITLAB_ROOT_PASSWORD"])
    print("login: root 会话 OK")
    whitelist_platform_host()

    pat = gitlab_pat()
    update_env(GITLAB_TOKEN=pat)
    print("pat: s09-bootstrap 签发并回写 .env GITLAB_TOKEN（值不回显）")
    client.headers["PRIVATE-TOKEN"] = pat

    gid = ensure_group_pid(client)
    secret = env["GITLAB_WEBHOOK_SECRET"]
    summary = {}
    for path, file_path, content in REPOS:
        proj = ensure_project(client, gid, path)
        pid = proj["id"]
        hook = ensure_hook(client, pid, secret)
        # **第五课精修（重建实撞）**：down -v 冷启动后首次投递可能吃到 worker 侧
        # 陈旧设置缓存 → BlockedUrlError，白名单写对也拦；设置本身即时生效，**下一次
        # 投递即绿**（实测首发 BlockedUrlError→重投 404/err=nil）。故提交内容打时间戳
        # 保证每次 bootstrap 都产生真 push 事件——重跑即自愈，不吃陈旧日志假绿。
        sha = ensure_commit(client, pid, file_path, content + f"# stamp {int(time.time())}\n")
        summary[path] = {"project_id": pid, "hook_id": hook["id"], "commit_sha": sha}
        print(f"{path}: project#{pid} hook#{hook['id']} commit {sha[:10]}")

    time.sleep(4)  # push 事件走 sidekiq，留投递余量
    for path, s in summary.items():
        ev = hook_events(client, s["project_id"], s["hook_id"])
        if isinstance(ev, str):
            s["hook_events"] = ev
        else:
            s["hook_events"] = [
                {k: e.get(k) for k in ("event_type", "status", "http_status_code")} for e in ev[:3]
            ]
    platform = platform_side_receipts()
    s09_platform = platform["hook_log_lines"] > 0 and " 404 " in (platform["last"] or " 404 ")
    s09_gitlab = all(
        isinstance(s["hook_events"], list) and s["hook_events"] for s in summary.values()
    )
    ok = s09_platform and s09_gitlab  # 双相都必须：单侧绿=证据面自己会漏（第七课）
    print(
        json.dumps(
            {
                "platform_side": platform,
                "dual_side": {"app_404": s09_platform, "gitlab_events": s09_gitlab},
                "verdict": "PASS" if ok else "FAIL",
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


# ---------------- Jenkins（Step 3） ----------------

# §3.3 文档钉死的唯一路径（不同于 GitLab hook 的暂定路径）：此刻 404 同为证据本体。
CALLBACK_PATH = "/api/v1/callbacks/jenkins"

# 零插件立场：只用 core 的 hudson.tasks.Shell。签名口径 §3.3：消息 = "{ts}.{raw}"，
# raw 就是 --data-binary 实际发出去的字节串，中间零转换（拍板③ openssl 直签）。
JOB_CONFIG_XML = """<?xml version='1.1' encoding='UTF-8'?>
<project>
  <description>S0-9 临时联调栈演示作业：尾步按 §3.3 HMAC 签名回调平台（端点未建，404 即证据本体）。</description>
  <keepLog>false</keepLog>
  <blockBuildWhenDownstreamBuilding>false</blockBuildWhenDownstreamBuilding>
  <blockBuildWhenUpstreamBuilding>false</blockBuildWhenUpstreamBuilding>
  <canRoam>true</canRoam>
  <disabled>false</disabled>
  <scm class="hudson.scm.NullSCM"/>
  <builders>
    <hudson.tasks.Shell>
      <command>#!/bin/bash
set -eu
KEY="${JENKINS_CALLBACK_HMAC_KEY:?容器 env 未注入 HMAC key——compose 插值断链}"
TS=$(date +%s)
PAYLOAD='{"job":"entry-deploy","result":"SUCCESS","build_number":'"${BUILD_NUMBER}"',"commit_sha":"s09-demo"}'
SIG=$(printf '%s' "${TS}.${PAYLOAD}" | openssl dgst -sha256 -hmac "${KEY}" -r | cut -d" " -f1)
echo "callback: X-TL-Timestamp=${TS} X-TL-Signature=sha256=${SIG}"
curl -sS -o /dev/null -w "callback http=%{http_code}\\n" -m 8 \\
  -X POST http://host.docker.internal:8000/api/v1/callbacks/jenkins \\
  -H "Content-Type: application/json" \\
  -H "X-TL-Timestamp: ${TS}" \\
  -H "X-TL-Signature: sha256=${SIG}" \\
  --data-binary "${PAYLOAD}"
echo "done"</command>
    </hudson.tasks.Shell>
  </builders>
  <publishers/>
  <buildWrappers/>
</project>
"""


def _jenkins_form_crumb(html: str) -> str:
    for pat in (
        r'<input[^>]*name="Jenkins-Crumb"[^>]*?value="([^"]+)"',
        r'<input[^>]*?value="([^"]+)"[^>]*?name="Jenkins-Crumb"',
    ):
        if m := re.search(pat, html):
            return m.group(1)
    raise RuntimeError("解锁表单里找不到 Jenkins-Crumb input——表单面假设破裂，如实上报")


def _jenkins_login(c: httpx.Client, user: str, password: str) -> None:
    r = _req(c, "GET", "/login", ok=(200,))
    r = _req(
        c,
        "POST",
        "/j_spring_security_check",
        ok=(200, 302),
        data={
            "from": "/",
            "Jenkins-Crumb": _jenkins_form_crumb(r.text),
            "j_username": user,
            "j_password": password,
        },
    )
    # 成败都可能是 302/200——以认证后的 /api/json 为准绳
    _req(c, "GET", "/api/json", ok=(200,))


def _jenkins_crumb(c: httpx.Client) -> dict[str, str]:
    j = _req(c, "GET", "/crumbIssuer/api/json", ok=(200,)).json()
    return {j["crumbRequestField"]: j["crumb"]}


def _jenkins_exec(cmd: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "tl-jenkins", "sh", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"jenkins exec rc={r.returncode}: {r.stderr[-200:]}")
    return r.stdout


def _jenkins_set_signup(c: httpx.Client, allow: bool) -> None:
    """实据第五课：此镜像无向导阶段可言（/api/createFirstAdmin/* 不存在、realm 已是
    HudsonPrivateSecurityRealm——config.xml 为证），doCreateAccount 被 disableSignup
    把死为 401。唯一 REST 通途 = sed 开关 + POST /reload（会话存活、即刻生效免重启）。
    用完即恢复 true——临时栈也不留敞开的注册面（端口虽只绑环回）。"""
    v = "false" if allow else "true"
    _jenkins_exec(
        f"sed -i 's|<disableSignup>[a-z]*</disableSignup>|<disableSignup>{v}</disableSignup>|'"
        " /var/jenkins_home/config.xml"
    )
    _req(c, "POST", "/reload", ok=(200, 302), headers=_jenkins_crumb(c))
    time.sleep(4)


def bootstrap_jenkins() -> int:
    env = load_env()
    base = env.get("JENKINS_BASE_URL", "http://localhost:8920")
    c = httpx.Client(base_url=base, follow_redirects=False, timeout=30)
    # 第七课（续）：compose healthcheck 是 TCP 口径——healthy 先于 Jetty HTTP 面就绪
    # （/login 503 "Starting Jenkins"）。登录前必须轮询到 HTTP 面起来。
    for _ in range(40):
        if c.get("/login").status_code == 200:
            break
        time.sleep(3)
    else:
        raise RuntimeError("Jenkins HTTP 面 120s 未就绪（503 不退）——如实失败")
    admin_pw = _jenkins_exec("cat /var/jenkins_home/secrets/initialAdminPassword").strip()
    _jenkins_login(c, "admin", admin_pw)
    print("unlock: initialAdminPassword 认证成功（/api/json 200）")

    ops_pw = env.get("TL_S09_JENKINS_PASSWORD", "")
    user_exists = c.get("/user/s09ops/api/json").status_code == 200
    if user_exists and ops_pw in ("", "change-me-s09-only"):
        # 口令不可知的孤儿子号 → 删除重建（临时栈唯一出路；glob 限名不伤及）
        _jenkins_exec("rm -rf /var/jenkins_home/users/s09ops_*")
        subprocess.run(["docker", "restart", "tl-jenkins"], capture_output=True, timeout=180)
        for _ in range(60):
            try:
                c = httpx.Client(base_url=base, follow_redirects=False, timeout=30)
                _jenkins_login(c, "admin", admin_pw)
                break
            except Exception:
                time.sleep(2)
        else:
            raise RuntimeError("tl-jenkins 重启后 120s 未回到可登录状态——如实失败")
        user_exists = False
    if not user_exists:
        ops_pw = secrets.token_urlsafe(24)
        _jenkins_set_signup(c, allow=True)
        for attempt in range(3):
            r = c.post(
                "/securityRealm/createAccount",
                headers=_jenkins_crumb(c),
                data={"username": "s09ops", "password1": ops_pw, "password2": ops_pw},
            )
            if r.status_code == 200:
                break
            if attempt == 2:
                raise RuntimeError(f"createAccount -> {r.status_code}: {r.text[:200]}")
            time.sleep(3)
        _jenkins_set_signup(c, allow=False)
        update_env(JENKINS_USER="s09ops", TL_S09_JENKINS_PASSWORD=ops_pw)
        print("user: s09ops 已建（口令回写 .env 不回显；signup 已恢复关闭）")

    c2 = httpx.Client(base_url=base, follow_redirects=False, timeout=30)
    _jenkins_login(c2, "s09ops", ops_pw)
    print("session: s09ops 正式会话建立")

    # 实据第六课：2.568 的 token UI 在 /user/<id>/security/ 页（configure 路由整体不存在），
    # 签发端点是 descriptorByName 老路径，响应包在 data.tokenValue 里。
    r = _req(
        c2,
        "POST",
        "/user/s09ops/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken",
        ok=(200,),
        headers=_jenkins_crumb(c2),
        data={"newTokenName": "s09-bootstrap"},
    )
    token = r.json()["data"]["tokenValue"]
    update_env(JENKINS_TOKEN=token)
    chk = httpx.get(f"{base}/api/json", auth=("s09ops", token), timeout=15)
    if chk.status_code != 200:
        raise RuntimeError(f"API token Basic 认证复验失败：{chk.status_code}")
    print("token: s09-bootstrap 签发+Basic 复验 200（回写 .env 不回显）")

    h = _jenkins_crumb(c2)
    r = c2.post(
        "/createItem?name=entry-deploy",
        headers={**h, "Content-Type": "application/xml"},
        content=JOB_CONFIG_XML.encode(),
    )
    if r.status_code == 400:
        r = c2.post(
            "/job/entry-deploy/config.xml",
            headers={**h, "Content-Type": "application/xml"},
            content=JOB_CONFIG_XML.encode(),
        )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"ensure job -> {r.status_code}: {r.text[:300]}")
    print("job: entry-deploy 就绪（零插件，core Shell builder）")

    _req(c2, "POST", "/job/entry-deploy/build", ok=(201,), headers=_jenkins_crumb(c2))
    t0, build = time.time(), None
    while time.time() - t0 < 180:
        j = _req(
            c2,
            "GET",
            "/job/entry-deploy/api/json?tree=lastBuild[number,building,result]",
            ok=(200,),
        )
        b = j.json().get("lastBuild") or {}
        if b and not b.get("building") and b.get("result"):
            build = b
            break
        time.sleep(2)
    if not build:
        raise RuntimeError("构建 180s 未落定——如实失败")
    console = _req(c2, "GET", "/job/entry-deploy/lastBuild/consoleText", ok=(200,)).text
    out = subprocess.run(
        ["docker", "logs", "--since", "5m", "tl-app"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    lines = [ln for ln in (out.stderr + out.stdout).splitlines() if CALLBACK_PATH in ln]
    console_ok = build["result"] == "SUCCESS" and "callback http=404" in console
    app_ok = bool(lines) and " 404 " in lines[-1]
    print(
        json.dumps(
            {
                "build": build,
                "console_callback_line": next(
                    (ln for ln in console.splitlines() if "callback" in ln), None
                ),
                "platform_last": lines[-1] if lines else None,
                "dual_side": {"console_404": console_ok, "app_404": app_ok},
                "verdict": "PASS" if console_ok and app_ok else "FAIL",
            },
            ensure_ascii=False,
        )
    )
    return 0 if console_ok and app_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gitlab", help="OQ-1 双仓+PAT+hook+代提交 种子化与出站取证")
    sub.add_parser("jenkins", help="解锁+令牌+entry-deploy 作业+签名回调（Step 3）")
    args = ap.parse_args()
    os.chdir(ROOT)
    if args.cmd == "gitlab":
        return bootstrap_gitlab()
    return bootstrap_jenkins()


if __name__ == "__main__":
    sys.exit(main())
