#!/usr/bin/env python3
"""S0-1 spike：通过 ansible-runner Python API 跑 playbook，全量捕获 event_handler 事件流。

产物（均在 docs/spikes/s0-1/ 下，artifacts/ 不入库）：
  events_stream.jsonl        正常 run 的每一条事件：{wall_ts, monotonic_ns, thread_name, event, payload}
  events_stream_stress.jsonl --stress 模式的事件流
  artifacts/<ident>/...      runner 自身的 artifact_dir（job_events/*.json 等，验证懒加载假设）
  run_meta.json              每次运行的 status/rc/版本/线程/速率摘要（报告引用其证据路径）

用法：
  .venv/bin/python capture.py            # 六结局 playbook
  .venv/bin/python capture.py --stress   # ~5000 事件压测（Q7）
"""
import json
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
# ansible-runner 以子进程方式调 ansible-playbook，必须保证 venv/bin 在 PATH 最前
os.environ["PATH"] = str(HERE / ".venv/bin") + os.pathsep + os.environ.get("PATH", "")

import ansible_runner  # noqa: E402
from importlib.metadata import version as pkg_version  # noqa: E402

ARTIFACTS = HERE / "artifacts"


def make_handler(stream_path: Path):
    """返回 event_handler；每条事件立刻以 JSONL 落盘（handler 内不做任何 DB/同步 INSERT）。"""
    main_thread = threading.main_thread()
    t0 = time.monotonic_ns()
    counter = Counter()
    threads = Counter()

    def handler(data: dict) -> bool:
        cur = threading.current_thread()
        rec = {
            "wall_ts": round(time.time(), 6),
            "monotonic_ns": time.monotonic_ns() - t0,
            "thread_name": cur.name,
            "is_main_thread": cur is main_thread,
            "event": data.get("event"),
            "payload": data,
        }
        counter[rec["event"]] += 1
        threads[rec["thread_name"]] += 1
        with stream_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return True

    handler.counter = counter
    handler.threads = threads
    return handler


def run_one(playbook: str, ident: str, stream_path: Path) -> dict:
    stream_path.write_text("", encoding="utf-8")
    artifact_dir = ARTIFACTS / ident
    handler = make_handler(stream_path)
    cancel_calls = []

    def cancel_callback() -> bool:
        cancel_calls.append(time.monotonic_ns())
        return False  # spike 中永不取消，仅证明该参数按约定被调用/可注入

    wall_start = time.time()
    r = ansible_runner.run(
        private_data_dir=str(HERE),
        playbook=playbook,
        artifact_dir=str(artifact_dir),
        event_handler=handler,
        cancel_callback=cancel_callback,
        quiet=True,
    )
    wall_end = time.time()

    total_events = sum(handler.counter.values())
    meta = {
        "ident": ident,
        "playbook": playbook,
        "runner_status": r.status,
        "rc": r.rc,
        "cancel_callback_invocations": len(cancel_calls),
        "elapsed_s": round(wall_end - wall_start, 3),
        "total_events": total_events,
        "events_per_s": round(total_events / max(wall_end - wall_start, 1e-9), 1),
        "event_kind_counts": dict(handler.counter),
        "handler_thread_names": dict(handler.threads),
        "artifact_dir": str(artifact_dir),
    }
    return meta


def main() -> int:
    stress = "--stress" in sys.argv
    if stress:
        playbook, ident, stream = "stress.yml", "stress", HERE / "events_stream_stress.jsonl"
    else:
        playbook, ident, stream = "playbook.yml", "main", HERE / "events_stream.jsonl"

    meta = run_one(playbook, ident, stream)
    meta["versions"] = {
        "python": sys.version.split()[0],
        "ansible-runner": pkg_version("ansible-runner"),
        "ansible-core": None,  # 由下方 ansible --version 补充，避免子进程失败时缺字段
    }
    try:
        import subprocess

        out = subprocess.run(
            [str(HERE / ".venv/bin/ansible-playbook"), "--version"],
            capture_output=True, text=True, timeout=30,
        ).stdout.splitlines()[0]
        meta["versions"]["ansible-core"] = out
    except Exception as e:  # noqa: BLE001
        meta["versions"]["ansible-core"] = f"ERROR: {e}"

    # 合并写入（main 与 stress 共用一个 run_meta.json）
    meta_path = HERE / "run_meta.json"
    all_meta = {}
    if meta_path.exists():
        all_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    all_meta[ident] = meta
    meta_path.write_text(json.dumps(all_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({k: v for k, v in meta.items() if k != "payload_hints"}, ensure_ascii=False, indent=2))
    # 自检的是捕获管线，不是 playbook 结局：unreachable 是故意制造的，rc 非 0 属预期。
    # ansible-playbook 即使有 unreachable 也会输出 stats；若整场只有 verbose 则说明启动/语法即失败。
    kinds = meta["event_kind_counts"]
    ok = kinds.get("playbook_on_stats", 0) >= 1 and meta["total_events"] > 1
    print(f"CAPTURE {'PASS' if ok else 'FAIL'}: {meta['total_events']} events, kinds={len(kinds)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
