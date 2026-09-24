"""S0-2 spike：事件泵最小实现（§2.3 的 EventPump + RunEventPersister）。

刻意做成 spike 形状，不依赖 Django 工程骨架：
  - EventPump.handler()：在 runner 的事件读取线程被回调（run_async 下）→ 归一化 →
    LPUSH(+LTRIM 0 5000，供重连补发) + PUBLISH（实时扇出给 Persister）。
    handler 内**只有 Redis 写，无任何 DB 操作**（§2.3 硬约束）。
  - RunEventPersister：独立**非 daemon** 线程，独立 Redis 连接，SUBSCRIBE bus →
    批量 flush（500ms / 200 条 / 终态收口）→ 写 JSONL 假落库 + channel_layer.group_send。
  - SQ6 语义：task 结束时以 stop(expected) 收口，Persister 未能在 join(5) 内清空
    → flush_timeout=True，调用方按 ORPHANED 处理。
"""
import json
import threading
import time
from pathlib import Path

import redis

LTRIM_MAX = 5000


class EventPump:
    def __init__(self, run_id: str, url="redis://127.0.0.1:6379/0", ltrim=True):
        self.run_id = run_id
        self.list_key = f"tl:run:{run_id}:events"
        self.bus_key = f"tl:run:{run_id}:bus"
        self.r = redis.Redis.from_url(url)
        self.ltrim = ltrim
        self.threads_seen = set()   # SQ1/SQ7 探针：handler 回调线程
        self.published = 0
        self.seq = 0
        self.cancel_calls = 0       # SQ5 探针

    def handler(self, data: dict) -> bool:
        self.threads_seen.add(threading.current_thread().name)
        ed = data.get("event_data") or {}
        res = ed.get("res") if isinstance(ed.get("res"), dict) else {}
        norm = {
            "seq": self.seq,
            "kind": data.get("event"),
            "host": ed.get("host"),
            "task": ed.get("task"),
            "changed": res.get("changed"),
            "duration_ms": int(ed["duration"] * 1000) if ed.get("duration") is not None else None,
            "stdout_len": len(data.get("stdout") or ""),
        }
        self.seq += 1
        pipe = self.r.pipeline()
        pipe.lpush(self.list_key, json.dumps(norm))
        if self.ltrim:
            pipe.ltrim(self.list_key, 0, LTRIM_MAX - 1)
        pipe.publish(self.bus_key, json.dumps(norm))
        pipe.execute()
        self.published += 1
        return True


class RunEventPersister(threading.Thread):
    def __init__(self, run_id: str, bus_key: str, out_path: Path,
                 url="redis://127.0.0.1:6379/0", batch=200, flush_s=0.5):
        super().__init__(name="RunEventPersister", daemon=False)  # §2.3：不得 daemon
        self.group = f"tl.run.{run_id}"
        self.bus_key = bus_key
        self.r = redis.Redis.from_url(url)
        self.out = out_path
        self.batch = batch
        self.flush_s = flush_s
        self.flushed = 0
        self.batches = 0
        self.threads_seen = set()      # SQ1 探针：flush 与 group_send 发生线程
        self._expected = None
        self._closing = threading.Event()  # 注意：_stop 是 Thread 私有方法名，绝不能占用
        self._layer = None
        self.group_send_errors = []

    # ---- channel layer 桥：本线程之外单开一个 loop（spike 简化，生产在 worker 装配层解决）
    def setup_layer(self):
        import asyncio

        loop = asyncio.new_event_loop()

        def runner():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self._loop_thread = threading.Thread(target=runner, name="ChannelLayerLoop", daemon=True)
        self._loop_thread.start()
        self._loop = loop
        from channels_redis.core import RedisChannelLayer

        async def _make():
            return RedisChannelLayer(hosts=[("127.0.0.1", 6379)], prefix="tl", expiry=60)

        self._layer = asyncio.run_coroutine_threadsafe(_make(), loop).result(timeout=10)

    def _group_send_batch(self, items):
        import asyncio

        msg = {"type": "run.batch", "count": len(items),
               "seq_from": items[0]["seq"], "seq_to": items[-1]["seq"]}
        try:
            asyncio.run_coroutine_threadsafe(
                self._layer.group_send(self.group, msg), self._loop).result(timeout=5)
        except Exception as e:  # noqa: BLE001
            self.group_send_errors.append(repr(e))

    def stop(self, expected_flushed: int):
        """终态收口：Persister 须刷完 expected_flushed 条才退出（SQ6）。"""
        self._expected = expected_flushed
        self._closing.set()

    def run(self):
        self.setup_layer()
        self.threads_seen.add(threading.current_thread().name)
        ps = self.r.pubsub()
        ps.subscribe(self.bus_key)
        buf, last = [], time.time()
        out_empty_rounds = 0
        with self.out.open("w", encoding="utf-8") as f:
            while True:
                msg = ps.get_message(timeout=0.05, ignore_subscribe_messages=True)
                if msg:
                    buf.append(json.loads(msg["data"]))
                    out_empty_rounds = 0
                now = time.time()
                if len(buf) >= self.batch or (buf and now - last >= self.flush_s):
                    self._flush(buf, f)
                    buf = []
                    last = now
                if self._closing.is_set():
                    if self.flushed >= (self._expected or 0):
                        break
                    out_empty_rounds += 1
                    if out_empty_rounds > 200:  # pub/sub 无积压兜底（≈1s）
                        break
                time.sleep(0.005) if not msg else None
            if buf:
                self._flush(buf, f)
        ps.close()

    def _flush(self, items, f):
        self.threads_seen.add(threading.current_thread().name)
        for it in items:
            f.write(json.dumps(it) + "\n")
        f.flush()
        self.flushed += len(items)
        self.batches += 1
        self._group_send_batch(items)


def wait_terminal_and_close(runner, pump: EventPump, persister: RunEventPersister,
                            join_timeout=5):
    """runner 结束后 stop(expected) + join(5)；返回 flush_timeout 标志（SQ6/ORPHANED 语义）。"""
    while runner.is_alive():
        time.sleep(0.05)
    expected = pump.published
    persister.stop(expected)
    persister.join(timeout=join_timeout)
    return persister.is_alive() or persister.flushed < expected
