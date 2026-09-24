"""GitLab DTOs（§0.4）。纯 stdlib dataclass/enum，零 Django（§0.2 规则 1）。"""

from __future__ import annotations

import dataclasses
import enum


class ActionType(str, enum.Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclasses.dataclass(frozen=True)
class FileInfo:
    path: str
    ref: str
    content: str  # 文件全文（M1-13 代提交读改写以它为事实源）
    blob_sha: str | None = None


@dataclasses.dataclass(frozen=True)
class CommitAction:
    path: str
    type: ActionType
    content: str | None = None  # DELETE 时为 None


@dataclasses.dataclass(frozen=True)
class CommitInfo:
    sha: str
    message: str
    web_url: str | None = None
