# PRD.md — TomatoLite 发布闭环平台产品需求文档

| 项目 | 内容 |
|---|---|
| 产品名称 | TomatoLite |
| 文档版本 | v1.0（PRD 定稿候选） |
| 编写日期 | 2026-09-17 |
| 文档状态 | 待评审 |
| 读者 | 平台开发、运维、测试、业务方接口人 |

---

## 1. 背景与目标

### 1.1 背景

当前环境已有 GitLab（运维侧脚本/Playbook/K8s 编排文件资产库 + Webhook 触发入口）、Jenkins（发布流水线）、Ansible（运维作业执行）三类基础设施，但缺少统一平台对发布过程做**单据化、状态化、数字化**管理，存在以下痛点：

- 发布动作分散（GitLab 直推、手工 Ansible），过程无统一记录
- 发布结果依赖人工判断，探活、日志、应用状态检查靠口口相传
- 无强制闭环机制，发布完成与否、业务是否确认无据可查
- 无度量数据，无法回答"发布成功率/回滚率/平均时长"类问题

### 1.2 产品定位

> 小而美的发布闭环平台 —— 以 CMDB 为地基，包住既有 GitLab→Jenkins 链路与 Ansible 作业，让每一次发布从发起到确认全程数字化。

### 1.3 设计三铁律（不可违反的边界纪律）

1. **不重造轮子**：构建归 Jenkins、执行归 Ansible、资产归 GitLab；TomatoLite 只做 CMDB、单据状态机、闭环数字化
2. **CMDB 唯一事实源**：所有主机/应用/环境数据只存 CMDB，任何模块禁止私建主机表
3. **无闭环不算完成**：发布单未走完"验证 + 反馈"不允许到达终态

### 1.4 目标与非目标

**目标（In Scope）**
- 发布单全生命周期状态机管理（双轨：应用发布 / Ansible 作业）
- 包住既有 GitLab 提交触发 → Jenkins 流水线链路，**Jenkins 流水线零侵入（仅新增通知 stage）**
- Ansible 作业平台化执行，task 级实时展示
- 发布后自动验证：探活、日志检查、应用状态回显
- 业务反馈确认闭环 + 一键回滚
- 发布度量与报告

**非目标**
- 不做 CI 构建能力（Jenkins 专职）
- 不做自研 Agent（一期 SSH 模式）
- 不做代码托管 / MR 评审（开发侧不使用本 GitLab）
- 不做多租户、插件市场、可视化拖拽编排（长期再议）

---

## 2. 名词定义

| 术语 | 定义 |
|---|---|
| 发布单 | 平台内一次发布/作业的唯一凭证，编号 `TL-YYYYMMDD-NNN` |
| 通道 A | 应用发布：平台代提交清单 → GitLab Webhook → Jenkins 流水线 |
| 通道 B | Ansible 作业：平台内置 ansible-runner 直接执行 |
| 代提交 | 平台以服务账号通过 GitLab API 向入口 Project 提交发布清单文件 |
| 旁路单（BYPASS） | 人工直推 GitLab 触发的发布，由 Webhook 旁听自动补录的单据 |
| 发布清单 | 入口 Project 中的 `releases/TL-xxx.yaml` 文件，触发布的唯一载体 |
| 闭环 | 发布单达到 SUCCESS（验证通过 + 业务确认，**或**人工强制放行）。**闭环不等于不可撤销**：闭环后可发起事后回滚，单据转入 `ROLLBACK → ROLLED_BACK` |

---

## 3. 用户角色与权限（RBAC）

| 角色 | 权限 |
|---|---|
| 管理员 | CMDB 维护、用户/权限管理、全局配置 |
| 运维 | 创建/审批/执行发布单与作业、回滚操作 |
| 开发 | 查看、发起（是否可执行生产环境待定，见开放问题 OQ-2）|
| 业务方 | 发布后确认/驳回反馈 |
| 系统账号 | GitLab API 代提交、Jenkins 回调接收、Webhook 接收 |

职责分离硬约束：**同一用户不得同时担任同一发布单的发起人和审批人**。

---

## 4. 系统总体架构

