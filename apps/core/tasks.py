"""core 域任务（S0-4：从 config/celery.py 迁入，兑现 S0-3 提交承诺"探针 S0-4 删除"）。

- `core.probe`：S0-3 Linux prefork 断言探针（移交清单闭环件），保留供 X-01 冒烟复用。
- `core.slow_probe`：S0-3a 拓扑断言高水位计数（db9，与业务库 db0 分离）。
任务名不变（`config.probe`→`core.probe` 为唯一改名；S0-3 报告中的旧名以本文注为桥）。
"""

import time

from celery import shared_task
from django.conf import settings


@shared_task(name="core.probe")
def probe(payload: str = "") -> str:
    """S0-3 临时探针：闭环移交断言（Linux prefork 是否需 FORKED_BY_MULTIPROCESSING——结论：不需要）。"""
    import os
    import platform

    return f"ok pid={os.getpid()} plat={platform.system()} host={platform.node()} {payload}"


def _counter_redis():
    import redis as _r

    return _r.Redis.from_url(
        settings.CELERY_BROKER_URL.rsplit("/", 1)[0] + "/9", decode_responses=True
    )


_HWM_LUA = """
local a = redis.call('INCR', KEYS[1])
local m = tonumber(redis.call('GET', KEYS[2]) or '0')
if a > m then redis.call('SET', KEYS[2], a) end
return a
"""


@shared_task(name="core.slow_probe")
def slow_probe(seconds: float = 3.0, key: str = "tl:topo:exec") -> None:
    """高水位并发计数：INCR active → 刷 max → sleep → DECR/INCR done。"""
    r = _counter_redis()
    r.register_script(_HWM_LUA)(keys=[f"{key}:active", f"{key}:max"])
    try:
        time.sleep(seconds)
    finally:
        r.decr(f"{key}:active")
        r.incr(f"{key}:done")
