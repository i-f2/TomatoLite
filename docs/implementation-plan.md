# TomatoLite 实施进度总表

| 项目 | 内容 |
|---|---|
| 文档 | 实施进度总表（任务台账） |
| 版本 | v0.18 |
| 日期 | 2026-09-24 |
| 上游依据 | `PRD.md` v1.0（含 ADR-6 ~ ADR-11） |
| 配套文档 | `docs/design-notes.md` v0.9（工程设计说明） |
| 任务数 | 101（不含 8 条里程碑验收项） |
| 风险分布 | L3×25 / L2×24 / L1×52 |
| 验证分布 | 自动断言×83 / 故障注入×8 / 人工观察×10 |
| 状态 | 待认领 —— 剩余 5 项待拍板项**均不在关键路径**，见 §阻塞项汇总 |

> **本表不估人天。** 项目由 AI 编码完成，工期不是约束；任务行给出的是**风险等级**与**验证强度**两列，口径见 §阶段总览与关键路径。v0.3 及以前的人天数字已从任务行与统计中移除（§变更记录保留原文，仅供追溯），理由见该表 v0.4 行。

---

## 本文档的定位

`PRD.md` 回答**做什么**，`docs/design-notes.md` 回答**怎么做**，本文档回答**按什么顺序做、谁做、怎么算做完**。

每个任务都可独立认领、独立验收：有 ID、有依赖、有一行风险等级、有一行验证强度、有一条可执行的验收方式。任务描述里凡涉及"为什么这么设计"的部分，都指向 `design-notes.md` 的对应章节，本文档不重复推演。

**使用方式**：从台账中挑选任务（可跨里程碑、可并行），对单个任务出具详细 Spec（风险等级、允许修改的文件边界、接口/模型变更、分步计划、验证命令），Spec 确认后才动代码。

---

## 决策快照

### 已拍板

| 项 | 结论 | 影响 |
|---|---|---|
| 技术栈（OQ-6） | Django + DRF + PostgreSQL + Redis + Celery + Channels；前端 Vue 3 + Element Plus | 全量任务按此栈编写 |
| 入口 Project 分离（OQ-1） | **物理分离**：触发仓库与 Playbook/脚本资产库是两个 repo | 解除 M2 的最高优先阻塞；通道 A 可直接排期 |
| 生产发布审批（OQ-2） | **生产环境强制审批**，开发不可自助发生产 | 审批为硬门禁，非可选流转 |
| 联调环境 | **双层**：本地有 docker/compose 拉起的**临时真实 GitLab + Jenkins**（人工联调，`S0-9`）；PostgreSQL 用本地已装实例（v16，非容器，见 `design-notes.md` §0.6a；基线 ≥15，F-8 起，design-notes v0.9 §7 第八轮） | 自动化断言与故障注入**仍以 Mock 层为准**（`S0-6a/b/c` 不解除）——`callback_drop_rate`、"落库成功但响应超时"、"webhook 早于 API 响应"三类时序真实系统构造不出来，且临时实例**不得被任何 CI 用例依赖**（§6 第 7 条）。见 Phase 0-G |
| **D-2** `APPROVED` 语义 | **已批准待调度（排队中，不持有锁）** | `APPROVED` 不入部分唯一索引谓词；持有态为 `EXECUTING / VERIFYING / CONFIRMING / VERIFICATION_ERROR / PAUSED / ROLLBACK`。见 ADR-7 |
| **OQ-9** 验证阶段独立终态 | **新增 `VERIFICATION_ERROR`** | 状态机 13→14 态；`M2-16` 拆出 `M2-16a`；`M2-17`/`M2-17a`/`M3-01`/`X-03` 相应调整。见 ADR-6 |
| **边 29** 人工强制放行 | **开放** `VERIFY_FAILED → CONFIRMING` | 新增 `M2-27a`（L3）；`M1-09` 守卫、`X-03` 角标、`M3-01` 强制放行率相应调整。守卫与 `VERIFICATION_ERROR` 出口**不得复用**。见 ADR-10 |
| **边 30** 事后回滚 | **开放** `SUCCESS → ROLLBACK`，**`SUCCESS` 不再是终态** | 终态 4→3；新增 `M2-28a`（L3）；`M3-01` 改为按最终态统计；`M2-27` 上调 L2。见 ADR-11 |

> 以上决策均已同步至 `PRD.md` §5.2、§5.7~§5.9、§10、§12（ADR-6 ~ ADR-11），`design-notes.md` §7 有完整取舍分析（含第三轮两条边的开放理由与对冲约束）。

### 仍待拍板

| 项 | 阻塞任务 | 不拍板时的临时打法 |
|---|---|---|
| OQ-3 K8s 状态读取方式 | `M2-18` | 先落 `K8sStateReader` 抽象接口 + 假实现，真实现后置 |
| OQ-4 IM 渠道（企微/钉钉） | `M2-23`、`M2-24`、`M2-31` | 先落 `IMNotifier` 抽象 + 控制台/Mock 渠道，渠道实现后置 |
| OQ-5 CMDB 存量数据现状 | `M1-06` | 导入工具后置，M1 先手工录少量种子数据 |
| OQ-7 日志保留/Loki | `M3-05` | 保留策略常量可配置，默认 90 天 |
| OQ-8 旁路率治理目标值 | `M3-08` | 看板先出数，不出告警阈值 |

> **剩余 5 项均不在关键路径上**，且都有"抽象接口先行"的临时打法，不构成开工阻塞。

---

## 阶段总览与关键路径

```
Phase 0  地基与 Spike                12 任务   L3×2  ── 决定通道 B 的实现形态
   └─ S0-1 ansible-runner 事件流 spike ─┐
   └─ S0-2 Channels/Celery 桥接 spike ──┤ 这两个 spike 决定通道 B 的实现形态
   └─ S0-3 工程骨架 ──┬── S0-3a 队列/worker 拓扑
   └─ S0-4 app 划分   ├── S0-8 本地强制 PG（本地实例 §0.6a）
   └─ S0-5 RBAC 基座  ├── S0-7 质量门
   └─ S0-9 临时真实联调栈（人工，§0.6b）
   └─ S0-6a/b/c Mock GitLab/Jenkins/目标主机（自动化基座，最高优先）

M1  CMDB + 状态机 + 通道B + 旁路落单     31 任务   L3×12  关键路径：状态机/锁 → 通道B → 实时日志
M2  通道A + 验证 + 闭环 + 回滚 + 审批    43 任务   L3×10  关键路径：代提交 → 回调/sweeper → 验证 → 确认
M3  度量 + 报告 + 定时作业                8 任务   L3×0
横切 前端工程 / 契约 / 部署               7 任务   L3×1   （与 M1–M3 并行）
──────────────────────────────────────────────────────────────────
合计                                     101 任务  L3×25   自动断言 83 / 故障注入 8 / 人工观察 10
```

**本表不排工期。** 项目由 AI 编码完成，时间不是约束，**顺序与依赖才是**——上表的任务是并行认领的粒度单位，串行关系只看任务行的"依赖"列与下面的关键路径。

**全局关键路径（决定交付顺序）：**
`S0-6 Mock 层` → `M1-09 状态机引擎` → `M1-10 并发锁` → `M1-10b 排队晋升` → `M1-14 runner 执行器` → `M2-02 代提交` → `M2-06 sweeper` → `M2-11~15 验证` → `M2-23 确认卡片`

**必须先做、不能后置的两件事：**

1. `S0-1` / `S0-2` 两个 spike —— 若 ansible-runner 事件无法在 Celery prefork worker 内稳定回传，通道 B 的整个设计要改（`design-notes.md` §2.3），越晚发现代价越大。
2. `S0-6a/b/c` Mock 层 —— 在三个 Mock 服务可用之前，**M1 与 M2 的联调类任务全部无法验收**。

**两列口径说明**

本项目由 AI 编码完成，**不估人天**。任务行上的两列回答的是 AI 编码时真正会踩坑的两个问题：这一步错了有多难查，以及做完之后**谁来判断它真的对了**。

**风险等级** —— 衡量"做错了的爆炸半径"，不是工作量：

| 等级 | 含义 | 编码要求 |
|---|---|---|
| `L1` | 局部。错了只影响本任务自己的功能 | 常规实现 + 常规单测 |
| `L2` | 跨模块。错了会让别的模块行为不对，但不会静默损坏数据 | 需在任务 Spec 中列明受影响的调用方 |
| `L3` | 高风险 / 架构 / Infra。错了会产生**静默的数据或状态损坏**（丢锁、重号、重复执行、审计断链、权限绕过） | 必须先写失败用例再写实现；禁止"看起来对就提交" |

`L3` 共 25 条，集中在四处：**并发与锁**（`M1-10`/`10a`/`10b`/`18`/`18a`/`19`/`19b`）、**幂等与去重**（`M1-23`/`M1-25`/`M2-03`/`M2-05a`/`M2-06`/`M2-06a`）、**审计链与签名**（`M1-08`/`M1-11`/`M2-05`）、**安全门禁**（`M1-24`/`M2-25`/`M2-26`/**`M2-27a`**/**`M2-28a`**——后两条是 ADR-10/ADR-11 新增的两条特权边，都要求"权限 + 理由/目标版本 + 审计"三者齐备，写错的后果是**绕过了本该拦住的操作**），与 `design-notes.md` 的失败模式清单一一对应；余下 4 条（`S0-6b`/`S0-7`/`M2-17a`/`X-06`）是测试与 Mock 底座本身——底座错了，上面所有 `L3` 的验收都会变成假绿。

**验证强度** —— 衡量"验收动作由谁执行"，这一列对 AI 编码是硬约束：

| 取值 | 含义 | 对任务的约束 |
|---|---|---|
| `自动断言` | AI 自己跑一个命令、自己判 PASS/FAIL | **该任务的 DoD 必须是一条 AI 能独立执行的命令**（pytest / migrate / curl / 断言脚本），不接受"界面看起来对" |
| `故障注入` | 需要先构造故障（kill 进程、断网、超时、丢回调）再断言 | DoD 必须给出**具体的注入手段**（改哪个开关、kill 哪个进程），否则无法验收 |
| `人工观察` | 必须有人在界面或报告上看一眼 | DoD 必须写明**人在看什么**；此类任务 AI 只能做到"自测通过"，不得自行判定完成 |

`自动断言 + 故障注入` 合计 90 条（90%），即绝大部分任务 AI 可以自行闭环验收；剩下 10 条 `人工观察` 是前端页面、演练报告与本地真实实例联调（`S0-9`），需要人过目。

> 两列均为**首轮分类**：按任务描述关键词初判，再对安全门禁与核心链路做人工修正。**单项任务的 Spec 可以提高本任务的等级**（例如实现时发现某 `L1` 任务实际会写共享状态），但不得降低——降低需要在此表变更记录中留下理由。

---

