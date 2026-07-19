from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import ipaddress
import locale
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .htcondor_runtime import get_htcondor_runtime_status


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class HTCondorClusterError(RuntimeError):
    pass


class HTCondorClusterManager:
    """很简单的 HTCondor 执行管理器。

    这个版本先做平台能用的功能：
    1. 查看本机 HTCondor 状态；
    2. 在 local / htcondor 两种执行模式之间切换；
    3. 把平台生成的命令包装成 condor_submit 作业运行。

    说明：
    这个文件故意写得直接一点，方便后续继续改成真正的创建 / 加入 HTCondor 池。
    """

    def __init__(self, base_dir: str | Path, project_root: str | Path | None = None):
        self.base_dir = Path(base_dir)
        self.project_root = Path(project_root) if project_root else self.base_dir.parent
        self.runtime_dir = self.base_dir / "runtime" / "htcondor"
        self.job_dir = self.runtime_dir / "jobs"
        self.log_dir = self.base_dir / "logs" / "htcondor"
        self.state_file = self.runtime_dir / "state.json"
        self.install_result_file = self.runtime_dir / "install_result.json"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.default_timeout_seconds = int(os.environ.get("LOCAL_WEB_HTCONDOR_JOB_TIMEOUT", "604800"))
        # 作业监控轮询间隔。默认 1.5 秒，减少多个并发子任务同时每秒读取日志造成的磁盘 I/O。
        # 如需更快刷新可设置 LOCAL_WEB_HTCONDOR_POLL_SECONDS=1；如任务很多可设为 2~3。
        try:
            self.job_poll_seconds = max(
                0.5,
                min(10.0, float(os.environ.get("LOCAL_WEB_HTCONDOR_POLL_SECONDS", "1.5") or "1.5")),
            )
        except Exception:
            self.job_poll_seconds = 1.5
        self.running_jobs = {}
        self.state = self._load_state()
        self._parent_health_lock = threading.Lock()
        self._parent_health: Dict[str, Any] = {
            "endpoint": "",
            "consecutive_failures": 0,
            "last_failure_monotonic": 0.0,
            "last_success_at": "",
        }
        self._write_health_lock = threading.Lock()
        self._write_health: Dict[str, Any] = {
            "endpoint": "",
            "state": "unknown",
            "consecutive_timeouts": 0,
            "last_timeout_monotonic": 0.0,
            "last_success_at": "",
        }

        try:
            self.status_query_timeout_seconds = max(
                30.0,
                min(90.0, float(os.environ.get("LOCAL_WEB_HTCONDOR_STATUS_TIMEOUT", "30") or "30")),
            )
        except Exception:
            self.status_query_timeout_seconds = 30.0
        try:
            self.parent_failure_threshold = max(
                2,
                min(5, int(os.environ.get("LOCAL_WEB_HTCONDOR_FAILURE_THRESHOLD", "2") or "2")),
            )
        except Exception:
            self.parent_failure_threshold = 2

    def _load_json(self, path: Path) -> Dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_json(self, path: Path, data: Dict[str, Any]):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_text_auto(self, path: Path) -> str:
        """读取 Windows 命令输出，自动处理 UTF-8/UTF-16 BOM。"""
        if not path.is_file():
            return ""
        raw = path.read_bytes()
        if not raw:
            return ""
        try:
            if raw.startswith(b"\xff\xfe"):
                return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
            if raw.startswith(b"\xfe\xff"):
                return raw.decode("utf-16-be", errors="replace").lstrip("\ufeff")
            if raw.startswith(b"\xef\xbb\xbf"):
                return raw.decode("utf-8-sig", errors="replace")

            text = raw.decode("utf-8", errors="replace")
            if "�" in text and os.name == "nt":
                # 有些 cmd / exe 输出不是 UTF-8，直接按 UTF-8 读会变成乱码。
                # 这里再用 Windows 本地编码试一次。
                alt = raw.decode(self._win_text_encoding(), errors="replace")
                if alt.count("�") < text.count("�"):
                    return alt
            return text
        except Exception:
            return raw.decode(self._win_text_encoding(), errors="replace")

    def _decode_text_bytes_auto(self, raw: bytes, at_start: bool = False) -> str:
        """解码日志字节，兼容 UTF-8、UTF-16 BOM 和 Windows 本地编码。"""
        if not raw:
            return ""
        try:
            if at_start and raw.startswith(b"\xff\xfe"):
                return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
            if at_start and raw.startswith(b"\xfe\xff"):
                return raw.decode("utf-16-be", errors="replace").lstrip("\ufeff")
            if at_start and raw.startswith(b"\xef\xbb\xbf"):
                return raw.decode("utf-8-sig", errors="replace")

            text = raw.decode("utf-8", errors="replace")
            if "�" in text and os.name == "nt":
                alt = raw.decode(self._win_text_encoding(), errors="replace")
                if alt.count("�") < text.count("�"):
                    return alt
            return text
        except Exception:
            return raw.decode(self._win_text_encoding(), errors="replace")

    def _read_text_delta_auto(self, path: Path, offset: int = 0) -> tuple[str, int]:
        """只读取日志文件从 offset 之后新增的字节。

        旧实现每轮都读取完整 stdout/stderr/event.log，日志越长，重复读取量越大。
        新实现只读取新增部分；若文件被截断或重新创建，则自动从头读取。
        """
        try:
            if not path.is_file():
                return "", max(0, int(offset or 0))
            size = int(path.stat().st_size)
            current = max(0, int(offset or 0))
            if size < current:
                current = 0
            if size == current:
                return "", current
            with path.open("rb") as stream:
                stream.seek(current)
                raw = stream.read()
            return self._decode_text_bytes_auto(raw, at_start=(current == 0)), current + len(raw)
        except Exception:
            return "", max(0, int(offset or 0))

    def _emit_live_piece(self, callback, job_id: str, prefix: str, piece: str):
        """直接发送增量日志片段，避免再次按完整文本长度切片。"""
        if callback is None or not piece:
            return
        try:
            callback({
                "type": str(prefix or "event").lower(),
                "job_id": job_id,
                "text": piece,
            })
        except Exception:
            pass

    def _win_text_encoding(self) -> str:
        # Windows PowerShell 5.1 的错误输出通常是系统本地编码，
        # 中文 Windows 上一般是 gbk。这里不用写死 gbk，直接问系统。
        if os.name == "nt":
            return locale.getpreferredencoding(False) or "utf-8"
        return "utf-8"

    def _repair_acl_hint(self) -> str:
        return (
            "这属于系统自动修复项，不需要用户手动执行 PowerShell。"
            "请关闭系统后重新运行 start_system.bat，启动器会自动请求管理员授权并修复权限。"
        )

    def _load_state(self) -> Dict[str, Any]:
        data = self._load_json(self.state_file)
        if not data:
            data = {
                "execution_mode": "local",
                "updated_at": now_iso(),
            }
            self._write_json(self.state_file, data)
        return data

    def _save_state(self):
        self.state["updated_at"] = now_iso()
        self._write_json(self.state_file, self.state)


    # =========================
    # HTCondor 共享目录模式配置
    # =========================
    def _normalize_shared_io_items(self) -> List[Dict[str, Any]]:
        """把旧版单共享配置和新版多共享配置统一成 shares 列表。"""
        raw = self.state.get("shared_io_config") or {}
        if not isinstance(raw, dict):
            raw = {}

        items: List[Dict[str, Any]] = []

        def add_item(item: Dict[str, Any]):
            if not isinstance(item, dict):
                return
            local_root = str(item.get("local_root") or "").strip()
            unc_root = str(item.get("unc_root") or "").strip()
            share_name = str(item.get("share_name") or "").strip()
            if not share_name:
                share_name = (unc_root.rstrip("\\/").split("\\")[-1] if unc_root else "LocalWebData") or "LocalWebData"
            enabled = bool(item.get("enabled", True)) and bool(local_root or unc_root)
            clean = {
                "enabled": enabled,
                "local_root": local_root,
                "unc_root": unc_root,
                "share_name": share_name,
                "role": str(item.get("role") or raw.get("role") or "").strip(),
                "parent_ip": str(item.get("parent_ip") or raw.get("parent_ip") or "").strip(),
                "connect_ok": bool(item.get("connect_ok", raw.get("connect_ok", enabled))),
                "connect_message": str(item.get("connect_message") or raw.get("connect_message") or "").strip(),
                "updated_at": str(item.get("updated_at") or raw.get("updated_at") or "").strip(),
            }
            key = (clean["unc_root"] or clean["local_root"] or clean["share_name"]).lower()
            if not key:
                return
            for idx, old in enumerate(items):
                old_key = str(old.get("unc_root") or old.get("local_root") or old.get("share_name") or "").lower()
                if old_key == key or str(old.get("share_name") or "").lower() == share_name.lower():
                    items[idx] = {**old, **clean}
                    return
            items.append(clean)

        raw_shares = raw.get("shares")
        if isinstance(raw_shares, list):
            for item in raw_shares:
                add_item(item)
        if raw.get("unc_root") or raw.get("local_root"):
            add_item(raw)
        return items

    def _primary_shared_io_item(self, items: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
        items = items if items is not None else self._normalize_shared_io_items()
        if not items:
            return {}
        raw = self.state.get("shared_io_config") or {}
        primary_unc = str(raw.get("primary_unc_root") or raw.get("unc_root") or "").strip().lower()
        if primary_unc:
            for item in items:
                if str(item.get("unc_root") or "").strip().lower() == primary_unc:
                    return item
        return next((x for x in items if x.get("enabled")), items[0])

    def _save_shared_io_items(self, items: List[Dict[str, Any]], primary_unc_root: str = ""):
        primary_unc_root = str(primary_unc_root or "").strip()
        primary = None
        if primary_unc_root:
            for item in items:
                if str(item.get("unc_root") or "").strip().lower() == primary_unc_root.lower():
                    primary = item
                    break
        if primary is None and items:
            primary = next((x for x in items if x.get("enabled")), items[0])
        primary = primary or {}
        self.state["shared_io_config"] = {
            "enabled": any(bool(x.get("enabled")) for x in items),
            "local_root": str(primary.get("local_root") or ""),
            "unc_root": str(primary.get("unc_root") or ""),
            "share_name": str(primary.get("share_name") or "LocalWebData"),
            "role": str(primary.get("role") or ""),
            "parent_ip": str(primary.get("parent_ip") or ""),
            "connect_ok": bool(primary.get("connect_ok")),
            "connect_message": str(primary.get("connect_message") or ""),
            "primary_unc_root": str(primary.get("unc_root") or primary_unc_root or ""),
            "shares": items,
            "updated_at": now_iso(),
        }
        self._save_state()

    def shared_io_config(self) -> Dict[str, Any]:
        """读取共享目录模式配置。新版支持多个共享目录，并兼容旧版单共享字段。"""
        items = self._normalize_shared_io_items()
        primary = self._primary_shared_io_item(items)
        enabled = any(bool(item.get("enabled")) for item in items)
        connect_ok = bool(primary.get("connect_ok"))
        connect_message = str(primary.get("connect_message") or "").strip()

        pool_role = str(self.state.get("pool_role") or "standalone").strip()
        pool_parent_ip = str(self.state.get("parent_ip") or "").strip()
        shared_role = str(primary.get("role") or "").strip()
        shared_parent_ip = str(primary.get("parent_ip") or "").strip()
        stale = bool(
            items
            and pool_role in {"parent", "child"}
            and (
                shared_role != pool_role
                or (pool_role == "child" and shared_parent_ip != pool_parent_ip)
            )
        )
        if stale:
            enabled = False
            connect_ok = False
            connect_message = "共享目录配置属于旧集群角色或旧父节点，请重新连接当前父节点共享目录。"
            items = [{**item, "enabled": False, "connect_ok": False, "stale": True} for item in items]
        return {
            "enabled": bool(enabled),
            "local_root": str(primary.get("local_root") or "").strip(),
            "unc_root": str(primary.get("unc_root") or "").strip(),
            "share_name": str(primary.get("share_name") or "LocalWebData").strip() or "LocalWebData",
            "role": str(primary.get("role") or "").strip(),
            "parent_ip": str(primary.get("parent_ip") or "").strip(),
            "connect_ok": connect_ok,
            "connect_message": connect_message,
            "stale": stale,
            "shares": items,
            "share_count": len(items),
            "primary_unc_root": str(primary.get("unc_root") or "").strip(),
            "updated_at": str((self.state.get("shared_io_config") or {}).get("updated_at") or ""),
        }

    def set_shared_io_config(
        self,
        enabled: bool,
        local_root: str = "",
        unc_root: str = "",
        share_name: str = "LocalWebData",
    ) -> Dict[str, Any]:
        """保存共享目录配置。启用时采用追加/更新方式，允许多个共享目录同时存在。"""
        local_root = str(local_root or "").strip()
        unc_root = str(unc_root or "").strip()
        share_name = str(share_name or "LocalWebData").strip() or "LocalWebData"

        if not enabled:
            items = self._normalize_shared_io_items()
            for item in items:
                item["enabled"] = False
                item["connect_ok"] = False
            self._save_shared_io_items(items)
            data = self.shared_io_config()
            data["message"] = "共享目录模式已关闭"
            return data

        if not local_root:
            raise HTCondorClusterError("启用共享目录模式时必须填写父节点本地目录，例如 D:/H8/data。")
        if not unc_root:
            host = socket.gethostname()
            unc_root = f"\\\\{host}\\{share_name}"

        items = self._normalize_shared_io_items()
        raw = self.state.get("shared_io_config") or {}
        new_item = {
            "enabled": True,
            "local_root": local_root,
            "unc_root": unc_root,
            "share_name": share_name,
            "role": str(raw.get("role") or "parent"),
            "parent_ip": str(raw.get("parent_ip") or ""),
            "connect_ok": True,
            "connect_message": "共享目录已添加",
            "updated_at": now_iso(),
        }
        key = (unc_root or local_root or share_name).lower()
        replaced = False
        for idx, item in enumerate(items):
            old_key = str(item.get("unc_root") or item.get("local_root") or item.get("share_name") or "").lower()
            if old_key == key or str(item.get("share_name") or "").lower() == share_name.lower():
                items[idx] = {**item, **new_item}
                replaced = True
                break
        if not replaced:
            items.append(new_item)
        self._save_shared_io_items(items, primary_unc_root=unc_root)
        data = self.shared_io_config()
        data["message"] = "共享目录已添加"
        return data

    def delete_shared_io_config(
        self,
        share_name: str = "",
        unc_root: str = "",
        local_root: str = "",
        delete_windows_share: bool = True,
    ) -> Dict[str, Any]:
        """删除一个共享目录配置。父节点可同时删除 Windows 共享，但不会删除本地数据文件。"""
        share_name = str(share_name or "").strip()
        unc_root = str(unc_root or "").strip()
        local_root = str(local_root or "").strip()
        if not (share_name or unc_root or local_root):
            raise HTCondorClusterError("删除共享目录时必须提供共享名、UNC 路径或本地目录。")

        items = self._normalize_shared_io_items()
        kept: List[Dict[str, Any]] = []
        deleted: List[Dict[str, Any]] = []

        def same(a: str, b: str) -> bool:
            return str(a or "").strip().rstrip("\\/").lower() == str(b or "").strip().rstrip("\\/").lower()

        for item in items:
            matched = False
            if share_name and same(item.get("share_name"), share_name):
                matched = True
            if unc_root and same(item.get("unc_root"), unc_root):
                matched = True
            if local_root and same(item.get("local_root"), local_root):
                matched = True
            if matched:
                deleted.append(item)
            else:
                kept.append(item)

        if not deleted:
            raise HTCondorClusterError("没有找到要删除的共享目录配置。")

        commands: List[Dict[str, Any]] = []
        admin_result: Dict[str, Any] | None = None
        # 父节点删除共享名需要管理员权限；只删除共享映射，不删除本地文件夹。
        if delete_windows_share and os.name == "nt":
            names = []
            for item in deleted:
                name = str(item.get("share_name") or "").strip()
                if name and name not in names:
                    names.append(name)
            if names:
                result_path = self.runtime_dir / "cluster_admin" / "delete_share_result.json"
                names_json = json.dumps(names, ensure_ascii=False)
                script_text = f"""
$ErrorActionPreference = 'Continue'
$resultPath = {self._ps_quote(str(result_path))}
$shareNames = ConvertFrom-Json -InputObject {self._ps_quote(names_json)}
$commands = New-Object System.Collections.ArrayList
function Add-CommandResult([string]$command, [bool]$ok, [string]$stdout, [string]$stderr, [int]$returncode) {{
    [void]$commands.Add([ordered]@{{ command=$command; ok=$ok; stdout=$stdout; stderr=$stderr; returncode=$returncode }})
}}
foreach ($name in $shareNames) {{
    try {{
        $output = & net.exe share $name /delete /y 2>&1
        $code = if ($null -eq $LASTEXITCODE) {{ 0 }} else {{ [int]$LASTEXITCODE }}
        $text = (($output | Out-String).Trim())
        Add-CommandResult ('net share ' + $name + ' /delete') ($code -eq 0 -or $text -match '不存在|does not exist|not found') $text '' $code
    }} catch {{
        Add-CommandResult ('net share ' + $name + ' /delete') $false '' $_.Exception.Message -1
    }}
    try {{
        if (Get-Command Remove-SmbShare -ErrorAction SilentlyContinue) {{
            $s = Get-SmbShare -Name $name -ErrorAction SilentlyContinue
            if ($null -ne $s) {{
                Remove-SmbShare -Name $name -Force -ErrorAction Stop
                Add-CommandResult ('Remove-SmbShare ' + $name) $true 'ok' '' 0
            }} else {{
                Add-CommandResult ('Remove-SmbShare ' + $name) $true 'not exists' '' 0
            }}
        }}
    }} catch {{
        Add-CommandResult ('Remove-SmbShare ' + $name) $false '' $_.Exception.Message -1
    }}
}}
[ordered]@{{
    success = $true
    message = '共享目录已从 Windows 共享中移除；本地数据文件未删除。'
    deleted_share_names = $shareNames
    commands = $commands
}} | ConvertTo-Json -Depth 8 | Set-Content -Path $resultPath -Encoding UTF8
"""
                admin_result = self._run_elevated_ps("delete_share", script_text, timeout=180)
                commands = list(admin_result.get("commands") or []) if isinstance(admin_result, dict) else []

        # 子节点或非管理员场景下，尝试移除 net use 映射；失败不影响配置删除。
        for item in deleted:
            unc = str(item.get("unc_root") or "").strip()
            if unc:
                commands.append({"command": f"net use {unc} /delete", **self._run(["net.exe", "use", unc, "/delete", "/y"], timeout=30)})

        self._save_shared_io_items(kept)
        data = self.shared_io_config()
        data["message"] = f"已删除 {len(deleted)} 个共享目录配置。本地目录和数据文件不会被删除。"
        data["deleted"] = deleted
        data["deleted_count"] = len(deleted)
        data["commands"] = commands
        data["admin_result"] = admin_result or {}
        return data

    def prepare_local_share(self, local_root: str, share_name: str = "LocalWebData", unc_host: str = "") -> Dict[str, Any]:
        """在父节点本机创建 Windows 共享目录。

        这一版改为：普通后端进程不直接执行 icacls/net share，而是生成一个
        PowerShell 管理员脚本，并通过 UAC 只请求一次管理员权限完成：
        1. 创建本地目录；
        2. 设置 NTFS 权限；
        3. 创建/重建 Windows 共享；
        4. 写回 shared_io_config。
        """
        if os.name != "nt":
            raise HTCondorClusterError("共享目录自动配置当前只支持 Windows。")

        local_root = str(local_root or "").strip()
        share_name = str(share_name or "LocalWebData").strip() or "LocalWebData"
        if not local_root:
            raise HTCondorClusterError("本地共享目录不能为空，例如 D:/H8/data。")

        # 共享名不能包含反斜杠、斜杠、冒号等特殊字符，避免 net share 直接打印帮助信息。
        safe_share_name = re.sub(r"[^0-9A-Za-z_.\-]+", "_", share_name).strip("._-")
        if not safe_share_name:
            safe_share_name = "LocalWebData"
        share_name = safe_share_name

        host = str(unc_host or self.state.get("bind_ip") or "").strip()
        if not host:
            local_ips = self._local_ipv4_list()
            host = next((ip for ip in local_ips if ip.startswith("192.168.")), "") or (local_ips[0] if local_ips else socket.gethostname())
        unc_root = f"\\\\{host}\\{share_name}"

        root = Path(local_root)
        result_path = self.runtime_dir / "cluster_admin" / "prepare_share_result.json"
        share_user = str(os.environ.get("LOCAL_WEB_HTCONDOR_SHARE_USER", "")).strip()

        script_text = f"""
$ErrorActionPreference = 'Stop'
$resultPath = {self._ps_quote(str(result_path))}
$localRoot = {self._ps_quote(str(root))}
$shareName = {self._ps_quote(share_name)}
$uncRoot = {self._ps_quote(unc_root)}
$shareUser = {self._ps_quote(share_user)}
$commands = New-Object System.Collections.ArrayList

function Add-CommandResult([string]$command, [bool]$ok, [string]$stdout, [string]$stderr, [int]$returncode) {{
    [void]$commands.Add([ordered]@{{
        command = $command
        ok = $ok
        stdout = $stdout
        stderr = $stderr
        returncode = $returncode
    }})
}}

function Run-External([string]$file, [string[]]$argsList, [bool]$ignoreFail = $false) {{
    $cmdText = $file + ' ' + ($argsList -join ' ')
    try {{
        $output = & $file @argsList 2>&1
        $code = if ($null -eq $LASTEXITCODE) {{ 0 }} else {{ [int]$LASTEXITCODE }}
        $text = (($output | Out-String).Trim())
        $ok = ($code -eq 0)
        Add-CommandResult $cmdText $ok $text '' $code
        if ((-not $ok) -and (-not $ignoreFail)) {{
            throw ($cmdText + ' failed: ' + $text)
        }}
        return @{{ ok = $ok; stdout = $text; returncode = $code }}
    }} catch {{
        $msg = $_.Exception.Message
        Add-CommandResult $cmdText $false '' $msg -1
        if (-not $ignoreFail) {{ throw }}
        return @{{ ok = $false; stderr = $msg; returncode = -1 }}
    }}
}}

function Resolve-AccountName([string]$sidText, [string]$fallback) {{
    try {{
        $sid = New-Object System.Security.Principal.SecurityIdentifier($sidText)
        return $sid.Translate([System.Security.Principal.NTAccount]).Value
    }} catch {{
        return $fallback
    }}
}}

try {{
    New-Item -ItemType Directory -Force -Path $localRoot | Out-Null
    Add-CommandResult ('New-Item -ItemType Directory -Force -Path ' + $localRoot) $true 'ok' '' 0

    # NTFS 权限优先用 SID，避免中文 Windows 上 Everyone/Users 名称本地化导致失败。
    Run-External 'icacls.exe' @($localRoot, '/grant', '*S-1-1-0:(OI)(CI)M', '/T', '/C') $true | Out-Null
    Run-External 'icacls.exe' @($localRoot, '/grant', '*S-1-5-32-545:(OI)(CI)M', '/T', '/C') $true | Out-Null

    $computerName = $env:COMPUTERNAME
    if ($computerName) {{
        Run-External 'icacls.exe' @($localRoot, '/grant', ($computerName + '\\LocalWebCondor:(OI)(CI)M'), '/T', '/C') $true | Out-Null
    }}
    if ($shareUser) {{
        Run-External 'icacls.exe' @($localRoot, '/grant', ($shareUser + ':(OI)(CI)M'), '/T', '/C') $true | Out-Null
    }}

    # 已有同名共享时先删除。失败通常表示不存在，可以忽略。
    Run-External 'net.exe' @('share', $shareName, '/delete', '/y') $true | Out-Null

    $everyoneName = Resolve-AccountName 'S-1-1-0' 'Everyone'
    $usersName = Resolve-AccountName 'S-1-5-32-545' 'Users'
    $created = $false

    # 优先用 net share。这里使用本机本地化后的 Everyone/Users 名称，避免语法帮助页问题。
    $r1 = Run-External 'net.exe' @('share', ($shareName + '=' + $localRoot), ('/GRANT:' + $everyoneName + ',FULL')) $true
    if ($r1.ok) {{ $created = $true }}

    if (-not $created) {{
        $r2 = Run-External 'net.exe' @('share', ($shareName + '=' + $localRoot), ('/GRANT:' + $usersName + ',FULL')) $true
        if ($r2.ok) {{ $created = $true }}
    }}

    # net share 在部分中文系统/账号名场景可能只打印帮助；失败时改用 PowerShell SMB cmdlet。
    if (-not $created) {{
        try {{
            if (Get-Command New-SmbShare -ErrorAction SilentlyContinue) {{
                $old = Get-SmbShare -Name $shareName -ErrorAction SilentlyContinue
                if ($null -ne $old) {{ Remove-SmbShare -Name $shareName -Force -ErrorAction SilentlyContinue }}
                New-SmbShare -Name $shareName -Path $localRoot -FullAccess $everyoneName -CachingMode None -ErrorAction Stop | Out-Null
                Add-CommandResult ('New-SmbShare -Name ' + $shareName + ' -Path ' + $localRoot) $true 'ok' '' 0
                $created = $true
            }}
        }} catch {{
            Add-CommandResult ('New-SmbShare -Name ' + $shareName + ' -Path ' + $localRoot) $false '' $_.Exception.Message -1
        }}
    }}

    # 最后兜底创建共享；如果共享创建成功但权限较保守，NTFS 权限仍然保证读写基础。
    if (-not $created) {{
        $r3 = Run-External 'net.exe' @('share', ($shareName + '=' + $localRoot)) $true
        if ($r3.ok) {{ $created = $true }}
    }}

    if (-not $created) {{
        throw 'Windows 共享目录创建失败：net share 和 New-SmbShare 均未成功。请检查是否同意了 UAC 管理员授权，以及共享名/路径是否合法。'
    }}

    # 尽量开启文件和打印机共享防火墙规则；不同系统语言下 group 名可能不同，失败不影响主流程。
    Run-External 'netsh.exe' @('advfirewall', 'firewall', 'set', 'rule', 'group=File and Printer Sharing', 'new', 'enable=Yes') $true | Out-Null

    $verify = Run-External 'net.exe' @('share', $shareName) $true
    if (-not $verify.ok) {{ throw '共享创建后校验失败：net share ' + $shareName }}

    $result = [ordered]@{{
        success = $true
        ok = $true
        message = '管理员授权完成，Windows 共享目录已创建。'
        local_root = $localRoot
        share_name = $shareName
        unc_root = $uncRoot
        commands = $commands
    }}
}} catch {{
    $result = [ordered]@{{
        success = $false
        ok = $false
        message = $_.Exception.Message
        local_root = $localRoot
        share_name = $shareName
        unc_root = $uncRoot
        commands = $commands
    }}
}}

$result | ConvertTo-Json -Depth 8 | Set-Content -Path $resultPath -Encoding UTF8
"""

        admin_result = self._run_elevated_ps("prepare_share", script_text, timeout=240)
        commands = admin_result.get("commands") if isinstance(admin_result.get("commands"), list) else []
        if not bool(admin_result.get("success") or admin_result.get("ok")):
            detail = admin_result.get("message") or admin_result.get("launcher_stderr") or admin_result.get("launcher_stdout") or "未知错误"
            raise HTCondorClusterError(
                "创建 Windows 共享目录失败。系统已经尝试弹出一次管理员权限窗口；"
                "如果没有看到 UAC，请确认浏览器后方是否有授权窗口。"
                f"输出：{detail}"
            )

        unc_root = str(admin_result.get("unc_root") or unc_root).strip()
        config = self.set_shared_io_config(True, local_root=str(root), unc_root=unc_root, share_name=share_name)
        self.state["shared_io_config"]["role"] = "parent"
        self.state["shared_io_config"]["connect_ok"] = True
        self.state["shared_io_config"]["connect_message"] = "父节点共享目录已创建，已通过一次 UAC 管理员授权完成。"
        self._save_state()
        config = self.shared_io_config()
        config["commands"] = commands
        config["message"] = f"已创建共享目录：{unc_root} -> {root}"
        return config

    def connect_parent_shared_io(self, parent_ip: str, share_name: str = "LocalWebData", unc_root: str = "") -> Dict[str, Any]:
        """子节点加入集群后自动连接父节点共享目录。

        父节点通过 prepare_local_share 创建共享目录；子节点加入集群时调用此函数，
        自动执行 cmdkey/net use，并做一次读写测试。这样不需要用户再手动执行 net use。
        """
        if os.name != "nt":
            raise HTCondorClusterError("共享目录自动连接当前只支持 Windows。")

        parent_ip = str(parent_ip or "").strip()
        share_name = str(share_name or "LocalWebData").strip() or "LocalWebData"
        unc_root = str(unc_root or "").strip()
        if not unc_root:
            if not parent_ip:
                raise HTCondorClusterError("自动连接共享目录需要父节点 IP。")
            unc_root = f"\\\\{parent_ip}\\{share_name}"

        user = str(os.environ.get("LOCAL_WEB_HTCONDOR_SHARE_USER", "")).strip()
        password = str(os.environ.get("LOCAL_WEB_HTCONDOR_SHARE_PASSWORD", "")).strip()
        server = parent_ip or unc_root.strip("\\").split("\\")[0]
        commands: List[Dict[str, Any]] = []

        commands.append({"command": f"net use {unc_root} /delete", **self._run(["net.exe", "use", unc_root, "/delete", "/y"], timeout=30)})
        if server:
            commands.append({"command": f"cmdkey /delete:{server}", **self._run(["cmdkey.exe", f"/delete:{server}"], timeout=30)})

        if user and password and server:
            commands.append({"command": f"cmdkey /add:{server}", **self._run(["cmdkey.exe", f"/add:{server}", f"/user:{user}", f"/pass:{password}"], timeout=30)})

        if user and password:
            use_cmd = ["net.exe", "use", unc_root, f"/user:{user}", password, "/persistent:no"]
        else:
            use_cmd = ["net.exe", "use", unc_root, "/persistent:no"]
        commands.append({"command": f"net use {unc_root}", **self._run(use_cmd, timeout=45)})

        ok = False
        message = ""
        test_path = ""
        try:
            test_dir = Path(unc_root) / "_localweb_share_test"
            test_dir.mkdir(parents=True, exist_ok=True)
            test_file = test_dir / f"child_{socket.gethostname()}_{int(time.time())}.txt"
            test_file.write_text("ok", encoding="utf-8")
            text = test_file.read_text(encoding="utf-8")
            try:
                test_file.unlink(missing_ok=True)
            except Exception:
                pass
            ok = text == "ok"
            test_path = str(test_file)
            message = "子节点已自动连接父节点共享目录并通过读写测试" if ok else "子节点共享目录读写测试异常"
        except Exception as exc:
            ok = False
            message = f"子节点自动连接共享目录失败：{type(exc).__name__}: {exc}"

        self.state["shared_io_config"] = {
            "enabled": bool(ok),
            "local_root": "",
            "unc_root": unc_root,
            "share_name": share_name,
            "role": "child",
            "parent_ip": parent_ip,
            "connect_ok": bool(ok),
            "connect_message": message,
            "updated_at": now_iso(),
        }
        self._save_state()
        return {
            "ok": bool(ok),
            "enabled": bool(ok),
            "unc_root": unc_root,
            "share_name": share_name,
            "parent_ip": parent_ip,
            "message": message,
            "test_path": test_path,
            "commands": commands,
        }

    def test_shared_io(self) -> Dict[str, Any]:
        """测试当前配置的共享目录是否可读写。多共享目录会逐个测试。"""
        cfg = self.shared_io_config()
        shares = cfg.get("shares") if isinstance(cfg.get("shares"), list) else []
        if not shares and cfg.get("unc_root"):
            shares = [cfg]
        if not cfg.get("enabled") or not shares:
            return {"ok": False, "message": "共享目录模式未启用", "config": cfg, "results": []}

        results: List[Dict[str, Any]] = []
        for item in shares:
            unc_root = str(item.get("unc_root") or "").strip()
            share_name = str(item.get("share_name") or "").strip()
            if not unc_root:
                results.append({"ok": False, "share_name": share_name, "message": "共享 UNC 路径为空"})
                continue
            test_dir = Path(unc_root) / "_localweb_share_test"
            test_file = test_dir / f"test_{socket.gethostname()}_{int(time.time())}.txt"
            try:
                test_dir.mkdir(parents=True, exist_ok=True)
                test_file.write_text("ok", encoding="utf-8")
                text = test_file.read_text(encoding="utf-8")
                try:
                    test_file.unlink(missing_ok=True)
                except Exception:
                    pass
                ok = text == "ok"
                results.append({
                    "ok": ok,
                    "share_name": share_name,
                    "unc_root": unc_root,
                    "message": "共享目录读写测试通过" if ok else "共享目录读写测试异常",
                    "test_path": str(test_file),
                })
            except Exception as exc:
                results.append({
                    "ok": False,
                    "share_name": share_name,
                    "unc_root": unc_root,
                    "message": f"共享目录读写测试失败：{type(exc).__name__}: {exc}",
                    "test_path": str(test_file),
                })
        ok = all(bool(x.get("ok")) for x in results)
        return {
            "ok": ok,
            "message": "全部共享目录读写测试通过" if ok else "部分共享目录读写测试失败",
            "config": cfg,
            "results": results,
        }

    def _condor_bin(self) -> Path:
        runtime = get_htcondor_runtime_status()
        bin_dir = str((runtime.get("installed_runtime") or {}).get("bin_dir") or "").strip()
        if bin_dir:
            p = Path(bin_dir)
            if p.is_dir():
                return p

        for raw in [r"C:\Condor\bin", r"C:\condor\bin"]:
            p = Path(raw)
            if p.is_dir():
                return p

        raise HTCondorClusterError("找不到 HTCondor bin 目录，请先完成一键安装。")

    def _exe(self, name: str) -> str:
        path = self._condor_bin() / name
        if not path.is_file():
            raise HTCondorClusterError(f"找不到 HTCondor 命令：{path}")
        return str(path)

    def _run(self, command: List[str], timeout: float = 30) -> Dict[str, Any]:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding=self._win_text_encoding(),
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
        except Exception as exc:
            return {
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _install_result(self) -> Dict[str, Any]:
        return self._load_json(self.install_result_file)

    def _effective_pool_state(self) -> Dict[str, Any]:
        """Read the pool role that HTCondor will actually use.

        The web state file and condor_config.local are written in two separate
        steps. A service health-check failure after the config write must not
        leave the UI showing an old role.
        """
        candidates = []
        configured = str(os.environ.get("CONDOR_CONFIG") or "").strip()
        if configured:
            configured_path = Path(configured)
            candidates.append(
                configured_path.with_name("condor_config.local")
                if configured_path.name.lower() == "condor_config"
                else configured_path
            )
        candidates.extend(
            [
                Path(r"C:\Condor\condor_config.local"),
                Path(r"C:\condor\condor_config.local"),
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "HTCondor" / "condor_config.local",
            ]
        )

        config_path = next((path for path in candidates if path.is_file()), None)
        if config_path is None:
            return {}
        try:
            text = config_path.read_text(encoding="utf-8-sig", errors="replace")
        except Exception:
            return {}

        match = re.search(
            r"(?ms)^\s*# === LOCAL_WEB_HTCONDOR_POOL_START ===\s*$"
            r"(.*?)"
            r"^\s*# === LOCAL_WEB_HTCONDOR_POOL_END ===\s*$",
            text,
        )
        if not match:
            if "Generated by local_web_module_system" in text:
                return {"pool_role": "standalone", "parent_ip": "", "bind_ip": ""}
            return {}

        values: Dict[str, str] = {}
        for raw_line in match.group(1).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip().upper()] = value.strip()

        role = values.get("LOCAL_WEB_POOL_ROLE", "").lower()
        if role not in {"parent", "child"}:
            return {}
        bind_ip = values.get("NETWORK_INTERFACE", "")
        parent_ip = values.get("CONDOR_HOST", "") if role == "child" else bind_ip
        result: Dict[str, Any] = {
            "pool_role": role,
            "parent_ip": parent_ip,
            "bind_ip": bind_ip,
        }
        for source_key, target_key in (("LOWPORT", "low_port"), ("HIGHPORT", "high_port")):
            try:
                result[target_key] = int(values.get(source_key, ""))
            except (TypeError, ValueError):
                pass
        return result

    def _reconcile_pool_state(self) -> bool:
        effective = self._effective_pool_state()
        if not effective:
            return False
        changed = False
        for key in ("pool_role", "parent_ip", "bind_ip", "low_port", "high_port"):
            if key in effective and self.state.get(key) != effective[key]:
                self.state[key] = effective[key]
                changed = True
        if changed:
            self._save_state()
        return changed

    def _queue_summary(self) -> Dict[str, Any]:
        try:
            command = [self._exe("condor_q.exe")]
            role = str(self.state.get("pool_role") or "standalone")
            pool_ip = (
                str(self.state.get("parent_ip") or "")
                if role == "child"
                else str(self.state.get("bind_ip") or "")
            )
            if role in {"parent", "child"} and pool_ip:
                command.extend(["-pool", f"{pool_ip}:9618", "-global"])
            result = self._run(
                command,
                timeout=self.status_query_timeout_seconds,
            )
            return {
                "ok": bool(result.get("ok")),
                "text": "\n".join(x for x in [result.get("stdout", ""), result.get("stderr", "")] if x),
            }
        except Exception as exc:
            return {"ok": False, "text": str(exc)}

    def _slot_status(self) -> Dict[str, Any]:
        try:
            result = self._run(
                [self._exe("condor_status.exe")],
                timeout=self.status_query_timeout_seconds,
            )
            return {
                "ok": bool(result.get("ok")),
                "text": "\n".join(x for x in [result.get("stdout", ""), result.get("stderr", "")] if x),
            }
        except Exception as exc:
            return {"ok": False, "text": str(exc)}

    def _ping_write(self) -> Dict[str, Any]:
        """检查 HTCondor WRITE 权限。

        测试阶段允许两种通过方式：
        1. NTSSPI：Windows 身份认证；
        2. unauthenticated@unmapped：当前系统自动写入的宽松测试模式。

        以前这里只认 NTSSPI，会导致 condor_ping 明明返回 ALLOW，
        页面仍显示“NTSSPI 检查失败”。现在只要 WRITE 被 ALLOW，
        就认为提交权限可用，同时把认证模式返回给前端展示。
        """
        try:
            command = [self._exe("condor_ping.exe")]
            role = str(self.state.get("pool_role") or "standalone")
            pool_ip = (
                str(self.state.get("parent_ip") or "")
                if role == "child"
                else str(self.state.get("bind_ip") or "")
            )
            if role in {"parent", "child"} and pool_ip:
                command.extend(["-pool", f"{pool_ip}:9618", "-type", "COLLECTOR"])
            command.extend(["-table", "WRITE"])
            result = self._run(
                command,
                timeout=self.status_query_timeout_seconds,
            )
            text = "\n".join(
                x for x in [result.get("stdout", ""), result.get("stderr", ""), result.get("error", "")] if x
            )
            upper = text.upper()

            allow = "ALLOW" in upper
            has_ntsspi = "NTSSPI" in upper
            is_unauthenticated = "UNAUTHENTICATED" in upper or "UNMAPPED" in upper
            raw_ok = result.get("returncode") == 0 and allow
            timed_out = "TIMEOUTEXPIRED" in upper or "TIMED OUT AFTER" in upper

            if has_ntsspi:
                auth_mode = "NTSSPI"
                message = "WRITE 权限已允许，认证模式为 NTSSPI。"
            elif is_unauthenticated:
                auth_mode = "unauthenticated"
                message = "WRITE 权限已允许，当前为局域网测试模式 unauthenticated@unmapped。"
            elif allow:
                auth_mode = "unknown"
                message = "WRITE 权限已允许，但认证模式未识别。"
            else:
                auth_mode = "denied"
                message = "WRITE 权限未通过。"

            endpoint = f"{pool_ip or socket.gethostname()}:9618".lower()
            now_monotonic = time.monotonic()
            minimum_timeout_interval = min(10.0, max(2.0, self.status_query_timeout_seconds / 2.0))
            with self._write_health_lock:
                if self._write_health.get("endpoint") != endpoint:
                    self._write_health.update({
                        "endpoint": endpoint,
                        "state": "unknown",
                        "consecutive_timeouts": 0,
                        "last_timeout_monotonic": 0.0,
                        "last_success_at": "",
                        "last_auth_mode": "",
                    })

                if raw_ok:
                    self._write_health.update({
                        "state": "allowed",
                        "consecutive_timeouts": 0,
                        "last_timeout_monotonic": 0.0,
                        "last_success_at": now_iso(),
                        "last_auth_mode": auth_mode,
                    })
                    ok = True
                    pending = False
                elif timed_out:
                    last_timeout = float(self._write_health.get("last_timeout_monotonic") or 0.0)
                    if not last_timeout or now_monotonic - last_timeout >= minimum_timeout_interval:
                        self._write_health["consecutive_timeouts"] = int(
                            self._write_health.get("consecutive_timeouts") or 0
                        ) + 1
                        self._write_health["last_timeout_monotonic"] = now_monotonic
                    timeout_count = int(self._write_health.get("consecutive_timeouts") or 0)
                    pending = timeout_count < 2
                    ok = bool(pending and self._write_health.get("state") == "allowed")
                    if pending:
                        auth_mode = str(self._write_health.get("last_auth_mode") or "checking")
                        message = (
                            "WRITE 权限本轮检查超时，正在再次确认；"
                            + ("暂时沿用最后一次通过结果。" if ok else "暂不判定为权限失败。")
                        )
                    else:
                        self._write_health["state"] = "timeout"
                        auth_mode = "timeout"
                        message = "WRITE 权限连续两次检查超时，暂时标记为不可用。"
                else:
                    self._write_health.update({
                        "state": "denied",
                        "consecutive_timeouts": 0,
                        "last_timeout_monotonic": 0.0,
                    })
                    ok = False
                    pending = False

                consecutive_timeouts = int(self._write_health.get("consecutive_timeouts") or 0)
                last_success_at = str(self._write_health.get("last_success_at") or "")

            return {
                "ok": ok,
                "text": text,
                "returncode": result.get("returncode"),
                "auth_mode": auth_mode,
                "has_ntsspi": has_ntsspi,
                "allow": ok,
                "raw_allow": allow,
                "pending": pending,
                "timed_out": timed_out,
                "consecutive_timeouts": consecutive_timeouts,
                "last_success_at": last_success_at,
                "message": message,
            }
        except Exception as exc:
            return {
                "ok": False,
                "text": str(exc),
                "returncode": None,
                "auth_mode": "error",
                "has_ntsspi": False,
                "allow": False,
                "raw_allow": False,
                "pending": False,
                "timed_out": False,
                "consecutive_timeouts": 0,
                "last_success_at": "",
                "message": f"{type(exc).__name__}: {exc}",
            }

    def status(self) -> Dict[str, Any]:
        self._reconcile_pool_state()
        runtime = get_htcondor_runtime_status()
        install_result = self._install_result()
        service_state = ((runtime.get("installed_runtime") or {}).get("service") or {}).get("state")
        install_ok = bool(install_result.get("success") and install_result.get("status") == "fully_validated")
        service_ok = service_state == "running"
        if service_ok:
            # All three commands contact the same remote collector on a child
            # node. Running them serially triples any DNS/network delay.
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="htcondor-status") as executor:
                nodes_future = executor.submit(self.node_status)
                ping_future = executor.submit(self._ping_write)
                queue_future = executor.submit(self._queue_summary)
                nodes = nodes_future.result()
                ping = ping_future.result()
                queue = queue_future.result()
            slot = {"ok": bool(nodes.get("ok")), "text": str(nodes.get("text") or "")}
        else:
            offline = {"ok": False, "text": "Condor 服务未运行"}
            nodes = {**offline, "items": []}
            ping = offline
            queue = offline
        mode = str(self.state.get("execution_mode") or "local")
        local_machine = socket.gethostname()
        pool_role = str(self.state.get("pool_role") or "standalone")
        parent_ip = str(self.state.get("parent_ip") or "")
        collector_port = int(self.state.get("collector_port") or 9618)
        visible_machines = {
            str(item.get("machine") or "").strip().lower()
            for item in (nodes.get("items") or [])
            if isinstance(item, dict)
        }
        local_machine_visible = local_machine.strip().lower() in visible_machines

        parent_connection: Dict[str, Any] = {
            "applicable": pool_role == "child",
            "status": "not_applicable",
            "connected": False,
            "reconnecting": False,
            "parent_ip": parent_ip,
            "collector_port": collector_port,
            "local_machine_visible": local_machine_visible,
            "checked_at": now_iso(),
            "consecutive_failures": 0,
            "failure_threshold": self.parent_failure_threshold,
            "last_success_at": "",
            "reason": "",
            "message": "",
        }
        if pool_role == "child":
            diagnostic_text = "\n".join(
                str(item.get("text") or "")
                for item in (nodes, queue, ping)
                if isinstance(item, dict) and item.get("text")
            )
            reason = next((line.strip() for line in diagnostic_text.splitlines() if line.strip()), "父节点暂时无响应")
            if not nodes.get("ok"):
                # Individual Condor commands can alternate between timeout,
                # communication and authentication wording for the same
                # outage. Keep the user-facing state stable; raw diagnostics
                # remain available in nodes/queue/ping.
                reason = "父节点无响应、网络不可达或连接认证失败（详细错误见右侧诊断）"
            endpoint = f"{parent_ip or '未配置'}:{collector_port}"
            endpoint_key = endpoint.lower()
            if not service_ok:
                parent_connection.update({
                    "status": "disconnected",
                    "reason": "本机 Condor 服务未运行",
                    "message": f"与父节点 {endpoint} 的连接已断开；本机 Condor 服务未运行，当前无法接收分布式任务。",
                })
            elif not parent_ip:
                parent_connection.update({
                    "status": "disconnected",
                    "reason": "尚未配置父节点 IP",
                    "message": "子节点配置不完整：尚未配置父节点 IP。",
                })
            elif nodes.get("ok") and local_machine_visible:
                with self._parent_health_lock:
                    if self._parent_health.get("endpoint") != endpoint_key:
                        self._parent_health.update({
                            "endpoint": endpoint_key,
                            "consecutive_failures": 0,
                            "last_failure_monotonic": 0.0,
                            "last_success_at": "",
                        })
                    self._parent_health.update({
                        "consecutive_failures": 0,
                        "last_failure_monotonic": 0.0,
                        "last_success_at": now_iso(),
                    })
                    last_success_at = str(self._parent_health.get("last_success_at") or "")
                parent_connection.update({
                    "status": "connected",
                    "connected": True,
                    "last_success_at": last_success_at,
                    "reason": "父节点可访问，且本机已注册到集群",
                    "message": f"已连接父节点 {endpoint}，本机已注册并可接收分布式任务。",
                })
            elif nodes.get("ok"):
                with self._parent_health_lock:
                    if self._parent_health.get("endpoint") != endpoint_key:
                        self._parent_health.update({
                            "endpoint": endpoint_key,
                            "consecutive_failures": 0,
                            "last_failure_monotonic": 0.0,
                            "last_success_at": "",
                        })
                    self._parent_health.update({
                        "consecutive_failures": 0,
                        "last_failure_monotonic": 0.0,
                    })
                    last_success_at = str(self._parent_health.get("last_success_at") or "")
                parent_connection.update({
                    "status": "degraded",
                    "reconnecting": True,
                    "last_success_at": last_success_at,
                    "reason": "父节点可访问，但本机尚未出现在执行节点列表中",
                    "message": f"父节点 {endpoint} 可访问，但本机尚未注册到集群；当前无法接收分布式任务，系统将继续自动重连。",
                })
            elif queue.get("ok") or ping.get("ok"):
                # At least one authenticated Condor command reached the
                # collector. A slow node-list query alone must not be treated
                # as a disconnected parent.
                with self._parent_health_lock:
                    if self._parent_health.get("endpoint") != endpoint_key:
                        self._parent_health.update({
                            "endpoint": endpoint_key,
                            "consecutive_failures": 0,
                            "last_failure_monotonic": 0.0,
                            "last_success_at": "",
                        })
                    self._parent_health.update({
                        "consecutive_failures": 0,
                        "last_failure_monotonic": 0.0,
                    })
                    last_success_at = str(self._parent_health.get("last_success_at") or "")
                parent_connection.update({
                    "status": "checking",
                    "last_success_at": last_success_at,
                    "reason": "父节点已有响应，正在等待执行节点列表",
                    "message": f"父节点 {endpoint} 已有响应，正在确认本机节点注册状态；暂不判定为断开。",
                })
            else:
                now_monotonic = time.monotonic()
                minimum_failure_interval = min(10.0, max(2.0, self.status_query_timeout_seconds / 2.0))
                with self._parent_health_lock:
                    if self._parent_health.get("endpoint") != endpoint_key:
                        self._parent_health.update({
                            "endpoint": endpoint_key,
                            "consecutive_failures": 0,
                            "last_failure_monotonic": 0.0,
                            "last_success_at": "",
                        })
                    last_failure = float(self._parent_health.get("last_failure_monotonic") or 0.0)
                    if not last_failure or now_monotonic - last_failure >= minimum_failure_interval:
                        self._parent_health["consecutive_failures"] = int(
                            self._parent_health.get("consecutive_failures") or 0
                        ) + 1
                        self._parent_health["last_failure_monotonic"] = now_monotonic
                    consecutive_failures = int(self._parent_health.get("consecutive_failures") or 0)
                    last_success_at = str(self._parent_health.get("last_success_at") or "")

                parent_connection.update({
                    "consecutive_failures": consecutive_failures,
                    "last_success_at": last_success_at,
                    "reason": reason,
                })
                if consecutive_failures < self.parent_failure_threshold:
                    parent_connection.update({
                        "status": "checking",
                        "message": (
                            f"父节点 {endpoint} 本轮响应超时，正在再次确认连接状态；"
                            f"连续 {self.parent_failure_threshold} 次失败后才会判定为断开。"
                        ),
                    })
                else:
                    parent_connection.update({
                        "status": "disconnected",
                        "reconnecting": True,
                        "message": f"与父节点 {endpoint} 的连接已断开；当前无法接收分布式任务，系统将继续自动重连。",
                    })

        enabled = bool(mode == "htcondor" and install_ok and service_ok and slot.get("ok") and ping.get("ok"))
        return {
            "backend": "htcondor",
            "execution_mode": mode,
            "enabled": enabled,
            "machine": local_machine,
            "local_ips": self._local_ipv4_list(),
            "pool_role": pool_role,
            "parent_ip": parent_ip,
            "bind_ip": str(self.state.get("bind_ip") or ""),
            "collector_port": collector_port,
            "low_port": int(self.state.get("low_port") or 9700),
            "high_port": int(self.state.get("high_port") or 9800),
            "state_file": str(self.state_file),
            "runtime_dir": str(self.runtime_dir),
            "shared_io": self.shared_io_config(),
            "install_result": install_result,
            "runtime": runtime,
            "service_running": service_ok,
            "install_validated": install_ok,
            "slot_status": slot,
            "nodes": nodes,
            "queue": queue,
            "ping": ping,
            "parent_connection": parent_connection,
            "message": (
                "HTCondor 可用于任务执行"
                if enabled
                else (parent_connection.get("message") or "HTCondor 未启用或未完全通过检查")
            ),
        }

    def distributed_execution_requested(self) -> bool:
        return str(self.state.get("execution_mode") or "local") == "htcondor"

    def distributed_execution_enabled(self) -> bool:
        data = self.status()
        return bool(data.get("enabled"))

    def set_execution_mode(self, mode: str) -> Dict[str, Any]:
        mode = str(mode or "local").strip().lower()
        if mode not in {"local", "htcondor"}:
            raise HTCondorClusterError("HTCondor 执行模式只能是 local 或 htcondor")

        if mode == "htcondor":
            data = self.status()
            if not (data.get("install_validated") and data.get("service_running") and data.get("ping", {}).get("ok")):
                raise HTCondorClusterError("HTCondor 尚未通过安装、自检或 WRITE 权限检查，不能启用。")

        self.state["execution_mode"] = mode
        self._save_state()
        data = self.status()
        data["message"] = "已启用 HTCondor 执行" if mode == "htcondor" else "已切回本机执行"
        return data


    def _local_ipv4_list(self) -> List[str]:
        """取本机常用 IPv4，给前端下拉和自动填充用。"""
        items: List[str] = []
        try:
            name = socket.gethostname()
            for item in socket.getaddrinfo(name, None, socket.AF_INET):
                ip = str(item[4][0])
                if ip and not ip.startswith("127.") and ip not in items:
                    items.append(ip)
        except Exception:
            pass

        # socket.getaddrinfo 有时候只拿到 127.0.0.1，这里再用 ipconfig 简单兜底。
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["ipconfig"],
                    capture_output=True,
                    text=True,
                    encoding=self._win_text_encoding(),
                    errors="replace",
                    timeout=8,
                    check=False,
                )
                for ip in re.findall(r"IPv4[^:\r\n]*[:：]\s*([0-9]+(?:\.[0-9]+){3})", result.stdout or ""):
                    if ip and not ip.startswith("127.") and ip not in items:
                        items.append(ip)
            except Exception:
                pass
        return items

    def _lan_allow_pattern(self, ip: str) -> str:
        """把 192.168.2.136 变成 192.168.2.*。

        HTCondor 的父节点和子节点只需要在同一个小局域网内互相通信。
        这里不做复杂网段计算，先按教学/实验环境常见的 C 段处理，代码更容易看懂。
        """
        ip = str(ip or "").strip()
        try:
            obj = ipaddress.ip_address(ip)
            if obj.version != 4:
                return ""
        except Exception:
            return ""

        parts = ip.split(".")
        if len(parts) != 4:
            return ""
        return f"{parts[0]}.{parts[1]}.{parts[2]}.*"

    def _same_lan_segment(self, ip_a: str, ip_b: str) -> bool:
        """简单判断两个 IPv4 是否属于同一个前三段网段。"""
        pattern_a = self._lan_allow_pattern(ip_a)
        pattern_b = self._lan_allow_pattern(ip_b)
        return bool(pattern_a and pattern_b and pattern_a == pattern_b)

    def _build_allow_hosts(self, bind_ip: str, parent_ip: str = "") -> str:
        """生成 HTCondor 的 ALLOW_* 主机范围。

        修复点：
        子节点 startd 会向父节点 collector 上报自己。
        如果父节点没有放行 ALLOW_ADVERTISE_STARTD，就会出现 DENIED。
        """
        hosts: List[str] = ["127.0.0.1", "localhost"]
        for ip in [bind_ip, parent_ip]:
            ip = str(ip or "").strip()
            if not ip:
                continue
            if ip not in hosts:
                hosts.append(ip)
            pattern = self._lan_allow_pattern(ip)
            if pattern and pattern not in hosts:
                hosts.append(pattern)
        return ", ".join(hosts)

    def _pick_bind_ip(self, requested_ip: str = "", parent_ip: str = "") -> str:
        """选择并检查本机绑定 IP。

        这个函数用来避免用户把子节点绑定到错误网卡，
        例如父节点是 192.168.2.136，却把子节点填成 192.168.43.129。
        """
        requested_ip = str(requested_ip or "").strip()
        parent_ip = str(parent_ip or "").strip()
        local_ips = self._local_ipv4_list()

        if requested_ip:
            if local_ips and requested_ip not in local_ips:
                raise HTCondorClusterError(
                    f"本机绑定 IP {requested_ip} 不是当前电脑检测到的可用 IP。"
                    f"当前可用 IP：{', '.join(local_ips)}"
                )
            if parent_ip and not self._same_lan_segment(requested_ip, parent_ip):
                suggestion = ""
                for ip in local_ips:
                    if self._same_lan_segment(ip, parent_ip):
                        suggestion = ip
                        break
                if suggestion:
                    raise HTCondorClusterError(
                        f"本机绑定 IP {requested_ip} 与父节点 {parent_ip} 不在同一网段，"
                        f"建议改用 {suggestion}。"
                    )
                raise HTCondorClusterError(
                    f"本机绑定 IP {requested_ip} 与父节点 {parent_ip} 不在同一网段。"
                )
            return requested_ip

        if parent_ip:
            for ip in local_ips:
                if self._same_lan_segment(ip, parent_ip):
                    return ip
            raise HTCondorClusterError(
                f"没有找到与父节点 {parent_ip} 同网段的本机 IP。"
                f"当前可用 IP：{', '.join(local_ips)}"
            )

        for ip in local_ips:
            if ip.startswith("192.168."):
                return ip
        return local_ips[0] if local_ips else "127.0.0.1"

    def _ps_quote(self, value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def _clean_condor_text(self, text: str) -> str:
        """清理 condor_rm / condor_q 的空输出，让任务日志不再出现 condor_rm.exe : 这类脏行。"""
        lines = []
        for line in str(text or "").replace("\r", "\n").split("\n"):
            raw = line.strip()
            if not raw:
                continue
            low = raw.lower().strip()
            if low in {"condor_rm.exe :", "condor_q.exe :", "condor_rm:", "condor_q:"}:
                continue
            if re.fullmatch(r"condor_(rm|q)(\.exe)?\s*:\s*", low):
                continue
            lines.append(raw)
        return "\n".join(lines).strip()

    def _run_elevated_ps(self, name: str, script_text: str, timeout: int = 180) -> Dict[str, Any]:
        """运行需要管理员权限的 PowerShell 脚本。

        用户不需要手动复制命令；系统只弹出一次 UAC 窗口。
        """
        if os.name != "nt":
            return {"success": False, "message": "HTCondor 集群配置目前只支持 Windows。"}

        safe_name = re.sub(r"[^0-9A-Za-z_.-]+", "_", name or "cluster")
        run_dir = self.runtime_dir / "cluster_admin"
        run_dir.mkdir(parents=True, exist_ok=True)
        script_path = run_dir / f"{safe_name}.ps1"
        result_path = run_dir / f"{safe_name}_result.json"
        launcher_path = run_dir / f"{safe_name}_launcher.ps1"
        result_path.unlink(missing_ok=True)

        script_path.write_text(script_text, encoding="utf-8-sig")
        launcher_text = f"""
$ErrorActionPreference = 'Stop'
$target = {self._ps_quote(str(script_path))}
Start-Process -FilePath powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$target) -Verb RunAs -Wait
"""
        launcher_path.write_text(launcher_text, encoding="utf-8-sig")

        powershell = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        )
        completed = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher_path)],
            capture_output=True,
            text=True,
            encoding=self._win_text_encoding(),
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )

        if result_path.is_file():
            try:
                data = json.loads(result_path.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    data.setdefault("launcher_stdout", completed.stdout.strip())
                    data.setdefault("launcher_stderr", completed.stderr.strip())
                    return data
            except Exception as exc:
                return {
                    "success": False,
                    "message": f"读取管理员脚本结果失败：{type(exc).__name__}: {exc}",
                    "launcher_stdout": completed.stdout.strip(),
                    "launcher_stderr": completed.stderr.strip(),
                }

        return {
            "success": False,
            "message": "管理员脚本没有返回结果。可能是用户取消了 UAC 授权，或者脚本被系统拦截。",
            "launcher_stdout": completed.stdout.strip(),
            "launcher_stderr": completed.stderr.strip(),
            "returncode": completed.returncode,
        }

    def _make_pool_config_script(
        self,
        role: str,
        parent_ip: str = "",
        bind_ip: str = "",
        low_port: int = 9700,
        high_port: int = 9800,
    ) -> str:
        role = str(role or "standalone").strip().lower()
        parent_ip = str(parent_ip or "").strip()
        bind_ip = str(bind_ip or "").strip()
        low_port = int(low_port or 9700)
        high_port = int(high_port or 9800)
        machine = socket.gethostname().lower()
        allow_hosts = self._build_allow_hosts(bind_ip=bind_ip, parent_ip=parent_ip or bind_ip)

        if role == "parent":
            daemon_list = "MASTER, COLLECTOR, NEGOTIATOR, SCHEDD, STARTD"
            condor_host = bind_ip
            collector_host = f"{bind_ip}:9618"
            role_lines = "use ROLE: CentralManager\r\nuse ROLE: Submit\r\nuse ROLE: Execute"
        elif role == "child":
            daemon_list = "MASTER, STARTD"
            condor_host = parent_ip
            collector_host = f"{parent_ip}:9618"
            role_lines = "use ROLE: Execute"
        else:
            daemon_list = ""
            condor_host = ""
            collector_host = ""
            role_lines = ""

        if role in {"parent", "child"}:
            health_pool_ip = parent_ip if role == "child" else bind_ip
            status_health_arguments = f"-pool {health_pool_ip}:9618 -af Name Machine State Activity"
            ping_health_arguments = f"-pool {health_pool_ip}:9618 -type COLLECTOR -table WRITE"
        else:
            status_health_arguments = "-af Name Machine State Activity"
            ping_health_arguments = "-table WRITE"

        if role in {"parent", "child"}:
            block = f"""# === LOCAL_WEB_HTCONDOR_POOL_START ===
# 由 local_web_module_system 自动生成，不要手动改这一段
LOCAL_WEB_POOL_ROLE = {role}
{role_lines}
DAEMON_LIST = {daemon_list}
CONDOR_HOST = {condor_host}
COLLECTOR_HOST = {collector_host}
NETWORK_INTERFACE = {bind_ip}
UID_DOMAIN = {machine}
FILESYSTEM_DOMAIN = {machine}
# 下面这段是集群通信权限，课堂/局域网测试使用。
# 如果没有 ALLOW_ADVERTISE_STARTD，子节点会启动成功但父节点会 DENIED。
LOCAL_WEB_ALLOW_HOSTS = {allow_hosts}
ALLOW_READ = {allow_hosts}
ALLOW_WRITE = {allow_hosts}
ALLOW_DAEMON = {allow_hosts}
ALLOW_CLIENT = {allow_hosts}
ALLOW_ADMINISTRATOR = {allow_hosts}
ALLOW_ADVERTISE_MASTER = {allow_hosts}
ALLOW_ADVERTISE_STARTD = {allow_hosts}
ALLOW_ADVERTISE_SCHEDD = {allow_hosts}

SEC_DEFAULT_AUTHENTICATION = OPTIONAL
SEC_READ_AUTHENTICATION = OPTIONAL
SEC_WRITE_AUTHENTICATION = OPTIONAL
SEC_DAEMON_AUTHENTICATION = OPTIONAL
SEC_CLIENT_AUTHENTICATION = OPTIONAL
SEC_ADMINISTRATOR_AUTHENTICATION = OPTIONAL
SEC_ADVERTISE_MASTER_AUTHENTICATION = OPTIONAL
SEC_ADVERTISE_STARTD_AUTHENTICATION = OPTIONAL
SEC_ADVERTISE_SCHEDD_AUTHENTICATION = OPTIONAL
SEC_DEFAULT_ENCRYPTION = OPTIONAL
SEC_DEFAULT_INTEGRITY = OPTIONAL
SEC_CLIENT_AUTHENTICATION_METHODS = NTSSPI, CLAIMTOBE
QUEUE_SUPER_USERS = SYSTEM, condor, LocalWebCondor
ALLOW_SUBMIT_FROM_KNOWN_USERS_ONLY = FALSE
LOWPORT = {low_port}
HIGHPORT = {high_port}
START = TRUE
SUSPEND = FALSE
PREEMPT = FALSE
KILL = FALSE
# === LOCAL_WEB_HTCONDOR_POOL_END ==="""
        else:
            block = ""

        restart_condor_script = r"""
$serviceName = 'Condor'
$warnings = New-Object System.Collections.Generic.List[string]

function Add-WarningText {
    param([string]$Text)
    if (-not [string]::IsNullOrWhiteSpace($Text)) {
        $warnings.Add($Text) | Out-Null
    }
}

function Wait-CondorServiceState {
    param(
        [string]$Wanted,
        [int]$Seconds
    )
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($null -eq $svc) {
            return $false
        }
        if ([string]$svc.Status -eq $Wanted) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Find-CondorExe {
    param([string]$Name)
    $candidates = @(
        (Join-Path 'C:/Condor/bin' $Name),
        (Join-Path 'C:/condor/bin' $Name),
        (Join-Path (Join-Path $env:ProgramFiles 'HTCondor\bin') $Name),
        (Join-Path (Join-Path $env:ProgramFiles 'Condor\bin') $Name)
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }
    return ''
}

function Invoke-ExternalWithTimeout {
    param(
        [string]$FilePath,
        [string]$Arguments,
        [int]$Seconds
    )
    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        Add-WarningText "找不到命令：$FilePath"
        return $false
    }
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $FilePath
        $psi.Arguments = $Arguments
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $proc = New-Object System.Diagnostics.Process
        $proc.StartInfo = $psi
        [void]$proc.Start()
        if (-not $proc.WaitForExit($Seconds * 1000)) {
            try { $proc.Kill() } catch {}
            Add-WarningText "$FilePath $Arguments 执行超过 ${Seconds}s，已跳过等待。"
            return $false
        }
        $out = $proc.StandardOutput.ReadToEnd()
        $err = $proc.StandardError.ReadToEnd()
        if ($proc.ExitCode -ne 0) {
            Add-WarningText "$FilePath $Arguments 返回码=$($proc.ExitCode)：$out $err"
            return $false
        }
        return $true
    } catch {
        Add-WarningText "$FilePath $Arguments 执行异常：$($_.Exception.Message)"
        return $false
    }
}

try {
    $svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        throw '找不到 Condor 服务。'
    }

    # NETWORK_INTERFACE / CONDOR_HOST 改变后，condor_reconfig 与
    # condor_restart -master 不能可靠地重建 master 的继承地址。
    # 旧 master 可能继续把原 IP 传给 shared_port/startd，表现为
    # 9618 已监听，但 condor_status / condor_ping 永久等待。
    # 因此集群角色或绑定 IP 改动后必须完整停止并启动 Windows 服务。
    $oldMasterPid = 0
    try {
        $oldMasterPid = [int](Get-CimInstance Win32_Service -Filter "Name='Condor'" -ErrorAction Stop).ProcessId
    } catch {}

    # 清理网页健康检查留下的挂起客户端，避免旧连接干扰重启验证。
    Get-Process -Name condor_status,condor_ping,condor_q -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue

    $scExe = Join-Path $env:SystemRoot 'System32\sc.exe'
    $taskkillExe = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    $svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

    if ($svc.Status -ne 'Stopped') {
        [void](Invoke-ExternalWithTimeout -FilePath $scExe -Arguments 'stop Condor' -Seconds 12)

        if (-not (Wait-CondorServiceState -Wanted 'Stopped' -Seconds 15)) {
            $servicePid = 0
            try {
                $servicePid = [int](Get-CimInstance Win32_Service -Filter "Name='Condor'" -ErrorAction Stop).ProcessId
            } catch {}

            if ($servicePid -gt 0) {
                [void](Invoke-ExternalWithTimeout -FilePath $taskkillExe -Arguments "/PID $servicePid /T /F" -Seconds 15)
            }

            if (-not (Wait-CondorServiceState -Wanted 'Stopped' -Seconds 20)) {
                $current = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
                $statusText = if ($null -eq $current) { 'missing' } else { [string]$current.Status }
                throw "Condor 服务停止超时，当前状态：$statusText。"
            }
        }
    }

    # 服务停止后删除动态地址文件，防止客户端在新 master 建立前读取旧 IP。
    $staleAddressFiles = @(
        'C:/Condor/log/.master_address',
        'C:/Condor/log/.collector_address',
        'C:/Condor/log/.startd_address',
        'C:/Condor/log/shared_port_ad',
        'C:/Condor/spool/.schedd_address'
    )
    foreach ($path in $staleAddressFiles) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }

    Start-Service -Name $serviceName -ErrorAction Stop
    if (-not (Wait-CondorServiceState -Wanted 'Running' -Seconds 45)) {
        throw 'Condor 服务启动超时。'
    }
    Start-Sleep -Seconds 6

    $newMasterPid = 0
    try {
        $newMasterPid = [int](Get-CimInstance Win32_Service -Filter "Name='Condor'" -ErrorAction Stop).ProcessId
    } catch {}
    if ($oldMasterPid -gt 0 -and $newMasterPid -eq $oldMasterPid) {
        throw "Condor master 没有真正重启，PID 仍为 $newMasterPid。"
    }

    $statusExe = Find-CondorExe 'condor_status.exe'
    if (-not (Invoke-ExternalWithTimeout -FilePath $statusExe -Arguments '__LOCAL_WEB_STATUS_ARGUMENTS__' -Seconds 30)) {
        throw 'Condor 服务已启动，但 condor_status 健康检查失败。'
    }

    $pingExe = Find-CondorExe 'condor_ping.exe'
    if (-not (Invoke-ExternalWithTimeout -FilePath $pingExe -Arguments '__LOCAL_WEB_PING_ARGUMENTS__' -Seconds 30)) {
        throw 'Condor 服务已启动，但 WRITE 权限健康检查失败。'
    }
} catch {
    $warningText = ($warnings -join '; ')
    if ($warningText) {
        throw "刷新 Condor 服务失败：$($_.Exception.Message)。附加信息：$warningText"
    }
    throw "刷新 Condor 服务失败：$($_.Exception.Message)"
}
""".strip()
        restart_condor_script = restart_condor_script.replace(
            "__LOCAL_WEB_STATUS_ARGUMENTS__", status_health_arguments
        ).replace("__LOCAL_WEB_PING_ARGUMENTS__", ping_health_arguments)

        block_b64 = base64.b64encode(block.encode("utf-8")).decode("ascii")
        role_text = role
        return f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$resultPath = {self._ps_quote(str(self.runtime_dir / 'cluster_admin' / f'{role_text}_result.json'))}
$result = @{{ success = $false; config_applied = $false; message = ''; role = {self._ps_quote(role_text)}; stdout = ''; stderr = '' }}
try {{
    $cfg = 'C:/Condor/condor_config.local'
    if (-not (Test-Path -LiteralPath $cfg -PathType Leaf)) {{
        throw "找不到 HTCondor 配置文件：$cfg。请先完成一键安装。"
    }}

    $oldText = Get-Content -LiteralPath $cfg -Raw -ErrorAction SilentlyContinue
    if ($null -eq $oldText) {{ $oldText = '' }}
    $pattern = '(?s)\r?\n?# === LOCAL_WEB_HTCONDOR_POOL_START ===.*?# === LOCAL_WEB_HTCONDOR_POOL_END ===\r?\n?'
    $cleanText = [regex]::Replace($oldText, $pattern, "`r`n")

    $backup = $cfg + '.before_localweb_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
    Copy-Item -LiteralPath $cfg -Destination $backup -Force

    $block = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({self._ps_quote(block_b64)}))
    if ([string]::IsNullOrWhiteSpace($block)) {{
        $newText = $cleanText.TrimEnd() + "`r`n"
    }}
    else {{
        $newText = $cleanText.TrimEnd() + "`r`n`r`n" + $block + "`r`n"
    }}
    Set-Content -LiteralPath $cfg -Value $newText -Encoding ASCII
    $result.config_applied = $true

    New-NetFirewallRule -DisplayName 'LocalWeb-HTCondor-Collector-9618' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 9618 -Profile Any -ErrorAction SilentlyContinue | Out-Null
    New-NetFirewallRule -DisplayName 'LocalWeb-HTCondor-Dynamic-{low_port}-{high_port}' -Direction Inbound -Action Allow -Protocol TCP -LocalPort {low_port}-{high_port} -Profile Any -ErrorAction SilentlyContinue | Out-Null

{restart_condor_script}

    $statusText = 'condor_status 与 condor_ping WRITE 健康检查均已通过。'


    $result.success = $true
    $result.message = 'HTCondor 集群配置已写入，Condor 服务已重启。'
    $result.stdout = $statusText
    $result.config_file = $cfg
    $result.backup_file = $backup
}}
catch {{
    $result.success = $false
    $result.message = $_.Exception.Message
    $result.stderr = $_.Exception.ToString()
}}
finally {{
    $result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $resultPath -Encoding UTF8
}}
"""

    def _query_pool_nodes(self, pool_ip: str = "") -> Dict[str, Any]:
        """查询指定父节点池中的执行节点。"""
        args = [self._exe("condor_status.exe")]
        if pool_ip:
            args.extend(["-pool", f"{pool_ip}:9618"])
        args.extend(["-af", "Name", "Machine", "State", "Activity", "Cpus", "Memory", "SlotType", "PartitionableSlot"])
        result = self._run(args, timeout=self.status_query_timeout_seconds)
        text = "\n".join(x for x in [result.get("stdout", ""), result.get("stderr", ""), result.get("error", "")] if x).strip()
        items = []
        # condor_status may print authentication/communication errors using
        # whitespace-separated words.  Do not mistake those messages for a
        # valid slot row, otherwise the UI oscillates between 0 and 1 nodes.
        valid_states = {"owner", "unclaimed", "matched", "claimed", "preempting", "drained", "backfill"}
        valid_activities = {"idle", "busy", "retiring", "vacating", "suspended", "benchmarking", "killing"}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) < 6:
                continue
            if parts[2].lower() not in valid_states or parts[3].lower() not in valid_activities:
                continue
            try:
                float(parts[4])
                float(parts[5])
            except (TypeError, ValueError):
                continue
            items.append({
                "name": parts[0],
                "machine": parts[1],
                "state": parts[2],
                "activity": parts[3],
                "cpus": parts[4],
                "memory": parts[5],
                "slot_type": parts[6] if len(parts) >= 7 else "",
                "partitionable": str(parts[7]).lower() == "true" if len(parts) >= 8 else False,
            })
        return {"ok": bool(result.get("ok")), "text": text, "items": items}

    def _wait_machine_visible_in_pool(self, pool_ip: str, machine: str, timeout_seconds: int = 60) -> Dict[str, Any]:
        """等待父节点 condor_status 里出现当前子节点。

        这样可以避免“页面提示加入成功，但父节点实际只有 1 个节点”的假成功。
        """
        machine = str(machine or "").strip().lower()
        deadline = time.time() + max(5, int(timeout_seconds or 60))
        last = {"ok": False, "text": "", "items": []}
        while time.time() < deadline:
            last = self._query_pool_nodes(pool_ip)
            text = str(last.get("text") or "").lower()
            if machine and machine in text:
                last["verified"] = True
                return last
            time.sleep(3)
        last["verified"] = False
        return last

    def node_status(self) -> Dict[str, Any]:
        """返回当前 HTCondor 池里的执行节点。"""
        try:
            return self._query_pool_nodes()
        except Exception as exc:
            return {"ok": False, "text": str(exc), "items": []}

    def create_parent_node(self, bind_ip: str = "", low_port: int = 9700, high_port: int = 9800) -> Dict[str, Any]:
        bind_ip = self._pick_bind_ip(bind_ip)
        script = self._make_pool_config_script(
            role="parent",
            bind_ip=bind_ip,
            parent_ip=bind_ip,
            low_port=low_port,
            high_port=high_port,
        )
        result = self._run_elevated_ps("parent", script, timeout=240)
        if result.get("success") or result.get("config_applied"):
            self.state["pool_role"] = "parent"
            self.state["parent_ip"] = bind_ip
            self.state["bind_ip"] = bind_ip
            self.state["collector_port"] = 9618
            self.state["low_port"] = int(low_port or 9700)
            self.state["high_port"] = int(high_port or 9800)
            self._save_state()
        data = self.status()
        data["action_result"] = result
        data["message"] = result.get("message") or ("父节点创建完成" if result.get("success") else "父节点创建失败")
        return data

    def join_parent_node(
        self,
        parent_ip: str,
        child_ip: str = "",
        low_port: int = 9700,
        high_port: int = 9800,
        auto_shared_io: bool = True,
        share_name: str = "LocalWebData",
        shared_unc_root: str = "",
    ) -> Dict[str, Any]:
        parent_ip = str(parent_ip or "").strip()
        if not parent_ip:
            raise HTCondorClusterError("请填写父节点 IP。")
        child_ip = self._pick_bind_ip(child_ip, parent_ip=parent_ip)
        script = self._make_pool_config_script(
            role="child",
            bind_ip=child_ip,
            parent_ip=parent_ip,
            low_port=low_port,
            high_port=high_port,
        )
        result = self._run_elevated_ps("child", script, timeout=240)
        if result.get("success") or result.get("config_applied"):
            self.state["pool_role"] = "child"
            self.state["parent_ip"] = parent_ip
            self.state["bind_ip"] = child_ip
            self.state["collector_port"] = 9618
            self.state["low_port"] = int(low_port or 9700)
            self.state["high_port"] = int(high_port or 9800)
            self._save_state()

            verify = self._wait_machine_visible_in_pool(parent_ip, socket.gethostname(), timeout_seconds=60)
            result["verify"] = verify
            if verify.get("verified"):
                result["success"] = True
                result["message"] = "子节点已成功加入父节点，父节点已经能看到本机执行节点。"
            else:
                result["success"] = False
                result["message"] = (
                    "子节点配置已写入，但父节点还没有看到本机执行节点。"
                    "常见原因是父节点安全配置没有放行 ALLOW_ADVERTISE_STARTD，"
                    "或父节点 Condor 服务还没有完成重载。"
                )

            # 子节点加入集群后，自动连接父节点共享目录。
            # 连接失败不回滚 HTCondor 加入动作，但会把失败原因返回到前端。
            if auto_shared_io:
                try:
                    shared_result = self.connect_parent_shared_io(
                        parent_ip=parent_ip,
                        share_name=share_name,
                        unc_root=shared_unc_root,
                    )
                    result["shared_io"] = shared_result
                    if shared_result.get("ok"):
                        result["message"] = (result.get("message") or "子节点已加入父节点") + "；共享目录已自动连接。"
                    else:
                        result["message"] = (result.get("message") or "子节点已加入父节点") + f"；但共享目录自动连接失败：{shared_result.get('message')}"
                except Exception as exc:
                    result["shared_io"] = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
                    result["message"] = (result.get("message") or "子节点已加入父节点") + f"；但共享目录自动连接失败：{type(exc).__name__}: {exc}"
        data = self.status()
        data["action_result"] = result
        data["message"] = result.get("message") or ("已加入父节点" if result.get("success") else "加入父节点失败")
        return data

    def leave_pool(self) -> Dict[str, Any]:
        script = self._make_pool_config_script(role="standalone")
        result = self._run_elevated_ps("standalone", script, timeout=180)
        if result.get("success") or result.get("config_applied"):
            self.state["pool_role"] = "standalone"
            self.state["parent_ip"] = ""
            self.state["bind_ip"] = ""
            self._save_state()
        data = self.status()
        data["action_result"] = result
        data["message"] = result.get("message") or ("已退出 HTCondor 集群" if result.get("success") else "退出集群失败")
        return data

    def _batch_quote(self, value: str) -> str:
        text = str(value)
        text = text.replace("%", "%%")
        text = text.replace('"', '\\"')
        return f'"{text}"'

    def _batch_arg(self, value: str) -> str:
        """生成 Windows bat/cmd 中可用的单个命令参数。

        cmd.exe 的 /D、/C 这类开关不能强行加双引号。
        之前自检任务会生成类似 cmd.exe "/D" "/C" "echo ..."，
        在部分 Windows cmd 解析下会导致 echo 命令失败，result.txt 不生成，
        最终表现为 condor_wait 超时或作业进入 Hold。
        """
        text = str(value)
        if re.fullmatch(r"[-/][A-Za-z0-9_:.,=+\-]+", text):
            return text.replace("%", "%%")
        return self._batch_quote(text)


    def _read_submit_account_password(self) -> str:
        """读取一键安装脚本保存的 LocalWebCondor 密码。

        这里先用 Python 直接试读文件。这样如果是 ACL 权限问题，
        可以给出清楚的中文提示，不会把 PowerShell 的 GBK 错误输出解成乱码。
        """
        secret_path = self.runtime_dir / "submit_account_secret.bin"
        if not secret_path.is_file():
            raise HTCondorClusterError(
                f"找不到 LocalWebCondor 密码密文：{secret_path}。请先重新运行 HTCondor 一键安装。"
            )

        try:
            secret_path.read_bytes()
        except PermissionError:
            raise HTCondorClusterError(
                "无法读取 LocalWebCondor 密码密文。当前后端用户没有读取权限。"
                f"密文文件：{secret_path}。{self._repair_acl_hint()}"
            )
        except OSError as exc:
            raise HTCondorClusterError(
                f"读取 LocalWebCondor 密码密文失败：{type(exc).__name__}: {exc}。{self._repair_acl_hint()}"
            )

        # PowerShell 只输出 ASCII 格式：OK:<base64密码> 或 ERROR:<异常类型>
        # 这样可以避开中文 Windows PowerShell 5.1 的乱码问题。
        ps_code = f"""
