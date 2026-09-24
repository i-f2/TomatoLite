# S0-2 Spike 报告：Celery + Channels 事件桥

- **日期**: 2026-09-23
- **环境**: macOS 15.5 arm64 / pyenv 3.12.13 / celery 5.6.3（prefork）/ channels 4.3.2 + channels_redis 4.3.0 / daphne 4.2.3 / ansible-runner 2.4.3 + ansible-core 2.21.4（与 S0-1 同桩）/ redis:7-alpine（临时容器 `tl-s02-redis`，127.0.0.1:6379）
- **被验设计**: design-notes v0.6 §2.3（事件泵三级：handler→Redis 双写→Persister→group_send）与 §2.4（WS 批帧转发）；S0-1 附B 移交项
- **代码形状**: 不建 Django 工程，裸 ASGI3（`server.py`）+ 独立 `pump.py`，与 §2.3 图逐点对应；落库以 JSONL 模拟（"handler 内零 DB" 的硬约束由线程探针与代码走查共同担保）

## 结论总览

| # | 问题（Spec SQ） | 结论 | 实据 |
|---|---|---|---|
| SQ1 | run_async 下 handler 回调线程模型 | **✓ 符合设计**：runner 事件读取独立线程，非 worker 主线程 | `handler_threads=["Thread-1 (run)"]`；A5 |
| SQ2 | 单事件 >60KB 时 channel layer 行为 | **✗ 证伪（F-6）**：4.3.0 无尺寸上限，200KB 原样投递 | E1：`received size=200000`，`max_message_size` 属性不存在 |
| SQ3 | LTRIM 追尾后缺口可否辨识 | **✓ 数据侧可判**：`since < oldest` 必产生 resync 信号的前提成立 | E2：灌 6000 → 窗存 1000..5999，`hole_detected=true` |
| SQ4 | 高吞吐批帧端到端计数收口 | **✓ 零丢失**：5006 发布 = 5006 落盘 = 5006 WS 收到，28 批帧 | A1；`driver_result.json` |
| SQ5 | 取消语义（显式置位） | **✓ 可用**：置位后 2.26s 于 task 间隙生效，`status=canceled`，流无半行 | E3：39/39 事件可逐行解析 |
| SQ6 | 终态收口 / flush 超时可否观测 | **✓ 两路皆验**：正常 `flush_timeout=false`；人为拖慢后 5.0s 精确放弃、上层可观测（ORPHANED 前提成立） | A4 + E4 |
| SQ7 | seq 保序无洞 | **✓**：批帧展开恰为 `0..5005` 严格递增 | A2 |

## F-6（证伪，提请下一次 /spec 修订）

**§2.3/§2.4 隐含假设"channel layer 有 max_message_size（≈60KB）兜底截断超限消息"不成立。**

- 实测（channels_redis 4.3.0）：`RedisChannelLayer` 对象无 `max_message_size` 属性；`group_send` 200KB 消息**不 raise、不截断、不丢弃**，订阅通道**完整收到 200000 字节**；无订阅者时同样静默成功。
- 影响：批帧上线尺寸完全由应用层自限。最坏情形 = 单批 200 条 × 全量 stdout，帧可达 MB 级直贯 Redis→daphne→浏览器。§2.3 归一化"excerpt 截断"由**硬护栏降级为唯一防线**，且 WS 帧（非仅单事件）也须设尺寸预算。
- 建议修订方向（待拍板，本 spike 未动 design-notes）：M1-15 归一化 excerpt 上限 + Persister 批帧字节预算（超限即提前 flush）双护栏；`group_send` 无订阅者静默 → 前端断开后 Persister 不感知，属 §2.4 既有语义（fire-and-forget），无需改。

## 其余 Q 详单

### SQ1 线程模型 —— ✓
- 终态桩 `tasks.run_demo` 在 prefork 子进程（pid 55786）执行；`run_async` 返回 `(thread, Runner)` 二元组（spike 实据，文档常见"AsyncRunner 对象"写法不准确）。
- handler 探针只见 `Thread-1 (run)`；Persister 独立线程 `RunEventPersister`（非 daemon，§2.3 原文照做）；`group_send` 桥在专用 `ChannelLayerLoop` loop 线程。三级线程隔离 + "handler 内只有 Redis 写" 均落地。
- 与 §2.3 差异: 无。

### SQ3 LTRIM 追尾 —— ✓（数据侧）
- 6000 连发后 `llen=5000`、头 seq 5999、尾 seq 1000；`since=5` 的请求可从 `oldest>since` 判定缺口。
- **边界说明**: "返回显式 resync 帧而非静默接洞"是 X-04 协议 + M1-16 消费者职责，本 spike 只证明**数据层可判定性**；服务端实现时按 §2.3 补 `{"type":"resync","oldest":…}` 帧。
- Step 2 现场同步验证环基线：`A3_ltrim_ring`（llen==5000 且头==published-1）。

