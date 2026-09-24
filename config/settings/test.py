"""§0.6 test：真实 PG（test runner 自建 test_tomatolite_*，依赖 §0.6a 的 CREATEDB），
InMemory layer；ALWAYS_EAGER 仅限非并发用例——并发用例禁 eager（§0.7 陷阱）。"""
from .base import *  # noqa: F401,F403

DEBUG = False
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
