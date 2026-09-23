#!/usr/bin/env python3
"""S0-1 Step 3：从 job_events/ 为每种事件 kind 抽一个代表样例到 samples/。

命名规则实测：artifact_dir/<job_uuid>/job_events/<counter>-<event_uuid>.json
（第二段是该事件自身的 uuid，与 payload.uuid 一致；counter 与事件流顺序一致）。
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN_DIRS = {
    "main": sorted((HERE / "artifacts/main").glob("*/job_events"))[-1],  # 最后一次完整 run
    "stress": sorted((HERE / "artifacts/stress").glob("*/job_events"))[-1],
}
SAMPLES = HERE / "samples"
INDEX = {}


def harvest(run: str, job_events: Path):
    for path in sorted(job_events.iterdir()):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        kind = data.get("event")
        if not kind:
            continue
        ed = data.get("event_data") or {}
        res = ed.get("res") if isinstance(ed.get("res"), dict) else {}
        # 同一 kind 内的语义变体单独留样（§2.3 的 ignored/rescued/changed/item 假设都靠这些区分）
        variant = ""
        if kind == "runner_on_failed":
            variant = "_rescued" if ed.get("rescued") else ("_ignore_errors" if ed.get("ignore_errors") or res.get("ignore_errors") else "")
        elif kind == "runner_on_ok" and "ignore_errors" in json.dumps(ed):
            variant = "_ignore_errors"
        elif kind == "runner_on_ok":
            variant = "_changed" if ed.get("task_args") or res.get("changed") else ""
        elif kind == "runner_item_on_ok" and res.get("failed"):
            variant = "_failed_item"
        key = f"{run}:{kind}{variant}"
        if key in INDEX:
            continue
        dest = SAMPLES / f"{run}_{kind}{variant}.json"
        shutil.copyfile(path, dest)
        INDEX[key] = {
            "source": str(path.relative_to(HERE)),
            "sample": str(dest.relative_to(HERE)),
            "top_level_keys": sorted(data.keys()),
            "payload_keys": sorted(ed.keys()) if ed else None,
        }


def main() -> int:
    if SAMPLES.exists():
        shutil.rmtree(SAMPLES)
    SAMPLES.mkdir()
    for run, job_events in RUN_DIRS.items():
        harvest(run, job_events)
    (HERE / "samples_index.json").write_text(
        json.dumps(INDEX, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(INDEX)} samples -> samples/  (index: samples_index.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
