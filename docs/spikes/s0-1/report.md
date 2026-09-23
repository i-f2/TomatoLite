# S0-1 spike 报告：ansible-runner 事件流实据

- 日期：2026-09-23　执行人：AI（/spec 流程）　验证方式：全部命令可重跑（见 §附）
- 环境：**ansible-core 2.21.4 / ansible-runner 2.4.3 / Python 3.12.13（pyenv）**，
  依赖钉死于 `requirements.txt`，隔离在 `.venv/`（未进生产 requirements）。
  注：pip 在 py3.12 上解析到的 ansible-runner 最新可装版本为 2.4.3（上游 3.x 未取到），
  **本报告全部实据基于 2.4.3**；若生产栈改用 runner 3.x，S0-3 需复核本表结论是否漂移。
- 被检假设：`docs/design-notes.md` §2.3「事件泵 → Channels 桥接」（未修改，按 Spec 红线）。

## 执行摘要（一句话）

§2.3 的骨架成立（事件可全量捕获、白名单 7 种 kind 全部存在、stdout 可懒加载、stats 可映射、
速率无压力）；但发现 **3 处与假设不符**（回调线程、item 级 kind 不在白名单、RESCUED 无事件级标志）
和 **2 处需 S0-2 注意的行为**（cancel_callback 非取消期也被调用、unreachable 实际耗时 ≈10s）。

---

## Q1 白名单 7 kind 的确切事件名 —— 成立，但白名单不完整

**结论**：7 种全部按原名出现（含 `runner_on_start`）；但流中还有白名单之外的 kind，
其中 **`runner_item_on_ok` 承载 loop 逐项结果，漏掉它就永远写不出 `AnsibleTaskResult` 的 item 行**。

**实据**（`events_stream.jsonl`，行号==payload.counter；`run_meta.json.main.event_kind_counts`）：

| kind | 次数 | 首现行号 | 备注 |
|---|---|---|---|
| playbook_on_start | 1 | L1 | 白名单外 |
| playbook_on_play_start | 2 | L2 | 白名单外 |
| playbook_on_task_start | 11 | L4 | ✓ |
| runner_on_start | 11 | L5 | ✓（与 task_start 一一对应，含 skip 的 task） |
| runner_on_ok | 6 | L6 | ✓ |
| runner_on_skipped | 1 | L15 | ✓ |
| runner_on_failed | 3 | L18 | ✓ |
| **runner_item_on_ok** | **3** | **L30** | **白名单外，见 Q5** |
| runner_on_unreachable | 1 | L40 | ✓ |
| playbook_on_stats | 1 | L41 | ✓ |
| verbose | 1 | L5(仅stdout) | 白名单外，ansible 警告走这里 |

stress run 复核：5000 次循环项**全部**是 `runner_item_on_ok`（`events_stream_stress.jsonl:L5–L5004`），
聚合的 `runner_on_ok` 仅 1 条（L5005）。

**与 §2.3 差异**：⚠️ 白名单需增补 `runner_item_on_ok`（及对称的 `runner_item_on_failed/skipped/unreachable`
——本 spike 未触发后三种，属残留缺口，S0-2 故障注入时补验）。

## Q2 payload 字段与 duration —— 成立

**实据**：`samples/main_runner_on_ok.json` 等。顶层统一为
`uuid, counter, event, created, pid, runner_ident, start_line, end_line, stdout, event_data[, parent_uuid]`；
`event_data` 关键字段：`task, task_uuid, task_action, resolved_action, task_args, task_path, host,
play, play_uuid, play_pattern, duration, start, end, res`。

- `duration`（秒，float）在 ok/failed/skipped/unreachable 均在（如 L12=0.159615、L15=0.005214）→ `duration_ms = round(duration*1000)` 可行。
- ⚠️ **item 事件 `duration=None`**（L30；stress L5–L5004 同样）——逐项行拿不到独立耗时，聚合行才有（L33=0.012888，stress L5005=8.39664）。
- `res` 为模块返回 dict（`changed/rc/msg/item/_ansible_verbose_always` 等），失败时含 `msg/exception`。