## Phase 0 — 地基与技术验证

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| S0-1 | **ansible-runner 事件流 spike**：本地跑通一个 playbook，抓取 `runner_on_ok/failed/skipped`、`playbook_on_task_start` 等事件原始结构与字段 | — | L1 | 自动断言 | spike 报告 + 事件样例 JSON 落 `docs/spikes/`。**✅ 已完成（2026-09-23），报告 `docs/spikes/s0-1/report.md`——5 项假设修订待拍板（F-1~F-5）** |
| S0-2 | **Celery + Channels 事件桥接 spike**：验证 runner 事件在 prefork worker 内如何推至 Redis channel layer 的 WebSocket group；验证 flush/buffering 行为 | S0-1 | L1 | 自动断言 | 最小 demo：浏览器实时看到逐行输出。**✅ 已完成（2026-09-23），报告 `docs/spikes/s0-2/report.md`——端到端 5006/5006 零丢、保序、取消/收口两路皆验；1 项证伪待拍板（F-6：channels_redis 4.3 无消息尺寸兜底，截断成为唯一防线）** |
| S0-3 | **工程骨架**：`docker-compose`（Redis7 + app + **按队列拆分的 worker 组** + beat；**PG 不进 compose**，用本地已装实例，最低版本 **15**（F-8 上调；本任务验收时台账原文为 14，见变更记录 v0.14），口径见 `design-notes.md` §0.6a）、Django 项目、settings 分片（base/local/test/prod）、DRF、drf-spectacular、Celery、Channels、pytest-django。**密钥交付物（第四轮补）**：`.env.example` 列出全部必需变量占位（DB/Redis/HMAC key/GitLab token/S0-9 凭据位），仓库根 `.gitignore` 至少含 `.env`、`var/`、`__pycache__`、`node_modules`——`base.py` 无密钥默认值（§0.6），不交付模板等于逼使用者把真密钥写进受版本控制的文件 | — | L1 | 自动断言 | `docker compose up` 起服务；`/api/schema/` 可访问；**`cp .env.example .env` 后 `manage.py check` 通过**；`git status` 不出现 `.env`。**✅ 已完成（2026-09-24）**——五步 DoD 全绿；负断言（缺 .env 即 ImproperlyConfigured）同验；**S0-2 移交断言闭环：Linux 容器 prefork 无 `FORKED_BY_MULTIPROCESSING` 三队列 probe 全消费、零 ValueError → 该 env 系 macOS 本地开发专属缓解，生产部署不需要** |
| S0-3a | **队列与 worker 拓扑**：按 `callback.process / exec.runner / verify.runner / sweeper+notify+default` 拆池；`exec.runner` 必须 `--prefetch-multiplier=1`；beat **单实例**（依据 §0.5） | S0-3 | L2 | 故障注入 | 拓扑断言确认（`scripts/s0_3a_topology_check.py`，F-7 口径）：正例 12×3s → `max_active==3` 且队列未被预取独占（`LLEN>0 ∧ reserved≤3`）；注入段 prefetch=4 唯一消费者 → 占用放大 `LLEN==0 ∧ reserved==9 ∧ max_active==3`（预取放大占用≠放大并发）；4 节点消费队列集合逐字等于 §0.5 且无多余节点；beat 容器数 ==1。**✅ 已完成（2026-09-24）**——四段全绿：正例 12×3s `max_active=3∧LLEN=6∧reserved=3`；注入段 prefetch=4 `LLEN=0∧reserved=9∧max_active=3`、拆场后基线复跑回绿；queues 按节点名逐字 4 池无多余；beat==1。**F-7 修订口径落地**（原「max_active>3」经取证证伪——占用放大≠并发放大，见 design-notes v0.8 §7 第七轮） |
| S0-4 | **Django app 划分落地**：`core / cmdb / tickets / channels_a / channels_b / verification / feedback / metrics` + `integrations/`（零 Django 依赖，§0.2）；统一 BaseModel（时间戳/软删）、统一响应包装与异常处理、统一分页 | S0-3 | L1 | 自动断言 | 每个 app 有占位 health 接口全绿；断言 `integrations/` 不 import Django。**✅ 已完成（2026-09-24）**——容器实测 8×health 200；envelope 拍板默认落地（code 字符串 slug + HTTP 真状态码解耦、request_id uuid4hex、分页 page_size=20、data={items,page,page_size,total}）；`AbstractBaseModel` 软删基座（四字段 + 默认 Manager 隐删 / `all_objects` 全量 / delete=软删 / `hard_delete` 显式）；`integrations/{gitlab,jenkins,im,k8s}` §0.4 签名 ABC + DTO（`BuildResult` 六值含 NOT_FOUND 基建/业务分流）+ NotImplementedError 占位；**零 Django 双重断言绿**（源码 grep ＋ 无 settings 子进程 `'django' not in sys.modules`）；探针任务兑现 S0-3 承诺迁 `apps/core/tasks.py`（`config.probe`→`core.probe`、`core.slow_probe` 同名），`autodiscover_tasks()` 生效，S0-3a 拓扑回归复跑全绿 |
| S0-5 | **认证与 RBAC 基座**：自定义 User（`admin/ops/dev/business/system`）、DRF 权限类、职责分离校验工具函数 | S0-4 | L2 | 自动断言 | 单测：各角色访问矩阵 + "发起人≠审批人" 断言。**✅ 已完成（2026-09-24）**——`core.User`（AbstractUser×AbstractBaseModel，`tl_user`，role 必填无默认）+ 软删即除权；session+ModelBackend（拍板①）；system 强制 unusable/禁 staff（拍板②）；`HasRole`（匿名 401/缺角色 403/envelope，缺声明炸配置错误）+ `role_at_least`（system 无秩）+ `enforce_separation_of_duties`；4 位点×5 角色 20 格矩阵全枚举 33 断言。**期间触发 F-8 修订**（PG 基线 ≥14→≥15，见 design-notes v0.9 §7 第八轮与本表 v0.14 行） |
| S0-7 | **质量门**：pre-commit（ruff/black）、pytest 配置、覆盖率阈值；**并发用例一律 `TransactionTestCase` + 真实线程**（依据 §0.7） | S0-3 | L3 | 自动断言 | `pre-commit run --all-files` 通过；故意写一个假锁测试，确认它**能失败**（证明测试基础设施有效） |
| S0-8 | **本地强制 PostgreSQL**：禁用 SQLite（依据 §0.6）；本地实例为已装的 **PG16**（`/opt/local/bin/psql16`），非容器（§0.6a；F-8 起由 PG14 上调，design-notes v0.9 §7 第八轮） | S0-3 | L1 | 自动断言 | 断言 `connection.vendor == 'postgresql'` 且 `server_version >= 15`，非 PG 直接启动失败 |
| S0-9 | **本地临时联调栈**：`docker-compose.integration.yml` 拉起临时真实 GitLab + Jenkins；初始化 root token、建测试仓库与 job、配 push webhook 与 notify 端点指向平台；`local.py` 以 `INTEGRATION_MODE=integration` 切换 base_url 指向真实实例（§6 第 7 条）。**定位＝人工联调与里程碑演示，不是自动化基座** | S0-3, S0-5 | L1 | 人工观察 | **人在看**：真实 GitLab push 一次 → 平台收到 webhook 并落 BYPASS 单；真实 Jenkins job 跑完 → notify 回调**验签通过**进 `callback.process` 队列。两条都肉眼确认后记录到 spike 报告 |

### Phase 0-G — Mock 层（解阻塞，M1/M2 的前置）

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| S0-6a | **Mock GitLab**：Repository Files API（读文件、提交文件、查 commits，端点表见 §4.6）、push webhook 发送器、`_control` 故障注入 | S0-3 | L1 | 自动断言 | `curl` 能建文件并推送 webhook 到本地回调；`action:create` 遇已存在路径返回 **400**；故障注入开关生效。**✅ 已完成（2026-09-24）**——stdlib-only `mocks/gitlab_fake/`（拍板②零新依赖；主机进程 `python -m mocks.gitlab_fake --port 8061` 不进 compose，拍板①；内存态+reset，拍板③；`integrations/` 未动，拍板④对齐留 M1-13）；§4.6 端点表全覆盖：项目解析（id/urlencoded path 双口径，OQ-1 两库并种）、读文件（base64/blob_id/sha256/commit_id 字段级）、POST commits（**create 撞已存在→400 且整批零应用**=M2-02 幂等守卫断言点）、GET commits?path（新→旧+分页）；三时序构造能力（`webhook_timing`=pre→③、post→①、post+`response_hold_ms`→②，拍板⑤待 M2-03a 消费）；payload `commits[].id` 与 `after` 可独立构造（§4.7③ 去重键素材）；`X-Gitlab-Token` 请求级可覆盖（M1-24 坏 token 素材）；`deliveries` 如实记对端 200/5xx/拒连。21 新断言（8+6+7），全套 78 passed |
| S0-6b | **Mock Jenkins**：REST API（端点表见 §3.8）、notify 回调发送器（HMAC 签名，**复用生产侧 `core/hmac.py`**，§6 第 3 条）、`_control` 故障注入 | S0-3 | L3 | 自动断言 | 手动触发可送出签名回调；`callback_drop_rate` 开关可让 sweeper 场景复现。**✅ 已完成（2026-09-24）**——stdlib-only `mocks/jenkins_fake/`（主机进程 `python -m mocks.jenkins_fake --port 8062` 不进 compose，与 8061 错开；内存态+`_control/reset` 状态配置一并复位）；**生产侧 `apps/core/hmac.py` 随本任务诞生**（§6 第 3 条签名单一实现，零 Django 双断言同 `integrations/` 口径；header 拍板 `X-TL-Signature: sha256=<hex>` / `X-TL-Timestamp`，窗 ±300s；`sign()` 收非 bytes 即 TypeError——§3.3 重序列化首坑摁在类型层）；§3.8 端点表全覆盖：201 **只回报 queue_id**（映射唯一出处=queue item——边 31"绝不猜 build_no"在 mock 层同步成立）、`building=true 时 result=null 显式键`、queue `executable=null`/`cancelled`/404 三形状、consoleText 分块、wfapi 三 stage 状态随构建阶段如实、终态再 stop 返 200 `already`（§0.4）；回调发送器以**接收侧生产 `verify()` 对原始字节重算**作反假绿主证据，`bad_signature`=错密钥（双相：挂在密钥不挂在格式）、`callback_repeat` 逐字节一致（dedup 双键输入）；`_control/config` 全开关逐键校验（未知键/坏值→400 零写入），注入只打数据面、**`_control` 面永不被注入**；`callback_drop_rate` seeded RNG 同 seed 同剧本（丢失**如实记 DROPPED** 且 REST 面照样可查=sweeper 修复前提）、build hang 永不落锤**且永不回调**（关回后新触发自愈）、`trigger_response_hold_ms` 先调度后持响应=**回调抢在 201 之前**（§4.4 同型竞态素材）。30 新断言（12+7+11），全套 108 passed |
| S0-6c | **Mock 目标主机**：提供 HTTP 探活端点、可写日志文件（含轮转能力，供 §5.7 假阳性测试）、SSH 登录；可注入"版本变化/进程状态变化" | S0-3 | L1 | 自动断言 | 探活/日志扫描/进程检查三类用例可跑通。**✅ 已完成（2026-09-24）**——`mocks/host_fake/` 一面假主机四张脸：app HTTP 探活 + 裸 TCP 半开 + 真 SSH-2（`asyncssh==2.24.0`，拍板①§5.2 钦定件，本任务仅被连方）+ 独立 `_control` 端口（app 8064 / tcp 8065 / ssh 8066 / control 8067，8063 刻意弃用=6b 冒烟一次性接收器；主机进程 `python -m mocks.host_fake` 不进 compose）；**控制面独立端口=硬设计**：refuse/blackhole 整张弄死 app 端口，开关要关得回是端口级前提；§5.4.1 四形状可判互不混淆：refuse=真关听（内核 RST）、blackhole=真收了永不回（ReadTimeout）、STATUS_MISMATCH=配置码、TIMEOUT_CONNECT=消费方以 RFC5737 `192.0.2.1` 构造（拍板③：本机内核 backlog 先完成握手，mock 造不出亦绝不假装）；Step 3 实据教训：开发 shell 带 `HTTP_PROXY` 时 httpx 默认 trust_env 会把 connect-timeout 伪装成 read-timeout——探活消费方 `trust_env=False` 纪律钉进判料函数；`process_alive=False` ⇒ `effective_app_mode` 联动 refuse（进程死端口活=mock 自相矛盾），显式 http_mode 优先级低于联动；版本注入与 HTTP `/version`、SSH `cat VERSION` 同文件同出处三脸一致；日志=真文件、轮转=真 rename+重建 ⇒ **inode 真变**（§5.7③：历史 ERROR 种子下 offset 判绿 vs tail 全窗判红、rotate 后旧 offset 读空=假绿素材、inode 校验逼回退 tail、历史入 `app.log.1` 仍可查）；SSH exec 方言=Linux/GNU coreutils（生产目标全 Linux）：pgrep/systemctl is-active(exit 3)/kill -0/cat/tail/stat -c 六命令+未知命令 exit 127（§5.3 ERROR 素材），路径逃逸=不存在，纯函数 `run_command` 可脱离 SSH 断言；publickey-only、password 只立拒不问（§1132 BatchMode 首坑反面：错钥断"拒<2s"，挂起经 wait_for=可见失败）；6a/6b 同族纪律：config 逐键校验坏值 400 零写入、reset 连配置+种子、`_control` 面永不被注入、响应写包 `except OSError`。25 新断言（12+8+5），全套 133 passed；冒烟：真 OpenSSH 客户端连入 tail 同一文件 |

---

## M1 — CMDB + 状态机 + 通道 B + 旁路落单

