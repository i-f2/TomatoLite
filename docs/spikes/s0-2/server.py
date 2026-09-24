"""S0-2 spike：daphne 上的裸 ASGI3 WS 服务（不建 Django 工程）。

ws://127.0.0.1:8765/ws/run/<run_id>/  →  join group `tl.run.<run_id>` → 转发帧。
生产里这一步是 RunLogConsumer + connect() 权限门禁；spike 只验桥，不做门禁
（门禁属 M1-16/M2 安全断言，见 §2.3 要点）。
"""
import asyncio
import json

from channels_redis.core import RedisChannelLayer

layer: RedisChannelLayer | None = None


async def app(scope, receive, send):
    global layer
    t = scope["type"]
    if t == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                layer = RedisChannelLayer(hosts=[("127.0.0.1", 6379)], prefix="tl", expiry=60)
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if t == "http":
        body = b"tl-s02-spike\n"
        await send({"type": "http.response.start", "status": 200,
                    "headers": [[b"content-type", b"text/plain"]]})
        await send({"type": "http.response.body", "body": body})
        return
    if t != "websocket":
        return

    if layer is None:  # daphne CLI 未跑 lifespan 时惰性建层
        layer = RedisChannelLayer(hosts=[("127.0.0.1", 6379)], prefix="tl", expiry=60)

    run_id = scope["path"].rstrip("/").split("/")[-1]
    group = f"tl.run.{run_id}"
    channel = await layer.new_channel()  # channels 4.3：协程（spike 实据，旧文档多写作同步）
    await layer.group_add(group, channel)
    await send({"type": "websocket.accept"})

    stop = asyncio.Event()

    async def forward():
        while not stop.is_set():
            msg = await layer.receive(channel)
            if msg is None:
                continue
            await send({"type": "websocket.send", "text": json.dumps(msg)})

    fwd = asyncio.create_task(forward())
    try:
        while True:
            m = await receive()
            if m["type"] == "websocket.disconnect":
                break
    finally:
        stop.set()
        fwd.cancel()
        try:
            await fwd
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await layer.group_discard(group, channel)
