"""S0-3a 队列/worker 拓扑断言（故障注入级，§0.5/§0.7）。

立场：断言必须证明"能红"——正例（prefetch=1：同时执行=3 且队列未被预取独占）之外，
注入段临时起一个 prefetch 默认 4 的同队列 worker，复现真实失效模式（F-7）：
**预取占用放大**——12 条消息瞬间被单 worker 揽走（LLEN=0、reserved=9），
而同时执行仍被 -c 硬顶在 3。占用放大≠并发放大，随后自清恢复。两段都过才算过。

用法（compose 栈需在跑）：
  .venv/bin/python scripts/s0_3a_topology_check.py            # 全部阶段
  .venv/bin/python scripts/s0_3a_topology_check.py --only positive
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
sys.path.insert(0, str(ROOT))

import django  # noqa: E402

django.setup()
import redis  # noqa: E402
from celery import Celery  # noqa: E402
from django.conf import settings  # noqa: E402

DB9 = settings.CELERY_BROKER_URL.rsplit("/", 1)[0] + "/9"
KEY = "tl:topo:exec"
results: dict[str, dict] = {}


def reset():
    r = redis.Redis.from_url(DB9, decode_responses=True)
    r.delete(f"{KEY}:active", f"{KEY}:max", f"{KEY}:done")
    return r


def drain(r, n_tasks, timeout=120.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if int(r.get(f"{KEY}:done") or 0) >= n_tasks:
            time.sleep(0.3)  # 让最后一笔 DECR 落定
            return True
        time.sleep(0.2)
    return False


def _broker():
    return redis.Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


def _sample(app, node):
    """t≈1.5s 采样：队列积压 LLEN + 该节点的预占/在跑数（占用口径三指标，F-7）。"""
    ins = app.control.inspect(destination=[node])
    return {
        "llen": _broker().llen("exec.runner"),
        "reserved": sum(len(v) for v in (ins.reserved() or {}).values()),
        "active": sum(len(v) for v in (ins.active() or {}).values()),
    }


def positive(r, n_tasks=12, seconds=3.0):
    """正例（对照组）：exec.runner（-c 3 prefetch=1）12 发 →
    高水位==3 ∧ LLEN>0（队列未被独占）∧ reserved≤3（占用有度）。"""
    app = Celery(broker=settings.CELERY_BROKER_URL)
    if not app.control.ping(destination=["runner@tl"], timeout=3.0):
        raise RuntimeError("runner@tl 未就绪")
    for _ in range(n_tasks):
        app.send_task("core.slow_probe", args=[seconds, KEY], queue="exec.runner")
    time.sleep(1.5)
    sample = _sample(app, "runner@tl")
    ok_drain = drain(r, n_tasks)
    mx = int(r.get(f"{KEY}:max") or 0)
    return {"ok": ok_drain and mx == 3 and sample["llen"] > 0 and sample["reserved"] <= 3,
            "tasks": n_tasks, "max_active": mx, "expected_max": 3, "sample": sample}


DC = ["docker", "compose", "--project-directory", str(ROOT)]


def _dc(*args, timeout=180):
    out = subprocess.run(DC + list(args), capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"compose {' '.join(args)} rc={out.returncode}: {out.stderr[:300]}")
    return out.stdout


def _wait_ping(app, node, timeout=60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if app.control.ping(destination=[node], timeout=2.0):
            return True
        time.sleep(1.0)
    return False


def inject(r, n_tasks=12, seconds=3.0):
    """注入段（F-7 口径）：停掉唯一 runner，临时起 prefetch=4 的同队列 worker——
    断言占用放大三指标：LLEN==0 ∧ reserved==9 ∧ max_active==3。
    （原 Spec 的 max_active>3 物理上不可能：同时执行被 -c 硬顶，prefetch 放大的是
    未 ack 预取**占用**而非并发——修订 Spec 实证，见 design-notes §7 第七轮。）
    红即红，如实记录；结束必恢复基线并复跑全量正例。"""
    app = Celery(broker=settings.CELERY_BROKER_URL)
    _dc("stop", "worker_runner")
    cid = ""
    try:
        reset()
        cid = _dc("run", "-d", "--no-deps", "--rm", "worker_runner",
                  "celery", "-A", "config", "worker", "-Q", "exec.runner",
                  "-n", "inj@tl", "--concurrency=3", "--prefetch-multiplier=4",
                  "--loglevel=INFO").strip().splitlines()[-1]
        if not _wait_ping(app, "inj@tl"):
            raise RuntimeError("inj@tl 启动超时")
        for _ in range(n_tasks):
            app.send_task("core.slow_probe", args=[seconds, KEY], queue="exec.runner")
        time.sleep(1.5)
        sample = _sample(app, "inj@tl")
        ok_drain = drain(r, n_tasks)
        mx = int(r.get(f"{KEY}:max") or 0)
        result = {"ok": (ok_drain and sample["llen"] == 0
                         and sample["reserved"] == n_tasks - 3 and mx == 3),
                  "tasks": n_tasks, "max_active": mx,
                  "assertion": "LLEN==0 ∧ reserved==9 ∧ max_active==3",
                  "sample": sample, "concurrency": 3, "prefetch_multiplier": 4}
    finally:
        if cid:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=60)
        _dc("start", "worker_runner")
        if not _wait_ping(app, "runner@tl"):
            raise RuntimeError("worker_runner 恢复超时——手工检查 tl-worker-runner")
    reset()  # 清掉注入段计数，基线复跑才是干净的
    result["baseline_after"] = positive(r)  # 全量正例（含占用对照），基线必须回绿
    result["ok"] = result["ok"] and result["baseline_after"]["ok"]
    return result


def active_queues():
    """逐池核验（一次广播、按节点判定）：celery inspect 是广播命令——任一节点执行
    都收齐全部在线 worker 的应答，故单次采集后按 **节点名** 逐一断言其消费队列集合
    等于 §0.5 表中它的队列，逐字相等、无串队，且恰好 4 节点、无多余节点。"""
    expect = {
        "callback@tl": {"callback.process"},
        "runner@tl": {"exec.runner"},
        "verify@tl": {"verify.runner"},
        "default@tl": {"sweeper", "notify", "default"},
    }
    out = subprocess.run(
        [*DC, "exec", "-T", "worker_callback",
         "celery", "-A", "config", "inspect", "active_queues", "-j"],
        capture_output=True, text=True, timeout=60)
    try:
        payload = json.loads(out.stdout[out.stdout.find("{"):])
    except ValueError:
        return {"ok": False,
                "error": f"rc={out.returncode} stdout={out.stdout[:200]!r} stderr={out.stderr[:200]!r}"}
    detail = {}
    for node, want in expect.items():
        got = {q["name"] for q in payload.get(node, [])}
        detail[node] = {"ok": got == want, "got": sorted(got), "want": sorted(want)}
    extra = sorted(set(payload) - set(expect))
    if extra:
        detail["_unexpected_nodes"] = {"ok": False, "got": extra}
    return {"ok": all(v["ok"] for v in detail.values()),
            "nodes_online": len(payload), "pools": detail}


def beat_singleton():
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}|{{.Command}}"],
        capture_output=True, text=True, timeout=15)
    n = sum(1 for l in out.stdout.splitlines() if "beat" in l and l.startswith("tl-"))
    return {"ok": n == 1, "beat_containers": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["positive", "inject", "queues", "beat"])
    args = ap.parse_args()

    r = reset()
    stages = [args.only] if args.only else ["positive", "inject", "queues", "beat"]
    for st in stages:
        if st == "positive":
            results[st] = positive(r)
            print(f"positive: {json.dumps(results[st])}")
        elif st == "inject":
            results[st] = inject(r)
            print(f"inject: {json.dumps(results[st])}")
        elif st == "queues":
            results[st] = active_queues()
            print(f"queues: {json.dumps(results[st], ensure_ascii=False)}")
        elif st == "beat":
            results[st] = beat_singleton()
            print(f"beat: {json.dumps(results[st])}")
    ok = all(v["ok"] for v in results.values())
    print(f"S0-3a {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
