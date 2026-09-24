from django.apps import AppConfig

from .dbguard import ensure_postgresql


class CoreConfig(AppConfig):
    name = "apps.core"

    def ready(self):
        # S0-8（§0.6a 全局闸）：任何进程入口都必经 django.setup() ⇒ ready()，
        # 非 PG 配置在此即 ImproperlyConfigured——启动失败，不带病运行。
        ensure_postgresql()
