# job_events 命名规则与懒加载可行性（S0-1 Step 3 实测）

实测环境：ansible-runner 2.4.3 / ansible-core 2.21.4 / pyenv Python 3.12.13（见 `run_meta.json.versions`）。

## 目录结构

```
artifact_dir/<job_uuid>/            # 一次 run 一个目录（job_uuid 即 runner_ident，见事件 payload.runner_ident）
├── command                         # 实际执行的 ansible-playbook 命令行
├── rc                              # ansible-playbook 退出码（本次 main=4：存在 unreachable）
├── status                          # failed / successful
├── stdout / stderr                 # 全量控制台输出（ANSI 色码保留）
├── fact_cache/                     # gather_facts:false 时也有目录
└── job_events/
    └── <counter>-<event_uuid>.json # ← 命名规则：序号-事件自身uuid（不是 parent_uuid）
```

- `counter` 与事件流顺序严格一致（从 1 起），因此**可以按 counter 二分/续读**，懒加载成立。
- 事件数 ↔ 文件数一一对应：main 41/41，stress 5006/5006。
- 每文件是该事件的完整 payload JSON，与 `event_handler` 收到的 dict **逐字段一致**（对照 `events_stream.jsonl` 同 counter 行）。
- 另有顶层 `parent_uuid`：play 事件层级链用，归一化表不依赖。

## payload 通用顶层字段

`uuid, counter, event, created(ISO8601), pid, runner_ident, start_line, end_line, stdout, event_data[, parent_uuid]`

- **`stdout` 在事件级直接可得**（start_line/end_line 指向全量 stdout 文件的行区间）——§2.3"stdout 走 artifact_dir 懒加载"假设成立，且单事件文本不必读文件即有。
- stress run（5000 事件）目录下 job_events 全部落盘无丢失。

## 采样

`samples_index.json`（20 个样本，含同 kind 的 ignore_errors/rescued 等变体），由 `make_samples.py` 可重跑生成。
