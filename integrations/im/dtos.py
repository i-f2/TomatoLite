"""IM DTOs（§0.4 OQ-4 双实现共用）。

`ticket`/`view` 是 apps 侧对象——本包零 Django（§0.2 规则 1），故签名以结构化
窄类型收：调用方（apps/feedback）负责把卡片字段抽成 ConfirmCard 传入。
"""

from __future__ import annotations

import dataclasses
import enum


class FeedbackActionType(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"
    CONFIRM_DEPLOY = "confirm_deploy"  # 通道 A 两类回执（§4）
    UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True)
class ConfirmCard:
    ticket_id: int
    ticket_no: str
    title: str
    state: str
    detail_url: str
    actor_candidates: tuple[str, ...] = ()  # 可审批人（职责分离校验在 apps 层）


@dataclasses.dataclass(frozen=True)
class FeedbackAction:
    type: FeedbackActionType
    actor: str | None = None
    ticket_no: str | None = None
    raw: dict = dataclasses.field(default_factory=dict, compare=False)
