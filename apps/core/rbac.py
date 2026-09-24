"""S0-5：RBAC 工具函数（PRD §3 角色序 + SoD 硬约束）。

- `role_at_least`：rank 比较（business < dev < ops < admin，拍板③）。
  **system 无秩恒 False**——服务身份要过位点，必须显式把 SYSTEM 列进
  `required_roles` / 白名单，绝不随"最低门槛"溜过任何排序判定。
- `enforce_separation_of_duties`：SoD 两要件（edge-29 口径，design-notes §3）——
  actor 秩 ≥ ops **且** actor ≠ owner。守卫在状态机层（M1-09/M2-25 消费），
  本模块只提供函数本体，不落任何视图（S0-5 Non-Goal）。
"""
from .exceptions import AppError
from .models import ROLE_RANK, Role


def role_at_least(user, floor: str) -> bool:
    """user 的角色秩 ≥ floor？匿名/无角色/system（无秩）一律 False。"""
    role = getattr(user, "role", None)
    return role in ROLE_RANK and floor in ROLE_RANK and ROLE_RANK[role] >= ROLE_RANK[floor]


def enforce_separation_of_duties(actor, owner) -> None:
    """同一发布单"发起人≠审批人"+ 审批最低 ops（PRD §3 SoD 硬约束）。

    通过则 None；任一要件不满足抛 AppError(SEPARATION_OF_DUTIES, 403)。
    actor/owner 皆取 User 主键比较（同一人换会话仍是同一人）。
    """
    if actor is None or owner is None:
        raise AppError("职责分离校验缺主体", code="SEPARATION_OF_DUTIES", status_code=403)
    if not role_at_least(actor, Role.OPS):
        raise AppError("审批需 ops 及以上角色", code="SEPARATION_OF_DUTIES", status_code=403)
    if actor.pk is not None and actor.pk == getattr(owner, "pk", None):
        raise AppError("发起人不得审批自己的发布单", code="SEPARATION_OF_DUTIES", status_code=403)
