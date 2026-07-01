from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent

HTCONDOR_BUNDLE_DIR = PROJECT_ROOT / "third_party" / "htcondor"
HTCONDOR_MSI = HTCONDOR_BUNDLE_DIR / "condor-Windows-x64.msi"
HTCONDOR_MANIFEST = HTCONDOR_BUNDLE_DIR / "manifest.json"

DEFAULT_CONDOR_BIN_CANDIDATES = [
    Path(r"C:\Condor\bin"),
    Path(r"C:\condor\bin"),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "HTCondor" / "bin",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Condor" / "bin",
]

CONDOR_SERVICE_NAME = "Condor"


class HTCondorRuntimeError(RuntimeError):
    """HTCondor 运行时检测错误。"""


def _run_command(command: list[str], timeout: float = 10.0) -> dict[str, Any]:
    """运行短命令并返回可序列化结果。"""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "error": "",
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "error": f"FileNotFoundError: {exc}",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "error": f"命令执行超过 {timeout:.1f} 秒",
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().lower()


def get_bundled_installer_status() -> dict[str, Any]:
    """检查系统内置的 HTCondor MSI 和 manifest.json。"""
    manifest = _read_json(HTCONDOR_MANIFEST)
    expected_hash = str(manifest.get("sha256") or "").strip().lower()

    result: dict[str, Any] = {
        "bundle_dir": str(HTCONDOR_BUNDLE_DIR),
        "msi_path": str(HTCONDOR_MSI),
        "manifest_path": str(HTCONDOR_MANIFEST),
        "msi_exists": HTCONDOR_MSI.is_file(),
        "manifest_exists": HTCONDOR_MANIFEST.is_file(),
        "product_name": str(manifest.get("product_name") or ""),
        "product_version": str(manifest.get("product_version") or ""),
        "expected_sha256": expected_hash,
        "actual_sha256": "",
        "hash_matches_manifest": False,
        "signature_status": str(manifest.get("signature_status") or ""),
        "error": "",
    }

    if not HTCONDOR_MSI.is_file():
        result["error"] = f"找不到内置 MSI：{HTCONDOR_MSI}"
        return result

    try:
        actual_hash = _sha256(HTCONDOR_MSI)
        result["actual_sha256"] = actual_hash
        result["hash_matches_manifest"] = bool(expected_hash) and actual_hash == expected_hash
        if not expected_hash:
            result["error"] = "manifest.json 中没有 sha256"
        elif actual_hash != expected_hash:
            result["error"] = "内置 MSI 的 SHA-256 与 manifest.json 不一致"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


def _find_condor_version_exe() -> Path | None:
    """查找已安装的 condor_version.exe。"""
    from_path = shutil.which("condor_version")
    if from_path:
        path = Path(from_path)
        if path.is_file():
            return path

    for bin_dir in DEFAULT_CONDOR_BIN_CANDIDATES:
        candidate = bin_dir / "condor_version.exe"
        if candidate.is_file():
            return candidate

    return None


def get_condor_service_status() -> dict[str, Any]:
    """查询 Windows Condor 服务，不修改服务状态。"""
    if os.name != "nt":
        return {
            "service_name": CONDOR_SERVICE_NAME,
            "exists": False,
            "state": "unsupported_platform",
            "raw": "",
            "error": "当前系统不是 Windows",
        }

    result = _run_command(["sc.exe", "query", CONDOR_SERVICE_NAME])
    combined = "\n".join(
        value for value in [result.get("stdout", ""), result.get("stderr", ""), result.get("error", "")]
        if value
    )

    upper = combined.upper()
    exists = result["ok"] or "STATE" in upper
    if "RUNNING" in upper:
        state = "running"
    elif "STOPPED" in upper:
        state = "stopped"
    elif "PAUSED" in upper:
        state = "paused"
    elif "1060" in upper or "DOES NOT EXIST" in upper or "未安装的服务" in combined:
        state = "not_installed"
        exists = False
    else:
        state = "unknown"

    return {
        "service_name": CONDOR_SERVICE_NAME,
        "exists": exists,
        "state": state,
        "raw": combined,
        "error": "" if exists or state == "not_installed" else (result.get("error") or result.get("stderr") or ""),
    }


def get_installed_runtime_status() -> dict[str, Any]:
    """检测本机是否已有可用的 HTCondor 命令和 Windows 服务。"""
    version_exe = _find_condor_version_exe()
    service = get_condor_service_status()

    result: dict[str, Any] = {
        "installed": False,
        "version_exe": str(version_exe) if version_exe else "",
        "bin_dir": str(version_exe.parent) if version_exe else "",
        "version_output": "",
        "version_command_ok": False,
        "service": service,
        "error": "",
    }

    if version_exe:
        command_result = _run_command([str(version_exe)])
        result["version_command_ok"] = command_result["ok"]
        result["version_output"] = command_result["stdout"] or command_result["stderr"]
        if not command_result["ok"]:
            result["error"] = command_result["error"] or command_result["stderr"]

    result["installed"] = bool(
        version_exe
        and result["version_command_ok"]
        and service.get("exists")
    )
    return result


def get_htcondor_runtime_status() -> dict[str, Any]:
    """返回前端和安装流程可直接使用的完整检测结果。"""
    bundled = get_bundled_installer_status()
    installed = get_installed_runtime_status()

    bundle_ready = bool(
        bundled.get("msi_exists")
        and bundled.get("manifest_exists")
        and bundled.get("hash_matches_manifest")
        and bundled.get("product_version")
    )

    return {
        "platform": platform.platform(),
        "is_windows": os.name == "nt",
        "project_root": str(PROJECT_ROOT),
        "bundled_installer": bundled,
        "installed_runtime": installed,
        "bundle_ready": bundle_ready,
        "ready_to_install": bool(os.name == "nt" and bundle_ready and not installed.get("installed")),
        "ready_to_configure": bool(os.name == "nt" and installed.get("installed")),
    }


def main() -> int:
    status = get_htcondor_runtime_status()
    print(json.dumps(status, ensure_ascii=False, indent=2))

    if not status["is_windows"]:
        return 2
    if not status["bundle_ready"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
