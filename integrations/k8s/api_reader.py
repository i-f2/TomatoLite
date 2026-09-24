"""OQ-3 `api_reader`：只读 ServiceAccount 直连 kube API 占位（实现归 M2 验证引擎落地）。"""
from __future__ import annotations

import httpx

from .interface import K8sStateReader


class ApiK8sReader(K8sStateReader):
    def __init__(self, api_server: str, sa_token: str, ca_path: str | None = None,
                 *, timeout: float = 10.0):
        self._client = httpx.Client(
            base_url=api_server.rstrip("/"),
            headers={"Authorization": f"Bearer {sa_token}"},
            verify=ca_path or True,
            timeout=timeout,
        )

    def get_workload(self, app, env):  # noqa: ANN001
        raise NotImplementedError("M2")

    def list_pods(self, app, env):  # noqa: ANN001
        raise NotImplementedError("M2")

    def get_restarts(self, pod):  # noqa: ANN001
        raise NotImplementedError("M2")
