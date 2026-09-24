"""§0.6 local：DEBUG=True，InMemory channel layer，强制本地 PG（§0.6a，非容器）。"""
from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
