"""S0-2 Step 3：故障与边界断言（SQ2/SQ3/SQ5/SQ6）。

E1 为"行为记录项"：raise 或 drop 都是有效实据，只要结论确定即 PASS。
E2/E3/E4 为真断言：失败 = spike 代码问题，须修复重跑。
结果落 artifacts/edge_result.json。
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ["PATH"] = str(HERE / ".venv/bin") + os.pathsep + os.environ.get("PATH", "")  # ansible-playbook 可解析
sys.path.insert(0, str(HERE))

import redis
from channels_redis.core import RedisChannelLayer

R = redis.Redis(host="127.0.0.1", port=6379)
results = {}


# ---------------- E1 (SQ2): 超过 max_message_size 的 group_send ----------------
async def e1_big_message():
    layer = RedisChannelLayer(hosts=[("127.0.0.1", 6379)], prefix="tl", expiry=60)
    big = {"type": "run.batch", "blob": "x" * 200_000}  # ~200KB，远超默认上限
    try:  # 无订阅者时
        await layer.group_send("run_e1", big)  # group 名会被加 prefix "tl."
        no_sub = "no error"
    except Exception as e:  # noqa: BLE001
        no_sub = f"{type(e).__name__}: {str(e)[:160]}"
    recv = RedisChannelLayer(hosts=[("127.0.0.1", 6379)], prefix="tl", expiry=60)
    ch = await recv.new_channel()
    await recv.group_add("run_e1", ch)
    try:  # 有订阅者时
        await layer.group_send("run_e1", big)
        try:
            got = await asyncio.wait_for(recv.receive(ch), timeout=2)
            with_sub = f"send ok | received size={len(got.get('blob', ''))}"
        except asyncio.TimeoutError:
            with_sub = "send ok | 订阅者 2s 内未收到（静默丢弃侧证）"
    except Exception as e:  # noqa: BLE001
        with_sub = f"{type(e).__name__}: {str(e)[:160]}"
    return True, {"max_message_size": getattr(layer, "max_message_size", "n/a"),
                  "no_subscriber": no_sub, "with_subscriber": with_sub}


# ---------------- E2 (SQ3): LTRIM 追尾后补发语义 ----------------
async def e2_ltrim_resync():
    key = "tl:run:e2:events"
    R.delete(key)
    pipe = R.pipeline(transaction=False)
    for seq in range(6000):  # 一次性灌入，验证环形窗口只留最后 5000
        pipe.lpush(key, json.dumps({"seq": seq}))
        pipe.ltrim(key, 0, 4999)
    pipe.execute()
    llen = R.llen(key)
    newest = json.loads(R.lindex(key, 0))["seq"]
    oldest = json.loads(R.lindex(key, -1))["seq"]
    since = 5  # 客户端声称已看到 seq<5，请求补发
    hole_detected = oldest > since  # True ⇒ 服务端可识别缺口，必须显式 resync
    ok = llen == 5000 and newest == 5999 and oldest == 1000 and hole_detected
    return ok, {"llen": llen, "newest": newest, "oldest": oldest,
                "since5_hole_detected": hole_detected}


# ---------------- E3 (SQ5): 跑到一半显式置位取消 ----------------
async def e3_cancel():
    import threading

    import ansible_runner

    from pump import EventPump, RunEventPersister

    run_id = "e3"
    R.delete(f"tl:run:{run_id}:events", f"tl:run:{run_id}:cancel")
    pump = EventPump(run_id)
    art = HERE / "artifacts" / run_id
    art.mkdir(parents=True, exist_ok=True)
    persister = RunEventPersister(run_id, pump.bus_key, art / "persisted.jsonl")
    persister.start()
    time.sleep(0.3)  # 待 SUBSCRIBE 生效，避免首事件丢失污染计数

    flag = threading.Event()

    def cancel_callback() -> bool:
        pump.cancel_calls += 1
        return flag.is_set()

    thread, runner = ansible_runner.run_async(
        private_data_dir=str(HERE), playbook="playbook.yml",
        artifact_dir=str(art / "runner"),
        event_handler=pump.handler, cancel_callback=cancel_callback, quiet=True)
    t0 = time.time()
    await asyncio.sleep(3.0)
    flag.set()  # 模拟"点取消"：显式置位判定，而非依赖 callback 是否被调用
    thread.join(timeout=90)
    cancel_landed_s = round(time.time() - t0, 2)
    persister.stop(pump.published)
    persister.join(timeout=10)
    # 落盘文件逐行可解析 = 事件流无半行
    lines = (art / "persisted.jsonl").read_text("utf-8").splitlines()
    no_half_line = all(_json_ok(x) for x in lines) and len(lines) == pump.published
    ok = (runner.status == "canceled" and pump.published < 41 and no_half_line
          and persister.flushed == pump.published)
    return ok, {"status": runner.status, "published": pump.published,
                "flushed": persister.flushed, "cancel_effect_after_s": cancel_landed_s,
                "persisted_lines": len(lines), "no_half_line": no_half_line,
                "cancel_callback_calls": pump.cancel_calls}


def _json_ok(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except ValueError:
        return False


# ---------------- E4 (SQ6): 人为拖慢 flush，join(5) 必须被观测为超时 ----------------
async def e4_flush_timeout():
    from pump import EventPump, RunEventPersister, wait_terminal_and_close

    run_id = "e4"
    R.delete(f"tl:run:{run_id}:events")
    pump = EventPump(run_id)
    art = HERE / "artifacts" / run_id
    art.mkdir(parents=True, exist_ok=True)
    p = RunEventPersister(run_id, pump.bus_key, art / "persisted.jsonl")

    real_flush = p._flush

    def slow_flush(items, f):  # 模拟 PG 卡死：单批 10s > join(5)
        time.sleep(10)
        real_flush(items, f)

    p._flush = slow_flush  # type: ignore[method-assign]
    p.start()
    time.sleep(0.3)  # 待 SUBSCRIBE 生效
    for i in range(10):
        pump.handler({"event": "runner_on_ok", "event_data": {"host": "h", "task": f"t{i}"}})

    class _Finished:
        def is_alive(self):
            return False

    t0 = time.time()
    ft = wait_terminal_and_close(_Finished(), pump, p, join_timeout=5)
    elapsed = round(time.time() - t0, 2)
    ok = ft is True and elapsed < 7
    p.join(timeout=25)  # _closing 已置位，慢批刷完后线程自行收口（勿 clear，否则永挂）
    return ok, {"flush_timeout_detected": ft, "join_gave_up_after_s": elapsed,
                "thread_alive_at_join": True, "flushed_after_settle": p.flushed,
                "published": pump.published}


async def main():
    for name, fn in [("E1 SQ2 大消息", e1_big_message), ("E2 SQ3 LTRIM 追尾", e2_ltrim_resync),
                     ("E3 SQ5 显式取消", e3_cancel), ("E4 SQ6 flush 超时", e4_flush_timeout)]:
        try:
            ok, detail = await fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, {"exception": f"{type(e).__name__}: {e}"}
        results[name] = {"ok": ok, "detail": detail}
        print(f"{name}: {'PASS' if ok else 'FAIL'} {json.dumps(detail, ensure_ascii=False)}")
    (HERE / "artifacts").mkdir(exist_ok=True)
    (HERE / "artifacts" / "edge_result.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), "utf-8")
    allok = all(v["ok"] for v in results.values())
    print(f"EDGE {'PASS' if allok else 'FAIL'}")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
