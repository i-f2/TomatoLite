# TomatoLite 工程设计说明

| 项目 | 内容 |
|---|---|
| 文档 | 工程设计说明（Design Notes） |
| 版本 | v0.6（设计评审稿） |
| 日期 | 2026-09-23 |
| 上游依据 | `PRD.md` v1.0（含 ADR-6 ~ ADR-11） |
| 配套文档 | `docs/implementation-plan.md`（实施进度总表） |
| 状态 | 第二轮全文审计结论已全部落地（§7）；原 2 处留白（边 29 人工强制放行 / 边 30 事后回滚）**已由产品拍板开放**，迁移表定稿为 **31 条边 + 3 终态**——`SUCCESS` 不再是终态。v0.4 补入本地开发环境实况：§0.6a（本地 PG ≥14，非容器）/ §0.6b（临时真实实例联调栈）/ §6 第 7 条（双层环境分界线）。v0.5 = 第四轮开工前缺口盘查（§7）：**补录边 31 `TRIGGER_BUILD_LOST`**（§3.6 早已引用而漏登表）、修边 10 陈旧引用、`JenkinsProvider.abort` 闭环、边 25 落任务 `M2-34`。v0.6 = 第五轮（§7）：**S0-1 spike 实据修订 §2.1/§2.3**（F-1~F-5；RESCUED 改终态计数回填 ③′、白名单增补 `runner_item_on_*` 族、生产定死 `run_async()`），迁移表 31 边不动 |

---

## 本文档的定位

`PRD.md` 回答**做什么**；本文档回答**怎么做才不会在生产上炸**。

它不重复 PRD 的需求编号，只写五个高风险子系统（状态机、通道 B 执行、Jenkins 回调与对账、GitLab 代提交与旁路、验证引擎）的工程决策，以及每条决策背后的失败模式。**每个子系统末尾单列"生产会炸的点"**——这些是评审中逐条推演出来的，不是通用最佳实践清单。

阅读顺序建议：**§0（布局边界）→ §1（状态机）→ §5（验证引擎）**。§1 和 §5 决定了全系统的锁序与状态语义，改动成本最高；§2/§3/§4 是三条外部链路，相对独立。

---

## 目录

