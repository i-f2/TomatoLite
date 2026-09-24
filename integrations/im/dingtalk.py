"""钉钉实现占位（OQ-4 之二；实现归 M1-19a/M2 通道 A 落地）。"""
from __future__ import annotations

import httpx

from .interface import IMNotifier


class DingTalkNotifier(IMNotifier):
    def __init__(self, app_key: str, app_secret: str, robot_code: str,
                 *, base_url: str = "https://api.dingtalk.com", timeout: float = 10.0):
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._app_key, self._app_secret, self._robot_code = app_key, app_secret, robot_code

    def send_confirm_card(self, card):  # noqa: ANN001
        raise NotImplementedError("M1-19a")

    def verify_signature(self, headers, body):  # noqa: ANN001
        raise NotImplementedError("M1-19a")

    def parse_action(self, body):  # noqa: ANN001
        raise NotImplementedError("M1-19a")