### SQ4/SQ7 吞吐与保序 —— ✓
- stress.yml 5000+ 事件 + 六态 playbook，端到端 11.93s；发布/落盘/WS 三方计数 5006/5006/5006；28 批帧（≈200/批，500ms 定时器与 200 条阈值均触发过）；seq 展开恰为 0..5005。
- `group_send_errors=[]`：daphne 订阅在场时全程无错。

### SQ5 取消 —— ✓
- 3s 显式置位 `threading.Event`（生产换 Redis cancel-key，形态同 `tasks.py` 内 `tl:run:{id}:cancel` 探测）→ `cancel_callback` 在下一 task 间隙返回 True；置位→终止实测延迟 2.26s（≈一个 task 时长，与 S0-1"取消粒度在 task 间"一致）。
- `status=='canceled'`、rc≠0 语义由 runner 给；事件流 39 条逐行可解析，**无半行、无终态漏发**（Persister 收口 flushed==published）。

### SQ6 收口与超时 —— ✓（两路）
- 正常路：`wait_terminal_and_close` 在 runner 结束后 stop(expected)→join(5)，`flush_timeout=false`。
- 故障路：注入 10s/批的假 PG 卡死 → join(5) **5.0s 精确放弃**，返回 `True` 供上层写 ORPHANED；慢批刷完后线程自行收口（勿清 `_closing`，否则非 daemon 线程永挂——spike 期实踩）。
- 附实现陷阱（写入移交）：`Thread._stop` 是 CPython 私有方法名，Persister 的关闭标志曾占用它导致 `join()` 炸 `'Event' object is not callable`；生产代码用 `_closing` 并保留注释。

## 与 §2.3/§2.4 差异汇总

| 项 | 设计原文 | 实测 | 处置 |
|---|---|---|---|
| F-6 | （隐含）layer max_message_size 兜底 | 无兜底，原样透传 | 提请 /spec 修订 §2.3 截断护栏表述 |
| run_async 返回值 | 未写明 | `(thread, Runner)` 二元组 | 移交 S0-3/生产代码 |
| `new_channel()` | 未写明 | 协程（须 await） | 同上 |
| prefork+macOS | 未涉及 | 需 `FORKED_BY_MULTIPROCESSING=1` 否则 fast_trace ValueError | 移交：S0-3 部署形态复核（生产 Linux 下是否仍触发待验） |

## S0-3 移交清单（承接 S0-1 附B）

1. **栈锁定**: celery 5.6.3 / channels 4.3.2 / channels_redis 4.3.0 / daphne 4.2.3 / redis-py 8.1.0（`requirements.txt` 全量 freeze）。
2. **macOS prefork 修复**: worker 环境需 `FORKED_BY_MULTIPROCESSING=1`；Linux 生产不复现与否列入 S0-3 断言。
3. **取消键**: `tl:run:{id}:cancel`（SET EX，跨进程可观测）形态已被 E3 证明可用；M1 装配时接 API 取消端点。
4. **批帧双护栏**（随 F-6 修订落地）: excerpt 截断 + 批帧字节预算。
5. **resync 服务端语义**: `since<oldest → 显式 resync 帧`（数据可判定性已证，X-04 协议细化）。
6. **命名戒律**: 线程类属性禁占 `_stop`/`_state` 等 `Thread` 私有名。

## 人工观察（Spec 附带，非 DoD）

```bash
.venv/bin/python driver.py --manual   # 起 daphne:8775 + prefork worker
# 浏览器打开 demo.html → 连 ws://127.0.0.1:8775/ws/run/demo1/
# 另开终端: .venv/bin/python -c "from celery import Celery; \
#   Celery(broker='redis://127.0.0.1:6379/0').send_task('spike.run_demo',args=['demo1','stress.yml',0.0],protocol=2)"
```

预期：页面滚动显示批帧计数，与 A1 同源。

## 附录：复跑与清理

```bash
# 前置：redis 容器（用户批准的临时载体）
docker start tl-s02-redis
.venv/bin/python driver.py        # Step 2 端到端，末行 "S0-2 END-TO-END PASS"
.venv/bin/python edge_tests.py    # Step 3 边界，末行 "EDGE PASS"，exit 0
# 回滚：
#   docs/spikes/s0-2/ 整目录未提交前直接删除；redis 容器 docker rm -f tl-s02-redis
#   已提交则 git revert <commit>（仅文档+spike 目录，零生产代码污染）
```