Add-Type -AssemblyName System.Security
$ErrorActionPreference = 'Stop'
try {{
    $secretPath = {json.dumps(str(secret_path))}
    $entropy = [Text.Encoding]::UTF8.GetBytes('local_web_module_system.htcondor.submit_account.v1')
    $protectedBytes = [IO.File]::ReadAllBytes($secretPath)
    $plainBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $protectedBytes,
        $entropy,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    $plainText = [Text.Encoding]::UTF8.GetString($plainBytes)
    $plainBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($plainText))
    [Console]::Out.Write('OK:' + $plainBase64)
}}
catch {{
    [Console]::Out.Write('ERROR:' + $_.Exception.GetType().FullName)
    exit 1
}}
"""
        powershell = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        )
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_code],
            capture_output=True,
            text=True,
            encoding=self._win_text_encoding(),
            errors="replace",
            timeout=30,
            check=False,
        )

        output = (result.stdout or "").strip()
        if result.returncode != 0 or output.startswith("ERROR:"):
            err_type = output.replace("ERROR:", "").strip() or "未知错误"
            raise HTCondorClusterError(
                "LocalWebCondor 密码密文解密失败。"
                f"PowerShell 异常类型：{err_type}。{self._repair_acl_hint()}"
            )

        if not output.startswith("OK:"):
            raise HTCondorClusterError("LocalWebCondor 密码密文解密输出格式异常。")

        try:
            password_bytes = base64.b64decode(output[3:].encode("ascii"))
            password = password_bytes.decode("utf-8")
        except Exception as exc:
            raise HTCondorClusterError(f"LocalWebCondor 密码解码失败：{type(exc).__name__}: {exc}")

        if not password:
            raise HTCondorClusterError("LocalWebCondor 密码密文解密后为空。")
        return password

    def _run_as_submit_account(
        self,
        command: List[str],
        working_dir: Path,
        timeout: float = 60,
    ) -> Dict[str, Any]:
        """用 LocalWebCondor 身份执行短命令。

        condor_submit 的提交人就是启动 condor_submit.exe 的 Windows 用户。
        这里必须切到一键安装时创建的 LocalWebCondor，不能用当前登录用户提交。
        """
        if os.name != "nt":
            return self._run(command, timeout=timeout)

        if not command:
            return {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": "空命令"}

        password = self._read_submit_account_password()
        working_dir.mkdir(parents=True, exist_ok=True)

        stdout_path = working_dir / "submit_stdout.txt"
        stderr_path = working_dir / "submit_stderr.txt"
        helper_path = working_dir / "submit_as_localwebcondor.ps1"

        exe_path = str(command[0])
        arg_list = [str(x) for x in command[1:]]
        arg_json = json.dumps(arg_list, ensure_ascii=False)
        arg_b64 = base64.b64encode(arg_json.encode("utf-8")).decode("ascii")

        helper_text = """
