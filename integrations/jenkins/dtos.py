"""Jenkins DTOs（§0.4）。BuildInfo.result 六值枚举是**刻意设计**：
"Jenkins 挂了/404"（NOT_FOUND）与"构建失败"（FAILURE）在 DTO 层就分开，
上层不得把基础设施故障误判成业务失败（§0.4 注 / §5.4 同旨）。"""
from __future__ import annotations

import dataclasses
import enum


class BuildResult(str, enum.Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ABORTED = "ABORTED"
    UNSTABLE = "UNSTABLE"
    RUNNING = "RUNNING"
    NOT_FOUND = "NOT_FOUND"


@dataclasses.dataclass(frozen=True)
class BuildInfo:
    number: int
    result: BuildResult
    building: bool = False
    url: str | None = None
    timestamp_ms: int | None = None
    duration_ms: int | None = None


@dataclasses.dataclass(frozen=True)
class QueueInfo:
    id: int
    why: str | None = None        # 排队原因（sweeper 判卡死用，§3.4）
    executable_number: int | None = None  # 出队后映射到的 build 号


@dataclasses.dataclass(frozen=True)
class StageInfo:
    id: str
    name: str
    status: str
    duration_ms: int | None = None