## Q3 event_handler 线程 —— **不成立（按字面）**

**结论**：同步 `ansible_runner.run()` 时，handler 在**调用线程**回调——两次 run 的
`handler_thread_names` 均为 `{"MainThread": …}`（`run_meta.json.{main,stress}.handler_thread_names`，
41/41、5006/5006 条全部如此），且 payload 带 `pid`，无跨线程迹象。

**与 §2.3 差异**：⚠️ 图中「ansible-runner 在独立线程回调」仅在 `run_async()` 用法下成立。
不影响架构（Persister 仍是自己起的独立线程），但影响两处实现措辞：
(a) 「不允许在 event_handler 里同步 INSERT」的理由从"阻塞独立线程"变为"阻塞 pump 所在 worker 线程"——结论更强而非更弱；
(b) 生产用 `run()` 还是 `run_async()` 是 S0-2 必须钉死的第一个决策（决定 worker 占用模型）。

## Q4 stdout 与 job_events 懒加载 —— 成立（且好于假设）

**实据**（`notes-job-events.md`）：`artifact_dir/<job_uuid>/job_events/<counter>-<event_uuid>.json`；
counter 严格递增可二分续读；事件数↔文件数 1:1（41/41、5006/5006，逐字段一致）；
**且 handler 收到的事件本身就带 `stdout` 全文 + `start_line/end_line` 指向 `artifact_dir/<job_uuid>/stdout` 的行区间**——多数场景无需懒加载，超长输出才回文件。

## Q5 ignored / rescued / skipped / loop —— 两处不成立

| 结局 | 触发 | 事件实据 | 判别字段 |
|---|---|---|---|
| ignore_errors 失败 | T4 | `runner_on_failed` @L18 | ✅ `event_data.ignore_errors=True`（T5a @L21 同）|
| skipped | T3 | `runner_on_skipped` @L15 | ✅ 有独立 kind，无 res |
| loop item | T6 | `runner_item_on_ok` @L30–32 | ✅ `event_data.res.item`；⚠️ kind 在白名单外；⚠️ 无 duration |
| **rescued** | T5b1→T5b2 | `runner_on_failed` @L24（ig=False，**无任何 rescued 标志**）→ `runner_on_ok` @L27 | ❌ **事件级无法区分"被 rescue 的失败"与"致命失败"** |

**实据补充**：rescued 只体现在**聚合** `playbook_on_stats`（L41）：
`event_data.rescued={"localhost":1}, ignored={"localhost":2}, failures={}, dark={"192.0.2.1":1}`——
T5b1 被 rescue 后 `failures` 不计数，与致命失败在流内不可分。
样例：`samples/main_runner_on_failed.json`（被 rescue 的那个）vs `samples/main_runner_on_failed_ignore_errors.json`。

**与 §2.3/§1.x 差异**：⚠️⚠️ `AnsibleTaskResult.result_status` 的 **RESCUED 六值之一无法从事件流推导**。
候选出路（留待决策，本报告不改设计）：
① 解析 `task_path`+block 结构预知 rescue 关系（脆弱）；② 自定义 stdout callback / 插件注入标志（引依赖）；
③ 降级方案：`failures==0 且 rescued>0` 时把当次 run 内所有未标 ignore_errors 的 `runner_on_failed` 按 task_uuid 与后续 rescue-ok 的相邻顺序配对（启发式）；④ 把 RESCUED 从"任务级"降为"run 级计数"。

## Q6 playbook_on_stats → AnsibleRun.stats —— 成立

`samples/main_playbook_on_stats.json`：`changed/dark/failures/ignored/ok/processed/rescued/skipped`
均为 `{host: count}` dict（另有 `artifact_data`），可直接映射。**注意**：`failures` 因 rescued 恒空（见 Q5）。

## Q7 事件速率 —— 无压力

