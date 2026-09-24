"""Mock 目标主机内存态 + **真日志文件**。

§5.7③ 的假阳性/轮转素材必须是真 inode——轮转是真 `rename+重建`，不是 config
里一个布尔位。`version` 同样落成真文件（`VERSION`），HTTP `/version`、SSH
`cat`、控制面写三者同源（口径=§6 第 3 条"单一实现"的文件版：单一出处）。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading

SEED_LOG_LINES = [
    "2026-09-24T00:00:01 INFO app boot pid=4001",
    "2026-09-24T00:00:09 ERROR legacy crash loop BEFORE deploy (historical line)",
]

DEFAULT_CONFIG = {
    "http_status": 200,  # M2-11 期望码素材
    "http_body": '{"status":"ok"}',  # M2-11 响应关键字素材
    "http_mode": "normal",  # normal | refuse(真关听=RST) | blackhole(收了不回)
    "tcp_listen": True,  # M2-12 裸 TCP 端口开/关
    "version": "1.2.3",  # 版本注入（写入 VERSION 文件，多面同源）
    "process_alive": True,  # 进程状态注入；False ⇒ 联动 app 端口拒连
}


class State:
    def __init__(self, base_dir: str | None = None):
        self.lock = threading.RLock()
        self.dir = base_dir or tempfile.mkdtemp(prefix="tl-host-")
        self.owns_dir = base_dir is None
        self.log_path = os.path.join(self.dir, "app.log")
        self.log_rotated_path = os.path.join(self.dir, "app.log.1")
        self.version_path = os.path.join(self.dir, "VERSION")
        self.config = dict(DEFAULT_CONFIG)
        self._write_seed()

    # ---- 文件面（调用方负责持锁）----

    def _write_seed(self) -> None:
        with open(self.log_path, "w") as f:
            f.write("\n".join(SEED_LOG_LINES) + "\n")
        self._write_version(DEFAULT_CONFIG["version"])
        if os.path.exists(self.log_rotated_path):
            os.remove(self.log_rotated_path)

    def _write_version(self, v: str) -> None:
        with open(self.version_path, "w") as f:
            f.write(v)

    def get_version(self) -> str:
        with self.lock, open(self.version_path) as f:
            return f.read().strip()

    def read_log(self) -> str:
        with self.lock, open(self.log_path) as f:
            return f.read()

    def append_log(self, lines: list[str]) -> None:
        with self.lock, open(self.log_path, "a") as f:
            f.write("\n".join(lines) + "\n")

    def set_log(self, lines: list[str]) -> None:
        with self.lock, open(self.log_path, "w") as f:
            f.write("\n".join(lines) + "\n")

    def rotate_log(self) -> None:
        """真轮转：rename → 重建。inode 变、size 归零、历史 ERROR 随 .1 离场。"""
        with self.lock:
            os.replace(self.log_path, self.log_rotated_path)
            with open(self.log_path, "w"):
                pass

    def log_stat(self) -> os.stat_result:
        return os.stat(self.log_path)

    # ---- 语义联动 ----

    def effective_app_mode(self) -> str:
        """进程死了端口还开着 = mock 自己先撒谎。联动优先级高于显式 mode。"""
        if not self.config["process_alive"]:
            return "refuse"
        return self.config["http_mode"]

    # ---- 生命周期 ----

    def reset(self) -> None:
        with self.lock:
            self.config = dict(DEFAULT_CONFIG)
            self._write_seed()

    def cleanup(self) -> None:
        if self.owns_dir:
            shutil.rmtree(self.dir, ignore_errors=True)
