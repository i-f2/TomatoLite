import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("tomatolite")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks([])  # apps/* 尚不存在（S0-4 起逐 app 注册）


@app.task(name="config.probe")
def probe(payload: str = "") -> str:
    """S0-3 临时探针：闭环 S0-2 移交断言（Linux prefork 是否需 FORKED_BY_MULTIPROCESSING）。

    S0-4 建 apps/core 后删除本任务与它的引用。
    """
    import os
    import platform

    return f"ok pid={os.getpid()} plat={platform.system()} host={platform.node()} {payload}"
