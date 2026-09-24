"""§0.2 规则 4：SQL 侧时间比较一律用 DB 时钟（`Now()`），不用 Python 进程时钟。

用途：`qs.filter(created_at__lt=db_now() - timedelta(minutes=5))`——表达式进 SQL 由
数据库求值，规避 app/worker 机器与 DB 的时钟漂移（sweeper/超时判定属此类）。
**不要**对返回值调 `.now()` 取 Python 时间——它的意义恰恰是被渲染进 SQL。
"""

from django.db.models.functions import Now


def db_now():
    return Now()