### M1-A CMDB 模块

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| M1-01 | **CMDB 四层模型**：`cmdb_system / cmdb_app / cmdb_env / cmdb_host`，含应用级验证配置字段（探活 url、期望码、日志路径、错误关键字[]、k8s workload、部署形态 host\|k8s）与 `env.min_hosts` | S0-4 | L2 | 人工观察 | migrate 通过；Admin 可增删改查 |
| M1-02 | **CMDB CRUD API**：`/api/v1/cmdb/{systems,apps,envs,hosts}`，含层级关系与唯一性校验；**保存时做形态与校验规则交叉验证**（`deploy_mode=host` 却配了 `k8s_workload` 直接拒绝，§5.7） | M1-01 | L1 | 自动断言 | OpenAPI 契约 + pytest 覆盖 CRUD 与非法输入 |
| M1-03 | **应用命名规范校验**：禁止同义混名（如"电子银行/电子渠道"），别名冲突拦截 | M1-02 | L1 | 自动断言 | 单测：提交同义词被拒且返回明确原因 |
| M1-04 | **Ansible Inventory 动态生成器**：从 CMDB 出 inventory dict（不落临时文件），并落 `InventorySnapshot`（含每台 host 的 `updated_at`） | M1-02 | L1 | 自动断言 | 单测：给定 env 生成的 inventory 与预期一致；无主机时报错而非空跑 |
| M1-05 | CMDB 变更历史（P1） | M1-02 | L1 | 自动断言 | 改一条记录产生一条历史 |
| M1-06 | **CMDB 存量导入工具（P1）** ⚠️ **阻塞于 OQ-5** | M1-01 | L1 | 自动断言 | 导入样本文件后数据与源一致 |
| M1-07 | CMDB 前端页面（四层树 + 表单 + 命名校验提示） | M1-02, X-01 | L1 | 人工观察 | 手工走查：建/改/删全链路 |

### M1-B 单据状态机

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| M1-08 | **`tl_ticket` + `tl_ticket_event` 模型**：事件表 append-only 三重强制（DB 权限 + 触发器 + 哈希链，§1.7）；`Ticket` 须含 `verify_attempts`（`guard_verify_attempts` 的唯一计数来源）与 `paused_from_state`（边 24 恢复的目标态，**权威来源是字段**，事件 payload 的 `from_state` 仅供审计） | S0-4 | L3 | 自动断言 | migrate 通过；集成测试断言 `has_table_privilege('tl_ticket_event','UPDATE') = false`；`paused_from_state` 与事件 payload `from_state` 不一致时**必须告警**（有人绕过 `apply()` 的证据） |
| M1-09 | **状态机引擎**：`TRANSITIONS` 纯数据表 + guards + 单一入口 `apply()`（§1.4）。表内容**必须与 §1.3.1 权威迁移表逐行一致**（31 条边 + 3 个终态）；`requires_lock=True` 的边**必须传 `lease`**，并在 `SELECT FOR UPDATE` 之后校验 `lease.fencing_token == tl_app_env_lock.fencing_token`，不符抛 `StaleLease`（§1.5.2）；迁移与事件落库同事务；effects 一律 `transaction.on_commit` 包装 | M1-08 | L2 | 自动断言 | 单测覆盖 §1.3.1 表中的**每一条边**（31 条）；边 29/30 的守卫须各自独立用例——**放行守卫与 `VERIFICATION_ERROR` 的出口守卫不得是同一个函数**（两条路径语义相反，见 §5.5）；持有过期 fencing token 的调用被拒且静默退出（属正常路径，打点计数）；断言事务内不发 I/O |
| M1-10 | **并发锁（四层）**：① `pg_advisory_xact_lock`（**事务级**，非会话级）② `tl_app_env_lock` 租约行 + fencing token ③ **部分唯一索引**兜底，谓词已定：`state IN ('EXECUTING','VERIFYING','CONFIRMING','VERIFICATION_ERROR','PAUSED','ROLLBACK') AND deleted_at IS NULL`（**`APPROVED` 不在谓词内**，D-2/ADR-7）④ Redis 预检（仅体验）。抢不到锁的单进 `tl_ticket_queue` 排队（**该表与晋升逻辑在 `M1-10b` 实现**）。**加锁顺序是硬规则**：必须先 `AppEnvLock` 再 `ConcurrencySlot`，反向调用直接报错（§1.5.1） | M1-09 | L3 | 自动断言 | 并发创建第二单入队；**绕过 ORM 直插第二活动单被 DB 拒绝**；软删的活动单不占槽位；**同一 app/env 下多张 `APPROVED` 单可共存**；构造“先槽位后锁”的调用，断言其**失败** |
| M1-10a | **租约心跳 + watchdog 锁回收**：执行方每 10s 续租 + 刷 Redis 心跳键；beat 每 30s 扫失联单 → **先查心跳键**（只比时间戳会被时钟偏移坑死）→ 转 PAUSED，**绝不自动回 EXECUTING**（§1.6） | M1-10 | L3 | 故障注入 | 单测：kill 掉持有者进程后，锁在一个 watchdog 周期内被回收且单据进入 PAUSED 而非重跑 |
| M1-10b | **`tl_ticket_queue` 模型 + 排队晋升调度器**（D-2 的直接后果，§1.6a）：`dispatch_queued` 走 beat 每 10s **兜底** + 锁释放时 `on_commit` 投信号**提速**，二者缺一不可；每 `(app,env)` 每 tick 最多晋升一张；按 `(priority, seq)` FIFO；**晋升必须复用 `apply()` 而非直写 `state`**；抢锁失败静默退出不 `retry()` | M1-10 | L3 | 自动断言 | 单测：锁被占时单据停在 APPROVED 并可查"第 N 位"；释放后 ≤1 个 tick 内自动进 EXECUTING；**关掉 beat 只留事件信号时，丢一次信号即卡死**（证明兜底不可省）；低优先级单不被持续插队饿死 |
| M1-11 | **单号生成器** `TL-YYYYMMDD-NNN`：当日序号，事务内自增分配，防并发重号 | M1-08 | L3 | 自动断言 | 并发单测：100 次并发创建无重号 |
| M1-12 | 单据 CRUD API + 时间线接口 `GET /tickets/{id}` | M1-09 | L2 | 自动断言 | OpenAPI + pytest |