$ErrorActionPreference = 'Stop'
$exePath = '__EXE_PATH__'
$argBase64 = '__ARG_BASE64__'
$workDir = '__WORK_DIR__'
$stdoutPath = '__STDOUT_PATH__'
$stderrPath = '__STDERR_PATH__'
$accountName = '__ACCOUNT_NAME__'

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$argsJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($argBase64))
$argList = @()
if ($argsJson.Trim()) {
    $items = ConvertFrom-Json -InputObject $argsJson
    if ($items -is [System.Array]) {
        foreach ($item in $items) {
            $argList += [string]$item
        }
    }
    else {
        $argList += [string]$items
    }
}

$plainPassword = $env:LOCAL_WEB_CONDOR_PASSWORD
$env:LOCAL_WEB_CONDOR_PASSWORD = ''
if ([string]::IsNullOrWhiteSpace($plainPassword)) {
    throw 'LOCAL_WEB_CONDOR_PASSWORD 为空，无法切换到 LocalWebCondor。'
}

$securePassword = ConvertTo-SecureString $plainPassword -AsPlainText -Force
$credential = New-Object System.Management.Automation.PSCredential($accountName, $securePassword)

# 不再使用 -Wait 和 -PassThru。
# 有些 Windows 机器上，Start-Process 已经把子进程启动起来了，
# 但 PowerShell 获取子进程句柄时会报 Access is denied。
# 所以这里让子进程自己写 exit_code.txt，父脚本只等这个文件。
$childScript = Join-Path $workDir 'run_submit_command.ps1'
$exitPath = Join-Path $workDir 'submit_exit_code.txt'
Remove-Item -LiteralPath $stdoutPath, $stderrPath, $exitPath -Force -ErrorAction SilentlyContinue