```
                      ┌──────────────────────────────────────┐
  发布发起/旁听落单 ──▶ │         TomatoLite 控制面             │
                      │  CMDB │ 状态机 │ 验证引擎 │ 度量报告   │
                      │  Django + DRF │ PostgreSQL │ Redis    │
                      └───┬─────────────────┬────────────────┘
                          │ GitLab Files API │ ansible-runner
             ┌────────────▼────┐      ┌─────▼─────────────┐
             │ GitLab 入口Proj. │      │  通道B: Ansible    │
             │ (releases/*.yaml)│      │  Playbook 从GitLab │
             │      │ webhook   │      │  Inventory 由CMDB  │
             │      ▼           │      │  生成, task级回传   │
             │ Jenkins 流水线    │      └───────────────────┘
             │ (post.always 通知 │
             │  回调 TomatoLite) │
             └──────────────────┘
                发布后验证 → 探活/日志/状态回显 → IM确认 → 闭环
```

**已冻结的关键技术决策**

| 决策项 | 结论 |
|---|---|
| 入口 Project 触发约定 | **提交即触发**（commit 级）|
| Jenkins 状态回传 | **回调为主**（notify stage）+ **轮询对账兜底**（sweeper）|
| 数据库 | PostgreSQL（JSONB 存事件载荷/时间线）|
| 后端/前端 | Django + DRF / Vue 3 + Element Plus（如团队栈冲突见 OQ-6）|
| Ansible 调用 | ansible-runner 程序化调用，事件流回传 |
| 实时日志 | WebSocket |
| 部署形态 | 一期单机 Docker Compose |

---

## 5. 功能需求

> 优先级定义：P0 = MVP 必须；P1 = M2 交付；P2 = M3 交付。

### 5.1 CMDB 模块（FR-CMDB）

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-CMDB-01 | 业务系统 → 应用 → 环境 → 主机 四层模型 CRUD 及 API | P0 |
| FR-CMDB-02 | 应用级验证配置：探活 URL/期望码、日志路径、日志错误关键字、K8s workload 名、部署形态 | P0 |
| FR-CMDB-03 | 应用命名规范校验（禁止同义混名，如"电子银行/电子渠道"）| P0 |
| FR-CMDB-04 | Ansible Inventory 由 CMDB 动态生成（禁止手写主机列表）| P0 |
| FR-CMDB-05 | 主机与 CMDB 变更历史记录 | P1 |
| FR-CMDB-06 | CMDB 数据导入（存量迁移）工具 | P1（依赖 OQ-5 调研结果）|

### 5.2 发布单与状态机（FR-TKT）

**统一状态机（双轨共用主干）：**

```
DRAFT → SUBMITTED → APPROVED → EXECUTING → VERIFYING → CONFIRMING → SUCCESS
(草稿)   (待审批)    (已批准)    (执行中)     (验证中)    (待业务确认)   (闭环✔)
            │↓REJECTED  ↘ PAUSED ↗        │              │              │
              (驳回)     (暂停观察点)       │              │              │ 事后回滚
                                          ↓              ↓              ↓
                                   VERIFY_FAILED   VERIFICATION_ERROR  ROLLBACK
                                    (应用坏了)       (平台够不着)          │
                                          │              │                ↓
                                          ↘ ROLLBACK ────┘          ROLLED_BACK
                                                 ↓
                              ┌──────────────────┴──────────────────┐
                              │ 人工强制放行（≥审批人级别，须填理由）│
                              └──────────────────┬──────────────────┘
                                                 ↓ 回到 CONFIRMING → SUCCESS
BYPASS(旁路自动落单) ────────────────→ 自动流转至 EXECUTING，走相同后半程
                                        （建单后立即自动推进，无需人工确认）
```

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-TKT-01 | 发布单类型：`APP_RELEASE`（通道A）/ `OPS_JOB`（通道B）| P0 |
| FR-TKT-02 | 发布前信息记录：发布说明（人工填写）、应用/环境、目标版本、发起人 | P0 |
| FR-TKT-03 | 状态机流转写事件表 `tl_ticket_event`（JSONB 载荷），全量留痕 | P0 |
| FR-TKT-04 | 应用级发布并发锁：同一 应用+环境 的活动单仅允许一个，后续排队 | P0 |
| FR-TKT-05 | 审批：平台内置一级审批（审批人/意见留痕）| P1 |
| FR-TKT-06 | 暂停点：流程中可配置暂停观察，人工恢复 | P1 |
| FR-TKT-07 | BYPASS 单自动创建：Webhook 收到人工直推时落单，标记 `BYPASS`，不阻断 | P0 |

