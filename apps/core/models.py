"""统一 BaseModel（§0.1 / S0-4）：时间戳 + 软删。所有业务表继承 AbstractBaseModel。

软删口径：默认 Manager **只见未删行**；`all_objects` 供审计/回收站；
实例与 QuerySet 的 `.delete()` 均落软删，硬删必须显式 `hard_delete()`（评审前业务代码禁用）。
"""
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

    objects = SoftDeleteManager()      # 默认：隐藏已软删
    all_objects = models.Manager()     # 全量：审计 / 回收站 / 唯一性复活判断

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