$childExe = $exePath.Replace("'", "''")
$childStdout = $stdoutPath.Replace("'", "''")
$childStderr = $stderrPath.Replace("'", "''")
$childExit = $exitPath.Replace("'", "''")

$childText = @"
`$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
`$exePath = '$childExe'
`$argBase64 = '$argBase64'
`$stdoutPath = '$childStdout'
`$stderrPath = '$childStderr'
`$exitPath = '$childExit'
`$argsJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(`$argBase64))
`$argList = @()
if (`$argsJson.Trim()) {
    `$items = ConvertFrom-Json -InputObject `$argsJson
    if (`$items -is [System.Array]) {
        foreach (`$item in `$items) {
            `$argList += [string]`$item
        }
    }
    else {
        `$argList += [string]`$items
    }
}
try {
    `$outputText = & `$exePath @argList 2>&1
    `$code = `$LASTEXITCODE
    if (`$null -eq `$code) { `$code = 0 }
    `$outputText | Out-File -LiteralPath `$stdoutPath -Encoding UTF8
    '' | Out-File -LiteralPath `$stderrPath -Encoding UTF8
    [string]`$code | Set-Content -LiteralPath `$exitPath -Encoding ASCII
    exit `$code
}
catch {
    `$_.Exception.ToString() | Out-File -LiteralPath `$stderrPath -Encoding UTF8
    '1' | Set-Content -LiteralPath `$exitPath -Encoding ASCII
    exit 1
}
"@
$childText | Set-Content -LiteralPath $childScript -Encoding UTF8

$childArgs = @(
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    $childScript
)

try {
    Start-Process `
        -FilePath powershell.exe `
        -ArgumentList $childArgs `
        -WorkingDirectory $workDir `
        -Credential $credential `
        -WindowStyle Hidden
}
catch {
    $_.Exception.ToString() | Out-File -LiteralPath $stderrPath -Encoding UTF8
    exit 1
}