**状态流转守卫（开发硬约束）：**
- 代提交仅允许从 `EXECUTING` 状态执行（**ADR-8 修正**：原为 `APPROVED`。`APPROVED` 不持有并发锁，而代提交必须持锁，该条件在新语义下永远无法满足）
- 未到达 `VERIFYING` 不得跳过验证
- `CONFIRMING` 未确认超时自动升级提醒（见 FR-FB-03）
- 所有状态迁移必须在数据库事务内完成
- `VERIFY_FAILED`（应用失败）与 `VERIFICATION_ERROR`（平台够不着）**不得合并**：前者提示回滚，后者提示先修复可达性再重试（见 ADR-6）
- `VERIFICATION_ERROR` 的出口（重试 / 回滚 / 暂停）**均需人工触发，不自动推进**
- **人工强制放行**（`VERIFY_FAILED → CONFIRMING`，ADR-10）：仅"验证不过但业务判定可接受"时使用。执行者须 ≥ 审批人级别且**不是发起人**，**必须填写理由**并留 `FORCE_RELEASED` 审计事件；进入的 `SUCCESS` 在 UI 上必须与常规成功区分，并计入**强制放行率**看板。**该入口不对 `VERIFICATION_ERROR` 开放**——"平台够不着"不允许人工放行
- **事后回滚**（`SUCCESS → ROLLBACK`，ADR-11）：闭环后发现线上问题时使用。执行者须 ≥ 审批人级别，**必须显式指定要回滚到的目标版本**（平台不默认"上一版"）。若目标应用/环境正被其他单据占用，**当场返回冲突并指名占用单据，不排队**

**关于 `APPROVED` 的语义（ADR-7 / ADR-8）**：`APPROVED` = **已批准待调度**（排队中，不持有并发锁）。真正持有 `应用+环境` 锁的是 `EXECUTING / VERIFYING / CONFIRMING / VERIFICATION_ERROR / PAUSED / ROLLBACK`。从 `APPROVED` 到 `EXECUTING` 的流转由**排队调度器唯一触发**（ADR-8）——人工点"立即执行"走的也是同一条边。

由此产生一条**对用户可见的行为约定**：审批通过**不等于立即开始发布**。若该应用环境正被另一单据占用，本单会停留在"已批准"并在队列中等待，界面上应显示其排队位次；占用的单据结束后自动晋升执行，**无需人工再次点击**。

### 5.3 通道 A：GitLab 代提交 + Jenkins 集成（FR-CHA）

**代提交规范（冻结）：**

```
文件路径:  releases/TL-20260917-001.yaml
文件内容:  单号 / 应用 / 环境 / 目标版本 / 参数 / 发起人 / 触发类型(RELEASE|ROLLBACK)
commit message: [TL-20260917-001] RELEASE app=order-center env=prod version=v1.2.3 by zhangsan
```

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-CHA-01 | GitLab Repository Files API 代提交发布清单，提交成功 → `EXECUTING` | P0 |
| FR-CHA-02 | 幂等守卫：提交前按文件路径 + message 查询 GitLab 确认未重复提交（防网络超时重试导致重复构建）| P0 |
| FR-CHA-03 | Webhook 旁听：入口 Project push 事件推送 TomatoLite（与推送 Jenkins 的 webhook 并存）| P0 |
| FR-CHA-04 | 数字化主键链：单号 ↔ commit SHA ↔ Jenkins build_no 三键强关联 | P0 |
| FR-CHA-05 | Jenkins notify stage 回调：`post.always` + `retry(3)` + HMAC 签名，事件类型含 `stage_finished` / `build_finished`，成功/失败/中止均通知 | P0 |
| FR-CHA-06 | 回调接口：HMAC 校验、快速返回 200 异步处理、`(job, build_no, event, stage)` 唯一约束幂等去重 | P0 |
| FR-CHA-07 | 对账 sweeper：每 1~2 分钟扫描 `EXECUTING` 超时单，主动查 Jenkins REST API 修复漏失回调；终态迁移前 REST API 二次确认 | P0 |
| FR-CHA-08 | 终态后拉取 consoleText 归档至单据日志目录，PG 存路径 | P0 |
| FR-CHA-09 | Jenkins 阶段时间线展示 | P1 |

**GitLab Webhook 安全要求**：secret token 校验；回调/事件接口一律幂等。

