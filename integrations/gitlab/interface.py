"""GitLabProvider 抽象（§0.4 签名逐一对应）。"""
from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Sequence

from .dtos import CommitAction, CommitInfo, FileInfo


class GitLabProvider(abc.ABC):
    @abc.abstractmethod
    def get_file(self, project: str, path: str, ref: str) -> FileInfo | None:
        """不存在返回 None——404 与基础设施故障必须分流（故障抛异常，§5.4 同旨）。"""

    @abc.abstractmethod
    def create_commit(self, project: str, branch: str, message: str,
                      actions: Sequence[CommitAction]) -> CommitInfo:
        """M1-13 代提交唯一写通道；乐观并发守卫（head sha 断言）在 M1 落地。"""

    @abc.abstractmethod
    def list_commits(self, project: str, path: str, since: dt.datetime,
                     per_page: int = 20) -> list[CommitInfo]: ...
