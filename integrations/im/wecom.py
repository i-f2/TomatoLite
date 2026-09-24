"""企微实现占位（OQ-4 之一；实现归 M1-19a/M2 通道 A 落地）。"""

from __future__ import annotations

import httpx

from .interface import IMNotifier


class WeComNotifier(IMNotifier):
    def __init__(
        self,
        corp_id: str,
        corp_secret: str,
        agent_id: str,
        *,
        base_url: str = "https://qyapi.weixin.qq.com",
        timeout: float = 10.0,
    ):
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._corp_id, self._corp_secret, self._agent_id = corp_id, corp_secret, agent_id

    def send_confirm_card(self, card):  # noqa: ANN001
        raise NotImplementedError("M1-19a")

    def verify_signature(self, headers, body):  # noqa: ANN001
        raise NotImplementedError("M1-19a")

    def parse_action(self, body):  # noqa: ANN001
        raise NotImplementedError("M1-19a")