### 5.4 通道 B：Ansible 作业（FR-CHB）

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-CHB-01 | 作业单创建：选择应用/环境（或主机集合）+ GitLab 中的 Playbook（repo/branch/path）| P0 |
| FR-CHB-02 | 执行前 shallow pull 指定 Playbook，**平台不存剧本副本，GitLab 为唯一剧本源** | P0 |
| FR-CHB-03 | ansible-runner 调用，事件流逐行回传 → WebSocket 实时日志 + 落库 | P0 |
| FR-CHB-04 | task × host 级结果记录：状态、耗时、changed、stdout/stderr 摘要 | P0 |
| FR-CHB-05 | 超时控制、并发度限制（全局 + 单应用）| P0 |
| FR-CHB-06 | Inventory 快照落库（保证复现）| P1 |
| FR-CHB-07 | 作业可绑定可选验证 Playbook，执行后自动进入验证 | P1 |
| FR-CHB-08 | 定时作业 | P2 |

### 5.5 发布后验证（FR-VFY）—— 第一重点模块

> 流水线/作业成功 ≠ 发布成功。`EXECUTING` 完成后**自动执行**，结果写入 `tl_verification`。

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-VFY-01 | 探活-主机形态：HTTP（URL+期望状态码/关键字）、TCP 端口、进程存活 | P0 |
| FR-VFY-02 | 探活-K8s 形态：Deployment readyReplicas/replicas、Pod 状态与重启次数（读法见 OQ-3）| P1 |
| FR-VFY-03 | 日志检查：按 CMDB 配置的日志路径 tail N 行，扫描错误关键字（内置 error/exception/panic + 应用自定义）| P0 |
| FR-VFY-04 | 应用状态回显：发布前后对比视图（运行版本 vs 目标版本、进程/Pod 清单、健康状态）| P0 |
| FR-VFY-05 | 验证结果流转：通过 → `CONFIRMING`；失败 → `VERIFY_FAILED` + 告警 + 回滚入口（另有**人工强制放行**出口，见 ADR-10）。**注（ADR-9）**：若一次验证中**全部**失败均为"连接类"（连不上 / DNS / 认证 / 权限），结论提升为 `VERIFICATION_ERROR` 而非 `VERIFY_FAILED`——那说明平台够不着目标，不能断言应用坏了；只有部分目标连接失败时才维持 `VERIFY_FAILED` | P0 |
| FR-VFY-06 | 验证配置随应用版本化（验证 Playbook 存 GitLab）| P1 |
| FR-VFY-07 | 发布前后日志错误率对比 | P2 |

### 5.6 反馈闭环（FR-FB）

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-FB-01 | 通道 A：验证通过后推送 IM（企微/钉钉）确认卡片，附状态回显视图，一键确认/驳回 | P1 |
| FR-FB-02 | 通道 B：发起人确认即闭环 | P0 |
| FR-FB-03 | 确认超时升级：超 X 小时未确认，提醒升级至审批人/主管（X 可配置）| P1 |
| FR-FB-04 | 反馈记录：确认人、渠道、结论、备注、时间，全量留痕 | P0 |

### 5.7 回滚（FR-RB）

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-RB-01 | 一键回滚 = 平台代提交**上一版本**发布清单，commit message 标记 `ROLLBACK`，链路完全复用通道 A | P1 |
| FR-RB-02 | 通道 B 回滚 = 执行指定回滚 Playbook | P1 |
| FR-RB-03 | 回滚单与原单关联，双向可追溯 | P1 |
| FR-RB-04 | 验证失败场景下，回滚入口在告警卡片中直接暴露 | P1 |
| FR-RB-05 | **事后回滚**（ADR-11）：已闭环（`SUCCESS`）的发布单可发起回滚，**必须显式指定目标版本**；平台校验该版本存在于本应用历史记录中 | P1 |
| FR-RB-06 | 事后回滚遇到并发占用时**返回冲突并指名占用单据**，不得静默排队 | P1 |

### 5.8 权限与审计（FR-SEC）

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-SEC-01 | RBAC + 发起/审批职责分离校验 | P0 |
| FR-SEC-02 | 全量操作审计：谁、何时、对哪台主机、执行什么、结果如何 | P0 |
| FR-SEC-03 | 所有入站回调（GitLab Webhook / Jenkins notify）签名校验 | P0 |
| FR-SEC-04 | 发布单时间线完整回放页面 | P1 |
| FR-SEC-05 | **人工强制放行的审计**（ADR-10）：`FORCE_RELEASED` 事件须含执行人、理由、当时的失败检查快照；强制放行率进度量看板 | P1 |

