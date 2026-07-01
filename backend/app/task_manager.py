from __future__ import annotations
import json
import time
import os
import shutil
import subprocess
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .dask_job_runner import execute_subprocess_job

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

TERMINAL_STATUSES = {"success", "failed", "cancelled"}

class TaskManager:
    def __init__(self, tasks_file: str | Path):
        self.tasks_file = Path(tasks_file)
        self.tasks_file.parent.mkdir(parents=True, exist_ok=True)
        self.base_dir = self.tasks_file.parent.parent
        self.runtime_dir = self.base_dir / "runtime"
        self.parallel_chunks_dir = self.runtime_dir / "parallel_chunks"
        self.parallel_chunks_dir.mkdir(parents=True, exist_ok=True)

        self.lock = threading.RLock()
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.processes: Dict[str, subprocess.Popen] = {}
        self.cancel_flags: set[str] = set()

        # 运行调度器：根据本机 CPU 核数控制同时运行的模块进程数。
        # 遥感反演通常会加载大模型/大数组，CPU 核数不能直接等于安全并发。
        # 这版采用保守默认值：
        # - 建议值最高 2；16 核/24 核机器也默认建议 2。
        # - 上限值最高 4；用户可以选更高，但后端仍会按 CPU/内存/磁盘压力自动降级或排队。
        # 如需手动覆盖，可设置环境变量：
        # LOCAL_WEB_SUGGESTED_PROCESS_SLOTS / LOCAL_WEB_MAX_PROCESS_SLOTS。
        self.cpu_count = max(1, int(os.cpu_count() or 1))
        # 放宽默认值：建议数更接近实际可用进程池。
        # 16 核/24 核默认建议 4，上限 8；用户选择后不再因为固定模型大小直接砍成 1。
        default_suggested_slots = max(1, min(4, (self.cpu_count + 3) // 4))
        default_max_slots = max(default_suggested_slots, min(8, max(4, (self.cpu_count + 2) // 3)))

        try:
            env_suggested_slots = int(os.environ.get("LOCAL_WEB_SUGGESTED_PROCESS_SLOTS", "") or default_suggested_slots)
        except Exception:
            env_suggested_slots = default_suggested_slots

        try:
            env_max_slots = int(os.environ.get("LOCAL_WEB_MAX_PROCESS_SLOTS", "") or default_max_slots)
        except Exception:
            env_max_slots = default_max_slots

        self.max_process_slots = max(1, min(self.cpu_count, env_max_slots))
        self.suggested_process_slots = max(1, min(self.max_process_slots, env_suggested_slots))
        # 顶层排队只做“临界保护”。一般负载不阻止父任务启动，避免一直 queued。
        self.cpu_busy_threshold = float(os.environ.get("LOCAL_WEB_CPU_QUEUE_THRESHOLD", "99"))
        self.scheduler_queue: list[Dict[str, Any]] = []
        self.active_slots: Dict[str, int] = {}
        self.drain_lock = threading.Lock()
        # 运行中保护：批处理/并行任务启动子进程前会检查 CPU、内存和磁盘压力，压力过高时暂停启动新子任务。
        # 子进程启动保护：逐个启动子任务；达到阈值时暂停启动新的子任务。
        self.child_launch_cpu_threshold = float(os.environ.get("LOCAL_WEB_CHILD_START_CPU_THRESHOLD", "96"))
        self.child_launch_memory_threshold = float(os.environ.get("LOCAL_WEB_CHILD_START_MEMORY_THRESHOLD", "99"))
        self.child_launch_min_memory_gb = float(os.environ.get("LOCAL_WEB_CHILD_START_MIN_MEMORY_GB", "0.3"))
        self.child_launch_disk_threshold = float(os.environ.get("LOCAL_WEB_CHILD_START_DISK_THRESHOLD", "99.5"))
        self.child_launch_min_disk_free_gb = float(os.environ.get("LOCAL_WEB_CHILD_START_MIN_DISK_FREE_GB", "0.5"))
        self.child_launch_wait_seconds = float(os.environ.get("LOCAL_WEB_CHILD_START_WAIT_SECONDS", "2"))
        self.child_start_stagger_seconds = float(os.environ.get("LOCAL_WEB_CHILD_START_STAGGER_SECONDS", "0.5"))
        self.adaptive_child_start_enabled = str(os.environ.get("LOCAL_WEB_ADAPTIVE_CHILD_START", "1")).strip().lower() not in {"0", "false", "no", "off"}
        self.adaptive_child_start_min_interval = float(os.environ.get("LOCAL_WEB_ADAPTIVE_CHILD_START_MIN_SECONDS", "5"))
        self.adaptive_child_start_max_interval = float(os.environ.get("LOCAL_WEB_ADAPTIVE_CHILD_START_MAX_SECONDS", "60"))
        self.adaptive_child_start_sample_seconds = float(os.environ.get("LOCAL_WEB_ADAPTIVE_CHILD_START_SAMPLE_SECONDS", "1.5"))
        self.adaptive_child_start_decline_threshold = float(os.environ.get("LOCAL_WEB_ADAPTIVE_CHILD_START_CPU_DECLINE", "10"))
        self.adaptive_child_start_stable_samples = max(1, int(os.environ.get("LOCAL_WEB_ADAPTIVE_CHILD_START_STABLE_SAMPLES", "3")))
        self.adaptive_child_start_max_probe_seconds = float(os.environ.get("LOCAL_WEB_ADAPTIVE_CHILD_START_MAX_PROBE_SECONDS", "90"))
        self.adaptive_child_start_min_peak_cpu = float(os.environ.get("LOCAL_WEB_ADAPTIVE_CHILD_START_MIN_PEAK_CPU", "60"))
        self.adaptive_child_start_memory_threshold = float(os.environ.get("LOCAL_WEB_ADAPTIVE_CHILD_START_MEMORY_THRESHOLD", "90"))
        self.adaptive_child_start_min_memory_gb = float(os.environ.get("LOCAL_WEB_ADAPTIVE_CHILD_START_MIN_MEMORY_GB", "1.0"))
        self.learned_child_start_intervals: Dict[str, float] = {}
        self.child_start_gate_locks: Dict[str, threading.Lock] = {}
        self._last_pressure_log_at: Dict[str, float] = {}

        # 可选 Dask 分布式执行后端。未启用时完全保持原本机 subprocess 行为。
        self.cluster_manager = None
        self.dask_futures: Dict[str, Any] = {}
        self.dask_cancel_files: Dict[str, str] = {}
        # 防止 Worker 结果回传被防火墙阻断时 future.result() 无限等待。
        self.dask_result_timeout_seconds = max(
            10.0,
            float(os.environ.get("LOCAL_WEB_DASK_RESULT_TIMEOUT_SECONDS", "45")),
        )

        # 可选 HTCondor 执行后端。现在先和 Dask 并存，测试稳定后再删除旧分布式按钮。
        self.htcondor_manager = None
        self.htcondor_job_timeout_seconds = max(
            60,
            int(os.environ.get("LOCAL_WEB_HTCONDOR_JOB_TIMEOUT_SECONDS", "604800")),
        )

        # 性能优化：日志高频输出时，不再每一行都把完整 tasks.json 写回磁盘。
        # 前端读取任务时仍然直接读内存中的 logs；这里只是把持久化写盘做成短时间合并。
        self.task_save_debounce_seconds = float(os.environ.get("LOCAL_WEB_TASK_SAVE_DEBOUNCE_SECONDS", "0.8"))
        self.max_logs_per_task = max(200, int(os.environ.get("LOCAL_WEB_MAX_LOG_LINES_PER_TASK", "2000")))
        self._save_dirty = False
        self._save_timer: threading.Timer | None = None

        self._load_tasks()
        self._mark_interrupted_tasks()
        self._scheduler_heartbeat_thread = threading.Thread(
            target=self._scheduler_heartbeat,
            daemon=True,
        )
        self._scheduler_heartbeat_thread.start()
    def set_cluster_manager(self, cluster_manager):
        """注入 DaskClusterManager，避免 TaskManager 直接依赖 FastAPI 层。"""
        self.cluster_manager = cluster_manager

    def set_htcondor_manager(self, htcondor_manager):
        """注入 HTCondor 管理器。"""
        self.htcondor_manager = htcondor_manager

    def _htcondor_execution_requested(self) -> bool:
        manager = self.htcondor_manager
        if manager is None:
            return False
        try:
            requested = getattr(manager, "distributed_execution_requested", None)
            if callable(requested):
                return bool(requested())
            state = getattr(manager, "state", {}) or {}
            return str(state.get("execution_mode") or "") == "htcondor"
        except Exception:
            return False

    def _htcondor_execution_enabled(self) -> bool:
        manager = self.htcondor_manager
        if manager is None:
            return False
        try:
            return bool(manager.distributed_execution_enabled())
        except Exception:
            return False

    def _fail_when_htcondor_unavailable(self, task_id: str, task_kind: str = "任务"):
        message = (
            "已选择 HTCondor 执行，但当前 HTCondor 没有通过安装、自检或安全检查。"
            "系统不会自动退回本机运行，请先到 HTCondor 页面查看状态。"
        )
        self.append_log(task_id, f"[HTCONDOR-ERROR] {message}")
        self.update_task(
            task_id,
            status="failed",
            return_code=-1,
            ended_at=now_iso(),
            execution_backend="htcondor",
            queue_reason=message,
        )

    def _distributed_execution_requested(self) -> bool:
        manager = self.cluster_manager
        if manager is None:
            return False
        try:
            requested = getattr(manager, "distributed_execution_requested", None)
            if callable(requested):
                return bool(requested())
            state = getattr(manager, "state", {}) or {}
            return (
                str(state.get("role") or "") == "head"
                and str(state.get("execution_mode") or "") == "distributed"
            )
        except Exception:
            return False

    def _distributed_execution_enabled(self) -> bool:
        manager = self.cluster_manager
        if manager is None:
            return False
        try:
            return bool(manager.distributed_execution_enabled())
        except Exception:
            return False

    def _distributed_parent_slots(self, local_slots: int) -> int:
        # 用户已选择分布式时，父任务只占一个本机协调槽。
        # 即使 Scheduler 暂时不可用，也不应退回并占用多个本机执行槽。
        return 1 if (self._distributed_execution_requested() or self._htcondor_execution_requested()) else max(1, int(local_slots or 1))

    def _fail_when_dask_unavailable(self, task_id: str, task_kind: str = "任务"):
        """用户选择分布式但集群不可用时直接失败，禁止静默退回本机执行。"""
        message = (
            "已选择分布式执行，但当前 Dask Scheduler 或 Worker 不可用。"
            "系统不会自动退回本机运行，请检查主节点、Worker、共享目录和执行模式。"
        )
        self.append_log(task_id, f"[DASK-ERROR] {message}")
        self.update_task(
            task_id,
            status="failed",
            return_code=-1,
            ended_at=now_iso(),
            execution_backend="dask",
            queue_reason=message,
        )

    def _prepare_dask_payload(self, spec: Dict[str, Any], job_id: str) -> Dict[str, Any]:
        command = [str(x) for x in (spec.get("command") or [])]
        config_text = None
        config_arg_index = None

        # 平台生成的 config.json 很小，可随 Dask 任务发送并在 Worker 本地重建。
        # 大型 NC/HDF/TIF 不在这里传输，仍通过共享路径读取。
        for index in range(len(command) - 1, -1, -1):
            arg = command[index]
            if not str(arg).lower().endswith(".json"):
                continue
            try:
                path = Path(arg)
                if path.is_file() and path.stat().st_size <= 10 * 1024 * 1024:
                    config_text = path.read_text(encoding="utf-8")
                    config_arg_index = index
                    break
            except Exception:
                continue

        cancel_file = ""
        manager = self.cluster_manager
        if manager is not None:
            try:
                shared_root = str(manager.get_shared_runtime_root() or "").strip()
                if shared_root:
                    cancel_dir = Path(shared_root) / "cancel"
                    cancel_dir.mkdir(parents=True, exist_ok=True)
                    cancel_path = cancel_dir / f"{job_id}.cancel"
                    if cancel_path.exists():
                        cancel_path.unlink()
                    cancel_file = str(cancel_path)
                    with self.lock:
                        self.dask_cancel_files[job_id] = cancel_file
            except Exception:
                cancel_file = ""

        return {
            "job_id": job_id,
            "command": command,
            "working_dir": spec.get("working_dir"),
            "env": spec.get("env") or {},
            "config_text": config_text,
            "config_arg_index": config_arg_index,
            "cancel_file": cancel_file,
            "max_output_chars": 2_000_000,
        }

    def _append_dask_output(self, task_id: str, prefix: str, text: str, max_lines: int = 500):
        if not text:
            return
        lines = str(text).splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
            self.append_log(task_id, f"[{prefix}] 输出过长，仅保留最后 {max_lines} 行")
        for line in lines:
            self.append_log(task_id, f"[{prefix}] {line}")

    def _apply_dask_result(self, task_id: str, result: Dict[str, Any]) -> str:
        result = result or {}
        self._append_dask_output(task_id, "STDOUT", str(result.get("stdout") or ""))
        self._append_dask_output(task_id, "STDERR", str(result.get("stderr") or ""))

        return_code = int(result.get("return_code", -1))
        if bool(result.get("cancelled")) or return_code == -2:
            status = "cancelled"
        else:
            status = "success" if return_code == 0 else "failed"
        remote_name = str(result.get("hostname") or result.get("ip") or "unknown")
        self.append_log(
            task_id,
            f"[DASK] 远程节点={remote_name}，PID={result.get('pid') or '-'}，"
            f"return_code={return_code}，耗时={result.get('duration_seconds') or '-'}s",
        )
        self.update_task(
            task_id,
            status=status,
            return_code=return_code,
            pid=result.get("pid"),
            started_at=result.get("started_at") or now_iso(),
            ended_at=result.get("ended_at") or now_iso(),
            remote_node=remote_name,
            remote_ip=result.get("ip") or "",
            execution_backend="dask",
        )
        cancel_file = self.dask_cancel_files.pop(task_id, "")
        if cancel_file:
            try:
                Path(cancel_file).unlink(missing_ok=True)
            except Exception:
                pass
        return status

    def _signal_dask_cancel(self, task_id: str):
        cancel_file = str(self.dask_cancel_files.get(task_id) or "").strip()
        if not cancel_file:
            return
        try:
            path = Path(cancel_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("cancel", encoding="utf-8")
        except Exception:
            pass

    def _run_dask_single_task(
        self,
        task_id: str,
        command: List[str],
        working_dir: str | None,
        env: Dict[str, str] | None,
    ):
        manager = self.cluster_manager
        if manager is None:
            self._run_process_task(task_id, command, working_dir, env)
            return

        self.update_task(
            task_id,
            status="running",
            started_at=now_iso(),
            execution_backend="dask",
        )
        self.append_log(task_id, "[DASK] 单任务已提交到 Dask 集群")
        spec = {
            "command": command,
            "working_dir": working_dir,
            "env": env or {},
        }

        try:
            client = manager.get_client()
            info = client.scheduler_info(n_workers=-1)
            if not (info.get("workers") or {}):
                raise RuntimeError("Dask 集群没有可用 Worker")

            payload = self._prepare_dask_payload(spec, task_id)
            future = client.submit(
                execute_subprocess_job,
                payload,
                pure=False,
                key=f"local-web-{task_id}-{uuid.uuid4().hex[:8]}",
            )
            with self.lock:
                self.dask_futures[task_id] = future

            while not future.done():
                if task_id in self.cancel_flags:
                    self._signal_dask_cancel(task_id)
                    # 先给远程 subprocess 时间读取共享取消标记并结束进程树，
                    # 再取消 Future，避免取消文件被 finally 过早删除。
                    deadline = time.time() + 5.0
                    while time.time() < deadline and not future.done():
                        time.sleep(0.2)
                    if not future.done():
                        try:
                            future.cancel()
                        except Exception:
                            pass
                    self.update_task(
                        task_id,
                        status="cancelled",
                        ended_at=now_iso(),
                        return_code=-1,
                    )
                    return
                time.sleep(0.5)

            try:
                result = future.result(timeout=self.dask_result_timeout_seconds)
            except Exception as exc:
                raise RuntimeError(
                    "Dask 任务可能已在 Worker 完成，但主节点无法取回结果。"
                    "请检查所有节点的 Windows 防火墙是否开放 Worker 端口 9000-9099 "
                    "和 Nanny 端口 9100-9199。"
                    f" 原始错误：{type(exc).__name__}: {exc}"
                ) from exc
            self._apply_dask_result(task_id, result)

        except Exception as exc:
            self.append_log(task_id, f"[DASK-ERROR] {type(exc).__name__}: {exc}")
            self.append_log(task_id, traceback.format_exc())
            self.update_task(
                task_id,
                status="failed",
                return_code=-1,
                ended_at=now_iso(),
                execution_backend="dask",
            )
        finally:
            with self.lock:
                self.dask_futures.pop(task_id, None)
            cancel_file = self.dask_cancel_files.pop(task_id, "")
            if cancel_file:
                try:
                    Path(cancel_file).unlink(missing_ok=True)
                except Exception:
                    pass
            self.cancel_flags.discard(task_id)

    def _run_dask_job_group(
        self,
        parent_id: str,
        entries: List[Dict[str, Any]],
        max_workers: int,
        group_name: str,
    ):
        total = len(entries)
        max_workers = max(1, min(int(max_workers or 1), max(1, total)))
        manager = self.cluster_manager
        if manager is None:
            raise RuntimeError("DaskClusterManager 未初始化")

        self.update_task(
            parent_id,
            status="running",
            started_at=now_iso(),
            parallel_total=total,
            parallel_done=0,
            parallel_failed=0,
            max_workers=max_workers,
            execution_backend="dask",
        )
        self.append_log(
            parent_id,
            f"[DASK] {group_name}启动：总任务数={total}，最多同时提交={max_workers}",
        )
        self.append_log(
            parent_id,
            "[DASK] 大型输入数据不会通过调度器传输；所有节点必须能访问 config.json 中的输入/输出路径。",
        )

        client = manager.get_client()
        scheduler_info = client.scheduler_info(n_workers=-1)
        worker_count = len(scheduler_info.get("workers") or {})
        if worker_count <= 0:
            raise RuntimeError("Dask 集群没有可用 Worker")
        self.append_log(parent_id, f"[DASK] 当前可用 Worker={worker_count}")

        next_index = 0
        done_count = 0
        failures = 0
        running: Dict[Any, Dict[str, Any]] = {}

        try:
            while (next_index < total or running) and parent_id not in self.cancel_flags:
                while (
                    next_index < total
                    and len(running) < max_workers
                    and parent_id not in self.cancel_flags
                ):
                    entry = entries[next_index]
                    spec = entry["spec"]
                    child_id = entry.get("child_id")
                    label = str(spec.get("label") or f"子任务 {next_index + 1}")

                    if not child_id:
                        parent_task = self.get_task(parent_id) or {}
                        child = self.create_task(
                            module_id=spec.get("module_id", ""),
                            module_name=spec.get("module_name", label),
                            command=spec.get("command") or [],
                            inputs=spec.get("inputs") or {},
                            kind="module",
                            extra={
                                "parent_id": parent_id,
                                "job_index": next_index + 1,
                                "owner_username": str(parent_task.get("owner_username") or ""),
                                "execution_backend": "dask",
                            },
                        )
                        child_id = child["id"]
                        with self.lock:
                            parent = self.tasks.get(parent_id)
                            if parent:
                                parent.setdefault("children", []).append(child_id)
                    else:
                        self.update_task(
                            child_id,
                            status="running",
                            started_at=now_iso(),
                            execution_backend="dask",
                        )

                    payload = self._prepare_dask_payload(spec, child_id)
                    future = client.submit(
                        execute_subprocess_job,
                        payload,
                        pure=False,
                        key=f"local-web-{child_id}-{uuid.uuid4().hex[:8]}",
                    )
                    with self.lock:
                        self.dask_futures[child_id] = future

                    running[future] = {
                        "child_id": child_id,
                        "label": label,
                        "index": next_index,
                    }
                    self.append_log(
                        parent_id,
                        f"[DASK] 已提交 {next_index + 1}/{total}: {label}；"
                        f"当前在途 {len(running)}/{max_workers}",
                    )
                    next_index += 1

                if not running:
                    break

                completed = [future for future in running if future.done()]
                if not completed:
                    time.sleep(0.5)
                    continue

                for future in completed:
                    meta = running.pop(future)
                    child_id = meta["child_id"]
                    label = meta["label"]
                    try:
                        try:
                            result = future.result(
                                timeout=self.dask_result_timeout_seconds
                            )
                        except Exception as gather_exc:
                            raise RuntimeError(
                                "Worker 已报告任务结果，但主节点无法取回。"
                                "请开放所有节点的 Worker 端口 9000-9099 和 "
                                "Nanny 端口 9100-9199。"
                                f" 原始错误：{type(gather_exc).__name__}: {gather_exc}"
                            ) from gather_exc
                        status = self._apply_dask_result(child_id, result)
                    except Exception as exc:
                        status = "failed"
                        self.append_log(child_id, f"[DASK-ERROR] {type(exc).__name__}: {exc}")
                        self.append_log(child_id, traceback.format_exc())
                        self.update_task(
                            child_id,
                            status="failed",
                            return_code=-1,
                            ended_at=now_iso(),
                            execution_backend="dask",
                        )
                    finally:
                        with self.lock:
                            self.dask_futures.pop(child_id, None)
                        cancel_file = self.dask_cancel_files.pop(child_id, "")
                        if cancel_file:
                            try:
                                Path(cancel_file).unlink(missing_ok=True)
                            except Exception:
                                pass

                    if status != "success":
                        failures += 1
                        self._append_child_failure_to_parent(parent_id, child_id, label)

                    done_count += 1
                    self.update_task(
                        parent_id,
                        parallel_done=done_count,
                        parallel_failed=failures,
                    )
                    self.append_log(
                        parent_id,
                        f"[DASK] 完成 {done_count}/{total}: {label}，状态={status}",
                    )

            if parent_id in self.cancel_flags:
                for future, meta in list(running.items()):
                    child_id = meta["child_id"]
                    self._signal_dask_cancel(child_id)
                    try:
                        future.cancel()
                    except Exception:
                        pass
                    self.update_task(child_id, status="cancelled", ended_at=now_iso(), return_code=-1)
                    with self.lock:
                        self.dask_futures.pop(child_id, None)

            parent = self.get_task(parent_id) or {}
            children = parent.get("children") or []
            child_statuses = [(self.get_task(cid) or {}).get("status") for cid in children]

            if parent_id in self.cancel_flags or any(s == "cancelled" for s in child_statuses):
                final_status = "cancelled"
                return_code = -1
            elif failures > 0 or done_count < total or any(s != "success" for s in child_statuses):
                final_status = "failed"
                return_code = 1
            else:
                final_status = "success"
                return_code = 0

            self.update_task(
                parent_id,
                status=final_status,
                return_code=return_code,
                ended_at=now_iso(),
                parallel_done=done_count,
                parallel_failed=sum(1 for s in child_statuses if s != "success"),
                execution_backend="dask",
            )
            self.append_log(parent_id, f"[DASK] {group_name}结束，状态={final_status}")
            self._cleanup_runtime_roots_for_task(
                parent_id,
                reason=f"Dask {group_name}结束，状态={final_status}",
            )

        finally:
            self.cancel_flags.discard(parent_id)

    def _append_htcondor_output(self, task_id: str, prefix: str, text: str, max_lines: int = 500):
        if not text:
            return
        lines = str(text).splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
            self.append_log(task_id, f"[{prefix}] 输出过长，仅保留最后 {max_lines} 行")
        for line in lines:
            self.append_log(task_id, f"[{prefix}] {line}")

    def _apply_htcondor_result(self, task_id: str, result: Dict[str, Any]) -> str:
        result = result or {}

        # 如果 run_job 已经边运行边把 stdout/stderr 写入任务日志，
        # 这里就不再重复追加完整日志。
        if not result.get("live_output_sent"):
            self._append_htcondor_output(task_id, "STDOUT", str(result.get("stdout") or ""))
            self._append_htcondor_output(task_id, "STDERR", str(result.get("stderr") or ""))
        self._append_htcondor_output(task_id, "CONDOR", str(result.get("wait_output") or ""), max_lines=80)

        return_code = int(result.get("return_code", -1))
        if bool(result.get("cancelled")) or return_code == -2:
            status = "cancelled"
        else:
            status = "success" if return_code == 0 else "failed"

        remote_name = str(result.get("hostname") or "unknown")
        cluster_id = str(result.get("cluster_id") or "")
        self.append_log(
            task_id,
            f"[HTCONDOR] 执行节点={remote_name}，ClusterId={cluster_id}，return_code={return_code}",
        )
        self.update_task(
            task_id,
            status=status,
            return_code=return_code,
            started_at=result.get("started_at") or now_iso(),
            ended_at=result.get("ended_at") or now_iso(),
            remote_node=remote_name,
            htcondor_cluster_id=cluster_id,
            htcondor_job_dir=result.get("job_dir") or "",
            execution_backend="htcondor",
        )
        return status

    def _run_htcondor_single_task(
        self,
        task_id: str,
        command: List[str],
        working_dir: str | None,
        env: Dict[str, str] | None,
    ):
        manager = self.htcondor_manager
        if manager is None:
            self._run_process_task(task_id, command, working_dir, env)
            return

        self.update_task(
            task_id,
            status="running",
            started_at=now_iso(),
            execution_backend="htcondor",
        )
        self.append_log(task_id, "[HTCONDOR] 单任务已提交给 HTCondor")
        self.append_log(task_id, "[HTCONDOR] 当前版本要求执行节点具备相同的软件安装路径；大型输入输出仍按 config.json 中的路径读取和写入。")

        def should_cancel():
            task = self.get_task(task_id) or {}
            return task_id in self.cancel_flags or task.get("status") == "cancelled"

        def on_update(info):
            info = info or {}
            kind = str(info.get("type") or "")

            if kind == "submitted":
                cluster_id = str(info.get("cluster_id") or "")
                job_dir = str(info.get("job_dir") or "")
                self.update_task(
                    task_id,
                    htcondor_cluster_id=cluster_id,
                    htcondor_job_dir=job_dir,
                    execution_backend="htcondor",
                )
                self.append_log(task_id, f"[HTCONDOR] ClusterId={cluster_id}，已进入 HTCondor 队列")
                return

            text = str(info.get("text") or "")
            if not text:
                return

            if kind == "stdout":
                self._append_htcondor_output(task_id, "STDOUT", text)
            elif kind == "stderr":
                self._append_htcondor_output(task_id, "STDERR", text)
            elif kind == "event":
                # event.log 很长，只记录关键事件，避免日志刷太多。
                useful = []
                for line in text.splitlines():
                    raw = str(line or "").strip()
                    if not raw:
                        continue
                    low = raw.lower()
                    if (
                        "submitted" in low
                        or "executing" in low
                        or "terminated" in low
                        or "aborted" in low
                        or "held" in low
                        or "removed" in low
                        or "任务已请求取消" in raw
                    ):
                        useful.append(raw)
                if useful:
                    self._append_htcondor_output(task_id, "CONDOR", "\n".join(useful), max_lines=80)

        try:
            result = manager.run_job(
                job_id=task_id,
                command=command,
                working_dir=working_dir,
                env=env or {},
                timeout_seconds=self.htcondor_job_timeout_seconds,
                on_update=on_update,
                should_cancel=should_cancel,
            )
            self._apply_htcondor_result(task_id, result)
        except Exception as exc:
            self.append_log(task_id, f"[HTCONDOR-ERROR] {type(exc).__name__}: {exc}")
            self.append_log(task_id, traceback.format_exc())
            self.update_task(
                task_id,
                status="failed",
                return_code=-1,
                ended_at=now_iso(),
                execution_backend="htcondor",
            )
        finally:
            self.cancel_flags.discard(task_id)

    def _run_htcondor_job_group(
        self,
        parent_id: str,
        entries: List[Dict[str, Any]],
        max_workers: int,
        group_name: str,
    ):
        total = len(entries)
        max_workers = max(1, min(int(max_workers or 1), max(1, total)))
        manager = self.htcondor_manager
        if manager is None:
            raise RuntimeError("HTCondorClusterManager 未初始化")

        self.update_task(
            parent_id,
            status="running",
            started_at=now_iso(),
            parallel_total=total,
            parallel_done=0,
            parallel_failed=0,
            max_workers=max_workers,
            execution_backend="htcondor",
        )
        self.append_log(parent_id, f"[HTCONDOR] {group_name}启动：总任务数={total}，最多同时提交={max_workers}")
        self.append_log(parent_id, "[HTCONDOR] 这是第一版接入，要求各执行节点安装相同模块，输入输出路径按 config.json 保持不变。")

        done_count = 0
        failures = 0
        future_map: Dict[Any, Dict[str, Any]] = {}

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                for index, entry in enumerate(entries):
                    if parent_id in self.cancel_flags:
                        break
                    spec = entry["spec"]
                    child_id = entry.get("child_id")
                    label = str(spec.get("label") or f"子任务 {index + 1}")

                    if not child_id:
                        parent_task = self.get_task(parent_id) or {}
                        child = self.create_task(
                            module_id=spec.get("module_id", ""),
                            module_name=spec.get("module_name", label),
                            command=spec.get("command") or [],
                            inputs=spec.get("inputs") or {},
                            kind="module",
                            extra={
                                "parent_id": parent_id,
                                "job_index": index + 1,
                                "owner_username": str(parent_task.get("owner_username") or ""),
                                "execution_backend": "htcondor",
                            },
                        )
                        child_id = child["id"]
                        with self.lock:
                            parent = self.tasks.get(parent_id)
                            if parent:
                                parent.setdefault("children", []).append(child_id)
                    else:
                        self.update_task(
                            child_id,
                            status="running",
                            started_at=now_iso(),
                            execution_backend="htcondor",
                        )

                    future = pool.submit(
                        manager.run_job,
                        child_id,
                        spec.get("command") or [],
                        spec.get("working_dir"),
                        spec.get("env") or {},
                        self.htcondor_job_timeout_seconds,
                    )
                    future_map[future] = {
                        "child_id": child_id,
                        "label": label,
                    }
                    self.append_log(parent_id, f"[HTCONDOR] 已提交 {index + 1}/{total}: {label}")

                while future_map:
                    if parent_id in self.cancel_flags:
                        self.append_log(parent_id, "[HTCONDOR] 收到取消请求，等待已提交的任务结束。")
                        break

                    done, _ = wait(list(future_map.keys()), timeout=0.5, return_when=FIRST_COMPLETED)
                    if not done:
                        continue

                    for future in done:
                        meta = future_map.pop(future)
                        child_id = meta["child_id"]
                        label = meta["label"]
                        try:
                            result = future.result()
                            status = self._apply_htcondor_result(child_id, result)
                        except Exception as exc:
                            status = "failed"
                            self.append_log(child_id, f"[HTCONDOR-ERROR] {type(exc).__name__}: {exc}")
                            self.append_log(child_id, traceback.format_exc())
                            self.update_task(
                                child_id,
                                status="failed",
                                return_code=-1,
                                ended_at=now_iso(),
                                execution_backend="htcondor",
                            )

                        done_count += 1
                        if status != "success":
                            failures += 1
                            self._append_child_failure_to_parent(parent_id, child_id, label)

                        self.update_task(
                            parent_id,
                            parallel_done=done_count,
                            parallel_failed=failures,
                        )
                        self.append_log(parent_id, f"[HTCONDOR] 完成 {done_count}/{total}: {label}，状态={status}")

            if parent_id in self.cancel_flags:
                final_status = "cancelled"
            else:
                final_status = "success" if failures == 0 and done_count == total else "failed"

            self.update_task(
                parent_id,
                status=final_status,
                ended_at=now_iso(),
                parallel_done=done_count,
                parallel_failed=failures,
                execution_backend="htcondor",
            )
            self.append_log(parent_id, f"[HTCONDOR] {group_name}结束，状态={final_status}")
            self._cleanup_runtime_roots_for_task(parent_id, reason=f"HTCondor {group_name}结束，状态={final_status}")
        except Exception as exc:
            self.append_log(parent_id, f"[HTCONDOR-ERROR] {type(exc).__name__}: {exc}")
            self.append_log(parent_id, traceback.format_exc())
            self.update_task(
                parent_id,
                status="failed",
                return_code=-1,
                ended_at=now_iso(),
                execution_backend="htcondor",
            )
            self._cleanup_runtime_roots_for_task(parent_id, reason=f"HTCondor {group_name}异常结束")
        finally:
            self.cancel_flags.discard(parent_id)

    def _run_parallel_task_htcondor(self, parent_id: str, jobs: List[Dict[str, Any]], max_workers: int):
        entries = [{"spec": job} for job in jobs]
        return self._run_htcondor_job_group(parent_id, entries, max_workers, "并行任务")

    def _run_batch_group_htcondor(self, parent_id: str, child_job_map: Dict[str, Dict[str, Any]], max_parallel: int):
        entries = [
            {"child_id": child_id, "spec": job}
            for child_id, job in child_job_map.items()
        ]
        return self._run_htcondor_job_group(parent_id, entries, max_parallel, "批处理")

    def _mark_dask_parent_failed(self, parent_id: str, exc: Exception, group_name: str):
        self.append_log(parent_id, f"[DASK-ERROR] {type(exc).__name__}: {exc}")
        self.append_log(parent_id, traceback.format_exc())
        self.update_task(
            parent_id,
            status="failed",
            return_code=-1,
            ended_at=now_iso(),
            execution_backend="dask",
        )
        self._cleanup_runtime_roots_for_task(
            parent_id,
            reason=f"Dask {group_name}异常结束",
        )

    def _run_parallel_task_dask(
        self,
        parent_id: str,
        jobs: List[Dict[str, Any]],
        max_workers: int,
    ):
        entries = [{"child_id": None, "spec": job} for job in jobs]
        try:
            return self._run_dask_job_group(
                parent_id,
                entries,
                max_workers,
                group_name="并行任务",
            )
        except Exception as exc:
            self._mark_dask_parent_failed(parent_id, exc, "并行任务")

    def _run_batch_group_dask(
        self,
        parent_id: str,
        child_job_map: Dict[str, Dict[str, Any]],
        max_parallel: int,
    ):
        entries = [
            {"child_id": child_id, "spec": job}
            for child_id, job in child_job_map.items()
        ]
        try:
            return self._run_dask_job_group(
                parent_id,
                entries,
                max_parallel,
                group_name="批处理",
            )
        except Exception as exc:
            self._mark_dask_parent_failed(parent_id, exc, "批处理")

    def kick_scheduler(self):
        """
        外部主动唤醒调度器。
        前端轮询 /api/tasks、/api/tasks/{id}、/api/system/resources 时调用。
        """
        try:
            self._drain_scheduler_queue()
        except Exception as exc:
            try:
                with self.lock:
                    for item in self.scheduler_queue:
                        task_id = str(item.get("task_id") or "")
                        task = self.tasks.get(task_id)
                        if task and task.get("status") == "queued":
                            task["queue_reason"] = (
                                f"调度器唤醒失败: {type(exc).__name__}: {exc}"
                            )
                self._save_tasks()
            except Exception:
                pass
    def _normalize_cleanup_roots(self, roots: Any) -> list[Path]:
        """规范化并限制可清理的临时任务目录。"""
        if not roots:
            return []

        if isinstance(roots, (str, Path)):
            items = [roots]
        elif isinstance(roots, (list, tuple, set)):
            items = list(roots)
        else:
            return []

        allowed_roots: list[Path] = []
        try:
            allowed_roots.append(self.parallel_chunks_dir.resolve())
        except Exception:
            pass

        manager = self.cluster_manager
        if manager is not None:
            try:
                shared = str(manager.get_shared_runtime_root() or "").strip()
                if shared:
                    allowed_roots.append((Path(shared) / "parallel_chunks").resolve())
            except Exception:
                pass

        if not allowed_roots:
            return []

        result: list[Path] = []
        seen: set[str] = set()
        for item in items:
            try:
                p = Path(str(item)).resolve()
            except Exception:
                continue

            allowed = False
            for allowed_root in allowed_roots:
                try:
                    p.relative_to(allowed_root)
                    allowed = True
                    break
                except ValueError:
                    continue
            if not allowed:
                continue

            key = str(p).lower()
            if key not in seen:
                seen.add(key)
                result.append(p)

        return result


    def _cleanup_runtime_roots(self, task_id: str, roots: Any, reason: str = "任务结束"):
        """清理平台拆分产生的 runtime/parallel_chunks 临时输入目录。"""
        paths = self._normalize_cleanup_roots(roots)
        if not paths:
            return

        removed = 0
        for path in paths:
            try:
                if path.exists() and path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
            except Exception as exc:
                try:
                    self.append_log(task_id, f"[CLEANUP] 清理临时输入目录失败: {path}，原因: {type(exc).__name__}: {exc}")
                except Exception:
                    pass

        if removed:
            try:
                self.append_log(task_id, f"[CLEANUP] {reason}，已清理 {removed} 个平台拆分临时输入目录，避免占用磁盘空间。")
            except Exception:
                pass

    def _cleanup_runtime_roots_for_task(self, task_id: str, reason: str = "任务结束"):
        task = self.get_task(task_id) or {}
        roots = task.get("cleanup_roots") or (task.get("inputs") or {}).get("_parallel_cleanup_roots")
        self._cleanup_runtime_roots(task_id, roots, reason=reason)

    def _scheduler_heartbeat(self):
        """
        调度器心跳。
        防止某次 enqueue/drain 因异常或热重载时机导致 queued 任务没有被启动。
        """
        while True:
            time.sleep(1.0)
            try:
                with self.lock:
                    has_queued = any(
                        (self.tasks.get(str(item.get("task_id") or "")) or {}).get("status") == "queued"
                        for item in self.scheduler_queue
                    )

                if has_queued:
                    self._drain_scheduler_queue()
            except Exception:
                pass
    def _load_tasks(self):
        if not self.tasks_file.exists():
            self.tasks = {}
            return

        try:
            raw = json.loads(self.tasks_file.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self.tasks = {
                    item["id"]: item
                    for item in raw
                    if isinstance(item, dict) and item.get("id")
                }
            elif isinstance(raw, dict):
                self.tasks = raw
            else:
                self.tasks = {}
        except Exception:
            self.tasks = {}

    def _mark_interrupted_tasks(self):
        """服务重启后，内存里的进程和调度队列都不存在了。把旧的 running/queued 标记为 cancelled，并清理平台拆分临时目录。"""
        changed = False
        cleanup_map: dict[str, Any] = {}
        with self.lock:
            for task in self.tasks.values():
                if task.get("status") in {"queued", "running"}:
                    task["status"] = "cancelled"
                    task["ended_at"] = now_iso()
                    task.setdefault("logs", []).append("[SYSTEM] 服务已重启，历史未完成任务已自动取消")
                    roots = task.get("cleanup_roots") or (task.get("inputs") or {}).get("_parallel_cleanup_roots")
                    if roots:
                        cleanup_map[str(task.get("id") or "")] = roots
                    changed = True
        if changed:
            self._save_tasks()
            for task_id, roots in cleanup_map.items():
                self._cleanup_runtime_roots(task_id, roots, reason="服务重启后清理历史临时输入目录")

    def _system_cpu_percent(self) -> float | None:
        """读取本机 CPU 使用率。

        优先使用 psutil；没有 psutil 时，在 Windows 下用 wmic/typeperf 兜底，
        避免前端一直显示 “-”。
        """
        try:
            import psutil  # type: ignore
            return float(psutil.cpu_percent(interval=0.35))
        except Exception:
            pass

        if os.name == "nt":
            # 方式 1：wmic，很多 Windows 仍可用。
            try:
                result = subprocess.run(
                    ["wmic", "cpu", "get", "loadpercentage", "/value"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=3,
                    shell=False,
                )
                text = (result.stdout or "") + "\n" + (result.stderr or "")
                values = []
                for line in text.splitlines():
                    line = line.strip()
                    if line.lower().startswith("loadpercentage="):
                        values.append(float(line.split("=", 1)[1].strip()))
                if values:
                    return max(0.0, min(100.0, sum(values) / len(values)))
            except Exception:
                pass

            # 方式 2：typeperf。
            try:
                result = subprocess.run(
                    ["typeperf", r"\\Processor(_Total)\\% Processor Time", "-sc", "1"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=5,
                    shell=False,
                )
                import re
                nums = re.findall(r'"[^"]+","([0-9.]+)"', result.stdout or "")
                if nums:
                    return max(0.0, min(100.0, float(nums[-1])))
            except Exception:
                pass

        try:
            if hasattr(os, "getloadavg"):
                load1, _, _ = os.getloadavg()
                return max(0.0, min(100.0, float(load1) / max(1, self.cpu_count) * 100.0))
        except Exception:
            pass
        return None

    def _active_process_count(self) -> int:
        """当前平台真实启动、尚未退出的子进程数。"""
        count = 0
        with self.lock:
            processes = list(self.processes.values())
        for process in processes:
            try:
                if process and process.poll() is None:
                    count += 1
            except Exception:
                continue
        return count

    def _running_process_cpu_percent(self) -> float | None:
        """读取平台启动的模块进程 CPU 占用总和。

        psutil 的 cpu_percent(interval=0.0) 第一次常返回 0，所以这里用短采样；
        同时把子进程的子进程也统计进去，避免前端一直显示 0。
        """
        try:
            import psutil  # type: ignore
        except Exception:
            return None

        total = 0.0
        with self.lock:
            processes = list(self.processes.values())
        for process in processes:
            try:
                if process.poll() is None:
                    proc = psutil.Process(process.pid)
                    total += float(proc.cpu_percent(interval=0.03))
                    for child in proc.children(recursive=True):
                        try:
                            if child.is_running():
                                total += float(child.cpu_percent(interval=0.0))
                        except Exception:
                            continue
            except Exception:
                continue
        return round(total, 2)

    def _used_slots_locked(self) -> int:
        return sum(max(1, int(v or 1)) for v in self.active_slots.values())

    def _normalize_requested_slots(self, value: int | str | None) -> int:
        try:
            n = int(value or 1)
        except Exception:
            n = 1
        return max(1, min(n, self.max_process_slots))

    def _remove_from_scheduler_queue_locked(self, task_id: str):
        self.scheduler_queue = [item for item in self.scheduler_queue if item.get("task_id") != task_id]
        self._refresh_queue_positions_locked()

    def _refresh_queue_positions_locked(self):
        pos = 1
        used = self._used_slots_locked()
        for item in self.scheduler_queue:
            tid = str(item.get("task_id") or "")
            task = self.tasks.get(tid)
            if not task or task.get("status") != "queued":
                continue
            requested = self._normalize_requested_slots(item.get("requested_slots"))
            task["queue_position"] = pos
            if used == 0 and requested <= self.max_process_slots:
                task["queue_reason"] = (
                    f"调度器等待启动：当前占用 {used}/{self.max_process_slots}，"
                    f"本任务需要 {requested} 个进程槽。若长时间不启动，请检查后端是否使用 --reload 或调度器是否异常。"
                )
            else:
                task["queue_reason"] = (
                    f"等待本地 CPU 空闲：当前占用 {used}/{self.max_process_slots}，"
                    f"本任务需要 {requested} 个进程槽"
                )
            pos += 1


    def _virtual_memory_snapshot(self) -> Dict[str, Any]:
        try:
            import psutil  # type: ignore
            mem = psutil.virtual_memory()
            return {
                "percent": float(mem.percent),
                "available_gb": float(mem.available or 0) / (1024 ** 3),
            }
        except Exception:
            pass

        if os.name == "nt":
            # wmic: FreePhysicalMemory/TotalVisibleMemorySize 单位是 KB。
            try:
                result = subprocess.run(
                    ["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize", "/value"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=3,
                    shell=False,
                )
                vals = {}
                for line in (result.stdout or "").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        vals[k.strip().lower()] = float(v.strip() or 0)
                free_kb = vals.get("freephysicalmemory")
                total_kb = vals.get("totalvisiblememorysize")
                if free_kb and total_kb:
                    used_percent = max(0.0, min(100.0, (1.0 - free_kb / total_kb) * 100.0))
                    return {
                        "percent": used_percent,
                        "available_gb": free_kb / 1024.0 / 1024.0,
                    }
            except Exception:
                pass
        return {"percent": None, "available_gb": None}

    def _disk_usage_snapshot(self) -> Dict[str, Any]:
        try:
            path = self.tasks_file.parent.resolve()
        except Exception:
            path = Path(".")
        try:
            import psutil  # type: ignore
            usage = psutil.disk_usage(str(path))
            return {
                "percent": float(usage.percent),
                "free_gb": float(usage.free or 0) / (1024 ** 3),
            }
        except Exception:
            try:
                usage = shutil.disk_usage(str(path))
                total = float(usage.total or 1)
                return {
                    "percent": float(usage.used) / total * 100.0,
                    "free_gb": float(usage.free or 0) / (1024 ** 3),
                }
            except Exception:
                return {"percent": None, "free_gb": None}

    def _runtime_pressure_reason(self) -> str:
        """返回是否应该暂停启动新的子进程。

        轻量化策略：
        - 顶层任务不因为内存 80% 多就长期 queued；
        - 真正启动每一个子进程前，才检查 CPU/内存/磁盘和当前平台进程数；
        - 已启动的进程不强杀，只暂停后续启动，等负载下降再继续。
        """
        active_processes = self._active_process_count()
        if active_processes >= self.max_process_slots:
            return f"平台已启动 {active_processes}/{self.max_process_slots} 个模块进程，等待已有进程完成"

        if not self.adaptive_child_start_enabled:
            cpu = self._system_cpu_percent()
            if cpu is not None and cpu >= self.child_launch_cpu_threshold:
                return f"CPU 使用率 {cpu:.1f}% 已超过暂停启动阈值 {self.child_launch_cpu_threshold:.0f}%"

        mem = self._virtual_memory_snapshot()
        mem_percent = mem.get("percent")
        mem_available = mem.get("available_gb")
        if mem_percent is not None and mem_percent >= self.child_launch_memory_threshold:
            return f"内存使用率 {mem_percent:.1f}% 已超过暂停启动阈值 {self.child_launch_memory_threshold:.0f}%"
        if mem_available is not None and mem_available <= self.child_launch_min_memory_gb:
            return f"可用内存仅 {mem_available:.1f}GB，低于最低阈值 {self.child_launch_min_memory_gb:.1f}GB"

        disk = self._disk_usage_snapshot()
        disk_percent = disk.get("percent")
        disk_free = disk.get("free_gb")
        if disk_percent is not None and disk_percent >= self.child_launch_disk_threshold:
            return f"磁盘使用率 {disk_percent:.1f}% 已超过暂停启动阈值 {self.child_launch_disk_threshold:.0f}%"
        if disk_free is not None and disk_free <= self.child_launch_min_disk_free_gb:
            return f"磁盘剩余空间仅 {disk_free:.1f}GB，低于最低阈值 {self.child_launch_min_disk_free_gb:.1f}GB"

        return ""

    def _wait_until_safe_to_start_child(self, parent_id: str, label: str):
        """父并行任务运行期间，系统压力过高时不再启动新子进程，等压力下降再继续。

        需要连续两次采样都安全才放行，避免刚启动几个进程时 CPU 还没来得及升高，
        后续进程又被瞬间全部拉起导致电脑卡死。
        """
        safe_samples = 0
        while parent_id not in self.cancel_flags:
            reason = self._runtime_pressure_reason()
            if not reason:
                safe_samples += 1
                if safe_samples >= 2:
                    return
                time.sleep(max(1.0, min(self.child_launch_wait_seconds, 3.0)))
                continue

            safe_samples = 0
            now = time.time()
            last = self._last_pressure_log_at.get(parent_id, 0.0)
            if now - last >= 12:
                self._last_pressure_log_at[parent_id] = now
                self.append_log(
                    parent_id,
                    f"[SAFE] 暂停启动新子任务 {label}：{reason}。已启动的任务继续运行，等负载下降后自动继续，防止电脑卡死。",
                )
            time.sleep(max(1.0, self.child_launch_wait_seconds))

    def _adaptive_start_memory_safe(self) -> tuple[bool, str]:
        mem = self._virtual_memory_snapshot()
        mem_percent = mem.get("percent")
        mem_available = mem.get("available_gb")
        if mem_percent is not None and mem_percent >= self.adaptive_child_start_memory_threshold:
            return False, f"内存使用率 {mem_percent:.1f}% 已超过自适应启动阈值 {self.adaptive_child_start_memory_threshold:.0f}%"
        if mem_available is not None and mem_available <= self.adaptive_child_start_min_memory_gb:
            return False, f"可用内存 {mem_available:.1f}GB 低于自适应启动阈值 {self.adaptive_child_start_min_memory_gb:.1f}GB"
        return True, ""

    def _learn_child_start_interval_after_first_launch(self, parent_id: str, label: str) -> float:
        min_interval = max(0.0, self.adaptive_child_start_min_interval)
        max_interval = max(min_interval, self.adaptive_child_start_max_interval)
        sample_seconds = max(0.2, self.adaptive_child_start_sample_seconds)
        decline_threshold = max(0.0, self.adaptive_child_start_decline_threshold)
        required_samples = max(1, self.adaptive_child_start_stable_samples)
        max_probe_seconds = max(min_interval, self.adaptive_child_start_max_probe_seconds)

        start_time = time.time()
        peak_cpu = 0.0
        decline_count = 0
        last_cpu: float | None = None
        self.append_log(
            parent_id,
            f"[ADAPTIVE] 已启动首个子任务 {label}，开始监测 CPU 峰值回落，用于学习后续子任务启动间隔。",
        )

        while parent_id not in self.cancel_flags:
            elapsed = time.time() - start_time
            cpu = self._system_cpu_percent()
            memory_safe, memory_reason = self._adaptive_start_memory_safe()

            if cpu is not None:
                peak_cpu = max(peak_cpu, cpu)
                has_peak = peak_cpu >= self.adaptive_child_start_min_peak_cpu
                dropped_from_peak = has_peak and cpu <= peak_cpu - decline_threshold
                moving_down = last_cpu is not None and cpu <= last_cpu
                if dropped_from_peak and moving_down:
                    decline_count += 1
                else:
                    decline_count = 0
                last_cpu = cpu

                if decline_count >= required_samples and memory_safe:
                    learned = max(min_interval, min(elapsed, max_interval))
                    self.append_log(
                        parent_id,
                        f"[ADAPTIVE] 学到子任务启动间隔 {learned:.1f}s：CPU峰值 {peak_cpu:.1f}%，当前 {cpu:.1f}%，连续回落 {decline_count} 次。",
                    )
                    return learned

                if not has_peak and elapsed >= min_interval and memory_safe:
                    self.append_log(
                        parent_id,
                        f"[ADAPTIVE] 首个子任务未形成明显 CPU 峰值，使用最小启动间隔 {min_interval:.1f}s。",
                    )
                    return min_interval

            if elapsed >= max_probe_seconds:
                learned = max(min_interval, min(elapsed, max_interval))
                reason = f"，内存暂不安全：{memory_reason}" if memory_reason else ""
                self.append_log(
                    parent_id,
                    f"[ADAPTIVE] CPU 回落探测达到上限，使用保守启动间隔 {learned:.1f}s{reason}。",
                )
                return learned

            time.sleep(sample_seconds)

        return max_interval

    def _sleep_before_adaptive_child_launch(self, parent_id: str, label: str, interval: float, last_launch_at: float) -> bool:
        if interval <= 0 or last_launch_at <= 0:
            return True
        remaining = interval - (time.time() - last_launch_at)
        if remaining <= 0:
            return True

        self.append_log(parent_id, f"[ADAPTIVE] 启动 {label} 前等待 {remaining:.1f}s，按首个子任务学习到的间隔错峰启动。")
        end_at = time.time() + remaining
        while parent_id not in self.cancel_flags:
            left = end_at - time.time()
            if left <= 0:
                return True
            time.sleep(min(0.5, max(0.05, left)))
        return False

    def get_system_resource_info(self) -> Dict[str, Any]:
        with self.lock:
            running_workers = self._used_slots_locked()
            active_task_count = len(self.active_slots)
            queued_task_count = sum(
                1 for item in self.scheduler_queue
                if (self.tasks.get(str(item.get("task_id") or "")) or {}).get("status") == "queued"
            )
            active_tasks = []
            for task_id, slots in self.active_slots.items():
                task = self.tasks.get(task_id) or {}
                active_tasks.append({
                    "id": task_id,
                    "module_name": task.get("module_name") or "",
                    "requested_workers": slots,
                    "pid": task.get("pid"),
                    "status": task.get("status") or "running",
                })

        cpu_percent = self._system_cpu_percent()
        process_cpu_percent = self._running_process_cpu_percent()
        mem_snapshot = self._virtual_memory_snapshot()
        disk_snapshot = self._disk_usage_snapshot()
        active_processes = self._active_process_count()
        return {
            "cpu_count": self.cpu_count,
            "suggested_workers": self.suggested_process_slots,
            "max_workers": self.max_process_slots,
            "adaptive_child_start": self.adaptive_child_start_enabled,
            "learned_child_start_intervals": dict(self.learned_child_start_intervals),
            "running_workers": active_processes,
            "available_workers": max(0, self.max_process_slots - active_processes),
            "active_task_count": active_task_count,
            "queued_task_count": queued_task_count,
            "cpu_percent": cpu_percent,
            "running_process_cpu_percent": process_cpu_percent,
            "cpu_busy_threshold": self.cpu_busy_threshold,
            "memory_percent": mem_snapshot.get("percent"),
            "memory_available_gb": mem_snapshot.get("available_gb"),
            "disk_percent": disk_snapshot.get("percent"),
            "disk_free_gb": disk_snapshot.get("free_gb"),
            "active_tasks": active_tasks,
            "execution_backend": "htcondor" if self._htcondor_execution_enabled() else ("dask" if self._distributed_execution_enabled() else "local"),
        }

    def _can_start_queued_item_locked(self, item: Dict[str, Any]) -> tuple[bool, str]:
        requested = self._normalize_requested_slots(item.get("requested_slots"))
        used = self._used_slots_locked()
        if used + requested > self.max_process_slots:
            return False, f"进程数超过本机安全上限：当前 {used}/{self.max_process_slots}，本任务需要 {requested}"

        # 顶层任务只按槽位排队，不再因为内存/磁盘中等压力长期 queued。
        # CPU/内存/磁盘保护放到子进程逐个启动前执行。
        return True, ""

    def _enqueue_task_runner(
        self,
        task_id: str,
        runner,
        args: tuple,
        requested_slots: int | str | None = 1,
    ):
        requested_slots = self._normalize_requested_slots(requested_slots)
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task["status"] = "queued"
            task["requested_workers"] = requested_slots
            task["queued_at"] = now_iso()
            task["queue_position"] = len(self.scheduler_queue) + 1
            task["queue_reason"] = "等待调度"
            self.scheduler_queue.append({
                "task_id": task_id,
                "runner": runner,
                "args": args,
                "requested_slots": requested_slots,
            })
            self._refresh_queue_positions_locked()
        self._save_tasks()
        self._drain_scheduler_queue()

    def _run_scheduled_item(self, item: Dict[str, Any]):
        task_id = str(item.get("task_id") or "")
        try:
            runner = item.get("runner")
            args = item.get("args") or ()
            if runner:
                runner(*args)
        finally:
            with self.lock:
                self.active_slots.pop(task_id, None)
            self._save_tasks()
            self._drain_scheduler_queue()

    def _drain_scheduler_queue(self):
        if not self.drain_lock.acquire(blocking=False):
            return
        try:
            while True:
                start_item: Dict[str, Any] | None = None
                with self.lock:
                    # 清理已取消/已删除的队列项。
                    while self.scheduler_queue:
                        candidate = self.scheduler_queue[0]
                        task_id = str(candidate.get("task_id") or "")
                        task = self.tasks.get(task_id)
                        if task and task.get("status") == "queued":
                            break
                        self.scheduler_queue.pop(0)

                    if not self.scheduler_queue:
                        self._refresh_queue_positions_locked()
                        return

                    item = self.scheduler_queue[0]
                    task_id = str(item.get("task_id") or "")
                    task = self.tasks.get(task_id)
                    can_start, reason = self._can_start_queued_item_locked(item)
                    if not can_start:
                        if task:
                            task["queue_reason"] = reason
                        self._refresh_queue_positions_locked()
                        self._save_tasks()
                        return

                    start_item = self.scheduler_queue.pop(0)
                    requested_slots = self._normalize_requested_slots(start_item.get("requested_slots"))
                    self.active_slots[task_id] = requested_slots
                    if task:
                        task["queue_position"] = None
                        task["queue_reason"] = ""
                        task["scheduled_at"] = now_iso()
                        task.setdefault("logs", []).append(
                            f"[SYSTEM] 已获得 {requested_slots} 个 CPU 进程槽，开始运行"
                        )
                    self._refresh_queue_positions_locked()
                    self._save_tasks()

                threading.Thread(
                    target=self._run_scheduled_item,
                    args=(start_item,),
                    daemon=True,
                ).start()
        finally:
            self.drain_lock.release()

    def _save_tasks(self):
        """立即把任务快照写入 tasks.json。

        只在状态变化、任务结束等关键位置直接调用。
        高频日志写入改由 _schedule_save_tasks() 合并，避免每输出一行日志就重写完整 JSON。
        """
        with self.lock:
            data = list(self.tasks.values())
            self.tasks_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _flush_scheduled_save(self):
        with self.lock:
            if not self._save_dirty:
                self._save_timer = None
                return
            self._save_dirty = False
            self._save_timer = None
        self._save_tasks()

    def _schedule_save_tasks(self):
        """把多次日志写盘合并到一次，降低 runtime 期间磁盘 I/O。"""
        if self.task_save_debounce_seconds <= 0:
            self._save_tasks()
            return

        with self.lock:
            self._save_dirty = True
            timer = self._save_timer
            if timer is not None and timer.is_alive():
                return
            self._save_timer = threading.Timer(
                self.task_save_debounce_seconds,
                self._flush_scheduled_save,
            )
            self._save_timer.daemon = True
            self._save_timer.start()

    def _trim_task_logs_locked(self, task: Dict[str, Any]):
        logs = task.get("logs")
        if not isinstance(logs, list):
            return
        if len(logs) <= self.max_logs_per_task:
            return

        keep_tail = max(100, self.max_logs_per_task - 1)
        removed = len(logs) - keep_tail
        task["logs"] = [
            f"[LOG-TRIM] 日志过长，已省略前 {removed} 行；可通过调大 LOCAL_WEB_MAX_LOG_LINES_PER_TASK 保留更多日志。"
        ] + logs[-keep_tail:]

    def list_tasks(self, owner_username: str | None = None) -> List[Dict[str, Any]]:
        with self.lock:
            items = list(self.tasks.values())

        if owner_username:
            owner_username = str(owner_username)
            items = [
                item for item in items
                if str(item.get("owner_username") or "") == owner_username
            ]

        items.sort(key=lambda x: x.get("started_at") or x.get("created_at") or "", reverse=True)
        return items

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                return None
            return dict(task)

    def create_task(
        self,
        module_id: str,
        module_name: str,
        command: List[str],
        inputs: Dict[str, Any],
        kind: str = "module",
        extra: Dict[str, Any] | None = None,
        auto_save: bool = True,
    ) -> Dict[str, Any]:
        task_id = uuid.uuid4().hex[:12]
        task = {
            "id": task_id,
            "module_id": module_id,
            "module_name": module_name,
            "kind": kind,
            "status": "queued",
            "return_code": None,
            "pid": None,
            "command": command,
            "inputs": inputs,
            "logs": [],
            "created_at": now_iso(),
            "started_at": None,
            "ended_at": None,
            "children": [],
            "owner_username": "",
        }
        if extra:
            task.update(extra)

        with self.lock:
            self.tasks[task_id] = task

        if auto_save:
            self._save_tasks()
        return task

    def append_log(self, task_id: str, text: str):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task.setdefault("logs", []).append(str(text))
            self._trim_task_logs_locked(task)
        self._schedule_save_tasks()

    def _extract_error_lines_for_parent(self, child_task: Dict[str, Any], max_lines: int = 35) -> List[str]:
        """把子任务失败原因摘出来写回父任务日志。

        任务管理页现在只展示父任务，子任务被隐藏后，如果不把子任务 stderr/traceback
        汇总到父任务，用户只能看到“状态=failed”，无法定位算法报错。
        """
        logs = [str(x) for x in (child_task.get("logs") or [])]
        if not logs:
            return []

        important: List[str] = []
        capture_traceback = False
        for line in logs:
            text = str(line)
            low = text.lower()
            if (
                "[stderr]" in low
                or "traceback" in low
                or "error" in low
                or "exception" in low
                or "failed" in low
                or "错误" in text
                or "失败" in text
                or "nameerror" in low
                or "filenotfounderror" in low
                or "indexerror" in low
                or "keyerror" in low
                or "valueerror" in low
                or "runtimeerror" in low
            ):
                important.append(text)
                capture_traceback = "traceback" in low
            elif capture_traceback and (text.startswith("[STDERR]") or text.startswith(" ") or text.startswith("[PYTHON-EXCEPTION]")):
                important.append(text)

        if not important:
            important = logs[-max_lines:]
        else:
            important = important[-max_lines:]

        cleaned: List[str] = []
        for line in important:
            if len(line) > 800:
                line = line[:800] + " ..."
            cleaned.append(line)
        return cleaned

    def _append_child_failure_to_parent(self, parent_id: str, child_id: str, label: str):
        child_task = self.get_task(child_id) or {}
        status = child_task.get("status")
        return_code = child_task.get("return_code")
        self.append_log(
            parent_id,
            f"[CHILD-FAILED] {label} 失败；子任务ID={child_id}；状态={status}；return_code={return_code}",
        )
        cmd = child_task.get("command") or []
        if cmd:
            try:
                self.append_log(parent_id, "[CHILD-COMMAND] " + " ".join(str(x) for x in cmd))
            except Exception:
                pass
        for line in self._extract_error_lines_for_parent(child_task):
            self.append_log(parent_id, f"[CHILD-LOG] {line}")


    def _append_parallel_adjustment_log(self, task_id: str, inputs: Dict[str, Any] | None):
        inputs = inputs or {}
        if not inputs.get("_parallel_auto_adjusted"):
            return
        requested = inputs.get("_requested_parallel_workers") or inputs.get("parallel_workers") or "-"
        effective = inputs.get("_effective_parallel_workers") or inputs.get("_parallel_workers") or "-"
        reason = inputs.get("_parallel_adjust_reason") or "系统负载保护"
        self.append_log(
            task_id,
            f"[SAFE] 用户选择 {requested} 个进程，系统已自动降为 {effective} 个进程。原因：{reason}。",
        )

    def update_task(self, task_id: str, **kwargs):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task.update(kwargs)
        self._save_tasks()

    def submit_module_task(
            self,
            module_id: str,
            module_name: str,
            command: List[str],
            inputs: Dict[str, Any],
            working_dir: str | None = None,
            env: Dict[str, str] | None = None,
            owner_username: str = "",
    ) -> Dict[str, Any]:
        task = self.create_task(
            module_id=module_id,
            module_name=module_name,
            command=command,
            inputs=inputs,
            kind="module",
            extra={"owner_username": str(owner_username or "")},
        )

        self._append_parallel_adjustment_log(task["id"], inputs)
        # 单个模块进程只占 1 个真实启动槽；parallel_workers 只写入配置，不作为父任务排队条件。
        if self._htcondor_execution_requested():
            if self._htcondor_execution_enabled():
                runner = self._run_htcondor_single_task
                runner_args = (task["id"], command, working_dir, env)
            else:
                runner = self._fail_when_htcondor_unavailable
                runner_args = (task["id"], "单模块任务")
        elif self._distributed_execution_requested():
            if self._distributed_execution_enabled():
                runner = self._run_dask_single_task
                runner_args = (task["id"], command, working_dir, env)
            else:
                runner = self._fail_when_dask_unavailable
                runner_args = (task["id"], "单模块任务")
        else:
            runner = self._run_process_task
            runner_args = (task["id"], command, working_dir, env)

        self._enqueue_task_runner(
            task["id"],
            runner,
            runner_args,
            requested_slots=1,
        )
        return self.get_task(task["id"]) or task

    def submit_parallel_module_task(
            self,
            module_id: str,
            module_name: str,
            jobs: List[Dict[str, Any]],
            inputs: Dict[str, Any],
            max_workers: int = 2,
            owner_username: str = "",
    ) -> Dict[str, Any]:
        requested_workers = max(1, int(max_workers or 1))
        job_count = len(jobs)
        effective_workers = max(1, min(requested_workers, max(1, job_count)))

        cleanup_roots = sorted({
            str(job.get("cleanup_root") or "")
            for job in jobs
            if str(job.get("cleanup_root") or "").strip()
        })
        input_link_modes = sorted({
            str(mode)
            for job in jobs
            for mode in (job.get("link_modes") or [])
            if str(mode).strip()
        })

        parent_inputs = dict(inputs or {})
        if cleanup_roots:
            parent_inputs["_parallel_cleanup_roots"] = cleanup_roots
        if input_link_modes:
            parent_inputs["_parallel_chunk_link_modes"] = input_link_modes
        parent_inputs["parallel_workers"] = effective_workers
        parent_inputs["_parallel_workers"] = effective_workers
        parent_inputs["_requested_parallel_workers"] = requested_workers
        parent_inputs["_effective_parallel_workers"] = effective_workers
        if requested_workers != effective_workers:
            parent_inputs["_parallel_worker_note"] = f"用户选择 {requested_workers} 个进程，但本次只有 {job_count} 个子任务，实际只申请 {effective_workers} 个 CPU 进程槽。"

        parent = self.create_task(
            module_id=module_id,
            module_name=module_name,
            command=[],
            inputs=parent_inputs,
            kind="parallel",
            extra={
                "parallel_total": job_count,
                "parallel_done": 0,
                "parallel_failed": 0,
                "max_workers": effective_workers,
                "requested_workers": requested_workers,
                "owner_username": str(owner_username or ""),
                "cleanup_roots": cleanup_roots,
            },
        )

        if requested_workers != effective_workers:
            self.append_log(parent["id"], parent_inputs["_parallel_worker_note"])
        self._append_parallel_adjustment_log(parent["id"], parent_inputs)
        self._enqueue_task_runner(
            parent["id"],
            self._run_parallel_task,
            (parent["id"], jobs, effective_workers),
            requested_slots=self._distributed_parent_slots(effective_workers),
        )
        return self.get_task(parent["id"]) or parent

    def submit_batch_group(
            self,
            module_id: str,
            module_name: str,
            jobs: List[Dict[str, Any]],
            max_parallel: int,
            owner_username: str = "",
    ) -> Dict[str, Any]:
        """Submit a batch parent task using a real stable process-pool style.

        Each job becomes one hidden child task. The parent task is the only task shown
        in task management. max_parallel controls how many child processes are allowed
        to run at the same time. The parent must request the same number of CPU slots
        as the real child-process concurrency, otherwise the scheduler will say it only
        obtained 1 slot while the batch group actually launches multiple children.
        """
        requested_parallel = max(1, int(max_parallel or 1))
        job_count = len(jobs)
        effective_parallel = max(1, min(requested_parallel, max(1, job_count)))

        parent_inputs: Dict[str, Any] = {
            "job_count": job_count,
            "parallel_workers": effective_parallel,
            "_parallel_workers": effective_parallel,
            "_requested_parallel_workers": requested_parallel,
            "_effective_parallel_workers": effective_parallel,
        }
        if requested_parallel != effective_parallel:
            parent_inputs["_parallel_worker_note"] = (
                f"用户选择 {requested_parallel} 个进程，但本次只有 {job_count} 个子任务，"
                f"实际只申请 {effective_parallel} 个 CPU 进程槽。"
            )

        parent = self.create_task(
            module_id=module_id,
            module_name=f"{module_name} 批处理",
            command=[],
            inputs=parent_inputs,
            kind="batch_parent",
            extra={
                "parallel_total": job_count,
                "parallel_done": 0,
                "parallel_failed": 0,
                "max_workers": effective_parallel,
                "requested_workers": requested_parallel,
                "owner_username": str(owner_username or ""),
            },
        )

        child_ids: list[str] = []
        child_job_map: dict[str, Dict[str, Any]] = {}

        for idx, job in enumerate(jobs, start=1):
            child = self.create_task(
                module_id=module_id,
                module_name=f"{module_name} [{idx}/{job_count}]",
                command=job["command"],
                inputs=job["inputs"],
                kind="module",
                extra={
                    "parent_id": parent["id"],
                    "job_index": idx,
                    "owner_username": str(owner_username or ""),
                },
                auto_save=False,
            )
            child_ids.append(child["id"])
            child_job_map[child["id"]] = job

        with self.lock:
            self.tasks[parent["id"]]["children"] = child_ids
            self.tasks[parent["id"]]["status"] = "queued"
        self._save_tasks()

        if parent_inputs.get("_parallel_worker_note"):
            self.append_log(parent["id"], str(parent_inputs["_parallel_worker_note"]))

        first_job_inputs = next(iter(child_job_map.values()), {}).get("inputs") if child_job_map else None
        self._append_parallel_adjustment_log(parent["id"], first_job_inputs)

        self._enqueue_task_runner(
            parent["id"],
            self._run_batch_group,
            (parent["id"], child_job_map, effective_parallel),
            requested_slots=self._distributed_parent_slots(effective_parallel),
        )
        return self.get_task(parent["id"]) or parent

    def _run_batch_group(
        self,
        parent_id: str,
        child_job_map: Dict[str, Dict[str, Any]],
        max_parallel: int,
    ):
        if self._htcondor_execution_requested():
            if self._htcondor_execution_enabled():
                return self._run_batch_group_htcondor(parent_id, child_job_map, max_parallel)
            return self._fail_when_htcondor_unavailable(parent_id, "批处理任务")

        if self._distributed_execution_requested():
            if self._distributed_execution_enabled():
                return self._run_batch_group_dask(parent_id, child_job_map, max_parallel)
            return self._fail_when_dask_unavailable(parent_id, "批处理任务")

        total = len(child_job_map)
        max_parallel = max(1, int(max_parallel or 1))
        self.update_task(
            parent_id,
            status="running",
            started_at=now_iso(),
            parallel_total=total,
            parallel_done=0,
            parallel_failed=0,
            max_workers=max_parallel,
        )
        self.append_log(parent_id, f"[INFO] 批处理开始，共 {total} 个子任务")
        self.append_log(parent_id, f"[INFO] 用户选择并发数 = {max_parallel}；系统会逐个启动子进程，负载高时暂停启动新进程")
        self.append_log(parent_id, "[POOL] 稳定进程池：最多同时运行 max_parallel 个子任务；一个完成后补一个；检测到高负载时先等已有子任务完成，再决定是否补位。")
        self.append_log(parent_id, "[SAFE] 启动新子进程前检查 CPU/内存/磁盘，真正接近危险阈值时才暂停补位。")

        job_items = list(child_job_map.items())
        child_label_map: Dict[str, str] = {
            child_id: str((job or {}).get("label") or child_id)
            for child_id, job in job_items
        }
        next_index = 0
        failures = 0
        done = 0
        with self.lock:
            start_gate = self.child_start_gate_locks.setdefault(parent_id, threading.Lock())
        learned_interval = self.learned_child_start_intervals.get(parent_id)
        last_child_launch_at = 0.0

        def _worker(child_id: str, job: Dict[str, Any]):
            child_snapshot = self.get_task(child_id) or {}
            if parent_id in self.cancel_flags or child_snapshot.get("status") == "cancelled":
                self.update_task(child_id, status="cancelled", ended_at=now_iso())
                return child_id
            self._run_process_task(
                child_id,
                job["command"],
                job.get("working_dir"),
                job.get("env"),
            )
            return child_id

        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            running: Dict[Any, str] = {}
            paused_by_pressure = False
            while (next_index < total or running) and parent_id not in self.cancel_flags:
                launched_any = False
                while next_index < total and len(running) < max_parallel and parent_id not in self.cancel_flags:
                    child_id, job = job_items[next_index]
                    label = job.get("label") or child_id

                    # 一旦检测到 CPU/内存/磁盘压力，就不要在没有子任务完成的情况下继续补位。
                    # 这样不会出现“刚提示暂停，马上又把下一个任务提交上去”的情况。
                    if paused_by_pressure and running:
                        break

                    reason = self._runtime_pressure_reason()
                    if reason:
                        paused_by_pressure = True
                        now = time.time()
                        last = self._last_pressure_log_at.get(parent_id, 0.0)
                        if now - last >= 8:
                            self._last_pressure_log_at[parent_id] = now
                            self.append_log(
                                parent_id,
                                f"[SAFE] 暂停启动新子任务 {label}：{reason}。当前运行 {len(running)} 个；已完成 {done}/{total}。",
                            )
                        break

                    with start_gate:
                        self._wait_until_safe_to_start_child(parent_id, str(label))
                        if parent_id in self.cancel_flags:
                            break
                        if self.adaptive_child_start_enabled and learned_interval is not None:
                            if not self._sleep_before_adaptive_child_launch(parent_id, str(label), learned_interval, last_child_launch_at):
                                break
                            self._wait_until_safe_to_start_child(parent_id, str(label))
                            if parent_id in self.cancel_flags:
                                break

                        self.append_log(parent_id, f"[INFO] 启动子任务 {next_index + 1}/{total}: {label}；当前运行 {len(running) + 1}/{max_parallel}")
                        future = executor.submit(_worker, child_id, job)
                        running[future] = child_id
                        next_index += 1
                        launched_any = True
                        last_child_launch_at = time.time()

                    if self.adaptive_child_start_enabled and learned_interval is None and next_index < total:
                        learned_interval = self._learn_child_start_interval_after_first_launch(parent_id, str(label))
                        self.learned_child_start_intervals[parent_id] = learned_interval
                    elif self.child_start_stagger_seconds > 0:
                        time.sleep(self.child_start_stagger_seconds)

                if not running:
                    # 没有运行中的子任务时，即使压力高也需要周期性重试。
                    paused_by_pressure = False
                    time.sleep(max(1.0, self.child_launch_wait_seconds))
                    continue

                done_set, _ = wait(set(running.keys()), timeout=1.0, return_when=FIRST_COMPLETED)
                if not done_set and not launched_any:
                    time.sleep(0.5)
                    continue

                if done_set:
                    paused_by_pressure = False

                for future in done_set:
                    child_id = running.pop(future, "")
                    try:
                        future.result()
                        task = self.get_task(child_id) or {}
                        status = task.get("status")
                        return_code = task.get("return_code")
                        if status != "success":
                            failures += 1
                            self._append_child_failure_to_parent(
                                parent_id,
                                child_id,
                                child_label_map.get(child_id, child_id),
                            )
                        self.append_log(parent_id, f"[INFO] 子任务完成: {child_id}, 状态={status}, return_code={return_code}")
                    except Exception as e:
                        failures += 1
                        self.append_log(parent_id, f"[ERROR] 子任务异常: {child_id} -> {repr(e)}")
                        self.append_log(parent_id, traceback.format_exc())
                        if child_id:
                            self._append_child_failure_to_parent(
                                parent_id,
                                child_id,
                                child_label_map.get(child_id, child_id),
                            )

                    done += 1
                    self.update_task(parent_id, parallel_done=done, parallel_failed=failures)

        if parent_id in self.cancel_flags:
            final_status = "cancelled"
            return_code = -1
        else:
            final_status = "success" if failures == 0 and done == total else "failed"
            return_code = 0 if final_status == "success" else 1

        self.update_task(
            parent_id,
            status=final_status,
            ended_at=now_iso(),
            return_code=return_code,
            parallel_done=done,
            parallel_failed=failures,
        )
        self.append_log(parent_id, f"[INFO] 批处理结束，完成={done}/{total}，失败数={failures}")
        with self.lock:
            self.learned_child_start_intervals.pop(parent_id, None)
            self.child_start_gate_locks.pop(parent_id, None)
        self.cancel_flags.discard(parent_id)

    def _stream_reader(self, pipe, task_id: str, prefix: str):
        try:
            if pipe is None:
                return

            # tqdm 这类进度条经常用 \r 刷新同一行，不一定输出 \n。
            # 这里按字节块读取，同时识别 \r 和 \n，这样前端能更快看到进度。
            buffer = b""
            last_line = ""

            while True:
                chunk = pipe.read(256)
                if not chunk:
                    break

                buffer += chunk

                while True:
                    positions = [p for p in [buffer.find(b"\n"), buffer.find(b"\r")] if p >= 0]
                    if not positions:
                        break

                    pos = min(positions)
                    raw_line = buffer[:pos]
                    buffer = buffer[pos + 1:]

                    line = self.decode_process_output(raw_line).strip()
                    if not line:
                        continue

                    # 进度条会不断刷新同一行，完全相同的就不重复写。
                    if line == last_line:
                        continue
                    last_line = line
                    self.append_log(task_id, f"[{prefix}] {line}")

            if buffer:
                line = self.decode_process_output(buffer).strip()
                if line and line != last_line:
                    self.append_log(task_id, f"[{prefix}] {line}")

        except Exception as e:
            self.append_log(task_id, f"[PYTHON-LOG-ERROR] {prefix}: {repr(e)}")
        finally:
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:
                pass

    def _log_runtime_context(
            self,
            task_id: str,
            command: List[str],
            working_dir: str | None,
            merged_env: Dict[str, str],
    ):
        self.append_log(task_id, "[INFO] 准备启动模块")
        self.append_log(task_id, f"[INFO] cwd = {working_dir or os.getcwd()}")
        self.append_log(task_id, f"[INFO] command = {' '.join(command)}")

        runtime_source_mode = merged_env.get("RUNTIME_SOURCE_MODE", "")
        fixed_resource_policy = merged_env.get("RUNTIME_FIXED_RESOURCE_POLICY", "")
        if runtime_source_mode or fixed_resource_policy:
            self.append_log(
                task_id,
                f"[INFO] runtime_source_mode = {runtime_source_mode or '-'}；fixed_resource_policy = {fixed_resource_policy or '-'}",
            )
        if merged_env.get("LOCAL_WEB_NO_FIXED_RESOURCE_COPY") == "1":
            self.append_log(
                task_id,
                "[INFO] 固定资源不复制：模型、pkl、resources、LUT 等固定文件直接从 installed_modules 模块目录读取；本任务只生成独立 config.json。",
            )
            if merged_env.get("RUNTIME_SHARED_SOURCE_DIR"):
                self.append_log(task_id, f"[INFO] 固定资源读取目录 = {merged_env.get('RUNTIME_SHARED_SOURCE_DIR')}")
            if merged_env.get("RUNTIME_CONFIG_ONLY_DIR"):
                self.append_log(task_id, f"[INFO] 本任务配置目录 = {merged_env.get('RUNTIME_CONFIG_ONLY_DIR')}")

        path_value = merged_env.get("PATH", "")
        path_parts = path_value.split(";") if path_value else []
        self.append_log(task_id, "[INFO] PATH 前 10 项如下：")
        for idx, item in enumerate(path_parts[:10], start=1):
            self.append_log(task_id, f"[INFO]   {idx}. {item}")

        self.append_log(
            task_id,
            f"[INFO] OPENBLAS_NUM_THREADS = {merged_env.get('OPENBLAS_NUM_THREADS', '')}",
        )
        self.append_log(
            task_id,
            f"[INFO] OMP_NUM_THREADS = {merged_env.get('OMP_NUM_THREADS', '')}",
        )
        self.append_log(
            task_id,
            f"[INFO] GOTO_NUM_THREADS = {merged_env.get('GOTO_NUM_THREADS', '')}",
        )

        config_arg = None

        for item in reversed(command):
            try:
                p = Path(str(item))
                if p.suffix.lower() == ".json":
                    config_arg = p
                    break
            except Exception:
                pass

        if not config_arg:
            return

        self.append_log(task_id, f"[INFO] config/input = {config_arg}")

        if config_arg.exists() and config_arg.suffix.lower() == ".json":
            try:
                content = config_arg.read_text(encoding="utf-8")
                self.append_log(task_id, "[INFO] config.json 内容如下：")
                for line in content.splitlines():
                    self.append_log(task_id, line)
            except Exception as e:
                self.append_log(task_id, f"[WARN] 读取 config.json 失败: {repr(e)}")

    def _hint_from_return_code(self, return_code: int) -> Optional[str]:
        if return_code == 0:
            return None

        hints = {
            -1073741502: "对应 0xc0000142，通常是 DLL / 运行库初始化失败。",
            3221225794: "通常对应 0xc0000142，常见于 DLL 初始化失败。",
            -1073741515: "通常是缺少依赖 DLL。",
            -1073740791: "通常表示原生程序崩溃或堆损坏。",
            -1073741819: "通常表示访问冲突（0xC0000005）。",
        }
        return hints.get(return_code)

    @staticmethod
    def decode_process_output(raw: bytes) -> str:
        if raw is None:
            return ""

        for encoding in ("utf-8", "gbk", "cp936"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue

        return raw.decode("utf-8", errors="replace")
    def _run_process_task(
        self,
        task_id: str,
        command: List[str],
        working_dir: str | None,
        env: Dict[str, str] | None,
    ):
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        self._log_runtime_context(task_id, command, working_dir, merged_env)

        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(
                command,
                cwd=working_dir or None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=False,
                bufsize=0,
                env=merged_env,
                creationflags=creationflags,
            )

            with self.lock:
                self.processes[task_id] = process
                task = self.tasks.get(task_id)
                if task:
                    task["status"] = "running"
                    task["pid"] = process.pid
                    task["started_at"] = now_iso()
                    task.setdefault("logs", []).append(
                        f"[INFO] 进程已启动，PID = {process.pid}"
                    )
            self._save_tasks()

            t_out = threading.Thread(
                target=self._stream_reader,
                args=(process.stdout, task_id, "STDOUT"),
                daemon=True,
            )
            t_err = threading.Thread(
                target=self._stream_reader,
                args=(process.stderr, task_id, "STDERR"),
                daemon=True,
            )
            t_out.start()
            t_err.start()

            return_code = process.wait()

            t_out.join(timeout=1)
            t_err.join(timeout=1)

            with self.lock:
                task = self.tasks.get(task_id)
                if task:
                    if task.get("status") != "cancelled":
                        task["return_code"] = return_code
                        task["status"] = "success" if return_code == 0 else "failed"
                        task["ended_at"] = now_iso()
                    task.setdefault("logs", []).append(
                        f"[INFO] 进程结束，return_code = {return_code}"
                    )

                    hint = self._hint_from_return_code(return_code)
                    if hint:
                        task.setdefault("logs", []).append(f"[HINT] {hint}")

                    if return_code != 0 and not task.get("logs"):
                        task.setdefault("logs", []).append(
                            "[WARN] 进程失败，但没有捕获到 stdout/stderr。"
                        )

            self.processes.pop(task_id, None)
            self._save_tasks()

        except Exception as e:
            with self.lock:
                task = self.tasks.get(task_id)
                if task:
                    if task.get("status") != "cancelled":
                        task["status"] = "failed"
                        task["return_code"] = -1
                        task["ended_at"] = now_iso()
                    task.setdefault("logs", []).append(
                        f"[PYTHON-EXCEPTION] {repr(e)}"
                    )
                    task.setdefault("logs", []).append(traceback.format_exc())

            self.processes.pop(task_id, None)
            self._save_tasks()

    def _run_parallel_task(self, parent_id: str, jobs: List[Dict[str, Any]], max_workers: int):
        if self._htcondor_execution_requested():
            if self._htcondor_execution_enabled():
                return self._run_parallel_task_htcondor(parent_id, jobs, max_workers)
            return self._fail_when_htcondor_unavailable(parent_id, "并行任务")

        if self._distributed_execution_requested():
            if self._distributed_execution_enabled():
                return self._run_parallel_task_dask(parent_id, jobs, max_workers)
            return self._fail_when_dask_unavailable(parent_id, "并行任务")

        total = len(jobs)
        max_workers = max(1, min(int(max_workers or 1), max(1, total)))

        self.update_task(
            parent_id,
            status="running",
            started_at=now_iso(),
            parallel_total=total,
            parallel_done=0,
            parallel_failed=0,
            max_workers=max_workers,
        )

        self.append_log(parent_id, "[BACKEND] 当前任务使用本机进程池（local），未提交到 Dask 集群")
        self.append_log(parent_id, f"[PARALLEL] 并行任务启动：总任务数={total}，实际并行数={max_workers}")
        parent_task = self.get_task(parent_id) or {}
        parent_inputs = parent_task.get("inputs") or {}
        link_modes = parent_inputs.get("_parallel_chunk_link_modes") or []
        if link_modes:
            self.append_log(
                parent_id,
                f"[LINK] 子任务输入文件引用方式：{', '.join(str(x) for x in link_modes)}；系统未复制原始输入大文件到 runtime。"
            )
        self.append_log(parent_id, "[POOL] 稳定进程池：最多同时运行 max_workers 个子任务；一个完成后补一个；检测到高负载时先等已有子任务完成，再决定是否补位。")
        self.append_log(parent_id, "[SAFE] 不再按模型文件大小直接降为 1；启动新子进程前检查 CPU/内存/磁盘，真正接近危险阈值时才暂停补位。")

        progress = {"done": 0, "failed": 0}
        with self.lock:
            start_gate = self.child_start_gate_locks.setdefault(parent_id, threading.Lock())
        learned_interval = self.learned_child_start_intervals.get(parent_id)
        last_child_launch_at = 0.0

        def run_one(index: int, spec: Dict[str, Any]):
            if parent_id in self.cancel_flags:
                return None

            label = spec.get("label") or f"子任务 {index + 1}"
            parent_task = self.get_task(parent_id) or {}
            owner_username = str(parent_task.get("owner_username") or "")

            child = self.create_task(
                module_id=spec.get("module_id", ""),
                module_name=spec.get("module_name", label),
                command=spec.get("command") or [],
                inputs=spec.get("inputs") or {},
                kind="module",
                extra={
                    "parent_id": parent_id,
                    "worker_no": None,
                    "job_index": index + 1,
                    "owner_username": owner_username,
                },
            )

            with self.lock:
                parent = self.tasks.get(parent_id)
                if parent:
                    parent.setdefault("children", []).append(child["id"])
            self._save_tasks()

            self.append_log(parent_id, f"[PARALLEL] 启动 {index + 1}/{total}: {label}")

            self._run_process_task(
                child["id"],
                spec.get("command") or [],
                spec.get("working_dir"),
                spec.get("env"),
            )

            child_task = self.get_task(child["id"]) or {}
            return child["id"], child_task.get("status")

        failures = 0
        next_index = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            running: Dict[Any, int] = {}
            paused_by_pressure = False
            while (next_index < total or running) and parent_id not in self.cancel_flags:
                launched_any = False
                while next_index < total and len(running) < max_workers and parent_id not in self.cancel_flags:
                    spec = jobs[next_index]
                    label = spec.get("label") or f"子任务 {next_index + 1}"

                    # 一旦检测到 CPU/内存/磁盘压力，就等待至少一个正在运行的子任务结束后再判断是否补位。
                    # 旧版会在压力短暂波动时继续提交，导致日志里出现“暂停后仍提交第 6 个任务”。
                    if paused_by_pressure and running:
                        break

                    reason = self._runtime_pressure_reason()
                    if reason:
                        paused_by_pressure = True
                        now = time.time()
                        last = self._last_pressure_log_at.get(parent_id, 0.0)
                        if now - last >= 8:
                            self._last_pressure_log_at[parent_id] = now
                            self.append_log(
                                parent_id,
                                f"[SAFE] 暂停启动新子任务 {label}：{reason}。当前运行 {len(running)} 个；已完成 {progress['done']}/{total}。",
                            )
                        break

                    with start_gate:
                        self._wait_until_safe_to_start_child(parent_id, str(label))
                        if parent_id in self.cancel_flags:
                            break
                        if self.adaptive_child_start_enabled and learned_interval is not None:
                            if not self._sleep_before_adaptive_child_launch(parent_id, str(label), learned_interval, last_child_launch_at):
                                break
                            self._wait_until_safe_to_start_child(parent_id, str(label))
                            if parent_id in self.cancel_flags:
                                break

                        future = executor.submit(run_one, next_index, spec)
                        running[future] = next_index
                        self.append_log(parent_id, f"[PARALLEL] 已提交 {next_index + 1}/{total}；当前运行 {len(running)}/{max_workers}")
                        next_index += 1
                        launched_any = True
                        last_child_launch_at = time.time()

                    if self.adaptive_child_start_enabled and learned_interval is None and next_index < total:
                        learned_interval = self._learn_child_start_interval_after_first_launch(parent_id, str(label))
                        self.learned_child_start_intervals[parent_id] = learned_interval
                    elif self.child_start_stagger_seconds > 0:
                        time.sleep(self.child_start_stagger_seconds)

                if not running:
                    paused_by_pressure = False
                    time.sleep(max(1.0, self.child_launch_wait_seconds))
                    continue

                done_set, _ = wait(set(running.keys()), timeout=1.0, return_when=FIRST_COMPLETED)
                if not done_set and not launched_any:
                    time.sleep(0.5)
                    continue

                if done_set:
                    paused_by_pressure = False

                for future in done_set:
                    idx = running.pop(future, -1)
                    label = jobs[idx].get("label") if 0 <= idx < len(jobs) else "子任务"
                    try:
                        result = future.result()
                        child_id, status = result if result else (None, "cancelled")
                        if status != "success":
                            failures += 1
                            if child_id:
                                self._append_child_failure_to_parent(parent_id, child_id, label)
                        progress["done"] += 1
                        progress["failed"] = failures
                        self.update_task(parent_id, parallel_done=progress["done"], parallel_failed=progress["failed"])
                        self.append_log(parent_id, f"[PARALLEL] 完成 {progress['done']}/{total}: {label}，状态={status}")
                    except Exception as exc:
                        failures += 1
                        progress["done"] += 1
                        progress["failed"] = failures
                        self.update_task(parent_id, parallel_done=progress["done"], parallel_failed=progress["failed"])
                        self.append_log(parent_id, f"[PARALLEL-ERROR] 子任务异常: {type(exc).__name__}: {exc}")
                        self.append_log(parent_id, traceback.format_exc())

        parent = self.get_task(parent_id) or {}
        children = parent.get("children") or []
        child_statuses = [(self.get_task(cid) or {}).get("status") for cid in children]

        if parent_id in self.cancel_flags or any(s == "cancelled" for s in child_statuses):
            final_status = "cancelled"
            return_code = -1
        elif failures > 0 or progress["done"] < total or any(s != "success" for s in child_statuses):
            final_status = "failed"
            return_code = 1
        else:
            final_status = "success"
            return_code = 0

        self.update_task(
            parent_id,
            status=final_status,
            return_code=return_code,
            ended_at=now_iso(),
            parallel_done=progress["done"],
            parallel_failed=sum(1 for s in child_statuses if s != "success"),
        )

        self.append_log(parent_id, f"[PARALLEL] 并行任务结束，状态={final_status}")
        self._cleanup_runtime_roots_for_task(parent_id, reason=f"并行任务结束，状态={final_status}")
        with self.lock:
            self.learned_child_start_intervals.pop(parent_id, None)
            self.child_start_gate_locks.pop(parent_id, None)
        self.cancel_flags.discard(parent_id)

    def cancel_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task:
            return False

        # queued 状态尚未启动进程，直接从调度队列移除并标记取消。
        if task.get("status") == "queued":
            self.cancel_flags.add(task_id)
            with self.lock:
                self._remove_from_scheduler_queue_locked(task_id)
                queued_task = self.tasks.get(task_id)
                if queued_task:
                    queued_task["status"] = "cancelled"
                    queued_task["ended_at"] = now_iso()
                    queued_task.setdefault("logs", []).append("[SYSTEM] 排队任务已取消")

                if queued_task and queued_task.get("kind") in {"parallel", "batch_parent"}:
                    for child_id in queued_task.get("children") or []:
                        child = self.tasks.get(child_id)
                        if child and child.get("status") not in TERMINAL_STATUSES:
                            child["status"] = "cancelled"
                            child["ended_at"] = now_iso()
                            child.setdefault("logs", []).append("[SYSTEM] 父任务排队取消，子任务取消")
            self._save_tasks()
            self._cleanup_runtime_roots_for_task(task_id, reason="排队任务取消")
            self._drain_scheduler_queue()
            return True

        # 子任务还没启动时，也允许取消。
        if task.get("status") == "queued" or (task.get("parent_id") and task.get("status") not in TERMINAL_STATUSES and task_id not in self.processes):
            with self.lock:
                child = self.tasks.get(task_id)
                if child:
                    child["status"] = "cancelled"
                    child["ended_at"] = now_iso()
                    child.setdefault("logs", []).append("[SYSTEM] 子任务排队已取消")
            self._save_tasks()
            return True

        # 并行父任务：标记取消，并尽量停止已经启动的所有子进程。
        if task.get("kind") in {"parallel", "batch_parent"}:
            self.cancel_flags.add(task_id)
            any_stopped = False
            for child_id in task.get("children") or []:
                dask_future = self.dask_futures.get(child_id)
                if dask_future is not None:
                    self._signal_dask_cancel(child_id)
                    try:
                        dask_future.cancel()
                        any_stopped = True
                    except Exception:
                        pass
                process = self.processes.get(child_id)
                if process is not None:
                    try:
                        if process.poll() is None:
                            process.terminate()
                        any_stopped = True
                    except Exception:
                        pass
                with self.lock:
                    child = self.tasks.get(child_id)
                    if child and child.get("status") not in TERMINAL_STATUSES:
                        child["status"] = "cancelled"
                        child["ended_at"] = now_iso()
                        child.setdefault("logs", []).append("[SYSTEM] 父并行任务已取消，子任务终止")
            with self.lock:
                parent = self.tasks.get(task_id)
                if parent:
                    parent["status"] = "cancelled"
                    parent["ended_at"] = now_iso()
                    parent.setdefault("logs", []).append("[SYSTEM] 并行任务已被手动终止")
            self._save_tasks()
            self._cleanup_runtime_roots_for_task(task_id, reason="并行任务取消")
            return True or any_stopped

        dask_future = self.dask_futures.get(task_id)
        if dask_future is not None:
            self._signal_dask_cancel(task_id)
            try:
                dask_future.cancel()
            except Exception:
                pass
            with self.lock:
                task = self.tasks.get(task_id)
                if task:
                    task["status"] = "cancelled"
                    task["ended_at"] = now_iso()
                    task["return_code"] = -1
                    task.setdefault("logs", []).append("[SYSTEM] Dask 任务已请求取消")
                self.dask_futures.pop(task_id, None)
            self._save_tasks()
            return True

        # HTCondor 任务不能按本机 PID 杀掉，必须用 condor_rm 取消。
        if str(task.get("execution_backend") or "") == "htcondor" and task.get("status") not in TERMINAL_STATUSES:
            self.cancel_flags.add(task_id)
            cluster_id = str(task.get("htcondor_cluster_id") or "")
            result = None
            if self.htcondor_manager is not None:
                try:
                    result = self.htcondor_manager.cancel_job(
                        job_id=task_id,
                        cluster_id=cluster_id,
                    )
                except Exception as exc:
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

            with self.lock:
                task = self.tasks.get(task_id)
                if task:
                    task["status"] = "cancelled"
                    task["ended_at"] = now_iso()
                    task["return_code"] = -2
                    task.setdefault("logs", []).append("[SYSTEM] 已请求取消 HTCondor 任务")
                    if result:
                        if result.get("cluster_id"):
                            task["htcondor_cluster_id"] = str(result.get("cluster_id") or "")
                        msg = result.get("message") or result.get("stdout") or result.get("stderr") or result.get("error") or ""
                        if msg:
                            task.setdefault("logs", []).append(f"[HTCONDOR] 停止结果：{msg}")

            self._save_tasks()
            return True

        process = self.processes.get(task_id)
        if process is None:
            return False

        try:
            if process.poll() is None:
                process.terminate()
        except Exception:
            return False

        with self.lock:
            task = self.tasks.get(task_id)
            if task:
                task["status"] = "cancelled"
                task["ended_at"] = now_iso()
                task.setdefault("logs", []).append("[SYSTEM] 任务已被手动终止")

        self.processes.pop(task_id, None)
        self._save_tasks()
        return True

    def delete_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task is None:
            return False

        # 删除并行父任务时，同步删除子任务。
        ids_to_delete = [task_id]
        if task.get("kind") in {"parallel", "batch_parent"}:
            ids_to_delete.extend(task.get("children") or [])

        with self.lock:
            for tid in ids_to_delete:
                self._remove_from_scheduler_queue_locked(tid)

        for tid in ids_to_delete:
            process = self.processes.get(tid)
            if process is not None:
                try:
                    if process.poll() is None:
                        process.terminate()
                except Exception:
                    pass
                self.processes.pop(tid, None)

        cleanup_roots = []
        for tid in ids_to_delete:
            t = self.get_task(tid) or {}
            cleanup_roots.extend(t.get("cleanup_roots") or [])
            cleanup_roots.extend((t.get("inputs") or {}).get("_parallel_cleanup_roots") or [])

        if cleanup_roots:
            self._cleanup_runtime_roots(task_id, cleanup_roots, reason="任务记录删除")

        with self.lock:
            for tid in ids_to_delete:
                self.tasks.pop(tid, None)

        self._save_tasks()
        return True
