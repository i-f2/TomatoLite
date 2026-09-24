"""§0.6 prod：RedisChannelLayer + 显式 task_routes（§0.5）+ SECURE_* / Sentry / JSON 日志。

骨架期只落"结构"，运维参数按部署环境再调；task_routes 的键为最终 task 全名，
S0-4/S0-5 落 app 时逐条替换占位注释。
"""
from .base import *  # noqa: F401,F403
from .base import env, CHANNEL_LAYERS  # noqa: F401

DEBUG = False
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("redis", 6379)],
            "expiry": 60,
            # F-6 实据：channel layer 无消息尺寸兜底——帧尺寸由 M1-15/M1-16 应用层双护栏保证
        },
    }
}

# §0.5：显式路由，绝不依赖默认队列；beat 单实例由 compose 拓扑保证
CELERY_TASK_ROUTES = {
    # "channels_a.callbacks.process": {"queue": "callback.process"},   # S0-5 落地时启用
    # "channels_b.tasks.run_playbook": {"queue": "exec.runner"},       # S0-4/M1-15 落地时启用
    # "verification.tasks.run_checks": {"queue": "verify.runner"},     # M2 落地时启用
    # "channels_a.sweeper.scan": {"queue": "sweeper"},                 # M2-XX 落地时启用
}
CELERY_TASK_DEFAULT_QUEUE = "default"

# pgbouncer transaction 模式：连接复用下的事务级 session 状态不可依赖（§1.5 advisory lock 口径同步）
CONN_MAX_AGE = 0
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "logging.Formatter"}},  # 结构化日志器 M1 装配时替换
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