### 5.9 度量与报告（FR-MTR）

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-MTR-01 | 看板：发布成功率、回滚率、平均发布时长、**旁路率（BYPASS 占比）**、**强制放行率**。成功率/回滚率**按单据最终态统计**：一张“先 SUCCESS、后事后回滚”的单计入回滚率（ADR-11） | P2 |
| FR-MTR-02 | 发布报告自动生成（时间线 + 验证结果 + 反馈结论）并归档 | P2 |
| FR-MTR-03 | 按系统/环境/失败原因分类统计 | P2 |

---

## 6. 数据模型

> **本节仅为概览。** 工程级字段、约束与索引的**完整定义以 `docs/design-notes.md` §1.2（模型）/ §1.3.1（权威迁移表）/ §1.4–§1.7（事件表与锁）为准**——两边不一致时，design-notes 为准，本节有意不随工程细节演进。
>
> 日志本体存磁盘（按单据归档目录），PG 存路径 + 尾部摘要；JSONB 用于一切结构化快照。

```sql
-- CMDB
cmdb_system(id, 名称, 负责人, ...)
cmdb_app(id, system_id, 应用名, 部署形态 host|k8s, 探活url, 期望码,
         日志路径, 错误关键字[], k8s_workload, 制品仓库映射, ...)
cmdb_env(id, app_id, 环境, 集群, 变量 JSONB, ...)
cmdb_host(id, env_id, 主机名, IP, 标签, ...)

-- 单据
tl_ticket(id, 编号, 类型 APP_RELEASE|OPS_JOB, 触发方式 PLATFORM|BYPASS|MANUAL,
          app/env, 目标版本, 发布说明, 状态, 当前批次, 创建人, 审批人, 审批意见,
          时间戳组)
tl_ticket_event(id, ticket_id, 状态迁移, 载荷 JSONB, 时间)   -- 数字化原始积累

-- 通道 A
tl_pipeline_run(id, ticket_id, commit_sha, jenkins_job, build_no,
                阶段时间线 JSONB, 结果, 日志路径)

-- 通道 B
tl_ansible_run(id, ticket_id, playbook_repo, branch, path,
               inventory快照 JSONB, 汇总统计 JSONB)
tl_ansible_task(id, run_id, host, task, 结果, 耗时, changed, 日志引用)

-- 验证 / 反馈 / 报告
tl_verification(id, ticket_id, 探活 JSONB, 日志扫描 JSONB, 状态回显 JSONB,
                结论, 时间)
tl_feedback(id, ticket_id, 确认人, 渠道, 结论, 备注, 时间)
tl_report(id, ticket_id, 时间线 JSONB, 归档路径)

-- 系统回调去重
tl_callback_log(id, source, dedup_key, payload, 处理结果)  -- 唯一约束(source, dedup_key)
```

---

## 7. API 概览（供前后端与集成对齐）

```
# CMDB
GET/POST/PUT  /api/v1/cmdb/{systems,apps,envs,hosts}

# 发布单
POST /api/v1/tickets                      创建（含发布说明）
GET  /api/v1/tickets/{id}                 详情 + 时间线
POST /api/v1/tickets/{id}/approve|reject  审批
POST /api/v1/tickets/{id}/rollback        一键回滚
POST /api/v1/tickets/{id}/resume          暂停恢复
GET  /api/v1/tickets/{id}/logs            WebSocket 实时日志

# 通道 B
POST /api/v1/jobs                         创建 Ansible 作业单

# 入站回调（均需签名）
POST /api/v1/callbacks/gitlab             Webhook 旁听（push 事件）
POST /api/v1/callbacks/jenkins            notify stage 回调

# 验证与反馈
GET  /api/v1/tickets/{id}/verification    验证结果 + 前后对比
POST /api/v1/tickets/{id}/feedback        业务确认/驳回

# 度量
GET  /api/v1/metrics/dashboard
```

---

## 8. 非功能需求

