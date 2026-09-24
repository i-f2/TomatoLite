import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("tomatolite")
app.config_from_object("django.conf:settings", namespace="CELERY")
# S0-4：任务全部住 apps/<app>/tasks.py（Celery 内建 django fixup 会在 worker finalize
# 时 django.setup() 并逐 app 导入 tasks 模块——apps.core 起生效）
app.autodiscover_tasks()
# S0-4 Step 4 兑现承诺：config.probe / core.slow_probe 已迁 apps/core/tasks.py
# （probe 顺带改名 core.probe；S0-3/S0-3a 证据中的旧名以该注为桥）。