$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $exitPath) {
        break
    }
    Start-Sleep -Milliseconds 300
}

if (-not (Test-Path -LiteralPath $exitPath)) {
    'LocalWebCondor 子进程没有在 60 秒内返回 exit_code。' | Out-File -LiteralPath $stderrPath -Encoding UTF8
    exit 2
}

$codeText = (Get-Content -LiteralPath $exitPath -Raw).Trim()
$code = 1
if ([int]::TryParse($codeText, [ref]$code)) {
    exit $code
}
else {
    exit 1
}
"""
        account_name = f"{os.environ.get('COMPUTERNAME', '.')}\\LocalWebCondor"
        replacements = {
            "__EXE_PATH__": exe_path.replace("'", "''"),
            "__ARG_BASE64__": arg_b64,
            "__WORK_DIR__": str(working_dir).replace("'", "''"),
            "__STDOUT_PATH__": str(stdout_path).replace("'", "''"),
            "__STDERR_PATH__": str(stderr_path).replace("'", "''"),
            "__ACCOUNT_NAME__": account_name.replace("'", "''"),
        }
        for key, value in replacements.items():
            helper_text = helper_text.replace(key, value)
        helper_path.write_text(helper_text, encoding="utf-8-sig")

        powershell = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        )
        env = os.environ.copy()
        env["LOCAL_WEB_CONDOR_PASSWORD"] = password

        try:
            completed = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(helper_path)],
                capture_output=True,
                text=True,
                encoding=self._win_text_encoding(),
                errors="replace",
                timeout=timeout,
                check=False,
                shell=False,
                env=env,
            )
            stdout_text = self._read_text_auto(stdout_path) if stdout_path.is_file() else completed.stdout
            stderr_text = self._read_text_auto(stderr_path) if stderr_path.is_file() else completed.stderr
            if completed.stdout.strip():
                stdout_text = (stdout_text + "\n" + completed.stdout).strip()
            if completed.stderr.strip():
                stderr_text = (stderr_text + "\n" + completed.stderr).strip()
            return {
                "ok": completed.returncode == 0,
                "returncode": completed.returncode,
                "stdout": stdout_text.strip(),
                "stderr": stderr_text.strip(),
                "error": "",
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


    def _looks_like_path(self, value: str) -> bool:
        """粗略判断一个字符串是不是路径。"""
        text = str(value or "").strip().strip('"')
        if not text:
            return False
        if re.match(r"^[A-Za-z]:[\\/]", text):
            return True
        if text.startswith("\\\\"):
            return True
        return False

    def _collect_paths_from_json(self, value: Any) -> List[Path]:
        """从 config.json 里面找可能的输入输出路径。"""
        paths: List[Path] = []

        def walk(item: Any):
            if isinstance(item, dict):
                for v in item.values():
                    walk(v)
                return
            if isinstance(item, list):
                for v in item:
                    walk(v)
                return
            if isinstance(item, str) and self._looks_like_path(item):
                paths.append(Path(item.strip().strip('"')))

        walk(value)
        return paths

    def _grant_one_path_for_job(self, path: Path):
        r"""给 HTCondor 作业能访问的账号加权限。

        这里不让用户手动修权限。平台生成的 config.json、输出目录、运行目录，
        都由系统在提交作业前自动放开给本机 Users 组和 LocalWebCondor。

        注意：UNC 共享路径（例如 \\192.168.2.140\H8Data）不能在这里做 icacls。
        共享目录的权限必须由父节点本地目录的 NTFS 权限和 Windows 共享权限控制。
        """
        if os.name != "nt":
            return

        raw_path_text = str(path or "").strip().strip('"')
        if raw_path_text.startswith("\\\\"):
            return

        try:
            path = Path(raw_path_text)
        except Exception:
            return

        targets: List[Path] = []
        if path.exists():
            targets.append(path)
            if path.is_file() and path.parent.exists():
                targets.append(path.parent)
        else:
            # 输出目录可能还没创建；如果父目录存在，就先给父目录权限。
            parent = path.parent
            if parent and parent.exists():
                targets.append(parent)

        if not targets:
            return

        account = f"{os.environ.get('COMPUTERNAME', '.')}\\LocalWebCondor"
        grants_file = [f"{account}:M", "*S-1-5-32-545:M"]
        grants_dir = [f"{account}:(OI)(CI)M", "*S-1-5-32-545:(OI)(CI)M"]

        for target in targets:
            try:
                grants = grants_dir if target.is_dir() else grants_file
                cmd = ["icacls.exe", str(target), "/grant", *grants, "/C"]
                if target.is_dir():
                    cmd.append("/T")
                subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding=self._win_text_encoding(),
                    errors="replace",
                    timeout=30,
                    check=False,
                )
            except Exception:
                # 这里不能因为某个输入路径授权失败就让整个提交流程中断。
                # 如果确实没有权限，算法运行时 stderr 会给出具体路径。
                pass

    def _grant_paths_for_real_job(self, command: List[str], working_dir: str | None):
        """提交真实反演任务前，自动处理 config.json 等路径权限。"""
        paths: List[Path] = []

        if working_dir:
            paths.append(Path(working_dir))

        for item in command or []:
            text = str(item or "").strip().strip('"')
            if not self._looks_like_path(text):
                continue

            p = Path(text)
            paths.append(p)

            # 平台生成的 config.json 里面通常还有输入、输出目录。
            # 这里顺便解析一下，避免 EXE 后续写输出时又遇到拒绝访问。
            if p.suffix.lower() == ".json" and p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8-sig"))
                    paths.extend(self._collect_paths_from_json(data))
                except Exception:
                    pass

        seen: set[str] = set()
        for p in paths:
            key = str(p).lower()
            if key.startswith("\\\\"):
                continue
            if key in seen:
                continue
            seen.add(key)
            self._grant_one_path_for_job(p)

    def _local_machine_names(self) -> set[str]:
        """返回当前父节点可能出现在 HTCondor Machine 字段中的名字。"""
        names: set[str] = set()
        for value in [
            os.environ.get("COMPUTERNAME", ""),
            os.environ.get("HOSTNAME", ""),
            socket.gethostname(),
            socket.getfqdn(),
        ]:
            text = str(value or "").strip().lower()
            if text:
                names.add(text)
                names.add(text.split(".")[0])
        return names

    def _is_local_target_machine(self, target_machine: str) -> bool:
        """判断 HTCondor 目标节点是否就是父节点本机。"""
        target = str(target_machine or "").strip().lower()
        if not target:
            return False
        local_names = self._local_machine_names()
        return target in local_names or target.split(".")[0] in local_names

    def _write_job_files(
        self,
        job_id: str,
        command: List[str],
        working_dir: str | None,
        env: Dict[str, str] | None,
        target_machine: str = "",
    ) -> Dict[str, Path]:
        job_path = self.job_dir / job_id
        if job_path.exists():
            shutil.rmtree(job_path, ignore_errors=True)
        job_path.mkdir(parents=True, exist_ok=True)

        # 保证 LocalWebCondor 可以读写这个作业目录。
        if os.name == "nt":
            account = f"{os.environ.get('COMPUTERNAME', '.')}\\LocalWebCondor"
            try:
                subprocess.run(
                    ["icacls.exe", str(job_path), "/grant", f"{account}:(OI)(CI)M", "/T", "/C"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                    check=False,
                )
            except Exception:
                pass

        # 真实反演任务的 config.json 是后端临时生成的，
        # HTCondor 作业换账号运行后可能读不到。
        # 所以这里在提交前自动给相关路径加权限。
        self._grant_paths_for_real_job(command, working_dir)

        run_cmd = job_path / "run_job.cmd"
        sub_file = job_path / "job.sub"

        # 为了让子节点也能读到平台生成的 config.json，
        # 这里把较小的 json 配置文件随作业一起传输。
        #
        # 如果 TaskManager 生成的 config.json 里包含 __LOCAL_WEB_JOB_DIR__，
        # 说明这是自动拆分后的子任务配置。系统会把 config.json 同目录下的
        # input/output 子目录一起传给 HTCondor，并在执行节点上把占位符替换成
        # 当前 HTCondor 作业目录。
        safe_command = [str(x) for x in command]
        transfer_files = ["run_job.cmd"]
        transfer_output_items = ["result.txt"]
        config_arg_index = None
        config_copy = job_path / "localweb_config.json"
        rewrite_script = job_path / "rewrite_config.ps1"

        # 共享目录模式下，HTCondor 不再传输大型 input/output/cm_files 目录。
        # 子节点直接通过 UNC 读取父节点共享目录，作业只传输 run_job.cmd、localweb_config.json、result.txt。
        try:
            shared_cfg = self.shared_io_config()
        except Exception:
            shared_cfg = {}
        shared_io_enabled = bool(shared_cfg.get("enabled") and str(shared_cfg.get("unc_root") or "").strip())

        for idx in range(len(safe_command) - 1, -1, -1):
            try:
                p = Path(safe_command[idx].strip().strip('"'))
                if p.suffix.lower() == ".json" and p.is_file() and p.stat().st_size <= 10 * 1024 * 1024:
                    config_text = p.read_text(encoding="utf-8-sig", errors="replace")
                    shutil.copy2(p, config_copy)
                    config_arg_index = idx
                    transfer_files.append("localweb_config.json")

                    if "__LOCAL_WEB_JOB_DIR__" in config_text:
                        rewrite_script.write_text(
                            "\n".join([
                                "$ErrorActionPreference = 'Stop'",
                                "$jobDir = $env:LOCAL_WEB_JOB_DIR",
                                "$jobDirForJson = $jobDir -replace '\\\\','/'",
                                "$src = Join-Path $jobDir 'localweb_config.json'",
                                "$dst = Join-Path $jobDir 'localweb_runtime_config.json'",
                                "$text = Get-Content -LiteralPath $src -Raw",
                                "$text = $text.Replace('__LOCAL_WEB_JOB_DIR__', $jobDirForJson)",
                                "$utf8NoBom = New-Object System.Text.UTF8Encoding($false)",
                                "[System.IO.File]::WriteAllText($dst, $text, $utf8NoBom)",
                            ]) + "\n",
                            encoding="utf-8-sig",
                        )
                        transfer_files.append("rewrite_config.ps1")

                        # 非共享目录模式：把 part_config.json 同目录下的 input/output/cm_files 等目录传给执行节点。
                        # 共享目录模式：禁止传输这些大目录，子节点通过 UNC 直接读取/写入共享目录。
                        if shared_io_enabled:
                            break

                        for child in sorted(p.parent.iterdir()):
                            if not child.is_dir():
                                continue
                            target_dir = job_path / child.name
                            if target_dir.exists():
                                shutil.rmtree(target_dir, ignore_errors=True)

                            # 这里会把子任务目录中的 input/output/cm_files 等目录传给执行节点。
                            # 输入目录通常已经包含 nc 文件；输出目录一般是空目录。
                            # 只有空目录才加入 transfer_output_files，避免把大型输入文件又从子节点传回父节点。
                            has_initial_files = any(x.is_file() for x in child.rglob('*'))
                            shutil.copytree(child, target_dir)
                            transfer_files.append(child.name)
                            if not has_initial_files:
                                transfer_output_items.append(child.name)
                    break
            except Exception:
                pass

        # 去重，避免 transfer_input_files 中重复出现同名文件。
        clean_transfer_files = []
        seen_transfer = set()
        for item in transfer_files:
            if item not in seen_transfer:
                seen_transfer.add(item)
                clean_transfer_files.append(item)
        transfer_files = clean_transfer_files

        cmd_parts = []
        for idx, item in enumerate(safe_command):
            if config_arg_index is not None and idx == config_arg_index:
                cmd_parts.append('"%LOCAL_WEB_CONFIG_JSON%"')
            else:
                cmd_parts.append(self._batch_arg(item))
        cmd_line = " ".join(cmd_parts)

        lines = [
            "@echo off",
            "chcp 65001 >nul",
            "setlocal EnableExtensions",
            "set LOCAL_WEB_JOB_DIR=%CD%",
            "set LOCAL_WEB_CONFIG_JSON=%LOCAL_WEB_JOB_DIR%\\localweb_config.json",
            "if exist \"%LOCAL_WEB_JOB_DIR%\\rewrite_config.ps1\" (",
            "  powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"%LOCAL_WEB_JOB_DIR%\\rewrite_config.ps1\"",
            "  if errorlevel 1 exit /b 101",
            "  set LOCAL_WEB_CONFIG_JSON=%LOCAL_WEB_JOB_DIR%\\localweb_runtime_config.json",
            ")",
            "echo [HTCONDOR] job started",
            "echo [HTCONDOR] computer=%COMPUTERNAME%",
            "echo [HTCONDOR] target_machine=" + str(target_machine or ""),
            "echo [HTCONDOR] date=%DATE% %TIME%",
        ]
        # 共享目录模式下，HTCondor 子任务运行在独立会话中。
        # 先尝试写入凭据并连接共享目录；如果 net use 失败，再直接测试 UNC 可访问性。
        # 这样可兼容两类场景：
        # 1）执行账号需要 localwebshare 凭据才能访问父节点共享目录；
        # 2）执行账号本身已具备共享目录权限，net use 失败但 UNC 仍可直接访问。
        share_unc = str(shared_cfg.get("unc_root") or "").strip()
        share_user = str(os.environ.get("LOCAL_WEB_HTCONDOR_SHARE_USER", "")).strip()
        share_password = str(os.environ.get("LOCAL_WEB_HTCONDOR_SHARE_PASSWORD", "")).strip()

        local_target_machine = self._is_local_target_machine(target_machine)
        if shared_cfg.get("enabled") and share_unc and local_target_machine:
            lines.extend([
                "echo [HTCONDOR] shared directory mode enabled, but target is parent/local machine; skip net use and use local paths from config.",
            ])

        if shared_cfg.get("enabled") and share_unc and not local_target_machine:
            safe_unc = share_unc.replace("%", "%%")
            safe_user = share_user.replace("%", "%%")
            safe_password = share_password.replace("%", "%%")

            share_host = ""
            try:
                share_host = share_unc.strip("\\").split("\\")[0]
            except Exception:
                share_host = ""
            safe_host = share_host.replace("%", "%%")

            lines.extend([
                "echo [HTCONDOR] connect shared directory",
                "echo [HTCONDOR] job user:",
                "whoami",
                "echo [HTCONDOR] share_unc=" + safe_unc,
                "echo [HTCONDOR] current net use before connect:",
                "net use",
            ])

            if share_user and share_password:
                lines.extend([
                    f'net use "{safe_unc}" /delete /y >nul 2>nul',
                    f'cmdkey /delete:{safe_host} >nul 2>nul',
                    f'cmdkey /add:{safe_host} /user:"{safe_user}" /pass:"{safe_password}"',
                    "if errorlevel 1 (",
                    "  echo [HTCONDOR-WARN] failed to store shared credential, will test direct UNC access",
                    ")",
                    f'net use "{safe_unc}" /persistent:no',
                    "if errorlevel 1 (",
                    "  echo [HTCONDOR-WARN] net use shared directory failed, will test direct UNC access",
                    "  echo [HTCONDOR] net use after failed connect:",
                    "  net use",
                    "  echo [HTCONDOR] cmdkey list:",
                    "  cmdkey /list",
                    ")",
                ])
            else:
                lines.extend([
                    "echo [HTCONDOR-WARN] shared directory enabled but no share credential configured",
                ])

            lines.extend([
                f'dir "{safe_unc}" >nul 2>nul',
                "if errorlevel 1 (",
                "  echo [HTCONDOR-ERROR] shared directory is not accessible in this HTCondor job session",
                "  echo [HTCONDOR] job user:",
                "  whoami",
                "  echo [HTCONDOR] net use:",
                "  net use",
                "  echo [HTCONDOR] cmdkey list:",
                "  cmdkey /list",
                "  (",
                "    echo return_code=1326",
                "    echo computer=%COMPUTERNAME%",
                "    echo ended_at=%DATE% %TIME%",
                "  ) > \"%LOCAL_WEB_JOB_DIR%\\result.txt\"",
                "  exit /b 1326",
                ")",
                "echo [HTCONDOR] shared directory accessible",
            ])
        for key, value in (env or {}).items():
            key = str(key).strip()
            if not key or any(ch in key for ch in " =&|"):
                continue
            val = str(value).replace("%", "%%")
            lines.append(f"set {key}={val}")

        lines.extend([
            "echo [HTCONDOR] request_cpus=%LOCAL_WEB_HTCONDOR_REQUEST_CPUS%",
            "echo [HTCONDOR] request_memory_mb=%LOCAL_WEB_HTCONDOR_REQUEST_MEMORY_MB%",
            "echo [HTCONDOR] threads_per_exe=%LOCAL_WEB_HTCONDOR_THREADS_PER_EXE%",
        ])

        if working_dir:
            lines.append(f"cd /d {self._batch_quote(str(working_dir))}")
            lines.append("if errorlevel 1 exit /b 100")

        lines.extend([
            cmd_line,
            "set LOCAL_WEB_EXIT=%ERRORLEVEL%",
            "(",
            "  echo return_code=%LOCAL_WEB_EXIT%",
            "  echo computer=%COMPUTERNAME%",
            "  echo ended_at=%DATE% %TIME%",
            ") > \"%LOCAL_WEB_JOB_DIR%\\result.txt\"",
            "if not exist \"%LOCAL_WEB_JOB_DIR%\\result.txt\" echo return_code=%LOCAL_WEB_EXIT% > \"%LOCAL_WEB_JOB_DIR%\\result.txt\"",
            "echo [HTCONDOR] job finished, return_code=%LOCAL_WEB_EXIT%",
            "exit /b %LOCAL_WEB_EXIT%",
        ])
        run_cmd.write_text("\r\n".join(lines) + "\r\n", encoding="ascii", errors="ignore")

        job_dir_posix = str(job_path).replace("\\", "/")
        target_machine = str(target_machine or "").strip()
        requirements_line = ""
        if target_machine:
            # 指定执行节点。这样父节点和子节点可以各拿到一份子任务。
            safe_machine = target_machine.replace('\\', '\\\\').replace('"', '\\"')
            requirements_line = f'requirements = (Machine == "{safe_machine}")\n'

        transfer_input_files = ", ".join(transfer_files)
        clean_transfer_output_items = []
        seen_output_items = set()
        for item in transfer_output_items:
            if item and item not in seen_output_items:
                seen_output_items.add(item)
                clean_transfer_output_items.append(item)
        transfer_output_files = ", ".join(clean_transfer_output_items)
        transfer_output_line = f"transfer_output_files = {transfer_output_files}\n" if transfer_output_files else ""

        try:
            request_cpus_raw = (
                (env or {}).get("LOCAL_WEB_HTCONDOR_REQUEST_CPUS")
                or os.environ.get("LOCAL_WEB_HTCONDOR_REQUEST_CPUS", "1")
                or "1"
            )
            request_cpus = max(1, min(128, int(request_cpus_raw)))
        except Exception:
            request_cpus = 1

        try:
            default_request_memory_raw = (
                (env or {}).get("LOCAL_WEB_HTCONDOR_DEFAULT_PEAK_MEMORY_MB")
                or os.environ.get("LOCAL_WEB_HTCONDOR_DEFAULT_PEAK_MEMORY_MB")
                or "4096"
            )
            request_memory_raw = (
                (env or {}).get("LOCAL_WEB_HTCONDOR_REQUEST_MEMORY_MB")
                or os.environ.get("LOCAL_WEB_HTCONDOR_REQUEST_MEMORY_MB")
                or default_request_memory_raw
                or "4096"
            )
            request_memory_mb = int(float(request_memory_raw))
        except Exception:
            request_memory_mb = 4096
        request_memory_mb = max(1024, min(262144, request_memory_mb))

        # 默认不启用 run_as_owner：Windows HTCondor 没有配置 CREDD_HOST 时，
        # condor_submit 会直接拒绝 run_as_owner=true。需要该模式时可显式设置
        # LOCAL_WEB_HTCONDOR_RUN_AS_OWNER=1，并完成 HTCondor CREDD 配置。
        run_as_owner_env = str(os.environ.get("LOCAL_WEB_HTCONDOR_RUN_AS_OWNER", "")).strip().lower()
        if run_as_owner_env in {"1", "true", "yes", "on"}:
            run_as_owner_value = "true"
        elif run_as_owner_env in {"0", "false", "no", "off"}:
            run_as_owner_value = "false"
        else:
            run_as_owner_value = "false"

        sub_text = f"""universe = vanilla