### M1-C 通道 B（Ansible 作业）

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| M1-13 | **`tl_ansible_run` / `tl_ansible_task` 模型** | S0-4 | L2 | 自动断言 | migrate 通过 |
| M1-14 | **GitLab shallow pull 剧本**：临时目录拉取并解析 `resolved_commit_sha`，平台不存剧本副本 | S0-6a | L1 | 故障注入 | 单测：拉取后无残留副本；GitLab 不可达时报错清晰 |
| M1-15 | **ansible-runner 执行器 + 事件泵**：`run_async` + 白名单事件归一化 → Redis LPUSH/PUBLISH → Persister 线程批量 upsert（§2.3，**白名单含 `runner_item_on_*` 族、`seq` 复用 payload.counter、RESCUED 走终态计数回填 ③′**——S0-1 实据；**尺寸双护栏 `stdout_excerpt_bytes`=4KB + 批帧 `ws_batch_max_bytes`=256KB 先到者触发 flush，layer 不兜底系 S0-2 实据 F-6**） | S0-1 ✅, S0-2 ✅, M1-13 | L2 | 自动断言 | 本地跑通 demo playbook，事件完整入库；**断言 `event_handler` 内无同步 INSERT**；**断言 rescued 对账：`n == stats.rescued+stats.failures` 时逐条判对，不等时全判 FAILED 且产生告警事件**；**注入单事件 stdout≥1MB：断言落库 excerpt≤4KB 且带 `truncated=True`、任一 `group_send` 帧≤256KB** |
| M1-16 | **事件流 → WebSocket（三层防洪水）**：① 分层——WS 只发状态迁移，全文落 artifact 文件懒加载 ② 合并——`LogCoalescer` 50 行/100ms/**256KB 字节预算三条件先到者发帧**（F-6：出口同闸） ③ 限流——超阈值只发计数帧。Consumer `connect()` 内校验 ticket 可见性（§2.4） | M1-15 | L2 | 故障注入 | 压测：200 主机 × 100 task 下 WebSocket 不崩、DB 写入不阻塞 ansible 输出；**未授权用户订阅被拒** |
| M1-17 | **task × host 结果汇总**：状态、耗时、changed、stdout/stderr 摘要（S0-1 实据：item 行 `duration_ms=NULL`；stderr 列自 `res.msg/exception`+stdout 剥 ANSI 提取，事件无独立字段） | M1-15 | L1 | 自动断言 | 与 ansible-runner 原始事件逐条比对一致（样例基线 `docs/spikes/s0-1/samples/`） |
| M1-18 | **并发度限制**：DB 信号量表 `tl_concurrency_slot`（`FOR UPDATE SKIP LOCKED` 抢占，带租约），全局 + 单应用双 scope——**双 scope 是全等于语义**：两次 `SELECT ... FOR UPDATE SKIP LOCKED` 必须都成功，任一失败即整体释放，**不允许只拿一半**。槽位行**按需创建**（应用是运行时建的，不能预置）；Celery 并发数由启动断言钉死为 `槽位数 + 1`。未拿到 → 写回 `QUEUED` 由 beat 重扫，**不用 `self.retry()`**（会占满预取位）（§2.2） | M1-15 | L3 | 自动断言 | 单测：超出并发度的作业排队而非并行；只拿到单 scope 时整体释放；worker 重启后槽位可回收；**启动时并发数 ≠ 槽位+1 直接失败** |
| M1-18a | **验证槽位预留（防分布式死锁）**：`scope="verify"` 独立命名空间并保留固定配额（默认 8）（§5.6） | M1-18 | L3 | 自动断言 | 单测：ansible 作业占满全局槽位时，验证仍能获得槽位推进 |
| M1-19 | **超时控制与强制终止（三层）**：① `run_async(timeout=)` ② stall watchdog（`last_event_at`）③ `os.killpg` 杀**进程组**（§2.6） | M1-15 | L3 | 故障注入 | 单测：超时 playbook 被终止、**无孤儿 ssh 残留**、单据状态正确 |
| M1-19a | **执行前预检 preflight**：`ansible -m ping` 剔除不可达主机并记入快照；剔除后数量 < `env.min_hosts` → **中止整单**；`host.updated_at > run.created_at` → 标 `STALE_INVENTORY`（§2.7③） | M1-04, M1-15 | L2 | 自动断言 | 单测：CMDB 中已删主机不进 inventory；剔除后低于阈值时整单中止而非部分发布 |
| M1-19b | **孤儿执行认领**：任务开头按 `runner_ident`/`runner_pid` 检测已存在的 runner —— 存活则 **adopt，绝不自动重跑**（§2.7①） | M1-19 | L3 | 故障注入 | 单测：模拟 worker 崩溃后重投，确认是 adopt 而非第二次执行 |
| M1-20 | **Inventory 快照落库（P1）**：保证复现 | M1-04, M1-13 | L2 | 自动断言 | 快照可还原出当时的 inventory |
| M1-21 | 作业单创建 API `POST /api/v1/jobs` | M1-15 | L1 | 自动断言 | OpenAPI + pytest |
| M1-22 | **通道 B 前端**：task × host 矩阵 + 实时日志 | M1-16, X-04 | L1 | 人工观察 | 手工走查：矩阵状态随执行实时变化 |

### M1-D 旁路落单与回调

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| M1-23 | **`tl_callback_log` 去重表**：唯一约束 `(source, dedup_key)` | S0-4 | L3 | 自动断言 | 重放同 payload 两次只处理一次 |
| M1-24 | **GitLab Webhook 接收接口**：`X-Gitlab-Token` **明文 token 常量时间比较**（**不是 HMAC**，§4.5）、快速 200、异步处理 | S0-6a, M1-23 | L3 | 自动断言 | 错误 token 被拒；重复推送不重复落单 |
| M1-25 | **BYPASS 单自动落单**：人工直推 → 先建 `state=DRAFT` 的单，**同事务内**立刻 `apply("TRIGGER_BYPASS_DETECTED")` 进入 EXECUTING（§1.3.1 边 2——新生单没有源态，靠这条边解决）；**抢不到锁时不得丢单**：改为落 `PAUSED`（`paused_from_state=EXECUTING`）并告警。**去重键用 push 粒度 `after`**，非 `commits[].id`（§4.4 L3） | M1-24, M1-09 | L3 | 自动断言 | 手工直推 GitLab → 平台自动出现旁路单；一次推 3 个文件只出 1 张单；**锁被占时旁路单进 PAUSED 而非丢失**；断言建单与 `TRIGGER_BYPASS_DETECTED` 在同一事务 |
| M1-26 | **旁路前置过滤 + 兜底判定**：L0 前置过滤（非入口 Project / 非触发分支 / 非 `releases/**` / 删除与新建分支）→ L2 兜底（服务账号 / commit message 模板，**命中即埋点告警**——说明 L1 已坏）→ L3 建单 | M1-25 | L1 | 自动断言 | 单测：资产库推送不落单；tag push 不落单；非 `releases/**` 推送不落单 |

> **`M1-26` 的 L1 关联（清单路径比对 + INTENT 竞态回填）不在本里程碑**——M1 阶段尚无平台代提交，该路径无从验证。已拆为 `M2-26a`。

### M1 里程碑验收（PRD §9）

| ID | 验收项 | 关联任务 |
|---|---|---|
| M1-A1 | 一个 Ansible 作业全流程落单、实时日志、task 级结果展示 | M1-21, M1-22 |
| M1-A2 | 手动直推 GitLab 被自动记录为 BYPASS 单 | M1-25, M1-26 |
| M1-A3 | CMDB 命名规范校验生效 | M1-03 |

---

## M2 — 通道 A + 验证 + 闭环 + 回滚 + 审批

### M2-A 通道 A（GitLab 代提交 + Jenkins）

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| M2-01 | **`tl_pipeline_run` 模型**：commit_sha / jenkins_job / build_no / 阶段时间线 JSONB / 结果 / `confirmed_at`+`confirmed_source` / 轮询状态字段 | S0-4 | L2 | 自动断言 | migrate 通过 |
| M2-02 | **代提交**：Repository Files API 提交 `releases/TL-xxx.yaml` + 结构化 commit message（PRD §5.3 格式） | S0-6a, M2-01, M1-09 | L2 | 自动断言 | Mock GitLab 上出现清单文件，commit message 格式符合 PRD §5.3 |
| M2-03 | **幂等守卫（前置条件：清单内容确定性）**：`build_manifest` 必须 `sort_keys=True`，时间字段取 `ticket` 上的时间而非 `now()`；`ManifestSubmission(state=INTENT)` 意图日志在网络调用**之前**写入；`action:"create"` 而非 `"update"`。**哈希钉在字节上**：`build_manifest()` 返回 `(content, sha256_hex)`，哈希对**实际发送的字节串** `content.encode("utf-8")` 计算；预检阶段对 base64 解码后的原始字节取哈希，**不得再规范化一次**（§4.2） | M2-02 | L3 | 自动断言 | ① 内容快照测试：同一 ticket 生成 100 次哈希恒定 ② 故障注入"**已落库但响应超时**"→ 只产生一次构建 ③ **只对 GET 重试，POST 绝不重试** |
| M2-03a | **Mock GitLab 时序构造能力**：三种时序 ① 落库→成功→发 webhook ② 落库→**超时**→发 webhook ③ 落库→**发 webhook→响应**（§4.6） | S0-6a, M2-03 | L1 | 自动断言 | 三种时序各跑一次，断言平台行为正确 |
| M2-04 | **数字化主键链**：单号 ↔ commit SHA ↔ build_no 三键强关联；三条关联路径都写 `TicketEvent(payload={...,source})`（§4.3） | M2-02 | L1 | 自动断言 | 单测：三键任一可反查另两个 |
| M2-05 | **Jenkins notify 回调接口**：HMAC **对原始字节**验签 + 时间窗防重放；`INSERT 日志 → 入队 → 200`（顺序不可反）；归属解析按"payload 单号 → (job,build_no) → commit_sha"优先级，**命中 0 或 >1 条一律 ORPHAN 告警**（§3.3/§3.4）。**坏签名返回 401 且不写任何 DB 行**（审计走应用日志 + `jenkins_callback_signature_rejected_total` 计数）；`200` 只用于“签名正确但处理失败”（§3.3） | S0-6b, M1-23 | L3 | 自动断言 | 重放回调不重复迁移；**错签名返回 401 且 `tl_callback_log` 零新增行**；并行两个应用发布时回调不串单 |
| M2-05a | **去重键双层设计**：`tl_callback_log` 管"投递去重"，`tl_ticket_event.idempotency_key` 管"状态幂等"。**两者都要**（§3.2） | M2-05 | L3 | 自动断言 | 单测：同一回调投递 3 次 → 1 条日志、1 条事件 |
| M2-06 | **对账 sweeper**：beat 扫 EXECUTING 超时单（`SKIP LOCKED` + `LIMIT`）→ 查 Jenkins REST → 终态前二次确认。带**熔断退避**；时间基准一律走 `core/db_time.py::db_now()`，**禁止 Python 侧算好的时间戳**（§0.2 规则 4 / §3.6）。**边 31 的触发者（第四轮补主）**：`queue_id` 存在但 `build_no` 迟迟不出现 → `get_queue_item` 判定 `cancelled`/404 → `apply("TRIGGER_BUILD_LOST")`；**绝不猜 build_no**（§1.3.1 边 31） | M2-05 | L3 | 故障注入 | **故障注入：丢弃回调，sweeper 能自行修复到正确终态**（M2-A2）；Jenkins 持续 500 时 sweeper 降频而非打满；**注入"队列项被取消"→ 单落 `PAUSED` 且 `BUILD_LOST` 事件含判定依据** |
| M2-06a | **callback 与 sweeper 的单一收敛点**：`confirm_and_transition(run_id)`——`SELECT FOR UPDATE` 串行化 + `confirmed_at` 幂等标志（§3.5）。**这是"两条路径不双写事件"的唯一实现手段** | M2-06 | L3 | 自动断言 | 单测：callback 与 sweeper 同时断言同一 build → 恰好 1 条事件、1 次迁移 |
| M2-07 | **consoleText 归档**：终态后**流式**（`httpx.stream`）拉取，PG 存路径 + 尾部 200 行摘要 | M2-05 | L1 | 自动断言 | 终态单日志文件存在且可读；构造大响应不 OOM |
| M2-08 | Jenkins 阶段时间线展示（P1） | M2-05, X-01 | L1 | 自动断言 | 前端可见 stage 推进 |
| M2-09 | 通道 A 前端：阶段推进视图 + 实时日志 | M2-08, X-04 | L1 | 人工观察 | 手工走查 |
| M2-26a | **旁路 L1 关联（从 M1-26 拆出）**：按清单路径解析单号并比对 `ManifestSubmission`，**含"API 响应晚于 webhook"竞态：`state=INTENT` 时回填 sha，不建单**（§4.4 L1） | M1-26, **M2-02** | L1 | 自动断言 | 构造"webhook 早于 API 响应"时序下不建假单；内容哈希不匹配时建 `MANIFEST_TAMPERED` 单 |

### M2-B 验证引擎（第一重点）

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| M2-10 | **`tl_verification` / `tl_verification_check` 模型 + 执行器×传输层抽象**：`CheckExecutor` + `Transport` 二维组合（§5.2） | S0-4 | L2 | 自动断言 | 单测：新增一种 Probe 无需改调度器；`LocalTransport`/`SshTransport` 连接复用生效 |
| M2-11 | **探活 - HTTP**：URL + 期望状态码 + 响应关键字。失败时**必须区分** `CONNECT_REFUSED`（RST，应用挂了）与 `TIMEOUT_CONNECT` / `DNS` / `AUTH`（平台够不着）——这个区分是 §5.4.1 上修结论的唯一依据 | M2-10, S0-6c | L1 | 自动断言 | Mock 主机返回 200/500/错关键字三种场景判定正确；**端口关闭 → `CONNECT_REFUSED`，黑洞地址 → `TIMEOUT_CONNECT`，两者不得混淆** |
| M2-12 | **探活 - TCP 端口** | M2-10, S0-6c | L1 | 自动断言 | 端口关闭时判定失败 |
| M2-13 | **探活 - 进程存活**（SSH，`BatchMode=yes`） | M2-10, S0-6c | L1 | 自动断言 | 进程不存在时判定失败；未知 host key 不挂起 |
| M2-14 | **日志检查**：tail N 行 + 内置关键字（error/exception/panic）+ 应用自定义关键字 | M2-10, S0-6c | L1 | 自动断言 | Mock 日志注入错误行后判定失败 |
| M2-15 | **应用状态回显 + 发布前后对比**：`before_snapshot` 由 FSM 进入 EXECUTING 的 effect 触发，且**尽力而为**（失败只降级不阻塞，§5.5） | M2-10, M1-09 | L1 | 自动断言 | 对比视图能显示版本是否已切换；快照失败时发布仍继续 |
| M2-16 | **验证结果流转（三分支）**：`PASS` → CONFIRMING；`FAIL` → **VERIFY_FAILED**（产品信号，告警 + 回滚入口）；`ERROR`/`TIMEOUT`/`INCONCLUSIVE` → **VERIFICATION_ERROR**（基础设施信号，告警类别不同，**不主动提示回滚**）。两条失败分支**不得合并**（ADR-6） | M2-10, M1-09 | L1 | 自动断言 | 单测：三条分支的状态迁移与事件落库正确；`VERIFICATION_ERROR` 的告警文案不含"回滚"字样 |
| M2-16a | **`VERIFICATION_ERROR` 出口三路**：重试 `→ VERIFYING` / 回滚 `→ ROLLBACK` / 暂停 `→ PAUSED`。**均需人工触发，不自动推进**；重试受 `max_verify_attempts`（默认 3）约束，超限后仅剩回滚/暂停 | M2-16, M2-17 | L1 | 自动断言 | 单测：三路出口均可走通；第 4 次重试被拒；`VERIFICATION_ERROR` 静置 24h 不会自动变化 |
| M2-17 | **五态结果分类**：`PASS / FAIL / ERROR / TIMEOUT / SKIPPED`。**全 SKIPPED 判 INCONCLUSIVE**；超时按 `on_timeout ∈ {FAIL,ERROR,RETRY}` 归因（§5.3/§5.4）。**分类结果决定落到哪个终态**（见 M2-16），故 `FAIL` 与 `ERROR` 的边界即 ADR-6 的边界。**第 5 步（§5.4.1 / ADR-9）**：若所有失败项的 `error_class` **均属连接类**（`CONNECT`/`CONNECT_REFUSED`/`TIMEOUT_CONNECT`/`DNS`/`AUTH`），把结论从 `FAIL` **上修为 `ERROR`** | M2-10 | L2 | 自动断言 | 单测：Probe 抛异常 → ERROR 而非 FAIL；全 SKIPPED → INCONCLUSIVE；所有 ERROR/TIMEOUT/INCONCLUSIVE 均不落 VERIFY_FAILED；**多目标全部连接类失败 → 结论为 ERROR 而非 FAIL**（第 5 步须独立于第 2/3 步、且在其后执行） |
| M2-17a | **验证 deadline watchdog**：`deadline_at` 超时强制收口为 INCONCLUSIVE，**落 `VERIFICATION_ERROR`**（平台够不着，非应用坏了，§5.4④） | M2-17 | L3 | 自动断言 | 单测：卡死的 check 不会让单据永久停在 VERIFYING；收口终态为 `VERIFICATION_ERROR` 而非 `VERIFY_FAILED` |
| M2-17b | **日志扫描假阳性治理**：记录发布前日志文件 `size/offset` + `inode`，只扫新增区段；关键字默认 `re.escape`（§5.7①） | M2-14, M2-17 | L1 | 自动断言 | 单测：发布前就存在的错误行不导致失败；含 `(`/`[` 的关键字不抛异常 |
| M2-18 | **K8s 探活（P1）** ⚠️ **阻塞于 OQ-3** | M2-10, M2-15 | L1 | 自动断言 | 按拍板结果实现 `K8sStateReader` 的 api/kubectl 实现；此前仅假实现 |
| M2-19 | 验证结果页前端：发布前后对比视图（置于业务确认按钮旁） | M2-15, X-01 | L1 | 人工观察 | 手工走查 |

### M2-C 反馈闭环

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| M2-20 | **`IMNotifier` 抽象接口**（可插拔，解 OQ-4 未定） | S0-4 | L2 | 自动断言 | 单测：注册新渠道不改调用方 |
| M2-21 | **`tl_feedback` 模型 + 反馈接口** `POST /tickets/{id}/feedback`：确认人/渠道/结论/备注/时间全留痕 | M1-09 | L2 | 自动断言 | 反馈记录可查且不可改 |
| M2-22 | **通道 B：发起人确认即闭环**（P0，先于 IM 渠道打通） | M2-21 | L1 | 自动断言 | 作业单走完整闭环到 SUCCESS |
| M2-23 | **IM 确认卡片 + 一键确认/驳回** ⚠️ **阻塞于 OQ-4** | M2-20, M2-21 | L1 | 自动断言 | 卡片可确认/驳回并回写单据 |
| M2-24 | **确认超时升级**：超 X 小时未确认 → 升级提醒至审批人/主管（X 可配置，Celery beat） | M2-23 | L1 | 自动断言 | 时钟拨快后升级提醒触发 |

### M2-D 审批与暂停点

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| M2-25 | **一级审批** + 职责分离硬校验；非 prod 自动审批**必须写 `AUTO_APPROVED` 事件**，不允许 `approved_by=null` 静默进 APPROVED（§1.3） | M1-09, S0-5 | L3 | 自动断言 | 单测：同人审批被拒；自动审批产生 `AUTO_APPROVED` 事件 |
| M2-26 | **生产环境强制审批门禁**（OQ-2 已定）：prod 单未经审批不得进入 EXECUTING | M2-25 | L3 | 自动断言 | 单测：prod 单跳过审批被拒 |
| M2-27 | **暂停观察点 + 恢复** `POST /tickets/{id}/resume`：恢复目标由 `Ticket.paused_from_state` 决定（§1.3.1 边 24），**不允许调用方指定目标态**——否则可以借恢复接口把单跳到任意阶段 | M1-09 | L2 | 自动断言 | 暂停后 worker 不推进；从 `EXECUTING` 暂停的单恢复回 `EXECUTING`、从 `VERIFYING` 暂停的恢复回 `VERIFYING`；**请求体里传 `target_state` 被忽略或拒绝** |
| M2-34 | **单据终止** `POST /tickets/{id}/terminate`（§1.3.1 边 25，第四轮补主）：仅 `PAUSED` 可发（人工对账确认无法继续）→ `CANCELLED`。通道 A 单终止前，若其 `PipelineRun` 构建非终态则 best-effort 调 `JenkinsProvider.abort(job, build_no)`（`POST /job/{job}/{n}/stop`；**`abort()` 在 `integrations/jenkins/` 客户端随本任务实现**，构建已终态的返回不视为错误）——**stop 失败不阻塞终止**，落一条 warn 事件留痕；终止后迟到的回调/对账结果被 `apply()` 以"终态无出边"拒绝，不得复活单据 | M1-09, M2-06 | L2 | 自动断言 | 非 `PAUSED` 发起终止被拒；Mock 注入"构建进行中"→ 终止后 Mock 侧 build 结果 `ABORTED`；Mock 注入 stop 返回 500 → **终止仍成功且多一条 warn 事件**；对已 `CANCELLED` 的单重放合法回调 → 状态不变、事件被拒 |

### M2-E 回滚

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| M2-27a | **人工强制放行** `POST /tickets/{id}/force-release`（ADR-10，§1.3.1 边 29）：`VERIFY_FAILED → CONFIRMING`。权限 ≥ 审批人且非发起人，**理由必填且 ≥ 10 字**；同事务写 `FORCE_RELEASED` 事件与 `force_released_*` 三字段。**不得复用 `VERIFICATION_ERROR` 的出口守卫** | M2-16, S0-5 | L3 | 自动断言 | 发起人自己放行被拒；空理由/<10 字被拒；角色不足被拒；放行后单据可正常走到 `SUCCESS`（即 `guard_no_skip_verifying` 的放宽生效，**不放宽则该单会被自己的守卫卡死在 `CONFIRMING`**）；`VERIFICATION_ERROR` 单据调用该接口**必须失败** |
| M2-28 | **一键回滚**：代提交**上一版本**清单，commit message 标记 `ROLLBACK`，链路完全复用通道 A | M2-02, M2-04 | L1 | 自动断言 | 回滚单触发新的 Jenkins 构建 |
| M2-28a | **事后回滚** `POST /tickets/{id}/post-hoc-rollback`（ADR-11，§1.3.1 边 30）：`SUCCESS → ROLLBACK`。**必须显式传 `target_version`**并校验其存在于本应用历史版本；抢不到锁时**返回 409 并指名占用单据**，不写 `tl_ticket_queue` | M2-28, M1-10 | L3 | 自动断言 | 不传 `target_version` 被拒；传一个从未上线过的版本号被拒；**并发占用时返回 409 且 `tl_ticket_queue` 零新增行**；回滚完成后单据落 `ROLLED_BACK` 且事件表保留原 `SUCCESS` 与本次发起时间 |
| M2-29 | **通道 B 回滚**：执行指定回滚 Playbook | M1-15 | L1 | 自动断言 | 回滚作业可执行并落单 |
| M2-30 | **回滚单与原单双向关联** | M2-28 | L1 | 自动断言 | 任一单可跳转另一单 |
| M2-31 | **告警卡片内暴露回滚入口** ⚠️ 受 OQ-4 影响 | M2-16, M2-23, M2-28 | L1 | 自动断言 | 失败告警卡片可直接点回滚 |

### M2-F 权限与审计

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| M2-32 | **全量操作审计**：谁、何时、对哪台主机、执行什么、结果如何 | S0-5 | L2 | 自动断言 | 审计查询可按主机/人/时间过滤 |
| M2-33 | 发布单时间线完整回放页面（P1） | M1-12, X-01 | L1 | 自动断言 | 时间线按序展示全部事件 |

### M2 里程碑验收（PRD §9）

| ID | 验收项 | 关联任务 |
|---|---|---|
| M2-A1 | 平台发起 → 代提交 → Jenkins → 自动验证 → IM 确认 → 闭环，全程可回放 | M2-02, M2-05, M2-16, M2-23, M2-33 |
| M2-A2 | 模拟回调丢失，sweeper 可修复 | M2-06, M2-06a |
| M2-A3 | 验证失败自动告警且一键回滚成功 | M2-16, M2-28, M2-31 |
| M2-A4 | **人工强制放行**（ADR-10）可把 `VERIFY_FAILED` 单放行闭环并留痕，且对 `VERIFICATION_ERROR` 单**必须拒绝**（两处 guard 不可复用） | M2-27a, M2-16a, M2-33 |
| M2-A5 | **事后回滚**（ADR-11）：`SUCCESS` 单可回滚至 `ROLLED_BACK`；占用中返回 409 且**不入队**；回滚后计入回滚率而非成功率 | M2-28a, M2-28, M3-01 |

---

## M3 — 度量、报告、定时作业

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| M3-01 | **度量看板 API** `GET /metrics/dashboard`：成功率、回滚率、平均时长、**旁路率**。**终态分流口径已定（OQ-9）**：`VERIFY_FAILED` 计入"发布失败"，`VERIFICATION_ERROR` **单列为"平台未能验证"**，不计入发布成功率/失败率分母的一次成败判定——否则平台自身抖动会污染发布质量数据。**新增两项（ADR-10/ADR-11）**：**强制放行率**（与旁路率同级）；成功率/回滚率**一律按单据最终态统计**——`SUCCESS` 不再是终态，一张"先成功、后事后回滚"的单必须计入回滚率 | M2-33 | L2 | 自动断言 | 用种子数据校验每个指标口径；一张 `VERIFICATION_ERROR` 的单不改变"发布成功率"分子分母；**一张 `SUCCESS → ROLLBACK → ROLLED_BACK` 的单计入回滚率而非成功率**；强制放行过的单在成功率里可被单独标出 |
| M3-02 | **发布报告自动生成 + 归档**：时间线 + 验证结果 + 反馈结论 → `tl_report` + 归档文件 | M2-33 | L1 | 自动断言 | 终态单自动产出报告 |
| M3-03 | **分类统计**：按系统/环境/失败原因 | M3-01 | L1 | 自动断言 | 报表与手工统计一致 |
| M3-04 | **定时作业（P2）**：cron 表达式创建周期作业单 | M1-21 | L1 | 自动断言 | 到点自动建单 |
| M3-05 | **日志保留策略**（默认 90 天可配置）⚠️ **阻塞于 OQ-7** | M2-07 | L1 | 自动断言 | 过期日志被清理，配置可调 |
| M3-06 | 发布前后日志错误率对比（P2） | M2-14 | L1 | 人工观察 | 对比图有数据 |
| M3-07 | 度量看板前端 | M3-01 | L1 | 人工观察 | 手工走查 |
| M3-08 | **旁路率治理阈值告警** ⚠️ **阻塞于 OQ-8** | M3-01 | L1 | 自动断言 | 超阈值触发提醒 |

---

## 横切任务（与 M1–M3 并行）

| ID | 任务 | 依赖 | 风险 | 验证强度 | 验收方式 |
|---|---|---|---|---|---|
| X-01 | **前端工程骨架**：Vite + Vue3 + Element Plus + Pinia + Router + axios 封装 + 权限路由 | S0-3 | L1 | 自动断言 | `npm run dev` 起前端，能登录并拿到当前用户 |
| X-02 | **契约流水线**：OpenAPI → 前端 TS 类型自动生成 | S0-3 | L2 | 自动断言 | 后端改接口后前端类型同步更新 |
| X-03 | **番茄状态标识组件**（UI §10）：青番茄/半红/全红/蔫了/虫眼（`VERIFY_FAILED`）+ **"平台看不清"标识**（`VERIFICATION_ERROR`，OQ-9 新增，**不得与虫眼共用图标**，两者语义相反）+ 🌿旁路角标 + **⚠️ 人工放行角标**（`force_released_at` 非空，ADR-10）+ **全红叠蔫了双标**（曾成功、后事后回滚，ADR-11） | X-01 | L2 | 人工观察 | 组件故事页展示全部状态（含 `VERIFICATION_ERROR`）；**"强制放行的成功"与"干净的成功"必须一眼可辨**——两者混同则强制放行率失去意义；与 UX 确认新图标语义不引起误读 |
| X-04 | **实时日志 WebSocket 通用组件**：断线重连 + `last_seq` 历史回填；**必须处理服务端的 `{"resync": true, "oldest_seq": N}` 缺口信号**——收到后在界面**显式标记“此处日志有缺口”**，不得静默接续（§2.4③）；**`truncated=True` 的行必须显示“内容已截断”角标，不得呈现为全文**（§2.4②，F-6） | X-01, S0-2 ✅ | L2 | 故障注入 | 断网重连后日志不丢；**构造 Redis list 被 LTRIM 截断后前端出现缺口标记**而非静默丢行；**注入 excerpt 截断事件，断言截断角标在场** |
| X-05 | **部署编排**：单机 Docker Compose 生产编排（含 §0.5 的 worker 分组）、迁移、静态资源、数据备份 | S0-3 | L2 | 自动断言 | 干净机器上一键拉起 |
| X-06 | **幂等性与故障演练**（NFR-01/02）：回调重放、断网重试、平台自身挂掉时直推逃生通道可用 | M2-06 | L3 | 人工观察 | 演练报告：每项注入故障后系统状态正确 |
| X-07 | **日志保留与结构化日志**（NFR-03 的工程侧） | S0-3 | L1 | 自动断言 | 日志可按 ticket_id 聚合检索 |

---

## 阻塞项汇总

| 阻塞 | 阻塞哪些任务 | 是否在关键路径 | 建议拍板时点 |
|---|---|---|---|
| ~~**D-2** `APPROVED` 语义~~ | ~~`M1-10`、`M1-10a`~~ | — | ✅ **已于 2026-09-20 拍板** |
| ~~**OQ-9** `VERIFICATION_ERROR` 状态~~ | ~~`M2-16`、`M2-17`、`M2-17a`、`M3-01`~~ | — | ✅ **已于 2026-09-20 拍板** |
| ~~**边 29** 人工强制放行~~ | ~~`M1-09`、`X-03`、`M3-01`~~ | — | ✅ **已于 2026-09-21 拍板：开放**（ADR-10） |
| ~~**边 30** 事后回滚~~ | ~~`M1-09`、`M2-27`、`M3-01`、`X-03`~~ | — | ✅ **已于 2026-09-21 拍板：开放**（ADR-11） |
| **OQ-3** K8s 状态读取方式 | `M2-18` | ❌ | M2 启动前 |
| **OQ-4** IM 渠道（企微/钉钉） | `M2-23`、`M2-24`、`M2-31` | ❌ | `M2-C` 反馈闭环开工前（此前走 `IMNotifier` 抽象 + 通道 B 确认） |
| **OQ-5** CMDB 存量数据 | `M1-06` | ❌ | M1 启动前 |
| **OQ-7** 日志保留/Loki | `M3-05` | ❌ | M3 启动前 |
| **OQ-8** 旁路率目标值 | `M3-08` | ❌ | M3 中期 |

---

## 风险登记（按影响排序）

| 风险 | 影响 | 缓解 | 详见 |
|---|---|---|---|
| ansible-runner 事件流在 Celery prefork worker 内不稳定 | 通道 B 设计返工 | `S0-1`/`S0-2` 前置 spike | §2.3 |
| Mock 层不到位导致 M1/M2 联调任务全部卡住 | 交付顺序整体后移 | `S0-6a/b/c` 与工程骨架同批交付，且**先于一切联调任务** | §6 |
| **验证槽位与 app/env 锁互等 → 全局死锁** | 全平台卡死 | `M1-18a` 验证配额预留，设计期切断 | §5.6 |
| worker 崩溃后任务重投，自动重跑已成功的生产发布 | **生产事故** | `M1-19b` adopt 而非重跑 | §2.7① |
| 验证检查自身异常被误判为发布失败 | **误回滚一个成功的发布** | ✅ 已处置：`M2-17` 区分 ERROR/FAIL，`M2-16` 落 `VERIFICATION_ERROR` 独立终态且不提示回滚（ADR-6） | §5.5 |
| 锁被占时审批通过的单据无人晋升，**永远停在 APPROVED 且不报错** | 单据静默失效，看板只显示"待执行" | `M1-10b` 调度器 = 事件信号提速 + beat 兜底，**两者缺一不可** | §1.6a |
| **加锁顺序反了（先槽位后锁）** | 与 `M1-18a` 同一类死锁，且**只在并发下偶发** | `M1-10` 硬规则 + 断言"先槽位后锁"的调用必须失败 | §1.5.1 |
| **应用整体不可达被 `FAIL` 误判为"发布失败"** | **误触发回滚，把一次没发生的故障当成事故** | `M2-17` 第 5 步：全部失败均为连接类时上修为 `ERROR` → `VERIFICATION_ERROR`（ADR-9） | §5.4.1 |
| 清单 YAML 内容不确定导致幂等守卫失效 | 重复构建、重复发布 | `M2-03` 确定性生成 + 100 次哈希恒定 | §4.2 |
| 回调归属靠猜，并行发布时串单 | 数据污染，A 的结果写到 B 的单据 | `M2-05` 优先级解析 + 0/>1 命中一律 ORPHAN | §3.9③ |
| 平台自己的代提交被误判为人工直推，产生假旁路单 | 旁路率指标失真 | `M1-26` + `M2-26a` 四层关联 | §4.4 |
| 事件洪水压垮 WebSocket / DB | 实时日志不可用 | `M1-16` 三层治理 | §2.4 |
| 主机解析路径分叉（发布 A 组、验证 B 组） | **所有检查误报通过** | `resolve_hosts` 单函数 + 集合恒等测试 | §0.3 |
| 日志扫描假阳性 | 正常发布被判失败 | `M2-17b` 按 offset 只扫新增区段 | §5.7① |
| 回调丢失与 sweeper 并发，同一单双重推进 | 状态机脏数据 | `M2-06a` 单一收敛点 | §3.5 |
| **强制放行被当成常规路径使用**，`SUCCESS` 不再等价于"验证通过" | 看板失真、发布质量数据不可信 | `M2-27a` 四条约束（权限/理由/UI 角标/度量）+ `X-03` 角标 | §1.3.1 边 29 |
| **强制放行守卫被复用到 `VERIFICATION_ERROR` 出口** | "平台够不着"被人工放行，ADR-6/ADR-9 全部失效 | `M2-27a` 显式断言该接口对 `VERIFICATION_ERROR` 必须失败 | §5.5 |
| **事后回滚回滚到错误版本**（线上已发多版，"上一版"未必是想回到的那版） | 把线上拉回一个更旧甚至从未上线的版本 | `M2-28a` 强制显式传 `target_version` 并校验其在历史版本中 | §1.3.1 边 30 |
| 事后回滚被塞进自动队列，操作者以为在回滚其实在排队 | 新的静默失败（D-2 同类） | `M2-28a` 抢不到锁返回 409 并指名占用单据，**不入队** | §1.3.1 边 30 |

---

## 验收与验证环境

- **单元/集成**：pytest + pytest-django。状态机、幂等、并发锁必须有专门用例，**并发用例一律 `TransactionTestCase` + 真实线程**（`design-notes.md` §0.7）。
- **端到端（自动化基座＝Mock）**：由 `S0-6a/b/c` 三个 Mock 服务组成一条完整假链路（假 GitLab → 假 Jenkins → 假目标主机）。三个里程碑的验收项全部在这条链路上跑——可重复、可断言、CI 可依赖。
- **真实实例联调（人工，`S0-9`）**：本地 docker/compose 临时拉起真实 GitLab + Jenkins，里程碑验收项**另在这条真链路上人工各走一遍**（`design-notes.md` §0.6b、§6 第 7 条）。两层定位相反：真实例验证"我们对真系统的假设是对的"，Mock 验证"系统在坏情况下不撒谎"；**两次都过才算过**。临时实例随时可销毁，**任何 CI 用例与 `自动断言` DoD 不得依赖它**。
- **故障注入**：Mock 层内置开关——提交超时、回调丢失、主机不通、日志含错误行、日志轮转——用于 M2-A2/A3 与 `X-06`。
- **手工走查**：前端页面按每个里程碑验收项逐条走一遍。

**AI 编码下的验收纪律**（对应两列口径）：

1. 标 `自动断言` 的任务，**DoD 必须是一条能贴进终端的命令**，且退出码即结论。做不到这一点的任务，先改 DoD 再写代码。
2. 标 `L3` 的任务，**先写出会失败的用例，再写实现**；用例在实现前必须红过一次。
3. 标 `人工观察` 的任务，AI 只能交付"自测通过 + 待人工确认"，**不得自行标完成**；一次交付里应把这类任务单独列出来。
4. 任何任务在实现中发现"实际改动面超出 Spec 边界"，按 `/spec` 的 Hard Stop 停下汇报，不得就地扩大范围。

---

## 变更记录

| 版本 | 变更 |
|---|---|
| v0.1 | 按 PRD §9 拆解为 83 个任务 |
| v0.2 | 依 `design-notes.md` 工程评审修订：<br>· 新增 12 个任务（`S0-3a`/`S0-8`/`M1-10a`/`M1-18a`/`M1-19a`/`M1-19b`/`M2-03a`/`M2-05a`/`M2-06a`/`M2-17a`/`M2-17b`/`M2-26a`）<br>· `M1-26` 拆分：L1 关联移出至 `M2-26a`（原依赖 `M2-02`，与 M1 排期矛盾）<br>· 10 个任务人天上调（`S0-3`/`S0-7`/`M1-10`/`M1-16`/`M1-18`/`M1-19`/`M2-03`/`M2-05`/`M2-07`/`M2-17`），理由见各任务描述引用的 design-notes 章节<br>· **修正人天总计**：v0.1 的 74/104/35/40 系估数，与分项不符；v0.2 按分项实算为 87/113.5/27.5/21 |
| v0.3 | 落实两项已拍板决策 + 补齐其连带缺口：<br>· **D-2**（`APPROVED` = 待调度不持锁）：`M1-10` 谓词定稿（`APPROVED` 不入谓词）；**新增 `M1-10b`**——`tl_ticket_queue` 模型 + 晋升调度器。这是 D-2 选"不持锁"的直接后果：审批通过不再等于立即执行，若无调度器，锁被占时单据会静默卡在 APPROVED（已在 `design-notes.md` 新增 §1.6a 说明）<br>· **OQ-9**（新增 `VERIFICATION_ERROR`）：`M2-16` 改为三分支且两条失败路径不得合并；**新增 `M2-16a`**三路出口 + 重试上限；`M2-17`/`M2-17a`/`M3-01`/`X-03` 相应调整<br>· 人天复核：M1 87→89.5、M2 113.5→115.5、横切 21→21.5（`X-03` +0.5 为新图标与语义确认）、合计 266.5→**271.5** |
| v0.4 | **取消人天估算，改为"风险等级 + 验证强度"两列**（本项目由 AI 编码完成，工期非约束）：<br>· 全部 97 个任务行删除人天列，替换为 `风险`（L1/L2/L3）与 `验证强度`（自动断言/故障注入/人工观察）；11 张任务表表头同步改名<br>· 删除 10 个分节标题与 4 个阶段标题中的人天小计、§阶段总览的 271.5 合计，改为任务数与 L3 计数<br>· 新增 §阶段总览 **两列口径说明**：`自动断言` 任务的 DoD 必须是 AI 能独立执行的命令；`人工观察` 任务的 DoD 必须写明人在看什么，AI 不得自行判定该类任务完成<br>· 新增规则：单项任务 Spec **可上调本任务风险等级，不得下调**；下调须在本表留理由<br>· 风险判定：关键词初判 + 人工修正 9 条——`S0-3a`/`M1-15`/`M1-16`/`M2-02`/`M2-32`→L2，`M2-25`/`M2-26`→L3；另两条因本版新增的约束而上调：`M2-17`（新增第 5 步，结论直接决定终态分支）与 `X-04`（新增缺口信号处理）由 L1→L2<br>· 同步 `design-notes.md` v0.3 的第二轮审计结论到具体任务行：<br>　- §1.3.1 权威迁移表 → `M1-09`（表内容须逐行一致；**2 条留白边未拍板前必须抛 `NotImplemented`**）<br>　- §1.3 `verify_attempts` / `paused_from_state` → `M1-08`<br>　- §1.5.1 加锁顺序硬规则 / §1.5.2 fencing token 校验点 → `M1-10`、`M1-09`<br>　- §2.2 槽位按需创建 + 双 scope 全等于 + 并发数启动断言 → `M1-18`<br>　- §4.4 L3 边 2（新生单靠 `TRIGGER_BYPASS_DETECTED` 进 EXECUTING）+ 抢锁失败不丢单 → `M1-25`<br>　- §4.2 字节级清单哈希（`content_sha256` 钉在发送字节上）→ `M2-03`<br>　- §3.3 坏签名 401 且不落库 → `M2-05`<br>　- §0.2 规则 4 `db_now()` → `M2-06`<br>　- §5.4.1 连接类失败统一修正 → `M2-11`、`M2-17`<br>　- §2.4③ `resync` 缺口信号 → `X-04`<br>· 本行为最后一次含人天数字的版本说明；v0.1–v0.3 的历史记录保留原文以供追溯 |
| v0.5 | **两条留白边拍板开放**（`design-notes.md` §7 第三轮）：<br>· **边 29 `VERIFY_FAILED → CONFIRMING` 人工强制放行**（ADR-10）：新增 `M2-27a`（L3）——权限 ≥ 审批人且非发起人、理由必填、同事务写 `FORCE_RELEASED`；`M1-09` 守卫用例、`X-03` 加"人工放行"角标、`M3-01` 加强制放行率<br>· **边 30 `SUCCESS → ROLLBACK` 事后回滚**（ADR-11）：新增 `M2-28a`（L3）——必须显式传 `target_version`、抢不到锁返回 409 **不入队**；**`SUCCESS` 不再是终态**（4→3），`M3-01` 改为按最终态统计，`M2-27` 上调 L2<br>· **两处隐藏冲突已一并处理**：① `guard_no_skip_verifying` 原要求最新结论为 `PASS`，会让强制放行进来的单被自己的守卫卡死在 `CONFIRMING`，现放宽为"`PASS` **或** `force_released_at` 非空"；② 新增守卫与 `VERIFICATION_ERROR` 出口守卫**长得像但语义相反，明令不得复用**<br>· `Ticket` 新增 `force_released_at / force_released_by / force_release_reason` 三字段<br>· 任务数 97→**99**；风险分布 L3×23→**25**、L2×20→**23**、L1×54→**51**；验证强度分布不变<br>· 同步修复 `PRD.md` 的 Markdown 围栏错配（外层 ```` ```markdown ```` 包裹全文导致 §8~§12 渲染为代码块）并删除文末残留的生成对话文字；新增 ADR-10 / ADR-11、FR-RB-05/06、FR-SEC-05、UI 放行角标<br>· `PRD.md` §9 的 M2 交付内容与 DoD 补入两条新能力（新增 DoD ④），M3 度量补 `强制放行率`；**并删除 §9 的「周期」列**（原 M1 4~6 周 / M2 6~8 周 / M3 8 周），改为明示「串行推进，顺序固定 M1 → M2 → M3，不承诺日期、不估工期」，与本表「不估人天 / 不排工期」的立场对齐；本表新增 `M2-A4` / `M2-A5`（里程碑验收项 6→**8**） |
| v0.6 | **本地开发环境实况落账**（用户提供的两项环境事实，改文档不动环境）：<br>· 前提表「联调环境」行改写为**双层**——本地 docker/compose 临时拉起真实 GitLab + Jenkins 做人工联调；自动化断言与故障注入仍以 Mock 层为准，**`S0-6a/b/c` 不解除**（`callback_drop_rate`、落库超时、webhook 抢跑三类时序真实系统构造不出来；临时实例不得被 CI 依赖）。分界线落为 `design-notes.md` §6 第 7 条："真实例验证假设对不对，Mock 验证坏情况下不撒谎；两次都过才算过"<br>· PostgreSQL 用**本地已装实例**（MacPorts `/opt/local/bin/psql14`，v14，非容器）：`S0-3` compose 清单**删去 PG16**；版本基线钉为 **≥14**——全文档所用 PG 特性（JSONB/部分唯一索引/SKIP LOCKED/事务级 advisory lock/REVOKE UPDATE/`Now()`）均为 9.x 时代特性，已逐条核对，15+/16-only 语法引入前必须先上调基线（`design-notes.md` 新增 §0.6a）<br>· 新增 `S0-9`（L1/人工观察）本地临时联调栈（`docker-compose.integration.yml`，§0.6b）；`S0-8` 断言补 `server_version >= 14`；`local.py` 的 `INTEGRATION_MODE` 扩为三态 mock/integration，业务代码仍零分支<br>· 任务数 99→**100**；L1×51→**52**；人工观察 9→**10**；L3/L2/自动断言/故障注入不变；`design-notes.md` v0.3→**v0.4** |
| v0.7 | **开工前缺口盘查落地**（`design-notes.md` v0.5 §7 第四轮 G-1~G-4）：<br>· **边 31 `TRIGGER_BUILD_LOST` 补录**进权威表（§3.6 早已引用而漏登）——`M2-06` 补主为触发者、`M1-09` 30→**31 条边**两处<br>· **边 25 补主**：新增 `M2-34` 终止端点（L2），同时闭环 `JenkinsProvider.abort` 遗留（§0.4 已补方法行；abort 失败不阻塞终止，落 warn 事件；终态单不得被迟到回调复活）<br>· **`S0-3` 补密钥交付物**：`.env.example`（全变量占位）+ `.gitignore`（`.env` 必入），DoD 补"`cp .env.example .env` 后 check 通过、`git status` 无 `.env`"——无出处则第一次提交就有真密钥进 git 历史的现实风险<br>· 边 10 陈旧引用"边 20"→"边 24"（design-notes 侧修复）<br>· **修 v0.6 漏账**：阶段总览 ASCII 块未随 `S0-9` 更新（11→12），本版一并改准；`合计 99` 为陈旧数字，现按实算重写<br>· 任务数 100→**101**；L2×23→**24**；自动断言×82→**83**；分布数字全部经脚本实算复核<br>· `PRD.md` §6 加一行数据模型权威指向（design-notes §1.2/§1.4/§1.7）<br>· 本表进入**开工基线**状态：开发期修订仅允许走 `/spec` Hard Stop 流程 |
| v0.8 | **S0-1 spike 实据修订**（`design-notes.md` v0.6 §7 第五轮 F-1~F-5；开工基线冻结通道的第一次启用，经 Hard Stop 呈报 + 产品拍板）：<br>· `S0-1` 行加 ✅（报告 `docs/spikes/s0-1/report.md`）<br>· **F-3 拍板方案 ③′**：RESCUED 改"终态计数回填 + 对账"（`n == stats.rescued+failures` 才判，不等全判 FAILED + 告警）——`M1-15` DoD 增加对账断言<br>· `M1-15` 白名单增补 `runner_item_on_*` 族（loop 逐项结果的唯一载体）、`seq` 复用 payload.counter、线程措辞按实据改（生产定死 `run_async()`）<br>· `M1-17` 注记 item 行 `duration_ms=NULL`、stderr 列改为提取语义（事件无独立字段），比对基线指向 `docs/spikes/s0-1/samples/`<br>· `S0-2` 交接实据：`cancel_callback` 正常跑完亦被周期调用（取消判定必须显式置标志位）、unreachable 实测 ≈10s/黑洞主机（超时预算按 ≥10s 设）、`runner_item_on_failed/skipped/unreachable` 三成员故障注入补验<br>· 任务数 101、风险/验证分布**不变**（纯 DoD/措辞修订，无新增任务）；迁移表 31 边零触碰 |
| v0.9 | **S0-2 spike 完成记账**（`docs/spikes/s0-2/report.md`；本 spike 新增证伪**未动 design-notes**，待 Hard Stop 拍板后另走 /spec 修订）：<br>· `S0-2` 行加 ✅。端到端实据：prefork worker 子进程内 `run_async` → handler 于 `Thread-1 (run)`（SQ1 ✓ 线程隔离成立）、5006 事件发布=落盘=WS 收讫零丢失、28 批帧保序无洞（SQ4/SQ7 ✓）、LTRIM 环窗缺口数据侧可判（SQ3 ✓）、显式置位取消 2.26s 内于 task 间隙生效且流无半行（SQ5 ✓）、join(5) 超时精确可观测=ORPHANED 前提成立（SQ6 ✓）<br>· **F-6 证伪待拍板**：channels_redis 4.3.0 `group_send` **无任何消息尺寸上限**（200KB 原样投递、`max_message_size` 属性不存在）——§2.3/§2.4"layer 兜底截断"假设不成立，归一化 excerpt 截断升为唯一防线，建议增补"批帧字节预算"护栏<br>· `S0-3` 交接实据：macOS+py3.12+celery5.6 prefork 需 `FORKED_BY_MULTIPROCESSING=1`（Linux 是否复现列为 S0-3 断言）、`run_async` 返回 `(thread, Runner)` 二元组、channels 4.3 `new_channel()` 为协程、线程类禁占 `_stop` 私有名<br>· 任务数 101、风险/验证分布**不变**；迁移表 31 边零触碰 |
| v0.10 | **F-6 拍板落地**（`design-notes.md` v0.7 §7 第六轮；开工基线冻结通道第二次启用，经 S0-2 Hard Stop 呈报 + 产品拍板"按建议"）：<br>· **F-6**：channels_redis 4.3.0 实测**无消息尺寸兜底**（200KB `group_send` 原样投递）——§2.3/§2.4"layer 第二道闸"假设证伪<br>· 尺寸自限改应用层**双护栏**：归一化 `stdout_excerpt_bytes`（默认 4KB，超限截断 + `truncated=True`）+ 批帧 `ws_batch_max_bytes`（默认 256KB，与 500ms/200 条并列先到者触发；Consumer 出口同闸）<br>· `M1-15` DoD 加"注入 ≥1MB stdout 单事件，断言 excerpt≤4KB、帧≤256KB"；`M1-16` 合并条件改三阈值；`X-04` 加"截断角标必显，不得呈现为全文"<br>· 任务数 101、风险/验证分布**不变**（纯护栏钉数与 DoD 增补）；迁移表 31 边零触碰 |
| v0.11 | **S0-3 工程骨架完成**（`S0-3` 行 ✅）：<br>· 落地：`manage.py` `config/`（settings 四片§0.6 / urls / asgi / wsgi / celery / routing / views-healthz）、`requirements.txt`（12 项顶层钉版 = S0-2 实证栈同代次解析）、`docker-compose.yml`（redis7+app+**§0.5 四 worker 池**+beat，PG 不进 compose§0.6a）、`.env.example`（15 变量全表，base.py 零默认值）、根 `.gitignore`；**清单隐含配套**（compose/pytest 的必要件，非新增审批依赖）：`Dockerfile` `.dockerignore` `pytest.ini` `tests/unit/test_scaffold.py`<br>· DoD 五步全绿 + 负断言（无 .env → check 必须失败 ✓）+ `pytest -q` 3 passed（无 DB 依赖）<br>· **S0-2 移交断言闭环**：Linux 容器 worker 不设 `FORKED_BY_MULTIPROCESSING`，callback/runner/verify 三队列 probe 均在 `ForkPoolWorker` 子进程成功消费、零 ValueError——macOS 缺陷不复现，生产部署口径无需该 env；`exec.runner` 的 `--prefetch-multiplier=1` 已按 §0.5 写进 compose（并发放大防线），完整压测归 `S0-3a`<br>· S0-2 临时容器 `tl-s02-redis` 已按确认处置为停置克隆（6379 移交 compose；spike 复跑先 `docker start` 或指向 compose redis）<br>· 任务数 101、分布不变；迁移表 31 边零触碰；`config.probe` 探针任务 S0-4 建 app 时删除 |
| v0.12 | **F-7 拍板落地**（`design-notes.md` v0.8 §7 第七轮；开工基线冻结通道第三次启用，经 S0-3a Hard Stop 呈报 + 产品拍板"按建议"）：<br>· **F-7**：§0.5「默认 prefetch=4 把并发 3 放大成 12（同时跑 12 个）」措辞证伪——实测（12×3s，t≈1.5s 采样）prefetch=1 → `LLEN=6, reserved=3, max_active=3`；prefetch=4 唯一同队列消费者 → `LLEN=0, reserved=9, max_active=3`；两组排空墙钟 12.2s/12.1s 几乎一致。**同时执行恒被 `-c` 硬顶**，prefetch 放大的是未 ack 预取**占用**（危害：扩容副本饿死 / 崩溃后整批等 visibility timeout 重投 / LLEN 监控失明）<br>· `S0-3a` 注入段断言改占用三指标口径、正例段补对照采样；queues 段改单次广播 + 按节点名判定（首跑全红系 `-json` 非法与逐容器隔离两处脚本 bug，已修）；`docker-compose.yml` §0.5 注释同步重写<br>· **设计约束不变**：`--prefetch-multiplier=1` 仍为硬约束<br>· 任务数 101、风险/验证分布不变；迁移表 31 边零触碰 |
| v0.13 | **S0-3a + S0-4 完成**（`S0-3a`/`S0-4` 行 ✅）：<br>· `S0-3a` 四段故障注入全绿（`scripts/s0_3a_topology_check.py`：正例 `max_active=3∧LLEN=6∧reserved=3`／注入 prefetch=4 `LLEN=0∧reserved=9∧max_active=3`＋基线回绿／queues 按节点名逐字、4 池无多余／beat==1）。queues 段首跑全红系两处脚本 bug（`-json` 非法应为 `-j`；`celery inspect` 是**广播命令**、无逐容器隔离），改单次广播+按节点判定后绿——两处已记 design-notes §7 第七轮"顺带产出"<br>· `scripts/` 目录理由：一次性断言驱动需 `django.setup()` + broker 双库直连（db0 传输/db9 计数）且带故障注入副作用（stop/start 容器），既非 app 代码亦非可入 CI 的 pytest 用例，独立于 `tests/`<br>· `S0-4` 八 app×health 容器实测 200；**envelope 拍板默认**：`code` 字符串 slug（DRF 异常按类名驼峰转蛇形大写、`AppError` 自带 slug）+ HTTP 真状态码解耦 + `request_id` uuid4hex；分页 `page_size=20`、`data={items,page,page_size,total}`；`AbstractBaseModel`（`created_at/updated_at/is_deleted/deleted_at` + 双 Manager + delete=软删）；`integrations/` 四包 §0.4 签名 ABC + DTO（`BuildResult` 六值 NOT_FOUND 分流基建/业务故障）+ 占位实现，**零 Django 双重断言**（源码 grep ＋ 无 settings 子进程 import `sys.modules` 检查）；探针任务迁 `apps/core/tasks.py` 兑现 S0-3 提交承诺（`config.probe`→`core.probe`；`core.slow_probe` 名字不变），`autodiscover_tasks()` 起用，拓扑回归复跑绿<br>· **依赖补登披露**：`httpx==0.28.1` 入 requirements——§0.2 规则 1 钦定 HTTP 库、S0-3 十二钉单漏列，属 DoD 可执行性必要兑现件而非新增审批依赖<br>· 任务数 101、风险/验证分布不变；迁移表 31 边零触碰；`prod.py` task_routes 注释维持（其指名的业务 tasks 属 S0-5/M1 落地件，S0-4 无真实业务任务可路由） |
| v0.14 | **F-8 拍板落地**（`design-notes.md` v0.9 §7 第八轮；开工基线冻结通道第四次启用，经 S0-5 Step 1 Hard Stop 呈报 + 产品确认）：<br>· **F-8**：§0.6a"版本基线 ≥14"证伪——Django 6.1.1 PG 后端 `minimum_database_version=(15,)` 在建连时硬校验（`NotSupportedError`，无 settings 可绕），与"特性逐条核对 14 兼容"正交；S0-1~S0-3a 全程零库（compose 依 §0.6a 不含 db），此前提直至 S0-5 首次 `makemigrations` 才暴露<br>· 本机环境：MacPorts 装 `postgresql16-server@16.15`、initdb、launchd 停 14 启 16（端口 5432 不变）；`tomatolite_dev` 在 16 上重建（原库为空，零数据迁移）；14 仅停不删、可回退<br>· 文档同步：§0.6a 基线 ≥15 / 客户端 `psql16` / S0-8 断言 `server_version >= 15` / §6 第 7 条；`.env.example`、`config/settings/base.py` 注释随迁<br>· 任务数 101、风险/验证分布不变；迁移表 31 边零触碰 |
| v0.15 | **S0-5 完成**（`S0-5` 行 ✅）：<br>· `core.User` = AbstractUser×AbstractBaseModel（`tl_user`；`role` 必填**无默认**——安全字段不兜底）；`UserManager` 双谱系合成（软删口×auth 协议口——`get_by_natural_key` 缺失会让 ModelBackend 登录时 AttributeError，登录测试锁死）；**软删即除权**：软删用户 `authenticate()` 必返 None、`all_objects` 仍见行（证软删非硬删）<br>· 拍板落地：①session+本地密码（`DEFAULT_AUTHENTICATION_CLASSES=[SessionAuthentication]`，SSO=M3 后端替换位）②system 账号 save() 强制 unusable password + staff/superuser 位即抛 ③`ROLE_RANK` business<dev<ops<admin、**system 显式缺席**（无秩）④默认权限开放（health 匿名 200 回归红线复验 8/8+schema/healthz 200）<br>· `HasRole`：视图声明 `required_roles`，匿名→401 `NOT_AUTHENTICATED`、缺角色→403 `PERMISSION_DENIED`（均 envelope）；**缺声明炸 ImproperlyConfigured**（两种静默都拒）；`role_at_least` 无秩恒 False；`enforce_separation_of_duties` 两要件（秩≥ops ∧ actor≠owner，同人拒/降格拒/system 拒/异人合格放）——守卫本体留 M2-25/27a 消费（Non-Goal 遵守）<br>· 测试：Step1 7 断言 + Step2 矩阵 20 格+13 = **40 条 S0-5 新断言，全套 57 passed**；`test.py` 加 MD5 hasher（17.4s→0.39s，仅测试口）<br>· 容器重建后 **S0-3a 拓扑回归四段全绿 exit=0**（正例/注入/queues/beat==1）<br>· 环境前置：本任务触发 **F-8**（PG 基线证伪，独立修订见 v0.14 行）；dev 库 `tomatolite_dev` 于 PG16 重建并首次 `migrate`（core.0001 单一初始迁移，manager 序列化经重建归一）<br>· 任务数 101、风险/验证分布不变；迁移表 31 边零触碰 |
| v0.16 | **S0-6a Mock GitLab 完成**（`S0-6a` 行 ✅）：<br>· 交付 `mocks/gitlab_fake/`（state/server/`__main__`/`__init__`，**纯 stdlib** `http.server.ThreadingHTTPServer`，拍板②零新依赖；主机进程 `python -m mocks.gitlab_fake --port 8061`，不进 compose（拍板①）；内存态+`_control/reset`（拍板③）；`integrations/gitlab/client.py` 未动（拍板④，对齐留 M1-13）——§6 第 1 条"假服务必须是真 HTTP 进程"兑现；Dockerfile 不 COPY `mocks/`<br>· §4.6 端点表全覆盖：项目解析（数字 id / urlencoded path 双口径，**OQ-1 两库并种** `tl/apps-entry`+`tl/playbooks`）、读文件（base64/blob_id/sha256/commit_id 字段级真）、POST commits（create/update/delete **整批预校验后整批应用**，**create 撞已存在→400 零副作用** = M2-02 幂等守卫断言点）、GET commits?path（新→旧+分页窗口）、`_control/emit_push`（`commits[].id` 与 `after` 可独立构造=§4.7③ 去重键素材；`X-Gitlab-Token` 请求级可覆盖=M1-24 坏 token 素材；before 按段首父/全零如实给）、`_control/deliveries`（对端 200/**5xx/拒连如实记**）、`_control/reset`（**状态+config 一并复位**，开关关得回去）<br>· 三时序构造能力（拍板⑤，消费方 M2-03a）：`webhook_timing=pre`→③ commit→webhook→响应；`=post`+`response_hold_ms=0`→① 响应→webhook；`=post`+hold>客户端读超时→② **客户端超时、落库与 webhook 照到**（`_json` 写包 `except OSError`——响应写不出去不得连坐 webhook 腿）；`rest_status` 数据面强制 5xx、`latency_ms` 真延迟——**`_control` 面永不被注入**，否则开关关不回去；每条开关**双相断言**（§0.7）<br>· 测试：Step1 数据面 8 + Step2 webhook 6 + Step3 故障 7 = **21 条 S0-6a 新断言，全套 78 passed**；webhook 一律以测试内**真 HTTP 捕获接收器**（记录 headers+payload）证明"送达且可解析"，不信 mock 自说<br>· Non-Goal 遵守：`docker-compose.yml`/`Dockerfile`/`integrations/`/`apps/`/`config/`/`design-notes.md` 零触碰；任务数 101、风险/验证分布不变；迁移表 31 边零触碰 |
| v0.17 | **S0-6b Mock Jenkins 完成**（`S0-6b` 行 ✅）：<br>· 交付 `mocks/jenkins_fake/`（state/server/`__main__`/`__init__`，**纯 stdlib** `http.server.ThreadingHTTPServer`，零新依赖；主机进程 `python -m mocks.jenkins_fake --port 8062`（与 8061 错开），不进 compose；内存态+`_control/reset` 状态与配置一并复位；`integrations/jenkins/` 未动，对齐留 M1-13）——§6 第 1 条"假服务必须是真 HTTP 进程"兑现<br>· **生产侧 `apps/core/hmac.py` 随本任务诞生**（§6 第 3 条：mock 签名路径必须 import 生产实现，否则签名校验路径根本没被测过；零 Django 双断言与 `integrations/` 同口径；header 名设计未钉→Spec 拍板 `X-TL-Signature: sha256=<hex>` / `X-TL-Timestamp: <epoch秒>`，窗 ±300s，per-job secret；`sign()` 收非 bytes 即 TypeError——§3.3 首坑"loads→dumps 重序列化"摁在类型层）<br>· §3.8 端点表全覆盖：GET job api/json（lastBuild/builds）、build api/json（**building=true 时 result=null 为显式键**——与 FAILURE 区分）、POST buildWithParameters **201 只回报 queue_id**（build_no 唯一出处是 queue item 映射——边 31"绝不猜 build_no"在 mock 层同步成立）、queue 三形状（`executable=null`+why / `cancelled=true` / 404 vanish）、consoleText 65536 分块写（大响应）、wfapi 三 stage 状态随构建阶段如实推进、**终态再 stop 返 200 `already`**（§0.4 裁定）<br>· 签名回调发送器：反假绿主证据=**接收侧用生产 `verify()` 对收到的原始字节重算验签**（不信 mock 自说"我签了"）；`bad_signature`=错密钥非乱字节（双相：验签挂在密钥上、不挂在格式上——错密钥反验得过，证明机制没坏）；`callback_repeat` 逐字节一致投递（=§3.4 dedup 双键输入）；自动终态路径与手动 `_control/emit_callback` 走同一实现（手动口不受 drop 管辖，测试构造不碍事）<br>· §3.8 故障清单全收口：`callback_drop_rate`（seeded RNG 同 seed 同剧本、CI 可复现；丢失**照样如实记 DROPPED**——丢了不记则 drop 开关自己成不可断言黑洞；重点断 **drop=1.0 时 REST 面照样如实可查**=sweeper 修复的前提）、build hang **永不到终态也永不回调**（关回后新触发自愈落终态出回调，旧 hang 单不受牵连）、`rest_status`/`latency_ms` 只打数据面（**`_control` 面永不被注入**——否则开关关不回去；注入期零副作用、解除后全量可查）、`stop_status=500` 且**中止不执行**（构建仍 building——M2-34 "stop 失败不阻塞终止"素材）、`trigger_response_hold_ms` **先调度后持响应**——回调可**抢在 201 之前**到达（§4.4 同型竞态素材；201 写包 `except OSError`，客户端先走不连坐）<br>· `_control/config` 16 开关**逐键校验**：未知键→400、坏值→400 **零写入**（不允许半生效），`callback_seed` 写入即重播 rng<br>· 测试：Step1 hmac 本体+REST 数据面 12 + Step2 签名回调 7 + Step3 故障注入 11 = **30 条 S0-6b 新断言，全套 108 passed**；每条开关双相断言（§0.7）<br>· Non-Goal 遵守：`docker-compose.yml`/`Dockerfile`/`integrations/`/`config/`/`design-notes.md` 零触碰（`apps/` 内仅 Spec 批准的 `core/hmac.py` 新增）；回调接收 API（M2-05）不在本任务建；零新增依赖；任务数 101、风险/验证分布不变；迁移表 31 边零触碰 |
| v0.18 | **S0-6c Mock 目标主机完成**（`S0-6c` 行 ✅）：<br>· 交付 `mocks/host_fake/`（state/server/ssh/`__main__`/`__init__`）；**新增依赖 1 项=Spec 拍板①批准** `asyncssh==2.24.0`——§5.2 `SshTransport` 设计钦定库（"真 SSH-2 协议进程"stdlib 造不出，§6 第 1 条推论），本任务仅以「mock 服务端 + 测试客户端」使用、生产消费方 M2-10 不提前实现；传递依赖 cryptography/typing_extensions 均已在位，零连带；主机进程 `python -m mocks.host_fake` 不进 compose；端口面 app 8064 / tcp 8065 / ssh 8066 / control 8067（**8063 刻意弃用**——6b 冒烟的一次性接收器，记录在案防伪空洞）；密钥引导 `var/mockhost/`（`var/` 已 gitignore；重启不换脸）<br>· **控制面独立端口=硬设计**（§6"开关关得回去"推论）：`refuse`/`blackhole` 整张弄死 app 端口，控制面若同端口就关不回去；`refuse` 关/复听**端口号恒定**（探活 URL 按端口钉死，换端口=谎报另一台机器）<br>· §5.4.1 探活四形状**可判且互不混淆**（M2-11 上修结论唯一依据）：CONNECT_REFUSED=真关听→内核 RST / TIMEOUT_READ=blackhole 收了永不回 / STATUS_MISMATCH=配置的码 / TIMEOUT_CONNECT=**消费方以 RFC5737 `192.0.2.1` 构造**（拍板③：本机内核 backlog 先完成握手，mock 造不出、也绝不假装造得出）<br>· **Step 3 撞出的实据教训**：本机开发 shell 带 `HTTP_PROXY`，httpx 默认 `trust_env=True` 外连经代理——`192.0.2.1` 的 connect-timeout 被**伪装成 read-timeout**（连代理必成）；§5.4.1 两形状在消费方侧真会混淆，探活客户端 `trust_env=False` 纪律钉进判料函数注释（生产 M2-11 消费）<br>· `process_alive` 与 app 端口**联动**：进程死 ⇒ `effective_app_mode`=refuse（进程死端口活=mock 自相矛盾）；显式 `http_mode` 优先级低于联动（三口全 0→1/3/1 与 HTTP 面同向=可下结论的双相素材）；版本注入与 HTTP `/version`、SSH `cat VERSION` 同文件同出处三脸一致<br>· 日志面=真文件+**真轮转**：`os.replace` rename+重建 ⇒ inode 真变（§5.7③ 回退信号可判）；用例双相：种子含历史 ERROR——offset 增量判绿 vs tail 全窗判红（假阳性两种判料本就分岔）、轮转后旧 offset 读空=**假绿素材**、inode 校验逼回退 tail、历史入 `app.log.1` 仍可查不丢料<br>· SSH exec 方言=**Linux/GNU coreutils**（生产目标全 Linux，不是本机 macOS）：`pgrep`/`systemctl is-active`（exit 3 systemd 惯例）/`kill -0`/`cat`/`tail -n`/`stat -c` 六命令、未知命令 **exit 127**（§5.3 "命令不存在" ERROR 素材）；`cat/tail/stat` 只服务 `state.dir` 之下（路径逃逸=越界不存在，不泄露本机 FS）；纯函数 `run_command` 与线路层分离——方言可脱离 SSH 直接断言<br>· 认证面（§1132 BatchMode 首坑反面）：ed25519 publickey-only，password 请求**只立拒、绝不交互发问**——错钥/无钥断"拒 <2s"；测试客户端一律 `asyncio.wait_for` 包装，**挂起=可见失败**；asyncssh 2.24 实据一课：`exec_requested` 内发输出违反"session_started 前不得发输出"契约 ⇒ 客户端永久等待（Step 2 现场撞出并钉死）<br>· 6a/6b 同族纪律：`_control/config` 逐键校验（未知键/坏值→400 零写入）、reset 状态+配置+种子一并复位、**`_control` 面永不被注入**、响应写包 `except OSError`；每条开关双相断言（§0.7）<br>· 测试：Step1 数据面 12 + Step2 SSH 线路 8 + Step3 三类用例收口 5 = **25 条 S0-6c 新断言，全套 133 passed**<br>· Non-Goal 遵守：`docker-compose.yml`/`Dockerfile`/`integrations/`/`apps/`/`config/`/`.env.example`/`design-notes.md` 零触碰；无 M2-10 `SshTransport` 生产实现（asyncssh 仅 mock 服务端+测试客户端）；全端口 bind 127.0.0.1；任务数 101、风险/验证分布不变；迁移表 31 边零触碰 |
