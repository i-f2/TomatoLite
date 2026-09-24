"""Jenkins httpx 客户端占位（实现归 M1-05/M2-34；全 NotImplementedError）。

HMAC callback 验签（§3.3）在 feedback 侧消费 JENKINS_CALLBACK_HMAC_KEY，不在本客户端。
"""
from __future__ import annotations

import httpx

from .interface import JenkinsProvider


class JenkinsClient(JenkinsProvider):
    def __init__(self, base_url: str, user: str, token: str, *, timeout: float = 10.0):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), auth=(user, token), timeout=timeout
        )

    def get_build(self, job, build_no):  # noqa: ANN001
        raise NotImplementedError("M1-05")

    def get_queue_item(self, queue_id):  # noqa: ANN001
        raise NotImplementedError("M1-05")

    def trigger(self, job, params):  # noqa: ANN001
        raise NotImplementedError("M1-05")

    def abort(self, job, build_no):  # noqa: ANN001
        raise NotImplementedError("M2-34")

    def get_console_text(self, job, build_no):  # noqa: ANN001
        raise NotImplementedError("M1-05")

    def get_stages(self, job, build_no):  # noqa: ANN001
        raise NotImplementedError("M1-05")
