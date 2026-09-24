"""IMNotifier 抽象（§0.4；OQ-4 企微/钉钉双实现，配置选一，代码零分支判断在调用方）。"""
from __future__ import annotations

import abc
import collections.abc

from .dtos import ConfirmCard, FeedbackAction


class IMNotifier(abc.ABC):
    @abc.abstractmethod
    def send_confirm_card(self, card: ConfirmCard) -> str:
        """返回厂商消息 id（§4.2 哈希钉在发送字节上：回执对账锚）。"""

    @abc.abstractmethod
    def verify_signature(self, headers: collections.abc.Mapping[str, str],
                         body: bytes) -> bool:
        """坏签名 → False，由 feedback 侧 401 且不落库（§3.3/M2-05 同款立场）。"""

    @abc.abstractmethod
    def parse_action(self, body: collections.abc.Mapping) -> FeedbackAction: ...