- [§0 工程布局与基础设施](#0-工程布局与基础设施)
- [§1 单据状态机 + 追加式事件表](#1-单据状态机--追加式事件表)
- [§2 通道 B：ansible-runner 逐行事件流](#2-通道-bansible-runner-逐行事件流)
- [§3 Jenkins 回调接收器 + 对账 sweeper](#3-jenkins-回调接收器--对账-sweeper)
- [§4 GitLab：代提交 + 幂等守卫 + Webhook 旁听](#4-gitlab代提交--幂等守卫--webhook-旁听)
- [§5 验证引擎](#5-验证引擎)
- [§6 Mock 层与 Settings 落地规则](#6-mock-层与-settings-落地规则)
- [§7 决策记录](#7-决策记录)

---

## §0 工程布局与基础设施

### 0.1 目录与 App 划分

```
TomatoLite/
├── manage.py
├── config/
│   ├── settings/{base,local,test,prod}.py
│   ├── urls.py  asgi.py  wsgi.py  celery.py  routing.py
├── apps/
│   ├── core/            # 抽象模型、审计、HMAC、幂等工具、异常、请求上下文
│   ├── cmdb/            # 唯一事实源：system/app/env/host + 验证配置 + inventory 生成
│   ├── tickets/         # 状态机 + 事件表 + 并发锁 + 审批 + 排队
│   ├── channels_a/      # 通道A：gitlab client / 清单代提交 / jenkins client / 回调 / sweeper
│   ├── channels_b/      # 通道B：ansible-runner 执行 / 事件泵 / task 结果
│   ├── verification/    # 验证引擎：执行器 / 传输层 / 检查注册表 / 结论聚合
│   ├── feedback/        # IM 确认闭环（OQ-4 抽象）
│   └── metrics/         # 度量与报告
├── integrations/        # 纯接口 + DTO，零 Django 依赖
│   ├── gitlab/{interface.py,http_client.py,dtos.py}
│   ├── jenkins/{interface.py,http_client.py,dtos.py}
│   ├── im/{interface.py,wecom.py,dingtalk.py}
│   └── k8s/{interface.py,api_reader.py,kubectl_reader.py}
├── mocks/
│   ├── gitlab_fake/  jenkins_fake/     # 独立进程（HTTP 假服务）
│   ├── k8s_fake.py                     # 进程内假实现（Python 接口）
│   └── factories.py                    # 测试用 payload 构造器
└── tests/{unit,integration,concurrency}/
```

### 0.2 四条边界规则（违反即视为设计缺陷）

1. **`integrations/` 只依赖 `httpx` 与标准库，不 import Django。** 业务 service 层只依赖 `integrations/*/interface.py`。
2. **业务代码里不允许出现 `if settings.INTEGRATION_MODE == "mock"`。** 切换真实/假实现只靠 `base_url` 配置。一旦开始写 `if mock` 分支，等于把测试专用逻辑焊进生产代码。
3. **`cmdb.services.resolve_hosts(app, env)` 是主机解析的唯一函数。** 通道 B 生成 inventory 与验证引擎解析验证目标**必须调用同一个它**。
4. **凡是进入 SQL 比较的时间，一律用 `core/db_time.py::db_now()`（返回 Django 的 `Now()` 表达式），禁止用 Python 侧算好的时间戳。** Python 的 `timezone.now()` 只允许用于写入字段值和界面展示。

   违反规则的后果不是"精度差一点"，而是**多 worker 跨机时钟偏移时，某台机器上的 watchdog 永远判定"还没到期"，锁再也不回收**——且因为是相对偏移，测试环境（单机）永远复现不出来。

### 0.3 关于边界规则 3（全系统最容易埋的雷）

若 `verification` 用 `env.hosts.all()` 解析目标，而 `channels_b` 用 `resolve_hosts(app, env)` 生成 inventory，那么当一个环境承载多个应用时（现实中大量存在），会出现：**playbook 发布到 A 组主机、验证检查 B 组主机，结果全绿**。

这是最难发现的缺陷类型——所有检查都 PASS，单据正常闭环，但发布的机器根本没被验证过。

对策：强制两条路径调用同一函数，并加一条测试断言二者的输出集合恒等。

**`resolve_hosts` 与 `build_inventory` 的关系也必须钉死**（此前只写了前者是唯一函数，后者却出现在 §2.5）：`build_inventory(app, env, *, extra_host_ids=None)` **必须内部调用 `resolve_hosts(app, env)`**，不得自己查 `env.hosts`。`extra_host_ids` 是给人工运维作业用的逃生口，**发布链路（通道 B 的版本发布 + 验证引擎）必须传 `None`**；一旦在发布链路上用了它，§0.3 要防的"发布 A 组、验证 B 组"会以"发布 A+C、验证 A"的新形态回来。这条由 `M1-04` 的一条断言测试兜住。

### 0.4 外部系统抽象层

| 接口 | 方法签名（节选） | OQ 挂载点 |
|---|---|---|
| `GitLabProvider` | `get_file(project, path, ref) -> FileInfo \| None`；`create_commit(project, branch, message, actions) -> CommitInfo`；`list_commits(project, path, since, per_page)` | — |
| `JenkinsProvider` | `get_build(job, build_no) -> BuildInfo`；`get_queue_item(id) -> QueueInfo`；`trigger(job, params) -> queue_id`；`abort(job, build_no) -> bool`；`get_console_text(job, build_no) -> str`；`get_stages(job, build_no) -> list[StageInfo]` | — |

> **`abort` 的口径（第四轮补，闭环 §7 遗留 2）**：对应 `POST /job/{job}/{n}/stop`（Mock 端点 §3.8 早已暴露）。当前唯一消费者是 `M2-34`（边 25 终止通道 A 单前 best-effort 停掉非终态构建）；构建已终态时 Jenkins 对 stop 的返回**不视为错误**，实现内判掉。通道 B 的对应物是 §2.6 的 `killpg`——两条通道"叫停"语义自此对称。
| `K8sStateReader` | `get_workload(app, env) -> WorkloadState`；`list_pods(app, env) -> list[PodState]`；`get_restarts(pod) -> int` | **OQ-3**：`api_reader`（只读 SA）/ `kubectl_reader`（走 SshTransport 执行 kubectl）由 `settings.K8S_READER` 选择 |
| `IMNotifier` | `send_confirm_card(ticket, view) -> msg_id`；`verify_signature(headers, body)`；`parse_action(body) -> FeedbackAction` | **OQ-4**：`wecom.py` / `dingtalk.py` 双实现 |

`BuildInfo.result` 用枚举 `SUCCESS | FAILURE | ABORTED | UNSTABLE | RUNNING | NOT_FOUND`。

**这一处枚举设计是刻意的**：把"Jenkins 挂了 / 404"（`NOT_FOUND`）与"构建失败"（`FAILURE`）在 DTO 层就分开。若不在这一层分开，上层必然把基础设施故障误判成业务失败——同 §5.4 的问题。

### 0.5 Celery 队列与 worker 拓扑

| 队列 | 服务 | 并发 | 说明 |
|---|---|---|---|
| `callback.process` | `worker_callback` | 8 | 短任务、高优先级。**绝不能与 ansible 共池**——否则回调被长作业饿死，Jenkins 侧超时重试，雪崩 |
| `exec.runner` | `worker_runner` | = 全局并发上限（默认 3） | **必须 `--prefetch-multiplier=1`**，见下 |
| `verify.runner` | `worker_verify` | 4 | 独立池，防止验证被 ansible 队列饿死 |
| `sweeper` / `notify` / `default` | `worker_default` | 4 | **beat 必须单实例**，否则 sweeper 双发 |

**`--prefetch-multiplier` 默认是 4**：一个 prefork 子进程会一次性预取 4 个任务。表现为"配置的并发上限是 3，实际同时跑了 12 个 playbook"。这是最容易被忽略、又最难排查的一条——因为配置看起来完全正确。

### 0.6 Settings 分片

| 分片 | 要点 |
|---|---|
| `base.py` | `django-environ` 读环境变量；**无任何密钥默认值** |
| `local.py` | `DEBUG=True`；**强制 PostgreSQL**（本地已装实例，§0.6a）；`CHANNEL_LAYERS=InMemoryChannelLayer`；`INTEGRATION_MODE` 三态——`mock`（默认，base_url 指向假服务）/ `integration`（指向 §0.6b 的临时真实实例，仅人工联调）/ 生产用 `prod.py`。业务代码对三态**零分支**，差异只在 base_url（§6 第 2 条） |
| `test.py` | 真实 PG；`CHANNEL_LAYERS=InMemory`；`CELERY_TASK_ALWAYS_EAGER=True` 仅用于非并发测试 |
| `prod.py` | `CHANNEL_LAYERS=RedisChannelLayer`；显式 `task_routes`；`CONN_MAX_AGE` 配 pgbouncer transaction 模式；`SECURE_*`、Sentry、JSON 结构化日志 |

**为什么本地也强制 PG**：JSONB、部分唯一索引、`FOR UPDATE SKIP LOCKED`、事务级 advisory lock、`REVOKE UPDATE` 全部依赖 PostgreSQL。用 SQLite 会让迁移静默通过、运行期才炸；更糟的是**让 §1.4 的 append-only 测试变成永远通过的空测试**。

### 0.6a 本地数据库（已装实例，非容器）

PostgreSQL 使用**本地已安装的实例**（MacPorts），不进 `docker-compose`——`S0-3` 的 compose 清单只含 Redis7 + app + worker 组 + beat。

| 项 | 口径 |
|---|---|
| 客户端 | `/opt/local/bin/psql14 -U postgres -d postgres`（运维/排查时手查事件表用的就是它，DoD 里出现 `psql14` 即指此路径） |
| 开发库 | `DATABASE_URL=postgres://postgres@localhost:5432/tomatolite_dev` |
| 测试库 | Django test runner 自动建 `test_tomatolite_*`，依赖 `postgres` 角色的 `CREATEDB`（superuser 默认具备，勿回收） |
| **版本基线** | **≥ 14**。本文档使用的全部 PG 特性（JSONB / 部分唯一索引 / `FOR UPDATE SKIP LOCKED` / 事务级 advisory lock / `REVOKE UPDATE` / `Now()` 表达式）均为 9.x 时代特性，14 全兼容，已逐条核对 |
| 基线纪律 | 引入 **15+/16-only 语法（如 `MERGE`、`uuidv7()`）前，必须先在本节上调基线并记入 §7**，否则本地全绿、生产按锁定的版本炸 |

`S0-8` 的启动断言相应为：`connection.vendor == 'postgresql'` **且 `server_version >= 14`**。

### 0.6b 本地临时联调栈（docker/compose）

GitLab 与 Jenkins 在本地经 docker/compose **临时拉起**（`S0-9`，`docker-compose.integration.yml`），用于人工联调与里程碑演示：真实 push webhook、真实 notify 回调、真实凭据初始化。

两条硬约束：**① 它是临时的**——实例可被随时销毁，任何 CI 用例、任何 `自动断言` DoD 不得依赖它（自动化基座永远是 §6 的 Mock 层）；**② 切换只动 base_url**——`INTEGRATION_MODE=integration` 仅用于前端角标提示"当前连着临时真实实例"，业务代码零分支（§6 第 2 条）。

### 0.7 测试基础设施的一个陷阱

**并发测试必须用 `TransactionTestCase` + 真实线程 + 真实连接。**

Django 默认的 `TestCase` 把每个用例包在事务里，`select_for_update` 与部分唯一索引冲突的表现与生产完全不同——锁测试会变成永远通过的空测试。这是最隐蔽的测试陷阱：**测试全绿，但什么都没验证**。

建议在 `S0-7` 里故意写一个假锁测试，确认它**能失败**，以此证明测试基础设施有效。

---

## §1 单据状态机 + 追加式事件表

> 本章决定全系统的锁序与状态语义，是改动成本最高的部分。

### 1.1 PRD 未定义语义的冻结（已拍板 D-2）

**`APPROVED` = 已批准待调度（排队中，不持有锁）。**

真正持有锁的状态是 `EXECUTING / VERIFYING / CONFIRMING / VERIFICATION_ERROR / PAUSED / ROLLBACK`。

这一定义直接决定 §1.5 部分唯一索引的谓词。已于 2026-09-20 拍板为前者（见 §7 D-2 的取舍分析）。

由此得出：`APPROVED` **不入**部分唯一索引谓词，排队可见性由 `tl_ticket_queue` 承担，租约从进入 `EXECUTING` 时才开始（§1.6）。

### 1.2 模型（app: `tickets`）

```python
class Ticket(TimeStampedModel):
    code            # TL-YYYYMMDD-NNN, unique；由 TicketCodeSequence 表在事务内自增分配
    type            # APP_RELEASE | OPS_JOB
    trigger         # PLATFORM | BYPASS | MANUAL
    app, env        # FK cmdb，on_delete=PROTECT
    target_version, previous_version, release_note
    state           # TicketState
    current_batch   # 分批发布
    created_by, approved_by, approve_comment, approved_at

    # 并发与租约
    lease_owner           # f"{hostname}:{pid}:{uuid4}"，执行方持有
    lease_expires_at, heartbeat_at

    # 键链与幂等
    source_commit_sha, correlation_id, content_sha256
    parent_ticket         # 回滚双向关联

    verify_attempts       # int，默认 0；§1.3 guard_verify_attempts 的唯一计数来源
    paused_from_state     # TicketState | null；§1.3 边 24 恢复时的目标态

    # 边 29 人工强制放行（§1.3.1）。由 apply() 在同一事务内赋值，
    # guard_no_skip_verifying 读 force_released_at 决定是否放宽结论检查
    force_released_at     # datetime | null
    force_released_by     # FK(User) | null
    force_release_reason  # text | null

    state_entered_at, row_version    # 乐观锁

class TicketEvent(models.Model):        # tl_ticket_event，仅 INSERT
    ticket, seq, from_state, to_state, trigger
    actor, actor_role
    idempotency_key                     # unique
    payload = JSONField()               # JSONB
    prev_hash, hash                     # 每单哈希链
    created_at
    class Meta:
        unique_together = [("ticket", "seq")]

class AppEnvLock(models.Model):         # tl_app_env_lock：锁的"记录"
    app, env, holder_ticket, acquired_at, lease_expires_at, fencing_token
    class Meta:
        unique_together = [("app", "env")]

class TicketQueue(models.Model):        # 排队可见性，UI 展示"第 N 位"
    app, env, ticket, enqueued_at, priority, seq
```

### 1.3 状态机与守卫

```python
# apps/tickets/fsm.py
class TicketState(TextChoices):
    DRAFT, SUBMITTED, APPROVED, EXECUTING, VERIFYING, CONFIRMING, SUCCESS, \
    REJECTED, PAUSED, VERIFY_FAILED, ROLLBACK, ROLLED_BACK, CANCELLED, \
    VERIFICATION_ERROR = ...     # OQ-9：平台够不着 ≠ 应用坏了，见 §5.5

@dataclass(frozen=True)
class Transition:
    src: frozenset[TicketState]
    trigger: str
    dst: TicketState
    guards: tuple[Guard, ...]
    effects: tuple[Effect, ...]
    requires_lock: bool
    allowed_roles: frozenset[str]

TRANSITIONS: dict[str, Transition] = {...}   # 纯数据，可单元测试
```

**`TRANSITIONS` 是纯数据**——这样"每条合法边 + 每条非法边"都能被穷举测试，而不需要构造真实业务场景。

| Guard | 规则 |
|---|---|
| `guard_duty_separation` | `actor != ticket.created_by`。**OQ-2 硬约束**：`env.tier == 'prod'` 时强制；非 prod 可由 `env.auto_approve` 自动通过，但**必须写 `AUTO_APPROVED` 事件**，绝不允许 `approved_by=null` 静默进入 APPROVED |
| `guard_submit_only_from_executing` | 代提交入口断言 `state == EXECUTING` 且 `approved_by is not None`。**D-2 修正**：旧版为 `state == APPROVED`，与"`APPROVED` 不持锁"直接冲突——提交必须持锁，而 `APPROVED` 态不持锁，该守卫在新语义下永远无法满足 |
| `guard_verify_attempts` | 验证重试次数未超 `max_verify_attempts`（默认 3）。计数存在 `Ticket.verify_attempts`，**在该守卫内自增并写入事件 payload**，不依赖 `VerificationRun.objects.count()`（后者在并发重试下会数错） |
| `guard_pause_resume_same_phase` | 从 `PAUSED` 恢复只能回到**暂停时的来源态**。`PAUSED` 事件 payload 必须携带 `from_state` |
| `guard_no_skip_verifying` | 进入 `SUCCESS` 前，事件表必须存在 `to_state=VERIFYING` 与 `to_state=CONFIRMING` 两条记录，且满足其一：① 最新 `VerificationRun.conclusion == PASS`；② `Ticket.force_released_at is not None`。**①② 缺一不可地覆盖了边 18 的两个入口**——边 29 强制放行进来的单，其结论本就是 `FAIL`，若不加 ② 该单会被自己的守卫卡死在 `CONFIRMING` |
| `guard_holds_lock` | `requires_lock` 的转移必须先持有 `AppEnvLock` |
| `guard_version_monotonic` | `target_version != previous_version`（回滚单例外） |
| `guard_force_release_authorized` | 边 29 专用：`actor.role ∈ {ops, admin}` 且 `actor != ticket.created_by`。角色检查**放在守卫里而非视图层**——放行是一条状态边，绕过 API 直接调 `apply()` 的路径也必须被拦 |
| `guard_force_release_reason` | 边 29 专用：`reason` 非空且长度 ≥ 10（阈值常量可配）。**不接受默认值或占位符**——这条守卫的全部价值就是逼出一句人话 |
| `guard_post_hoc_rollback_authorized` | 边 30 专用：`actor.role ∈ {ops, admin}` 且 `actor != ticket.created_by` |
| `guard_target_version_confirmed` | 边 30 专用：必须显式传入 `target_version`，且该版本存在于本应用的历史版本记录中。**不传即拒绝**，不做"默认回滚到上一版"的猜测 |

#### 1.3.1 权威迁移表（`TRANSITIONS` 的唯一来源）

**这张表是 `TRANSITIONS` 字典的完整定义。** 此前触发器名字散落在 §2–§5 的散文与代码片段里，已经出现两处互相打架（见 §4.2 的 D-2 修正）。**任何新增边必须先改这张表**，代码与测试都以它为准。

`锁` 列 = `requires_lock`；`触发者` 中"调度器"指 §1.6a 的 `dispatch_queued`。

| # | 源态 | 触发 | 目标态 | 触发者 | 锁 | 关键守卫 / 说明 |
|---|---|---|---|---|---|---|
| 1 | `DRAFT` | `TRIGGER_SUBMIT` | `SUBMITTED` | 用户 | — | |
| 2 | `DRAFT` | `TRIGGER_BYPASS_DETECTED` | `EXECUTING` | 系统（webhook） | ✅ | **旁路单的出生路径**：单据先以 `DRAFT` 创建，再在**同一事务内** `apply()` 到 `EXECUTING`。§1.4 的"`apply()` 是唯一写 `state` 的函数"因此不被破坏 |
| 3 | `DRAFT` | `TRIGGER_CANCEL` | `CANCELLED` | 用户 | — | |
| 4 | `SUBMITTED` | `TRIGGER_APPROVE` | `APPROVED` | 审批人 | — | `guard_duty_separation` |
| 5 | `SUBMITTED` | `TRIGGER_REJECT` | `REJECTED` | 审批人 | — | `guard_duty_separation` |
| 6 | `SUBMITTED` | `TRIGGER_CANCEL` | `CANCELLED` | 用户 | — | |
| 7 | `APPROVED` | `TRIGGER_START` | `EXECUTING` | **调度器**（唯一晋升者）/ 人工立即执行 | ✅ | **D-2 修正后的唯一入口**。取 `AppEnvLock`；抢不到 → 留在 `APPROVED` 并写 `tl_ticket_queue`，不得报错 |
| 8 | `APPROVED` | `TRIGGER_CANCEL` | `CANCELLED` | 用户 | — | |
| 9 | `EXECUTING` | `TRIGGER_EXEC_FINISHED` | `VERIFYING` | 系统 | ✅ | 代提交 + 构建成功，进入验证 |
| 10 | `EXECUTING` | `TRIGGER_SUBMIT_FAILED` | `PAUSED` | 系统 | ✅ | **代提交永久失败（GitLab 不可达/5xx）**。`payload.reason_class=INFRA`，出口为"重试提交"（边 24）而非回滚 |
| 11 | `EXECUTING` | `TRIGGER_MANIFEST_CONFLICT` | `PAUSED` | 系统 | ✅ | 清单已存在且内容哈希不匹配（`MANIFEST_CONFLICT` / `MANIFEST_TAMPERED`）。必须人工判断是接受既有文件还是终止 |
| 12 | `EXECUTING` | `TRIGGER_RUN_ORPHANED` | `PAUSED` | 系统 | ✅ | §2.7① 孤儿执行；**绝不自动重跑** |
| 13 | `EXECUTING` | `TRIGGER_INVENTORY_STALE` | `PAUSED` | 系统 | ✅ | §2.7③ `STALE_INVENTORY`，要求重新审批 |
| 14 | `EXECUTING` | `TRIGGER_RUN_FAILED` | `PAUSED` | 系统 | ✅ | 构建失败（Jenkins `FAILURE`）。**不进 `VERIFY_FAILED`**——那个态专指"应用发布成功但验证不过" |
| 15 | `VERIFYING` | `TRIGGER_VERIFY_PASSED` | `CONFIRMING` | 系统 | ✅ | |
| 16 | `VERIFYING` | `TRIGGER_VERIFY_FAILED` | `VERIFY_FAILED` | 系统 | ✅ | §5.5：**产品信号** |
| 17 | `VERIFYING` | `TRIGGER_VERIFY_ERROR` | `VERIFICATION_ERROR` | 系统 | ✅ | §5.5：**基础设施信号** |
| 18 | `CONFIRMING` | `TRIGGER_CONFIRMED` | `SUCCESS` | 用户（IM/页面） | ✅ | `guard_no_skip_verifying` |
| 19 | `VERIFY_FAILED` | `TRIGGER_ROLLBACK` | `ROLLBACK` | 用户 | ✅ | |
| 20 | `VERIFY_FAILED` | `TRIGGER_PAUSE` | `PAUSED` | 用户 | ✅ | |
| 21 | `VERIFICATION_ERROR` | `TRIGGER_VERIFY_RETRY` | `VERIFYING` | 用户 | ✅ | `guard_verify_attempts` |
| 22 | `VERIFICATION_ERROR` | `TRIGGER_ROLLBACK` | `ROLLBACK` | 用户 | ✅ | |
| 23 | `VERIFICATION_ERROR` | `TRIGGER_PAUSE` | `PAUSED` | 用户 | ✅ | |
| 24 | `PAUSED` | `TRIGGER_RESUME` | **来源态** | 用户 | ✅ | `guard_pause_resume_same_phase`。见下方说明 |
| 25 | `PAUSED` | `TRIGGER_TERMINATE` | `CANCELLED` | 用户 | ✅ | 人工对账确认无法继续 |
| 26 | `ROLLBACK` | `TRIGGER_ROLLBACK_FINISHED` | `ROLLED_BACK` | 系统 | ✅ | |
| 27 | `ROLLBACK` | `TRIGGER_ROLLBACK_FAILED` | `PAUSED` | 系统 | ✅ | 回滚本身失败，必须人工介入 |
| 28 | **任意持有态** | `TRIGGER_LEASE_LOST` | `PAUSED` | 系统（watchdog） | — | §1.6。写 `LEASE_LOST` 事件并记录 `from_state` |
| 29 | `VERIFY_FAILED` | `TRIGGER_FORCE_RELEASE` | `CONFIRMING` | 用户（≥ 审批人级别） | ✅ | **人工强制放行**：验证不过但业务判定可接受。`guard_force_release_authorized` + `guard_force_release_reason`。必须写 `FORCE_RELEASED` 事件，见下方说明 |
| 30 | `SUCCESS` | `TRIGGER_POST_HOC_ROLLBACK` | `ROLLBACK` | 用户（≥ 审批人级别） | ✅ | **事后回滚**：已闭环后发现线上问题。`guard_post_hoc_rollback_authorized` + `guard_target_version_confirmed`。**抢不到锁当场拒绝，不入队**，见下方说明 |
| 31 | `EXECUTING` | `TRIGGER_BUILD_LOST` | `PAUSED` | 系统（sweeper） | ✅ | §3.6 触发兜底：`queue_id` 存在但 `build_no` 一直不出现，且队列项已 `cancelled`/消失。**绝不猜 `build_no`**。`payload.reason_class=INFRA`；恢复出口同边 10 走**边 24**，但动作是"重新触发构建"。系补录（见下方"边 31 的完整约束"） |

**终态（3 个，无出边）**：`REJECTED` / `ROLLED_BACK` / `CANCELLED`。

> ⚠️ **`SUCCESS` 自边 30 起不再是终态。** 它仍是**闭环节点**（业务确认完成），但闭环不等于不可撤销。连带影响三处，已分别落地：① 度量看板必须按**最终态**统计成功率，一张"先成功、后回滚"的单计入回滚率而非成功率（`M3-01`）；② `guard_no_skip_verifying` 需为边 29 放宽（见 §1.3 守卫表）；③ PRD §2 对"闭环"的定义需补一句"闭环后可被事后回滚"。

#### 边 29 的完整约束（人工强制放行）

这条边让一个**已知验证不过**的发布进入 `SUCCESS`，是整张表里语义最危险的一条，因此约束也最重：

| 约束 | 内容 |
|---|---|
| 权限 | 执行者角色 ≥ 审批人（`ops` / `admin`），且 `actor != ticket.created_by`——**发起人不能自己放行自己** |
| 理由 | `reason` 必填且非空（最短长度在守卫里校验）；空理由直接拒绝，不允许"默认放行" |
| 审计 | 同一事务内写 `FORCE_RELEASED` 事件（payload 含 `reason`、`actor`、**当时的失败检查快照**）；`Ticket.force_released_at / force_released_by / force_release_reason` 三字段由 `apply()` 赋值（§1.2） |
| 可见性 | `SUCCESS` 的语义因此变成"验证通过**或**人工强制放行"两种。UI 必须区分，否则看板上一次强制放行与一次干净成功长得一模一样（`X-03` 加"人工放行"角标） |
| 度量 | 强制放行率与旁路率同级，进看板（`M3-01`）。这是治理指标，不是审计脚注 |

**边 29 明确不延伸到 `VERIFICATION_ERROR`。** "平台够不着"不允许强制放行——那等于用一句"我担着"跳过 ADR-6/ADR-9 专门防的事。`VERIFICATION_ERROR` 的出口仍然只有重试 / 回滚 / 暂停三条（边 21–23）。若某天真的需要，那必须是一次单独的决策，不能靠这条边的角色检查顺带放过去。

#### 边 30 的完整约束（事后回滚）

| 约束 | 内容 |
|---|---|
| 权限 | 执行者角色 ≥ 审批人；**同样受职责分离约束** |
| 目标版本 | 调用方必须**显式传入**要回滚到的目标版本号，守卫校验它确实存在于该应用的历史版本中（`guard_target_version_confirmed`）。不传 = 拒绝。这是为了防止"一键回滚"在事后场景下静默回滚到一个比当前更旧、甚至从未上线过的版本 |
| 拿锁失败 | **当场返回冲突错误并指名占用者，不写入 `tl_ticket_queue`**，见下 |
| 审计 | 写 `POST_HOC_ROLLBACK` 事件，payload 记明原 `SUCCESS` 时间与本次发起时间（间隔是复盘的关键数据） |

> **为什么事后回滚不入队**（与边 7 的 `APPROVED` 处理方式不同）：
>
> 边 7 排队的理由是**没有人在场**——审批通过后系统自动推进，锁被占时必须有个地方等着，否则就是 D-2 那次的静默卡死。事后回滚相反，**是人在场的一次性动作**：抢不到锁就当场告诉他"该应用/环境正被 `TL-20260921-003` 占用"，他可以等，也可以先去处理那一单。
>
> 把人工动作塞进自动队列反而制造一种新的静默失败——操作者以为回滚已经在路上，实际它排在某个看不见的队列里。**同一个教训不需要踩第二次。**
>
> 这个选择还有个附带好处：`dispatch_queued`（`M1-10b`）不必泛化成"按意图晋升到不同目标态"，队列语义保持单一（只服务 `APPROVED → EXECUTING`）。

#### 边 31 的完整约束（`TRIGGER_BUILD_LOST`，补录）

这条边从初版起就被 §3.6 的代码片段与正文两处引用，却**从未登记进本表**——违反本表自己的规则"任何新增边必须先改这张表"。若拖到开工后发现，写 sweeper 的人要实现一个表里不存在的触发器，而 `M1-09` 的"表↔代码逐行一致"断言必然失败。第四轮开工前盘查发现并补录；**语义完全收编 §3.6 既有文字，不引入任何新设计**。编号按登记顺序取 31，不插号重排（边 29/30 同理——编号是 ID，不是序）。

| 约束 | 内容 |
|---|---|
| 判定权 | 唯一判定者是 sweeper 的 `GET /queue/item/{id}/api/json`：`cancelled` 或队列项消失（404）才走本边。**平台绝不能自己猜 `build_no`**（§3.6 原文——队列语义下猜错就把别人的构建当成自己的） |
| 落位 | `PAUSED`，`payload.reason_class=INFRA`——沿用边 10–14 家族惯例：`EXECUTING` 侧的系统类失败一律落 `PAUSED`，把"还救不救"留给人 |
| 恢复出口 | 走边 24，`paused_from_state=EXECUTING`；但恢复动作是**重新触发构建**而非重新代提交——清单已在仓库里，重放提交必被 §4.2 幂等守卫去重。恢复 effect 直指 `JenkinsProvider.trigger`，不走代提交路径 |
| 终止兜底 | 从本边进 `PAUSED` 的通道 A 单若被边 25 终止（`M2-34`），Jenkins 侧构建可能仍以 `PENDING` 挂在队列或已消失——`abort` 为 best-effort，失败不阻塞终止 |
| 审计 | 写 `BUILD_LOST` 事件，payload 记 `queue_id`、判定依据（队列项 `cancelled` / 404）、从 `trigger` 到判定的耗时（队列积压的信号） |

> **`PAUSED` 必须记住来源态。** 边 24 的目标是"来源态"而非某个固定状态：从 `EXECUTING` 暂停的单据恢复后必须回到 `EXECUTING`，从 `VERIFYING` 暂停的必须回到 `VERIFYING`。这是当前 14 态设计里唯一一处"状态不可自解释"的地方——`PAUSED` 本身不携带阶段信息，否则会出现"一个只跑到一半的发布被恢复到验证阶段"这种幽灵流转。
>
> **落地方式：`Ticket.paused_from_state` 字段**（§1.2），由 `apply()` 在写 `PAUSED` 的同一事务内赋值；事件 payload 里也带一份 `from_state` 供审计回放。**以字段为准，不以事件 payload 为准**——`guard_pause_resume_same_phase` 要在已加锁的票据行上做判断，为此去扫事件表既慢又给"事件表被绕过"留了后门。两者不一致时以字段为准**并告警**（说明有人绕过 `apply()` 写了事件）。
>
> 另一个选项是把 `PAUSED` 拆成 `PAUSED_EXEC` / `PAUSED_VERIFY` 两态——但那会让状态机变成 15 态，且 UI 上要区分两个"已暂停"。**当前按字段方案落地**（对应 `M2-27`）。

### 1.4 统一迁移入口

**这是唯一允许修改 `Ticket.state` 的函数。** 任何绕过它的状态写入都视为缺陷。

```python
# apps/tickets/services/transition.py
def apply(ticket_id: int, trigger: str, *, actor, payload: dict,
          idempotency_key: str, lease: Lease | None = None) -> TicketEvent:
    """
    1. SELECT ... FOR UPDATE on tl_ticket      (行锁 = 单据级串行化)
    2. 幂等短路: TicketEvent.objects.filter(idempotency_key=...).first()
                 命中则直接返回，不产生第二次副作用
    3. 校验 TRANSITIONS[trigger]，src 必须等于 ticket.state
    4. 跑 guards，任一不过抛 TransitionDenied(reason_code)
    5. UPDATE tl_ticket SET state=%s, state_entered_at=now(), row_version=row_version+1
         WHERE id=%s AND state=%s AND row_version=%s     <- 乐观锁兜底
    6. INSERT tl_ticket_event (seq = MAX(seq)+1，唯一约束兜底)
    7. 调度 effects —— 仅注册 Celery 任务，绝不在本事务内做 I/O
    全程一个 transaction.atomic()
    """
```

**Effects 只负责 `.delay()` / `apply_async(eta=...)`，不得在事务内发起 HTTP 或 SSH。**

必须用 `transaction.on_commit` 包装任务投递。否则 Celery worker 可能在事务提交前就取走任务，读到旧状态——表现为"任务执行了，但单据状态还没变"，排查时极具迷惑性。

### 1.5 并发锁：四层，职责不同（不是四选一）

| 层 | 机制 | 职责 | 失效时 |
|---|---|---|---|
| L1 互斥原语 | `pg_advisory_xact_lock(hashtext('app:{app_id}:env:{env_id}'))` | 拿锁动作本身原子化，**事务结束自动释放** | 进程崩溃 → 连接断开 → PG 自动释放，不会死锁 |
| L2 锁记录 | `tl_app_env_lock` 行 + `fencing_token`（单调递增） | 谁持锁、何时到期，可查询、可审计、可被 sweeper 回收 | 崩溃后行仍在 → 由 watchdog 按租约回收 |
| L3 物理不变量 | **部分唯一索引** | 即使有人绕过 ORM（admin / 手工 SQL / 数据订正）也无法产生两个活动单 | **永不失效** |
| L4 快速预检 | Redis `tl:aenv:{app}:{env}:busy` (TTL) | 前端提前告知"该应用环境正忙，将排队"，避免请求打到 DB 再等锁 | 可丢，仅体验优化 |

**为什么 Redis 绝不能作为锁的权威**：TTL 型锁在 ansible 长作业（30 分钟级）场景下必然过期，第二个单子会在第一个还在跑时开跑；Redis 主从切换时锁会丢。若坚持用 Redis，必须带 fencing token——但 PostgreSQL 已能提供，没必要增加组件。

L3 的 DDL（谓词与 §1.1 的语义冻结一致）：

```sql
CREATE UNIQUE INDEX uniq_active_ticket_per_app_env
ON tl_ticket (app_id, env_id)
WHERE state IN ('EXECUTING','VERIFYING','CONFIRMING','VERIFICATION_ERROR',
                'PAUSED','ROLLBACK')
  AND deleted_at IS NULL;

CREATE UNIQUE INDEX uniq_bypass_commit ON tl_ticket (source_commit_sha)
WHERE trigger = 'BYPASS' AND source_commit_sha IS NOT NULL;

CREATE UNIQUE INDEX uniq_event_idem ON tl_ticket_event (idempotency_key);
```

#### 1.5.1 加锁顺序：硬规则

**任何路径都必须先取 `AppEnvLock`，再取 `ConcurrencySlot`。禁止反向。**

这不是风格问题，是死锁与否的分界线。§5.6 曾把"ansible 占满槽位 → 验证拿不到槽位"论证成死锁，但按此顺序它**不构成环**：持有执行槽位的作业一定已经持有自己的 `AppEnvLock` 且正在运行，它们会自然结束，最坏是**饥饿**而非死锁。

反过来说，**一旦某条路径先取槽位再等锁，环就真的闭上了**：作业 A 持槽位等 app/env 锁，而该锁被一个 `VERIFYING` 的单据持有，它又在等验证槽位。因此这条顺序必须写进代码并有测试兜住——`M1-18` 的交付里包含一条断言：**抓到任何"先 slot 后 lock"的调用即失败**。

而 §5.6 的验证配额仍然要留，但它的理由是**防止验证被长作业饿死**（一个 30 分钟的 ansible 作业可以把验证堵到 `deadline_at` 超时，直接把单据推进 `VERIFICATION_ERROR`），不是防死锁。理由写错的后果是：以后有人"证明"配额没必要，就把它删了。

#### 1.5.2 fencing token 在哪里校验

`design-notes.md` 此前只在 L2 表格里提到 `fencing_token` 单调递增，却**从未说明它在哪里被校验**——若无人校验，它就是装饰品，§1.6 的锁回收会留下一个真实缺口：

> watchdog 判定租约失联 → 回收锁 → 单据转 `PAUSED`。但原持有者（进程只是卡住、并未死）随后醒过来，继续按自己手里的 `lease` 推进——**它会写状态、发 HTTP，而这些副作用无人认领**。

因此 `apply()` 的 `lease` 参数必须有确定语义（`design-notes.md:221` 的签名里有这个参数，7 步流程却从未用到它）：

1. 传入 `lease` 时，`apply()` 在 `SELECT ... FOR UPDATE` 之后、跑 guards 之前，**校验 `lease.fencing_token == tl_app_env_lock.fencing_token`**，不等则抛 `StaleLease`。
2. `requires_lock=True` 的转移**必须**传 `lease`，传 `None` 视为缺陷（用一条测试穷举断言）。
3. `StaleLease` 不是异常路径而是正常路径：捕获后**静默退出**（副作用已被新持有者接管），并埋点计数。这个计数持续增长说明 lease TTL 配得太短。

### 1.6 进程崩溃时锁怎么办

1. **`pg_advisory_xact_lock` 在连接断开时由 PG 自动释放** → 不存在永久死锁。
   **必须用事务级 `pg_advisory_xact_lock`，不是会话级 `pg_advisory_lock`。** 若生产走 pgbouncer transaction 模式，会话级 advisory lock 会串到别的连接上——这是极难排查的偶发故障。

2. L2 的租约：执行方（Celery 任务）每 10s 续租 `lease_expires_at`（**租期 30s**），同时刷新 Redis 键 `tl:lease:{ticket_id}`（**TTL 30s**）。

   两个常量此前未定义，现固定为 `LEASE_TTL=30s` / `HEARTBEAT_INTERVAL=10s` / `WATCHDOG_INTERVAL=30s`（settings 可覆盖）。**必须满足 `HEARTBEAT_INTERVAL * 2 ≤ LEASE_TTL ≤ WATCHDOG_INTERVAL * 2`**：低于则网络抖动即误回收，高于则崩溃后最坏 `LEASE_TTL + WATCHDOG_INTERVAL` 才被接管。启动时做一次断言，配置写错直接起不来，而不是在生产上表现为"偶尔锁不回收"。

3. `tickets.tasks.watchdog_leases`（beat 每 30s）：扫持有态且 `lease_expires_at < now()` 的单子，**先查 Redis 心跳键是否存在**——只比时间戳会被 worker 与 DB 的时钟偏移坑死——键不存在才判定失联。

   处理：写 `LEASE_LOST` 事件 → 转 `PAUSED`。**绝不自动回到 `EXECUTING`**：外部副作用可能已经发生（代码已提交、机器已变更），必须人工对账。

4. 补偿：失联单对应的 ansible 进程由 §2.6 的进程组清理处理；Jenkins 侧由 §3.6 的 sweeper 兜底。

### 1.6a 排队与晋升：`APPROVED` 单如何进入 `EXECUTING`（D-2 的直接后果）

D-2 定下"`APPROVED` 不持锁"之后，审批通过**不再等于**马上执行：审批时若该 `应用+环境` 的锁被占，单据停在 `APPROVED`，位置写进 `tl_ticket_queue`。于是必须有东西把队首的单子推上去——这个"东西"是 `tickets.tasks.dispatch_queued`（beat 每 10s）。

1. **晋升走同一个入口**：`apply(ticket_id, "start", ...)`，与人工点"立即执行"是同一条迁移边，因此同样受 `guard_holds_lock` 与部分唯一索引约束。**不允许 dispatcher 自己写 `Ticket.state`**。
2. **每个 `(app, env)` 每 tick 最多晋升一张**：同一 tick 内并发晋升虽能被唯一索引兜住，但会产生大量无谓异常，污染告警。
3. **按 `(priority, seq)` FIFO**：允许紧急单插队，但**不允许无限插队**，否则低优先级单饿死。`enqueued_at` 供 UI 展示"第 N 位"与预计等待。
4. **晋升失败不重试**：抢锁失败即静默退出，等下一个 tick。**不要 `self.retry()`**，理由同 §2.2。
5. **锁释放时主动触发**：单据离开持有态时在 `transaction.on_commit` 内向 `notify` 队列投 `dispatch_queued` 信号，把最坏等待从 10s 降到近实时。**beat 必须同时保留**。

**会炸的点**：只做事件驱动、不做 beat 兜底，一次 Redis 抖动或 worker 重启就会让某张单**永远停在 `APPROVED`**，且没有任何报错——看板上它只是"待执行"。这类"安静地卡住"比报错难查得多。

### 1.7 事件表 append-only 的三种强制手段

三者叠加，因为任一层单独都可能被绕过：

1. **DB 权限（唯一真手段）**：应用角色对 `tl_ticket_event` 只有 `INSERT, SELECT`；`REVOKE UPDATE, DELETE`；迁移用另一个 DDL 角色。
   **本地开发若用超级用户连接，该约束不起作用**——因此必须加一条集成测试断言 `has_table_privilege('tl_ticket_event','UPDATE') = false`。
2. **触发器**：`BEFORE UPDATE OR DELETE ON tl_ticket_event` 直接 `RAISE EXCEPTION`。防的是权限被误授的场景。
3. **哈希链**：`hash = sha256(prev_hash || seq || from_state || to_state || canonical_json(payload) || created_at)`。每日任务把每单链头快照落 `tl_event_anchor`，满足 NFR-04 的"数据不可篡改"可验证性。

### 1.8 生产会炸的点

**① "先读后写"的丢失更新。**

```python
if t.state == APPROVED:      # 两个运维同时点审批
    t.state = EXECUTING      # READ COMMITTED 下两者都读到 APPROVED
    t.save()                 # 都通过 → 两个活动单
```

必须 `SELECT FOR UPDATE`，并叠加 `UPDATE ... WHERE state=%s AND row_version=%s` 的乐观锁作为第二道。

**② 状态写入与事件写入不在同一事务。** 单据显示 SUCCESS 但事件表无记录（或反之），时间线回放出现断层，NFR-04 直接不达标。

**③ 在事务里做 I/O。** `with transaction.atomic(): gitlab.create_commit(...)` —— GitLab 超时 10s 意味着行锁被持有 10s，`AppEnvLock` 与单据行双重堵塞。更糟的是 HTTP 成功而事务回滚 → **代码提交了、单据没动，产生"幽灵发布"**。

**④ 部分唯一索引漏了 `deleted_at`。** 软删的活动单仍占用唯一槽位，导致该应用环境**永久无法发布**。

---

## §2 通道 B：ansible-runner 逐行事件流

### 2.1 模型（app: `channels_b`）

```python
class AnsibleRun(TimeStampedModel):      # tl_ansible_run
    ticket = OneToOneField(Ticket, related_name="ansible_run")
    playbook_repo, playbook_branch, playbook_path
    resolved_commit_sha                  # shallow pull 后解析出的真实 sha（可复现的锚点）
    inventory_snapshot = JSONField()     # 发起前落库（FR-CHB-06）
    extra_vars = JSONField(); limit; forks; timeout_seconds
    status            # PENDING|RUNNING|SUCCESS|FAILED|TIMEOUT|ORPHANED|KILLED
    runner_ident, artifact_dir, runner_pid, runner_pgid
    stats = JSONField()                  # ok/changed/failures/unreachable/skipped
    started_at, finished_at, last_event_at, stall_seconds
    class Meta:
        unique_together = [("ticket",)]   # 一单一次执行（重试走新 Ticket）

class AnsibleTaskResult(TimeStampedModel):    # tl_ansible_task
    run, host_ref = FK(cmdb_host, null=True), host_name, host_ip
    task_uuid, task_name, role, item_idx
    result_status   # OK|CHANGED|FAILED|UNREACHABLE|SKIPPED|RESCUED
                    # RESCUED 无法从单条事件判出（S0-1 F-3），按 §2.3"终态计数回填"规则落值
    changed: bool; duration_ms; started_at, finished_at
                    # duration_ms：item 行为 NULL（item 事件 payload 无 duration，S0-1 F-5）
    stdout_summary, stderr_summary, output_ref   # 全文落 artifact 文件
    class Meta:
        unique_together = [("run","host_name","task_uuid","item_idx")]
```

### 2.2 并发闸门用 DB 信号量，不用 Celery 并发数

Celery 的并发数重启即失效、不可观测、无法按应用维度限制。改用 DB 信号量表：

```python
# apps/channels_b/services/slots.py
class ConcurrencySlot(models.Model):     # tl_concurrency_slot，固定 N 行，seed 迁移预置
    scope        # "global" | f"app:{app_id}"
    slot_no; holder_run; acquired_at; lease_expires_at
    class Meta:
        unique_together = [("scope","slot_no")]
```

**槽位行必须按需创建，不能只靠 seed 迁移。** `scope = f"app:{app_id}"` 而应用是 CMDB 运行期创建的——若只在迁移里 seed，**新建的应用一行槽位都没有，`FOR UPDATE SKIP LOCKED` 取不到任何东西，该应用的作业会永远排不出去且不报错**。落地方式：应用创建时用 signal 预置 + 取用时 `INSERT ... ON CONFLICT DO NOTHING` 兜底（迁移只 seed `global` 与 `verify` 两个固定命名空间）。

**两个 scope 是"各取一行"，不是"任取一行"。** 原 SQL `WHERE scope IN (global, app:N) ... LIMIT 1` 只取一行，等于"全局 + 单应用"双限制根本没生效——同一应用照样能并发跑满全局槽位。正确语义是**全有或全无**：

```sql
-- 事务内，按 §1.5.1 的顺序：此处必然已持有 AppEnvLock
-- 第一次：全局
SELECT ... FROM tl_concurrency_slot
WHERE scope = 'global' AND (holder_run IS NULL OR lease_expires_at < now())
FOR UPDATE SKIP LOCKED LIMIT 1;
-- 第二次：本应用
SELECT ... FROM tl_concurrency_slot
WHERE scope = %s AND (holder_run IS NULL OR lease_expires_at < now())
FOR UPDATE SKIP LOCKED LIMIT 1;
-- 任一次取不到 → 回滚整个事务（释放已取到的那个），写回 QUEUED 等下一轮
```

`SKIP LOCKED` 让排队不互相阻塞。**"取到一半就返回"是必须避免的反模式**——那会造成全局槽位被占、应用槽位没占的中间态。

未拿到槽位 → **写回 `QUEUED` 状态由 beat 重扫**，不要用 `self.retry()`：retry 会占满 worker 的预取位，把队列堵死。

**`ConcurrencySlot` 的全局槽位数与 `exec.runner` 的 Celery 并发数必须一起改。** §0.5 把 `exec.runner` 的并发数写成"等于全局并发上限（默认 3）"，但两者是**独立的两个闸门**：槽位是权威闸门，Celery 并发数若小于槽位数，worker 先被占满，槽位表上看起来还有空位却没人来取（表现为"并发度上不去"，且极难归因）。落地上让 **Celery 并发数 = 槽位数 + 1**（留一个给即将释放的任务），并在启动时断言二者关系。

### 2.3 事件泵 → Channels 桥接

**架构决定：runner 进程永不直接写 PG。Redis 是唯一扇出总线，由一个 Persister 线程统一落库 + 推 group。**

```
ansible-runner (子进程, 输出 JSON event)
   │  event_handler=cb  (生产定死 run_async()：回调发生在 runner 的事件读取线程；
   │                     S0-1 实据：同步 run() 下回调在调用线程本身——见 F-1)
   ▼
EventPump._on_event(event)                      [apps/channels_b/runner/pump.py]
   │  归一化为 RunEvent(seq, kind, host, task, status, changed, duration_ms, stdout, stderr)
   │    seq←payload.counter(全局单调)  duration_ms←event_data.duration×1000(item 行 NULL, F-5)
   │    stderr 列：事件 payload 无独立字段，从 res.msg/exception + stdout(剥 ANSI) 提取 (F-4)
   │  白名单 kind（v0.6 按 S0-1 实据增补末族）：playbook_on_task_start / runner_on_start /
   │  runner_on_ok / runner_on_failed / runner_on_unreachable / runner_on_skipped /
   │  playbook_on_stats / runner_item_on_ok / runner_item_on_failed|skipped|unreachable
   │  （item 族承载全部 loop 逐项结果，漏掉则 AnsibleTaskResult 的 item_idx 行永远写不出，
   │    item_on_failed/skipped/unreachable 三成员 S0-1 未触发，S0-2 故障注入补验）
   ├─▶ Redis LPUSH tl:run:{run_id}:events  (+ LTRIM 0 5000)   ← 重连补发用
   └─▶ Redis PUBLISH tl:run:{run_id}:bus                      ← 实时扇出
                    │
                    ▼
        RunEventPersister (独立线程, 独立 DB 连接)
          ├─ 批量 upsert AnsibleTaskResult
          │    bulk_create(update_conflicts=True,
          │                unique_fields=["run","host_name","task_uuid","item_idx"])
          ├─ 每 500ms / 200 事件 / 终态事件 触发 flush，单事务批量写
          └─ async_to_sync(channel_layer.group_send)("tl.run.{uuid}", ...)
                    │
                    ▼
        RunLogConsumer (AsyncJsonWebsocketConsumer) → 浏览器
```

实现要点：

- **不允许在 `event_handler` 里同步 INSERT。** 200 主机 × 100 task ≈ 20000 事件，逐条 INSERT 会反向阻塞 ansible-runner 的事件读取线程，进而卡住 ansible 本身的输出管道——形成正反馈。
- Django 连接是线程局部的：Persister 线程必须自己 `close_old_connections()`，且**不能是 daemon 线程**（否则主任务结束时正在 flush 的批次被丢）。终态事件必须同步 flush 并 `join(timeout=5)`。
- group 名 `tl.run.{uuid}`：Channels 组名限 ASCII 字母数字与 `-_.`、长度 ≤100。用 int id 拼容易踩长度/字符问题。
- **权限门禁在 `connect()`。** Channels group 无访问控制，任何人猜到组名即可订阅。`connect` 内必须校验用户对 ticket 的可见性——否则任何人都能看生产发布日志。

**RESCUED 的终态计数回填（2026-09-23 拍板，方案 ③′；S0-1 实据 F-3）**

S0-1 spike 证明：被 `rescue` 挽回的 `runner_on_failed` 事件**不带任何事件级标志**（`ignore_errors=False`、无 `rescued` 字段，`samples/main_runner_on_failed.json`），单条事件无法与致命失败区分。但实据同时给出两个可靠锚点：① ansible 语义下**致命失败会立即把该主机踢出当 play**，即某主机的致命失败必然是它在该 play 的最后一条 failed；② `playbook_on_stats` 的 `failures` 计数**不包含**被 rescue 的失败尝试（`run_meta.json.main` + `events_stream.jsonl:L41`：`failures={}, rescued={"localhost":1}`）。落库规则：

```
终态（playbook_on_stats）时，对每台 host：
  n = 该 host 无 ignore_errors 标志的 runner_on_failed 条数
  r = stats.rescued[host]；f = stats.failures[host]
  n == r + f  →  按任务顺序，最后 f 条判 FAILED，先前 r 条判 RESCUED
  n != r + f  →  不做任何推断：全部判 FAILED，并写 rescued 对账告警事件
```

对账不等即"结果未知"，**必须显式表达**（同本节失败策略表末列的立场），禁止靠启发式硬猜成功。

**S0-1 spike 实测修订（v0.6，F-1 ~ F-5，全文见 `docs/spikes/s0-1/report.md` 附D）**

| # | 原假设 | 实据结论 | 已落入 |
|---|---|---|---|
| F-1 | "event_handler 在独立线程回调" | 同步 `run()` 在调用线程回调（41/41、5006/5006 全 `MainThread`）；架构结论不变，生产定死 `run_async()` | 上图回调行 |
| F-2 | 白名单 7 kind 够用 | loop 逐项结果**全部**走白名单外的 `runner_item_on_ok`（压测 5000/5000） | 上图白名单行 |
| F-3 | RESCUED 可由事件流判出 | 事件级无标志，**证伪** → 终态计数回填方案 ③′ | 上两块 |
| F-4 | RunEvent 有 stderr | payload 无独立字段，错误文本混在 stdout(ANSI) 与 `res.msg/exception` | 上图归一化行 |
| F-5 | item 行有 duration | item 事件 `event_data.duration=None` | §2.1 模型注 + 上图 |

补充实据（非假设修订）：事件↔`job_events/<counter>-<uuid>.json` 严格 1:1、counter 可作 `seq`；事件级自带 `stdout` 全文（§2.4① 懒加载成立且强于假设）；速率实测 ≈550 ev/s 无反压（Q7）；`cancel_callback` 正常跑完亦被调用 2~3 次（周期轮询，**不能以被调用当取消**，S0-2 起取消标志必须显式置位）。

**泵与落库的失败策略（此前未定义，是"日志缺一段但单据显示成功"的来源）**：

| 失败点 | 策略 |
|---|---|
| Redis 不可达（`LPUSH`/`PUBLISH` 抛异常） | **绝不阻塞 runner，绝不抛回 `event_handler`**。丢弃该事件、计数器 `tl_event_pump_dropped_total` 自增、每 100 条打一条告警日志。**宁可丢日志也不能卡住 ansible**——卡住 runner 会让发布本身失败，代价大得多 |
| Persister 批量写 PG 失败（死锁、连接断） | 重试 3 次（指数退避），仍失败则**整批落盘到 `artifact_dir/failed_batches/*.json`** 并告警。**不允许静默丢弃**：这批数据是 `AnsibleTaskResult` 的唯一来源，丢了就永远补不回来 |
| 终态事件的 `join(timeout=5)` 超时 | **不视为成功**。写 `RUN_EVENT_FLUSH_TIMEOUT` 事件、告警，并把该 run 的 `status` 置为 `ORPHANED` 而非 `SUCCESS`——**"结果未知"必须显式表达，不能默认成成功**（同 §2.7① 的理由） |
| `LTRIM 0 5000` 追尾 | 前端补发时必须显式报缺口，见 §2.4③ |

**这四条的共同点**：都没有"重试到成功"这个选项，因为上游是 ansible 的实时输出管道——**唯一不能做的就是反压回去**。所以策略统一是"降级 + 显式留痕 + 告警"，而不是保证不丢。

### 2.4 事件洪水的三层治理

不做这三层，200 主机场景下 WebSocket 必崩：

1. **分层**：WS 只承载"task × host 状态迁移"这类结构化事件；`runner_on_ok` 的完整 stdout 走 artifact 文件（`artifact_dir/job_events/*.json`），UI 点击某行时用 `GET /tickets/{id}/logs?task_uuid=...&offset=` 懒加载。
2. **合并**：Consumer 侧 `LogCoalescer` 缓冲 50 行 / 100ms 合成一帧（`{"type":"batch","items":[...]}`）。逐行 `send()` 会打爆 WebSocket，并顺带打爆 daphne 的 CPU。
3. **限流 + 丢弃策略**：每 run `max_lines_per_second`（默认 200），超出后只发 `{"dropped": N, "last_seq": M}` 计数帧。Redis list 有 5000 条上限，前端重连时带 `last_seq` 请求补发（`GET /runs/{id}/events?since=`）。

   **但环形缓冲会被追尾，此时"补发"是假的。** 客户端离线超过 5000 条事件后，它要的 `since` 已被 `LTRIM` 裁掉，服务端只能返回手上还留着的——**前端会静默显示一段有洞的日志，用户以为看到了全部**。这与"保证不瞎"正相反，且因为日志看起来连续，没有任何人会去怀疑它。

   正确做法：服务端在 `since < 缓冲区最老 seq` 时，返回 `{"resync": true, "oldest_seq": N, "items": [...]}`，**前端必须据此打上"此处有 N 行日志缺失"的显式标记**，而不是把缺口接上。同理，§2.4① 的 artifact 文件懒加载路径不存在这个问题（文件是全量），缺口只出现在 WS 补发路径上——**这也是"分层"策略的额外收益：即使 WS 补发有洞，用户点开某行的全文仍然完整**。

### 2.5 Inventory 由 CMDB 动态生成

```python
# apps/cmdb/services/inventory.py
def build_inventory(app, env, *, extra_host_ids=None) -> tuple[dict, InventorySnapshot]:
```

- 用 **dict 形式**传给 `ansible_runner.run_async(inventory=inv_dict)`，不落临时文件——避免并发写同名文件的竞态与清理问题。
- `InventorySnapshot`（含每台 host 的 name / ip / `updated_at` / groups / vars）在 run 创建时**先落库**，再执行。`updated_at` 必须记录，否则 §2.7 的 staleness 判断无从下手。
- 结构：`all.children.{app}_{env}.hosts`；`vars` 注入 `ansible_ssh_common_args`（含 `-o BatchMode=yes -o StrictHostKeyChecking=yes`）、`ansible_python_interpreter`。
- **`ANSIBLE_HOST_KEY_CHECKING` 必须为 False 或预置 known_hosts。** 否则首次连接停在交互式确认，表现为"卡在 0% 直到超时"——经典故障，排查成本极高。

### 2.6 超时控制：三层缺一不可

| 层 | 机制 | 覆盖 |
|---|---|---|
| L1 整体 | `run_async(timeout=run.timeout_seconds)` | playbook 总时长 |
| L2 卡死 | `channels_b.tasks.watchdog_run(run_id)`，独立任务 `apply_async(countdown=timeout)` | `last_event_at` 超过 `stall_seconds`（默认 300）判定挂起 |
| L3 进程 | `os.killpg(os.getpgid(run.runner_pgid), SIGTERM)` → 10s 后 `SIGKILL` | ansible-runner → ansible-playbook → ssh 三层子进程 |

**杀进程必须杀进程组。** 只 kill 父进程会留下孤儿 `ssh`：SSH 连接不释放，目标机上的 `Connection` 与 `ControlMaster` 残留，下一单因连接数打满而随机失败。推荐用 `start_new_session=True` 起 runner，或外包 `systemd-run --scope` 让 systemd 负责回收。

### 2.7 生产会炸的点

**① worker 崩溃留下孤儿 playbook + 任务重投导致双重执行。**

`task_acks_late=True` + `task_reject_on_worker_lost=True` 会重投，但重投时剧本可能还在跑。

必须：任务开头先按 `AnsibleRun.runner_ident` 与 `runner_pid` 做认领检查——`os.kill(pid, 0)` / Redis 心跳存活则 **adopt 而非重跑**，否则标记 `ORPHANED` 并转 `PAUSED` 等人工对账。

**绝不允许自动重跑一次可能已成功的生产发布。**

**② 事件洪水**（见 §2.4）。裸实现 `for line in runner.events: ws.send(line)` 在 200 主机 play 下会把 WS 与 DB 同时打死，且因逐条 INSERT 反过来拖慢 ansible 输出，形成正反馈。

**③ Inventory 漂移**（CMDB 中已不存在或已改 IP 的主机）：

- 执行前跑 `preflight_check`（`ansible -m ping`），把不可达主机从 inventory 中剔除并记入 `snapshot['removed']`。
- 若剔除后数量 < `cmdb_env.min_hosts`（默认 1）→ **中止整单**，不进 EXECUTING。
  **部分主机发布造成的版本偏斜（version skew）比发布失败严重得多**——失败可以重试，偏斜会静默地让集群处于不一致状态。
- 若 `host.updated_at > run.created_at` → 标 `STALE_INVENTORY`，要求重新审批。

**④ artifact 目录磁盘打满。** `-vvv` 下 200 主机单次可达 GB 级。需 `max_log_bytes` 截断 + 保留策略（默认 90 天）+ 磁盘水位告警。

---

## §3 Jenkins 回调接收器 + 对账 sweeper

### 3.1 模型（app: `channels_a`）

```python
class PipelineRun(TimeStampedModel):     # tl_pipeline_run
    ticket = ForeignKey(Ticket, related_name="pipeline_runs")
    commit_sha; jenkins_job; build_no; queue_id
    result          # RUNNING|SUCCESS|FAILURE|ABORTED|UNSTABLE|NOT_FOUND
    stage_timeline = JSONField()
    confirmed_at; confirmed_source   # CALLBACK | SWEEPER —— 审计"谁先到"
    last_polled_at; poll_failures; next_poll_at
    console_log_path; started_at; finished_at
    class Meta:
        unique_together = [("jenkins_job","build_no"), ("ticket","commit_sha")]
```

> **`commit_sha` 的唯一性是 `(ticket, commit_sha)` 复合的，不是单列的。**
> 本文档 §4.3 此前写作 `unique(commit_sha)`，**与本处冲突**。以本处为准，理由有两条：
> ① 回滚单会**复用同一个 `commit_sha`**（回滚到旧版本），单列唯一会让回滚单根本建不出来；
> ② §3.4 归属解析规则 3 正是按 `commit_sha` 查 `PipelineRun`，若它全局唯一，就与规则 4 的"歧义即 `ORPHAN`"自相矛盾——一个永远不可能歧义的查询没有存在的必要。
>
> 相应地，§3.4 规则 3 的判定补一句：**命中 >1 条且分属不同 ticket 才算歧义；同一 ticket 内命中多条按 `build_no` 最大者取**（同一单据的重试会产生多条 `PipelineRun`，这是正常的）。

```python
class PipelineStage(TimeStampedModel):   # tl_pipeline_stage
    pipeline_run, stage_name, event, status, duration_ms, started_at
    class Meta:
        unique_together = [("pipeline_run","stage_name","event")]

class CallbackLog(models.Model):         # tl_callback_log
    source        # GITLAB | JENKINS
    dedup_key; payload = JSONField()
    received_at; processed_at
    process_result   # OK | ORPHAN | ENQUEUE_FAILED | ERROR
    error
    ticket = FK(null=True)
    class Meta:
        unique_together = [("source","dedup_key")]
```

> **`signature_ok` 字段已删除**（§3.3 已定为"验签失败不落库"）。留着它只会让人以为坏签名也在这里存档，从而不再去查那个真正的计数器。

### 3.2 去重键：区分"投递去重"与"状态幂等"

**这是本章的核心区分。混为一谈必然出错。**

| 问题 | 机制 | 键 |
|---|---|---|
| 「这次投递我处理过了吗」 | `tl_callback_log` 唯一约束 | `dedup_key = f"{job_full_name}\|{build_no}\|{event}\|{stage_name or ''}"` |
| 「即使处理两次，单据也只能迁移一次」 | `tl_ticket_event.idempotency_key` 唯一约束 + FSM 的 `src == state` 校验 | `f"jenkins:{dedup_key}:{build_result or ''}"` |

只做前者：sweeper 与 callback 通过**不同数据源**（回调 payload vs REST 查询）得出同一结论时，dedup_key 相同 → 第二次被吞，看似没问题；但若 sweeper 先写入再被吞，`PipelineRun.confirmed_at` 可能没被置位，导致 sweeper 每分钟重复轮询。

只做后者：重复投递每次都要走一遍 FSM，浪费且日志噪音大。

**两者都要。**

### 3.3 接收器：快返回 + 异步处理

`POST /api/v1/callbacks/jenkins`（用裸 `APIView`，`request.body` 取原始字节）：

```python
def post(self, request):
    raw = request.body                      # 必须在 DRF 解析前拿原始字节
    # 1. 验签：hmac.compare_digest(sha256(secret, f"{ts}.{raw}"), header)
    #    + 时间窗 |now - ts| <= 300s（防重放）+ per-job secret
    # 2. 单条 INSERT INTO tl_callback_log (source='JENKINS', dedup_key=...)
    #      except IntegrityError -> return 200 {"dedup": true}
    # 3. channels_a.tasks.process_jenkins_callback.delay(log.id)
    # 4. return 200
```

细节与坑：

- **签名必须对原始字节计算。** 先 `json.loads` 再 `json.dumps` 重新序列化去验签，会因键序/空白差异失败——这是最常见的错误实现，且表现为"永远验签失败"，很容易被误判为密钥配错。
- **验签失败一律 401，且不落 `tl_callback_log`**（已拍板）。原因有二：① 鉴权失败与处理失败是两回事，"返 200 防重试风暴"的理由只对后者成立；② `tl_callback_log` 是无上限的纯 INSERT 表，**若对坏签名也落库，任何知道 URL 的人都能把它灌满**，这是一条不需要认证就能触发的磁盘耗尽路径。坏签名的审计走**应用日志 + 一个独立的 Prometheus 计数器** `jenkins_callback_signature_rejected_total`，不进业务表。
- **先落库再入队**，不能反过来：worker 可能比 HTTP 事务提交更快，读到不存在的行。
- **验签通过之后，第 2/3 步抛异常仍返回 200**（`process_result='ENQUEUE_FAILED'` 落库），避免 Jenkins 侧的重试风暴把接收器打挂。真正的重试由内部 `replay_unprocessed_callbacks`（扫 `processed_at IS NULL`）承担。**这条只覆盖处理链路，不覆盖鉴权**。
- 日志表是纯 INSERT 表，写入量小、无锁竞争——这是"快返回"的关键。
- 附带的一条清理规则：`tl_callback_log` 即使只收合法回调也会无限增长，**必须配保留策略**（默认 90 天，与 §3.7 的日志归档同一套策略）。

### 3.4 处理器：归属解析严禁靠猜

```python
# apps/channels_a/tasks/process_jenkins_callback.py
@shared_task(queue="callback.process")
def process_jenkins_callback(log_id):
    with atomic():
        log = CallbackLog.objects.select_for_update().get(pk=log_id)
        if log.processed_at:
            return                                  # 双 worker 保护
        run = resolve_pipeline_run(log)
        if run is None:
            log.process_result = "ORPHAN"; 告警; return   # 绝不猜、绝不建单
        if log.event == "stage_finished":
            upsert_pipeline_stage(run, log.payload)
        elif log.event == "build_finished":
            run.result = log.payload["result"]; run.save()
            confirm_build_via_api.delay(run.id)     # 终态二次确认
```

`resolve_pipeline_run` 的优先级（**顺序敏感，歧义必须中止**）：

1. 回调 payload 中显式 `TL-YYYYMMDD-NNN`（来自 `actions[parameters]` 的 `TICKET_CODE`，或 `displayName` / `cause` 的正则）
2. `PipelineRun(job, build_no)` 精确匹配
3. `PipelineRun(commit_sha)`（来自 `changeSet.items[].commitId`）
4. **命中 0 条或 >1 条 → `ORPHAN` + 告警，不建单不迁移**

### 3.5 收敛点：callback 与 sweeper 走同一个函数

这是"两条路径不双重迁移、不双写事件日志"的答案——**不靠约定，靠共享同一个加锁函数**：

```python
# apps/channels_a/services/jenkins_confirm.py
def confirm_and_transition(run_id: int, *, source: Literal["CALLBACK", "SWEEPER"]) -> None:
    with atomic():
        run = (PipelineRun.objects.select_for_update()
               .select_related("ticket").get(pk=run_id))

        if run.confirmed_at:                      # 已收敛，直接返回
            return

        api: BuildInfo = jenkins.get_build(run.jenkins_job, run.build_no)

        if api.result == "NOT_FOUND" and run.past_queue_grace():
            fsm.apply(run.ticket_id, "TRIGGER_BUILD_LOST",
                      idempotency_key=f"jl:{run.id}")
            run.confirm(source); return

        if api.result in (None, "RUNNING"):       # 仍在跑
            run.last_polled_at = db_now(); run.save()
            return

        run.result = api.result
        trigger = ("TRIGGER_BUILD_SUCCESS" if api.result == "SUCCESS"
                   else "TRIGGER_BUILD_FAILED")
        fsm.apply(run.ticket_id, trigger,
                  payload={"jenkins": api.raw},
                  idempotency_key=f"jenkins:{run.jenkins_job}:{run.build_no}:{api.result}")
        run.confirm(source)
```

**`source` 必须由调用方传入，不能在函数内推断。** `confirmed_source` 字段的**唯一存在理由**就是审计"谁先到"（见 §3.1），而原代码在成功路径写死 `"CALLBACK_OR_SWEEPER"`——恰好把这个信息丢掉了，还写出了一个不在取值集里的值。`PipelineRun.confirm(source)` 是一个小方法：`self.confirmed_at = db_now(); self.confirmed_source = source`。

**收敛性论证**：

- `select_for_update` 在 `PipelineRun` 行上把 callback 与 sweeper **串行化**。
- `confirmed_at` 是收敛标志，先到者置位，后到者第 3 行即返回。
- 即便 `confirmed_at` 因异常未置位而重复进入，`fsm.apply` 的 `idempotency_key` 唯一约束与 `src == state` 校验使第二次成为 no-op，且不会产生第二条事件。

因此「callback 说成功」与「sweeper 说成功」必然收敛到同一状态、同一条事件。

**代价与缓解**：`confirm_and_transition` 在持有行锁期间发起 HTTP。必须给 Jenkins client 设 `connect=3s, read=5s`，并设 `SET LOCAL lock_timeout='8s'`、`statement_timeout='15s'`，避免 Jenkins 慢响应把锁持有到数分钟。

### 3.6 Sweeper

```python
# apps/channels_a/tasks/sweep.py
@shared_task(queue="sweeper")
def sweep_executing_tickets():
    # 第一段：只取 ID，不加锁、不开长事务
    ids = list(Ticket.objects.filter(
               state="EXECUTING",
               state_entered_at__lt=db_now() - grace)
           .values_list("id", flat=True)[:50])

    # 第二段：逐单处理，锁的粒度是"一张单据"，不是"整个扫描"
    for tid in ids:
        run = PipelineRun.objects.filter(ticket_id=tid).order_by("-id").first()
        if run is None or db_now() < run.next_poll_at:
            continue
        confirm_and_transition(run.id, source="SWEEPER")
```

**这两段必须分开写，不能合成 `select_for_update(skip_locked=True)` 配一个 50 次的循环。** 后者有两个问题：① Django 的 `select_for_update()` 必须在打开的 transaction 内，裸用会直接 `TransactionManagementError`；② 补上 `atomic()` 包住整个循环后，会**一次持有 50 行单据锁跨越 50 次 Jenkins HTTP 调用**——这正是 §1.8③ 自己列为"生产会炸"的长事务，最坏 50 × `lock_timeout 8s`。锁的粒度应该与它保护的对象一致：`confirm_and_transition` 内部已经对单条 `PipelineRun` 加了 `select_for_update`，外层不需要再包一层。

- **`SKIP LOCKED` + `LIMIT 50`**：beat 每 1~2 分钟一次，多实例或上一轮未跑完时不会互相阻塞或重复处理。
- **熔断与退避**：`poll_failures` 连续 ≥5 次 REST 失败 → `next_poll_at = now() + exponential(60, 600) + jitter`，并上报告警。
  **无退避的 sweeper 在 Jenkins 宕机时会自我制造一次 DDoS 并拖垮平台。**
- **时间基准统一用 PG 的 `now()`**，不用 worker 的 Python 时钟。Jenkins 的 `timestamp` 是 epoch ms，只用它算 duration，**不解析人类可读的 `displayName`**（受 locale 影响）。
- 覆盖范围仅 `EXECUTING`：`VERIFYING` / `CONFIRMING` 的卡死单另有 §5.5 的 watchdog，**不要合并成一个巨型扫描**。
- 触发兜底：`queue_id` 存在但 `build_no` 一直不出现（队列积压或被取消）→ 通过 `GET /queue/item/{id}/api/json` 判定；`cancelled` 则走 `TRIGGER_BUILD_LOST`。
  **平台绝不能自己猜 build_no**（比如取 job 的 `nextBuildNumber`）——队列语义下猜错就把别人的构建当成自己的。

### 3.7 FR-CHA-08 日志归档

终态确认后 `channels_a.tasks.archive_console_log(run_id)`：拉 `/consoleText` **流式**（`httpx.stream`）写入 `var/logs/{ticket_code}/{build_no}.log`，PG 只存路径 + 尾部 200 行摘要。

**必须流式**：一个 500MB 的 consoleText 直接 `resp.text` 会把 worker 内存打爆。

### 3.8 Mock Jenkins 必须暴露的端点

| 端点 | 用途 | 必须支持的行为 |
|---|---|---|
| `GET /job/{job}/api/json` | job 概览 | `lastBuild`、`builds[]` |
| `GET /job/{job}/{n}/api/json?tree=result,building,duration,timestamp,actions[parameters]` | **终态判别与二次确认** | `building=true` 时 `result=null`（**关键**：区别于 `FAILURE`） |
| `POST /job/{job}/buildWithParameters` | 触发 | 201 + `Location: .../queue/item/{id}/` |
| `GET /queue/item/{id}/api/json` | 队列→build_no 映射 | 队列期 `executable=null`；支持 `cancelled=true` |
| `GET /job/{job}/{n}/consoleText` | 日志归档 | 分块返回、大响应 |
| `GET /job/{job}/{n}/wfapi/describe` | 阶段时间线 | stage 列表与 status |
| `POST /job/{job}/{n}/stop` | 中止 | — |
| `POST /_control/*` | 测试驱动 | 见 §6 |

**必须模拟的故障模式**（sweeper 存在的全部理由）：回调丢失（`callback_drop_rate`）、REST 5xx/超时、构建永久 hang（`building=true` 永不结束）、触发后无 build_no、签名错误、同一 build 回调重复投递 3 次。

此外必须能构造「**webhook 先于 API 响应到达**」的时序，用于 §4.4 的竞态测试。

### 3.9 生产会炸的点

**① 在回调里直接进终态。** `build_finished` 一到就把单据推 SUCCESS。回调 payload 可能来自一个过期或串号的构建（Jenkins 重试、队列复用），没有 REST 二次确认就会把一次失败发布标成成功。**FR-CHA-07 的"终态前 REST 二次确认"不是可选项。**

**② 接收器同步处理。** 验签 + 解析 + 状态迁移全在 HTTP 请求里完成。Jenkins 侧 `retry(3)` 叠加网络抖动时会并发打进来，加上 FSM 行锁 → 请求堆积 → Jenkins 侧超时重试 → 雪崩。必须 `INSERT 日志 + 入队 + 200`。

**③ 归属解析靠猜。** 用 job 名模糊匹配或"最近一条 EXECUTING 单"来归属回调。并行发布两个应用时必然串单——**把一个应用的构建结果写到另一个单据上**，这是最难发现、后果最严重的数据污染。

---

## §4 GitLab：代提交 + 幂等守卫 + Webhook 旁听

### 4.1 模型（app: `channels_a`）

```python
class ManifestSubmission(TimeStampedModel):    # tl_manifest_submission
    ticket = FK(Ticket, related_name="submissions")
    file_path        # releases/TL-YYYYMMDD-NNN.yaml
    content_sha256   # ★ 对"实际发送的字节串"取 sha256，详见 §4.2
    commit_message   # 规范化 message
    state            # INTENT | COMMITTED | MATCHED_BY_WEBHOOK | CONFLICT | FAILED
    attempt; commit_sha; request_id; gitlab_project_id
    class Meta:
        unique_together = [("ticket","content_sha256")]
```

### 4.2 代提交：内容确定性是幂等的前提

```python
# apps/channels_a/services/submit_manifest.py
def build_manifest(ticket) -> Manifest:      # 必须确定性
    yaml.safe_dump(data, sort_keys=True, default_flow_style=False, allow_unicode=True)
```

**`content_sha256` 的字节口径必须钉死：对"实际发送出去的字节串"取哈希。**

文档此前写作"规范化 YAML 的哈希"，但预检要拿它跟 **GitLab 返回的文件内容**比对——那两个不是同一个东西。发送的是 `yaml.safe_dump(...)` 的输出字节，取回的是 GitLab 存的字节，**尾部换行、编码、GitLab 侧是否做过任何规范化**都会让哈希不等。而不等的表现是"幂等守卫判定未提交 → 再提交一次 → 重复构建"，正是下面这段要防的事。

落实为两条：

1. `build_manifest()` 返回 `(content: str, sha256_hex)`，其中哈希**在 `content.encode("utf-8")` 上计算**，且 `content` 就是后面 POST 出去的 `content` 字段，中间不允许再经过任何转换。
2. 预检比对时，对 GitLab 返回的内容（`content` 字段 base64 解码后的**原始字节**）直接取哈希，**不要 `yaml.safe_load` 之后再 `safe_dump` 重新规范化**。若发现两者不同，那就是真的不同，应当报 `MANIFEST_CONFLICT` 而不是"再规范化一下看看"。

**`data` 中的时间字段用 `ticket.submitted_at`（单据时间），绝不用 `now()`。**

用 `now()` 意味着每次重试生成不同内容 → `content_sha256` 变化 → **幂等守卫完全失效**。这是"重试导致重复构建"的真正根因，比网络超时本身隐蔽得多——因为守卫代码看起来是对的，只是它匹配的键每次都不同。

重试必须**重放同一次提交**：以 `(ticket_id, content_sha256)` 为唯一键写 `ManifestSubmission(state=INTENT)`，**在网络调用之前**。没有意图日志，遇到"GitLab 已提交但响应超时"时平台完全失明。

提交流程：

```
0. 前置：调度器已完成 APPROVED --TRIGGER_START--> EXECUTING（边 7），本任务
   此时才被 effects 投递进来，AppEnvLock 已在调度器手里
1. FSM 守卫: state == EXECUTING 且 approved_by is not None 且 lease 有效
2. intent = get_or_create(ManifestSubmission(ticket, content_sha256, state=INTENT))
3. 幂等预检（FR-CHA-02，双通道）:
   a. GET /projects/{id}/repository/files/{path}?ref={branch}
      200 -> 原始字节哈希 == intent.content_sha256 ? 判定已提交 : 抛 MANIFEST_CONFLICT
      404 -> 未提交
   b. GET /projects/{id}/repository/commits?path={path}&per_page=20
      只作为 3a 的补充证据（见下方"通道 b 的收窄"）
4. POST /projects/{id}/repository/commits
   actions: [{action: "create", file_path, content}]
   400 "A file with this name already exists" -> 视为已提交，回到 3a 复核内容
5. intent.state=COMMITTED, commit_sha=resp.id
6. 写事件 TicketEvent(kind="MANIFEST_COMMITTED", payload={"commit_sha": ...})
   —— 里程碑事件，**不改变 state**（此时已是 EXECUTING）
```

> **第 6 步是 D-2 的修正点。** 旧版写的是 `fsm.apply(..., "TRIGGER_SUBMITTED_TO_GITLAB")` 让 `APPROVED → EXECUTING`——那是"审批通过即执行"时代的遗留，与 D-2（`APPROVED` 不持锁）和 §1.6a（调度器是唯一晋升者）**双重冲突**：一张不持锁的单据不可能满足"提交必须持锁"的前提。
>
> 修正后，`APPROVED → EXECUTING` 只由调度器经边 7 触发，代提交任务在 `EXECUTING` 态下工作。里程碑用**不改状态的事件**表达。这也顺带解决了一个旧问题：原设计里"提交进行到一半"的单据在 UI 上显示为 `APPROVED`，与真正在排队的单据**外观完全相同**，运维无法区分"在排队"和"正在提交"。

> **通道 b 的收窄（建议）**：原描述"按 blob 内容哈希匹配"要求对 20 个 commit 逐个拉 blob（`GET /repository/blobs/{sha}`），成本 20 次额外调用，而它要证明的结论（"这个文件是我提交的"）已被 3a 直接覆盖（3a 读的就是该路径当前内容）。**保留 b 但降为 P2**，且限定为"仅当 3a 返回 404 而 `intent.state == COMMITTED` 时"才跑——这恰是"提交成功但文件被删/被改写"的窄场景。不要在每次提交前都跑它。

**用 `action: "create"` 而非 `"update"`**：`create` 在路径已存在时返回 400，这个 400 本身就是服务端提供的一道幂等信号；`update` 会静默覆盖，把重复提交变成"看起来成功"。

**只对 GET 重试，绝不对 POST 重试。** 这是幂等策略的核心分界：GET 重试无副作用，POST 重试正是重复构建的来源。POST 失败一律走"由外层任务重新执行完整流程（含预检）"的路径。

### 4.3 数字化主键链

`Ticket.code ↔ ManifestSubmission.commit_sha ↔ PipelineRun.commit_sha ↔ (jenkins_job, build_no)`

- `PipelineRun` 上 `unique(jenkins_job, build_no)` 与 **`unique(ticket, commit_sha)`**（复合，见 §3.1 的说明——**不是**此前写的单列 `unique(commit_sha)`，那会让回滚单无法创建）。
- 关联建立有三条路径，最终都必须落到同一条 `PipelineRun`：平台自己知道 sha（主路径）、webhook 推来 sha（§4.4，用于平台 API 超时的补救）、Jenkins `changeSet` 带出 sha（兜底）。
- 每条关联都写一条 `TicketEvent(payload={commit_sha, build_no, source})`——`source` 字段让"链是怎么合上的"可审计。

### 4.4 Webhook 旁听与 BYPASS 关联

**问题本质**：平台自己提交的 push 也会推 webhook 给平台，不能产生假 BYPASS。

而 **GitLab 在提交事务中同步投递 webhook，HTTP 响应在其后**——所以"用 `ManifestSubmission.commit_sha` 比对"在竞态窗口内会落空（此时平台还不知道自己的 sha）。

分层关联：

```
收到 push webhook（快速 200 + 落 tl_callback_log 去重）
  │
L0 前置过滤（在关联之前，必须先过）
   - project.path_with_namespace != ENTRY_PROJECT          -> 忽略（OQ-1 已确认资产库物理分离）
   - ref 不是触发分支 / 是 tag push                         -> 忽略
   - after == 0000...0（删除）或 before == 0000...0（新建分支）-> 忽略
   - commits[].{added,modified} 无 releases/** 匹配          -> 忽略（非清单推送）
  │
L1 按 release 文件路径解析单号: releases/TL-YYYYMMDD-NNN.yaml -> 查找 Ticket
   ├─ 找到 且 trigger == PLATFORM:
   │     ├─ ManifestSubmission 存在且 state == INTENT（sha 未知）
   │     │     -> 这是"API 响应超时"竞态：把 webhook 的 after 回填为 commit_sha，
   │     │        state = MATCHED_BY_WEBHOOK，绑定 PipelineRun。不建 BYPASS。
   │     └─ 不存在或内容哈希不匹配
   │           -> MANIFEST_TAMPERED：写告警事件 + 建 BYPASS 单
   │              （人工改过清单，必须留痕）
   ├─ 找到 且 trigger == BYPASS -> 幂等，忽略
   └─ 未找到 -> 进入 L2
  │
L2 兜底判定（依次，任一命中即不建单，且必须记录"是靠哪条规则拦下的"）
   - 服务账号提交（user_username == SERVICE_ACCOUNT）—— 防深，不单独依赖
   - commit message 匹配平台规范化模板 AND 对应 Ticket 存在
  │
L3 建 BYPASS 单
   - source_commit_sha = after（push 粒度，不是 commit 粒度）
   - 去重: tl_callback_log(source='GITLAB',
            dedup_key=sha256(f"push|{project_id}|{after}|{ref}"))
           + 部分唯一索引 uniq_bypass_commit
   - 建单时 state=DRAFT，**同一事务内** apply("TRIGGER_BYPASS_DETECTED")
     -> EXECUTING（权威迁移表边 2），携带 commits[] 全量到事件 payload
```

> **为什么是"先 DRAFT 再 apply"，而不是建单时直接写 `state=EXECUTING`（PRD 原文是"直接进入 EXECUTING"）。** 因为 §1.4 规定 `apply()` 是**唯一**允许修改 `Ticket.state` 的函数，而它要求 `src == ticket.state`——一张新生单据没有 `src`，**直接写 `state` 就等于开了一个绕过 `apply()` 的口子**。一个口子开了，后面所有"这次情况特殊"的写入都会走它，事件表也就不再是完整的时间线了。
>
> 落地：建单与 `apply()` 在**同一个 `transaction.atomic()`** 内完成，外部（UI、webhook 响应）观察不到中间的 `DRAFT`。事件表里留下一条 `actor="system:gitlab-webhook"` 的 `DRAFT → EXECUTING`，这正是审计需要的"这张单是旁路自动落的"证据。
>
> PRD 的表述（FR-BYPASS）需要同步改成"**建单后自动流转至 EXECUTING，无需人工确认**"——产品语义完全一致，只是不再暗示"绕过状态机"。

> **旁路单不进入 `tl_ticket_queue`。** 它跳过 `APPROVED`，因此**不参与 §1.6a 的排队晋升**。若此时该 app/env 已有活动单，边 2 的 `requires_lock` 守卫会失败——**这是必须显式处理的第三条路径**：旁路单抢不到锁时，不能报错、不能丢弃（那会丢掉"有人绕过平台发版"这个事实），而应当**记录旁路事件 + 告警，但不建单**（因为建了也执行不了），或者建单后置为 `PAUSED` 等人工介入。**建议取后者**：先建单留痕，再进 `PAUSED` 并携带 `from_state=EXECUTING`，由人决定是排队还是撤销。这条待产品确认，暂按"建单 + `PAUSED`"落地。

**为什么不能只靠 L2 的服务账号过滤**：人会借用服务账号推代码，账号也可能改名。只靠它等于"平台自己推的拦得住，人借账号推的拦不住"，且账号一旦变更会一次性产生大量假 BYPASS 单，把 M3 的旁路率指标污染成废数。反过来只靠 L1 也不行——L1 依赖平台侧记录的完整性。

**两层并用，并且当"只有 L2 命中"时必须埋点告警**：那说明 L1 的关联逻辑已经坏了。

**另一个陷阱**：一个 push webhook payload 可能包含多个 commit。BYPASS 单是 **push 粒度**，用 `after` 作主键——用 `commits[].id` 会在一次 push 里建出 N 张单，表现为单据列表刷屏、旁路率虚高数倍。

### 4.5 Webhook 安全

**GitLab 用的是 `X-Gitlab-Token` 明文 token 常量时间比较，不是 HMAC。**

把 §3 的 HMAC 代码直接复用到 GitLab 回调上是常见错误：结果是永远验签失败（或永远通过，若误写成不校验）。

`integrations/gitlab/…::verify_webhook_token` 与 `integrations/jenkins/…::verify_hmac` **必须分开实现**。

此外，webhook 无论如何要返回 200 并不阻断 GitLab→Jenkins 链路（NFR-02）。

### 4.6 Mock GitLab 必须暴露的端点

| 端点 | 用途 | 必须支持的行为 |
|---|---|---|
| `GET /api/v4/projects/{urlencoded_path}` | 项目解析 | 按 path 查 id |
| `GET /api/v4/projects/:id/repository/files/:path?ref=` | **幂等预检主通道** | 200 返回 `{blob_id, content(base64), content_sha256, commit_id, last_commit_id}`；不存在 404 |
| `POST /api/v4/projects/:id/repository/commits` | 代提交 | **`action:create` 且路径已存在时返回 400 `A file with this name already exists`**（此行为是幂等测试的核心断言点） |
| `GET /api/v4/projects/:id/repository/commits?path=&per_page=` | 按路径查历史 | 返回 `id/title/created_at` |
| `POST /_control/emit_push` | **旁路与竞态测试** | 构造 push payload，用 `X-Gitlab-Token` 签名推送到接收器 |
| `POST /_control/config` | 故障注入 | `latency_ms`、`commit_then_timeout`（**已落库但响应超时**）、`rest_status=500` |
| `GET /_control/deliveries` | 断言 | "本单只提交了 1 次 commit"、"平台未创建 BYPASS 单" |

**假服务必须能构造三种时序**：

1. commit 落库 → 响应成功 → 发 webhook
2. commit 落库 → **响应超时** → 发 webhook（测幂等守卫）
3. commit 落库 → **发 webhook → 响应**（测 L1 的 INTENT 回填竞态）

### 4.7 生产会炸的点

**① YAML 内容不确定性导致幂等守卫完全失效。** `now()` 时间戳、dict 顺序、浮点格式任一不确定，重试都会生成新内容，预检查不到，最终产生第二次提交与第二次构建。必须对 `build_manifest` 输出做**内容快照测试**（同一 ticket 生成 100 次，哈希恒定）。

**② 资产库与入口库共用 webhook。** OQ-1 已确认物理分离，但 mock 与配置必须显式验证这条——只要有一次误配，**运维每改一个 playbook 就产生一张 BYPASS 单**，旁路率指标与单据列表同时被污染。

**③ 一次 push 建多张 BYPASS 单。** 用 `commits[].id` 而非 `after` 做去重键，或多 commit push 时逐个建单。

---

## §5 验证引擎

> PRD 称其为"第一重点模块"。本章的核心是把"业务失败"与"平台故障"彻底分开。

### 5.1 模型（app: `verification`）

按 PRD 保留三个 JSONB 列以兼容 API/UI，**同时**建规范化子表——否则 M3 的"失败原因 Top N"需要遍历 JSONB，无法做索引与聚合：

```python
class VerificationRun(TimeStampedModel):      # tl_verification
    ticket = FK(Ticket, related_name="verifications")
    attempt; trigger            # AUTO | MANUAL | RECHECK
    status                      # RUNNING | PASS | FAIL | ERROR | TIMEOUT | INCONCLUSIVE
    liveness = JSONField(); log_scan = JSONField(); state_diff = JSONField()   # PRD 兼容
    config_snapshot = JSONField()      # 运行时 CMDB 验证配置快照（配置会漂移！）
    before_snapshot = JSONField(null=True); after_snapshot = JSONField(null=True)
    diff = JSONField(); conclusion; summary = JSONField()
    started_at; finished_at; deadline_at; operator
    class Meta:
        unique_together = [("ticket","attempt")]

class VerificationCheck(TimeStampedModel):    # tl_verification_check
    run, seq, family            # LIVENESS | LOG_SCAN | STATE_DIFF
    check_type                  # HTTP | TCP | PROCESS | LOG_KEYWORD | VERSION | POD_LIST | HEALTH
    target                      # "host:10.0.0.1" / "url:https://..." / "pod:order-xxx"
    host_ref = FK(cmdb_host, null=True)
    required: bool; on_timeout  # FAIL | ERROR | RETRY
    status                      # PASS | FAIL | ERROR | TIMEOUT | SKIPPED
    expected = JSONField(); actual = JSONField()
    error_class                 # AUTH | DNS | CONNECT | CONNECT_REFUSED | TIMEOUT_CONNECT
                                # | TIMEOUT_EXEC | PERMISSION | NOT_FOUND | COMMAND
                                # ★ status ∈ {ERROR,TIMEOUT} 时必填，空值不得默认成 FAIL（§5.4.1）
    message; duration_ms; output_ref; check_attempt
```

### 5.2 执行器 × 传输层二维组合

**朴素实现只抽象"检查什么"，漏掉"在哪运行"**，于是 host 形态的检查在平台本机跑 `curl`，永远探的是平台自己。

```python
# apps/verification/executors/base.py
class CheckResult:
    status; expected; actual; message; duration_ms; error_class; raw_ref

class CheckExecutor(Protocol):
    family: str; check_type: str
    def plan(self, ctx) -> list[CheckSpec]: ...          # 拆解为 N 个目标
    def run(self, ctx: CheckContext, spec: CheckSpec,
            transport: Transport) -> CheckResult: ...

class Transport(Protocol):
    def exec(self, host: HostRef, argv: list[str], timeout: int) -> ExecResult: ...
    def http(self, url: str, *, timeout: int, expect_tls: bool) -> HttpResult: ...
```

| Transport | 实现要点 |
|---|---|
| `LocalTransport` | 平台本机直连（K8s API、平台可达的 HTTP） |
| `SshTransport` | `asyncssh` 连接池；`ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=yes`。**BatchMode 必须开**，否则未知 host key / 密码提示会挂到超时；**连接复用必不可少**——200 台串行建连 × 5s = 17 分钟 |
| `K8sTransport` | 委托给 `integrations/k8s/K8sStateReader`，由 `settings.K8S_READER = "api" \| "kubectl" \| "mock"` 选择（**OQ-3 挂载点**）。检查代码只依赖接口，**P1 工作不被 OQ-3 阻塞** |
| `MockTransport` | 本地开发返回预置结果 |

检查注册表：`verification/registry.py::resolve_checks(app, env) -> list[CheckSpec]`，合并 CMDB 默认（`cmdb_app` 上的探活 URL / 期望码 / 日志路径 / 错误关键字 / k8s_workload）与 `cmdb_verification_rule` 的环境级覆盖。

**合并粒度是"逐字段"，不是"整条规则替换"。** 原文只写了"合并"，而两种实现对生产的影响完全不同：若实现成"环境级规则存在即整体替换"，那么运维为了改一个探活 URL 而建的规则，会把应用级的**日志关键字、日志路径、期望码全部清空**——后果正是 §5.7③ 那个"全 SKIPPED 静默通过"，每一次发布都会自动判定验证通过。

落地上定死三条：

1. **`None` 表示继承，空字符串/空列表表示显式清空。** 只有后者才覆盖。这个区分不做，运维就永远没法把某个环境的关键字关掉。
2. 每个字段的合并必须**可追溯**：`config_snapshot`（§5.1）里记录每个生效字段的**来源**（`app` / `env`），否则"为什么这次验证没跑日志检查"要人去数据库里对。
3. 合并结果为空（一个 check 都解析不出来）→ **直接判 `INCONCLUSIVE`，不进入执行**。一个配不出任何检查的环境，绝不能让发布"自动通过"。

P1 的验证 Playbook（FR-VFY-06）**复用 `channels_b` 的 runner**，把 task 结果转成 `CheckResult`，不要另建第二个执行器。

### 5.3 结果分类：五态

| 状态 | 含义 | 语义归属 | 对闭环的动作 |
|---|---|---|---|
| `PASS` | 执行成功且符合预期 | 产品信号 | 通过 |
| `FAIL` | 执行成功但不符合预期（探活 500、命中错误关键字、版本不符） | **产品信号** | 阻断 → VERIFY_FAILED |
| `ERROR` | 无法执行（SSH 认证失败、DNS 解析失败、命令不存在、K8s API 403、日志文件不存在/无权限） | **基础设施信号** | 阻断但**不同类告警**，M3 失败原因分开统计 |
| `TIMEOUT` | 超出预算 | 按策略判定 | 见下 |
| `SKIPPED` | 不适用（host 形态应用跑了 k8s 检查） | — | 显式可见，**不得计入 PASS** |

### 5.4 超时 vs 合法失败的区分：四层

1. **强制硬超时**：每个 check 的 executor 与 transport 双重超时（如 `curl --max-time 5 --connect-timeout 3`、`timeout 10 tail -n 200 ...`）。
   **命令本身不带超时是验证引擎最常见的卡死原因。**
2. **子分类**：`TIMEOUT_CONNECT`（连不上）vs `TIMEOUT_EXEC`（命令挂住，典型是 `tail -f`、未加 `--max-time` 的 curl、`kubectl logs -f`）。
3. **策略化归因**：每个 check 带 `on_timeout ∈ {FAIL, ERROR, RETRY}`。
   - 探活类默认 `RETRY`（重试 2 次，含 1 次换目标）→ 仍超时**判 `FAIL`，但受下面的 §5.4.1 修正约束**
   - 基础设施读取类（K8s API、日志文件）默认 `RETRY` → 仍超时判 `ERROR`（是平台够不着，不是应用挂了）

#### 5.4.1 连接类失败的统一修正（已拍板）

**"探活超时 → FAIL" 这条规则必须加一个整体性例外，否则 OQ-9 想解决的问题会在另一条路径上原样复发。**

`TIMEOUT_CONNECT`（按本节第 2 条的子分类，指"连不上"）与 `ERROR` 里的 `DNS` / `CONNECT` / `PERMISSION` 类，**报的都是"平台到目标的这条网络路径不通"，与"应用是否健康"无关**。平台侧网络一抖、安全组一改、DNS 一挂，所有探活全部连接失败 → 全部判 `FAIL` → 全部 `VERIFY_FAILED` → 告警卡片上还带着回滚入口 → **驱动运维批量回滚一批其实成功的发布**。

这与 OQ-9 要防的是同一件事，只是发生在探活路径而非基础设施读取路径上。故加一条**全局修正规则**：

> **在一次 `VerificationRun` 内，若所有失败检查的失败原因都落在"连接类"集合里（`TIMEOUT_CONNECT` / `ERROR(class ∈ {DNS, CONNECT, AUTH, PERMISSION})`），且无一例 `FAIL` 是"执行成功但结果不符预期"，则整体结论从 `FAIL` 提升为 `ERROR` → `VERIFICATION_ERROR`。**

只有**部分**目标连接失败时，才维持 `FAIL`——那才是"应用真的只挂了一部分"，是合法的产品信号。

落地要点：

- 这条修正发生在 `evaluate()` 的**最后一步**（§5.5 第 5 步），不能散在各个 check 里判断——单个 check 看不到全局。
- 判据是 `error_class` 的**分布**，因此 §5.1 的 `VerificationCheck.error_class` 必须是**必填**（当 `status ∈ {ERROR, TIMEOUT}` 时）。空值不得默认成 `FAIL`。
- 反向也要防：**不能把"应用真的全挂了"误判成 `ERROR`**。区分点是——应用进程全挂时，探活返回的是 `TIMEOUT_EXEC` 或连接被拒（`CONNECT` 但 TCP RST，端口关闭），而平台网络不通时是 `TIMEOUT_CONNECT` 或 `DNS`/`AUTH` 失败。**这两类在实现上必须落成不同的 `error_class`，不能都归成 `CONNECT`**，否则这条修正是无效的。这是 `M2-11`/`M2-12` 的交付要点。
4. **聚合兜底**：`VerificationRun.deadline_at`（默认 300s）由 `verification.tasks.watchdog_verification` 强制收口为 `INCONCLUSIVE`，再按 §5.5 进入 `VERIFICATION_ERROR`（**不是** `VERIFY_FAILED`——超时是平台侧的推断，不能断言应用坏了）。
   **没有这一层，验证引擎的任何一次挂起都会让单据永久停在 VERIFYING**——而 PRD 的 CONFIRMING 超时升级（FR-FB-03）**只覆盖确认阶段，不覆盖验证阶段**，这里必须自建。

### 5.5 结论聚合与状态流转

```python
# apps/verification/services/evaluate.py
def evaluate(run: VerificationRun) -> Conclusion:
    # 1. 短路规划: 探活 CONNECT_TIMEOUT/ERROR -> 跳过日志扫描
    #    （应用都没起来，扫日志只是噪音且白烧 60s）
    # 2. 必检项: required=True 的任一 FAIL/ERROR/TIMEOUT -> 整体 FAIL / ERROR
    #    优先级: ERROR/TIMEOUT 先于 FAIL 输出（见第 5 步的修正）
    # 3. n-of-m 阈值: min_pass_ratio (默认 1.0，滚动发布可配 0.9)
    # 4. 全 SKIPPED -> INCONCLUSIVE (绝不判 PASS)
    # 5. ★ 连接类修正: 全部失败均属连接类 -> FAIL 提升为 ERROR  (见 §5.4.1)
```

第 5 步必须是**独立的一步**，且**在第 2/3 步之后**运行：前两步按逐项规则得出"整体是失败"以及失败的类型分布，第 5 步才拿到全局视野去判断"这些失败是否全部是平台侧原因"。把这条逻辑塞进第 2 步（逐项判定时）是错的——单个 check 无法知道自己是不是"唯一失败的那个"。

| 结论 | 迁移 | 后续 |
|---|---|---|
| `PASS` | `TRIGGER_VERIFY_PASSED` → CONFIRMING | 通道 B 由发起人确认即闭环（FR-FB-02） |
| `FAIL` | `TRIGGER_VERIFY_FAILED` → VERIFY_FAILED | **产品信号**：告警 + 在告警卡片直接暴露回滚入口（FR-RB-04） |
| `ERROR` / `TIMEOUT` / `INCONCLUSIVE` | `TRIGGER_VERIFY_ERROR` → **VERIFICATION_ERROR** | **基础设施信号**：不同类告警；允许 `→ VERIFYING` 重试（`max_verify_attempts` 默认 3，计数在 `Ticket.verify_attempts`，由 `guard_verify_attempts` 强制）；**不主动提示回滚** |
| `FAIL`（**但全部失败均属连接类**） | 同上，提升为 `TRIGGER_VERIFY_ERROR` | **§5.4.1 的修正**：连接类全失败 = 平台够不着，不是应用坏了。**这一行是 OQ-9 在探活路径上的补漏**，缺了它，一次网络抖动就会批量误报 `VERIFY_FAILED` 并弹出回滚入口 |

**两个终态的告警与操作必须分开**（OQ-9 已拍板新增 `VERIFICATION_ERROR`）：

- `VERIFY_FAILED` 的语义是"应用真的坏了"→ 回滚是正确的下一步。
- `VERIFICATION_ERROR` 的语义是"平台够不着，应用状态未知"→ 此时提示回滚会**驱动运维回滚一个其实成功的发布**；正确动作是先修复平台侧可达性（修 SSH、修 K8s SA 权限、修日志路径）再重试验证。

这也是 I 不接受把基础设施故障塞进 `VERIFY_FAILED` 的原因——那是"验证引擎反过来制造故障"的路径。

`VERIFICATION_ERROR` 的出口有三条，均由人工或超时驱动，**不自动推进**：

| 出口 | 触发 |
|---|---|
| `→ VERIFYING` | 人工重试，受 `max_verify_attempts` 约束 |
| `→ ROLLBACK` | 运维判断确实是发布问题，主动回滚 |
| `→ PAUSED` | 需要人工介入排查，暂停观察 |

**为什么必须区分**：基础设施误报被塞进 VERIFY_FAILED，会驱动运维**回滚一个其实成功的发布**。这是"验证引擎反而制造故障"的典型路径，也是团队最终弃用该模块的最常见原因。

`VERIFY_FAILED` 的出口有三条（§1.3.1 边 19 / 20 / 29）：

| 出口 | 触发 | 说明 |
|---|---|---|
| `→ ROLLBACK` | 用户，回滚入口在告警卡片中直接暴露 | 默认动作，FR-RB-04 |
| `→ PAUSED` | 用户，暂停观察 | 需要人工排查时 |
| `→ CONFIRMING` | 用户（≥ 审批人级别），**人工强制放行** | **边 29（2026-09-21 拍板开放）**：业务判定"验证不过但可接受"。必须填理由、留审计、进强制放行率看板，UI 上要与干净的成功区分开 |

**强制放行不是 `VERIFICATION_ERROR` 的出口。** 后者不允许人工放行——"平台够不着"用一句"我担着"跳过，等于把 ADR-6/ADR-9 专门防的事重新放回来。两条路径的权限检查长得像，但语义相反，实现时不要复用同一个守卫。

**前后对比（FR-VFY-04）**：`before_snapshot` 必须由 **FSM 进入 EXECUTING 的 effect** 触发（`verification.tasks.capture_before_snapshot.delay(ticket_id)`），**不能放在执行任务内部**——那时 playbook 已经开跑，快照已被污染。

且必须是**尽力而为**：失败只标 `before_snapshot_partial=True` 并降级展示，**不允许因为一个诊断快照失败而阻塞发布**。

### 5.6 并发与资源隔离

- 主机检查用 `asyncssh` + `asyncio.gather`（`verify_concurrency` 默认 20）。
- 与 §2.2 的 `ConcurrencySlot` **共用机制但预留配额**：`scope="verify"` 独立命名空间，全局池中为验证预留固定槽位（默认 8）。

**不预留会怎样**：一队 ansible 作业把所有槽位占满 → 要验证的单据拿不到验证槽位 → 卡在 `VERIFYING` 直到 `deadline_at` 超时 → 被 watchdog 推进 `VERIFICATION_ERROR` → **发布实际上成功了，平台却报"验证未能完成"**。

**这是饥饿，不是死锁。** 原文曾把这个场景称为"分布式死锁"，论证是"ansible 又在等这些单据释放 app/env 锁 → 形成环"。按 §1.5.1 的加锁顺序（**先 `AppEnvLock`，后 `ConcurrencySlot`**），持有执行槽位的作业必然已经持有自己的 app/env 锁并且正在运行，它们会自然结束——**环不成立**。真正的死锁只在"先取槽位、再等锁"时才出现，而那正是 §1.5.1 明令禁止的顺序。

把理由写对很重要：理由写错，后来的人就会"证明"配额没必要（因为死锁确实不存在），然后把它删掉，于是**验证被长作业饿死**这个问题原样回来。配额防的是饥饿，与加锁顺序是两件独立的事，两者都要。

### 5.7 生产会炸的点

**① 日志扫描的假阳性。** `tail -n 200` 的窗口里包含部署**之前**就存在的错误行 → 一次正常发布被判失败。

正确做法：`before_snapshot` 里记录日志文件的 `size/offset`（以及 `inode`，用于识别轮转），验证时只扫新增区段；仅在文件被轮转（inode 变化）时回退到 `tail -n`。

另外关键字来自 CMDB，含 `(`、`[`、`*` 的文本直接插进正则会抛异常或误匹配——**除显式声明 `is_regex=True` 外一律 `re.escape`**，并统一大小写归一。

**② 主机解析路径分叉**（见 §0.3）。这是本章与 §2.5 的交叉雷点：发布 A 组、验证 B 组，**结果全绿**。

**③ 一个检查卡住拖垮整单，或全 SKIPPED 静默通过。**

- 前者是缺少硬超时与 `deadline_at` → 单据永久停在 VERIFYING。
- 后者是 CMDB 验证配置写错（k8s 字段配在 host 形态应用上），所有检查 SKIPPED → 若把 SKIPPED 当 PASS，**每一次发布都会自动"验证通过"**。

故：全 SKIPPED 判 `INCONCLUSIVE`，且 **CMDB 保存时做形态与校验规则的交叉验证**（`deploy_mode=host` 却配了 `k8s_workload` 直接拒绝保存）。

---

## §6 Mock 层与 Settings 落地规则

1. **HTTP 系统用假服务进程，Python 接口用假实现。**
   判据：**该依赖是否被 Celery worker / sweeper 直接调用**——是，则必须真 HTTP。

   > **决定性论点**：sweeper 跑在 Celery worker 进程里，无法用 `responses` / `respx` 这类进程内 mock 覆盖。因此 Jenkins 假服务**必须是真 HTTP 服务**。

2. **业务代码零 `if mock` 分支。** 切换只靠 `GITLAB_BASE_URL` / `JENKINS_BASE_URL` + `*_VERIFY_TLS`；`INTEGRATION_MODE` 仅用于前端提示与 CI 断言。

3. **签名/验签工具单一实现。** 假服务 import 生产代码的 `core/hmac.py`，不允许假服务自己再实现一遍——否则签名校验路径根本没被测试。

4. **假服务必须自带 `_control` 与故障注入**，且必须能构造"落库成功但响应超时""webhook 早于 API 响应"这两类时序——它们分别是 §4.2 幂等守卫与 §4.4 关联逻辑的**唯一有效测试手段**。

5. **本地也强制 PostgreSQL**（见 §0.6）——用本地已装实例（≥14，非容器），见 §0.6a。

6. **并发测试用 `TransactionTestCase` + 真实线程**（见 §0.7）。

7. **双层环境：真实实例管"假设对不对"，Mock 管"坏情况下不撒谎"。** 本地 docker/compose 临时拉起的真实 GitLab + Jenkins（§0.6b、`S0-9`）验证**我们对真系统的假设是对的**——真实 webhook 载荷形状、真实 notify 签名行为、凭据初始化流程；Mock 层（`S0-6a/b/c`）验证**系统在坏情况下不撒谎**——`callback_drop_rate`、落库超时、webhook 抢跑，这些时序真实系统构造不出来，且临时实例随时可被销毁。**分界线：里程碑验收项在 Mock 链路跑（可重复、可断言），另用真实实例各人工走一遍（`人工观察`）；两次都过才算过。任何 CI 用例不得依赖真实实例。**

---

## §7 决策记录

本文档涉及两项需要产品侧拍板的决策，已于 2026-09-20 拍板。**两项均已同步至 `PRD.md` §5.2 与 §12（ADR-6 / ADR-7）**。

### 第一轮（2026-09-20）

| 编号 | 决策 | 结论 | 影响面 |
|---|---|---|---|
| **D-2** | `APPROVED` 的语义 | ✅ **已批准待调度（排队中，不持有锁）** | §1.1、§1.5 部分唯一索引谓词、§1.6 锁回收 |
| **OQ-9** | 是否为"验证基础设施故障"新增状态 | ✅ **新增 `VERIFICATION_ERROR`** | §1.3 `TicketState`、§1.5 DDL 谓词、§5.3–§5.5 流转、`M2-16`/`M2-16a`/`M2-17`/`M2-17a`、`M3-01`、`X-03` |

### 第二轮（2026-09-21）：全文审计后的七项

第二轮对本文档做了一次完整审计，目标是找"**自相矛盾**"与"**会导致静默错误**"的地方（而非风格问题）。共修七处，其中四处由用户拍板，三处按审计建议落地（下面标注了来源）。

| 编号 | 问题 | 结论 | 落地位置 |
|---|---|---|---|
| **A-1** | D-2 未传导：调度器与代提交任务**都**自称负责 `APPROVED → EXECUTING`，且旧守卫 `state == APPROVED` 在新语义下永远无法满足 | ✅ **用户拍板：调度器是唯一晋升者**。代提交在 `EXECUTING` 态下工作，里程碑改为不改状态的事件 | §1.3 `guard_submit_only_from_executing`、§1.3.1 边 7、§4.2 第 0/6 步、`PRD.md` §5.2 |
| **A-2** | 探活"连接类"超时判 `FAIL`，会让平台网络抖动批量误报 `VERIFY_FAILED` 并弹出回滚入口——与 OQ-9 要防的是同一件事，只是换了条路径 | ✅ **用户拍板：全目标连接类失败 → `ERROR`**（部分失败仍判 `FAIL`） | §5.4.1（新增小节）、§5.3 `error_class`、§5.5 表、`M2-11`/`M2-12` |
| **A-3** | 验签失败也返回 200 并落库，等于给了一个**无需认证即可灌满 `tl_callback_log`** 的磁盘耗尽路径 | ✅ **用户拍板：401 且不落库**，审计走应用日志 + 独立计数器；`signature_ok` 字段删除 | §3.3、§3.1 `CallbackLog` |
| **A-4** | 实施计划的人天估算对 AI coding 无意义，但它承担的"这件事更难"的信号会一起丢掉 | ✅ **用户拍板：换成 风险等级 `L1/L2/L3` + 验证强度 三值**。并要求"自动断言"类任务的 DoD 必须是 AI 能自己跑的判定命令 | `implementation-plan.md` |
| **A-5** | 权威迁移表不存在——触发器名字散落在 §2–§5 的散文与代码里，且已开始互相打架（正是 A-1 的成因）。另有 `PAUSED` 无出口、旁路单"直接进 `EXECUTING`"与"`apply()` 是唯一写 `state` 的函数"冲突 | ⚠️ **按审计建议落地**：新增 §1.3.1 完整迁移表；旁路单改为"先 `DRAFT` 再 `apply()`"，同事务内完成。**第三轮补全**：两条曾留白的边已由产品拍板（边 29 / 30）。**第四轮补录**：边 31 `TRIGGER_BUILD_LOST`（漏登表，见 §7 第四轮）。现为 **31 条边 + 3 终态** | §1.3.1、§4.4 L3、§1.2 `paused_from_state` |
| **A-6** | `PipelineRun` 的 `commit_sha` 唯一性在 §3.1 与 §4.3 互相矛盾；单列唯一还会让**回滚单建不出来** | ⚠️ **按审计建议落地**：以 `(ticket, commit_sha)` 复合为准 | §3.1、§3.4 规则 3、§4.3 |
| **A-7** | `ConcurrencySlot` 三处缺陷：`app:{id}` 作用域无法被 seed 迁移预置（**新应用的作业永远排不出去且不报错**）；`scope IN (...) LIMIT 1` 让"全局+单应用"双限制失效；与 Celery 并发数的关系未定义 | ⚠️ **按审计建议落地**：按需创建 + 双 scope 全有或全无 + 启动断言 | §2.2 |

**另修六处"写错了但没人会立刻发现"的细节**（不需拍板，只有一种正确写法）：

| 编号 | 问题 | 落地 |
|---|---|---|
| A-8 | `confirmed_source` 在成功路径被写成 `"CALLBACK_OR_SWEEPER"`——恰好丢掉了该字段存在的唯一理由（审计"谁先到"） | §3.5：`source` 改为入参 |
| A-9 | sweeper 示例代码跑不起来（`select_for_update` 无 `atomic()`），补上后又会变成"50 行锁跨 50 次 HTTP" | §3.6：拆成"取 ID / 逐单处理"两段 |
| A-10 | §5.6 把"饥饿"论证成"死锁"，而按正确的加锁顺序环不成立——理由写错会导致配额被后人"证明"没必要而删掉 | §5.6 重写 + §1.5.1 加锁顺序硬规则 |
| A-11 | `fencing_token` 全文档未说明在哪里校验，等于装饰品——锁回收后原持有者仍会继续写副作用 | §1.5.2（新增小节）：`apply()` 内校验 |
| A-12 | 时间基准规则自己违反自己（§3.6 明令用 PG `now()`，示例代码却是 Python 时间戳） | §0.2 新增边界规则 4、`db_now()` |
| A-13 | `content_sha256` 算在"规范化 YAML"还是"实际发送的字节"上未定；两者不等会让幂等守卫静默失效 → 重复构建 | §4.2 字节口径 |

**遗留（本轮未处理，需产品确认后再定）**：

1. ~~`VERIFY_FAILED → CONFIRMING`（人工强制放行）与 `SUCCESS → ROLLBACK`（事后回滚）两条边刻意留白~~ → ✅ **已于 2026-09-21 拍板：两条都开**，见 §1.3.1 边 29 / 边 30 与配套守卫、字段、度量。
2. ~~**`JenkinsProvider` 缺 abort 接口**：Mock 暴露了 `POST /job/{job}/{n}/stop`（§3.8），但 §0.4 的接口清单里没有对应方法，也没有任何任务负责调用它~~ → ✅ **已于第四轮闭环**：§0.4 补 `abort(job, build_no)` 方法行，消费者落为任务 `M2-34`（边 25 终止前 best-effort 叫停，失败不阻塞、落 warn 事件）。

### 第三轮（2026-09-21）：两条留白边拍板开放

第二轮审计刻意留下两条边未补，理由是"补边会引入新语义，需产品确认"。本轮产品确认：**两条都开**。

**边 29 `VERIFY_FAILED → CONFIRMING`（人工强制放行）**

打开它的代价是**削弱 `SUCCESS` 的语义**：在此之前，"到达 SUCCESS"等价于"验证通过且业务确认"；此后还多出一种"验证没过但有人担了"。这个代价用四条约束来抵：

1. **权限**：≥ 审批人级别，且发起人不能放行自己。
2. **理由**：必填、有最短长度、不接受占位符——守卫的全部价值就是逼出一句人话。
3. **可见性**：`SUCCESS` 与"强制放行的 SUCCESS"在 UI 上必须长得不一样（`X-03` 加角标）。**看板上两者混同，这条边的治理意义就归零了。**
4. **度量**：强制放行率进看板，与旁路率同级。

**明确不开放到 `VERIFICATION_ERROR`**：那是"平台够不着"，允许放行等于绕过 ADR-6/ADR-9。两条路径的守卫长得像但语义相反，**实现时不得复用同一个守卫函数**。

**边 30 `SUCCESS → ROLLBACK`（事后回滚）**

这条边的结构影响比边 29 大：**`SUCCESS` 从此不再是终态**，终态从 4 个减为 3 个（`REJECTED` / `ROLLED_BACK` / `CANCELLED`）。三处连带影响已分别落地（见 §1.3.1 的说明框）：度量按最终态统计、`guard_no_skip_verifying` 为边 29 放宽、PRD §2 的"闭环"定义补充说明。

两个设计选择值得记下来：

- **必须显式传目标版本**，不做"默认回滚到上一版"的猜测。事后回滚与失败回滚的信息条件不同——失败时"上一版"是唯一合理的答案，事后回滚时线上可能已经又发了几版，"上一版"未必是操作者想回到的那一版。
- **抢不到锁当场拒绝，不入 `tl_ticket_queue`**。队列存在的理由是"没有人在场"（审批通过后系统自动推进）；事后回滚是人在场的一次性动作，塞进自动队列会制造"操作者以为在回滚、其实在排队"的新静默失败。附带好处是 `dispatch_queued` 不必泛化成多目标晋升，`M1-10b` 的语义保持单一。

### D-2：`APPROVED` = 已批准待调度（不持锁）

**未选方案**：已批准即持锁。

**选择理由**：若 `APPROVED` 即持锁，则"批准了但排在队列里"的单据会占住 `应用+环境` 锁，而它可能几小时后才执行——**排队本身就成了阻塞**。且锁的持有者语义上应是执行方（Celery 任务），而非审批动作。

**落地影响**：

- `APPROVED` **不入**部分唯一索引谓词（§1.5 DDL），持有态集合为 `EXECUTING / VERIFYING / CONFIRMING / VERIFICATION_ERROR / PAUSED / ROLLBACK`。
- 排队可见性（UI 的"第 N 位"）由 `tl_ticket_queue` 承担，不依赖锁表。
- 审批动作不涉及租约与心跳；租约从进入 `EXECUTING` 时才开始（§1.6）。
- **新增一个必建组件**：审批通过不再等于立即执行，`APPROVED → EXECUTING` 需要由调度器晋升（§1.6a）。这是本项决策最容易被漏掉的连带改动——没有它，锁一旦被占，排队的单子会永远停在"待执行"且不报错。对应任务 `M1-10b`。

### OQ-9：新增 `VERIFICATION_ERROR` 状态

**未选方案**：保持 PRD §5.2 状态集冻结，走 `VERIFY_FAILED` + `reason_class=INFRA` 标记。

**选择理由**：基础设施误报若被塞进 `VERIFY_FAILED`，会驱动运维**回滚一个其实成功的发布**——这是"验证引擎反而制造故障"的典型路径，也是团队最终弃用该模块的最常见原因。新增状态让"平台够不着"与"应用真的坏了"在**数据模型层**就分开，而不是靠一个容易漏读的标记字段。

**落地影响**（六处，均已落到本文档与实施计划）：

1. §1.3 `TicketState` 增加 `VERIFICATION_ERROR`。
2. §1.5 部分唯一索引谓词增加 `VERIFICATION_ERROR`（它持有锁）。
3. §5.5 结论聚合：`ERROR` / `TIMEOUT` / `INCONCLUSIVE` → `TRIGGER_VERIFY_ERROR` → `VERIFICATION_ERROR`，与 `FAIL → VERIFY_FAILED` 是两条独立路径。
4. §5.5 出口三条（`→ VERIFYING` 重试 / `→ ROLLBACK` / `→ PAUSED`），**均不自动推进**。
5. 告警文案与 `M3-01` 指标口径按终态分流，不再需要 `reason_class` 字段做二次归因。
6. UI（PRD §10）需为该状态补一个番茄标识——`VERIFY_FAILED` 是"虫眼"，`VERIFICATION_ERROR` 是"平台看不清"，两者不能共用图标（`X-03`）。

**遗留的一处权衡**：新增状态使状态机从 13 态变为 14 态，`TRANSITIONS` 的合法边测试用例相应增加。这是可接受的代价——状态机是纯数据，测试成本低于在生产上误判一次回滚。

### 第四轮（2026-09-23）：开工前缺口盘查

产品宣布准备开始编码，本轮对两份文档做最后一次一致性盘查——只查"会不会让第一天写代码的人踩空"，不做新设计。发现并处理四处：

| # | 缺口 | 处理 |
|---|---|---|
| G-1 | **边 31 漏登表**：`TRIGGER_BUILD_LOST` 在 §3.6 代码片段与正文两处使用，权威迁移表却没有这条边——违反表自己的"先改表"纪律；`M1-09` 的逐行一致断言与 sweeper 实现必有一方落空 | §1.3.1 补录边 31 + "边 31 的完整约束"小节。语义全部收编 §3.6 既有文字，零新增设计 |
| G-2 | **边 10 陈旧引用**：说明里"出口为重试提交（边 20）"——边 20 在第三轮重排后是 `VERIFY_FAILED→PAUSED`，正确恢复出口是**边 24** | 改引边 24。教训：**编号是 ID 不是序**，重排表时所有正文引用都要过一遍 |
| G-3 | **边 25 无主 + 遗留 2 未闭环**：`TRIGGER_TERMINATE` 在实施计划中无任何任务落地；`JenkinsProvider.abort` 缺口挂着"下一轮"两轮了——而边 25 恰是 abort 的唯一合法消费者 | 两个同源缺口一次闭环：§0.4 补 `abort` 方法行，实施计划新增任务 `M2-34` |
| G-4 | **密钥无出处**：`base.py` 规定"无任何密钥默认值"，但没有任何交付物说明本地变量从哪来；仓库连 `.gitignore` 都没有——第一次提交就有把 `.env` 带进 git 历史的现实风险 | 实施计划 `S0-3` 交付物追加 `.env.example`（全部必需变量占位）与 `.gitignore`，DoD 补"`git status` 不出现 `.env`" |

**盘查中确认不需要处理的**：① 剩余 5 项 OQ 均有"抽象先行"临时打法，不在关键路径；② Mock/真实双层环境口径 v0.4/v0.6 已闭环；③ **本仓 CI 暂不加任务**——托管平台未定（现为本地 git），`S0-7` 的 pre-commit + pytest 本地门即当前权威验证门；将来落到 GitLab/GitHub 托管时再在变更记录里补一条任务，避免为一个还不存在的平台写流水线定义；④ `docs/spikes/` 目录随 `S0-1` 交付自然创建，无需预留。

本轮后设计文档进入**冻结观察**状态：开发期只允许"发现文档与实现冲突时按 `/spec` 的 Hard Stop 流程修订"，不允许顺手改设计。

### 第五轮（2026-09-23）：S0-1 spike 实据修订

本轮即第四轮所述冻结通道的**第一次启用**：S0-1 spike（`docs/spikes/s0-1/report.md`）对 §2.3 假设逐条取证，5 项不符按 Hard Stop 呈报、产品拍板后修订。迁移表 31 边零触碰。

| # | 原假设 | 实据（证据路径见 report 附D） | 处理 |
|---|---|---|---|
| F-1 | "event_handler 在独立线程回调" | 同步 `run()` 回调在调用线程（全量 MainThread） | §2.3 图改注：**生产定死 `run_async()`**；"不许在 handler 里同步 INSERT"的理由链在 async 下不变 |
| F-2 | 白名单 7 kind 覆盖所需事件 | loop 逐项结果全部走白名单外的 `runner_item_on_ok`（压测 5000/5000 实证） | 白名单增补 `runner_item_on_*` 族；三成员未触发，**S0-2 故障注入补验** |
| F-3 | `result_status` 的 RESCUED 可由事件流判出 | 被 rescue 的 failed 事件无任何事件级标志；仅 `stats.failures 不含 rescued 尝试` + `致命失败踢出 host` 两个聚合锚点 | **证伪**。产品拍板方案 **③′：终态计数回填 + 对账**，对账不等全部判 FAILED 并告警（规则全文见 §2.3） |
| F-4 | `RunEvent` 有 stderr 列 | payload 无独立 stderr，错误文本混在 stdout(ANSI)/`res.msg/exception` | §2.3 归一化行改注"提取"语义 |
| F-5 | item 行有 duration | item 事件 `duration=None` | §2.1 `duration_ms` item 行允许 NULL |

**顺带产出（无需设计变更，但改变了 S0-2/M1 的已知边界）**：`seq` 可直接复用 `counter`（严格单调 1:1）；事件级自带 stdout 全文，§2.4① 懒加载成立且强于假设；速率 ≈550 ev/s 无反压；`cancel_callback` 在正常跑完时也被周期调用（**取消判定必须显式置标志位**，已列 S0-2 交接清单）；黑洞主机 unreachable 实测 10.03s（`ConnectTimeout=3` 被 ssh 重试放大到 ~10s）——超时预算按 ≥10s/主机 设。