`run_meta.json.stress`：**5000 事件 9.10s ≈ 550 ev/s**（含 handler 逐条落 JSONL 的开销），
未观察到丢事件/乱序（counter 连续）。§2.3 预估的 200 主机 × 100 task ≈ 20000 事件量级
对 pump 不构成反压风险；风险仍在 handler 内做同步 IO（未测 DB 路径，属 S0-2）。

---

## 附A 归一化映射表（M1-15 输入）

`RunEvent(seq, kind, host, task, status, changed, duration_ms, stdout, stderr)`：

| RunEvent 字段 | 来源 | 备注 |
|---|---|---|
| seq | `counter` | 全局单调，可直接复用 |
| kind | `event` | 白名单 7 + `runner_item_on_ok`（+failed/skipped/unreachable 待 S0-2 验证）|
| host | `event_data.host` | task_start/stats 级事件可能无 host → NULL |
| task | `event_data.task` | task_start 里亦有 `name`（同值）|
| status | kind 映射 | ok→(changed?CHANGED:OK)；failed+`event_data.ignore_errors`→IGNORED；failed 其余→FAILED/RESCUED ❌（见 Q5）；skipped/unreachable 直映；item 事件按 `res.failed` 再分 |
| changed | `event_data.res.changed` | item 行同样可得；failed 无 res 时 False |
| duration_ms | `round(event_data.duration*1000)` | ⚠️ item 行 NULL |
| stdout | 事件级 `stdout` | 超长回退 `artifact_dir/<job_uuid>/stdout` 的 `[start_line, end_line]` |
| stderr | ❌ **payload 无独立 stderr** | 失败/警告文本混在 `stdout`（ANSI 色码需剥）与 `res.msg/exception`——§2.3 归一化表的 stderr 列改为"从 res+stdout 提取"或删除 |

## 附B S0-2 交接清单

1. 钉死 `run()` vs `run_async()`（决定 worker 线程模型，见 Q3）。
2. 故障注入补验：`runner_item_on_failed/skipped/unreachable`、多 host 并发、`runner_on_start` 与 start_line 对齐。
3. cancel_callback 语义：**正常跑完也被调用 3 次/2 次**（`run_meta.json.*.cancel_callback_invocations`）——它被周期轮询调用，取消标志必须显式置位，绝不能"以被调用当取消"。
4. unreachable 实测 10.03s（ConnectTimeout=3，ansible ssh 重试到 ~10s）→ 黑洞主机的 RunEvent 延迟预算按 ≥10s 设。
5. Redis/Celery/WebSocket 全链路（本 spike 未碰，按 Spec 排除）。

## 附C 复跑命令（DoD 自检）

```bash
cd docs/spikes/s0-1
.venv/bin/python capture.py            # exit 0 + CAPTURE PASS + 41 事件
.venv/bin/python capture.py --stress   # exit 0 + 5006 事件 ≈550 ev/s
.venv/bin/python make_samples.py       # 20 samples + samples_index.json
jq -r .event events_stream.jsonl | sort | uniq -c
```

## 附D 被证伪/修订清单（Hard Stop 呈报，未动 design-notes）

| # | §2.3 原假设 | 实据 | 性质 |
|---|---|---|---|
| F-1 | "event_handler 在独立线程回调" | `run_meta.json.*.handler_thread_names`=MainThread（全量） | 字面证伪（`run()` 下）；架构结论不变 |
| F-2 | 白名单 7 kind 覆盖所需事件 | `runner_item_on_ok` 承载全部 loop 逐项结果，不在名单 | 白名单不完整，需增补 |
| F-3 | `result_status` 含 RESCUED 可由事件流得出 | 被 rescue 的 `runner_on_failed` 无任何事件级标志（L24） | **证伪**，四选一决策待拍板 |
| F-4 | RunEvent 有 stderr 列 | 顶层无 stderr 字段，stderr 混在 stdout/ANSI 与 res.exception | 归一化表需修订 |
| F-5 | item 行有 duration | `event_data.duration=None`（L30 等） | 缺口，item 行 duration_ms 允许 NULL |
