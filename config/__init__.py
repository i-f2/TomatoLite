from .celery import app as celery_app  # §0.1：celery 入口挂 config 下（S0-4 建 app 后不变）

__all__ = ["celery_app"]
