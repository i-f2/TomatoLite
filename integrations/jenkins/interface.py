"""JenkinsProvider 抽象（§0.4 签名逐一对应）。"""
from __future__ import annotations

import abc
import collections.abc

from .dtos import BuildInfo, QueueInfo, StageInfo


class JenkinsProvider(abc.ABC):
    @abc.abstractmethod
    def get_build(self, job: str, build_no: int) -> BuildInfo:
        """404/不可达 → result=NOT_FOUND（不是 raise，§0.4 枚举注释）。"""

    @abc.abstractmethod
    def get_queue_item(self, queue_id: int) -> QueueInfo: ...

    @abc.abstractmethod
    def trigger(self, job: str, params: dict) -> int:
        """返回 queue_id（真 build 号要过排队映射，§3.2）。"""

    @abc.abstractmethod
    def abort(self, job: str, build_no: int) -> bool:
        """对应 POST /job/{job}/{n}/stop。构建已终态时 Jenkins 对 stop 的返回
        **不视为错误**，实现内判掉（§0.4 第四轮注：M2-34 边 25 通道 A 唯一消费者）。"""

    @abc.abstractmethod
    def get_console_text(self, job: str, build_no: int) -> str: ...

    @abc.abstractmethod
    def get_stages(self, job: str, build_no: int) -> list[StageInfo]: ...
