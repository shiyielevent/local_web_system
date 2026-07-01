from __future__ import annotations

import argparse
import json
import math
import os
import sys

try:
    import psutil
except Exception as exc:
    raise SystemExit(f"psutil is required: {exc}")


def get_float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(value)
    except Exception:
        return default


def detect_resources() -> dict:
    cpu = max(1, int(os.cpu_count() or 1))
    memory_gb = float(psutil.virtual_memory().total) / (1024 ** 3)

    # 预留给系统和前后端，不要全部拿去跑 EXE
    reserve_gb = max(4.0, memory_gb * 0.20)

    # 每个 EXE 子任务默认按 3GB 估算
    per_worker_gb = get_float_env("LOCAL_WEB_MEMORY_PER_WORKER_GB", 3.0)

    available_for_workers = max(0.0, memory_gb - reserve_gb)

    by_memory = max(1, int(available_for_workers // per_worker_gb))
    by_cpu = max(1, int(cpu * 0.75))

    max_slots = max(1, min(by_cpu, by_memory))

    # 原来是 int(max_slots * 0.5)，3 个上限会变成 1，太保守
    # 这里改成 ceil，3 个上限时建议从 2 个开始
    suggested = max(1, min(max_slots, math.ceil(max_slots * 0.5)))

    total_threads = max(1, int(cpu * 0.75))

    max_threads_per_child = max(
        1,
        min(4, max(1, total_threads // max(1, suggested))),
    )

    return {
        "cpu_count": cpu,
        "memory_gb": round(memory_gb, 1),
        "reserved_memory_gb": round(reserve_gb, 1),
        "memory_per_worker_gb": per_worker_gb,
        "suggested_process_slots": suggested,
        "max_process_slots": max_slots,
        "total_compute_threads": total_threads,
        "max_threads_per_child": max_threads_per_child,
    }


def print_bat_env(resources: dict) -> None:
    print(f"set LOCAL_WEB_DETECTED_CPU_COUNT={resources['cpu_count']}")
    print(f"set LOCAL_WEB_DETECTED_MEMORY_GB={resources['memory_gb']}")
    print(f"set LOCAL_WEB_SUGGESTED_PROCESS_SLOTS={resources['suggested_process_slots']}")
    print(f"set LOCAL_WEB_MAX_PROCESS_SLOTS={resources['max_process_slots']}")
    print(f"set LOCAL_WEB_TOTAL_COMPUTE_THREADS={resources['total_compute_threads']}")
    print(f"set LOCAL_WEB_MAX_THREADS_PER_CHILD={resources['max_threads_per_child']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON for PowerShell bootstrap scripts.",
    )
    args = parser.parse_args()

    resources = detect_resources()

    if args.json:
        print(json.dumps(resources, ensure_ascii=False, indent=2))
    else:
        print_bat_env(resources)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())