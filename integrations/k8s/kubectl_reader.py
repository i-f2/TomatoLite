"""OQ-3 `kubectl_reader`：经通道 B 的 SshTransport 执行 kubectl 占位（实现归 M2）。

与 api_reader 同一 DTO 出口——验证引擎对两种读法零分支（§6 第 2 条的同族纪律）。
"""

from __future__ import annotations

from .interface import K8sStateReader


class KubectlK8sReader(K8sStateReader):
    def __init__(self, transport, namespace: str):  # transport: SshTransport（M1-15 落地后注入）
        self._transport = transport
        self._namespace = namespace

    def get_workload(self, app, env):  # noqa: ANN001
        raise NotImplementedError("M2")

    def list_pods(self, app, env):  # noqa: ANN001
        raise NotImplementedError("M2")

    def get_restarts(self, pod):  # noqa: ANN001
        raise NotImplementedError("M2")
