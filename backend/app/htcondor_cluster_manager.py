from __future__ import annotations

import base64
import json
import ipaddress
import locale
import os
import re
import shutil
import socket
import subprocess
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
        self.running_jobs = {}
        self.state = self._load_state()

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
    def shared_io_config(self) -> Dict[str, Any]:
        """读取共享目录模式配置。

        共享目录模式的目标：
        - 大型输入文件不再通过 HTCondor 传输到执行节点；
        - 子节点直接通过 UNC 路径读取父节点共享目录；
        - 输出也直接写入父节点共享目录；
        - HTCondor 只传输 run_job.cmd、localweb_config.json、result.txt 等小文件。
        """
        raw = self.state.get("shared_io_config") or {}
        if not isinstance(raw, dict):
            raw = {}
        return {
            "enabled": bool(raw.get("enabled")),
            "local_root": str(raw.get("local_root") or "").strip(),
            "unc_root": str(raw.get("unc_root") or "").strip(),
            "share_name": str(raw.get("share_name") or "LocalWebData").strip() or "LocalWebData",
            "updated_at": str(raw.get("updated_at") or ""),
        }

    def set_shared_io_config(
        self,
        enabled: bool,
        local_root: str = "",
        unc_root: str = "",
        share_name: str = "LocalWebData",
    ) -> Dict[str, Any]:
        local_root = str(local_root or "").strip()
        unc_root = str(unc_root or "").strip()
        share_name = str(share_name or "LocalWebData").strip() or "LocalWebData"

        if enabled:
            if not local_root:
                raise HTCondorClusterError("启用共享目录模式时必须填写父节点本地目录，例如 D:/H8/data。")
            if not unc_root:
                host = socket.gethostname()
                unc_root = f"\\\\{host}\\{share_name}"

        self.state["shared_io_config"] = {
            "enabled": bool(enabled),
            "local_root": local_root,
            "unc_root": unc_root,
            "share_name": share_name,
            "updated_at": now_iso(),
        }
        self._save_state()
        data = self.shared_io_config()
        data["message"] = "共享目录模式已启用" if enabled else "共享目录模式已关闭"
        return data

    def prepare_local_share(self, local_root: str, share_name: str = "LocalWebData") -> Dict[str, Any]:
        """在父节点本机创建 Windows 共享目录。

        需要管理员权限。这里尽量自动完成：创建目录、设置 NTFS 权限、创建 net share。
        子节点必须能访问返回的 UNC 路径，后续任务才可以启用共享目录模式。
        """
        if os.name != "nt":
            raise HTCondorClusterError("共享目录自动配置当前只支持 Windows。")

        local_root = str(local_root or "").strip()
        share_name = str(share_name or "LocalWebData").strip() or "LocalWebData"
        if not local_root:
            raise HTCondorClusterError("本地共享目录不能为空，例如 D:/H8/data。")

        root = Path(local_root)
        root.mkdir(parents=True, exist_ok=True)

        commands: List[Dict[str, Any]] = []

        # 给常见执行账号和局域网测试账号足够的读写权限。
        # 注意：共享权限和 NTFS 权限是两套权限，都要放行。
        grant_targets = ["Users", "Everyone"]
        for target in grant_targets:
            result = self._run(["icacls.exe", str(root), "/grant", f"{target}:(OI)(CI)M", "/T", "/C"], timeout=60)
            commands.append({"command": f"icacls {root} /grant {target}", **result})

        # net share 已存在时会失败，这里先删除再创建，避免旧路径残留。
        delete_result = self._run(["net.exe", "share", share_name, "/delete", "/y"], timeout=30)
        commands.append({"command": f"net share {share_name} /delete", **delete_result})

        create_result = self._run(["net.exe", "share", f"{share_name}={str(root)}", "/GRANT:Everyone,FULL"], timeout=60)
        commands.append({"command": f"net share {share_name}={root}", **create_result})
        if not create_result.get("ok"):
            raise HTCondorClusterError(
                "创建 Windows 共享目录失败。请确认系统以管理员权限启动。"
                f"输出：{create_result.get('stdout') or create_result.get('stderr') or create_result.get('error')}"
            )

        host = socket.gethostname()
        unc_root = f"\\\\{host}\\{share_name}"
        config = self.set_shared_io_config(True, local_root=str(root), unc_root=unc_root, share_name=share_name)
        config["commands"] = commands
        config["message"] = f"已创建共享目录：{unc_root} -> {root}"
        return config

    def test_shared_io(self) -> Dict[str, Any]:
        """在当前机器上测试共享目录是否可读写。

        父节点测试通过只代表父节点本机可访问。子节点仍需能访问同一个 UNC 路径。
        """
        cfg = self.shared_io_config()
        if not cfg.get("enabled"):
            return {"ok": False, "message": "共享目录模式未启用", "config": cfg}
        unc_root = str(cfg.get("unc_root") or "").strip()
        if not unc_root:
            return {"ok": False, "message": "共享 UNC 路径为空", "config": cfg}

        test_dir = Path(unc_root) / "_localweb_share_test"
        test_file = test_dir / f"test_{int(time.time())}.txt"
        try:
            test_dir.mkdir(parents=True, exist_ok=True)
            test_file.write_text("ok", encoding="utf-8")
            text = test_file.read_text(encoding="utf-8")
            try:
                test_file.unlink(missing_ok=True)
            except Exception:
                pass
            return {
                "ok": text == "ok",
                "message": "共享目录读写测试通过" if text == "ok" else "共享目录读写测试异常",
                "config": cfg,
                "test_path": str(test_file),
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": f"共享目录读写测试失败：{type(exc).__name__}: {exc}",
                "config": cfg,
                "test_path": str(test_file),
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

    def _queue_summary(self) -> Dict[str, Any]:
        try:
            result = self._run([self._exe("condor_q.exe")], timeout=10)
            return {
                "ok": bool(result.get("ok")),
                "text": "\n".join(x for x in [result.get("stdout", ""), result.get("stderr", "")] if x),
            }
        except Exception as exc:
            return {"ok": False, "text": str(exc)}

    def _slot_status(self) -> Dict[str, Any]:
        try:
            result = self._run([self._exe("condor_status.exe")], timeout=10)
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
            result = self._run([self._exe("condor_ping.exe"), "-table", "WRITE"], timeout=10)
            text = "\n".join(x for x in [result.get("stdout", ""), result.get("stderr", "")] if x)
            upper = text.upper()

            allow = "ALLOW" in upper
            has_ntsspi = "NTSSPI" in upper
            is_unauthenticated = "UNAUTHENTICATED" in upper or "UNMAPPED" in upper
            ok = result.get("returncode") == 0 and allow

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

            return {
                "ok": ok,
                "text": text,
                "returncode": result.get("returncode"),
                "auth_mode": auth_mode,
                "has_ntsspi": has_ntsspi,
                "allow": allow,
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
                "message": f"{type(exc).__name__}: {exc}",
            }

    def status(self) -> Dict[str, Any]:
        runtime = get_htcondor_runtime_status()
        install_result = self._install_result()
        service_state = ((runtime.get("installed_runtime") or {}).get("service") or {}).get("state")
        install_ok = bool(install_result.get("success") and install_result.get("status") == "fully_validated")
        service_ok = service_state == "running"
        slot = self._slot_status()
        ping = self._ping_write() if service_ok else {"ok": False, "text": "Condor 服务未运行"}
        mode = str(self.state.get("execution_mode") or "local")
        enabled = bool(mode == "htcondor" and install_ok and service_ok and slot.get("ok") and ping.get("ok"))
        nodes = self.node_status() if service_ok else {"ok": False, "text": "Condor 服务未运行", "items": []}
        return {
            "backend": "htcondor",
            "execution_mode": mode,
            "enabled": enabled,
            "machine": socket.gethostname(),
            "local_ips": self._local_ipv4_list(),
            "pool_role": str(self.state.get("pool_role") or "standalone"),
            "parent_ip": str(self.state.get("parent_ip") or ""),
            "bind_ip": str(self.state.get("bind_ip") or ""),
            "collector_port": int(self.state.get("collector_port") or 9618),
            "low_port": int(self.state.get("low_port") or 9700),
            "high_port": int(self.state.get("high_port") or 9800),
            "state_file": str(self.state_file),
            "runtime_dir": str(self.runtime_dir),
            "install_result": install_result,
            "runtime": runtime,
            "service_running": service_ok,
            "install_validated": install_ok,
            "slot_status": slot,
            "nodes": nodes,
            "queue": self._queue_summary() if service_ok else {"ok": False, "text": "Condor 服务未运行"},
            "ping": ping,
            "message": "HTCondor 可用于任务执行" if enabled else "HTCondor 未启用或未完全通过检查",
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

    $reconfigExe = Find-CondorExe 'condor_reconfig.exe'
    $restartExe = Find-CondorExe 'condor_restart.exe'

    # 第一优先级：使用 HTCondor 自己的 reconfig/restart。
    # 这样不会触发 Windows Stop-Service 的长时间阻塞，也不会刷屏输出
    # “正在等待服务 Condor 停止...”。
    if ($reconfigExe) {
        [void](Invoke-ExternalWithTimeout -FilePath $reconfigExe -Arguments '' -Seconds 12)
        Start-Sleep -Seconds 2
    }

    if ($restartExe) {
        [void](Invoke-ExternalWithTimeout -FilePath $restartExe -Arguments '-master' -Seconds 20)
        Start-Sleep -Seconds 6
    }

    $svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        throw '找不到 Condor 服务。'
    }

    if ($svc.Status -eq 'Stopped') {
        Start-Service -Name $serviceName -ErrorAction Stop
        if (-not (Wait-CondorServiceState -Wanted 'Running' -Seconds 45)) {
            throw 'Condor 服务启动超时。'
        }
    }
    elseif ($svc.Status -eq 'Running') {
        # 服务已经运行，说明配置重载/重启请求已完成或服务没有必要停止。
        # 再做一次轻量 reconfig，确保新配置被 master/schedd/startd 看见。
        if ($reconfigExe) {
            [void](Invoke-ExternalWithTimeout -FilePath $reconfigExe -Arguments '' -Seconds 12)
        }
    }
    else {
        # 兜底：服务处于 StopPending/StartPending 等中间状态时，只等待有限时间。
        # 不能再调用 Stop-Service，因为它会在部分机器上一直刷屏等待。
        if (-not (Wait-CondorServiceState -Wanted 'Running' -Seconds 20)) {
            $svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            $statusText = if ($null -eq $svc) { 'missing' } else { [string]$svc.Status }
            throw "Condor 服务处于 $statusText，系统已停止继续等待，避免管理员 PowerShell 卡死。请手动重启系统或在没有任务运行时重启 Condor 服务。"
        }
    }

    Start-Sleep -Seconds 5
} catch {
    $warningText = ($warnings -join '; ')
    if ($warningText) {
        throw "刷新 Condor 服务失败：$($_.Exception.Message)。附加信息：$warningText"
    }
    throw "刷新 Condor 服务失败：$($_.Exception.Message)"
}
""".strip()

        block_b64 = base64.b64encode(block.encode("utf-8")).decode("ascii")
        role_text = role
        return f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$resultPath = {self._ps_quote(str(self.runtime_dir / 'cluster_admin' / f'{role_text}_result.json'))}
$result = @{{ success = $false; message = ''; role = {self._ps_quote(role_text)}; stdout = ''; stderr = '' }}
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

    New-NetFirewallRule -DisplayName 'LocalWeb-HTCondor-Collector-9618' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 9618 -Profile Any -ErrorAction SilentlyContinue | Out-Null
    New-NetFirewallRule -DisplayName 'LocalWeb-HTCondor-Dynamic-{low_port}-{high_port}' -Direction Inbound -Action Allow -Protocol TCP -LocalPort {low_port}-{high_port} -Profile Any -ErrorAction SilentlyContinue | Out-Null

{restart_condor_script}

    $statusText = ''
    try {{
        $statusText = & 'C:/Condor/bin/condor_status.exe' -af Name Machine State Activity 2>&1 | Out-String
    }} catch {{
        $statusText = $_.Exception.Message
    }}

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
        args.extend(["-af", "Name", "Machine", "State", "Activity", "Cpus", "Memory"])
        result = self._run(args, timeout=15)
        text = "\n".join(x for x in [result.get("stdout", ""), result.get("stderr", ""), result.get("error", "")] if x).strip()
        items = []
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 6:
                items.append({
                    "name": parts[0],
                    "machine": parts[1],
                    "state": parts[2],
                    "activity": parts[3],
                    "cpus": parts[4],
                    "memory": parts[5],
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
        if result.get("success"):
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

    def join_parent_node(self, parent_ip: str, child_ip: str = "", low_port: int = 9700, high_port: int = 9800) -> Dict[str, Any]:
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
        if result.get("success"):
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
                result["message"] = "子节点已成功加入父节点，父节点已经能看到本机执行节点。"
            else:
                result["success"] = False
                result["message"] = (
                    "子节点配置已写入，但父节点还没有看到本机执行节点。"
                    "常见原因是父节点安全配置没有放行 ALLOW_ADVERTISE_STARTD，"
                    "或父节点 Condor 服务还没有完成重载。"
                )
        data = self.status()
        data["action_result"] = result
        data["message"] = result.get("message") or ("已加入父节点" if result.get("success") else "加入父节点失败")
        return data

    def leave_pool(self) -> Dict[str, Any]:
        script = self._make_pool_config_script(role="standalone")
        result = self._run_elevated_ps("standalone", script, timeout=180)
        if result.get("success"):
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
        """给 HTCondor 作业能访问的账号加权限。

        这里不让用户手动修权限。平台生成的 config.json、输出目录、运行目录，
        都由系统在提交作业前自动放开给本机 Users 组和 LocalWebCondor。
        """
        if os.name != "nt":
            return

        try:
            path = Path(path)
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
            if key in seen:
                continue
            seen.add(key)
            self._grant_one_path_for_job(p)

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

                        # 把 part_config.json 同目录下的子目录一起作为输入文件传输。
                        # 这些目录通常是按节点拆出来的 input、output、cm_files 等。
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
        # 共享目录模式下，HTCondor 子任务运行在独立会话中，
        # 不能直接继承用户在 PowerShell 中执行 net use 得到的凭据。
        # 所以在 run_job.cmd 启动 EXE 前，先主动登录父节点共享目录。
        try:
            shared_cfg = self.shared_io_config()
        except Exception:
            shared_cfg = {}

        share_unc = str(shared_cfg.get("unc_root") or "").strip()
        share_user = str(os.environ.get("LOCAL_WEB_HTCONDOR_SHARE_USER", "")).strip()
        share_password = str(os.environ.get("LOCAL_WEB_HTCONDOR_SHARE_PASSWORD", "")).strip()

        if shared_cfg.get("enabled") and share_unc and share_user and share_password:
            # 这里把 % 转成 %% ，避免 Windows bat 误解析。
            safe_unc = share_unc.replace("%", "%%")
            safe_user = share_user.replace("%", "%%")
            safe_password = share_password.replace("%", "%%")

            lines.extend([
                "echo [HTCONDOR] connect shared directory",
                f'net use "{safe_unc}" /delete /y >nul 2>nul',
                f'net use "{safe_unc}" "{safe_password}" /user:"{safe_user}" /persistent:no',
                "if errorlevel 1 (",
                "  echo [HTCONDOR-ERROR] failed to connect shared directory",
                "  exit /b 1326",
                ")",
            ])
        elif shared_cfg.get("enabled") and share_unc:
            lines.extend([
                "echo [HTCONDOR-WARN] shared directory enabled but no share credential configured",
            ])
        for key, value in (env or {}).items():
            key = str(key).strip()
            if not key or any(ch in key for ch in " =&|"):
                continue
            val = str(value).replace("%", "%%")
            lines.append(f"set {key}={val}")

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
            request_memory_mb = int(os.environ.get("LOCAL_WEB_HTCONDOR_REQUEST_MEMORY_MB", "8192") or "8192")
        except Exception:
            request_memory_mb = 8192
        request_memory_mb = max(1024, request_memory_mb)

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
request_cpus = 1
request_memory = {request_memory_mb}MB
request_disk = 1024MB
run_as_owner = false
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

        self.running_jobs[str(job_id)] = {
            "cluster_id": cluster_id,
            "job_dir": str(files["job_dir"]),
            "event_log": str(files["event_log"]),
        }

        if on_update is not None:
            try:
                on_update({
                    "type": "submitted",
                    "job_id": job_id,
                    "cluster_id": cluster_id,
                    "job_dir": str(files["job_dir"]),
                    "target_machine": str(target_machine or ""),
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
        last_stdout_len = 0
        last_stderr_len = 0
        last_event_len = 0
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

                # stream_output / stream_error 可能会把日志边运行边写回来。
                # 如果执行节点暂时不支持流式输出，也不影响最后读取完整日志。
                stdout_text = self._read_text_auto(files["stdout"])
                stderr_text = self._read_text_auto(files["stderr"])
                event_text = self._read_text_auto(files["event_log"])
                last_stdout_len = self._emit_live_text(on_update, job_id, "stdout", stdout_text, last_stdout_len)
                last_stderr_len = self._emit_live_text(on_update, job_id, "stderr", stderr_text, last_stderr_len)
                last_event_len = self._emit_live_text(on_update, job_id, "event", event_text, last_event_len)

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

                time.sleep(1.0)

            out, err = process.communicate(timeout=5)
            wait_stdout = out or ""
            wait_stderr = err or ""

            # 最后再读一次，避免最后几行输出没有推到前端。
            stdout_text = self._read_text_auto(files["stdout"])
            stderr_text = self._read_text_auto(files["stderr"])
            event_text = self._read_text_auto(files["event_log"])
            last_stdout_len = self._emit_live_text(on_update, job_id, "stdout", stdout_text, last_stdout_len)
            last_stderr_len = self._emit_live_text(on_update, job_id, "stderr", stderr_text, last_stderr_len)
            last_event_len = self._emit_live_text(on_update, job_id, "event", event_text, last_event_len)

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
