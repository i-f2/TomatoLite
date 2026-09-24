"""统一 BaseModel（§0.1 / S0-4）：时间戳 + 软删。所有业务表继承 AbstractBaseModel。

软删口径：默认 Manager **只见未删行**；`all_objects` 供审计/回收站；
实例与 QuerySet 的 `.delete()` 均落软删，硬删必须显式 `hard_delete()`（评审前业务代码禁用）。

S0-5：自定义 User（PRD §3 四角色 + system 服务身份）。角色序
business < dev < ops < admin；system 不入序（逐位点显式放行，见 rbac.role_at_least）。
`role` **必填无默认**——安全字段不兜底，漏传必炸建/校验，不做静默降级。
"""

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as _AuthUserManager
from django.db import models
from django.utils import timezone


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(is_deleted=False)

    def dead(self):
        return self.filter(is_deleted=True)

    def delete(self):  # 批量软删（UPDATE 语义，不发信号——与单条一致走软删）
        return self.update(is_deleted=True, deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=False)


class AbstractBaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()  # 默认：隐藏已软删
    all_objects = models.Manager()  # 全量：审计 / 回收站 / 唯一性复活判断

    class Meta:
        abstract = True

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

    def delete(self, using=None, keep_parents=False):  # 默认 delete = 软删
        self.soft_delete()

    def hard_delete(self):
        return super().delete()


class Role(models.TextChoices):
    """PRD §3 角色。rank 显式编码序（business < dev < ops < admin）；
    system 不参与排序——它是无人值守服务身份，逐位点显式判定（rbac 层）。"""

    BUSINESS = "business", "业务方"
    DEV = "dev", "开发"
    OPS = "ops", "运维"
    ADMIN = "admin", "管理员"
    SYSTEM = "system", "系统账号"


ROLE_RANK: dict[str, int] = {
    Role.BUSINESS: 10,
    Role.DEV: 20,
    Role.OPS: 30,
    Role.ADMIN: 40,
    # SYSTEM 故意缺席：无秩——role_at_least 对 system 返回 False（除非位点显式列出）
}


class UserManager(SoftDeleteManager, _AuthUserManager):
    """两谱系 MRO 合成：`get_queryset` 取软删口（SoftDeleteManager 在前），
    auth 协议件（`get_by_natural_key` / `create_user` / `create_superuser`）
    取 _AuthUserManager——ModelBackend 登录路径走 `get_by_natural_key`，
    裸 SoftDeleteManager 会在登录时 AttributeError（Step 1 登录测试锁死）。"""


class User(AbstractUser, AbstractBaseModel):
    """自定义用户（AUTH_USER_MODEL="core.User"，S0-5）。

    - system 账号：save() 强制 unusable password + 禁 staff/superuser 位，
      永不交互登录（ModelBackend 对 unusable hash 直接拒）。
    - 软删即除权：默认 `objects` 隐藏已删行；`authenticate()` 走默认口，
      软删用户不可能再取得会话（Step 1 测试锁死此行为）。
    """

    role = models.CharField(max_length=16, choices=Role.choices)  # 必填，无默认

    objects = UserManager()  # 软删口 ∧ auth 协议口（见 UserManager docstring）
    all_objects = models.Manager()  # 全量口：审计 / 用户名占位判断

    class Meta(AbstractUser.Meta):
        db_table = "tl_user"

    def save(self, *args, **kwargs):
        if self.role == Role.SYSTEM:
            if self.is_staff or self.is_superuser:
                raise ValueError("system 账号不得持有 staff/superuser 位")
            if self.has_usable_password():
                self.set_unusable_password()
        return super().save(*args, **kwargs)
