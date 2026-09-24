"""K8sStateReader 抽象 + OQ-3 工厂（§0.4）。

工厂只收**字符串参数**（apps 侧读 settings.K8S_READER 传入）——本包不知道 settings 存在
（§0.2 规则 1）。app/env 语义键到集群坐标的解析在 apps/cmdb（与 resolve_hosts 同层）。
"""
from __future__ import annotations

import abc

from .dtos import PodState, WorkloadState


class K8sStateReader(abc.ABC):
    @abc.abstractmethod
    def get_workload(self, app: str, env: str) -> WorkloadState | None:
        """不存在返回 None；集群不可达抛异常——与 Jenkins NOT_FOUND 同一分流纪律。"""

    @abc.abstractmethod
    def list_pods(self, app: str, env: str) -> list[PodState]: ...

    @abc.abstractmethod
    def get_restarts(self, pod: str) -> int: ...


def make_reader(name: str, **kwargs) -> K8sStateReader:
    """settings.K8S_READER ∈ {"api", "kubectl"}（OQ-3）。未知值必须抛，不许静默兜底。"""
    from .api_reader import ApiK8sReader
    from .kubectl_reader import KubectlK8sReader

    impls = {"api": ApiK8sReader, "kubectl": KubectlK8sReader}
    try:
        return impls[name](**kwargs)
    except KeyError:
        raise ValueError(f"K8S_READER 未知取值: {name!r}（允许 {sorted(impls)}）") from None
