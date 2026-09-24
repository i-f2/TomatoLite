"""S0-2 spike 编排 + 端到端自动断言（DoD 权威通道）。

用法：
  .venv/bin/python driver.py            # 全自动：起 daphne+prefork worker，跑 stress，断言
  .venv/bin/python driver.py --manual   # 只起服务与 worker，浏览器开 demo.html 人工观察

断言项（结果打印为 JSON + PASS/FAIL）：
  A1 端到端计数：WS 客户端收到的事件总数 == task.published
  A2 顺序/无洞：batch 帧展开后的 seq 集合恰为 0..published-1 且保序
  A3 LTRIM 环：list 长度 == 5000 且最新 seq 在 LPOS 0（SQ3 基线）
  A4 终态收口：flush_timeout == False 且 flushed == published（SQ6 正常路径）
  A5 线程模型：handler 回调线程 ≠ 驱动线程（SQ1 run_async 下为独立线程）
  A6 prefork：task 在 worker 子进程执行（pid ≠ driver pid）
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ENV = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent)}
# S0-2 实据（macOS + py3.12 + celery5.6/billiard）：prefork 子进程 fork+execv 后
# celery 不回填 trace._localized → fast_trace_task 解包 ValueError；
# 该 env 命中 celery/concurrency/prefork.py 的 execv 恢复分支即修复。生产需在 S0-3 复核。
ENV.setdefault("FORKED_BY_MULTIPROCESSING", "1")

import redis
import websockets
from celery import Celery

HERE = Path(__file__).resolve().parent
RUN_ID = "demo1"
WS_URI = f"ws://127.0.0.1:8775/ws/run/{RUN_ID}/"  # 端口避开常用段
PROCS: list[subprocess.Popen] = []


def start_daphne():
    (HERE / "artifacts").mkdir(exist_ok=True)
    log = open(HERE / "artifacts" / "daphne.log", "wb")
    p = subprocess.Popen(
        [str(HERE / ".venv/bin/daphne"), "-p", "8775", "server:app"],
        cwd=HERE, env=ENV, stdout=log, stderr=subprocess.STDOUT)
    PROCS.append(p)
    for _ in range(100):
        time.sleep(0.1)
        try:
            import socket
            with socket.create_connection(("127.0.0.1", 8775), timeout=0.5):
                return p
        except OSError:
            continue
    raise RuntimeError("daphne 未在 10s 内就绪")


def start_worker():
    wlog = open(HERE / "artifacts" / "worker.log", "wb")
    p = subprocess.Popen(
        [str(HERE / ".venv/bin/celery"), "-A", "tasks", "worker",
         "-P", "prefork", "-c", "1", "-n", "w1@spike", "--loglevel=INFO"],
        cwd=HERE, env=ENV, stdout=wlog, stderr=subprocess.STDOUT)
    PROCS.append(p)
    app = Celery(broker="redis://127.0.0.1:6379/0")
    for _ in range(150):
        time.sleep(0.2)
        try:
            if app.control.ping(timeout=1.0):
                return p
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError("celery worker 未在 30s 内就绪")


async def collect(frames: list, stop: asyncio.Event):
    async with websockets.connect(WS_URI) as ws:
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            frames.append(json.loads(raw))


async def main():
    manual = "--manual" in sys.argv
    start_daphne()
    start_worker()
    if manual:
        print("daphne:8775 + prefork worker 已起。打开 demo.html 观察，Ctrl-C 退出。")
        try:
            asyncio.Event().wait()
        except KeyboardInterrupt:
            pass
        return 0

    redis_cli = redis.Redis(host="127.0.0.1", port=6379)
    redis_cli.delete(f"tl:run:{RUN_ID}:events")

    app = Celery(broker="redis://127.0.0.1:6379/0")
    frames: list = []
    stop = asyncio.Event()
    collector = asyncio.create_task(collect(frames, stop))
    await asyncio.sleep(0.5)  # 确保 group_add 先于第一批 group_send

    loop = asyncio.get_running_loop()
    redis_cli.delete(f"tl:run:{RUN_ID}:done")
    t0 = time.time()
    await loop.run_in_executor(None, lambda: app.send_task(
        "spike.run_demo", args=[RUN_ID, "stress.yml", 0.0], protocol=2))
    meta = None
    while time.time() - t0 < 240:
        raw = redis_cli.get(f"tl:run:{RUN_ID}:done")
        if raw:
            meta = json.loads(raw)
            break
        await asyncio.sleep(0.2)
    assert meta, "task 240s 内未完成（查 artifacts/worker.log）"
    await asyncio.sleep(2.0)  # 末批帧飞行余量
    stop.set()
    await collector

    # ---- 断言
    seqs_in_frames = []
    for fr in frames:
        if fr.get("type") == "run.batch":
            seqs_in_frames.extend(range(fr["seq_from"], fr["seq_to"] + 1))
    published = meta["published"]
    llen = redis_cli.llen(f"tl:run:{RUN_ID}:events")
    head = json.loads(redis_cli.lindex(f"tl:run:{RUN_ID}:events", 0))
    checks = {
        "A1_count": len(seqs_in_frames) == published and meta["flushed"] == published,
        "A2_order_nohole": seqs_in_frames == list(range(published)),
        "A3_ltrim_ring": llen == min(5000, published) and head["seq"] == published - 1,
        "A4_terminal_flush": meta["flush_timeout"] is False and meta["batches"] >= 1,
        # run_async 下 handler 应在 runner 事件读取线程（非 worker 主线程）
        "A5_handler_thread": len(meta["handler_threads"]) >= 1
                             and "MainThread" not in meta["handler_threads"],
        "A6_prefork_pid": meta["pid"] != __import__("os").getpid(),
        "A7_no_gse_errors": not meta["group_send_errors"],
    }
    result = {
        "seconds": round(time.time() - t0, 2),
        "meta": meta, "frames": len(frames),
        "ws_events": len(seqs_in_frames), "redis_llen": llen,
        "checks": checks,
    }
    (HERE / "artifacts").mkdir(exist_ok=True)
    (HERE / "artifacts" / "driver_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    ok = all(checks.values())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"S0-2 END-TO-END {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    finally:
        for p in PROCS:
            p.terminate()
