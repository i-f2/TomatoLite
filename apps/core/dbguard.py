"""S0-8 全局闸（§0.6a）：**非 PostgreSQL 直接启动失败**，SQLite 是禁区。

为什么必须是运行态闸而非 settings 校验：`base.py` 的 `DATABASES` 由
`env.db_url("DATABASE_URL")` 注入——被防的恰是"env 里配了个 sqlite:// 还
一切照常"这种失效形态（§0.6a：迁移静默通过、运行期才炸；更糟的是 §1.4
append-only 测试变成永绿空转）。闸挂在 `CoreConfig.ready()`：所有入口
（web/worker/beat/全部 manage.py 命令）都必经 `django.setup()` ⇒ 一处生效
全进程生效。

无 I/O 承诺（Django 文档对 ready() 的告诫）：`connection.vendor` 是后端
wrapper 的类属性，构造 wrapper 不建连——本模块**绝不**为测版本而强制
`ensure_connection()`；`server_version >= 15` 由三层分担：Django 6.1.1 PG
后端 `minimum_database_version=(15,)` 建连硬下限（F-8 实据，settings 不可
绕）、`test_s08_pg_guard` 的钉版+活体断言、S0-7 锁基建 vendor 前置断言。
"""

from django.core.exceptions import ImproperlyConfigured
from django.db import connections

REQUIRED_VENDOR = "postgresql"


def _check(alias: str, conn) -> None:
    if conn.vendor != REQUIRED_VENDOR:
        raise ImproperlyConfigured(
            f"§0.6a 禁区：alias={alias!r} 的 vendor={conn.vendor!r} 非 "
            f"'postgresql'（sqlite 是显式禁用项，见 design-notes §0.6a）——"
            "本地/生产一律 PostgreSQL（>= 15，F-8 基线）"
        )


def ensure_postgresql(conn=None, _aliases=None) -> None:
    """生产路径逐 alias 过闸；测试可注入单个 wrapper/stub。

    逐 alias（而非只看 default）是给未来的多库留闸：§0.6a 的禁区是整个
    项目，不是某个 alias。
    """
    if conn is not None:
        _check(getattr(conn, "alias", "default"), conn)
        return
    for alias in _aliases if _aliases is not None else connections:
        _check(alias, connections[alias])