| 编号 | 需求 |
|---|---|
| NFR-01 | 幂等性：所有任务节点、回调处理可安全重试且无副作用 |
| NFR-02 | 平台自身故障不得导致"既不能发布也不能回滚"：直推 GitLab 始终是逃生通道（由 BYPASS 事后补录）|
| NFR-03 | 日志保留策略可配置（默认 90 天，待 OQ-7 确认）|
| NFR-04 | 单发布单时间线完整可回放，数据不可篡改（append-only 事件表）|
| NFR-05 | 并发：通道 B 支持全局并发度限制；状态机事务保证无脏数据 |
| NFR-06 | 平台操作审计满足等保检查项 |

---

## 9. 里程碑与验收标准

三个阶段**串行推进，顺序固定为 M1 → M2 → M3**。本表**不承诺日期、不估工期**——项目由 AI 编码完成，时间不是约束，**顺序与依赖才是**；任务级的粒度、依赖关系与风险等级见 `docs/implementation-plan.md`。

| 阶段 | 交付内容 | 验收标准（DoD） |
|---|---|---|
| **M1** | CMDB（含验证配置字段）+ PG 状态机/事件表 + 通道 B（ansible-runner + task 级实时展示）+ GitLab Webhook 旁听落单 | ① 一个 Ansible 作业全流程落单、实时日志、task 级结果展示；② 手动直推 GitLab 被自动记录为 BYPASS 单；③ CMDB 命名规范校验生效 |
| **M2** | 通道 A（代提交 + 幂等守卫 + 并发锁 + 回调 + sweeper）+ 发布后验证三件套（探活/日志/状态回显）+ IM 确认闭环 + 一键回滚 + **事后回滚** + 审批 + **人工强制放行** | ① 发布单从平台发起 → GitLab 代提交 → Jenkins → 自动验证 → IM 确认 → 闭环，全程数字化可回放；② 模拟回调丢失，sweeper 可修复；③ 验证失败自动告警且一键回滚成功；④ 验证失败的单可经受权人**强制放行**至 `CONFIRMING` 闭环并留下旁路留痕，且该入口对 `VERIFICATION_ERROR` 单**必须拒绝**；已闭环的 `SUCCESS` 单可发起**事后回滚**，回滚后最终态为 `ROLLED_BACK`、计入回滚率而非成功率 |
| **M3** | 度量看板（含旁路率、**强制放行率**）+ 发布报告归档 + 定时作业 | 能回答：上季度发布成功率、回滚率、平均时长、旁路率及趋势、失败原因 Top N |

---

## 10. UI 需求（含番茄状态可视化）

- 单据列表/详情以番茄状态标识：
  - 🍅 青番茄 = EXECUTING　🍅 半红 = VERIFYING
  - 🍅 全红饱满 = SUCCESS（熟了）　🥀 蔫了 = 已回滚　🐛虫眼 = VERIFY_FAILED
  - 🍅 全红 + **⚠️ 人工放行角标** = SUCCESS **但经验证失败人工强制放行**（ADR-10）。**必须与干净的成功区分**——两者混同则强制放行率失去意义，看板上会看不出哪些"成功"是担下来的
  - 🍅 全红 + 🥀 **双标** = 曾成功、后经事后回滚（ADR-11），时间线上保留两段
  - 🌿 枝叶角标 = BYPASS 旁路单（一眼区分平台单/野生单）
  - ❓"平台看不清" = `VERIFICATION_ERROR`（与 🐛虫眼 语义相反，**不得共用图标**）
- 验证结果页核心区：**发布前后对比视图**（版本、进程/Pod、健康状态），置于业务确认按钮旁
- 实时日志：通道 A 展示 Jenkins 阶段推进，通道 B 展示 task × host 矩阵

---

## 11. 开放问题（Open Questions）

| 编号 | 问题 | 影响 | 状态 |
|---|---|---|---|
| OQ-1 | 入口 Project 与脚本/Playbook 资产库是否物理分离？（若同库，改 Playbook 会误触发 Jenkins）| 阻塞 M2 开发 | ✅ **已确认：物理分离** |
| OQ-2 | 应用发布是否强制审批门禁？开发可否自助发生产？ | M2 审批设计 | ✅ **已确认：生产强制审批，开发不可自助发生产** |
| OQ-3 | K8s 状态读取：平台直连 K8s API（只读 SA）还是统一走 Ansible/kubectl？ | FR-VFY-02 实现方式 | 待定（挂在 `K8sStateReader` 抽象后，不阻塞开发） |
| OQ-4 | IM 渠道：企微 or 钉钉？机器人权限申请流程？ | M2 | 待定（挂在 `IMNotifier` 抽象后，不阻塞开发） |
| OQ-5 | CMDB 存量数据现状：从零建 or 迁移？ | M1 导入工具 | 待调研 |
| OQ-6 | 后端语言终审：Django（与 Ansible 同栈，推荐）or 团队既有栈？ | M1 开工 | ✅ **已确认：Django + DRF + Vue3 + Element Plus** |
| OQ-7 | 日志保留时长、是否对接 Loki/ELK？ | NFR-03 | 待定 |
| OQ-8 | 旁路率治理目标值（如 <10%）| M3 度量 | 待定 |
| OQ-9 | 验证阶段的"平台故障"是否需要独立终态？ | FR-VFY-05、状态机 | ✅ **已确认：新增 `VERIFICATION_ERROR`，见 ADR-6** |

