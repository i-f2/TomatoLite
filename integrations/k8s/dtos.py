"""K8s 状态 DTOs（§0.4 OQ-3；验证引擎只读消费，§5.2）。"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class WorkloadState:
    kind: str  # Deployment / StatefulSet
    name: str
    ready_replicas: int
    desired_replicas: int
    current_image: str | None = None  # "repo:tag" 原样——验证比对锚（M2-06）


@dataclasses.dataclass(frozen=True)
class PodState:
    name: str
    node: str | None
    phase: str  # Running / Pending / CrashLoopBackOff ...
    restart_count: int = 0
    ready: bool = False
