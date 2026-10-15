"""CMDB 四层模型（M1-01）：业务系统 → 应用 → 环境 → 主机。

**唯一事实源铁律**（PRD §1.3 铁律 2）：主机/应用/环境只存这里，任何模块禁止私建主机表。
`tickets` 的 `app/env` 以 `on_delete=PROTECT` 指向本模块（design-notes §1.2）。

字段出处（本文件不发明字段——每个非平凡字段都指向设计文档）：
- `deploy_mode` / `health_url` / `health_expect_code` / `log_path` / `log_error_keywords`
  / `k8s_workload`：PRD FR-CMDB-02 应用级验证配置；§5.1 检查注册表按这些字段解析 check。
- `env.tier`：design-notes §1.3 `guard_duty_separation` 直接读它——`tier == 'prod'` 强制审批。
- `env.auto_approve`：同守卫——非 prod 可自动通过，但**必须写 `AUTO_APPROVED` 事件**，
  绝不允许 `approved_by=null` 静默进 APPROVED。
- `env.min_hosts`：§2.5——剔除不可达主机后数量低于它 → **中止整单**（默认 1）。
- `host.updated_at`：继承自 `AbstractBaseModel`；§2.7 以 `host.updated_at > run.created_at`
  判定 `STALE_INVENTORY`。
- `health_expect_code` 默认 200：PRD FR-CMDB-02 的探活语义（URL + 期望状态码），
  不设 null——"未配置期望码"在 §5 里没有对应语义，避免多一条无人处理的分支。

删除口径：一律软删（`AbstractBaseModel`）。因此唯一性**不能**用普通 unique：
- 唯一约束一律写成**部分唯一索引**（`condition=Q(is_deleted=False)`）——删掉的名字要能复用，
  而软删行仍留在表里供审计；两件事同时成立只有部分索引能做到。
- FK 一律 `PROTECT`：被单据/快照引用的层级不得硬删；业务侧删除走软删，故 PROTECT
  在正常路径上不会被触发，它是防"绕过软删直接硬删"的那道闸。
"""

from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.core.models import AbstractBaseModel


class DeployMode(models.TextChoices):
    """PRD FR-CMDB-02：部署形态。host 与 k8s 的验证路径完全不同（§5.2 执行器 × 传输层）。"""

    HOST = "host", "主机"
    K8S = "k8s", "K8s"


class EnvTier(models.TextChoices):
    """环境层级。**审批硬门禁读它**（§1.3）：prod 强制审批，开发不可自助发生产（OQ-2）。"""

    DEV = "dev", "开发"
    STAGING = "staging", "预发"
    PROD = "prod", "生产"


class CmdbSystem(AbstractBaseModel):
    """业务系统（四层模型最外层）。"""

    name = models.CharField(max_length=128)
    owner = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "cmdb_system"
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(is_deleted=False),
                name="uniq_cmdb_system_name_alive",
            )
        ]

    def __str__(self) -> str:
        return self.name


class CmdbApp(AbstractBaseModel):
    """应用 + **应用级验证配置**（FR-CMDB-02）。验证引擎按 deploy_mode 分派执行器/传输层。

    应用名规范校验（同义混名拦截）属 M1-03，不在本模型层做——此处只保证 `(system, name)`
    在未删行内唯一；规范化命名是 M1-03 的职责。
    """

    system = models.ForeignKey(CmdbSystem, on_delete=models.PROTECT, related_name="apps")
    name = models.CharField(max_length=128)
    deploy_mode = models.CharField(max_length=8, choices=DeployMode.choices)

    # ---- 验证配置（§5.1 检查注册表的输入）----
    health_url = models.URLField(max_length=512, blank=True)
    health_expect_code = models.PositiveSmallIntegerField(default=200)
    log_path = models.CharField(max_length=255, blank=True)
    # 应用自定义错误关键字；内置 error/exception/panic 由验证层兜底（§5.1），此处不重复录入
    log_error_keywords = ArrayField(models.CharField(max_length=64), default=list, blank=True)
    k8s_workload = models.CharField(max_length=128, blank=True)

    class Meta:
        db_table = "cmdb_app"
        constraints = [
            models.UniqueConstraint(
                fields=["system", "name"],
                condition=models.Q(is_deleted=False),
                name="uniq_cmdb_app_system_name_alive",
            )
        ]

    def __str__(self) -> str:
        return f"{self.system.name}/{self.name}"


class CmdbEnv(AbstractBaseModel):
    """应用下的环境。`tier` / `auto_approve` / `min_hosts` 三个字段都有设计文档里的现成消费者
    （见模块 docstring）——本任务只建字段，消费方分别在 M1-09（守卫）与 M1-19a（preflight）。
    """

    app = models.ForeignKey(CmdbApp, on_delete=models.PROTECT, related_name="envs")
    name = models.CharField(max_length=64)
    tier = models.CharField(max_length=16, choices=EnvTier.choices)
    auto_approve = models.BooleanField(default=False)
    cluster = models.CharField(max_length=128, blank=True)
    variables = models.JSONField(default=dict, blank=True)
    min_hosts = models.PositiveSmallIntegerField(default=1)

    class Meta:
        db_table = "cmdb_env"
        constraints = [
            models.UniqueConstraint(
                fields=["app", "name"],
                condition=models.Q(is_deleted=False),
                name="uniq_cmdb_env_app_name_alive",
            )
        ]

    def __str__(self) -> str:
        return f"{self.app}@{self.name}"


class CmdbHost(AbstractBaseModel):
    """环境下的主机。`updated_at`（继承）是 §2.7 `STALE_INVENTORY` 的判定输入。

    SSH 连接参数（port / user / 凭据引用）**不在本任务**：消费方是 `SshTransport`（M2-10），
    届时按 §5.2 的实际口径迁移补字段，不建投机字段。
    """

    env = models.ForeignKey(CmdbEnv, on_delete=models.PROTECT, related_name="hosts")
    hostname = models.CharField(max_length=255)
    ip = models.GenericIPAddressField()
    tags = ArrayField(models.CharField(max_length=64), default=list, blank=True)

    class Meta:
        db_table = "cmdb_host"
        constraints = [
            models.UniqueConstraint(
                fields=["env", "hostname"],
                condition=models.Q(is_deleted=False),
                name="uniq_cmdb_host_env_hostname_alive",
            )
        ]
        indexes = [models.Index(fields=["ip"], name="idx_cmdb_host_ip")]

    def __str__(self) -> str:
        return f"{self.hostname}({self.ip})"