---

## 12. 已冻结决策记录（ADR）

| # | 决策 | 理由 |
|---|---|---|
| ADR-1 | 入口触发约定 = 提交即触发 | 现有链路约定，Jenkins 零改动 |
| ADR-2 | Jenkins 回传 = 回调为主 + 轮询对账兜底 | 事件驱动实时性 + stage 级粒度；sweeper 抗丢事件；推送保实时、拉取保正确 |
| ADR-3 | 数据库 = PostgreSQL | JSONB 契合事件载荷/时间线/验证快照 |
| ADR-4 | 回滚 = 代提交上一版本清单 | 链路复用，无需额外通道 |
| ADR-5 | 旁路不阻断、事后补录 | 保证习惯平滑迁移与逃生通道 |
| ADR-6 | 验证阶段新增独立终态 `VERIFICATION_ERROR`，与 `VERIFY_FAILED` 分开 | 基础设施故障（SSH 不可达、K8s API 403、日志路径失效）若混入 `VERIFY_FAILED`，会驱动运维**回滚一个其实成功的发布**——"验证引擎反而制造故障"。分开后：`VERIFY_FAILED` 提示回滚，`VERIFICATION_ERROR` 提示先修可达性再重试，且 M3 失败原因统计能正确归因 |
| ADR-7 | `APPROVED` = 已批准待调度（排队中，**不持有**并发锁） | 若 `APPROVED` 即持锁，"批准了但排在队列里"的单据会占住 `应用+环境` 锁数小时，**排队本身成为阻塞**。锁的持有者语义上应是执行方而非审批动作 |
| ADR-8 | `APPROVED → EXECUTING` 由**排队调度器唯一触发**；代提交在 `EXECUTING` 态下工作 | ADR-7 的必然推论。既然 `APPROVED` 不持锁而代提交必须持锁，两者不可能同时成立——旧守卫"代提交仅允许从 APPROVED 执行"在新语义下永远无法满足。且若允许代提交任务自己晋升，它会与调度器**争抢同一条边**，锁被占时二者行为不一致 |
| ADR-9 | 一次验证中**全部**失败均为"连接类"时，结论提升为 `VERIFICATION_ERROR` 而非 `VERIFY_FAILED` | 连接类失败（连不上 / DNS / 认证 / 权限）报的是"平台到目标这条路径不通"，与应用是否健康无关。平台网络一抖就会批量误报 `VERIFY_FAILED` 并弹出回滚入口——**这正是 ADR-6 要防的那件事在探活路径上的复发**。只有部分目标连接失败时才维持 `VERIFY_FAILED`（那才是"应用只挂了一部分"的合法信号） |
| ADR-10 | 开放 `VERIFY_FAILED → CONFIRMING`（人工强制放行） | 业务确实存在"验证不过但可以接受"的情形（探活配置滞后、非关键检查项、已人工复核）。不开这条边的后果是逼运维绕过平台直接上，反而失去留痕。代价是削弱 `SUCCESS` 语义，故用四条约束对冲：权限 ≥ 审批人且非发起人、理由必填、UI 区分、计入强制放行率。**不对 `VERIFICATION_ERROR` 开放** |
| ADR-11 | 开放 `SUCCESS → ROLLBACK`（事后回滚），**`SUCCESS` 不再是终态** | 发布后运行一段时间才发现问题是最常见的回滚场景，而原设计只覆盖"失败后回滚"。代价是 `SUCCESS` 从终态变成可再入节点（终态 4→3），度量须按最终态统计；并且必须显式指定目标版本——事后场景下"上一版"未必是想回到的那一版 |
