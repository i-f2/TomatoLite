"""外部系统抽象层（§0.4）。

§0.2 规则 1：本包 **只准 stdlib + httpx**，任何 `django` import 即违约（tests/unit/test_s04_apps.py 双重断言：源码 grep + 无 settings 子进程 import）。
§0.2 规则 2 / §6 第 2 条：包内**不得**出现 `INTEGRATION_MODE` 分支——三态差异只是构造参数 base_url。
"""
