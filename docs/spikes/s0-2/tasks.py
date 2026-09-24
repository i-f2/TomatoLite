"""S0-2 spike：Celery 任务（以 prefork worker 跑 ansible-runner + 泵 + Persister）。

broker = 同一个临时 redis db0；事件走 db1（list/pubsub）、db2（channel layer prefix），
互不串门，便于断言读取。
"""
import json
import os
import threading
import time
from pathlib import Path

import ansible_runner
from celery import Celery

HERE = Path(__file__).resolve().parent
os.environ["PATH"] = str(HERE / ".venv/bin") + os.pathsep + os.environ.get("PATH", "")

app = Celery("spike", broker="redis://127.0.0.1:6379/0")
app.conf.update(result_backend=None, task_track_started=True, worker_prefetch_multiplier=1,
                task_protocol=2)  # spike 实据：send_task 默认协议与 prefork fast_trace 不匹配会炸


@app.task(name="spike.run_demo")
def run_demo(run_id: str, playbook: str = "stress.yml", trigger_cancel_after: float = 0.0):
    from pump import EventPump, RunEventPersister, wait_terminal_and_close

    art = HERE / "artifacts" / run_id
    art.mkdir(parents=True, exist_ok=True)
    pump = EventPump(run_id)
    persister = RunEventPersister(run_id, pump.bus_key, art / "persisted.jsonl")
    persister.start()

    cancel_flag = threading.Event()  # SQ5：取消判定必须是显式标志位，不是 callback 被调用
    pump.r.delete(f"tl:run:{run_id}:cancel")

    def cancel_callback() -> bool:
        pump.cancel_calls += 1
        return pump.r.exists(f"tl:run:{run_id}:cancel") == 1 or cancel_flag.is_set()

    # S0-2 实据：run_async 返回 (thread, Runner) 二元组——不是单个 AsyncRunner 对象
    runner_thread, runner = ansible_runner.run_async(
        private_data_dir=str(HERE),
        playbook=playbook,
        artifact_dir=str(art / "runner"),
        event_handler=pump.handler,
        cancel_callback=cancel_callback,
        quiet=True,
    )
    if trigger_cancel_after:
        def do_cancel():
            time.sleep(trigger_cancel_after)
            pump.r.set(f"tl:run:{run_id}:cancel", "1", ex=120)  # 显式置位（跨进程可观测）
        threading.Thread(target=do_cancel, daemon=True).start()

    flush_timeout = wait_terminal_and_close(runner_thread, pump, persister, join_timeout=5)

    meta = {
        "pid": os.getpid(),
        "runner_status": runner.status,
        "rc": runner.rc,
        "published": pump.published,
        "flushed": persister.flushed,
        "batches": persister.batches,
        "flush_timeout": flush_timeout,
        "handler_threads": sorted(pump.threads_seen),
        "persister_threads": sorted(persister.threads_seen),
        "cancel_callback_calls": pump.cancel_calls,
        "group_send_errors": persister.group_send_errors[:3],
    }
    (art / "task_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    # spike 不配 result backend：以 done-key 回传（SQ 断言用）
    pump.r.set(f"tl:run:{run_id}:done", json.dumps(meta), ex=600)
    return meta
