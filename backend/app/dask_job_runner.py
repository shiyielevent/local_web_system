from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def execute_subprocess_job(payload: Dict[str, Any]) -> dict:
    """
    在 Dask Worker 上启动算法模块。

    payload 只携带命令、环境变量、工作目录和小型 config.json 文本；
    NC/HDF/TIF 等大型输入文件必须通过所有节点可访问的共享路径读取。
    """
    command = [str(x) for x in (payload.get("command") or [])]
    working_dir = str(payload.get("working_dir") or "").strip()
    custom_env = payload.get("env") or {}
    config_text = payload.get("config_text")
    config_arg_index = payload.get("config_arg_index")
    timeout = payload.get("timeout")
    cancel_file = str(payload.get("cancel_file") or "").strip()
    max_output_chars = max(10000, int(payload.get("max_output_chars") or 2_000_000))

    started_at = _now_iso()
    started_monotonic = time.monotonic()
    temp_dir = None

    result = {
        "job_id": str(payload.get("job_id") or ""),
        "hostname": socket.gethostname(),
        "ip": "",
        "platform": platform.platform(),
        "pid": None,
        "return_code": -1,
        "stdout": "",
        "stderr": "",
        "started_at": started_at,
        "ended_at": None,
        "duration_seconds": None,
        "command": command,
        "working_dir": working_dir,
    }

    try:
        try:
            result["ip"] = socket.gethostbyname(socket.gethostname())
        except Exception:
            result["ip"] = ""

        if not command:
            raise RuntimeError("远程任务命令为空")

        if config_text is not None and config_arg_index is not None:
            temp_dir = tempfile.mkdtemp(prefix="local_web_dask_job_")
            local_config = Path(temp_dir) / "config.json"
            local_config.write_text(str(config_text), encoding="utf-8")
            index = int(config_arg_index)
            if 0 <= index < len(command):
                command[index] = str(local_config)
                result["command"] = command

        if working_dir and not Path(working_dir).is_dir():
            raise FileNotFoundError(
                f"远程节点工作目录不存在：{working_dir}。"
                "请保证所有节点的项目和模块安装路径一致。"
            )

        executable = command[0]
        looks_absolute = (
            Path(executable).is_absolute()
            or (len(executable) >= 3 and executable[1:3] in {":\\", ":/"})
        )
        if looks_absolute and not Path(executable).exists():
            raise FileNotFoundError(
                f"远程节点可执行文件不存在：{executable}。"
                "请在所有节点安装相同模块，或保持相同绝对路径。"
            )

        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in custom_env.items()})
        # 平台负责进程级并行，避免每个任务内部再占满全部 CPU。
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("MKL_NUM_THREADS", "1")
        env.setdefault("OPENBLAS_NUM_THREADS", "1")
        env.setdefault("NUMEXPR_NUM_THREADS", "1")

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
            env=env,
            creationflags=creationflags,
            shell=False,
        )
        result["pid"] = int(process.pid)

        deadline = None
        if timeout not in (None, "", 0, "0"):
            deadline = time.monotonic() + float(timeout)

        cancelled = False
        while True:
            try:
                stdout_raw, stderr_raw = process.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel_file and Path(cancel_file).exists():
                    cancelled = True
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            text=True,
                            shell=False,
                        )
                    else:
                        process.terminate()
                    stdout_raw, stderr_raw = process.communicate()
                    break

                if deadline is not None and time.monotonic() >= deadline:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            text=True,
                            shell=False,
                        )
                    else:
                        process.kill()
                    stdout_raw, stderr_raw = process.communicate()
                    result["stderr"] = f"任务执行超时，已终止。timeout={timeout}\n"
                    break

        stdout = (stdout_raw or b"").decode("utf-8", errors="replace")
        stderr = (stderr_raw or b"").decode("utf-8", errors="replace")
        if result["stderr"]:
            stderr = result["stderr"] + stderr

        if cancelled:
            result["cancelled"] = True
            result["return_code"] = -2
            stderr = "[CANCELLED] 收到平台取消信号，远程进程已终止。\n" + stderr
        else:
            result["cancelled"] = False
            result["return_code"] = int(process.returncode if process.returncode is not None else -1)
        result["stdout"] = stdout[-max_output_chars:]
        result["stderr"] = stderr[-max_output_chars:]
        return result

    except Exception as exc:
        result["return_code"] = -1
        result["stderr"] = f"{type(exc).__name__}: {exc}"
        return result

    finally:
        result["ended_at"] = _now_iso()
        result["duration_seconds"] = round(time.monotonic() - started_monotonic, 3)
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if cancel_file:
            try:
                Path(cancel_file).unlink(missing_ok=True)
            except Exception:
                pass
