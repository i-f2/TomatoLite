"""CMDB Admin 注册（M1-01）。

DoD 的"Admin 可增删改查"是**给人过目的入口**，故本文件只解决**可见性与可查性**，
不在此实现业务校验——形态与字段的交叉校验（如 `deploy_mode=host` 却配了 `k8s_workload`
直接拒绝，§5.7）属 M1-02 的保存路径。两处都写会出现两套迟早不一致的规则。

两条与模型口径有关的 Admin 事实：
- **删除即软删**：Admin 的删除动作走 `Model.delete()` → `soft_delete()`（AbstractBaseModel
  口径），行留在表里、名字被部分唯一索引放行可复用；因此 FK 的 `PROTECT` 在正常路径上
  不会被触发，它是防"绕过软删直接硬删"的闸。
- **列表只见未删行**：默认 Manager 隐藏软删行，admin 亦沿用它——这是有意的（软删=业务删除），
  软删行的审计查询走 `all_objects`，不占 Admin 列表。
"""

from django.contrib import admin

from .models import CmdbApp, CmdbEnv, CmdbHost, CmdbSystem


@admin.register(CmdbSystem)
class CmdbSystemAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    search_fields = ("name", "owner")


@admin.register(CmdbApp)
class CmdbAppAdmin(admin.ModelAdmin):
    list_display = ("name", "system", "deploy_mode", "health_url", "log_path", "created_at")
    list_filter = ("deploy_mode",)
    search_fields = ("name", "system__name")
    list_select_related = ("system",)


@admin.register(CmdbEnv)
class CmdbEnvAdmin(admin.ModelAdmin):
    # tier / auto_approve / min_hosts 是审批门禁与 preflight 的输入（§1.3 / §2.5），
    # 摆在列表页是为了让"哪些环境会自动通过审批"一眼可查。
    list_display = ("name", "app", "tier", "auto_approve", "min_hosts", "cluster")
    list_filter = ("tier", "auto_approve")
    search_fields = ("name", "app__name", "app__system__name")
    list_select_related = ("app", "app__system")


@admin.register(CmdbHost)
class CmdbHostAdmin(admin.ModelAdmin):
    list_display = ("hostname", "ip", "env", "updated_at")
    list_filter = ("env__tier",)
    search_fields = ("hostname", "ip", "env__name")
    list_select_related = ("env", "env__app")
