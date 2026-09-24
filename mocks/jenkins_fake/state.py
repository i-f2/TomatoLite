"""内存 Jenkins 态（拍板③：无持久化）。队列→构建生命周期见 §3.8；
config 键的语义在注释里钉死，REST 故障面/回调面的**应用**分别落在 Step 3/2。
"""

from __future__ import annotations

import datetime as dt
import itertools
import random
import threading

SEED_JOBS = ["tl-release"]  # 单段 job 名（§3.8 端点表 /job/{job}/ 形态）

DEFAULT_CONFIG = {
    # ---- 生命周期（trigger 时读定）----
    "queue_mode": "promote",  # promote | hang(executable 恒 null) | cancelled | vanish(队列项 404)
    "build_mode": "finish",  # finish | hang(building=true 永不终态、永不回调)
    "build_duration_ms": 0,
    "build_result": "SUCCESS",  # 终态写 SUCCESS|FAILURE；ABORTED 只能由 stop 产生
    "console_kb": 4,
    # ---- REST 故障面（Step 3 接线）----
    "latency_ms": 0,
    "rest_status": None,  # 非空 → 数据面端点强制该状态码
    "stop_status": None,  # 非空 → stop 端点强制该状态码（M2-34 stop 失败不阻塞）
    "trigger_response_hold_ms": 0,
    # ---- 回调面（Step 2 接线）----
    "notify_url": None,  # 终态后向其 POST 签名回调
    "notify_delay_ms": 0,
    "callback_drop_rate": 0.0,  # seeded RNG，CI 可复现（拍板⑤）
    "callback_seed": 42,
    "callback_repeat": 1,  # 同 build 重复投递 N 次（dedup 双键素材）
    "bad_signature": False,  # 真 body × 错 secret
    "secret": "mock-jenkins-hmac-key",
}


def now_ms() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)


class State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._qids = itertools.count(100)
        self.builds: dict[str, list[dict]] = {}
        self.queue: dict[int, dict] = {}
        self.callbacks: list[dict] = []
        self.config: dict = {}
        self.rng = random.Random(DEFAULT_CONFIG["callback_seed"])
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.builds = {}
            self.queue = {}
            self.callbacks = []
            self.config = dict(DEFAULT_CONFIG)
            self.rng = random.Random(self.config["callback_seed"])
            for job in SEED_JOBS:
                self.builds[job] = [self._mk_build(job, n, result="SUCCESS") for n in (1, 2)]

    def _mk_build(
        self,
        job: str,
        number: int,
        *,
        result: str | None = None,
        building: bool = False,
        ts: int | None = None,
        params: dict | None = None,
        commit_sha: str = "",
    ) -> dict:
        return {
            "job": job,
            "number": number,
            "url": f"http://mockjenkins/job/{job}/{number}/",
            "building": building,
            "result": result,
            "timestamp": ts if ts is not None else now_ms(),
            "duration": 0 if building else 1000,
            "params": params or {},
            "commit_sha": commit_sha,
            "notified": False,
        }

    def display_name(self, b: dict) -> str:
        code = b["params"].get("TICKET_CODE", "")
        return f"#{b['number']} {code}".strip()

    def actions(self, b: dict) -> list[dict]:
        return [{"parameters": [{"name": k, "value": v} for k, v in b["params"].items()]}]

    # ---- 触发 ----

    def trigger(self, job: str, params: dict) -> tuple[int, int | None] | str:
        """返回 (queue_id, build_number|None)；job 未知返回 'no_job'。
        queue_mode 的四种结局在 §3.8 故障清单逐一对应。"""
        with self.lock:
            if job not in self.builds:
                return "no_job"
            qid = next(self._qids)
            mode = self.config["queue_mode"]
            if mode == "vanish":
                return qid, None  # 201 照给，队列项永不可查
            why = None
            cancelled = False
            exec_no = None
            if mode == "promote":
                exec_no = len(self.builds[job]) + 1
                # hang 与否都建 build；差别在 server 侧是否调度 finish 落锤
                self.builds[job].append(
                    self._mk_build(
                        job,
                        exec_no,
                        building=True,
                        params=params,
                        commit_sha=params.get("COMMIT_SHA", ""),
                    )
                )
            elif mode == "hang":
                why = "Waiting for next available executor"
            elif mode == "cancelled":
                cancelled = True
                why = "Cancelled by tl-mock"
            self.queue[qid] = {
                "id": qid,
                "job": job,
                "cancelled": cancelled,
                "why": why,
                "exec": exec_no,
                "params": params,
                "in_queue_since": now_ms(),
            }
            return qid, exec_no

    # ---- 终态 ----

    def finish(self, job: str, number: int) -> bool:
        """Timer 落锤：building→终态。返回是否发生迁移（stop 已抢落则 False）。"""
        with self.lock:
            b = self.get_build(job, number)
            if b is None or not b["building"]:
                return False
            b["building"] = False
            b["result"] = self.config["build_result"]
            b["duration"] = max(now_ms() - b["timestamp"], 1)
            return True

    def stop(self, job: str, number: int) -> str:
        """stop 语义（§0.4 裁定：终态再 stop **不视为错误**）：
        'aborted' | 'already' | 'no_job' | 'no_build'。"""
        with self.lock:
            if job not in self.builds:
                return "no_job"
            b = self.get_build(job, number)
            if b is None:
                return "no_build"
            if not b["building"]:
                return "already"
            b["building"] = False
            b["result"] = "ABORTED"
            b["duration"] = max(now_ms() - b["timestamp"], 1)
            return "aborted"

    def get_build(self, job: str, number: int) -> dict | None:
        for b in self.builds.get(job, []):
            if b["number"] == number:
                return b
        return None

    def queue_item(self, qid: int) -> dict | None:
        return self.queue.get(qid)

    # ---- console / stages（数据形状，Step 3 的 console_kb 拉大也走这里）----

    def console_text(self, b: dict) -> str:
        lines = [
            "Started by Tomatolite release platform",
            f"Running on mock-agent-1 in /var/jenkins/workspace/{b['job']}",
            f"Checking out commit {b['commit_sha'] or 'n/a'}",
        ]
        lines += [f"[Pipeline] stage {s}" for s in ("Checkout", "Build", "Deploy")]
        for k, v in b["params"].items():
            lines.append(f"param {k}={v}")
        out = "\n".join(lines) + "\n"
        target = self.config["console_kb"] * 1024
        pad = "Processing work item ..........................................\n"
        while len(out) < target:
            out += pad
        out += f"Finished: {b['result'] if not b['building'] else 'IN_PROGRESS'}\n"
        return out

    def stages(self, b: dict) -> list[dict]:
        names = ("Checkout", "Build", "Deploy")
        if b["building"]:
            status = ("COMPLETED", "IN_PROGRESS", "NOT_EXECUTED")
        elif b["result"] == "ABORTED":
            status = ("COMPLETED", "ABORTED", "NOT_EXECUTED")
        elif b["result"] == "FAILURE":
            status = ("COMPLETED", "COMPLETED", "FAILED")
        else:
            status = ("COMPLETED", "COMPLETED", "COMPLETED")
        return [
            {"id": i + 1, "name": n, "status": s} for i, (n, s) in enumerate(zip(names, status))
        ]