executable = C:/Windows/System32/cmd.exe
arguments = /D /C run_job.cmd
initialdir = {job_dir_posix}
{requirements_line}should_transfer_files = YES
when_to_transfer_output = ON_EXIT
transfer_input_files = {transfer_input_files}
{transfer_output_line}# 关闭 HTCondor stdout/stderr 实时流，避免 tqdm 进度条频繁刷屏拖慢局域网传输。
# 任务结束后系统仍会读取 stdout.txt / stderr.txt 并写入平台日志。
stream_output = False
stream_error = False
output = stdout.txt
error = stderr.txt
log = event.log
request_cpus = {request_cpus}
request_memory = {request_memory_mb}MB
request_disk = 1024MB
run_as_owner = {run_as_owner_value}
queue 1
"""
        sub_file.write_text(sub_text, encoding="ascii", errors="ignore")

        return {
            "job_dir": job_path,
            "run_cmd": run_cmd,
            "sub_file": sub_file,
            "stdout": job_path / "stdout.txt",
            "stderr": job_path / "stderr.txt",
            "event_log": job_path / "event.log",
            "result": job_path / "result.txt",
        }

    def _parse_cluster_id(self, text: str) -> str:
        match = re.search(r"cluster\s+(\d+)", text or "", re.IGNORECASE)
        return match.group(1) if match else ""


    def get_running_job(self, job_id: str) -> Dict[str, Any]:
        """返回当前正在等待的 HTCondor 作业信息。"""
        return dict(self.running_jobs.get(str(job_id), {}) or {})

    def cancel_job(self, job_id: str = "", cluster_id: str = "") -> Dict[str, Any]:
        """取消一个 HTCondor 作业，日志尽量写得清楚一点。"""
        job_id = str(job_id or "")
        cluster_id = str(cluster_id or "").strip()

        info = {}
        if job_id:
            info = self.running_jobs.get(job_id) or {}
            if not cluster_id:
                cluster_id = str(info.get("cluster_id") or "").strip()

        if not cluster_id:
            return {
                "ok": False,
                "message": "没有找到可取消的 HTCondor ClusterId",
                "cluster_id": "",
            }

        if info.get("cancel_requested"):
            return {
                "ok": True,
                "cluster_id": cluster_id,
                "stdout": "",
                "stderr": "",
                "error": "",
                "message": f"ClusterId={cluster_id} 的取消命令已发送，正在等待 HTCondor 结束作业。",
                "returncode": 0,
            }
        if job_id and job_id in self.running_jobs:
            self.running_jobs[job_id]["cancel_requested"] = True

        job_dir_text = str(info.get("job_dir") or "").strip()
        if job_dir_text:
            work_dir = Path(job_dir_text)
        else:
            safe_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", cluster_id)
            work_dir = self.job_dir / f"cancel_{safe_id}_{int(time.time())}"
        work_dir.mkdir(parents=True, exist_ok=True)

        messages: List[str] = []
        final_result: Dict[str, Any] = {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "error": "",
        }

        # 先发普通 condor_rm。正常情况下这一条就够了。
        result = self._run_as_submit_account(
            [self._exe("condor_rm.exe"), cluster_id],
            working_dir=work_dir,
            timeout=60,
        )
        final_result = result
        text = self._clean_condor_text("\n".join(
            x for x in [result.get("stdout", ""), result.get("stderr", ""), result.get("error", "")]
            if x
        ))

        if text:
            messages.append(f"已发送 condor_rm {cluster_id}：{text}")
        else:
            messages.append(f"已发送 condor_rm {cluster_id}。")

        # 查一下队列状态。这里只等很短时间，主要是给前端明确反馈。
        last_query_text = ""
        job_still_in_queue = False
        for _ in range(5):
            query = self._run_as_submit_account(
                [self._exe("condor_q.exe"), cluster_id, "-af", "ClusterId", "ProcId", "JobStatus"],
                working_dir=work_dir,
                timeout=20,
            )
            query_text = self._clean_condor_text("\n".join(
                x for x in [query.get("stdout", ""), query.get("stderr", ""), query.get("error", "")]
                if x
            ))
            last_query_text = query_text

            if not query_text:
                job_still_in_queue = False
                break

            low = query_text.lower()
            if "all queues are empty" in low or "no jobs" in low or "not found" in low:
                job_still_in_queue = False
                break

            job_still_in_queue = True
            time.sleep(0.5)

        if job_still_in_queue:
            messages.append(f"condor_q 仍能看到作业：{last_query_text}")
            force = self._run_as_submit_account(
                [self._exe("condor_rm.exe"), "-forcex", cluster_id],
                working_dir=work_dir,
                timeout=60,
            )
            force_text = self._clean_condor_text("\n".join(
                x for x in [force.get("stdout", ""), force.get("stderr", ""), force.get("error", "")]
                if x
            ))
            if force_text:
                messages.append(f"已补发 condor_rm -forcex {cluster_id}：{force_text}")
            else:
                messages.append(f"已补发 condor_rm -forcex {cluster_id}。")
            if force.get("ok"):
                final_result = force
        else:
            messages.append("condor_q 已经查不到该作业，取消已生效或作业已退出。")

        ok = bool(final_result.get("ok")) or (not job_still_in_queue)
        message = "\n".join(x for x in messages if x).strip()
        return {
            "ok": ok,
            "cluster_id": cluster_id,
            "stdout": self._clean_condor_text(final_result.get("stdout") or ""),
            "stderr": self._clean_condor_text(final_result.get("stderr") or ""),
            "error": self._clean_condor_text(final_result.get("error") or ""),
            "message": message or f"condor_rm 已发送，ClusterId={cluster_id}",
            "returncode": final_result.get("returncode"),
        }

    def _emit_live_text(self, callback, job_id: str, prefix: str, text: str, old_len: int) -> int:
        """把新增 stdout/stderr/event 片段发给 TaskManager。"""
        if callback is None:
            return len(text or "")

        text = text or ""
        if len(text) <= old_len:
            return old_len

        piece = text[old_len:]
        if piece:
            try:
                callback({
                    "type": prefix.lower(),
                    "job_id": job_id,
                    "text": piece,
                })
            except Exception:
                pass
        return len(text)

    def run_job(
        self,
        job_id: str,
        command: List[str],
        working_dir: str | None = None,
        env: Dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        on_update=None,
        should_cancel=None,
        target_machine: str = "",
    ) -> Dict[str, Any]:
        if not self.distributed_execution_enabled():
            raise HTCondorClusterError("HTCondor 当前不可用，任务没有提交。")

        files = self._write_job_files(job_id, command, working_dir, env, target_machine=target_machine)
        timeout_seconds = int(timeout_seconds or self.default_timeout_seconds)

        # condor_submit 必须用 LocalWebCondor 提交，不能用当前登录用户提交。
        submit = self._run_as_submit_account(
            [self._exe("condor_submit.exe"), str(files["sub_file"])],
            working_dir=files["job_dir"],
            timeout=60,
        )
        submit_text = "\n".join(x for x in [submit.get("stdout", ""), submit.get("stderr", ""), submit.get("error", "")] if x)
        cluster_id = self._parse_cluster_id(submit_text)
        if not cluster_id:
            raise HTCondorClusterError(f"condor_submit 失败：{submit_text}")

        try:
            submitted_request_cpus = max(1, int((env or {}).get("LOCAL_WEB_HTCONDOR_REQUEST_CPUS") or 1))
        except Exception:
            submitted_request_cpus = 1
        try:
            submitted_request_memory_mb = max(1024, int((env or {}).get("LOCAL_WEB_HTCONDOR_REQUEST_MEMORY_MB") or 1024))
        except Exception:
            submitted_request_memory_mb = 1024

        self.running_jobs[str(job_id)] = {
            "cluster_id": cluster_id,
            "job_dir": str(files["job_dir"]),
            "event_log": str(files["event_log"]),
            "request_cpus": submitted_request_cpus,
            "request_memory_mb": submitted_request_memory_mb,
        }

        if on_update is not None:
            try:
                on_update({
                    "type": "submitted",
                    "job_id": job_id,
                    "cluster_id": cluster_id,
                    "job_dir": str(files["job_dir"]),
                    "target_machine": str(target_machine or ""),
                    "request_cpus": submitted_request_cpus,
                    "request_memory_mb": submitted_request_memory_mb,
                    "threads_per_exe": str((env or {}).get("LOCAL_WEB_HTCONDOR_THREADS_PER_EXE") or ""),
                })
            except Exception:
                pass

        wait_cmd = [
            self._exe("condor_wait.exe"),
            "-wait",
            str(timeout_seconds),
            str(files["event_log"]),
        ]

        wait_stdout = ""
        wait_stderr = ""
        stdout_offset = 0
        stderr_offset = 0
        event_offset = 0
        # 仅保留最近的事件/输出片段用于 Hold 和 return_code 判断，避免内存无限增长。
        stdout_tail = ""
        stderr_tail = ""
        event_tail = ""
        tail_limit = 256 * 1024
        cancelled = False
        cancel_requested = False

        try:
            process = subprocess.Popen(
                wait_cmd,
                cwd=str(files["job_dir"]),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding=self._win_text_encoding(),
                errors="replace",
                shell=False,
            )

            deadline = time.time() + timeout_seconds + 60

            while True:
                if callable(should_cancel) and should_cancel() and not cancel_requested:
                    cancelled = True
                    cancel_requested = True
                    rm_result = self.cancel_job(job_id=job_id, cluster_id=cluster_id)
                    rm_text = self._clean_condor_text(
                        rm_result.get("message") or rm_result.get("stdout") or rm_result.get("stderr") or rm_result.get("error") or ""
                    )
                    if not rm_text:
                        rm_text = f"已发送 condor_rm {cluster_id}。"
                    try:
                        on_update and on_update({
                            "type": "event",
                            "job_id": job_id,
                            "text": f"任务已请求取消。{rm_text}\n",
                        })
                    except Exception:
                        pass

                # 只读取自上次轮询后新增的日志字节，避免日志越长重复读取量越大。
                stdout_piece, stdout_offset = self._read_text_delta_auto(files["stdout"], stdout_offset)
                stderr_piece, stderr_offset = self._read_text_delta_auto(files["stderr"], stderr_offset)
                event_piece, event_offset = self._read_text_delta_auto(files["event_log"], event_offset)
                self._emit_live_piece(on_update, job_id, "stdout", stdout_piece)
                self._emit_live_piece(on_update, job_id, "stderr", stderr_piece)
                self._emit_live_piece(on_update, job_id, "event", event_piece)

                if stdout_piece:
                    stdout_tail = (stdout_tail + stdout_piece)[-tail_limit:]
                if stderr_piece:
                    stderr_tail = (stderr_tail + stderr_piece)[-tail_limit:]
                if event_piece:
                    event_tail = (event_tail + event_piece)[-tail_limit:]

                stdout_text = stdout_tail
                stderr_text = stderr_tail
                event_text = event_tail

                # HTCondor 传输输出失败时，作业会进入 Hold，condor_wait 不会自然返回。
                # 典型情况：EXE 已经 return_code=0，但 result.txt 没有写在执行沙箱根目录，
                # HTCondor 因找不到 result.txt 把作业 Hold。这里主动识别，避免前端一直显示运行中。
                event_lower = (event_text or "").lower()
                if ("job was held" in event_lower or "job is held" in event_lower or "transfer output files failure" in event_lower):
                    combined_now = "\n".join([stdout_text or "", stderr_text or "", event_text or ""])
                    exe_finished_ok = bool(
                        re.search(r"job finished,\s*return_code\s*=\s*0", combined_now, re.IGNORECASE)
                        or re.search(r"return_code\s*=\s*0", combined_now, re.IGNORECASE)
                    )
                    try:
                        process.kill()
                    except Exception:
                        pass

                    if exe_finished_ok:
                        # 作业已经完成，只是 HTCondor 因 transfer_output_files 中的某个文件缺失而 Hold。
                        # 清理 held 队列项，但不要把平台任务判为 cancelled。
                        try:
                            self._run_as_submit_account(
                                [self._exe("condor_rm.exe"), cluster_id],
                                working_dir=files["job_dir"],
                                timeout=60,
                            )
                        except Exception:
                            pass
                        wait_stdout += "\n[LOCAL-WEB] HTCondor 作业在 EXE return_code=0 后进入 Hold，已按完成处理并清理队列。"
                        break

                    hold_lines = [
                        line.strip()
                        for line in (event_text or "").splitlines()
                        if "Hold" in line or "held" in line.lower() or "Transfer output" in line
                    ]
                    detail = hold_lines[-1] if hold_lines else "HTCondor 作业进入 Hold。"
                    try:
                        self._run_as_submit_account(
                            [self._exe("condor_rm.exe"), cluster_id],
                            working_dir=files["job_dir"],
                            timeout=60,
                        )
                    except Exception:
                        pass
                    raise HTCondorClusterError(f"HTCondor 作业进入 Hold：{detail}")

                if process.poll() is not None:
                    break

                if time.time() > deadline:
                    try:
                        process.kill()
                    except Exception:
                        pass
                    try:
                        self.cancel_job(job_id=job_id, cluster_id=cluster_id)
                    except Exception:
                        pass
                    raise HTCondorClusterError("condor_wait 等待超时，已尝试取消 HTCondor 作业。")

                time.sleep(self.job_poll_seconds)

            out, err = process.communicate(timeout=5)
            wait_stdout = out or ""
            wait_stderr = err or ""

            # 最后再读取一次增量，避免最后几行输出没有推到前端。
            stdout_piece, stdout_offset = self._read_text_delta_auto(files["stdout"], stdout_offset)
            stderr_piece, stderr_offset = self._read_text_delta_auto(files["stderr"], stderr_offset)
            event_piece, event_offset = self._read_text_delta_auto(files["event_log"], event_offset)
            self._emit_live_piece(on_update, job_id, "stdout", stdout_piece)
            self._emit_live_piece(on_update, job_id, "stderr", stderr_piece)
            self._emit_live_piece(on_update, job_id, "event", event_piece)

            if process.returncode != 0 and not cancelled:
                wait_text = "\n".join(x for x in [wait_stdout, wait_stderr] if x)
                try:
                    self.cancel_job(job_id=job_id, cluster_id=cluster_id)
                except Exception:
                    pass
                raise HTCondorClusterError(f"condor_wait 失败或超时：{wait_text}")

        except HTCondorClusterError:
            raise
        except Exception as exc:
            try:
                self.cancel_job(job_id=job_id, cluster_id=cluster_id)
            except Exception:
                pass
            raise HTCondorClusterError(f"等待 HTCondor 作业失败：{type(exc).__name__}: {exc}") from exc
        finally:
            self.running_jobs.pop(str(job_id), None)

        stdout = self._read_text_auto(files["stdout"])
        stderr = self._read_text_auto(files["stderr"])
        event_text = self._read_text_auto(files["event_log"])
        result_text = self._read_text_auto(files["result"])

        # 先从 result.txt 取真实退出码。
        # 注意：tqdm 默认写到 stderr，stderr 有进度条不代表任务失败。
        return_code = None
        code_sources = [
            result_text,
            stdout,
            stderr,
            event_text,
        ]
        for source in code_sources:
            if not source:
                continue
            m = re.search(r"return_code\s*=\s*(-?\d+)", source, re.IGNORECASE)
            if not m:
                m = re.search(r"job finished,\s*return_code\s*=\s*(-?\d+)", source, re.IGNORECASE)
            if not m:
                m = re.search(r"return value\s+(-?\d+)", source, re.IGNORECASE)
            if m:
                try:
                    return_code = int(m.group(1))
                    break
                except Exception:
                    pass

        if return_code is None:
            if cancelled:
                return_code = -2
            elif "all jobs done" in (wait_stdout + wait_stderr).lower():
                # 如果没有 result.txt，但 condor_wait 已确认结束，先不因为 stderr 进度条判失败。
                return_code = 0
            else:
                return_code = 1

        # 如果用户在 EXE 已经自然结束后才点了“取消”，condor_rm 可能已经来不及真正杀掉作业。
        # 这种情况下 event.log / result.txt 会显示 return_code=0 或 Normal termination。
        # 旧逻辑只要看到 cancel_flags 就把任务判为 cancelled，导致后续不回收输出文件。
        # 这里以真实退出码和 HTCondor 终止事件为准：正常退出 return_code=0 优先判为成功。
        combined_runtime_text = "\n".join([stdout or "", stderr or "", result_text or "", event_text or ""]).lower()
        normal_success = (
            return_code == 0
            and (
                "normal termination" in combined_runtime_text
                or "job finished, return_code=0" in combined_runtime_text
                or "return_code=0" in combined_runtime_text
                or "return_code = 0" in combined_runtime_text
            )
        )
        final_cancelled = bool(cancelled and not normal_success)

        computer = ""
        m = re.search(r"computer\s*=\s*([^\r\n]+)", result_text)
        if m:
            computer = m.group(1).strip()

        return {
            "cluster_id": cluster_id,
            "job_dir": str(files["job_dir"]),
            "hostname": computer or socket.gethostname(),
            "target_machine": str(target_machine or ""),
            "return_code": return_code,
            "stdout": "" if on_update else stdout,
            "stderr": "" if on_update else stderr,
            "event_log": event_text,
            "wait_output": "\n".join(x for x in [wait_stdout, wait_stderr] if x),
            "started_at": now_iso(),
            "ended_at": now_iso(),
            "live_output_sent": bool(on_update),
            "cancelled": final_cancelled,
            "cancel_requested": cancelled,
        }

    def smoke_test(self) -> Dict[str, Any]:
        command = ["C:\\Windows\\System32\\cmd.exe", "/D", "/C", "echo htcondor smoke ok"]
        result = self.run_job(
            job_id=f"smoke_{int(time.time())}",
            command=command,
            working_dir=str(self.runtime_dir),
            env={},
            timeout_seconds=120,
        )
        result["success"] = result.get("return_code") == 0
        return result

    def tail_logs(self, max_lines: int = 200) -> Dict[str, Any]:
        log_files = []
        for path in [Path(r"C:\Condor\log\SchedLog"), Path(r"C:\Condor\log\MasterLog")]:
            if path.is_file():
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
                    log_files.append({"path": str(path), "lines": lines})
                except Exception as exc:
                    log_files.append({"path": str(path), "lines": [str(exc)]})
        return {"items": log_files}
