from __future__ import annotations
import sys
import base64
import json
import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import traceback
from pathlib import Path
from string import Formatter
from typing import Any, Dict, List, Optional
from datetime import datetime
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
import stat
import time
from .auth import (admin_reset_password,create_token,create_user,delete_user,get_current_user,get_security_question,load_users,register_user,remove_token,require_admin,reset_password_by_security_answer,sanitize_user,update_user_enabled,update_user_role,verify_user,)
from .task_manager import TaskManager
from .dask_cluster_manager import DaskClusterError, DaskClusterManager
from .htcondor_cluster_manager import HTCondorClusterError, HTCondorClusterManager

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MODULES_FILE = DATA_DIR / "modules.json"
TASKS_FILE = DATA_DIR / "tasks.json"
TOOLBARS_FILE = DATA_DIR / "toolbars.json"
DATA_FILES_FILE = DATA_DIR / "data_files.json"
INSTALLED_MODULES_DIR = BASE_DIR / "installed_modules"
INSTALLED_MODULES_DIR.mkdir(parents=True, exist_ok=True)
PYTHON_WHEELS_DIR = BASE_DIR / "python_wheels"
PYTHON_WHEELS_DIR.mkdir(parents=True, exist_ok=True)
STRICT_LOCAL_BINARY_PACKAGES = {"gdal","rasterio","pyproj","cartopy",}
PREFER_LOCAL_BINARY_PACKAGES = {"numpy","h5py","netcdf4",}
PYTHON_MODULE_ENVS_DIR = BASE_DIR / "module_envs"
PYTHON_MODULE_ENVS_DIR.mkdir(parents=True, exist_ok=True)
MODULE_DROP_DIR = PROJECT_ROOT / "module_drop"
MODULE_DROP_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR = BASE_DIR / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"

app = FastAPI(title="云和气溶胶反演系统API")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"],)
dask_cluster_manager = DaskClusterManager(BASE_DIR, project_root=PROJECT_ROOT)
htcondor_cluster_manager = HTCondorClusterManager(BASE_DIR, project_root=PROJECT_ROOT)
task_manager = TaskManager(TASKS_FILE)
task_manager.set_cluster_manager(dask_cluster_manager)
task_manager.set_htcondor_manager(htcondor_cluster_manager)

@app.get("/api/system/resources")
def api_system_resources(authorization: str | None = Header(default=None)):
    #读取当前电脑的CPU核数，根据CPU核数建议进程数和上限进程数并展示当前任务资源占用。
    get_current_user(authorization)
    task_manager.kick_scheduler()
    return task_manager.get_system_resource_info()
class LoginRequest(BaseModel):
    username: str
    password: str
    role: Optional[str] = None

class RegisterRequest(BaseModel):
    username: str
    password: str
    security_question: str = ""
    security_answer: str = ""

class ForgotPasswordResetRequest(BaseModel):
    username: str
    answer: str
    new_password: str


class AddUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    security_question: str = ""
    security_answer: str = ""


class UpdateUserRoleRequest(BaseModel):
    role: str

class UpdateUserEnabledRequest(BaseModel):
    enabled: bool
class ResetUserPasswordRequest(BaseModel):
    new_password: str


class ToolBarSaveRequest(BaseModel):
    key: str = ""
    label: str


class ToolBarUpdateRequest(BaseModel):
    key: str = ""
    label: str


class ModuleRunRequest(BaseModel):
    module_id: str
    inputs: Dict[str, Any] = {}
    parallel_workers: int = 1
class ModuleSaveRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: str = ""
    executable: str
    working_dir: str = "."
    config_mode: str = "none"
    command_template: List[str] = []
    inputs: List[Dict[str, Any]] = []
    tags: List[str] = []
    tool_type: str = "cloud"
    parallel: Dict[str, Any] = {}
    enabled: bool = True


class InstallLocalDropRequest(BaseModel):
    tool_type: str = "cloud"
    filename: str = ""


class FilePreviewRequest(BaseModel):
    path: str


class ParseParamJsonRequest(BaseModel):
    path: str
class InstallModuleFolderRequest(BaseModel):
    folder_path: str
    tool_type: str = "cloud"
    # C++ 模块按本地可执行文件安装，只需要 module.json、exe、resources 和运行时 deps。
    # 这里保留 runtime 字段，兼容旧前端调用。
    runtime: str = "cpp_native"
    auto_collect_dependencies: bool = True

class PythonFolderModuleUploadRequest(BaseModel):
    # 新模式：只传 Python 模块文件夹，后端自动找 python_module.json
    folder_path: str = ""
    config_filename: str = "python_module.json"

    # 旧模式：保留，避免影响之前的前端调用
    source_dir: str = ""
    param_json_path: str = ""
    module_id: str = ""
    module_name: str = ""
    entry_file: str = "main.py"
    tool_type: str = ""
    description: str = ""

class PythonModuleConfigRequest(BaseModel):
    path: str


class DaskInstallRequest(BaseModel):
    package_spec: str = ""
    upgrade: bool = False


class DaskStartHeadRequest(BaseModel):
    bind_ip: str = ""
    scheduler_port: int = 8786
    dashboard_port: int = 8787
    api_port: int = 8790
    worker_name: str = ""
    nworkers: int = 1
    nthreads: int = 1
    memory_limit: str = "4GB"
    shared_runtime_root: str = ""
    auto_install: bool = True


class DaskJoinRequest(BaseModel):
    head_ip: str
    api_port: int = 8790
    join_token: str
    worker_name: str = ""
    nworkers: int = 1
    nthreads: int = 1
    memory_limit: str = "4GB"
    auto_install: bool = True


class DaskExecutionModeRequest(BaseModel):
    mode: str = "local"
    shared_runtime_root: str = ""


class DaskFirewallRequest(BaseModel):
    api_port: int = 8790
    scheduler_port: int = 8786
    dashboard_port: int = 8787


class DaskSharedPathRequest(BaseModel):
    path: str = ""


class HTCondorExecutionModeRequest(BaseModel):
    mode: str = "local"


class HTCondorCreateParentRequest(BaseModel):
    bind_ip: str = ""
    low_port: int = 9700
    high_port: int = 9800


class HTCondorJoinParentRequest(BaseModel):
    parent_ip: str = ""
    child_ip: str = ""
    low_port: int = 9700
    high_port: int = 9800


# =========================
# Dask 分布式集群管理 API
# =========================
@app.get("/api/distributed/status")
def api_distributed_status(authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    status = dask_cluster_manager.status()
    if user.role != "admin":
        status["join_token"] = ""
    return status


@app.post("/api/distributed/install")
def api_distributed_install(
    payload: DaskInstallRequest,
    authorization: str | None = Header(default=None),
):
    # 普通用户也允许在当前节点安装 Dask，以便把该电脑加入集群。
    get_current_user(authorization)
    try:
        return dask_cluster_manager.install(
            package_spec=payload.package_spec,
            upgrade=payload.upgrade,
        )
    except DaskClusterError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/distributed/firewall")
def api_distributed_firewall(
    payload: DaskFirewallRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    return dask_cluster_manager.open_firewall(
        api_port=payload.api_port,
        scheduler_port=payload.scheduler_port,
        dashboard_port=payload.dashboard_port,
    )


@app.post("/api/distributed/start-head")
def api_distributed_start_head(
    payload: DaskStartHeadRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return dask_cluster_manager.start_head(
            bind_ip=payload.bind_ip,
            scheduler_port=payload.scheduler_port,
            dashboard_port=payload.dashboard_port,
            api_port=payload.api_port,
            worker_name=payload.worker_name,
            nworkers=payload.nworkers,
            nthreads=payload.nthreads,
            memory_limit=payload.memory_limit,
            shared_runtime_root=payload.shared_runtime_root,
            auto_install=payload.auto_install,
        )
    except DaskClusterError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/distributed/join-info")
def api_distributed_join_info(token: str):
    # 此接口供子节点后端执行加入握手；使用独立高强度随机令牌验证，
    # 不复用浏览器登录 token。
    try:
        return dask_cluster_manager.get_join_info(token)
    except DaskClusterError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@app.post("/api/distributed/join")
def api_distributed_join(
    payload: DaskJoinRequest,
    authorization: str | None = Header(default=None),
):
    # 普通用户可以把当前电脑作为 Worker 加入已有集群。
    get_current_user(authorization)
    try:
        return dask_cluster_manager.join_cluster(
            head_ip=payload.head_ip,
            api_port=payload.api_port,
            join_token=payload.join_token,
            worker_name=payload.worker_name,
            nworkers=payload.nworkers,
            nthreads=payload.nthreads,
            memory_limit=payload.memory_limit,
            auto_install=payload.auto_install,
        )
    except DaskClusterError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/distributed/leave")
def api_distributed_leave(authorization: str | None = Header(default=None)):
    # 普通用户可以让当前 Worker 退出集群。
    get_current_user(authorization)
    return dask_cluster_manager.leave_cluster()


@app.post("/api/distributed/stop")
def api_distributed_stop(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return dask_cluster_manager.stop_cluster()


@app.post("/api/distributed/execution-mode")
def api_distributed_execution_mode(
    payload: DaskExecutionModeRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return dask_cluster_manager.set_execution_mode(
            payload.mode,
            payload.shared_runtime_root,
        )
    except DaskClusterError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/distributed/test-shared-path")
def api_distributed_test_shared_path(
    payload: DaskSharedPathRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return dask_cluster_manager.test_shared_path(payload.path)
    except DaskClusterError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/distributed/logs")
def api_distributed_logs(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return dask_cluster_manager.tail_logs()


# =========================
# HTCondor 集群管理 API
# =========================
@app.get("/api/htcondor/status")
def api_htcondor_status(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return htcondor_cluster_manager.status()


@app.post("/api/htcondor/execution-mode")
def api_htcondor_execution_mode(
    payload: HTCondorExecutionModeRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return htcondor_cluster_manager.set_execution_mode(payload.mode)
    except HTCondorClusterError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/htcondor/smoke-test")
def api_htcondor_smoke_test(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        return htcondor_cluster_manager.smoke_test()
    except HTCondorClusterError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/htcondor/logs")
def api_htcondor_logs(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return htcondor_cluster_manager.tail_logs()


@app.get("/api/htcondor/nodes")
def api_htcondor_nodes(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return htcondor_cluster_manager.node_status()


@app.post("/api/htcondor/create-parent")
def api_htcondor_create_parent(
    payload: HTCondorCreateParentRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return htcondor_cluster_manager.create_parent_node(
            bind_ip=payload.bind_ip,
            low_port=payload.low_port,
            high_port=payload.high_port,
        )
    except HTCondorClusterError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/htcondor/join-parent")
def api_htcondor_join_parent(
    payload: HTCondorJoinParentRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return htcondor_cluster_manager.join_parent_node(
            parent_ip=payload.parent_ip,
            child_ip=payload.child_ip,
            low_port=payload.low_port,
            high_port=payload.high_port,
        )
    except HTCondorClusterError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/htcondor/leave-pool")
def api_htcondor_leave_pool(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        return htcondor_cluster_manager.leave_pool()
    except HTCondorClusterError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# 通用辅助函数
VALID_PARALLEL_MODES = {"none","auto","single_file","folder_chunks","module_internal","batch_group",}
DEFAULT_PARALLEL_PATTERNS = "*.tif;*.tiff;*.nc;*.hdf;*.h5"

# 初始默认工具栏。现在云反演 / 气溶胶反演也按普通动态工具栏处理，
# 只在第一次创建 toolbars.json 时写入；之后不会强制重新合并回来。
DEFAULT_TOOLBARS = [{"key": "cloud", "label": "云反演", "system": False},{"key": "aerosol", "label": "气溶胶反演", "system": False},]

CHINESE_PATH_PATTERN = re.compile(r"[\u4e00-\u9fff]")
PATH_LIKE_KEY_PATTERN = re.compile(
    r"(path|dir|file|folder|executable|working_dir|source_dir|outpath|out_dir|output|input|config|runtime_env|python_executable|python_path)",
    re.IGNORECASE,
)


def contains_chinese_text(value: Any) -> bool:
    return bool(CHINESE_PATH_PATTERN.search(str(value or "")))


def is_path_like_key(key: str) -> bool:
    return bool(PATH_LIKE_KEY_PATTERN.search(str(key or "")))


def is_path_like_value(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return (
        bool(re.match(r"^[A-Za-z]:[\\/]", text))
        or text.startswith("\\\\")
        or "\\" in text
        or "/" in text
        or text.startswith("./")
        or text.startswith("../")
        or text.startswith(".\\")
        or text.startswith("..\\")
    )


def collect_chinese_path_items(value: Any, prefix: str = "路径") -> list[dict]:
    items: list[dict] = []

    def walk(v: Any, key_path: str):
        if v is None:
            return

        if isinstance(v, str):
            text = v.strip()
            if text and contains_chinese_text(text) and (is_path_like_value(text) or is_path_like_key(key_path)):
                items.append({"field": key_path or "路径", "path": text})
            return

        if isinstance(v, dict):
            for key, item in v.items():
                next_key = f"{key_path}.{key}" if key_path else str(key)
                walk(item, next_key)
            return

        if isinstance(v, (list, tuple)):
            for idx, item in enumerate(v):
                walk(item, f"{key_path}[{idx}]")
            return

    walk(value, prefix)
    return items


def chinese_path_error_detail(items: list[dict]) -> dict:
    errors = []
    for item in items[:20]:
        field = str(item.get("field") or "路径")
        path = str(item.get("path") or "")
        errors.append({
            "field": field,
            "message": f"检测到中文路径：{path}",
            "suggestion": "当前系统暂不支持中文路径运行。请把数据、模块和输出目录放到纯英文路径下，例如 D:/H8/input、D:/H8/output。",
        })

    return {
        "message": "检测到中文路径，当前系统暂不支持中文路径。请改为纯英文路径后再继续。",
        "errors": errors,
        "suggestions": [
            "请将输入数据目录、输出目录、模块目录和 Python 解释器路径改为纯英文路径。",
            "推荐示例：D:/H8/input、D:/H8/output、D:/local_web_modules/H8_CLOUD_TYPE。",
        ],
    }


def raise_if_chinese_paths(value: Any, prefix: str = "路径"):
    items = collect_chinese_path_items(value, prefix)
    if items:
        raise HTTPException(status_code=400, detail=chinese_path_error_detail(items))


def add_chinese_path_errors_to_report(report: dict, value: Any, prefix: str = "路径") -> bool:
    items = collect_chinese_path_items(value, prefix)
    for item in items:
        _add_error(
            report,
            str(item.get("field") or prefix),
            f"检测到中文路径：{item.get('path')}",
            "当前系统暂不支持中文路径运行。请把该路径改为纯英文路径，例如 D:/H8/input 或 D:/local_web_modules/module_name。",
        )
    if items:
        _dedupe_report_items(report)
    return bool(items)


def to_project_relative_path(path: Path) -> str:
    """
    项目内部路径保存为相对于项目根目录的路径。
    例如：
    D:/xxx/local_web/backend/installed_modules/cth/main.exe
    保存成：
    backend/installed_modules/cth/main.exe

    如果路径不在项目目录内部，则保留绝对路径。
    """
    resolved = path.resolve()

    try:
        return resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def resolve_packaged_module_path(raw_value: str, module_id: str, target_dir: Path, default_path: Path) -> Path:
    """
    把 module.json 里的 executable / working_dir 转成安装后的真实路径。
    优先支持相对路径；如果 module.json 里误写了旧电脑绝对路径，则尽量兜底处理。
    """
    raw_value = str(raw_value or "").strip()

    if not raw_value or raw_value == ".":
        return default_path

    p = Path(raw_value)

    # 正常情况：module.json 里写的是相对路径
    if not p.is_absolute():
        return target_dir / p

    # 兜底情况：module.json 里写了绝对路径
    # 如果路径里包含模块 id，例如 .../installed_modules/cth/xxx.exe
    # 则取模块 id 后面的部分。
    parts = list(p.parts)
    if module_id in parts:
        idx = parts.index(module_id)
        rel_parts = parts[idx + 1:]
        if rel_parts:
            return target_dir.joinpath(*rel_parts)

    # executable 是绝对路径时，最后兜底用文件名
    if p.suffix:
        return target_dir / p.name

    # working_dir 是绝对路径时，最后兜底用模块根目录
    return default_path
def normalize_tool_key(value: str) -> str:
    """把工具栏 key 规范化，允许中文名称，但过滤路径和分隔符。"""
    value = (value or "").strip()
    if not value:
        return ""
    value = value.replace("..", "_").replace("/", "_").replace("\\", "_")
    value = "_".join(value.split())
    return value


def make_toolbar_key(label: str) -> str:
    raw = normalize_tool_key(label)
    if raw:
        return raw
    return f"tool_{datetime.now().strftime('%Y%m%d%H%M%S')}"


def ensure_toolbars_file():
    if not TOOLBARS_FILE.exists():
        TOOLBARS_FILE.write_text(
            json.dumps(DEFAULT_TOOLBARS, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_toolbars() -> List[dict]:
    ensure_toolbars_file()
    try:
        raw = json.loads(TOOLBARS_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raw = list(DEFAULT_TOOLBARS)
    except Exception:
        raw = list(DEFAULT_TOOLBARS)

    # 不再把 DEFAULT_TOOLBARS 每次强制合并进来。
    # 这样 cloud / aerosol 删除后不会自动复活，真正变成动态工具栏。
    merged: Dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = normalize_tool_key(str(item.get("key", "")))
        label = str(item.get("label") or key).strip()
        if not key or not label:
            continue
        merged[key] = {
            "key": key,
            "label": label,
            "system": False,
        }

    result = list(merged.values())
    result.sort(key=lambda x: (0 if x.get("key") in {"cloud", "aerosol"} else 1, x.get("label", "")))
    return result

def save_toolbars(toolbars: List[dict]):
    cleaned: List[dict] = []
    seen = set()
    for item in toolbars:
        if not isinstance(item, dict):
            continue
        key = normalize_tool_key(str(item.get("key", "")))
        label = str(item.get("label") or key).strip()
        if not key or not label or key in seen:
            continue
        seen.add(key)
        # 所有工具栏都按动态工具栏保存，不再写 system=True。
        cleaned.append({"key": key, "label": label, "system": False})

    TOOLBARS_FILE.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def add_toolbar(key: str, label: str) -> dict:
    label = (label or "").strip()
    if not label:
        raise ValueError("工具类型名称不能为空")

    key = normalize_tool_key(key) or make_toolbar_key(label)
    toolbars = load_toolbars()
    if any(t.get("key") == key for t in toolbars):
        raise ValueError("工具类型已存在")

    item = {"key": key, "label": label, "system": False}
    toolbars.append(item)
    save_toolbars(toolbars)
    return item


def update_toolbar(old_key: str, new_key: str, label: str) -> dict:
    old_key = normalize_tool_key(old_key)
    if not old_key:
        raise ValueError("工具类型标识不能为空")

    label = (label or "").strip()
    if not label:
        raise ValueError("工具类型名称不能为空")

    toolbars = load_toolbars()
    found = None
    for item in toolbars:
        if item.get("key") == old_key:
            found = item
            break

    # 有些历史模块可能只有 tool_type，没有在 toolbars.json 中登记；
    # 编辑时也允许把这个虚拟工具栏补登记后更新。
    if not found:
        modules = load_modules()
        if any(module.get("tool_type") == old_key for module in modules):
            found = {"key": old_key, "label": old_key, "system": False}
            toolbars.append(found)
        else:
            raise ValueError("工具栏不存在")

    candidate_key = normalize_tool_key(new_key) or old_key

    if candidate_key != old_key and any(t.get("key") == candidate_key for t in toolbars):
        raise ValueError("新的工具类型标识已存在")

    updated = {
        "key": candidate_key,
        "label": label,
        "system": False,
    }

    new_toolbars = []
    replaced = False
    for item in toolbars:
        if item.get("key") == old_key and not replaced:
            new_toolbars.append(updated)
            replaced = True
        elif item.get("key") != old_key:
            new_toolbars.append(item)

    if not replaced:
        new_toolbars.append(updated)

    save_toolbars(new_toolbars)

    # 修改 key 时，同步迁移该工具栏下模块的 tool_type。
    if candidate_key != old_key:
        modules = load_modules()
        changed = False
        for module in modules:
            if module.get("tool_type") == old_key:
                module["tool_type"] = candidate_key
                changed = True
        if changed:
            save_modules(modules)

    return updated

def delete_toolbar(key: str) -> dict:
    key = normalize_tool_key(key)
    if not key:
        raise ValueError("工具类型标识不能为空")

    toolbars = load_toolbars()
    exists_in_toolbar_file = any(item.get("key") == key for item in toolbars)

    modules = load_modules()
    affected_modules = [module for module in modules if module.get("tool_type") == key]

    if not exists_in_toolbar_file and not affected_modules:
        raise ValueError("工具栏不存在")

    remaining_toolbars = [item for item in toolbars if item.get("key") != key]

    moved_count = 0
    target_tool_type = ""
    if affected_modules:
        # 删除有模块的工具栏时，不删除模块；自动移动到其它工具栏。
        # 如果没有其它工具栏，则自动创建“未分类”。
        if remaining_toolbars:
            target_tool_type = remaining_toolbars[0].get("key") or "uncategorized"
        else:
            target_tool_type = "uncategorized"
            remaining_toolbars.append({"key": target_tool_type, "label": "未分类", "system": False})

        for module in modules:
            if module.get("tool_type") == key:
                module["tool_type"] = target_tool_type
                moved_count += 1

        save_modules(modules)

    save_toolbars(remaining_toolbars)
    return {
        "deleted_key": key,
        "moved_count": moved_count,
        "target_tool_type": target_tool_type,
    }

def ensure_toolbar_exists(key: str, label: str | None = None):
    key = normalize_tool_key(key) or "cloud"
    toolbars = load_toolbars()
    if any(t.get("key") == key for t in toolbars):
        return
    toolbars.append({"key": key, "label": label or key, "system": False})
    save_toolbars(toolbars)


def guess_module_tool_type(module: dict) -> str:
    explicit = normalize_tool_key(str(module.get("tool_type") or module.get("category") or ""))
    if explicit:
        return explicit

    text = " ".join(
        str(x or "")
        for x in [
            module.get("id"),
            module.get("name"),
            module.get("description"),
            " ".join(module.get("tags") or []),
        ]
    ).lower()

    if any(k in text for k in ["aod", "aerosol", "气溶胶", "h8", "polar", "偏振"]):
        return "aerosol"
    if any(k in text for k in ["cloud", "云", "cloud_type", "cth"]):
        return "cloud"
    return "cloud"


def normalize_parallel_config(module: dict) -> dict:
    raw = module.get("parallel")
    if not isinstance(raw, dict):
        raw = {}
    cfg = {
        "mode": raw.get("mode") or module.get("parallel_mode") or "auto",
        "input_key": raw.get("input_key") or module.get("parallel_input_key") or "",
        "output_key": raw.get("output_key") or module.get("parallel_output_key") or "",
        "file_patterns": raw.get("file_patterns") or module.get("parallel_file_patterns") or "*.tif;*.tiff;*.nc;*.hdf;*.h5",
        "output_suffix": raw.get("output_suffix") or module.get("parallel_output_suffix") or ".tif",

        # 性能优化参数：
        # files_per_job 控制 folder_chunks 模式下一个子进程处理几个文件。
        # 值越大，EXE 启动/模型加载次数越少；值越小，负载均衡越好。
        "files_per_job": raw.get("files_per_job") or module.get("parallel_files_per_job") or 1,

        # 预留字段：后续如果需要按 workers * chunk_multiplier 生成更多小块，可以直接使用。
        "chunk_multiplier": raw.get("chunk_multiplier") or module.get("parallel_chunk_multiplier") or 1,
    }
    mode = str(cfg.get("mode") or "auto").strip() or "auto"
    cfg["mode"] = mode if mode in VALID_PARALLEL_MODES else "auto"
    cfg["input_key"] = str(cfg.get("input_key") or "")
    cfg["output_key"] = str(cfg.get("output_key") or "")
    cfg["file_patterns"] = str(cfg.get("file_patterns") or "*.tif;*.tiff;*.nc;*.hdf;*.h5")
    cfg["output_suffix"] = str(cfg.get("output_suffix") or ".tif")

    try:
        cfg["files_per_job"] = max(1, int(cfg.get("files_per_job") or 1))
    except Exception:
        cfg["files_per_job"] = 1

    try:
        cfg["chunk_multiplier"] = max(1, int(cfg.get("chunk_multiplier") or 1))
    except Exception:
        cfg["chunk_multiplier"] = 1

    return cfg


def normalize_module_record(module: dict) -> dict:
    if not isinstance(module, dict):
        return {}
    copied = dict(module)
    copied["tool_type"] = guess_module_tool_type(copied)
    copied["parallel"] = normalize_parallel_config(copied)
    # 旧版本的并行平铺字段不再写回，避免管理页面变乱。
    for key in [
        "parallel_mode",
        "parallel_input_key",
        "parallel_output_key",
        "parallel_file_patterns",
        "parallel_output_suffix",
    ]:
        copied.pop(key, None)
    return copied


def ensure_modules_file():
    if not MODULES_FILE.exists():
        MODULES_FILE.write_text("[]", encoding="utf-8")


def recover_modules_from_installed_modules() -> List[dict]:
    recovered: List[dict] = []
    seen: set[str] = set()
    manifest_names = ["module.json", "executable_module.json", "python_module.json"]

    for module_dir in sorted(INSTALLED_MODULES_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not module_dir.is_dir():
            continue

        manifest_path = next((module_dir / name for name in manifest_names if (module_dir / name).exists()), None)
        if manifest_path is None:
            continue

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue

        module_id = str(raw.get("id") or raw.get("module_id") or module_dir.name).strip()
        if not module_id or module_id in seen:
            continue

        module = dict(raw)
        module["id"] = module_id
        module["name"] = module.get("name") or module.get("module_name") or module_id
        module["description"] = module.get("description") or ""
        module["enabled"] = module.get("enabled", True)

        entry = str(module.get("entry") or module.get("entry_file") or "").strip()
        executable = str(module.get("executable") or "").strip()
        if not entry and executable:
            entry = Path(executable).name
        if entry:
            module["entry"] = entry
            module["executable"] = to_project_relative_path(module_dir / entry)
        elif executable:
            exe_path = Path(executable)
            module["executable"] = (
                to_project_relative_path(module_dir / exe_path.name)
                if not exe_path.is_absolute()
                else executable
            )

        module["working_dir"] = to_project_relative_path(module_dir)
        module["config_mode"] = module.get("config_mode") or "json"
        module["command_template"] = module.get("command_template") or ["{executable}", "{config_json}"]
        module["inputs"] = module.get("inputs") if isinstance(module.get("inputs"), list) else []
        module["tags"] = module.get("tags") if isinstance(module.get("tags"), list) else []
        module["tool_type"] = normalize_tool_key(str(module.get("tool_type") or module.get("category") or "")) or guess_module_tool_type(module)
        module["parallel"] = normalize_parallel_config(module)

        seen.add(module_id)
        recovered.append(normalize_module_record(module))

    return recovered


def load_modules() -> List[dict]:
    ensure_modules_file()
    try:
        data = json.loads(MODULES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [normalize_module_record(item) for item in data if isinstance(item, dict)]
        return []
    except Exception:
        recovered = recover_modules_from_installed_modules()
        if recovered:
            try:
                save_modules(recovered)
            except Exception:
                pass
        return recovered

def sanitize_filename(name: str) -> str:
    name = Path(name).name
    return name.replace("..", "_").replace("/", "_").replace("\\", "_")

def save_modules(modules: List[dict]):
    MODULES_FILE.write_text(
        json.dumps(modules, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_module(module_id: str) -> Optional[dict]:
    for module in load_modules():
        if module.get("id") == module_id:
            return module
    return None


def is_tif_path(path: Path) -> bool:
    return path.suffix.lower() in {".tif", ".tiff"}


def _preview_valid_mask(array, nodata=None, prefer_nonzero: bool = True):
    """生成预览用的有效像元掩膜。

    很多遥感产品会把背景、无效区域写成 0、-9999、65535 等值。
    如果直接用全图 2%-98% 分位拉伸，背景 0 占比过高时整张预览会被压成黑色。
    """
    import numpy as np

    arr = array.astype("float32", copy=False)
    finite = np.isfinite(arr)
    mask = finite.copy()

    if nodata is not None:
        try:
            nd = float(nodata)
            if np.isfinite(nd):
                mask &= ~np.isclose(arr, nd, rtol=0, atol=1e-6)
        except Exception:
            pass

    for fill_value in (-999999.0, -99999.0, -9999.0, -999.0, -32768.0, 32767.0, 65535.0):
        mask &= ~np.isclose(arr, fill_value, rtol=0, atol=1e-6)

    if not mask.any():
        return mask

    if prefer_nonzero:
        valid = arr[mask]
        zero_ratio = float(np.mean(np.isclose(valid, 0.0, rtol=0, atol=1e-12))) if valid.size else 0.0
        nonzero_mask = mask & ~np.isclose(arr, 0.0, rtol=0, atol=1e-12)
        if zero_ratio >= 0.50 and nonzero_mask.any():
            return nonzero_mask

    return mask


def _array_preview_stats(array, nodata=None) -> dict:
    import numpy as np

    arr = array.astype("float32", copy=False)
    finite = np.isfinite(arr)
    base_mask = finite.copy()

    if nodata is not None:
        try:
            nd = float(nodata)
            if np.isfinite(nd):
                base_mask &= ~np.isclose(arr, nd, rtol=0, atol=1e-6)
        except Exception:
            pass

    stretch_mask = _preview_valid_mask(arr, nodata=nodata, prefer_nonzero=True)
    stats: dict[str, Any] = {
        "shape": list(arr.shape),
        "nodata": nodata,
        "finite_pixels": int(finite.sum()),
        "valid_pixels": int(base_mask.sum()),
        "stretch_pixels": int(stretch_mask.sum()),
    }

    if base_mask.any():
        vals = arr[base_mask]
        stats.update({
            "min": float(np.nanmin(vals)),
            "max": float(np.nanmax(vals)),
            "mean": float(np.nanmean(vals)),
            "p2": float(np.nanpercentile(vals, 2)),
            "p98": float(np.nanpercentile(vals, 98)),
            "zero_ratio": float(np.mean(np.isclose(vals, 0.0, rtol=0, atol=1e-12))),
        })
    if stretch_mask.any():
        vals = arr[stretch_mask]
        stats.update({
            "stretch_min": float(np.nanmin(vals)),
            "stretch_max": float(np.nanmax(vals)),
            "stretch_p2": float(np.nanpercentile(vals, 2)),
            "stretch_p98": float(np.nanpercentile(vals, 98)),
        })
    return stats


def _normalize_to_uint8(array, nodata=None, prefer_nonzero: bool = True):
    import numpy as np

    arr = array.astype("float32", copy=False)
    mask = _preview_valid_mask(arr, nodata=nodata, prefer_nonzero=prefer_nonzero)
    if not mask.any():
        return np.zeros(arr.shape, dtype="uint8")

    valid = arr[mask]
    lo, hi = np.nanpercentile(valid, [2, 98])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.nanmin(valid))
        hi = float(np.nanmax(valid))

    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        out = np.zeros(arr.shape, dtype="uint8")
        out[mask] = 180
        return out

    out = np.zeros(arr.shape, dtype="float32")
    out[mask] = (arr[mask] - lo) / (hi - lo) * 255.0
    out = np.clip(out, 0, 255)
    return out.astype("uint8")


def _colorize_gray(gray):
    """单波段数据转为更容易辨识的伪彩色预览。"""
    from PIL import Image

    img = Image.fromarray(gray, mode="L")
    try:
        from PIL import ImageOps
        return ImageOps.colorize(img, black="#000000", mid="#1d4ed8", white="#fff7a8")
    except Exception:
        return img.convert("RGB")


def _clean_cli_text(value: str) -> str:
    return (value or "").strip().replace("\r", " ").replace("\n", " ")

def _find_gdal_command(command_name: str) -> str:
    """查找系统 GDAL 命令。Windows 下 gdalinfo 可用但 Python 没装 osgeo 时会走这里。"""
    candidate = shutil.which(command_name)
    if candidate:
        return candidate
    raise HTTPException(
        status_code=500,
        detail=(
            f"系统未找到 {command_name} 命令。当前 Python 没有 osgeo 模块时，"
            f"需要把 GDAL 命令行工具加入 PATH，或给后端 Python 安装 GDAL/osgeo。"
        ),
    )

def _run_gdal_cli(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"未找到 GDAL 命令: {args[0]}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail=f"GDAL 命令执行超时: {' '.join(args)}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"GDAL 命令执行失败: {exc}")


def _read_tif_meta_with_gdalinfo_cli(tif_path: Path) -> dict:
    """用 gdalinfo -json 读取基础元数据。失败也不影响预览。"""
    try:
        gdalinfo = _find_gdal_command("gdalinfo")
        result = _run_gdal_cli([gdalinfo, "-json", str(tif_path)], timeout=60)
        if result.returncode != 0:
            return {"gdalinfo_error": _clean_cli_text(result.stderr or result.stdout)}
        data = json.loads(result.stdout or "{}")
        size = data.get("size") or []
        bands = data.get("bands") or []
        meta: dict[str, Any] = {
            "gdalinfo_driver": (data.get("driverShortName") or data.get("driverLongName") or ""),
            "width": int(size[0]) if len(size) > 0 else None,
            "height": int(size[1]) if len(size) > 1 else None,
            "bands": len(bands),
        }
        band_types = []
        nodata_values = []
        for band in bands[:8]:
            if band.get("type"):
                band_types.append(str(band.get("type")))
            if band.get("noDataValue") is not None:
                nodata_values.append(band.get("noDataValue"))
        if band_types:
            meta["band_types"] = band_types
        if nodata_values:
            meta["nodata_values"] = nodata_values
        return meta
    except Exception as exc:
        return {"gdalinfo_error": str(exc)}
def _render_tif_with_gdal_cli(tif_path: Path, meta: dict[str, Any] | None = None) -> dict:
    """
    Python 环境没有 osgeo 时，调用系统 gdal_translate 把 GeoTIFF 转成 PNG。
    这样只要命令行 gdalinfo/gdal_translate 可用，就能预览多波段遥感 TIFF。
    """
    meta = dict(meta or {})
    cli_meta = _read_tif_meta_with_gdalinfo_cli(tif_path)
    meta.update({k: v for k, v in cli_meta.items() if v is not None})

    band_count = int(meta.get("bands") or 1)
    gdal_translate = _find_gdal_command("gdal_translate")

    with tempfile.TemporaryDirectory(prefix="tif_preview_") as tmpdir:
        out_png = Path(tmpdir) / "preview.png"

        cmd = [
            gdal_translate,
            "-q",
            "-of",
            "PNG",
            "-ot",
            "Byte",
            "-scale",
            "-outsize",
            "1600",
            "0",
        ]

        if band_count >= 3:
            # 多波段遥感 TIFF 默认取 1/2/3 波段做 RGB 预览。
            # 后续如需严格真彩色/假彩色，可再在前端加波段选择。
            cmd.extend(["-b", "1", "-b", "2", "-b", "3"]);
            meta["render_mode"] = "gdal_translate_cli_rgb_1_2_3"
        else:
            cmd.extend(["-b", "1"]);
            meta["render_mode"] = "gdal_translate_cli_single_band"

        cmd.extend([str(tif_path), str(out_png)])

        result = _run_gdal_cli(cmd, timeout=120)
        if result.returncode != 0 or not out_png.exists():
            err = _clean_cli_text(result.stderr or result.stdout)
            raise HTTPException(status_code=500, detail=f"gdal_translate 预览失败: {err}")

        meta["preview_engine"] = "gdal_translate_cli"
        meta["gdal_command"] = " ".join(cmd)
        return {"png": out_png.read_bytes(), "meta": meta}
def _png_data_url(png_bytes: bytes) -> str:
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"

def _resize_preview_image(image, max_size: int = 1600):
    """
    限制预览图最大边长，避免大 tif 直接撑爆浏览器。
    """
    try:
        image.thumbnail((max_size, max_size))
    except Exception:
        pass
    return image


def _array_to_preview_png(array, meta: dict | None = None) -> dict:
    """
    把二维/三维数组转成 PNG bytes。
    不依赖 gdal_translate。
    """
    import numpy as np
    from PIL import Image

    meta = dict(meta or {})

    arr = np.asarray(array)
    arr = np.squeeze(arr)

    if arr.ndim == 0:
        raise ValueError("数组没有可预览的二维数据")

    # 如果是多波段，尽量转成 H,W,C
    if arr.ndim == 3:
        # 常见遥感格式：bands, height, width
        if arr.shape[0] <= 8 and arr.shape[1] > 8 and arr.shape[2] > 8:
            if arr.shape[0] >= 3:
                bands = [
                    _normalize_to_uint8(arr[0], nodata=None, prefer_nonzero=True),
                    _normalize_to_uint8(arr[1], nodata=None, prefer_nonzero=True),
                    _normalize_to_uint8(arr[2], nodata=None, prefer_nonzero=True),
                ]
                rgb = np.dstack(bands)
                image = Image.fromarray(rgb, mode="RGB")
                meta["render_mode"] = "python_array_bands_first_rgb"
            else:
                gray = _normalize_to_uint8(arr[0], nodata=None, prefer_nonzero=True)
                image = _colorize_gray(gray)
                meta["render_mode"] = "python_array_bands_first_single"
        # 常见图片格式：height, width, channels
        elif arr.shape[-1] in {3, 4}:
            if arr.shape[-1] == 4:
                arr = arr[:, :, :3]
            bands = [
                _normalize_to_uint8(arr[:, :, 0], nodata=None, prefer_nonzero=True),
                _normalize_to_uint8(arr[:, :, 1], nodata=None, prefer_nonzero=True),
                _normalize_to_uint8(arr[:, :, 2], nodata=None, prefer_nonzero=True),
            ]
            rgb = np.dstack(bands)
            image = Image.fromarray(rgb, mode="RGB")
            meta["render_mode"] = "python_array_channels_last_rgb"
        else:
            # 兜底：取第一个切片
            gray = _normalize_to_uint8(arr[0], nodata=None, prefer_nonzero=True)
            image = _colorize_gray(gray)
            meta["render_mode"] = "python_array_first_slice"
    elif arr.ndim == 2:
        gray = _normalize_to_uint8(arr, nodata=None, prefer_nonzero=True)
        image = _colorize_gray(gray)
        meta.update(_array_preview_stats(arr, nodata=None))
        meta["render_mode"] = "python_array_single_band"
    else:
        raise ValueError(f"暂不支持 {arr.ndim} 维数组预览")

    image = _resize_preview_image(image)

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    meta["preview_engine"] = meta.get("preview_engine") or "python_array"
    meta["preview_width"], meta["preview_height"] = image.size
    return {"png": buf.getvalue(), "meta": meta}


def render_tif_to_preview_result(tif_path: Path) -> dict:
    """
    后台 tif 预览：
    1. 优先用 Python osgeo.gdal；
    2. 没有 osgeo 时，尝试 tifffile；
    3. 再尝试 Pillow；
    4. 最后才尝试系统 gdal_translate。
    """
    if not tif_path.exists() or not tif_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    if tif_path.suffix.lower() not in {".tif", ".tiff"}:
        raise HTTPException(status_code=400, detail="只支持预览 tif/tiff 文件")

    meta: dict[str, Any] = {
        "name": tif_path.name,
        "size": tif_path.stat().st_size,
        "suffix": tif_path.suffix.lower(),
    }

    # 方案一：Python osgeo.gdal
    try:
        import numpy as np
        from osgeo import gdal

        ds = gdal.Open(str(tif_path))
        if ds is None:
            raise RuntimeError("GDAL 无法打开该 tif")

        width = int(ds.RasterXSize)
        height = int(ds.RasterYSize)
        band_count = int(ds.RasterCount or 1)

        max_size = 1600
        scale = min(1.0, max_size / max(width, height)) if max(width, height) else 1.0
        out_w = max(1, int(width * scale))
        out_h = max(1, int(height * scale))

        meta.update({
            "width": width,
            "height": height,
            "bands": band_count,
            "preview_engine": "python_osgeo_gdal",
        })

        if band_count >= 3:
            bands = []
            for band_index in (1, 2, 3):
                band = ds.GetRasterBand(band_index)
                nodata = band.GetNoDataValue()
                arr = band.ReadAsArray(buf_xsize=out_w, buf_ysize=out_h)
                bands.append(_normalize_to_uint8(arr, nodata=nodata, prefer_nonzero=True))

            rgb = np.dstack(bands)

            from PIL import Image
            image = Image.fromarray(rgb, mode="RGB")
            meta["render_mode"] = "python_osgeo_rgb"
        else:
            band = ds.GetRasterBand(1)
            nodata = band.GetNoDataValue()
            arr = band.ReadAsArray(buf_xsize=out_w, buf_ysize=out_h)

            from PIL import Image
            gray = _normalize_to_uint8(arr, nodata=nodata, prefer_nonzero=True)
            image = _colorize_gray(gray)
            meta.update(_array_preview_stats(arr, nodata=nodata))
            meta["render_mode"] = "python_osgeo_single_band"

        image = _resize_preview_image(image)

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        meta["preview_width"], meta["preview_height"] = image.size
        return {"png": buf.getvalue(), "meta": meta}

    except Exception as gdal_exc:
        meta["python_osgeo_error"] = str(gdal_exc)

    # 方案二：tifffile
    try:
        import tifffile

        arr = tifffile.imread(str(tif_path))
        meta["preview_engine"] = "python_tifffile"
        try:
            meta["array_shape"] = list(arr.shape)
        except Exception:
            pass

        return _array_to_preview_png(arr, meta)

    except Exception as tifffile_exc:
        meta["tifffile_error"] = str(tifffile_exc)

    # 方案三：Pillow
    try:
        import numpy as np
        from PIL import Image

        image = Image.open(tif_path)

        try:
            arr = np.asarray(image)
            meta["preview_engine"] = "python_pillow_array"
            meta["pillow_mode"] = image.mode
            return _array_to_preview_png(arr, meta)
        except Exception:
            image = Image.open(tif_path)
            image = _resize_preview_image(image)
            if image.mode not in {"L", "RGB", "RGBA"}:
                image = image.convert("RGB")

            buf = io.BytesIO()
            image.save(buf, format="PNG")

            meta["preview_engine"] = "python_pillow"
            meta["pillow_mode"] = image.mode
            meta["preview_width"], meta["preview_height"] = image.size
            return {"png": buf.getvalue(), "meta": meta}

    except Exception as pillow_exc:
        meta["pillow_error"] = str(pillow_exc)

    # 方案四：最后才尝试 gdal_translate
    try:
        return _render_tif_with_gdal_cli(tif_path, meta)
    except Exception as cli_exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "tif 后台预览失败：Python osgeo、tifffile、Pillow、gdal_translate 都无法生成预览。"
                f" osgeo错误: {meta.get('python_osgeo_error')};"
                f" tifffile错误: {meta.get('tifffile_error')};"
                f" Pillow错误: {meta.get('pillow_error')};"
                f" GDAL命令错误: {cli_exc}"
            ),
        )
def get_username_from_user(user) -> str:
    """
    兼容 get_current_user() 返回 dict 或对象两种情况。
    """
    if isinstance(user, dict):
        return str(user.get("username") or "")
    return str(getattr(user, "username", "") or "")
OUTPUT_ROLE_VALUES = {"output", "out", "result", "结果", "输出"}


def data_file_belongs_to_user(item: dict, username: str) -> bool:
    """
    判断 data_files.json 中的一条文件记录是否属于当前用户。
    旧数据没有 owner_username 时默认不显示，避免串用户。
    """
    owner = str(item.get("owner_username") or "").strip()
    return bool(owner) and owner == str(username)


def load_visible_data_files_for_user(username: str) -> tuple[list[dict], list[dict]]:
    """
    返回：
    1. all_items：清理过不存在文件后的全量 data_files
    2. visible_items：当前用户可见的文件列表

    注意：visible_items 的 id 是给前端用的用户内序号；
    _source_index 是它在 all_items 里的真实位置，给 preview/delete/reveal 用。
    """
    all_items = load_data_files()
    kept_items: list[dict] = []
    visible_items: list[dict] = []

    for item in all_items:
        if not isinstance(item, dict):
            continue

        role = str(item.get("io_role") or item.get("data_role") or "output").strip().lower()
        if role not in OUTPUT_ROLE_VALUES:
            continue

        path = Path(str(item.get("path") or ""))
        if not path.exists() or not path.is_file():
            continue

        row = dict(item)

        try:
            stat = path.stat()
            row["size"] = stat.st_size
            row["size_text"] = format_file_size(stat.st_size)
            row["file_name"] = row.get("file_name") or row.get("name") or path.name
            row["name"] = row.get("name") or path.name
            row["io_role"] = "output"
            row["data_role"] = "output"
            row["modified_at"] = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        except Exception:
            pass

        source_index = len(kept_items)
        kept_items.append(row)

        if data_file_belongs_to_user(row, username):
            visible_row = dict(row)
            visible_row["_source_index"] = source_index
            visible_items.append(visible_row)

    # 全量记录用全局 id 保存
    for idx, item in enumerate(kept_items):
        item["id"] = idx

    kept_items.sort(key=lambda x: x.get("modified_at", ""), reverse=True)

    # 排序后重新计算 source_index
    source_by_key = {}
    for idx, item in enumerate(kept_items):
        item["id"] = idx
        key = f"{item.get('owner_username', '')}::{item.get('path', '')}"
        source_by_key[key] = idx

    visible_items = []
    for item in kept_items:
        if not data_file_belongs_to_user(item, username):
            continue

        row = dict(item)
        key = f"{row.get('owner_username', '')}::{row.get('path', '')}"
        row["_source_index"] = source_by_key.get(key, -1)
        visible_items.append(row)

    # 前端看到的是当前用户自己的 0,1,2...
    for idx, item in enumerate(visible_items):
        item["id"] = idx

    save_data_files(kept_items)
    return kept_items, visible_items


def get_user_data_file_by_visible_id(file_id: int, username: str) -> tuple[list[dict], int, dict]:
    all_items, visible_items = load_visible_data_files_for_user(username)

    if file_id < 0 or file_id >= len(visible_items):
        raise HTTPException(status_code=404, detail="文件不存在")

    visible_item = visible_items[file_id]
    source_index = int(visible_item.get("_source_index", -1))

    if source_index < 0 or source_index >= len(all_items):
        raise HTTPException(status_code=404, detail="文件不存在")

    item = all_items[source_index]

    if not data_file_belongs_to_user(item, username):
        raise HTTPException(status_code=404, detail="文件不存在")

    return all_items, source_index, item

def get_data_file_by_id_with_permission(file_id: int, user) -> tuple[list[dict], int, dict]:
    username = get_username_from_user(user)

    if isinstance(user, dict):
        role = str(user.get("role") or "")
    else:
        role = str(getattr(user, "role", "") or "")

    # 管理员：按全局 id 访问全部文件
    if role == "admin":
        all_items, _ = load_visible_data_files_for_user(username)

        if file_id < 0 or file_id >= len(all_items):
            raise HTTPException(status_code=404, detail="文件不存在")

        item = all_items[file_id]
        return all_items, file_id, item

    # 普通用户：只能访问自己的 visible id
    return get_user_data_file_by_visible_id(file_id, username)
@app.post("/api/tasks/run")
def api_run_module(payload: ModuleRunRequest, authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    username = get_username_from_user(user)
    if not username:
        raise HTTPException(status_code=401, detail="未登录")

    module = get_module(payload.module_id)
    if not module:
        raise HTTPException(status_code=404, detail="模块不存在")
    if not module.get("enabled", True):
        raise HTTPException(status_code=400, detail="模块已禁用")

    # 1. 合并输入参数
    inputs = merge_admin_fixed_inputs(module, payload.inputs or {})
    inputs = coerce_json_marked_inputs(module, inputs)

    # 当前版本不支持中文路径：运行前统一拦截，避免 netCDF4/xarray/GDAL/HDF5 在 Windows 下读取失败。
    raise_if_chinese_paths(inputs, f"模块 {module.get('name') or module.get('id') or ''} 输入参数")

    requested_workers = clamp_parallel_workers(
        payload.parallel_workers,
        task_manager.max_process_slots,
    )

    # 2. 必填校验
    for field in module.get("inputs", []) or []:
        key = field.get("key")
        if not key:
            continue
        if field.get("control_only") is True:
            continue

        required = bool(field.get("required", False))
        if required and (key not in inputs or inputs.get(key) in ("", None)):
            raise HTTPException(status_code=400, detail=f"缺少必填参数: {key}")

    # 3. control_only 不写入 config
    for field in module.get("inputs", []) or []:
        if field.get("control_only") is True:
            key = field.get("key")
            if key:
                inputs.pop(key, None)

    # 4. 输出目录只创建，不改写用户路径
    for field in module.get("inputs", []) or []:
        key = field.get("key")
        if not key or not is_output_field(field):
            continue

        value = str(inputs.get(key) or "").strip()
        if not value:
            continue

        field_type = str(field.get("type", "")).lower()
        p = Path(value)

        if field_type == "dir_path":
            p.mkdir(parents=True, exist_ok=True)
        elif field_type == "file_path" and p.parent:
            p.parent.mkdir(parents=True, exist_ok=True)

    # 5. 根据 CPU/内存/磁盘/模型大小自动降低进程数
    workers, adjust_report = auto_adjust_parallel_workers(
        module,
        inputs,
        requested_workers,
    )
    inputs = apply_parallel_adjustment_to_inputs(inputs, adjust_report)

    # 6. 统一决定运行方式
    run_mode = resolve_run_parallel_mode(module, inputs, workers)
    output_paths = collect_output_paths_from_inputs(module, inputs)

    # 6.1 C++/多输入目录批处理模式
    if run_mode == "batch_group":
        jobs, batch_output_paths = build_batch_jobs_for_module(module, inputs, workers)
        task = task_manager.submit_batch_group(
            module_id=module["id"],
            module_name=module.get("name", module["id"]),
            jobs=jobs,
            max_parallel=workers,
            owner_username=username,
        )

        scan_paths = batch_output_paths or output_paths
        if scan_paths:
            start_data_file_scan_after_task(
                task["id"],
                module,
                scan_paths,
                owner_username=username,
            )

        return task

    # 6.2 平台级并行拆分
    if run_mode == "platform_split":
        jobs = prepare_parallel_jobs(module, inputs, workers)

        if len(jobs) > 1:
            task_inputs = dict(inputs)
            task_inputs["parallel_workers"] = workers
            task_inputs["_parallel_workers"] = workers
            task_inputs["_parallel_job_count"] = len(jobs)
            task_inputs["_parallel_mode"] = str(normalize_parallel_config(module).get("mode") or "auto")

            task = task_manager.submit_parallel_module_task(
                module_id=module["id"],
                module_name=module.get("name", module["id"]),
                jobs=jobs,
                inputs=task_inputs,
                max_workers=workers,
                owner_username=username,
            )

            if output_paths:
                start_data_file_scan_after_task(
                    task["id"],
                    module,
                    output_paths,
                    owner_username=username,
                )

            return task

    # 6.3 模块内部并行 / 普通单进程
    # module_internal 不拆任务，只把 parallel_workers 写入 config.json。
    command, working_dir, runtime_env = build_runtime_for_module(module, inputs)

    task = task_manager.submit_module_task(
        module_id=module["id"],
        module_name=module.get("name", module["id"]),
        command=command,
        inputs=inputs,
        working_dir=working_dir,
        env=runtime_env,
        owner_username=username,
    )

    if output_paths:
        start_data_file_scan_after_task(
            task["id"],
            module,
            output_paths,
            owner_username=username,
        )

    return task
def upsert_module(module_data: dict):
    module_data = normalize_module_record(module_data)
    ensure_toolbar_exists(module_data.get("tool_type") or "cloud")
    modules = load_modules()
    found = False
    for i, module in enumerate(modules):
        if module.get("id") == module_data.get("id"):
            modules[i] = module_data
            found = True
            break
    if not found:
        modules.append(module_data)
    save_modules(modules)


def _is_path_inside(child: Path, parent: Path) -> bool:
    """
    判断 child 是否在 parent 目录内部，避免误删项目外部目录。
    """
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _remove_path_safely(path: Path, allowed_roots: list[Path]) -> dict:
    """
    只允许删除指定安全目录下的文件或文件夹。
    """
    path = path.resolve()

    if not path.exists():
        return {
            "path": str(path),
            "status": "missing",
        }

    if not any(_is_path_inside(path, root) for root in allowed_roots):
        raise HTTPException(
            status_code=400,
            detail=f"拒绝删除非模块目录路径: {path}",
        )

    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=False)
        else:
            path.unlink()

        return {
            "path": str(path),
            "status": "deleted",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"删除模块本地文件失败: {path}，原因: {exc}",
        )

def remove_module(module_id: str) -> dict:
    """
    删除模块：
    1. 优先从 modules.json 中删除模块注册信息；
    2. 再尝试删除模块本地文件夹；
    3. 再尝试删除 Python 独立环境；
    4. 对于旧版本模块，还会根据 working_dir/source_dir/entry_script/param_template 反推可删除目录；
    5. 不删除任务记录、不删除用户输出结果。
    """
    module_id = str(module_id or "").strip()
    if not module_id:
        raise HTTPException(status_code=400, detail="模块 ID 不能为空")

    modules = load_modules()

    target_module = None
    new_modules = []

    # 关键：只要 id 匹配，就从 modules.json 里移除
    for module in modules:
        if str(module.get("id") or "") == module_id:
            target_module = module
        else:
            new_modules.append(module)

    if not target_module:
        return {
            "removed": False,
            "module_id": module_id,
            "deleted_paths": [],
            "message": "modules.json 中没有找到该模块注册信息",
        }

    safe_module_id = sanitize_filename(module_id).strip()
    if not safe_module_id:
        raise HTTPException(status_code=400, detail="模块 ID 非法")

    deleted_paths = []

    allowed_roots = [
        INSTALLED_MODULES_DIR,
        PYTHON_MODULE_ENVS_DIR,
    ]

    def add_candidate_path(raw_value):
        """把模块配置里的路径转成可删除候选路径。"""
        text = str(raw_value or "").strip()
        if not text:
            return None

        path = Path(text)

        # 相对路径按项目根目录解析
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        else:
            path = path.resolve()

        return path

    candidate_paths = []

    # 标准模块目录
    candidate_paths.append(INSTALLED_MODULES_DIR / safe_module_id)

    # Python 独立环境目录
    candidate_paths.append(PYTHON_MODULE_ENVS_DIR / safe_module_id)

    # 旧 Python 源码模块可能注册了 working_dir/source_dir
    for key in [
        "working_dir",
        "source_dir",
    ]:
        path = add_candidate_path(target_module.get(key))
        if path:
            candidate_paths.append(path)

    # entry_script / param_template 是文件路径，删除时提升到它们所在目录，
    # 避免只删一个 py/json 文件后留下空 release 目录。
    for key in [
        "entry_script",
        "param_template",
    ]:
        path = add_candidate_path(target_module.get(key))
        if path:
            candidate_paths.append(path.parent)

    # 去重，并且只允许删除 installed_modules 和 module_envs 内部内容
    seen = set()
    safe_candidates = []

    for path in candidate_paths:
        try:
            resolved = Path(path).resolve()
        except Exception:
            continue

        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)

        # 只允许删除系统模块目录和环境目录下的内容，防止误删用户数据
        allowed = False
        for root in allowed_roots:
            try:
                resolved.relative_to(root.resolve())
                allowed = True
                break
            except ValueError:
                continue

        if allowed:
            safe_candidates.append(resolved)

    # 关键修复：先写 modules.json，避免文件删除失败后注册信息残留
    save_modules(new_modules)

    # 再删除本地文件。失败也不回滚 modules.json，避免前端继续显示坏模块。
    for path in safe_candidates:
        try:
            if not path.exists():
                deleted_paths.append({
                    "path": str(path),
                    "status": "missing",
                })
                continue

            if path.is_dir():
                shutil.rmtree(path, ignore_errors=False)
                deleted_paths.append({
                    "path": str(path),
                    "status": "deleted",
                })
            else:
                path.unlink()
                deleted_paths.append({
                    "path": str(path),
                    "status": "deleted",
                })

        except Exception as exc:
            deleted_paths.append({
                "path": str(path),
                "status": "delete_failed",
                "error": f"{type(exc).__name__}: {exc}",
            })

    return {
        "removed": True,
        "module_id": module_id,
        "deleted_paths": deleted_paths,
        "message": "模块注册信息已从 modules.json 删除，本地文件已尽量清理",
    }


def format_command(template: List[str], values: Dict[str, Any]) -> List[str]:
    formatted = []
    for item in template:
        try:
            formatted.append(item.format(**values))
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"命令模板缺少参数: {e}")
    return formatted


def extract_template_fields(command_template: List[str]) -> List[str]:
    fields = set()
    for item in command_template:
        for _, field_name, _, _ in Formatter().parse(item):
            if field_name:
                fields.add(field_name)
    return list(fields)


def resolve_module_dir(module: dict) -> Path:
    working_dir = module.get("working_dir", ".")
    project_root = BASE_DIR.parent
    module_dir = Path(working_dir)
    if not module_dir.is_absolute():
        module_dir = (project_root / module_dir).resolve()
    else:
        module_dir = module_dir.resolve()
    return module_dir



def to_module_json_value(value: Any) -> Any:
    """把写给模块 exe 的 config 值整理成更兼容的 JSON。

    Windows 下很多 C/C++ 程序用简单字符串方式解析 JSON，不会处理反斜杠转义。
    因此写给模块的路径统一使用正斜杠 /，同时递归处理 dict/list。
    """
    if isinstance(value, dict):
        return {str(k): to_module_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_module_json_value(v) for v in value]
    if isinstance(value, tuple):
        return [to_module_json_value(v) for v in value]
    if isinstance(value, Path):
        return str(value).replace("\\", "/")
    if isinstance(value, str):
        return value.replace("\\", "/")
    return value


def resolve_input_value_for_module(module: dict, field: dict, value: Any) -> Any:
    if value in (None, ""):
        return value
    if field.get("path_mode") == "relative_to_module" and field.get("type") in {"file_path", "dir_path"}:
        p = Path(str(value))
        if not p.is_absolute():
            return str((resolve_module_dir(module) / p).resolve())
    return value


def merge_admin_fixed_inputs(module: dict, inputs: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(inputs or {})
    for field in module.get("inputs", []) or []:
        key = field.get("key")
        if not key:
            continue
        visible = field.get("visible_to_user", True) is not False
        admin_fixed = bool(field.get("admin_fixed", False)) or not visible
        has_user_value = key in merged and merged.get(key) not in ("", None)
        default_value = field.get("default")

        if admin_fixed or not has_user_value:
            if default_value not in ("", None):
                merged[key] = resolve_input_value_for_module(module, field, default_value)
        else:
            merged[key] = resolve_input_value_for_module(module, field, merged.get(key))
    return merged




def _resolve_runtime_path_no_copy(raw_value: str | Path, project_root: Path | None = None) -> Path:
    project_root = project_root or PROJECT_ROOT
    p = Path(str(raw_value or ""))
    if not p.is_absolute():
        p = (project_root / p).resolve()
    else:
        p = p.resolve()
    return p


def is_python_source_runtime_module(module: dict, exe_path: Path) -> bool:
    """判断是否应按 Python 源码模块运行。

    兼容旧模块：有些历史模块没有 runtime_type=python_venv，
    但有 source_dir / entry_file / python_env_dir / entry_script。
    这类模块运行时一律不能复制 source，只能直接读取 installed_modules 里的源码和固定资源。
    """
    runtime_type = str(module.get("runtime_type") or "").lower()
    if runtime_type == "python_venv":
        return True
    if module.get("python_env_dir"):
        return True
    if module.get("source_dir") and (module.get("entry_file") or module.get("entry_script")):
        return True
    try:
        name = exe_path.name.lower()
        if name in {"python.exe", "python", "python3", "python3.exe"} and (module.get("source_dir") or module.get("entry_script")):
            return True
    except Exception:
        pass
    return False


def resolve_python_source_dir_no_copy(module: dict, project_root: Path) -> Path:
    source_raw = module.get("source_dir") or module.get("working_dir") or ""
    if not source_raw and module.get("entry_script"):
        return _resolve_runtime_path_no_copy(module.get("entry_script"), project_root).parent
    source_dir = _resolve_runtime_path_no_copy(source_raw, project_root)
    if not source_dir.exists() or not source_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Python 源码目录不存在: {source_dir}")
    return source_dir


def resolve_python_entry_no_copy(module: dict, source_dir: Path, project_root: Path) -> Path:
    entry_script_raw = str(module.get("entry_script") or "").strip()
    if entry_script_raw:
        entry_path = _resolve_runtime_path_no_copy(entry_script_raw, project_root)
        if entry_path.exists() and entry_path.is_file():
            return entry_path

    entry_name = str(module.get("entry_file") or "main.py").strip() or "main.py"
    entry_path = source_dir / entry_name
    if not entry_path.exists():
        candidates = list(source_dir.rglob(Path(entry_name).name))
        if candidates:
            entry_path = candidates[0]
    if not entry_path.exists() or not entry_path.is_file():
        raise HTTPException(status_code=400, detail=f"Python 入口脚本不存在: {entry_name}")
    return entry_path

def coerce_json_marked_inputs(module: dict, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """把自动识别出的复杂 JSON 参数从字符串还原成 dict/list/number/bool。"""
    result = dict(inputs or {})
    for field in module.get("inputs", []) or []:
        key = field.get("key")
        if not key or key not in result:
            continue

        if field.get("json_value") is True and isinstance(result.get(key), str):
            raw = result.get(key, "")
            if raw == "":
                continue
            try:
                result[key] = json.loads(raw)
            except Exception:
                # 用户在文本框里填的不是合法 JSON 时，保留原字符串，避免任务直接崩溃。
                result[key] = raw

    return result

def build_runtime_for_module(module: dict, inputs: Dict[str, Any]) -> tuple[list[str], str, dict]:
    import os
    import json
    import tempfile
    from pathlib import Path

    executable = module.get("executable") or module.get("entry") or ""
    working_dir = module.get("working_dir", ".")
    config_mode = (module.get("config_mode") or "none").lower()
    command_template = module.get("command_template") or []

    if not executable:
        raise HTTPException(status_code=400, detail="模块未配置 executable")

    # 动态获取项目根目录，即 local_module_web_system 文件夹
    # BASE_DIR 是 backend 目录，所以 BASE_DIR.parent 就是项目根目录
    project_root = BASE_DIR.parent

    # 1. 解析模块工作目录
    module_dir = Path(working_dir)
    if not module_dir.is_absolute():
        module_dir = (project_root / module_dir).resolve()
    else:
        module_dir = module_dir.resolve()

    # 2. 解析可执行文件路径
    exe_path = Path(executable)
    if not exe_path.is_absolute():
        # 如果 executable 写的是 backend/installed_modules/xxx/xxx.exe
        # 就基于项目根目录拼接
        exe_path = (project_root / exe_path).resolve()
    else:
        exe_path = exe_path.resolve()

    if not exe_path.exists():
        raise HTTPException(status_code=400, detail=f"可执行文件不存在: {exe_path}")

    if not module_dir.exists():
        raise HTTPException(status_code=400, detail=f"工作目录不存在: {module_dir}")

    values = dict(inputs)
    values["executable"] = str(exe_path)
    values["python_executable"] = str(exe_path)
    values["working_dir"] = str(module_dir)

    entry_script = str(module.get("entry_script") or "").strip()
    if entry_script:
        entry_path = Path(entry_script)
        if not entry_path.is_absolute():
            entry_path = (project_root / entry_path).resolve()
        else:
            entry_path = entry_path.resolve()
        if not entry_path.exists():
            raise HTTPException(status_code=400, detail=f"Python 入口脚本不存在: {entry_path}")
        values["entry_script"] = str(entry_path)

    runtime_env = os.environ.copy()

    # 强制优先加载模块自己的依赖目录
    # 先读 module.json/modules.json 里的 dependency_dirs，没有的话默认只加 deps
    dependency_dirs = module.get("dependency_dirs") or ["deps"]

    dll_search_dirs = [str(module_dir)]

    for dep in dependency_dirs:
        dep_path = (module_dir / dep).resolve()
        if dep_path.exists() and dep_path.is_dir():
            dll_search_dirs.append(str(dep_path))

    # 去重，保持顺序
    seen = set()
    ordered_dirs = []
    for p in dll_search_dirs:
        if p not in seen:
            ordered_dirs.append(p)
            seen.add(p)

    runtime_type_for_env = str(module.get("runtime_type") or "").lower()

    if runtime_type_for_env == "python_venv":
        try:
            venv_python = exe_path
            venv_root = venv_python.parent.parent

            venv_dirs = [
                venv_python.parent,
                venv_root,
                venv_root / "DLLs",
                venv_root / "Library" / "bin",
            ]

            site_packages = venv_root / "Lib" / "site-packages"

            if site_packages.exists():
                for libs_dir in site_packages.glob("*.libs"):
                    if libs_dir.is_dir():
                        venv_dirs.append(libs_dir)

                for libs_dir in [
                    site_packages / "h5py.libs",
                    site_packages / "numpy.libs",
                    site_packages / "osgeo",
                ]:
                    if libs_dir.exists() and libs_dir.is_dir():
                        venv_dirs.append(libs_dir)

            for item in venv_dirs:
                if item.exists() and item.is_dir():
                    resolved_item = str(item.resolve())
                    if resolved_item not in seen:
                        ordered_dirs.append(resolved_item)
                        seen.add(resolved_item)
        except Exception:
            pass

    runtime_env["PATH"] = ";".join(ordered_dirs + [runtime_env.get("PATH", "")])
    runtime_env["MODULE_DLL_DIRS"] = ";".join(ordered_dirs)

    def _as_int(value: Any, default: int = 1) -> int:
        try:
            return int(value or default)
        except Exception:
            return default

    def _runtime_thread_count_for_child() -> int:
        """根据“平台并行进程数”和“总计算线程预算”分配子进程内部线程数。

        以前这里固定为 1，稳定但性能偏保守；现在改成：
        - 平台拆分/batch 子任务：总线程预算 / 并行池大小；
        - 普通单进程/module_internal：按用户设置或默认上限；
        - 可通过环境变量 LOCAL_WEB_TOTAL_COMPUTE_THREADS、LOCAL_WEB_MAX_THREADS_PER_CHILD 调整。
        """
        cpu_count = max(1, int(os.cpu_count() or 1))

        explicit = os.environ.get("LOCAL_WEB_CHILD_NUM_THREADS", "").strip()
        if explicit:
            return max(1, min(cpu_count, _as_int(explicit, 1)))

        mode = str(
            values.get("_parallel_mode")
            or normalize_parallel_config(module).get("mode")
            or "auto"
        ).strip()

        requested = _as_int(
            values.get("_requested_parallel_workers")
            or values.get("_effective_parallel_workers")
            or values.get("_parallel_workers")
            or values.get("parallel_workers"),
            1,
        )

        pool_size = _as_int(
            values.get("_parallel_pool_size")
            or values.get("_parallel_total")
            or values.get("_batch_total")
            or requested,
            1,
        )

        # 总计算线程预算：默认只用一部分 CPU，避免多进程 + 多线程把电脑打满。
        total_budget = _as_int(
            os.environ.get("LOCAL_WEB_TOTAL_COMPUTE_THREADS"),
            max(1, min(cpu_count, max(2, cpu_count // 2))),
        )

        # 单个子进程最多线程数。
        max_threads_per_child = _as_int(
            os.environ.get("LOCAL_WEB_MAX_THREADS_PER_CHILD"),
            4,
        )

        is_platform_child = bool(
            values.get("_parallel_index")
            or values.get("_parallel_total")
            or values.get("_parallel_pool_size")
            or values.get("_batch_index")
            or values.get("_batch_total")
        )

        if mode in {"single_file", "folder_chunks", "batch_group"} or is_platform_child:
            per_child = max(1, total_budget // max(1, pool_size))
            return max(1, min(cpu_count, max_threads_per_child, per_child))

        if mode in {"none", "module_internal"}:
            return max(1, min(cpu_count, max_threads_per_child, requested))

        return 1

    runtime_threads = _runtime_thread_count_for_child()

    # 控制数值计算库线程数。注意：这是“单个子进程内部线程数”，不是平台子进程数量。
    runtime_env["OPENBLAS_NUM_THREADS"] = str(runtime_threads)
    runtime_env["OMP_NUM_THREADS"] = str(runtime_threads)
    runtime_env["GOTO_NUM_THREADS"] = str(runtime_threads)
    runtime_env["MKL_NUM_THREADS"] = str(runtime_threads)
    runtime_env["NUMEXPR_NUM_THREADS"] = str(runtime_threads)
    runtime_env["LOCAL_WEB_RUNTIME_THREADS"] = str(runtime_threads)
    # 统一 Python 子进程输出编码，避免中文路径、tqdm 进度条在日志窗口乱码
    runtime_env["PYTHONIOENCODING"] = "utf-8"
    runtime_env["PYTHONUTF8"] = "1"
    runtime_env["PYTHONUNBUFFERED"] = "1"

    # tqdm 默认会输出 unicode 进度条块字符，在 Windows 日志管道里容易乱码；
    # 让 tqdm 使用 ASCII 进度条，例如 ####，避免出现 ��。


    # 便于排查 DLL 搜索路径
    runtime_env["MODULE_DLL_DIRS"] = ";".join(ordered_dirs)

    if config_mode in {"json", "json_file", "config_json"}:
        runtime_task_dir = Path(tempfile.mkdtemp(prefix="job_", dir=str(RUNTIME_DIR)))
        module_config = to_module_json_value(inputs)

        runtime_type = str(module.get("runtime_type") or "").lower()

        if is_python_source_runtime_module(module, exe_path):
            source_dir = resolve_python_source_dir_no_copy(module, project_root)
            entry_path = resolve_python_entry_no_copy(module, source_dir, project_root)

            # 绝对禁止复制固定资源：不创建 runtime/job_xxx/source。
            # pkl/model/resources/LUT 等固定文件只保留在 installed_modules/<模块>/source 中。
            # 每个任务只生成自己的 config.json，入口脚本直接从 source_dir 读取。
            config_path = runtime_task_dir / "config.json"
            config_path.write_text(
                json.dumps(module_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            values["config_json"] = str(config_path)
            values["config_path"] = str(config_path)
            values["runtime_dir"] = str(runtime_task_dir)
            values["entry_script"] = str(entry_path)
            values["source_dir"] = str(source_dir)
            values["module_source_dir"] = str(source_dir)

            module_dir = source_dir

            runtime_env["RUNTIME_SOURCE_MODE"] = "installed_source_no_copy_forced"
            runtime_env["RUNTIME_FIXED_RESOURCE_POLICY"] = "read_from_installed_modules_only"
            runtime_env["RUNTIME_SHARED_SOURCE_DIR"] = str(source_dir)
            runtime_env["RUNTIME_CONFIG_ONLY_DIR"] = str(runtime_task_dir)
            runtime_env["LOCAL_WEB_MODULE_SOURCE_DIR"] = str(source_dir)
            runtime_env["LOCAL_WEB_MODULE_RUNTIME_DIR"] = str(runtime_task_dir)
            runtime_env["LOCAL_WEB_NO_FIXED_RESOURCE_COPY"] = "1"
            # 防御性清理：如果旧代码或热重载残留创建了 runtime/job_xxx/source，立即删除，
            # 防止模型/资源文件再次进入 runtime。
            stale_source = runtime_task_dir / "source"
            if stale_source.exists():
                shutil.rmtree(stale_source, ignore_errors=True)

            template_text = " ".join(str(x) for x in (command_template or []))
            if (not command_template) or ("{entry_script}" not in template_text and "entry_script" not in template_text):
                command_template = ["{executable}", "{entry_script}", "{config_json}"]

        else:
            config_path = runtime_task_dir / "config.json"
            config_path.write_text(
                json.dumps(module_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            values["config_json"] = str(config_path)
            values["config_path"] = str(config_path)
            values["runtime_dir"] = str(runtime_task_dir)

            if not command_template:
                command_template = ["{executable}", "{config_json}"]
    else:
        if not command_template:
            command_template = ["{executable}"]

    command = format_command(command_template, values)

    # 强制 cwd 为模块目录
    return command, str(module_dir), runtime_env
def build_python_source_to_exe(
    source_zip: Path,
    module_id: str,
    entry_file: str = "main.py",
) -> tuple[Path, Path]:
    """
    把用户上传的 Python 源码 zip 打包成 exe。

    返回：
    - module_root: 解压后的源码目录
    - exe_path: 生成的 exe 路径
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="python_module_"))

    try:
        source_dir = temp_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(source_zip, "r") as zf:
            zf.extractall(source_dir)

        entry_path = source_dir / entry_file
        if not entry_path.exists():
            candidates = list(source_dir.rglob(entry_file))
            if candidates:
                entry_path = candidates[0]

        if not entry_path.exists():
            raise HTTPException(
                status_code=400,
                detail=f"未找到 Python 入口文件：{entry_file}",
            )

        requirements_path = source_dir / "requirements.txt"

        # 可选：如果源码包里有 requirements.txt，先安装依赖
        if requirements_path.exists():
            install_requirements_with_local_wheels(
                python_exe=Path(sys.executable),
                requirements_path=requirements_path,
                work_dir=source_dir,
            )

        dist_dir = temp_dir / "dist"
        build_dir = temp_dir / "build"
        spec_dir = temp_dir / "spec"

        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            module_id,
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(build_dir),
            "--specpath",
            str(spec_dir),
            str(entry_path),
        ]

        result = subprocess.run(
            cmd,
            cwd=str(source_dir),
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Python 代码打包失败：\n"
                    + (result.stderr or result.stdout or "未知错误")
                ),
            )

        exe_path = dist_dir / f"{module_id}.exe"
        if not exe_path.exists():
            raise HTTPException(
                status_code=400,
                detail="打包完成但未找到生成的 exe 文件",
            )

        return source_dir, exe_path

    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=400,
            detail=f"安装 Python 依赖失败：{e}",
        )


def _resolve_local_json_path(raw_path: str) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="参数 JSON 文件路径不能为空")

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    else:
        path = path.resolve()

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=400, detail=f"参数 JSON 文件不存在: {path}")
    if path.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="请选择 .json 参数文件")
    return path
def resolve_python_module_config_from_folder(folder_path: str, config_filename: str = "python_module.json") -> Path:
    raw = str(folder_path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="请选择 Python 模块文件夹")

    folder = Path(raw).expanduser()
    if not folder.is_absolute():
        folder = (PROJECT_ROOT / folder).resolve()
    else:
        folder = folder.resolve()

    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Python 模块文件夹不存在: {folder}")

    filename = sanitize_filename(config_filename or "python_module.json")
    config_path = folder / filename

    if not config_path.exists() or not config_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=(
                f"该文件夹下没有找到 {filename}: {config_path}\n"
                "请确认 Python 模块文件夹中包含 python_module.json、config.json、requirements.txt 和入口 .py 文件。"
            ),
        )

    return config_path

def load_param_json_file(raw_path: str) -> dict:
    path = _resolve_local_json_path(raw_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        data = json.loads(path.read_text(encoding="gbk"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"参数 JSON 解析失败: {exc}")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="参数 JSON 顶层必须是对象，例如 {\"input_dir\": \"...\"}")
    return data

def _resolve_path_relative_to_config(raw_path: str, config_path: Path) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="路径不能为空")

    path = Path(raw).expanduser()

    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    else:
        path = path.resolve()

    return path


def load_python_module_config(raw_path: str) -> tuple[dict, Path]:
    config_path = _resolve_local_json_path(raw_path)

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        data = json.loads(config_path.read_text(encoding="gbk"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Python 模块配置 JSON 解析失败: {exc}")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Python 模块配置 JSON 顶层必须是对象")

    # 兼容两种写法：
    # 1. 直接平铺字段：module_id/source_dir/...
    # 2. 写在 module 下面：{"module": {...}}
    module_cfg = data.get("module") if isinstance(data.get("module"), dict) else data

    module_id = str(module_cfg.get("module_id") or module_cfg.get("id") or "").strip()
    module_name = str(module_cfg.get("module_name") or module_cfg.get("name") or "").strip()
    source_dir_raw = str(module_cfg.get("source_dir") or module_cfg.get("python_source_dir") or "").strip()
    entry_file = str(module_cfg.get("entry_file") or "main.py").strip()
    tool_type = str(module_cfg.get("tool_type") or "").strip()
    description = str(module_cfg.get("description") or "").strip()
    python_executable = str(
        module_cfg.get("python_executable")
        or module_cfg.get("python")
        or module_cfg.get("python_path")
        or ""
    ).strip()

    python_env_mode = str(
        module_cfg.get("python_env_mode")
        or module_cfg.get("env_mode")
        or "create_venv"
    ).strip().lower() or "create_venv"

    if python_env_mode not in {"create_venv", "existing"}:
        raise HTTPException(
            status_code=400,
            detail="python_env_mode 只支持 create_venv 或 existing",
        )

    if python_executable:
        python_executable = str(_resolve_path_relative_to_config(python_executable, config_path))

    if python_env_mode == "existing" and not python_executable:
        raise HTTPException(
            status_code=400,
            detail="existing 模式必须指定 python_executable",
        )
    if not module_id:
        raise HTTPException(status_code=400, detail="Python 模块配置 JSON 缺少 module_id")
    if not module_name:
        raise HTTPException(status_code=400, detail="Python 模块配置 JSON 缺少 module_name")
    if not source_dir_raw:
        raise HTTPException(status_code=400, detail="Python 模块配置 JSON 缺少 source_dir")

    source_dir = _resolve_path_relative_to_config(source_dir_raw, config_path)

    if not source_dir.exists() or not source_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Python 源码文件夹不存在: {source_dir}")

    param_template = module_cfg.get("param_template")
    param_json_path = None

    if isinstance(param_template, dict):
        param_json = param_template
    else:
        param_json_raw = str(module_cfg.get("param_json_path") or module_cfg.get("config_json") or "").strip()
        if not param_json_raw:
            # 默认找源码目录下的 config.json
            param_json_path = source_dir / "config.json"
        else:
            param_json_path = _resolve_path_relative_to_config(param_json_raw, config_path)

        if not param_json_path.exists() or not param_json_path.is_file():
            raise HTTPException(status_code=400, detail=f"参数 JSON 文件不存在: {param_json_path}")

        param_json = load_param_json_file(str(param_json_path))

    return {
        "module_id": module_id,
        "module_name": module_name,
        "source_dir": str(source_dir),
        "entry_file": entry_file,
        "tool_type": tool_type,
        "description": description,
        "param_json_path": str(param_json_path) if param_json_path else "",
        "param_json": param_json,
        "python_executable": python_executable,
        "python_env_mode": python_env_mode,
    }, config_path
def make_python_install_release_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def remove_tree_windows_safe(path: Path, title: str = "删除目录"):
    path = Path(path)

    if not path.exists():
        return

    def on_rm_error(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    last_error = None

    for _ in range(6):
        try:
            shutil.rmtree(path, onerror=on_rm_error)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.4)

    raise HTTPException(
        status_code=409,
        detail=(
            f"{title}失败：{path}\n"
            f"原因：{last_error}\n\n"
            "通常是文件正在被 Python、GDAL、GIS 软件或资源管理器预览占用。"
        ),
    )


def copy_or_link_python_module_file(src: str, dst: str):
    """
    安装 Python 模块源码时：
    - 普通 .py/.json/.txt 直接复制；
    - 大模型、tif、pkl 等优先硬链接，减少磁盘占用；
    - 硬链接失败再复制。
    """
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    link_suffixes = {
        ".pkl", ".joblib", ".model", ".onnx",
        ".h5", ".hdf5", ".npy", ".npz",
        ".pt", ".pth", ".tif", ".tiff", ".nc", ".hdf",
    }

    try:
        should_link = (
            src_path.suffix.lower() in link_suffixes
            or src_path.stat().st_size >= 20 * 1024 * 1024
        )
    except Exception:
        should_link = False

    if should_link:
        try:
            if dst_path.exists():
                dst_path.unlink()
            os.link(src_path, dst_path)
            return str(dst_path)
        except OSError:
            pass

    return shutil.copy2(src_path, dst_path)


def copy_python_module_source_folder(source_root: Path, source_target_dir: Path):
    shutil.copytree(
        source_root,
        source_target_dir,
        dirs_exist_ok=True,
        copy_function=copy_or_link_python_module_file,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            ".git",
            ".idea",
            ".vscode",
            ".venv",
            "venv",
            "env",
            "module_envs",
            "runtime",
        ),
    )
def install_python_venv_module_from_values(
    module_id: str,
    module_name: str,
    source_dir: str,
    entry_file: str,
    tool_type: str = "",
    description: str = "",
    param_json_path: str = "",
    param_json: dict | None = None,
    python_executable: str = "",
    python_env_mode: str = "create_venv",
) -> dict:
    safe_module_id = sanitize_filename(module_id).strip()
    if not safe_module_id:
        raise HTTPException(status_code=400, detail="模块 ID 不能为空")

    python_env_mode = str(python_env_mode or "create_venv").strip().lower()
    if python_env_mode not in {"create_venv", "existing"}:
        raise HTTPException(status_code=400, detail="python_env_mode 只支持 create_venv 或 existing")

    source_root = Path(source_dir or "").expanduser()
    if not source_root.is_absolute():
        source_root = (PROJECT_ROOT / source_root).resolve()
    else:
        source_root = source_root.resolve()

    if not source_root.exists() or not source_root.is_dir():
        raise HTTPException(status_code=400, detail=f"Python 源码文件夹不存在: {source_root}")

    if param_json is None:
        if not param_json_path:
            param_json_path = str(source_root / "config.json")
        resolved_param_json_path = _resolve_local_json_path(param_json_path)
        param_json = load_param_json_file(str(resolved_param_json_path))
    else:
        resolved_param_json_path = None

    inferred_inputs = infer_inputs_from_param_json(param_json)

    entry_name = entry_file or "main.py"
    entry_candidate = source_root / entry_name
    if not entry_candidate.exists():
        candidates = list(source_root.rglob(entry_name))
        if candidates:
            entry_candidate = candidates[0]

    if not entry_candidate.exists() or not entry_candidate.is_file():
        raise HTTPException(status_code=400, detail=f"未找到 Python 入口文件: {entry_name}")

    release_id = make_python_install_release_id()

    module_root = INSTALLED_MODULES_DIR / safe_module_id
    releases_root = module_root / "releases"
    release_dir = releases_root / release_id
    source_target_dir = release_dir / "source"

    env_dir = PYTHON_MODULE_ENVS_DIR / f"{safe_module_id}_{release_id}"

    created_release = False
    created_env = False

    try:
        release_dir.mkdir(parents=True, exist_ok=False)
        created_release = True

        copy_python_module_source_folder(source_root, source_target_dir)

        param_template_path = release_dir / "param_template.json"
        param_template_path.write_text(
            json.dumps(param_json, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        rel_entry = entry_candidate.resolve().relative_to(source_root.resolve())
        installed_entry_script = source_target_dir / rel_entry

        if python_env_mode == "existing":
            python_exe = resolve_existing_python_executable(python_executable)
            actual_env_dir = python_exe.parent.parent
        else:
            python_exe = create_python_module_env(
                safe_module_id,
                source_target_dir,
                base_python_executable=python_executable,
                env_dir=env_dir,
            )
            created_env = True
            actual_env_dir = env_dir

        module_data = {
            "id": safe_module_id,
            "name": module_name or safe_module_id,
            "description": description or "Python 源码独立环境运行模块",
            "enabled": True,
        }

        selected_tool_type = (
            normalize_tool_key(tool_type or "")
            or guess_module_tool_type(module_data)
        )

        module_data["tool_type"] = selected_tool_type
        module_data["runtime_type"] = "python_venv"
        module_data["python_env_mode"] = python_env_mode
        module_data["base_python_executable"] = python_executable
        module_data["install_release"] = release_id

        module_data["config_mode"] = "config_json"
        module_data["command_template"] = ["{executable}", "{entry_script}", "{config_json}"]
        module_data["inputs"] = inferred_inputs

        module_data["param_template"] = to_project_relative_path(param_template_path)
        module_data["source_dir"] = to_project_relative_path(source_target_dir)
        module_data["entry_file"] = rel_entry.as_posix()
        module_data["entry_script"] = to_project_relative_path(installed_entry_script)

        module_data["python_env_dir"] = (
            str(actual_env_dir)
            if python_env_mode == "existing"
            else to_project_relative_path(actual_env_dir)
        )
        module_data["executable"] = to_project_relative_path(python_exe)
        module_data["working_dir"] = to_project_relative_path(source_target_dir)

        ensure_toolbar_exists(selected_tool_type)
        upsert_module(module_data)

        return module_data

    except HTTPException:
        if created_env and env_dir.exists():
            try:
                remove_tree_windows_safe(env_dir, title="清理失败的 Python 虚拟环境")
            except Exception:
                pass

        if created_release and release_dir.exists():
            try:
                remove_tree_windows_safe(release_dir, title="清理失败的 Python 模块版本目录")
            except Exception:
                pass

        raise

    except Exception as exc:
        if created_env and env_dir.exists():
            try:
                remove_tree_windows_safe(env_dir, title="清理失败的 Python 虚拟环境")
            except Exception:
                pass

        if created_release and release_dir.exists():
            try:
                remove_tree_windows_safe(release_dir, title="清理失败的 Python 模块版本目录")
            except Exception:
                pass

        raise HTTPException(
            status_code=500,
            detail=(
                "Python 模块安装失败：\n"
                f"{type(exc).__name__}: {exc}\n\n"
                + traceback.format_exc()
            ),
        )

def infer_param_input_type(key: str, value: Any) -> str:
    k = str(key or "").lower()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, (dict, list)):
        return "textarea"

    text = str(value or "")
    suffix = Path(text).suffix.lower() if text else ""
    if any(x in k for x in ["dir", "folder", "目录", "outpath", "out_dir", "output_dir", "输出目录"]):
        return "dir_path"
    if any(x in k for x in ["file", "path", "文件"]):
        if suffix:
            return "file_path"
        return "dir_path"
    if suffix in {".tif", ".tiff", ".nc", ".hdf", ".h5", ".json", ".txt", ".xml", ".dat", ".csv"}:
        return "file_path"
    return "text"


def infer_inputs_from_param_json(data: dict) -> list[dict]:
    inputs: list[dict] = []
    for key, value in data.items():
        if key in {"executable", "working_dir", "config_json", "config_path", "runtime_dir"}:
            continue

        field_type = infer_param_input_type(key, value)
        item: dict[str, Any] = {
            "key": str(key),
            "label": str(key),
            "type": field_type,
            "required": True,
            "visible_to_user": True,
            "admin_fixed": False,
            "path_mode": "absolute",
            "io_role": "auto",
        }

        lower_key = str(key).lower()
        if any(x in lower_key for x in ["out", "output", "result", "save", "输出"]):
            item["io_role"] = "output"
        elif field_type in {"file_path", "dir_path"}:
            item["io_role"] = "input"

        if isinstance(value, (dict, list)):
            item["default"] = json.dumps(value, ensure_ascii=False, indent=2)
            item["json_value"] = True
            item["help_text"] = "复杂 JSON 参数，运行前会自动还原为对象或数组"
        elif value is not None:
            item["default"] = value

        inputs.append(item)
    return inputs


def build_python_source_dir_to_exe(
    source_dir_path: Path,
    module_id: str,
    entry_file: str = "main.py",
) -> tuple[Path, Path]:
    """把本地 Python 源码文件夹复制到临时目录后打包为 exe。"""
    if not source_dir_path.exists() or not source_dir_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Python 源码文件夹不存在: {source_dir_path}")

    temp_dir = Path(tempfile.mkdtemp(prefix="python_module_folder_"))
    source_dir = temp_dir / "source"
    shutil.copytree(source_dir_path, source_dir, dirs_exist_ok=True)

    entry_path = source_dir / entry_file
    if not entry_path.exists():
        candidates = list(source_dir.rglob(entry_file))
        if candidates:
            entry_path = candidates[0]

    if not entry_path.exists():
        raise HTTPException(status_code=400, detail=f"未找到 Python 入口文件: {entry_file}")

    requirements_path = source_dir / "requirements.txt"
    if requirements_path.exists():
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(requirements_path)],
                cwd=str(source_dir),
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise HTTPException(status_code=400, detail=f"安装 Python 依赖失败: {exc}")

    dist_dir = temp_dir / "dist"
    build_dir = temp_dir / "build"
    spec_dir = temp_dir / "spec"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            module_id,
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(build_dir),
            "--specpath",
            str(spec_dir),
            str(entry_path),
        ],
        cwd=str(source_dir),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail="Python 代码打包失败:\n" + (result.stderr or result.stdout or "未知错误"),
        )

    exe_path = dist_dir / f"{module_id}.exe"
    if not exe_path.exists():
        raise HTTPException(status_code=400, detail="打包完成但未找到生成的 exe 文件")

    return source_dir, exe_path

def get_venv_python_path(env_dir: Path) -> Path:
    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"
def parse_requirement_package_name(line: str) -> str:
    """
    从 requirements.txt 的一行里解析包名。
    例如：
    GDAL==3.4.3 -> gdal
    numpy>=1.23 -> numpy
    h5py -> h5py
    """
    text = (line or "").strip()

    if not text or text.startswith("#"):
        return ""

    if text.startswith("-"):
        return ""

    for sep in ["==", ">=", "<=", "~=", "!=", ">", "<", "[", ";"]:
        if sep in text:
            text = text.split(sep, 1)[0]
            break

    return text.strip().lower().replace("_", "-")


def split_requirements_for_local_binary(requirements_path: Path) -> tuple[list[str], list[str], list[str]]:
    strict_specs: list[str] = []
    prefer_specs: list[str] = []
    normal_lines: list[str] = []

    for raw in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()

        if not line or line.startswith("#"):
            normal_lines.append(raw)
            continue

        pkg = parse_requirement_package_name(line)

        if pkg in STRICT_LOCAL_BINARY_PACKAGES:
            strict_specs.append(line)
        elif pkg in PREFER_LOCAL_BINARY_PACKAGES:
            prefer_specs.append(line)
        else:
            normal_lines.append(raw)

    return strict_specs, prefer_specs, normal_lines
def build_clean_pip_env() -> dict:
    env = os.environ.copy()

    # 清掉系统代理，避免 pip 走坏掉的代理配置
    for key in [
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "all_proxy",
    ]:
        env.pop(key, None)

    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"

    # Windows 下禁用用户级 pip.ini，避免里面写了错误代理
    if os.name == "nt":
        env["PIP_CONFIG_FILE"] = "NUL"
    else:
        env["PIP_CONFIG_FILE"] = "/dev/null"

    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    return env
def run_checked_command(
    cmd: list[str],
    cwd: Path | None = None,
    title: str = "执行命令",
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        env=env,
    )

    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{title}失败：\n"
                f"命令：{' '.join(map(str, cmd))}\n\n"
                f"STDOUT:\n{result.stdout or ''}\n\n"
                f"STDERR:\n{result.stderr or ''}"
            ),
        )

    return result
def install_requirements_with_local_wheels(
    python_exe: Path,
    requirements_path: Path,
    work_dir: Path,
):
    if not requirements_path.exists():
        return

    strict_specs, prefer_specs, normal_lines = split_requirements_for_local_binary(requirements_path)

    # numpy / h5py：优先本地 wheel，但不强制本地。
    # numpy / h5py：当前系统已经提前下载 wheel，必须从本地 python_wheels 安装。
    for spec in prefer_specs:
        run_checked_command(
            [
                str(python_exe),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--only-binary",
                ":all:",
                "--find-links",
                str(PYTHON_WHEELS_DIR),
                spec,
            ],
            cwd=work_dir,
            title=f"从本地二进制包安装 {spec}",
            env=build_clean_pip_env(),
        )

    # GDAL / rasterio / pyproj / cartopy：强制从本地 wheel 安装，避免源码编译失败。
    for spec in strict_specs:
        run_checked_command(
            [
                str(python_exe),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--only-binary",
                ":all:",
                "--find-links",
                str(PYTHON_WHEELS_DIR),
                spec,
            ],
            cwd=work_dir,
            title=f"从本地二进制包安装 {spec}",
            env=build_clean_pip_env(),
        )

    normal_req_path = work_dir / "requirements.normal.txt"
    normal_req_path.write_text(
        "\n".join(normal_lines),
        encoding="utf-8",
    )

    if normal_req_path.read_text(encoding="utf-8").strip():
        run_checked_command(
            [
                str(python_exe),
                "-m",
                "pip",
                "install",
                "--prefer-binary",
                "--find-links",
                str(PYTHON_WHEELS_DIR),
                "-r",
                str(normal_req_path),
            ],
            cwd=work_dir,
            title="安装普通 Python 依赖",
            env=build_clean_pip_env(),
        )
def resolve_existing_python_executable(raw_path: str) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="必须指定 python_executable")

    python_exe = Path(raw).expanduser()
    if not python_exe.is_absolute():
        python_exe = (PROJECT_ROOT / python_exe).resolve()
    else:
        python_exe = python_exe.resolve()

    if not python_exe.exists() or not python_exe.is_file():
        raise HTTPException(status_code=400, detail=f"指定的 Python 解释器不存在: {python_exe}")

    return python_exe
def create_python_module_env(
    module_id: str,
    source_dir: Path,
    base_python_executable: str = "",
    env_dir: Path | None = None,
) -> Path:
    """为 Python 源码模块创建独立 venv，并安装 requirements.txt。"""
    env_dir = Path(env_dir) if env_dir else PYTHON_MODULE_ENVS_DIR / module_id

    if env_dir.exists():
        remove_tree_windows_safe(env_dir, title="删除旧 Python 虚拟环境")

    # 用 --clear 明确创建干净环境，--copies 在 Windows 上比软链接更稳
    base_python = (
        resolve_existing_python_executable(base_python_executable)
        if str(base_python_executable or "").strip()
        else Path(sys.executable)
    )

    run_checked_command(
        [str(base_python), "-m", "venv", "--clear", "--copies", str(env_dir)],
        cwd=BASE_DIR,
        title="创建 Python 独立环境",
    )

    python_exe = get_venv_python_path(env_dir)
    if not python_exe.exists():
        raise HTTPException(status_code=400, detail=f"创建环境后未找到 Python 解释器: {python_exe}")

    # 关键：先用 ensurepip 修复/安装 pip。
    # 不依赖当前 venv 里已经损坏的 pip 包。
    run_checked_command(
        [str(python_exe), "-m", "ensurepip", "--upgrade", "--default-pip"],
        cwd=source_dir,
        title="初始化 pip",
    )

    # 检查 pip 是否真的可用
    run_checked_command(
        [str(python_exe), "-m", "pip", "--version"],
        cwd=source_dir,
        title="检查 pip",
    )

    requirements_path = source_dir / "requirements.txt"
    if requirements_path.exists():
        install_requirements_with_local_wheels(
            python_exe=python_exe,
            requirements_path=requirements_path,
            work_dir=source_dir,
        )

    return python_exe



# =========================
# C++ / 本地可执行模块校验与运行时依赖收集
# =========================
CPP_INPUT_TYPES = {"text", "textarea", "number", "integer", "file_path", "dir_path", "password"}
CPP_PARALLEL_MODES = {"none", "auto", "single_file", "folder_chunks", "module_internal"}
CPP_SYSTEM_DLLS = {
    "kernel32.dll", "user32.dll", "gdi32.dll", "winspool.drv", "comdlg32.dll", "advapi32.dll",
    "shell32.dll", "ole32.dll", "oleaut32.dll", "uuid.dll", "odbc32.dll", "odbccp32.dll",
    "ws2_32.dll", "bcrypt.dll", "crypt32.dll", "secur32.dll", "rpcrt4.dll", "shlwapi.dll",
    "msvcrt.dll", "ucrtbase.dll", "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll",
    "api-ms-win-crt-runtime-l1-1-0.dll", "api-ms-win-crt-stdio-l1-1-0.dll",
    "api-ms-win-crt-string-l1-1-0.dll", "api-ms-win-crt-heap-l1-1-0.dll",
    "api-ms-win-crt-convert-l1-1-0.dll", "api-ms-win-crt-math-l1-1-0.dll",
}


def _validation_item(field: str, message: str, suggestion: str = "", **extra) -> dict:
    item = {"field": field, "message": message, "suggestion": suggestion}
    for key, value in extra.items():
        if value not in [None, "", [], {}]:
            item[key] = value
    return item


def _missing_item(path: str | Path, reason: str, suggestion: str = "") -> dict:
    return {"path": str(path), "reason": reason, "suggestion": suggestion}


def make_cpp_validation_report(folder_path: str | Path) -> dict:
    return {
        "ok": False,
        "can_install": False,
        "folder_path": str(folder_path),
        "module_root": "",
        "module_json_path": "",
        "module": None,
        "errors": [],
        "warnings": [],
        "missing_files": [],
        "suggestions": [],
        "dependency_report": {
            "auto_collect_enabled": False,
            "analyzer": "",
            "copied": [],
            "missing_imports": [],
            "search_dirs": [],
            "target_dir": "",
            "message": "",
        },
    }


def _add_error(report: dict, field: str, message: str, suggestion: str = "", **extra):
    report.setdefault("errors", []).append(_validation_item(field, message, suggestion, **extra))
    if suggestion:
        report.setdefault("suggestions", []).append(suggestion)


def _add_warning(report: dict, field: str, message: str, suggestion: str = "", **extra):
    report.setdefault("warnings", []).append(_validation_item(field, message, suggestion, **extra))
    if suggestion:
        report.setdefault("suggestions", []).append(suggestion)


def _add_missing(report: dict, path: str | Path, reason: str, suggestion: str = ""):
    report.setdefault("missing_files", []).append(_missing_item(path, reason, suggestion))
    if suggestion:
        report.setdefault("suggestions", []).append(suggestion)


def _dedupe_report_items(report: dict):
    for key in ["errors", "warnings", "missing_files"]:
        seen = set()
        unique = []
        for item in report.get(key, []) or []:
            sig = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if sig in seen:
                continue
            seen.add(sig)
            unique.append(item)
        report[key] = unique

    seen_suggestions = []
    for item in report.get("suggestions", []) or []:
        if item and item not in seen_suggestions:
            seen_suggestions.append(item)
    report["suggestions"] = seen_suggestions


def _read_text_with_encoding(path: Path) -> tuple[str, str]:
    try:
        return path.read_text(encoding="utf-8"), "utf-8"
    except UnicodeDecodeError:
        return path.read_text(encoding="gbk"), "gbk"


def _json_error_snippet(text: str, line_no: int, col_no: int, window: int = 2) -> str:
    lines = text.splitlines() or [text]
    start = max(1, line_no - window)
    end = min(len(lines), line_no + window)
    out: list[str] = []
    for current in range(start, end + 1):
        prefix = ">>" if current == line_no else "  "
        line_text = lines[current - 1] if 0 <= current - 1 < len(lines) else ""
        out.append(f"{prefix} {current:>4}: {line_text}")
        if current == line_no:
            caret_pos = max(col_no - 1, 0)
            out.append("       " + " " * caret_pos + "^")
    return "\n".join(out)


def _load_module_json_for_validation(module_json_path: Path, report: dict) -> dict | None:
    display_name = module_json_path.name or "module.json"
    try:
        text, encoding = _read_text_with_encoding(module_json_path)
    except Exception as exc:
        _add_error(
            report,
            display_name,
            f"读取 {display_name} 失败：{type(exc).__name__}: {exc}",
            f"确认 {display_name} 没有被其他程序占用，并且文件编码建议保存为 UTF-8。",
        )
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = _json_error_snippet(text, exc.lineno, exc.colno)
        _add_error(
            report,
            f"{display_name} 第 {exc.lineno} 行，第 {exc.colno} 列",
            f"JSON 语法错误：{exc.msg}",
            "按箭头位置检查：常见问题是少逗号、多逗号、用了中文引号、字符串没有双引号、数组或对象括号没有闭合。JSON 不能写注释。",
            line=exc.lineno,
            column=exc.colno,
            char=exc.pos,
            encoding=encoding,
            snippet=snippet,
        )
        return None
    except Exception as exc:
        _add_error(
            report,
            display_name,
            f"JSON 解析失败：{type(exc).__name__}: {exc}",
            f"用 JSON 校验工具检查 {display_name}，注意不能有注释、尾随逗号或非法引号。",
        )
        return None

    if not isinstance(data, dict):
        _add_error(report, display_name, "顶层必须是 JSON 对象", f"{display_name} 顶层应写成 {{ ... }}，不能是数组或字符串。")
        return None

    return data


def _find_module_json(folder_path: Path, report: dict) -> Path | None:
    """查找可执行模块配置文件。

    新版可执行模块使用 executable_module.json + config.json，
    旧版仍兼容 module.json。优先使用 executable_module.json。
    """
    direct_new = folder_path / "executable_module.json"
    if direct_new.exists() and direct_new.is_file():
        return direct_new

    direct_old = folder_path / "module.json"
    if direct_old.exists() and direct_old.is_file():
        return direct_old

    candidates = [p for p in folder_path.rglob("executable_module.json") if p.is_file()]
    if not candidates:
        candidates = [p for p in folder_path.rglob("module.json") if p.is_file()]

    if not candidates:
        _add_error(
            report,
            "executable_module.json",
            "模块文件夹中未找到 executable_module.json 或 module.json",
            "把 executable_module.json 放在模块根目录，和 config.json、exe、resources、deps 等目录放在同一级。",
        )
        _add_missing(
            report,
            folder_path / "executable_module.json",
            "缺少可执行模块配置文件",
            "新增 executable_module.json，并填写 module_id、module_name、entry_file、param_json_path 等字段。",
        )
        return None

    candidates.sort(key=lambda x: (0 if x.name.lower() == "executable_module.json" else 1, len(x.parts)))
    if len(candidates) > 1:
        _add_warning(report, candidates[0].name, f"检测到多个模块配置文件，系统会使用这个：{candidates[0]}", "建议一个模块包只保留一个 executable_module.json，避免安装错模块。")
    elif candidates[0].parent != folder_path:
        _add_warning(report, candidates[0].name, f"配置文件不在选择目录根部，当前使用：{candidates[0]}", "建议把 executable_module.json 放到你选择的模块文件夹根目录。")
    return candidates[0]


def _resolve_module_reference(module_root: Path, raw_value: Any, module_id: str = "", default_path: Path | None = None) -> Path:
    raw = str(raw_value or "").strip()
    if not raw or raw == ".":
        return default_path or module_root

    p = Path(raw).expanduser()
    if p.is_absolute():
        if p.exists():
            return p.resolve()
        parts = list(p.parts)
        if module_id and module_id in parts:
            idx = parts.index(module_id)
            rel_parts = parts[idx + 1:]
            if rel_parts:
                return (module_root.joinpath(*rel_parts)).resolve()
        if p.name:
            return (module_root / p.name).resolve()
        return p.resolve()

    return (module_root / p).resolve()



def _cfg_alias_value(cfg: dict, *names: str, default=None):
    """兼容 executable_module.json 中的 module_id/module id/module-id 等写法。"""
    if not isinstance(cfg, dict):
        return default
    for name in names:
        candidates = [name, name.replace("_", " "), name.replace("_", "-")]
        for candidate in candidates:
            if candidate in cfg and cfg.get(candidate) not in (None, ""):
                return cfg.get(candidate)
    return default


def _is_new_executable_manifest(manifest_path: Path, data: dict) -> bool:
    if manifest_path.name.lower() == "executable_module.json":
        return True
    return any(k in data for k in ["module_id", "module id", "module-name", "module_name", "entry_file", "entry file", "param_json_path", "param json path"])


def _infer_executable_inputs_from_config(param_json: dict, module_root: Path, module_data: dict) -> list[dict]:
    """从新版 config.json 自动生成输入表单，并补充批处理/固定资源属性。"""
    inputs = infer_inputs_from_param_json(param_json)
    parallel_cfg = module_data.get("parallel") if isinstance(module_data.get("parallel"), dict) else {}
    parallel_patterns = parallel_cfg.get("file_patterns") or parallel_cfg.get("patterns") or "*"

    for item in inputs:
        key = str(item.get("key") or "")
        lower = key.lower()
        value = param_json.get(key)
        value_text = str(value or "")

        # 输出目录/输出文件
        if any(x in lower for x in ["out", "output", "result", "save", "输出"]):
            item["io_role"] = "output"
            item["batch_role"] = "output"
            if item.get("type") not in {"file_path", "dir_path"}:
                item["type"] = "dir_path"
            continue

        # 固定配置/资源文件：config_xml、LUT、IGBP、DEM 等默认隐藏，按模块目录解析。
        is_resource_like = (
            lower in {"config_xml", "xml", "lut", "lut_file", "igbp", "igbp_file", "dem", "dem_file", "geo", "geo1", "geo1_file"}
            or any(x in lower for x in ["xml", "lut", "igbp", "dem", "geo"])
        )
        if is_resource_like and value_text:
            p = Path(value_text)
            if not p.is_absolute():
                p = (module_root / p).resolve()
            if p.exists():
                item["visible_to_user"] = False
                item["admin_fixed"] = True
                item["path_mode"] = "relative_to_module"
                item["io_role"] = "input"
                item["type"] = "file_path" if p.is_file() else "dir_path"
                item["batch_role"] = ""
                continue

        # 主要输入目录/输入文件。新版 exe 和 Python 风格一致：用户只填目录，系统可按文件拆任务。
        if item.get("type") in {"file_path", "dir_path"}:
            item["io_role"] = "input"
            if any(x in lower for x in ["input", "file", "dir", "folder", "输入"]):
                item["batch_role"] = "input"
                item["match_mode"] = "each_file"
                item["file_patterns"] = parallel_patterns
                item["batch_allow_all_files"] = True
                item["batch_allow_no_extension"] = True

    return inputs


def _normalize_new_executable_manifest(module_root: Path, manifest_path: Path, raw_data: dict, report: dict) -> dict:
    """把新版 executable_module.json 转成系统内部沿用的 module.json 记录。"""
    if not _is_new_executable_manifest(manifest_path, raw_data):
        return raw_data

    module_id = str(_cfg_alias_value(raw_data, "module_id", "id", default="") or "").strip()
    module_name = str(_cfg_alias_value(raw_data, "module_name", "name", default="") or "").strip()
    entry_file = str(_cfg_alias_value(raw_data, "entry_file", "entry", "executable", default="") or "").strip()
    source_dir = str(_cfg_alias_value(raw_data, "source_dir", "working_dir", default=".") or ".").strip() or "."
    param_json_name = str(_cfg_alias_value(raw_data, "param_json_path", "config_json", "config_path", default="config.json") or "config.json").strip() or "config.json"
    description = str(_cfg_alias_value(raw_data, "description", default="") or "").strip()
    tool_type = str(_cfg_alias_value(raw_data, "tool_type", "category", default="") or "").strip()

    source_path = _resolve_module_reference(module_root, source_dir, module_id, module_root)
    if not source_path.exists() or not source_path.is_dir():
        _add_error(report, "source_dir", f"source_dir 指向的目录不存在：{source_dir}", "如果 exe、config.json 和资源都在模块根目录，source_dir 写 \".\"。")
        _add_missing(report, source_path, "source_dir 指向的目录不存在", "创建该目录，或修改 source_dir。")
        source_path = module_root

    entry_path = _resolve_module_reference(source_path, entry_file, module_id, source_path) if entry_file else source_path
    if not entry_file:
        _add_error(report, "entry_file", "缺少 entry_file", "在 executable_module.json 中添加例如：\"entry_file\": \"ParasolAOD.exe\"。")
    elif not entry_path.exists() or not entry_path.is_file():
        _add_error(report, "entry_file", f"入口可执行文件不存在：{entry_file}", "把 exe 放到模块目录，或修改 entry_file。")
        _add_missing(report, entry_path, "entry_file 指向的 exe 不存在", "把编译好的 exe 放到该位置。")

    param_json_path = _resolve_module_reference(source_path, param_json_name, module_id, source_path)
    param_json = None
    if not param_json_path.exists() or not param_json_path.is_file():
        _add_error(report, "param_json_path", f"参数 config.json 不存在：{param_json_path}", "把 config.json 放到模块目录，或修改 param_json_path。")
        _add_missing(report, param_json_path, "缺少运行参数 config.json", "新增 config.json，用它生成前端输入/输出表单。")
    else:
        param_json = _load_json_for_validation(param_json_path, report, param_json_path.name)
        if param_json is not None and not isinstance(param_json, dict):
            _add_error(report, "param_json_path", "config.json 顶层必须是对象", "例如 {\"input_file\": \"...\", \"output_dir\": \"...\"}。")
            param_json = None

    if not module_id:
        _add_error(report, "module_id", "缺少 module_id", "在 executable_module.json 中添加例如：\"module_id\": \"parasol_aod\"。")
    elif not re.match(r"^[A-Za-z0-9_\-\.]+$", module_id):
        _add_error(report, "module_id", f"module_id 不建议包含空格、中文或特殊符号：{module_id}", "建议只使用英文、数字、下划线、中划线或点。")
    if not module_name:
        _add_error(report, "module_name", "缺少 module_name", "在 executable_module.json 中添加例如：\"module_name\": \"PARASOL AOD 反演\"。")

    dependency_dirs = _cfg_alias_value(raw_data, "dependency_dirs", default=[])
    if isinstance(dependency_dirs, str):
        dependency_dirs = [dependency_dirs]
    if not isinstance(dependency_dirs, list):
        dependency_dirs = []

    resource_dirs = _cfg_alias_value(raw_data, "resource_dirs", default=[])
    if isinstance(resource_dirs, str):
        resource_dirs = [resource_dirs]
    if not isinstance(resource_dirs, list):
        resource_dirs = []

    runtime_env_path = _cfg_alias_value(raw_data, "runtime_env_path", "environment_path", "env_path", default="")
    dependency_search_dirs = _cfg_alias_value(raw_data, "dependency_search_dirs", default=[])
    if isinstance(dependency_search_dirs, str):
        dependency_search_dirs = [dependency_search_dirs]
    if runtime_env_path:
        dependency_search_dirs = list(dependency_search_dirs or []) + [runtime_env_path]

    parallel_cfg = raw_data.get("parallel") if isinstance(raw_data.get("parallel"), dict) else {}
    if not parallel_cfg:
        parallel_cfg = {
            "mode": "auto",
            "file_patterns": "*",
            "output_suffix": ".tif",
            "output_naming": "source_stem",
        }

    inputs = _infer_executable_inputs_from_config(param_json or {}, source_path, {"parallel": parallel_cfg})

    # 如果声明了 JD/JL 配对，确保输入字段只按 JD 生成任务。
    if isinstance(parallel_cfg, dict) and (parallel_cfg.get("jd_jl_pair") is True or parallel_cfg.get("jd_only") is True):
        for item in inputs:
            if item.get("batch_role") == "input":
                item["file_patterns"] = parallel_cfg.get("file_patterns") or "*jd*;*JD*"
                item["batch_include_regex"] = r"(?i)jd(?=[_.-])"

    # 内部 module 记录仍沿用 executable 字段。
    # 如果 source_dir 不是根目录，executable 需要保存为相对模块根目录的路径，
    # 这样安装复制后仍能正确定位 exe。
    try:
        executable_rel = str((Path(source_dir) / entry_file).as_posix()) if source_dir not in {"", "."} else entry_file
    except Exception:
        executable_rel = entry_file

    module_data = {
        "id": module_id,
        "name": module_name,
        "description": description,
        "runtime": "cpp_native",
        "entry": executable_rel,
        "executable": executable_rel,
        "working_dir": source_dir,
        "config_mode": "json_file",
        "command_template": ["{executable}", "{config_json}"],
        "dependency_dirs": dependency_dirs,
        "dependency_search_dirs": dependency_search_dirs,
        "resource_dirs": resource_dirs,
        "parallel": parallel_cfg,
        "tags": raw_data.get("tags") if isinstance(raw_data.get("tags"), list) else ["executable", "native"],
        "tool_type": tool_type or "气溶胶反演",
        "enabled": bool(raw_data.get("enabled", True)),
        "inputs": inputs,
        "_manifest_format": "executable_module_json",
        "_param_json_path": str(param_json_path),
    }
    return module_data

def _module_json_error_detail(report: dict) -> dict:
    _dedupe_report_items(report)
    report["ok"] = False
    report["can_install"] = False
    return {
        "message": "可执行模块校验失败，请按下面提示修改后再安装。",
        **report,
    }


def raise_cpp_validation_error(report: dict):
    raise HTTPException(status_code=400, detail=_module_json_error_detail(report))


def _validate_cpp_module_structure(module_root: Path, module_data: dict, report: dict):
    module_id = str(module_data.get("id") or "").strip()
    module_name = str(module_data.get("name") or "").strip()
    executable = str(module_data.get("executable") or module_data.get("entry") or "").strip()
    working_dir = str(module_data.get("working_dir") or ".").strip() or "."

    if not module_id:
        _add_error(report, "id", "缺少模块 id", "在 module.json 中添加例如：\"id\": \"parasol_aod\"。")
    elif not re.match(r"^[A-Za-z0-9_\-\.]+$", module_id):
        _add_error(report, "id", f"模块 id 不建议包含空格、中文或特殊符号：{module_id}", "建议只使用英文、数字、下划线、中划线或点，例如 parasol_aod。")

    if not module_name:
        _add_error(report, "name", "缺少模块名称 name", "在 module.json 中添加例如：\"name\": \"PARASOL AOD 反演\"。")

    if not executable:
        _add_error(report, "executable", "缺少 executable / entry", "C++ 原生模块需要写可执行程序，例如：\"executable\": \"ParasolAOD.exe\"。")
        exe_path = None
    else:
        exe_path = _resolve_module_reference(module_root, executable, module_id, module_root)
        if not exe_path.exists() or not exe_path.is_file():
            _add_error(report, "executable", f"可执行文件不存在：{executable}", "确认 exe 放在模块文件夹中，或把 executable 改成正确的相对路径。")
            _add_missing(report, exe_path, "module.json executable 指向的文件不存在", "把编译好的 exe 放进模块目录，或修改 executable 字段。")
        elif sys.platform.startswith("win") and exe_path.suffix.lower() not in {".exe", ".bat", ".cmd"}:
            _add_warning(report, "executable", f"Windows 下建议 executable 指向 .exe/.bat/.cmd，当前是：{exe_path.name}", "这里按 C++ 可执行模块安装，请确认已经编译为可执行文件。")

    wd_path = _resolve_module_reference(module_root, working_dir, module_id, module_root)
    if not wd_path.exists() or not wd_path.is_dir():
        _add_error(report, "working_dir", f"工作目录不存在：{working_dir}", "通常 C++ 模块 working_dir 写 \".\" 即可，资源路径写相对模块目录。")
        _add_missing(report, wd_path, "module.json working_dir 指向的目录不存在", "创建该目录，或把 working_dir 改为 \".\"。")

    command_template = module_data.get("command_template")
    if command_template is None:
        _add_warning(report, "command_template", "缺少 command_template，系统会默认只执行 executable", "建议显式写出命令参数，例如 [\"{executable}\", \"{input_file}\", \"{output_dir}\", \"{config_xml}\"]。")
        command_template = []
    elif not isinstance(command_template, list) or not all(isinstance(x, str) and x.strip() for x in command_template):
        _add_error(report, "command_template", "command_template 必须是非空字符串数组", "例如：[\"{executable}\", \"{input_file}\", \"{output_dir}\"]。")
        command_template = []

    inputs = module_data.get("inputs")
    input_keys = set()
    if not isinstance(inputs, list):
        _add_error(report, "inputs", "inputs 必须是数组", "例如：\"inputs\": [{\"key\": \"input_file\", \"label\": \"输入文件\", \"type\": \"file_path\"}]。")
        inputs = []

    for idx, field in enumerate(inputs):
        field_path = f"inputs[{idx}]"
        if not isinstance(field, dict):
            _add_error(report, field_path, "输入项必须是对象", "每个 inputs 项都要包含 key、label、type 等字段。")
            continue

        key = str(field.get("key") or "").strip()
        label = str(field.get("label") or "").strip()
        ftype = str(field.get("type") or "text").strip()
        path_mode = str(field.get("path_mode") or "absolute").strip()
        visible = field.get("visible_to_user", True) is not False
        admin_fixed = field.get("admin_fixed", False) is True
        required = field.get("required", True) is not False
        default_value = field.get("default")

        if not key:
            _add_error(report, f"{field_path}.key", "缺少 key", "给每个输入项设置唯一 key，例如 input_file、output_dir、config_xml。")
        elif key in input_keys:
            _add_error(report, f"{field_path}.key", f"key 重复：{key}", "inputs 中每个 key 必须唯一。")
        else:
            input_keys.add(key)

        if not label:
            _add_warning(report, f"{field_path}.label", f"{key or field_path} 缺少 label", "建议写中文显示名，方便用户理解。")

        if ftype not in CPP_INPUT_TYPES:
            _add_error(report, f"{field_path}.type", f"不支持的 type：{ftype}", "type 只能是 text、textarea、number、integer、file_path、dir_path、password。")

        if path_mode not in {"absolute", "relative_to_module"}:
            _add_error(report, f"{field_path}.path_mode", f"不支持的 path_mode：{path_mode}", "path_mode 只能是 absolute 或 relative_to_module。")

        if required and (admin_fixed or not visible):
            if default_value in [None, ""]:
                _add_error(report, f"{field_path}.default", f"隐藏/管理员固定参数 {key} 必须提供 default", "隐藏参数用户无法填写，default 应写 resources 中的相对路径或固定值。")
            elif ftype in {"file_path", "dir_path"} and path_mode == "relative_to_module":
                target = _resolve_module_reference(module_root, default_value, module_id, module_root)
                if ftype == "file_path" and (not target.exists() or not target.is_file()):
                    _add_error(report, f"{field_path}.default", f"固定文件不存在：{default_value}", "把文件放进 resources 目录，或修改 default 为正确相对路径。")
                    _add_missing(report, target, f"隐藏参数 {key} 指向的文件不存在", "确认 resources、LUT、XML、模型文件随模块一起上传。")
                if ftype == "dir_path" and (not target.exists() or not target.is_dir()):
                    _add_error(report, f"{field_path}.default", f"固定目录不存在：{default_value}", "把目录放进 resources 目录，或修改 default 为正确相对路径。")
                    _add_missing(report, target, f"隐藏参数 {key} 指向的目录不存在", "确认 resources、LUT、XML、模型目录随模块一起上传。")

    known_placeholders = set(input_keys) | {
        "executable", "python_executable", "working_dir", "config_json", "config_path",
        "runtime_dir", "entry_script", "module_dir",
    }
    for idx, token in enumerate(command_template):
        try:
            parsed = Formatter().parse(token)
            for _, field_name, _, _ in parsed:
                if not field_name:
                    continue
                base = re.split(r"[.\[]", field_name, 1)[0]
                if base and base not in known_placeholders:
                    _add_error(
                        report,
                        f"command_template[{idx}]",
                        f"命令模板引用了未知占位符：{{{field_name}}}",
                        f"把占位符改成 inputs 中已有的 key，或在 inputs 中新增 key={base} 的输入项。",
                    )
        except Exception as exc:
            _add_error(report, f"command_template[{idx}]", f"命令模板格式错误：{exc}", "检查大括号是否成对，例如 {input_file}。")

    parallel = module_data.get("parallel")
    if parallel is not None:
        if not isinstance(parallel, dict):
            _add_error(report, "parallel", "parallel 必须是对象", "例如：\"parallel\": {\"mode\": \"auto\", \"file_patterns\": \"*.tif\"}。")
        else:
            mode = str(parallel.get("mode") or "auto")
            if mode not in CPP_PARALLEL_MODES:
                _add_error(report, "parallel.mode", f"不支持的并行模式：{mode}", "可选 none、auto、single_file、folder_chunks、module_internal。")

    dependency_dirs = module_data.get("dependency_dirs", ["deps"])
    if dependency_dirs is None:
        dependency_dirs = []
    if not isinstance(dependency_dirs, list):
        _add_error(report, "dependency_dirs", "dependency_dirs 必须是字符串数组", "例如：\"dependency_dirs\": [\"deps\"]。")
        dependency_dirs = []

    for dep in dependency_dirs:
        dep_text = str(dep or "").strip()
        if not dep_text:
            continue
        dep_path = _resolve_module_reference(module_root, dep_text, module_id, module_root)
        if not dep_path.exists() or not dep_path.is_dir():
            _add_warning(report, "dependency_dirs", f"声明的依赖目录不存在：{dep_text}", "如果没有额外 DLL，可以删除该目录配置；如果有 DLL，请创建该目录并放入依赖。")
            _add_missing(report, dep_path, "dependency_dirs 声明的目录不存在", "创建目录并放入运行时 DLL，或从 dependency_dirs 中删除它。")

    dependency_search_dirs = module_data.get("dependency_search_dirs", [])
    if dependency_search_dirs is None:
        dependency_search_dirs = []
    if isinstance(dependency_search_dirs, str):
        dependency_search_dirs = [dependency_search_dirs]
    if not isinstance(dependency_search_dirs, list):
        _add_error(report, "dependency_search_dirs", "dependency_search_dirs 必须是字符串数组", "例如：\"dependency_search_dirs\": [\"C:/OSGeo4W/bin\"]。")
        dependency_search_dirs = []
    for dep in dependency_search_dirs:
        dep_text = str(dep or "").strip()
        if not dep_text:
            continue
        dep_path = _resolve_module_reference(module_root, dep_text, module_id, module_root)
        if not dep_path.exists() or not dep_path.is_dir():
            _add_warning(report, "dependency_search_dirs", f"依赖搜索目录不存在：{dep_text}", "如果希望系统自动收集 DLL，请填写本机真实存在的 DLL 目录；如果不需要自动收集，可以留空。")

    runtime = str(module_data.get("runtime") or "native").lower()
    if runtime not in {"native", "cpp", "cpp_native", "c++", "exe", "binary"}:
        _add_warning(report, "runtime", f"runtime 当前是 {runtime}，C++ 可执行模块建议写 native 或 cpp_native", "建议：\"runtime\": \"cpp_native\"。")


def _dependency_search_dirs(module_root: Path, exe_path: Path, module_data: dict) -> list[Path]:
    search_dirs: list[Path] = [exe_path.parent, module_root]
    module_id = str(module_data.get("id") or "")

    for key in ["dependency_dirs", "dependency_search_dirs"]:
        raw_list = module_data.get(key) or []
        if isinstance(raw_list, str):
            raw_list = [raw_list]
        if isinstance(raw_list, list):
            for item in raw_list:
                if not str(item or "").strip():
                    continue
                search_dirs.append(_resolve_module_reference(module_root, item, module_id, module_root))

    path_value = os.environ.get("PATH", "")
    sep = ";" if sys.platform.startswith("win") else os.pathsep
    for item in path_value.split(sep):
        item = item.strip().strip('"')
        if item:
            search_dirs.append(Path(item))

    result: list[Path] = []
    seen = set()
    for d in search_dirs:
        try:
            rd = d.resolve()
        except Exception:
            rd = d
        key = str(rd).lower() if sys.platform.startswith("win") else str(rd)
        if key in seen:
            continue
        seen.add(key)
        if rd.exists() and rd.is_dir():
            result.append(rd)
    return result


def _find_dependency_file(name: str, search_dirs: list[Path]) -> Path | None:
    target_lower = name.lower()
    for directory in search_dirs:
        direct = directory / name
        if direct.exists() and direct.is_file():
            return direct
        try:
            for child in directory.iterdir():
                if child.is_file() and child.name.lower() == target_lower:
                    return child
        except Exception:
            continue
    return None


def _parse_imports_with_pefile(exe_path: Path) -> tuple[list[str], str]:
    try:
        import pefile  # type: ignore
    except Exception:
        return [], ""

    try:
        pe = pefile.PE(str(exe_path))
        deps = []
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
            dll = entry.dll.decode("utf-8", errors="ignore") if isinstance(entry.dll, bytes) else str(entry.dll)
            if dll:
                deps.append(dll)
        return sorted(set(deps)), "pefile"
    except Exception:
        return [], ""


def _parse_imports_with_command(exe_path: Path) -> tuple[list[str], str]:
    candidates = [
        ("dumpbin", ["dumpbin", "/DEPENDENTS", str(exe_path)]),
        ("llvm-objdump", ["llvm-objdump", "-p", str(exe_path)]),
        ("objdump", ["objdump", "-p", str(exe_path)]),
    ]
    for analyzer, cmd in candidates:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20)
        except Exception:
            continue
        if result.returncode != 0:
            continue
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        deps = set()
        for line in text.splitlines():
            line = line.strip()
            match = re.search(r"(?i)\b([A-Za-z0-9_.+\-]+\.dll)\b", line)
            if match:
                deps.add(match.group(1))
        if deps:
            return sorted(deps), analyzer
    return [], ""


def collect_cpp_runtime_dependencies(module_root: Path, exe_path: Path, module_data: dict, copy_files: bool = False) -> dict:
    report = {
        "auto_collect_enabled": bool(copy_files),
        "analyzer": "",
        "copied": [],
        "missing_imports": [],
        "search_dirs": [],
        "target_dir": "",
        "message": "",
    }

    if not exe_path.exists() or not exe_path.is_file():
        report["message"] = "可执行文件不存在，跳过依赖收集。"
        return report

    deps, analyzer = _parse_imports_with_pefile(exe_path)
    if not deps:
        deps, analyzer = _parse_imports_with_command(exe_path)

    report["analyzer"] = analyzer
    if not deps:
        report["message"] = "没有可用的 PE 依赖分析器，未能自动识别 DLL。可安装 pefile，或在 Visual Studio Developer Prompt 中启动后端以使用 dumpbin。"
        return report

    search_dirs = _dependency_search_dirs(module_root, exe_path, module_data)
    report["search_dirs"] = [str(p) for p in search_dirs]

    target_dir = module_root / "deps" / "auto"
    if copy_files:
        target_dir.mkdir(parents=True, exist_ok=True)
    report["target_dir"] = str(target_dir)

    copied = []
    missing = []
    for dep_name in deps:
        if dep_name.lower() in CPP_SYSTEM_DLLS:
            continue
        found = _find_dependency_file(dep_name, search_dirs)
        if not found:
            missing.append(dep_name)
            continue
        try:
            # 已经在模块目录内的依赖不需要再复制，但仍然算作已找到。
            if str(found.resolve()).lower().startswith(str(module_root.resolve()).lower()):
                continue
            if copy_files:
                dst = target_dir / found.name
                if not dst.exists():
                    shutil.copy2(found, dst)
                copied.append(found.name)
        except Exception:
            missing.append(dep_name)

    report["copied"] = sorted(set(copied))
    report["missing_imports"] = sorted(set(missing))
    if missing:
        report["message"] = "已识别运行时 DLL，但有部分依赖未在模块目录、dependency_dirs 或系统 PATH 中找到。"
    elif copied:
        report["message"] = f"已自动复制 {len(copied)} 个运行时 DLL 到 deps/auto。"
    else:
        report["message"] = "运行时依赖已在模块目录、dependency_dirs 或系统 PATH 中找到，无需额外复制。"
    return report


def validate_cpp_module_folder(folder_path: Path, tool_type: str | None = None, collect_dependencies: bool = False, copy_dependencies: bool = False) -> dict:
    report = make_cpp_validation_report(folder_path)

    if add_chinese_path_errors_to_report(report, {"模块文件夹": str(folder_path)}, "可执行模块路径"):
        return report

    if not folder_path.exists() or not folder_path.is_dir():
        _add_error(report, "folder_path", f"模块文件夹不存在：{folder_path}", "请选择包含 module.json 的模块根目录。")
        _add_missing(report, folder_path, "选择的模块文件夹不存在", "重新选择正确的本地文件夹路径。")
        _dedupe_report_items(report)
        return report

    module_json_path = _find_module_json(folder_path, report)
    if not module_json_path:
        _dedupe_report_items(report)
        return report

    module_root = module_json_path.parent
    report["module_json_path"] = str(module_json_path)
    report["module_root"] = str(module_root)

    module_data = _load_module_json_for_validation(module_json_path, report)
    if module_data is None:
        _dedupe_report_items(report)
        return report

    module_data = _normalize_new_executable_manifest(module_root, module_json_path, module_data, report)

    selected_tool_type = (
        normalize_tool_key(tool_type or module_data.get("tool_type") or "")
        or guess_module_tool_type(module_data)
    )
    module_data["tool_type"] = selected_tool_type
    if str(module_data.get("runtime") or "").strip() == "":
        module_data["runtime"] = "cpp_native"

    add_chinese_path_errors_to_report(
        report,
        {
            "module_root": str(module_root),
            "executable": module_data.get("executable") or module_data.get("entry"),
            "working_dir": module_data.get("working_dir"),
            "runtime_env_path": module_data.get("runtime_env_path"),
            "dependency_search_dirs": module_data.get("dependency_search_dirs"),
            "inputs": module_data.get("inputs"),
        },
        "可执行模块配置路径",
    )

    _validate_cpp_module_structure(module_root, module_data, report)

    executable = str(module_data.get("executable") or module_data.get("entry") or "").strip()
    module_id = str(module_data.get("id") or "").strip()
    if collect_dependencies and executable:
        exe_path = _resolve_module_reference(module_root, executable, module_id, module_root)
        dep_report = collect_cpp_runtime_dependencies(module_root, exe_path, module_data, copy_files=copy_dependencies)
        report["dependency_report"] = dep_report
        if dep_report.get("missing_imports"):
            _add_warning(
                report,
                "dependency_dirs",
                "部分 DLL 运行依赖未找到：" + ", ".join(dep_report.get("missing_imports") or []),
                "把这些 DLL 放到 deps 目录，或把它们所在目录加入 module.json 的 dependency_search_dirs。",
            )
        if copy_dependencies and dep_report.get("copied"):
            dependency_dirs = module_data.get("dependency_dirs") or ["deps"]
            if isinstance(dependency_dirs, list) and "deps/auto" not in dependency_dirs:
                dependency_dirs.append("deps/auto")
                module_data["dependency_dirs"] = dependency_dirs

    report["module"] = module_data
    _dedupe_report_items(report)
    report["ok"] = len(report.get("errors") or []) == 0
    report["can_install"] = report["ok"]
    return report


def install_validated_cpp_module(module_root: Path, module_data: dict, collect_dependencies: bool = True) -> dict:
    module_id = str(module_data.get("id") or "").strip()
    if not module_id:
        raise HTTPException(status_code=400, detail="module.json 缺少 id")

    target_dir = INSTALLED_MODULES_DIR / module_id
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(module_root, target_dir)

    executable = str(module_data.get("executable") or module_data.get("entry") or "").strip()
    if executable:
        exe_path = resolve_packaged_module_path(
            raw_value=executable,
            module_id=module_id,
            target_dir=target_dir,
            default_path=target_dir,
        )
        module_data["executable"] = to_project_relative_path(exe_path)

        if collect_dependencies and module_data.get("auto_collect_deps", True) is not False:
            dep_report = collect_cpp_runtime_dependencies(target_dir, exe_path, module_data, copy_files=True)
            if dep_report.get("copied"):
                dependency_dirs = module_data.get("dependency_dirs") or ["deps"]
                if isinstance(dependency_dirs, list) and "deps/auto" not in dependency_dirs:
                    dependency_dirs.append("deps/auto")
                    module_data["dependency_dirs"] = dependency_dirs

    working_dir = str(module_data.get("working_dir") or ".").strip()
    wd_path = resolve_packaged_module_path(
        raw_value=working_dir,
        module_id=module_id,
        target_dir=target_dir,
        default_path=target_dir,
    )
    module_data["working_dir"] = to_project_relative_path(wd_path)
    module_data["runtime"] = module_data.get("runtime") or "cpp_native"

    upsert_module(module_data)
    return module_data


def install_uploaded_zip(zip_path: Path, tool_type: str | None = None, collect_dependencies: bool = True) -> dict:
    """安装 module_drop 中投放的 C++/本地可执行模块 zip，并先做完整规范校验。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="module_zip_"))
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(temp_dir)

        validation = validate_cpp_module_folder(
            temp_dir,
            tool_type=tool_type,
            collect_dependencies=True,
            copy_dependencies=False,
        )
        if not validation.get("can_install"):
            raise_cpp_validation_error(validation)

        module_data = validation["module"]
        module_root = Path(validation["module_root"])
        selected_tool_type = (
            normalize_tool_key(tool_type or module_data.get("tool_type") or "")
            or guess_module_tool_type(module_data)
        )
        module_data["tool_type"] = selected_tool_type
        ensure_toolbar_exists(selected_tool_type)

        return install_validated_cpp_module(
            module_root=module_root,
            module_data=module_data,
            collect_dependencies=collect_dependencies,
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)



# =========================
# Python 源码模块配置 JSON 校验
# =========================
def make_python_validation_report(config_path: str | Path) -> dict:
    return {
        "ok": False,
        "can_install": False,
        "config_path": str(config_path or ""),
        "config_dir": "",
        "module": None,
        "inputs": [],
        "count": 0,
        "errors": [],
        "warnings": [],
        "missing_files": [],
        "suggestions": [],
    }


def _python_validation_error_detail(report: dict) -> dict:
    _dedupe_report_items(report)
    report["ok"] = False
    report["can_install"] = False
    return {
        "message": "Python 模块配置 JSON 校验失败，请按下面提示修改后再安装。",
        **report,
    }


def raise_python_validation_error(report: dict):
    raise HTTPException(status_code=400, detail=_python_validation_error_detail(report))


def _resolve_validation_path(raw_path: str, base_dir: Path | None = None) -> Path:
    raw = str(raw_path or "").strip()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = ((base_dir or PROJECT_ROOT) / p).resolve()
    else:
        p = p.resolve()
    return p


def _load_json_for_validation(json_path: Path, report: dict, display_name: str) -> dict | None:
    try:
        text, encoding = _read_text_with_encoding(json_path)
    except Exception as exc:
        _add_error(
            report,
            display_name,
            f"读取 {display_name} 失败：{type(exc).__name__}: {exc}",
            "确认文件没有被其他程序占用，并建议保存为 UTF-8 编码。",
        )
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = _json_error_snippet(text, exc.lineno, exc.colno)
        _add_error(
            report,
            f"{display_name} 第 {exc.lineno} 行，第 {exc.colno} 列",
            f"JSON 语法错误：{exc.msg}",
            "按箭头位置检查：常见问题是少逗号、多逗号、用了中文引号、字符串没有双引号、数组或对象括号没有闭合。JSON 不能写注释。",
            line=exc.lineno,
            column=exc.colno,
            char=exc.pos,
            encoding=encoding,
            snippet=snippet,
        )
        return None
    except Exception as exc:
        _add_error(
            report,
            display_name,
            f"JSON 解析失败：{type(exc).__name__}: {exc}",
            "用 JSON 校验工具检查该文件，注意不能有注释、尾随逗号或非法引号。",
        )
        return None

    if not isinstance(data, dict):
        _add_error(report, display_name, "顶层必须是 JSON 对象", f"{display_name} 顶层应写成 {{ ... }}，不能是数组、字符串或数字。")
        return None

    return data


def _first_present_key(data: dict, keys: list[str]) -> str:
    for key in keys:
        if key in data:
            return key
    return ""


def _get_python_cfg_value(module_cfg: dict, keys: list[str], default: Any = "") -> Any:
    for key in keys:
        if key in module_cfg and module_cfg.get(key) not in (None, ""):
            return module_cfg.get(key)
    return default


def validate_python_module_config_file(raw_path: str) -> dict:
    raw = str(raw_path or "").strip()
    report = make_python_validation_report(raw)

    if not raw:
        _add_error(report, "path", "请选择 Python 模块配置 JSON", "点击“浏览并检查”，选择类似 python_module.json 的配置文件。")
        _dedupe_report_items(report)
        return report

    if add_chinese_path_errors_to_report(report, {"Python 模块配置 JSON": raw}, "Python 模块配置 JSON"):
        return report

    config_path = _resolve_validation_path(raw)
    report["config_path"] = str(config_path)
    report["config_dir"] = str(config_path.parent)

    if not config_path.exists() or not config_path.is_file():
        _add_error(report, "path", f"Python 模块配置 JSON 不存在：{config_path}", "重新选择真实存在的 python_module.json 文件。")
        _add_missing(report, config_path, "缺少 Python 模块配置 JSON", "新建或选择 python_module.json。")
        _dedupe_report_items(report)
        return report

    if config_path.suffix.lower() != ".json":
        _add_error(report, "path", "Python 模块配置文件必须是 .json", "请把配置保存为 .json 文件，例如 python_module.json。")

    data = _load_json_for_validation(config_path, report, "python_module.json")
    if data is None:
        _dedupe_report_items(report)
        return report

    if "module" in data:
        if isinstance(data.get("module"), dict):
            module_cfg = data["module"]
        else:
            _add_error(report, "module", "module 字段必须是对象", "如果使用嵌套写法，应写成：{\"module\": { ... }}。也可以直接使用平铺字段 module_id/source_dir/entry_file。")
            _dedupe_report_items(report)
            return report
    else:
        module_cfg = data

    module_id_key = _first_present_key(module_cfg, ["module_id", "id"])
    module_name_key = _first_present_key(module_cfg, ["module_name", "name"])
    source_dir_key = _first_present_key(module_cfg, ["source_dir", "python_source_dir"])
    entry_file_key = _first_present_key(module_cfg, ["entry_file"])
    param_json_key = _first_present_key(module_cfg, ["param_json_path", "config_json"])

    module_id = str(_get_python_cfg_value(module_cfg, ["module_id", "id"], "")).strip()
    module_name = str(_get_python_cfg_value(module_cfg, ["module_name", "name"], "")).strip()
    tool_type = str(_get_python_cfg_value(module_cfg, ["tool_type"], "")).strip()
    description = str(_get_python_cfg_value(module_cfg, ["description"], "")).strip()
    source_dir_raw = str(_get_python_cfg_value(module_cfg, ["source_dir", "python_source_dir"], "")).strip()
    entry_file = str(_get_python_cfg_value(module_cfg, ["entry_file"], "main.py")).strip() or "main.py"
    python_executable_raw = str(_get_python_cfg_value(module_cfg, ["python_executable", "python", "python_path"], "")).strip()
    python_env_mode = str(_get_python_cfg_value(module_cfg, ["python_env_mode", "env_mode"], "create_venv")).strip().lower() or "create_venv"

    add_chinese_path_errors_to_report(
        report,
        {
            "source_dir": source_dir_raw,
            "entry_file": entry_file,
            "param_json_path": str(_get_python_cfg_value(module_cfg, ["param_json_path", "config_json"], "")).strip(),
            "python_executable": python_executable_raw,
        },
        "Python 模块配置路径",
    )

    if module_id_key == "id":
        _add_warning(report, "id", "检测到使用 id 字段，系统兼容该写法", "Python 配置 JSON 建议使用 module_id，便于和 C++ module.json 的 id 区分。")
    if module_name_key == "name":
        _add_warning(report, "name", "检测到使用 name 字段，系统兼容该写法", "Python 配置 JSON 建议使用 module_name。")

    if not module_id:
        _add_error(report, "module_id", "缺少 module_id", "添加例如：\"module_id\": \"H8_CLOUD_TYPE\"。")
    elif not re.match(r"^[A-Za-z0-9_\-\.]+$", module_id):
        _add_error(report, "module_id", f"module_id 不建议包含空格、中文或特殊符号：{module_id}", "建议只使用英文、数字、下划线、中划线或点，例如 H8_CLOUD_TYPE。")

    if not module_name:
        _add_error(report, "module_name", "缺少 module_name", "添加例如：\"module_name\": \"葵花8号云类型反演\"。")

    if not tool_type:
        _add_warning(report, "tool_type", "未填写 tool_type，系统会尝试根据模块名称和标签推断工具栏", "建议填写已存在的工具栏 key，例如 cloud 或 aerosol。")

    if not source_dir_raw:
        _add_error(report, "source_dir", "缺少 source_dir", "添加源码目录，例如：\"source_dir\": \".\"。相对路径按 python_module.json 所在目录计算。")
        source_dir = None
    else:
        source_dir = _resolve_validation_path(source_dir_raw, config_path.parent)
        if not source_dir.exists() or not source_dir.is_dir():
            _add_error(report, "source_dir", f"Python 源码文件夹不存在：{source_dir}", "检查 source_dir 是否写错；如果源码和 python_module.json 在同一目录，source_dir 写 \".\"。")
            _add_missing(report, source_dir, "缺少 Python 源码文件夹", "把源码文件夹放到该位置，或修改 source_dir。")
    
    if not entry_file_key:
        _add_warning(report, "entry_file", "未填写 entry_file，系统会默认使用 main.py", "建议明确填写入口脚本，例如 \"entry_file\": \"CM_CTH.py\"。")

    entry_path = None
    if source_dir and source_dir.exists() and source_dir.is_dir():
        entry_path = (source_dir / entry_file).resolve()
        if not entry_path.exists() or not entry_path.is_file():
            candidates = list(source_dir.rglob(Path(entry_file).name))
            candidates = [p for p in candidates if p.is_file()]
            if candidates:
                _add_warning(
                    report,
                    "entry_file",
                    f"入口脚本没有在 source_dir 根部找到，但在子目录中找到：{candidates[0]}",
                    f"建议把 entry_file 改成相对 source_dir 的路径，例如：\"{candidates[0].relative_to(source_dir).as_posix()}\"。",
                )
                entry_path = candidates[0]
            else:
                _add_error(report, "entry_file", f"未找到 Python 入口脚本：{entry_file}", "确认入口 .py 文件名是否正确，并且它位于 source_dir 下。")
                _add_missing(report, source_dir / entry_file, "缺少 Python 入口脚本", "修改 entry_file，或把入口脚本放到该位置。")

    if python_env_mode not in {"create_venv", "existing"}:
        _add_error(report, "python_env_mode", f"python_env_mode 不支持：{python_env_mode}", "只能填写 create_venv 或 existing。create_venv 表示系统创建独立环境；existing 表示使用已有 Python 环境。")

    python_executable = ""
    if python_executable_raw:
        python_exe_path = _resolve_validation_path(python_executable_raw, config_path.parent)
        python_executable = str(python_exe_path)
        if not python_exe_path.exists() or not python_exe_path.is_file():
            _add_error(report, "python_executable", f"指定的 Python 解释器不存在：{python_exe_path}", "检查 python_executable 路径是否正确，例如 D:/Python/Python38/python.exe 或 D:/envs/fy4/python.exe。")
            _add_missing(report, python_exe_path, "缺少指定 Python 解释器", "安装该 Python 环境，或修改 python_executable。")
    elif python_env_mode == "existing":
        _add_error(report, "python_executable", "existing 模式必须指定 python_executable", "添加已有环境的 python.exe 路径，例如：\"python_executable\": \"D:/envs/fy4/python.exe\"。")
    else:
        _add_warning(report, "python_executable", "未指定 python_executable，create_venv 模式会使用后端当前 Python 创建虚拟环境", "如果需要固定 Python 版本，可以填写基础 Python 路径。")

    param_json = None
    param_json_path_str = ""
    param_template = module_cfg.get("param_template")
    if isinstance(param_template, dict):
        param_json = param_template
        _add_warning(report, "param_template", "使用了内嵌 param_template，将不会读取单独的参数 JSON 文件", "如果希望参数模板单独维护，可以改用 param_json_path。")
    elif "param_template" in module_cfg and not isinstance(param_template, dict):
        _add_error(report, "param_template", "param_template 必须是对象", "要么删除 param_template 并填写 param_json_path，要么写成 JSON 对象。")
    else:
        param_json_raw = str(_get_python_cfg_value(module_cfg, ["param_json_path", "config_json"], "")).strip()
        if not param_json_raw:
            if source_dir and source_dir.exists() and source_dir.is_dir():
                param_json_path = (source_dir / "config.json").resolve()
                _add_warning(report, "param_json_path", "未填写 param_json_path，系统会默认读取源码目录下的 config.json", "建议明确填写：\"param_json_path\": \"config.json\"。")
            else:
                param_json_path = _resolve_validation_path("config.json", config_path.parent)
        else:
            param_json_path = _resolve_validation_path(param_json_raw, config_path.parent)
        param_json_path_str = str(param_json_path)
        if not param_json_path.exists() or not param_json_path.is_file():
            _add_error(report, "param_json_path", f"参数 JSON 文件不存在：{param_json_path}", "检查 param_json_path 是否写错；相对路径按 python_module.json 所在目录计算。")
            _add_missing(report, param_json_path, "缺少参数 JSON 文件", "把 config.json 放到该位置，或修改 param_json_path。")
        else:
            param_json = _load_json_for_validation(param_json_path, report, "参数 JSON")
            if param_json is not None and not isinstance(param_json, dict):
                _add_error(report, "param_json_path", "参数 JSON 顶层必须是对象", "参数 JSON 应写成键值对对象，例如 {\"input_dir\": \"...\", \"output_dir\": \"...\"}。")
                param_json = None

    if source_dir and source_dir.exists() and source_dir.is_dir():
        req = source_dir / "requirements.txt"
        if python_env_mode == "create_venv" and not req.exists():
            _add_warning(report, "requirements.txt", "源码目录中未找到 requirements.txt", "如果模块依赖第三方库，建议在源码目录放 requirements.txt，系统创建环境时会自动安装。")
        if python_env_mode == "existing" and req.exists():
            _add_warning(report, "python_env_mode", "当前使用 existing 模式，系统不会自动给已有 Python 环境安装 requirements.txt", "请提前在该环境中安装 requirements.txt 里的依赖，或改为 create_venv。")

    inputs: list[dict] = []
    if isinstance(param_json, dict):
        add_chinese_path_errors_to_report(report, param_json, "参数 JSON 路径")
        try:
            inputs = infer_inputs_from_param_json(param_json)
        except Exception as exc:
            _add_warning(report, "param_json_path", f"参数自动识别失败：{type(exc).__name__}: {exc}", "检查参数 JSON 中的值是否能被序列化；也可以先简化参数 JSON。")

    report["module"] = {
        "module_id": module_id,
        "module_name": module_name,
        "tool_type": tool_type,
        "entry_file": entry_file,
        "entry_path": str(entry_path) if entry_path else "",
        "source_dir": str(source_dir) if source_dir else "",
        "param_json_path": param_json_path_str,
        "description": description,
        "python_executable": python_executable or python_executable_raw,
        "python_env_mode": python_env_mode,
    }
    report["inputs"] = inputs
    report["count"] = len(inputs)

    _dedupe_report_items(report)
    report["ok"] = len(report.get("errors") or []) == 0
    report["can_install"] = report["ok"] and len(report.get("missing_files") or []) == 0
    return report

@app.post("/api/auth/login")
def api_login(payload: LoginRequest):
    user = verify_user(payload.username, payload.password, payload.role)
    if not user:
        raise HTTPException(status_code=401, detail="用户名、密码或身份不正确")

    token = create_token(user)
    return {
        "token": token,
        "user": sanitize_user(user),
    }


@app.post("/api/auth/register")
def api_register(payload: RegisterRequest):
    try:
        user = register_user(
            payload.username,
            payload.password,
            payload.security_question,
            payload.security_answer,
        )
        return {"ok": True, "user": sanitize_user(user)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

def install_module_from_folder(
    folder_path: Path,
    tool_type: str | None = None,
    collect_dependencies: bool = True,
) -> dict:
    """安装本地 C++/可执行模块文件夹，并在复制前校验 module.json 和缺失文件。"""
    validation = validate_cpp_module_folder(
        folder_path,
        tool_type=tool_type,
        collect_dependencies=True,
        copy_dependencies=False,
    )
    if not validation.get("can_install"):
        raise_cpp_validation_error(validation)

    module_data = validation["module"]
    module_root = Path(validation["module_root"])
    selected_tool_type = (
        normalize_tool_key(tool_type or module_data.get("tool_type") or "")
        or guess_module_tool_type(module_data)
    )
    module_data["tool_type"] = selected_tool_type
    ensure_toolbar_exists(selected_tool_type)

    return install_validated_cpp_module(
        module_root=module_root,
        module_data=module_data,
        collect_dependencies=collect_dependencies,
    )

@app.post("/api/admin/modules/validate-cpp-folder")
def api_validate_cpp_module_folder(
    payload: InstallModuleFolderRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)

    folder_path = Path(payload.folder_path).expanduser().resolve()
    try:
        report = validate_cpp_module_folder(
            folder_path,
            tool_type=payload.tool_type,
            collect_dependencies=payload.auto_collect_dependencies,
            copy_dependencies=False,
        )
    except Exception as exc:
        # 校验接口不能只给前端一个 500 / Failed to fetch。
        # 任何未预料的异常都转换成结构化报告，前端可以直接显示“哪里错了”。
        report = make_cpp_validation_report(folder_path)
        _add_error(
            report,
            "backend.validate_cpp_module_folder",
            f"后端校验过程异常：{type(exc).__name__}: {exc}",
            "查看后端控制台日志；也可以先检查 module.json 是否存在、JSON 是否合法、executable 指向的 exe 是否存在。",
            traceback="".join(traceback.format_exception_only(type(exc), exc)).strip(),
        )
        _dedupe_report_items(report)

    return {
        "ok": bool(report.get("ok")),
        "message": "可执行模块校验通过" if report.get("ok") else "可执行模块校验未通过",
        **report,
    }


@app.post("/api/admin/modules/install-folder")
def api_install_module_folder(
    payload: InstallModuleFolderRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)

    try:
        module_data = install_module_from_folder(
            Path(payload.folder_path).expanduser().resolve(),
            payload.tool_type,
            collect_dependencies=payload.auto_collect_dependencies,
        )
    except HTTPException:
        raise
    except Exception as exc:
        report = make_cpp_validation_report(payload.folder_path)
        _add_error(
            report,
            "backend.install_folder",
            f"安装过程异常：{type(exc).__name__}: {exc}",
            "先点“检查模块规范”，按错误提示修正后再安装；如果检查通过但安装失败，请查看后端控制台日志。",
            traceback="".join(traceback.format_exception_only(type(exc), exc)).strip(),
        )
        raise_cpp_validation_error(report)

    return {
        "ok": True,
        "message": "可执行模块文件夹安装成功",
        "module": module_data,
    }
"""新增 /api/admin/modules/upload-python

这个接口负责：

接收 Python 源码 zip；
使用 PyInstaller 打包 exe；
放入 backend/installed_modules/{module_id}/；
自动生成或读取 module.json；
写入 modules.json。"""
@app.post("/api/admin/modules/upload-python")
def api_upload_python_module(
    file: UploadFile = File(...),
    module_id: str = Form(...),
    module_name: str = Form(...),
    entry_file: str = Form("main.py"),
    tool_type: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
):
    user = get_current_user(authorization)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以上传模块")

    safe_module_id = sanitize_filename(module_id).strip()
    if not safe_module_id:
        raise HTTPException(status_code=400, detail="模块 ID 不能为空")

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 Python 源码 zip 包")

    upload_tmp = Path(tempfile.mkdtemp(prefix="python_upload_"))

    try:
        zip_path = upload_tmp / sanitize_filename(file.filename)

        with zip_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)

        source_dir, exe_path = build_python_source_to_exe(
            source_zip=zip_path,
            module_id=safe_module_id,
            entry_file=entry_file,
        )

        target_dir = INSTALLED_MODULES_DIR / safe_module_id
        if target_dir.exists():
            shutil.rmtree(target_dir)

        target_dir.mkdir(parents=True, exist_ok=True)

        # 复制源码，方便后续查看和维护
        source_target_dir = target_dir / "source"
        shutil.copytree(source_dir, source_target_dir, dirs_exist_ok=True)

        # 复制 exe
        final_exe_path = target_dir / f"{safe_module_id}.exe"
        shutil.copy2(exe_path, final_exe_path)

        # 如果源码包里有 module.json，优先读取
        module_json_candidates = list(source_dir.rglob("module.json"))
        if module_json_candidates:
            module_data = json.loads(module_json_candidates[0].read_text(encoding="utf-8"))
        else:
            module_data = {
                "id": safe_module_id,
                "name": module_name,
                "description": "Python 源码自动打包生成的模块",
                "enabled": True,
                "inputs": [],
            }

        selected_tool_type = (
            normalize_tool_key(tool_type or module_data.get("tool_type") or "")
            or guess_module_tool_type(module_data)
        )

        module_data["id"] = safe_module_id
        module_data["name"] = module_name or module_data.get("name") or safe_module_id
        module_data["tool_type"] = selected_tool_type
        module_data["enabled"] = module_data.get("enabled", True)

        ensure_toolbar_exists(selected_tool_type)

        # 关键：保存项目相对路径，不保存 D:/... 绝对路径
        module_data["executable"] = to_project_relative_path(final_exe_path)
        module_data["working_dir"] = to_project_relative_path(target_dir)

        upsert_module(module_data)

        return {
            "ok": True,
            "message": "Python 模块打包并安装成功",
            "module": module_data,
        }

    finally:
        shutil.rmtree(upload_tmp, ignore_errors=True)

@app.post("/api/admin/modules/upload-python-folder")
def api_upload_python_folder_module(
    payload: PythonFolderModuleUploadRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)

    raise_if_chinese_paths(
        {
            "folder_path": payload.folder_path,
            "source_dir": payload.source_dir,
            "param_json_path": payload.param_json_path,
        },
        "Python 模块安装路径",
    )

    try:
        # 新模式：用户只选择 Python 模块文件夹
        if str(payload.folder_path or "").strip():
            config_path = resolve_python_module_config_from_folder(
                payload.folder_path,
                payload.config_filename or "python_module.json",
            )

            validation = validate_python_module_config_file(str(config_path))
            if not validation.get("can_install"):
                raise_python_validation_error(validation)

            config, _ = load_python_module_config(str(config_path))

            module_data = install_python_venv_module_from_values(
                module_id=config["module_id"],
                module_name=config["module_name"],
                source_dir=config["source_dir"],
                entry_file=config["entry_file"],
                tool_type=config["tool_type"],
                description=config["description"],
                param_json_path=config["param_json_path"],
                param_json=config["param_json"],
                python_executable=config.get("python_executable") or "",
                python_env_mode=config.get("python_env_mode") or "create_venv",
            )

            return {
                "ok": True,
                "message": "Python 模块文件夹安装成功",
                "module": module_data,
                "config_path": str(config_path),
                "validation": validation,
            }

        module_data = install_python_venv_module_from_values(
            module_id=payload.module_id,
            module_name=payload.module_name,
            source_dir=payload.source_dir,
            entry_file=payload.entry_file or "main.py",
            tool_type=payload.tool_type,
            description=payload.description,
            param_json_path=payload.param_json_path,
        )

        return {
            "ok": True,
            "message": "Python 源码模块已创建独立环境并安装成功",
            "module": module_data,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Python 模块文件夹安装过程异常：\n"
                f"{type(exc).__name__}: {exc}\n\n"
                + traceback.format_exc()
            ),
        )
@app.get("/api/auth/forgot-password/question")
def api_forgot_password_question(username: str):
    try:
        return {"question": get_security_question(username)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/forgot-password/reset")
def api_forgot_password_reset(payload: ForgotPasswordResetRequest):
    try:
        reset_password_by_security_answer(
            payload.username,
            payload.answer,
            payload.new_password,
        )
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/logout")
def api_logout(authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    token = authorization.split(" ", 1)[1].strip()
    remove_token(token)
    return {"ok": True}


@app.get("/api/auth/me")
def api_me(authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    return sanitize_user(user)


# =========================
# 用户管理接口
# =========================
@app.get("/api/admin/users")
def api_list_users(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return [sanitize_user(u) for u in load_users()]


@app.post("/api/admin/users")
def api_add_user(payload: AddUserRequest, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        user = create_user(
            payload.username,
            payload.password,
            payload.role,
            payload.security_question,
            payload.security_answer,
        )
        return {"ok": True, "user": sanitize_user(user)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/admin/users/{username}")
def api_delete_user(username: str, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        delete_user(username)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/admin/users/{username}/role")
def api_update_user_role(
    username: str,
    payload: UpdateUserRoleRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        update_user_role(username, payload.role)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/admin/users/{username}/enabled")
def api_update_user_enabled(
    username: str,
    payload: UpdateUserEnabledRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        update_user_enabled(username, payload.enabled)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/admin/users/{username}/password")
def api_reset_user_password(
    username: str,
    payload: ResetUserPasswordRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        admin_reset_password(username, payload.new_password)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =========================
# 工具栏 / 工具类型接口
# =========================
@app.get("/api/toolbars")
def api_list_toolbars(authorization: str | None = Header(default=None)):
    get_current_user(authorization)
    return load_toolbars()


@app.get("/api/admin/toolbars")
def api_admin_list_toolbars(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return load_toolbars()


@app.post("/api/admin/toolbars")
def api_add_toolbar(payload: ToolBarSaveRequest, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        item = add_toolbar(payload.key, payload.label)
        return {"ok": True, "toolbar": item}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/admin/toolbars/{toolbar_key}")
def api_update_toolbar(
    toolbar_key: str,
    payload: ToolBarUpdateRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        item = update_toolbar(toolbar_key, payload.key, payload.label)
        return {"ok": True, "toolbar": item}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/admin/toolbars/{toolbar_key}")
def api_delete_toolbar(toolbar_key: str, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        result = delete_toolbar(toolbar_key)
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# 有些本地打包/代理环境对 DELETE 支持不好，前端删除工具栏统一走这个 POST 接口。
@app.post("/api/admin/toolbars/{toolbar_key}/delete")
def api_delete_toolbar_post(toolbar_key: str, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        result = delete_toolbar(toolbar_key)
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =========================
# 模块接口
# =========================
@app.get("/api/modules")
def api_list_modules(authorization: str | None = Header(default=None)):
    get_current_user(authorization)
    modules = [m for m in load_modules() if m.get("enabled", True)]
    return modules


@app.get("/api/admin/modules")
def api_admin_list_modules(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return load_modules()


@app.post("/api/admin/modules")
def api_save_module(payload: ModuleSaveRequest, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    module_data = payload.model_dump()

    raise_if_chinese_paths(
        {
            "executable": module_data.get("executable"),
            "working_dir": module_data.get("working_dir"),
            "command_template": module_data.get("command_template"),
            "inputs": module_data.get("inputs"),
        },
        "模块配置路径",
    )

    upsert_module(module_data)
    return {"ok": True, "module": module_data}


@app.delete("/api/admin/modules/{module_id}")
def api_delete_module(module_id: str, authorization: str | None = Header(default=None)):
    require_admin(authorization)

    result = remove_module(module_id)

    if not result.get("removed"):
        raise HTTPException(status_code=404, detail="模块不存在")

    return {
        "ok": True,
        "message": "模块及本地文件已删除",
        **result,
    }


@app.post("/api/admin/modules/upload")
def api_upload_module_zip(
    file: UploadFile = File(...),
    tool_type: str = Form("cloud"),
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)

    selected_tool_type = normalize_tool_key(tool_type) or "cloud"
    ensure_toolbar_exists(selected_tool_type)

    suffix = Path(file.filename or "module.zip").suffix or ".zip"
    temp_zip = Path(tempfile.mktemp(suffix=suffix))
    try:
        with temp_zip.open("wb") as f:
            f.write(file.file.read())

        module_data = install_uploaded_zip(temp_zip, selected_tool_type)
        return {"ok": True, "module": module_data}
    finally:
        if temp_zip.exists():
            temp_zip.unlink(missing_ok=True)
class PythonModuleFolderRequest(BaseModel):
    folder_path: str
    config_filename: str = "python_module.json"


@app.post("/api/admin/modules/validate-python-folder")
def api_validate_python_module_folder(
    payload: PythonModuleFolderRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)

    if collect_chinese_path_items({"Python 模块文件夹": payload.folder_path}, "Python 模块文件夹"):
        report = make_python_validation_report(str(payload.folder_path or ""))
        add_chinese_path_errors_to_report(report, {"Python 模块文件夹": payload.folder_path}, "Python 模块文件夹")
        return report

    try:
        config_path = resolve_python_module_config_from_folder(
            payload.folder_path,
            payload.config_filename or "python_module.json",
        )
        return validate_python_module_config_file(str(config_path))
    except Exception as exc:
        report = make_python_validation_report(str(payload.folder_path or ""))
        _add_error(
            report,
            "python_module_folder",
            f"Python 模块文件夹检查失败：{type(exc).__name__}: {exc}",
            "确认该文件夹下存在 python_module.json、config.json、requirements.txt 和入口 .py 文件。",
            traceback="".join(traceback.format_exception_only(type(exc), exc)).strip(),
        )
        _dedupe_report_items(report)
        return report

@app.post("/api/admin/modules/validate-python-module-config")
def api_validate_python_module_config(
    payload: PythonModuleConfigRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)

    try:
        return validate_python_module_config_file(payload.path)
    except Exception as exc:
        report = make_python_validation_report(payload.path)
        _add_error(
            report,
            "backend.validate_python_module_config_file",
            f"后端校验过程异常：{type(exc).__name__}: {exc}",
            "查看后端控制台日志；也可以先检查 python_module.json 是否存在、JSON 是否合法、source_dir/entry_file/param_json_path 是否正确。",
            traceback="".join(traceback.format_exception_only(type(exc), exc)).strip(),
        )
        _dedupe_report_items(report)
        return report

@app.post("/api/admin/modules/parse-python-module-config")
def api_parse_python_module_config(
    payload: PythonModuleConfigRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)

    validation = validate_python_module_config_file(payload.path)
    if not validation.get("can_install"):
        raise_python_validation_error(validation)

    config, config_path = load_python_module_config(payload.path)
    inputs = infer_inputs_from_param_json(config["param_json"])

    return {
        "ok": True,
        "config_path": str(config_path),
        "validation": validation,
        "module": {
            "module_id": config["module_id"],
            "module_name": config["module_name"],
            "tool_type": config["tool_type"],
            "entry_file": config["entry_file"],
            "source_dir": config["source_dir"],
            "param_json_path": config["param_json_path"],
            "description": config["description"],
            "python_executable": config.get("python_executable") or "",
            "python_env_mode": config.get("python_env_mode") or "create_venv",
        },
        "inputs": inputs,
        "count": len(inputs),
    }


@app.post("/api/admin/modules/upload-python-config")
def api_upload_python_config_module(
    payload: PythonModuleConfigRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)

    raise_if_chinese_paths({"Python 模块配置 JSON": payload.path}, "Python 模块配置 JSON")

    validation = validate_python_module_config_file(payload.path)
    if not validation.get("can_install"):
        raise_python_validation_error(validation)

    config, _ = load_python_module_config(payload.path)

    module_data = install_python_venv_module_from_values(
        module_id=config["module_id"],
        module_name=config["module_name"],
        source_dir=config["source_dir"],
        entry_file=config["entry_file"],
        tool_type=config["tool_type"],
        description=config["description"],
        param_json_path=config["param_json_path"],
        param_json=config["param_json"],
        python_executable=config.get("python_executable") or "",
        python_env_mode=config.get("python_env_mode") or "create_venv",
    )

    return {
        "ok": True,
        "message": "Python 模块配置 JSON 安装成功",
        "module": module_data,
    }
@app.get("/api/admin/modules/drop-zips")
def api_list_module_drop_zips(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    MODULE_DROP_DIR.mkdir(parents=True, exist_ok=True)
    zips = []
    for p in sorted(MODULE_DROP_DIR.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
        stat = p.stat()
        zips.append({
            "name": p.name,
            "path": str(p.resolve()),
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return {"drop_dir": str(MODULE_DROP_DIR.resolve()), "items": zips}


def archive_installed_zip(zip_path: Path):
    archive_dir = MODULE_DROP_DIR / "installed"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / zip_path.name
    if target.exists():
        target = archive_dir / f"{zip_path.stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}{zip_path.suffix}"
    shutil.move(str(zip_path), str(target))


@app.post("/api/admin/modules/install-local-drop")
def api_install_modules_from_local_drop(
    payload: InstallLocalDropRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    MODULE_DROP_DIR.mkdir(parents=True, exist_ok=True)

    selected_tool_type = normalize_tool_key(payload.tool_type) or "cloud"
    ensure_toolbar_exists(selected_tool_type)

    if payload.filename:
        safe_name = sanitize_filename(payload.filename)
        candidates = [MODULE_DROP_DIR / safe_name]
    else:
        candidates = sorted(MODULE_DROP_DIR.glob("*.zip"), key=lambda x: x.stat().st_mtime)

    installed = []
    failed = []
    for zip_path in candidates:
        if not zip_path.exists() or not zip_path.is_file() or zip_path.suffix.lower() != ".zip":
            failed.append({"name": zip_path.name, "error": "zip 文件不存在"})
            continue
        try:
            module_data = install_uploaded_zip(zip_path, selected_tool_type)
            installed.append(module_data)
            archive_installed_zip(zip_path)
        except HTTPException as e:
            failed.append({"name": zip_path.name, "error": e.detail})
        except Exception as e:
            failed.append({"name": zip_path.name, "error": str(e)})

    return {
        "ok": len(failed) == 0,
        "drop_dir": str(MODULE_DROP_DIR.resolve()),
        "installed": installed,
        "failed": failed,
    }



# =========================
# 并行执行辅助逻辑
# =========================
VALID_PARALLEL_MODES = {"none", "auto", "single_file", "folder_chunks", "module_internal"}
DEFAULT_PARALLEL_PATTERNS = "*.tif;*.tiff;*.nc;*.hdf;*.h5"


def clamp_parallel_workers(value: int | str | None, max_workers: int | None = None) -> int:
    try:
        n = int(value or 1)
    except Exception:
        n = 1

    try:
        limit = int(max_workers or task_manager.max_process_slots or os.cpu_count() or 1)
    except Exception:
        limit = 1
    limit = max(1, limit)
    return max(1, min(n, limit))




# 固定资源/模型文件的安全并行估算：这些文件不会复制，但每个子进程通常会各自加载到内存。
# 因此不能只按 CPU 核数给进程数，需要同时看 CPU、内存、磁盘余量和模型大小。
FIXED_RESOURCE_SUFFIXES = {
    ".pkl", ".pickle", ".joblib", ".model", ".onnx", ".h5", ".hdf5",
    ".npy", ".npz", ".pt", ".pth", ".ckpt", ".bin",
    ".lut", ".tif", ".tiff", ".hdf", ".nc", ".nc4",
}
FIXED_RESOURCE_DIR_HINTS = {"models", "model", "weights", "weight", "resources", "resource", "lut", "luts", "data"}


def _gb(num_bytes: int | float | None) -> float:
    try:
        return float(num_bytes or 0) / (1024 ** 3)
    except Exception:
        return 0.0


def _resolve_module_path_for_safety(raw_value: str | None) -> Path | None:
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    try:
        if not p.is_absolute():
            p = (PROJECT_ROOT / p).resolve()
        else:
            p = p.resolve()
        return p
    except Exception:
        return None


def estimate_fixed_resource_bytes(module: dict) -> tuple[int, list[str]]:
    """估算模块固定资源体积。固定资源不复制，但并发进程可能各自加载，所以用于限制安全并发数。"""
    roots: list[Path] = []
    for key in ["source_dir", "working_dir"]:
        p = _resolve_module_path_for_safety(str(module.get(key) or ""))
        if p and p.exists() and p.is_dir():
            roots.append(p)

    executable = _resolve_module_path_for_safety(str(module.get("executable") or module.get("entry") or ""))
    if executable and executable.exists():
        roots.append(executable.parent)

    # 去重，避免同一个目录重复扫描。
    uniq_roots: list[Path] = []
    seen_roots = set()
    for root in roots:
        try:
            key = str(root.resolve()).lower()
        except Exception:
            key = str(root).lower()
        if key not in seen_roots:
            seen_roots.add(key)
            uniq_roots.append(root)

    total = 0
    examples: list[str] = []
    seen_files = set()
    for root in uniq_roots:
        try:
            for item in root.rglob("*"):
                if not item.is_file():
                    continue
                try:
                    resolved = str(item.resolve()).lower()
                    if resolved in seen_files:
                        continue
                    seen_files.add(resolved)
                    suffix = item.suffix.lower()
                    parts_lower = {part.lower() for part in item.parts}
                    size = item.stat().st_size
                    is_fixed = suffix in FIXED_RESOURCE_SUFFIXES or bool(parts_lower & FIXED_RESOURCE_DIR_HINTS)
                    if not is_fixed:
                        continue
                    # 小配置文件不参与内存估算，避免 resources 里 1KB json 把模块误判为重资源。
                    if size < 10 * 1024 * 1024 and suffix not in {".pkl", ".pt", ".pth", ".onnx", ".h5", ".hdf5"}:
                        continue
                    total += int(size)
                    if len(examples) < 5:
                        try:
                            examples.append(f"{item.relative_to(root)} ({_gb(size):.2f}GB)")
                        except Exception:
                            examples.append(f"{item.name} ({_gb(size):.2f}GB)")
                except Exception:
                    continue
        except Exception:
            continue
    return total, examples


def get_runtime_pressure_snapshot(path_for_disk: str | Path | None = None) -> dict:
    """读取当前 CPU/内存/磁盘状态。psutil 不存在时退化为磁盘余量检查。"""
    snapshot = {
        "cpu_percent": None,
        "memory_percent": None,
        "memory_available_gb": None,
        "disk_percent": None,
        "disk_free_gb": None,
        "disk_path": "",
    }

    try:
        import psutil  # type: ignore
        snapshot["cpu_percent"] = float(psutil.cpu_percent(interval=0.35))
        mem = psutil.virtual_memory()
        snapshot["memory_percent"] = float(mem.percent)
        snapshot["memory_available_gb"] = _gb(mem.available)
    except Exception:
        # 没有 psutil 时，Windows 下用 wmic 兜底，保证前端 CPU/内存不会一直显示为 -。
        if os.name == "nt":
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
                vals = []
                for line in (result.stdout or "").splitlines():
                    line = line.strip()
                    if line.lower().startswith("loadpercentage="):
                        vals.append(float(line.split("=", 1)[1].strip()))
                if vals:
                    snapshot["cpu_percent"] = max(0.0, min(100.0, sum(vals) / len(vals)))
            except Exception:
                pass
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
                    snapshot["memory_percent"] = max(0.0, min(100.0, (1.0 - free_kb / total_kb) * 100.0))
                    snapshot["memory_available_gb"] = free_kb / 1024.0 / 1024.0
            except Exception:
                pass

    disk_path = Path(path_for_disk or RUNTIME_DIR)
    try:
        disk_path = disk_path.resolve()
        if disk_path.is_file():
            disk_path = disk_path.parent
    except Exception:
        disk_path = Path(RUNTIME_DIR)

    try:
        import psutil  # type: ignore
        usage = psutil.disk_usage(str(disk_path))
        snapshot["disk_percent"] = float(usage.percent)
        snapshot["disk_free_gb"] = _gb(usage.free)
        snapshot["disk_path"] = str(disk_path)
    except Exception:
        try:
            usage = shutil.disk_usage(str(disk_path))
            total = float(usage.total or 1)
            used = float(usage.used)
            snapshot["disk_percent"] = used / total * 100.0
            snapshot["disk_free_gb"] = _gb(usage.free)
            snapshot["disk_path"] = str(disk_path)
        except Exception:
            pass

    return snapshot

def resolve_run_parallel_mode(module: dict, inputs: dict, workers: int) -> str:
    """
    统一决定任务运行方式，避免 batch_group / 平台拆分 / 模块内部并行混在一起。

    返回：
    - none：普通单进程运行
    - module_internal：模块自己处理并行，平台不拆任务，只把 parallel_workers 写入 config.json
    - platform_split：平台按文件/文件夹拆成多个子进程
    - batch_group：多输入目录按时次匹配，生成批处理子任务
    """
    cfg = normalize_parallel_config(module)
    mode = str(cfg.get("mode") or "auto").strip() or "auto"

    if mode not in VALID_PARALLEL_MODES:
        mode = "auto"

    # 显式 none：永远不拆。
    if mode == "none":
        return "none"

    # 显式模块内部并行：永远不拆。
    if mode == "module_internal":
        return "module_internal"

    # 显式批处理：按多输入目录匹配。
    if mode == "batch_group":
        return "batch_group"

    # 显式平台拆分。
    if mode in {"single_file", "folder_chunks"}:
        return "platform_split" if workers > 1 else "none"

    # auto 模式：
    # 1. 如果配置了 batch_role，例如 B01/B03/B06/SOLAR，走批处理；
    # 2. 否则 workers > 1 才走平台拆分；
    # 3. workers == 1 就普通运行。
    if _is_batch_request(module, inputs):
        return "batch_group"

    if workers > 1:
        return "platform_split"

    return "none"
def auto_adjust_parallel_workers(module: dict, inputs: dict, requested_workers: int) -> tuple[int, dict]:
    """轻量化并行调整。

    这版只在 CPU/内存/磁盘已经接近不可用时才降低进程数；
    固定模型/资源大小只做日志提示，不再参与降级。
    这样用户选择 4/6/8 时，系统不会因为 pkl 大小动不动降成 1，
    真正的保护交给 TaskManager 在启动每个子进程前动态暂停。
    """
    requested = clamp_parallel_workers(requested_workers, task_manager.max_process_slots)
    safe = requested
    reasons: list[str] = []

    disk_probe = RUNTIME_DIR
    try:
        for field in module.get("inputs", []) or []:
            key = field.get("key")
            if key and is_output_field(field) and inputs.get(key):
                disk_probe = Path(str(inputs.get(key)))
                break
    except Exception:
        pass

    pressure = get_runtime_pressure_snapshot(disk_probe)
    cpu = pressure.get("cpu_percent")
    mem_percent = pressure.get("memory_percent")
    mem_avail = pressure.get("memory_available_gb")
    disk_percent = pressure.get("disk_percent")
    disk_free = pressure.get("disk_free_gb")

    # 只在系统已经接近满载时，才在启动前降级。
    if cpu is not None:
        if cpu >= 98:
            safe = min(safe, 1)
            reasons.append(f"当前 CPU 使用率 {cpu:.1f}% 已接近满载")
        elif cpu >= 95:
            safe = min(safe, max(2, requested // 2))
            reasons.append(f"当前 CPU 使用率 {cpu:.1f}% 很高，先降低一部分并发")

    # 内存只做临界保护；不再因为可用内存 2GB 左右就直接降为 1。
    if mem_percent is not None and mem_avail is not None:
        if mem_percent >= 99 or mem_avail <= 0.3:
            safe = min(safe, 1)
            reasons.append(f"内存几乎耗尽：已用 {mem_percent:.1f}%，可用 {mem_avail:.1f}GB")
        elif mem_percent >= 97 or mem_avail <= 0.8:
            safe = min(safe, max(2, requested // 2))
            reasons.append(f"内存压力很高：已用 {mem_percent:.1f}%，可用 {mem_avail:.1f}GB")

    # 固定模型/资源只统计展示，不再限制进程数。
    resource_bytes, resource_examples = estimate_fixed_resource_bytes(module)
    resource_gb = _gb(resource_bytes)

    # 磁盘也只做临界保护。固定资源不复制后，磁盘压力主要来自输出文件。
    if disk_percent is not None and disk_free is not None:
        if disk_percent >= 99.5 or disk_free <= 0.5:
            safe = min(safe, 1)
            reasons.append(f"磁盘空间几乎耗尽：使用率 {disk_percent:.1f}%，剩余 {disk_free:.1f}GB")
        elif disk_percent >= 99 or disk_free <= 1.0:
            safe = min(safe, max(2, requested // 2))
            reasons.append(f"磁盘空间很紧：使用率 {disk_percent:.1f}%，剩余 {disk_free:.1f}GB")

    safe = max(1, min(requested, safe))
    adjusted = safe < requested
    report = {
        "requested_workers": requested,
        "effective_workers": safe,
        "adjusted": adjusted,
        "reasons": reasons,
        "cpu_percent": cpu,
        "memory_percent": mem_percent,
        "memory_available_gb": mem_avail,
        "disk_percent": disk_percent,
        "disk_free_gb": disk_free,
        "fixed_resource_gb": resource_gb,
        "fixed_resource_examples": resource_examples,
    }
    return safe, report


def apply_parallel_adjustment_to_inputs(inputs: dict, report: dict) -> dict:
    new_inputs = dict(inputs or {})
    requested = int(report.get("requested_workers") or 1)
    effective = int(report.get("effective_workers") or requested)
    new_inputs["parallel_workers"] = effective
    new_inputs["_parallel_workers"] = effective
    new_inputs["_requested_parallel_workers"] = requested
    new_inputs["_effective_parallel_workers"] = effective
    new_inputs["_parallel_auto_adjusted"] = bool(report.get("adjusted"))
    if report.get("adjusted"):
        reason_text = "；".join(report.get("reasons") or []) or "系统负载保护"
        new_inputs["_parallel_adjust_reason"] = reason_text
    return new_inputs


def parse_parallel_patterns(pattern_text: str | None) -> list[str]:
    raw = str(pattern_text or DEFAULT_PARALLEL_PATTERNS)
    parts = []
    for item in raw.replace(",", ";").split(";"):
        item = item.strip()
        if item:
            parts.append(item)
    return parts or ["*"]


def field_meta(module: dict, key: str) -> dict:
    for item in module.get("inputs", []) or []:
        if item.get("key") == key:
            return item
    return {}


def choose_parallel_input_key(module: dict, inputs: dict) -> str:
    explicit = str(normalize_parallel_config(module).get("input_key") or "").strip()
    if explicit:
        return explicit

    input_fields = module.get("inputs", []) or []
    for field in input_fields:
        key = field.get("key")
        if key in inputs and field.get("type") in {"file_path", "dir_path"}:
            return key

    preferred_words = ["input", "infile", "file", "inpath", "folder", "dir", "path"]
    for word in preferred_words:
        for key, value in inputs.items():
            if word in str(key).lower() and value not in ("", None):
                return key

    for key, value in inputs.items():
        if value not in ("", None):
            return key

    return ""


def discover_batch_files(path_value: str, patterns: list[str]) -> list[Path]:
    root = Path(path_value).expanduser()
    if root.is_file():
        return [root.resolve()]
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail=f"并行输入路径不存在或不是文件夹: {root}")

    found: list[Path] = []
    seen = set()
    for pattern in patterns:
        for item in root.rglob(pattern):
            if item.is_file():
                rp = item.resolve()
                if rp not in seen:
                    seen.add(rp)
                    found.append(rp)
    found.sort(key=lambda x: str(x).lower())
    return found


def split_evenly(items: list[Path], parts: int) -> list[list[Path]]:
    parts = max(1, min(parts, len(items)))
    buckets = [[] for _ in range(parts)]
    for idx, item in enumerate(items):
        buckets[idx % parts].append(item)
    return [bucket for bucket in buckets if bucket]


def link_or_copy_file(src: Path, dst: Path) -> str:
    """为并行子任务创建输入文件引用，默认只创建符号链接，不复制输入大文件。

    说明：
    - symlink：目录中显示为链接，最能直观看出不是复制文件；
    - hardlink：不是物理复制，但资源管理器里看起来像普通文件，容易被误认为复制；
    - copy/copy2：本函数已彻底禁用，不会再把 NC/HDF/TIF 大文件复制到 runtime。

    默认策略：
    - 只尝试 symlink；
    - 如果需要兼容不支持 symlink 的环境，可显式设置 LOCAL_WEB_ALLOW_INPUT_HARDLINKS=1，
      此时 symlink 失败后才允许 hardlink。
    """
    src = Path(src).resolve()
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    if not src.exists() or not src.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"并行输入文件不存在或不是文件: {src}",
        )

    if dst.exists() or dst.is_symlink():
        try:
            dst.unlink()
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"无法清理旧的子任务输入文件: {dst}，原因: {type(exc).__name__}: {exc}",
            )

    errors: list[str] = []

    # 默认只用 symlink，避免 hardlink 在资源管理器里显示成“普通大文件”而被误判为复制。
    raw_order = str(os.environ.get("LOCAL_WEB_INPUT_LINK_ORDER", "symlink") or "symlink")
    order = [
        item.strip().lower()
        for item in raw_order.replace("；", ",").replace(";", ",").split(",")
        if item.strip()
    ] or ["symlink"]

    allow_symlink = str(os.environ.get("LOCAL_WEB_ALLOW_INPUT_SYMLINKS", "1")).strip().lower() not in {"0", "false", "no", "off"}
    allow_hardlink = str(os.environ.get("LOCAL_WEB_ALLOW_INPUT_HARDLINKS", "0")).strip().lower() in {"1", "true", "yes", "on"}

    def try_symlink() -> str | None:
        if not allow_symlink:
            errors.append("symlink跳过: LOCAL_WEB_ALLOW_INPUT_SYMLINKS=0")
            return None
        if not hasattr(os, "symlink"):
            errors.append("symlink失败: 当前 Python/系统不支持 os.symlink")
            return None
        try:
            os.symlink(str(src), str(dst), target_is_directory=False)
            return "symlink"
        except Exception as exc:
            errors.append(f"symlink失败: {type(exc).__name__}: {exc}")
            return None

    def try_hardlink() -> str | None:
        if not allow_hardlink:
            errors.append("hardlink跳过: LOCAL_WEB_ALLOW_INPUT_HARDLINKS 未启用")
            return None
        try:
            os.link(str(src), str(dst))
            return "hardlink"
        except Exception as exc:
            errors.append(f"hardlink失败: {type(exc).__name__}: {exc}")
            return None

    tried: set[str] = set()
    for mode in order + ["symlink"]:
        if mode in tried:
            continue
        tried.add(mode)

        if mode in {"symlink", "symbolic", "symboliclink"}:
            result = try_symlink()
            if result:
                return result
        elif mode in {"hardlink", "link"}:
            result = try_hardlink()
            if result:
                return result
        elif mode in {"copy", "copy2"}:
            errors.append("copy/copy2 已禁用：系统不再复制输入大文件到 runtime")

    raise HTTPException(
        status_code=400,
        detail={
            "message": "无法为并行子任务创建输入文件符号链接。为避免复制输入大文件，系统已中止任务。",
            "errors": [
                {
                    "field": "parallel.input_symlink",
                    "message": f"源文件: {src}；目标位置: {dst}",
                    "suggestion": "请用管理员身份运行 start_backend.bat，或开启 Windows 开发者模式，以允许创建符号链接。",
                }
            ],
            "suggestions": [
                "Windows 推荐：设置 → 系统 → 开发者选项 → 开启开发人员模式，然后重启系统。",
                "或者右键 start_backend.bat，选择“以管理员身份运行”。",
                "如果你接受 hardlink 形式，可在启动脚本中设置 LOCAL_WEB_ALLOW_INPUT_HARDLINKS=1；hardlink 不是复制，但资源管理器里看起来像普通文件。",
                "本版本已禁用 copy/copy2，不会再把 NC/HDF/TIF 大文件复制到 runtime。",
            ],
            "debug": "; ".join(errors),
        },
    )

def unique_chunk_filename(src: Path, used: set[str]) -> str:
    name = src.name
    if name not in used:
        used.add(name)
        return name
    stem, suffix = src.stem, src.suffix
    idx = 1
    while True:
        candidate = f"{stem}_{idx}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        idx += 1



def is_parasol_jd_pair_module(module: dict) -> bool:
    """PARASOL AOD 专用：输入目录里 JD/JL 成对出现，但 exe 只需要 JD 主输入。

    只对 PARASOL 模块启用，避免影响其它模块的普通批处理/并行逻辑。
    如果以后有类似模块，也可以在 module.json 的 parallel 里显式写：
      "jd_jl_pair": true
    """
    parallel_cfg = module.get("parallel") if isinstance(module.get("parallel"), dict) else {}
    if parallel_cfg.get("jd_jl_pair") is True or parallel_cfg.get("jd_only") is True:
        return True

    tags = module.get("tags") or []
    text = " ".join([
        str(module.get("id") or ""),
        str(module.get("module_id") or ""),
        str(module.get("name") or ""),
        str(module.get("module_name") or ""),
        str(module.get("description") or ""),
        str(module.get("executable") or module.get("entry") or module.get("entry_file") or ""),
        " ".join(str(x) for x in tags),
    ]).lower()

    return "parasol" in text


def is_parasol_jd_input_file(path: Path) -> bool:
    """识别 PARASOL 主输入 JD 文件。

    示例：
      P3L1TBG1017047JD_n45_00_N35_00_e115_00_E125_00
      P3L1TBG1017047JD_n45_00_N35_00_e115_00_E125_01

    配套 JL 文件会由算法/exe 根据 JD 文件名自己匹配，不应单独生成平台任务。
    """
    name = path.name.upper()
    return bool(re.search(r"JD(?=[_.-])", name))


def filter_parasol_jd_files_for_jobs(module: dict, files: list[Path]) -> list[Path]:
    if not is_parasol_jd_pair_module(module):
        return files

    jd_files = [item for item in files if is_parasol_jd_input_file(item)]
    # 只有在确实识别到 JD 文件时才过滤；如果没识别到，直接报错比继续把 JL 当任务更安全。
    return jd_files

def is_probably_dir_output(module: dict, output_key: str, output_value: str) -> bool:
    meta = field_meta(module, output_key)
    k = output_key.lower()
    label = str(meta.get("label") or "").lower()
    if meta.get("type") == "dir_path":
        return True
    if "dir" in k or "folder" in k or "目录" in label or "文件夹" in label:
        return True
    p = Path(output_value)
    return bool(output_value) and (p.exists() and p.is_dir())


def apply_single_file_output_mapping(module: dict, base_inputs: dict, input_file: Path) -> dict:
    new_inputs = dict(base_inputs)
    output_key = str(normalize_parallel_config(module).get("output_key") or "").strip()
    if not output_key or output_key not in new_inputs:
        return new_inputs

    output_value = str(new_inputs.get(output_key) or "").strip()
    if not output_value:
        return new_inputs

    if is_probably_dir_output(module, output_key, output_value):
        # 输出字段本身就是目录时，不改字段值，让模块自己在目录里生成结果。
        Path(output_value).mkdir(parents=True, exist_ok=True)
        return new_inputs

    out_path = Path(output_value)
    suffix = str(normalize_parallel_config(module).get("output_suffix") or ".tif")
    if not suffix.startswith("."):
        suffix = "." + suffix

    if out_path.suffix:
        mapped = out_path.with_name(f"{out_path.stem}_{input_file.stem}{out_path.suffix}")
    else:
        out_path.mkdir(parents=True, exist_ok=True)
        mapped = out_path / f"{input_file.stem}{suffix}"

    mapped.parent.mkdir(parents=True, exist_ok=True)
    new_inputs[output_key] = str(mapped.resolve())
    return new_inputs


def infer_parallel_mode(module: dict, inputs: dict, input_key: str) -> str:
    mode = str(normalize_parallel_config(module).get("mode") or "auto").strip() or "auto"
    if mode not in VALID_PARALLEL_MODES:
        mode = "auto"
    if mode != "auto":
        return mode

    value = inputs.get(input_key)
    if not value:
        return "none"
    p = Path(str(value))
    meta = field_meta(module, input_key)
    if p.is_file():
        return "single_file"
    if meta.get("type") == "file_path":
        return "single_file"
    return "folder_chunks"


def prepare_parallel_jobs(module: dict, inputs: dict, parallel_workers: int) -> list[dict]:
    workers = clamp_parallel_workers(parallel_workers)
    if workers <= 1:
        return []

    input_key = choose_parallel_input_key(module, inputs)
    mode = infer_parallel_mode(module, inputs, input_key) if input_key else "none"

    if mode == "none":
        return []

    if mode == "module_internal":
        # 该模式不拆任务，只在 api_run_module 中把 parallel_workers 传给模块。
        return []

    if not input_key:
        raise HTTPException(status_code=400, detail="未找到并行输入字段，请在模块配置中填写 parallel_input_key")

    input_value = inputs.get(input_key)
    if input_value in ("", None):
        raise HTTPException(status_code=400, detail=f"并行输入字段为空: {input_key}")

    patterns = parse_parallel_patterns(normalize_parallel_config(module).get("file_patterns"))
    files = discover_batch_files(str(input_value), patterns)
    if not files:
        raise HTTPException(status_code=400, detail=f"未匹配到可并行处理的文件，匹配规则: {';'.join(patterns)}")

    # PARASOL AOD：输入目录里 JD/JL 成对出现，但平台只应该给 JD 主输入创建任务。
    # JL 文件由 AOD_AHI.exe / 算法自己按文件名匹配，不能单独生成任务。
    files_before_parasol_filter = len(files)
    if is_parasol_jd_pair_module(module):
        files = filter_parasol_jd_files_for_jobs(module, files)
        if not files:
            raise HTTPException(
                status_code=400,
                detail="PARASOL 输入目录中没有识别到 JD 主输入文件，不能生成任务。请检查文件名是否包含 JD_ 或 JD-。"
            )

    jobs: list[dict] = []
    if mode == "single_file":
        for idx, file_path in enumerate(files, start=1):
            job_inputs = apply_single_file_output_mapping(module, inputs, file_path)
            job_inputs[input_key] = str(file_path)

            # 平台已经负责并发，单个子任务内部默认只处理当前文件。
            # 线程数由 build_runtime_for_module 根据 _parallel_pool_size 动态分配。
            job_inputs["parallel_workers"] = 1
            job_inputs["parallel_workers"] = 1
            job_inputs["_parallel_workers"] = 1
            job_inputs["_parallel_index"] = idx
            job_inputs["_parallel_total"] = len(files)
            job_inputs["_parallel_pool_size"] = workers
            if is_parasol_jd_pair_module(module) and files_before_parasol_filter != len(files):
                job_inputs["_parasol_jd_filter"] = (
                    f"目录中共 {files_before_parasol_filter} 个文件，仅 JD 主输入生成 {len(files)} 个任务；"
                    "JL 文件由模块自动匹配"
                )
            command, working_dir, runtime_env = build_runtime_for_module(module, job_inputs)
            jobs.append({
                "module_id": module.get("id", ""),
                "module_name": module.get("name", module.get("id", "")),
                "label": file_path.name,
                "command": command,
                "working_dir": working_dir,
                "env": runtime_env,
                "inputs": job_inputs,
            })
        return jobs

    if mode == "folder_chunks":
        if Path(str(input_value)).is_file():
            # 传入单个文件时退化为 single_file。
            return prepare_parallel_jobs({**module, "parallel": {**normalize_parallel_config(module), "mode": "single_file"}}, inputs, workers)

        # 稳定进程池语义：并行进程数 = 同时运行的子任务数，而不是把文件预先切成 workers 份。
        # 例如目录里有 27 个文件、用户选择 4 个进程，就创建 27 个单文件 job；
        # TaskManager 始终保持最多 4 个子进程同时运行，一个完成后立即补上下一个。
        # 如果少数模块确实希望一个 job 处理多个文件，可在 module.json 的 parallel.files_per_job 中显式配置。
        parallel_cfg = normalize_parallel_config(module)
        try:
            files_per_job = int(parallel_cfg.get("files_per_job") or 1)
        except Exception:
            files_per_job = 1
        files_per_job = max(1, files_per_job)

        job_units: list[list[Path]] = []
        if files_per_job <= 1:
            job_units = [[f] for f in files]
        else:
            for i in range(0, len(files), files_per_job):
                job_units.append(files[i:i + files_per_job])

        if dask_cluster_manager.distributed_execution_enabled():
            shared_runtime_root = dask_cluster_manager.get_shared_runtime_root()
            if not shared_runtime_root:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "分布式 folder_chunks 模式需要所有节点可访问的共享运行目录。"
                        "请先在“分布式”页面填写 UNC 路径并检测，例如 "
                        r"\\192.168.2.100\local_web_runtime"
                    ),
                )
            chunk_root = (
                Path(shared_runtime_root)
                / "parallel_chunks"
                / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            )
        else:
            chunk_root = RUNTIME_DIR / "parallel_chunks" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        chunk_root.mkdir(parents=True, exist_ok=True)

        for idx, chunk in enumerate(job_units, start=1):
            chunk_dir = chunk_root / f"job_{idx:04d}"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            used_names: set[str] = set()
            link_modes: list[str] = []
            for src in chunk:
                dst = chunk_dir / unique_chunk_filename(src, used_names)
                link_modes.append(link_or_copy_file(src, dst))

            job_inputs = dict(inputs)
            job_inputs["_parallel_chunk_link_modes"] = sorted(set(link_modes))
            job_inputs[input_key] = str(chunk_dir.resolve())
            if is_parasol_jd_pair_module(module) and files_before_parasol_filter != len(files):
                job_inputs["_parasol_jd_filter"] = (
                    f"目录中共 {files_before_parasol_filter} 个文件，仅 JD 主输入生成 {len(files)} 个任务；"
                    "JL 文件由模块自动匹配"
                )
            job_inputs["_parallel_workers"] = 1
            job_inputs["_parallel_index"] = idx
            job_inputs["_parallel_total"] = len(job_units)
            job_inputs["_parallel_chunk_file_count"] = len(chunk)
            job_inputs["_parallel_pool_size"] = workers
            command, working_dir, runtime_env = build_runtime_for_module(module, job_inputs)
            if len(chunk) == 1:
                label = f"{idx}/{len(job_units)} {chunk[0].name}"
            else:
                label = f"job_{idx:04d} ({len(chunk)} files)"
            jobs.append({
                "module_id": module.get("id", ""),
                "module_name": module.get("name", module.get("id", "")),
                "label": label,
                "command": command,
                "working_dir": working_dir,
                "env": runtime_env,
                "inputs": job_inputs,
                "cleanup_root": str(chunk_root.resolve()),
                "link_modes": sorted(set(link_modes)),
            })
        return jobs

    raise HTTPException(status_code=400, detail=f"不支持的并行模式: {mode}")


# =========================
# 批处理进程池辅助函数
# =========================
BATCH_FILE_EXTS = {".tif", ".tiff", ".img", ".hdf", ".h5", ".nc", ".nc4", ".dat", ".json"}
SHARED_BATCH_MATCH_MODES = {
    "first",
    "shared",
    "fixed",
    "constant",
    "single",
    "reuse_first",
    "all_jobs",
}


def _infer_batch_role_from_field(field: dict) -> str:
    """显式 batch_role 优先；没有时从常见字段名自动推断 B01/B03/B06/SOLAR。

    这样旧模块或手工编辑时 batch_role 丢失，也不会把 B01 文件夹直接传给 exe。
    """
    explicit = str(field.get("batch_role") or "").strip()
    if explicit and explicit.upper() != "OUTPUT_DIR":
        return explicit

    if bool(field.get("control_only", False)):
        return ""

    if is_output_field(field):
        return ""

    field_type = str(field.get("type") or "").lower()
    if field_type not in {"dir_path", "file_path"}:
        return ""

    text = f"{field.get('key', '')} {field.get('label', '')}".upper()
    role_patterns = [
        ("B01", r"(^|[^A-Z0-9])B0?1([^A-Z0-9]|$)|B01_FILE|B01文件|B01 文件"),
        ("B03", r"(^|[^A-Z0-9])B0?3([^A-Z0-9]|$)|B03_FILE|B03文件|B03 文件"),
        ("B06", r"(^|[^A-Z0-9])B0?6([^A-Z0-9]|$)|B06_FILE|B06文件|B06 文件"),
        ("SOLAR", r"SOLAR|SUN|太阳角"),
    ]
    for role, pattern in role_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return role
    return ""


def _field_batch_role(field: dict) -> str:
    return _infer_batch_role_from_field(field)


def _is_shared_batch_field(field: dict) -> bool:
    mode = str(field.get("match_mode") or "").strip().lower()
    if mode in SHARED_BATCH_MATCH_MODES:
        return True
    if field.get("shared_across_jobs") is True:
        return True
    return False


def _parse_batch_patterns(field: dict) -> list[str]:
    """解析批处理文件匹配规则。

    说明：
    - 旧版默认只扫 tif/nc/hdf 等带扩展名文件；
    - PARASOL 原始输入常见为无扩展名文件，Windows 里显示“类型=文件”；
    - 所以 batch_role 批处理字段默认改为 "*"，并允许无扩展名文件进入。
    """
    raw = (
        field.get("file_patterns")
        or field.get("patterns")
        or field.get("pattern")
        or "*"
    )
    if isinstance(raw, list):
        patterns = [str(x).strip() for x in raw if str(x).strip()]
    else:
        patterns = [x.strip() for x in str(raw).replace(",", ";").split(";") if x.strip()]
    return patterns or ["*"]


def _pattern_means_all_files(patterns: list[str]) -> bool:
    normalized = {str(p or "").strip().replace("\\", "/") for p in patterns}
    return bool(normalized & {"*", "*.*", "**/*", "**/*.*"})


def _split_suffix_list(value: Any) -> set[str]:
    if value in (None, ""):
        return set()
    if isinstance(value, list):
        raw_items = value
    else:
        text = str(value).replace("；", ";").replace("，", ",")
        for sep in [",", "|", "\n", "\t", " "]:
            text = text.replace(sep, ";")
        raw_items = text.split(";")
    result: set[str] = set()
    for item in raw_items:
        suffix = str(item or "").strip().lower()
        if not suffix:
            continue
        if suffix == "*":
            result.add("*")
        elif suffix.startswith("."):
            result.add(suffix)
        else:
            result.add("." + suffix)
    return result


def _is_ignored_batch_file(path: Path, field: dict) -> bool:
    name = path.name
    if name.startswith("."):
        return True

    suffix = path.suffix.lower()

    default_ignored = {".tmp", ".bak", ".log", ".txt", ".json", ".xml"}
    extra_ignored = _split_suffix_list(field.get("batch_exclude_suffixes") or field.get("exclude_suffixes"))
    ignored = default_ignored | extra_ignored
    if suffix and suffix in ignored:
        return True

    exclude_regex = str(field.get("batch_exclude_regex") or "").strip()
    if exclude_regex:
        try:
            if re.search(exclude_regex, name, re.IGNORECASE):
                return True
        except re.error as exc:
            raise HTTPException(status_code=400, detail=f"batch_exclude_regex 正则错误: {exc}")

    return False


def _batch_file_allowed(path: Path, field: dict, patterns: list[str]) -> bool:
    """判断某个文件是否允许作为批处理输入。

    兼容规则：
    - field.batch_allow_all_files=true：除临时/日志/配置文件外全部允许；
    - patterns 包含 "*" 或 "*.*"：按全文件模式处理，允许无扩展名和未知扩展名；
    - 无扩展名文件默认允许，适配 PARASOL 原始文件；
    - 有扩展名时默认仍按 BATCH_FILE_EXTS 白名单控制。
    """
    if _is_ignored_batch_file(path, field):
        return False

    include_regex = str(field.get("batch_include_regex") or "").strip()
    if include_regex:
        try:
            if not re.search(include_regex, path.name, re.IGNORECASE):
                return False
        except re.error as exc:
            raise HTTPException(status_code=400, detail=f"batch_include_regex 正则错误: {exc}")

    suffix = path.suffix.lower()

    allowed_suffixes = _split_suffix_list(field.get("batch_suffixes") or field.get("allowed_suffixes"))
    if "*" in allowed_suffixes:
        return True
    if allowed_suffixes:
        if not suffix:
            return bool(field.get("batch_allow_no_extension", True))
        return suffix in allowed_suffixes

    if bool(field.get("batch_allow_all_files", False)):
        return True

    if _pattern_means_all_files(patterns):
        return True

    if not suffix:
        return bool(field.get("batch_allow_no_extension", True))

    return suffix in BATCH_FILE_EXTS


def _list_batch_files(value: str, field: dict) -> list[Path]:
    p = Path(str(value)).expanduser()
    if p.is_file():
        if _batch_file_allowed(p, field, ["*"]):
            return [p.resolve()]
        return []

    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail=f"批量输入路径不存在或不是文件夹: {field.get('key')} -> {value}")

    patterns = _parse_batch_patterns(field)
    found: list[Path] = []
    seen: set[Path] = set()

    for pattern in patterns:
        for item in p.glob(pattern):
            if item.is_file() and _batch_file_allowed(item, field, patterns):
                rp = item.resolve()
                if rp not in seen:
                    seen.add(rp)
                    found.append(rp)

    # 兜底：如果用户写了 "*.*" 但文件没有扩展名，glob("*.*") 会扫不到。
    # 这里再扫一层目录，并套用同样的过滤规则。
    if not found:
        fallback_patterns = ["*"]
        for item in p.iterdir():
            if item.is_file() and _batch_file_allowed(item, field, fallback_patterns):
                rp = item.resolve()
                if rp not in seen:
                    seen.add(rp)
                    found.append(rp)

    found.sort(key=lambda x: x.name.lower())
    return found


def _extract_datetime_keys(path: Path) -> set[str]:
    """从文件名中提取时次 key。

    兼容：
    - 20260301_0400
    - 20260301_040000
    - 202603010400
    - 20260301040000

    统一生成 YYYYMMDD_HHMM，忽略秒。
    """
    text = path.stem
    keys: set[str] = set()

    for m in re.finditer(r"(?<!\d)(20\d{6})[_-]?(\d{4})(\d{2})?(?!\d)", text):
        date = m.group(1)
        hm = m.group(2)
        keys.add(f"{date}_{hm}")
        keys.add(f"{date}{hm}")

    # 兜底：原始 stem 也放进去，适配不含标准时间的文件名一一对应。
    if not keys:
        keys.add(text.lower())

    return keys


def _build_role_index(files: list[Path]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for f in files:
        for key in _extract_datetime_keys(f):
            index.setdefault(key, []).append(f)
    return index


def _get_batch_input_fields(module: dict) -> list[dict]:
    fields: list[dict] = []
    for field in module.get("inputs", []) or []:
        if bool(field.get("control_only", False)):
            continue
        role = _field_batch_role(field)
        if role and role.upper() != "OUTPUT_DIR":
            copied = dict(field)
            copied["batch_role"] = role
            fields.append(copied)
    return fields


def _is_batch_request(module: dict, effective_inputs: Dict[str, Any]) -> bool:
    for field in _get_batch_input_fields(module):
        key = field.get("key")
        if not key:
            continue
        value = effective_inputs.get(key)
        if value in ("", None):
            continue
        p = Path(str(value))
        if p.exists() and p.is_dir():
            return True
    return False


def _get_output_dir_field_for_batch(module: dict) -> Optional[dict]:
    for field in module.get("inputs", []) or []:
        if bool(field.get("control_only", False)):
            continue
        if is_output_field(field):
            return field
    return None


def _make_batch_output_value(module: dict, base_inputs: dict, slot: str, primary_file: Path) -> tuple[dict, Optional[Path]]:
    job_inputs = dict(base_inputs)
    output_field = _get_output_dir_field_for_batch(module)
    if not output_field:
        return job_inputs, None

    key = output_field.get("key")
    if not key:
        return job_inputs, None

    raw_value = str(job_inputs.get(key) or "").strip()
    if not raw_value:
        return job_inputs, None

    output_ext = str(output_field.get("output_ext") or output_field.get("suffix") or ".tif")
    if output_ext and not output_ext.startswith("."):
        output_ext = "." + output_ext
    if not output_ext:
        output_ext = ".tif"

    p = Path(raw_value)
    field_type = str(output_field.get("type") or "").lower()

    # 批处理时，输出目录类型默认生成每个 job 一个文件。
    if field_type == "dir_path" or (not p.suffix):
        p.mkdir(parents=True, exist_ok=True)
        output_naming = str(
            (module.get("parallel") or {}).get("output_naming")
            or output_field.get("output_naming")
            or "source_stem"
        ).strip().lower()

        if output_naming in {"source_stem", "input_stem", "primary_stem", "file_stem"}:
            base_name = primary_file.stem
        else:
            base_name = str(slot or primary_file.stem)

        safe_slot = str(base_name).replace(":", "_").replace("/", "_").replace("\\", "_")
        out_path = p / f"{safe_slot}{output_ext}"
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        out_path = p.with_name(f"{p.stem}_{primary_file.stem}{p.suffix}")

    job_inputs[key] = str(out_path.resolve())
    return job_inputs, out_path.resolve()


def _format_batch_validation_error(message: str, missing: list[dict] | None = None, extras: list[dict] | None = None) -> str:
    parts = [message]
    if missing:
        parts.append("缺失匹配：")
        for idx, item in enumerate(missing[:30], start=1):
            parts.append(
                f"{idx}. slot={item.get('slot', '-')} role={item.get('role', '-')} expected_from={item.get('expected_from', '-')}"
            )
        if len(missing) > 30:
            parts.append(f"... 还有 {len(missing) - 30} 项")
    if extras:
        parts.append("未使用文件：")
        for idx, item in enumerate(extras[:20], start=1):
            files = item.get("files") or []
            preview = ", ".join(str(x) for x in files[:5])
            if len(files) > 5:
                preview += f", ... 还有 {len(files) - 5} 个"
            parts.append(f"{idx}. role={item.get('role', '-')} files={preview}")
    return "\n".join(parts)


def build_batch_jobs_for_module(module: dict, inputs: dict, parallel_workers: int) -> tuple[list[dict], list[Path]]:
    batch_fields = _get_batch_input_fields(module)
    if not batch_fields:
        return [], []

    role_files: dict[str, list[Path]] = {}
    role_indexes: dict[str, dict[str, list[Path]]] = {}
    role_fields: dict[str, dict] = {}

    for field in batch_fields:
        key = field.get("key")
        role = _field_batch_role(field) or str(key)
        value = inputs.get(key)
        if value in ("", None):
            continue
        files = _list_batch_files(str(value), field)
        if not files:
            raise HTTPException(status_code=400, detail=f"批量目录为空或没有匹配文件: {key} -> {value}")

        role_files[role] = files
        role_indexes[role] = _build_role_index(files)
        role_fields[role] = field

    if not role_files:
        return [], []

    primary_roles = [
        role for role, field in role_fields.items()
        if not _is_shared_batch_field(field)
    ]
    if not primary_roles:
        primary_roles = list(role_files.keys())

    primary_role = max(primary_roles, key=lambda r: len(role_files.get(r, [])))
    primary_field = role_fields[primary_role]
    primary_key = primary_field.get("key")

    jobs: list[dict] = []
    output_paths: list[Path] = []
    missing: list[dict] = []
    used_by_role: dict[str, set[str]] = {role: set() for role in role_files}

    primary_files = role_files[primary_role]
    primary_files_before_parasol_filter = len(primary_files)

    # PARASOL AOD：JD/JL 是成对输入。平台只用 JD 文件生成子任务；
    # JL 文件由模块内部根据 JD 文件名自动匹配，不能把 JL 当成独立任务启动。
    if is_parasol_jd_pair_module(module):
        primary_files = filter_parasol_jd_files_for_jobs(module, primary_files)
        if not primary_files:
            raise HTTPException(
                status_code=400,
                detail="PARASOL 输入目录中没有识别到 JD 主输入文件，无法生成任务。请检查文件名是否包含 JD_ 或 JD-。"
            )
        role_files[primary_role] = primary_files
        role_indexes[primary_role] = _build_role_index(primary_files)

    total = len(primary_files)

    for idx, primary_path in enumerate(primary_files, start=1):
        keys = _extract_datetime_keys(primary_path)
        slot = sorted(keys)[0] if keys else primary_path.stem
        if "_" not in slot and len(slot) == 12:
            slot = f"{slot[:8]}_{slot[8:12]}"

        job_inputs = dict(inputs)
        job_inputs[primary_key] = str(primary_path.resolve())
        used_by_role[primary_role].add(str(primary_path.resolve()))

        ok = True
        for role, files in role_files.items():
            if role == primary_role:
                continue

            field = role_fields[role]
            key = field.get("key")

            selected: Optional[Path] = None
            # first/shared：取第一个文件给所有 job 共用。
            if _is_shared_batch_field(field):
                selected = files[0]
            # 当前目录只有一个文件时，也允许作为所有 job 共用，方便临时测试 SOLAR。
            elif len(files) == 1:
                selected = files[0]
            else:
                # 正常按时次匹配。
                index = role_indexes[role]
                for k in keys:
                    candidates = index.get(k)
                    if candidates:
                        selected = candidates[0]
                        break

                # 临时兼容：SOLAR 没有对应时次时，允许用排序第一个文件先跑通流程。
                # 正式生产建议把 SOLAR 文件准备成对应时次，或显式配置 match_mode=timeslot。
                if selected is None and str(role).upper() == "SOLAR":
                    selected = files[0]

            if selected is None:
                ok = False
                missing.append({
                    "slot": slot,
                    "role": role,
                    "expected_from": str(primary_path),
                })
                continue

            job_inputs[key] = str(selected.resolve())
            used_by_role.setdefault(role, set()).add(str(selected.resolve()))

        if not ok:
            continue

        job_inputs, out_path = _make_batch_output_value(module, job_inputs, slot, primary_path)
        if out_path is not None:
            output_paths.append(out_path)

        # 平台字段不写进 exe config。
        for field in module.get("inputs", []) or []:
            if field.get("control_only") is True:
                k = field.get("key")
                if k:
                    job_inputs.pop(k, None)

        job_inputs["_batch_index"] = idx
        job_inputs["_batch_total"] = total
        job_inputs["_batch_slot"] = slot

        # batch_group 也是平台进程池并行，需要把并行池大小传给 build_runtime_for_module，
        # 用于动态分配单个 EXE 内部的 MKL/OpenBLAS 线程数。
        job_inputs["_parallel_pool_size"] = max(1, int(parallel_workers or 1))
        job_inputs["_parallel_workers"] = 1
        job_inputs["parallel_workers"] = 1

        if is_parasol_jd_pair_module(module) and primary_files_before_parasol_filter != len(primary_files):
            job_inputs["_parasol_jd_filter"] = (
                f"目录中共 {primary_files_before_parasol_filter} 个文件，仅 JD 主输入生成 {len(primary_files)} 个任务；"
                "JL 文件由模块自动匹配"
            )

        # 不把平台内部字段写给 exe，除非模块显式要求。
        exe_inputs = {
            k: v for k, v in job_inputs.items()
            if not (str(k).startswith("_batch_") or str(k).startswith("_parasol_"))
        }

        command, working_dir, runtime_env = build_runtime_for_module(module, exe_inputs)
        jobs.append({
            "module_id": module.get("id", ""),
            "module_name": module.get("name", module.get("id", "")),
            "label": f"{idx}/{total} {slot}",
            "command": command,
            "working_dir": working_dir,
            "env": runtime_env,
            "inputs": exe_inputs,
        })

    if missing:
        extras: list[dict] = []
        for role, files in role_files.items():
            unused = [str(f) for f in files if str(f.resolve()) not in used_by_role.get(role, set())]
            if unused:
                extras.append({"role": role, "files": unused})
        raise HTTPException(
            status_code=400,
            detail=_format_batch_validation_error(
                "批量输入不匹配，请检查各输入目录文件名时次是否对应。"
                " 如果只是临时测试 SOLAR，可以把 SOLAR_file 的 match_mode 改成 first；"
                "本版也会在 SOLAR 找不到时次时自动取第一个文件兜底。",
                missing,
                extras,
            ),
        )

    if not jobs:
        raise HTTPException(status_code=400, detail="没有生成任何批处理 job，请检查输入目录和 batch_role 配置")

    return jobs, output_paths
def task_belongs_to_user(task: dict, user) -> bool:
    if not task:
        return False

    owner = str(task.get("owner_username") or "")
    username = get_username_from_user(user)

    return bool(owner) and owner == username


def require_own_task(task_id: str, user) -> dict:
    task = task_manager.get_task(task_id)
    if not task or not task_belongs_to_user(task, user):
        raise HTTPException(status_code=404, detail="任务不存在")
    return task

# =========================
# 任务接口
# =========================
@app.get("/api/tasks")
def api_list_tasks(authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    username = get_username_from_user(user)
    task_manager.kick_scheduler()
    return task_manager.list_tasks(owner_username=username)


@app.get("/api/tasks/{task_id}")
def api_get_task(task_id: str, authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    task_manager.kick_scheduler()
    return require_own_task(task_id, user)


@app.post("/api/tasks/{task_id}/cancel")
def api_cancel_task(task_id: str, authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    require_own_task(task_id, user)

    ok = task_manager.cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在或已结束")
    return {"ok": True}


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: str, authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    require_own_task(task_id, user)

    ok = task_manager.delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True}

def field_io_role(field: dict) -> str:
    """读取模块 JSON 中用于区分输入/输出的显式字段。

    推荐在 inputs 的每一项里加：
      "io_role": "input"   # 输入文件/输入目录/管理员固定资源
      "io_role": "output"  # 输出文件/输出目录

    兼容别名：
      data_role / file_role / direction / role
    没有显式配置时返回 auto，再走旧的 key/label 关键词判断，兼容老模块。
    """
    for name in ("io_role", "data_role", "file_role", "direction", "role"):
        value = field.get(name)
        if value in (None, ""):
            continue
        text = str(value).strip().lower()
        if text in {"output", "out", "result", "result_file", "result_dir", "save", "输出", "结果"}:
            return "output"
        if text in {"input", "in", "source", "source_file", "source_dir", "resource", "输入", "源文件", "资源"}:
            return "input"
        if text in {"auto", "none", "unknown"}:
            return "auto"
    return "auto"


def is_output_field(field: dict) -> bool:
    # 显式 io_role 优先。标成 input 的字段，即使 key/label 里有特殊词，也不会被登记到数据管理。
    role = field_io_role(field)
    if role == "output":
        return True
    if role == "input":
        return False

    key = str(field.get("key", "")).lower()
    label = str(field.get("label", "")).lower()

    output_keywords = [
        "output",
        "outpath",
        "out_dir",
        "output_dir",
        "result",
        "save",
        "输出",
        "结果",
    ]

    return any(k in key or k in label for k in output_keywords)
def ensure_data_files_file():
    if not DATA_FILES_FILE.exists():
        DATA_FILES_FILE.write_text("[]", encoding="utf-8")


def load_data_files() -> list[dict]:
    ensure_data_files_file()
    try:
        data = json.loads(DATA_FILES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_data_files(items: list[dict]):
    DATA_FILES_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_file_type(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return suffix or "unknown"


def format_file_size(size: int) -> str:
    size = int(size or 0)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.2f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.2f} MB"
    return f"{size / 1024 / 1024 / 1024:.2f} GB"


def collect_output_paths_from_inputs(module: dict, inputs: dict) -> list[Path]:
    """
    从模块输入参数里找出输出路径。
    只记录路径，不移动文件。
    """
    paths: list[Path] = []

    for field in module.get("inputs", []) or []:
        key = field.get("key")
        if not key:
            continue

        if not is_output_field(field):
            continue

        value = str(inputs.get(key) or "").strip()
        if not value:
            continue

        paths.append(Path(value))

    return paths


def scan_output_files(output_paths: list[Path]) -> list[Path]:
    """
    扫描输出路径下的真实文件。
    - 如果输出路径是文件：记录这个文件
    - 如果输出路径是文件夹：递归记录文件夹下的文件
    """
    files: list[Path] = []
    seen = set()

    for raw_path in output_paths:
        p = Path(raw_path)

        if p.is_file():
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                files.append(rp)

        elif p.is_dir():
            for item in p.rglob("*"):
                if item.is_file():
                    rp = item.resolve()
                    if rp not in seen:
                        seen.add(rp)
                        files.append(rp)

    files.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
    return files


def upsert_data_files_from_outputs(
    module: dict,
    task_id: str,
    output_paths: list[Path],
    owner_username: str = "",
):
    """
    把任务输出结果登记到 data_files.json。
    只登记信息，不移动文件。
    """
    existing = load_data_files()
    by_key = {}

    for item in load_data_files():
        if not isinstance(item, dict):
            continue
        path_text = str(item.get("path") or "")
        owner = str(item.get("owner_username") or "")
        key = f"{owner}::{path_text}"
        by_key[key] = item

    files = scan_output_files(output_paths)

    for file_path in files:
        if not file_path.exists() or not file_path.is_file():
            continue

        stat = file_path.stat()
        path_text = str(file_path.resolve())

        record_key = f"{owner_username}::{path_text}"
        old = by_key.get(record_key) or {}

        by_key[record_key] = {
            **old,
            "path": path_text,
            "name": file_path.name,
            "file_name": file_path.name,
            "file_type": get_file_type(file_path),
            "io_role": "output",
            "data_role": "output",
            "source_kind": "module_output",
            "module_id": module.get("id", ""),
            "module_name": module.get("name") or module.get("id", ""),
            "task_id": task_id,
            "owner_username": str(owner_username or ""),
            "size": stat.st_size,
            "size_text": format_file_size(stat.st_size),
            "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(timespec="seconds"),
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        }

    items = list(by_key.values())
    items.sort(key=lambda x: x.get("modified_at", ""), reverse=True)

    for idx, item in enumerate(items):
        item["id"] = idx

    save_data_files(items)
    return items


def start_data_file_scan_after_task(
    task_id: str,
    module: dict,
    output_paths: list[Path],
    owner_username: str = "",
):
    """
    任务结束后扫描输出路径，将结果登记到数据管理。
    """
    import threading
    import time

    terminal_statuses = {"success", "failed", "cancelled"}

    def worker():
        while True:
            task = task_manager.get_task(task_id)
            if not task:
                return

            status = task.get("status")
            if status in terminal_statuses:
                if status == "success":
                    try:
                        task_owner = owner_username or str(task.get("owner_username") or "")

                        upsert_data_files_from_outputs(
                            module=module,
                            task_id=task_id,
                            output_paths=output_paths,
                            owner_username=task_owner,
                        )
                        try:
                            task_manager.append_log(task_id, "[DATA] 输出结果已登记到数据管理")
                        except Exception:
                            pass
                    except Exception as exc:
                        try:
                            task_manager.append_log(task_id, f"[DATA-ERROR] 数据管理登记失败: {repr(exc)}")
                        except Exception:
                            pass
                else:
                    try:
                        task_manager.append_log(task_id, f"[DATA] 任务状态为 {status}，不登记输出文件")
                    except Exception:
                        pass
                return

            time.sleep(2)

    threading.Thread(target=worker, daemon=True).start()

# =========================
# 数据管理接口
# =========================
@app.get("/api/data/files")
def api_list_data_files(authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    username = get_username_from_user(user)
    if not username:
        raise HTTPException(status_code=401, detail="未登录")

    # 管理员查看全部用户输出文件
    if isinstance(user, dict):
        role = str(user.get("role") or "")
    else:
        role = str(getattr(user, "role", "") or "")

    all_items, visible_items = load_visible_data_files_for_user(username)

    if role == "admin":
        result = [dict(item) for item in all_items]
    else:
        result = [dict(item) for item in visible_items]

    for item in result:
        item.pop("_source_index", None)

    return result


@app.post("/api/data/files/{file_id}/reveal")
def api_reveal_data_file(file_id: int, authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    username = get_username_from_user(user)
    if not username:
        raise HTTPException(status_code=401, detail="未登录")

    _, _, item = get_data_file_by_id_with_permission(file_id, user)

    path = Path(str(item.get("path") or ""))
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    folder = path.parent

    try:
        if os.name == "nt":
            os.startfile(str(folder))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"打开文件所在位置失败: {exc}")

    return {"ok": True}

@app.delete("/api/data/files/{file_id}")
def api_delete_data_file(file_id: int, authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    username = get_username_from_user(user)
    if not username:
        raise HTTPException(status_code=401, detail="未登录")

    # 只删除 data_files.json 中的登记记录，不删除本地真实文件
    items, source_index, item = get_data_file_by_id_with_permission(file_id, user)

    items.pop(source_index)

    for idx, row in enumerate(items):
        row["id"] = idx

    save_data_files(items)
    return {"ok": True, "message": "已从数据管理列表移除，本地文件未删除"}


@app.get("/api/data/files/{file_id}/preview")
def api_preview_data_file(file_id: int, authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    username = get_username_from_user(user)
    if not username:
        raise HTTPException(status_code=401, detail="未登录")

    _, _, item = get_data_file_by_id_with_permission(file_id, user)

    path = Path(str(item.get("path") or ""))

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    suffix = path.suffix.lower()

    if suffix in {".tif", ".tiff"}:
        result = render_tif_to_preview_result(path)
        return {
            "type": "image",
            "name": path.name,
            "path": str(path.resolve()),
            "data_url": _png_data_url(result["png"]),
            "meta": result.get("meta", {}),
        }

    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}:
        data = path.read_bytes()
        mime = "image/png"
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".gif":
            mime = "image/gif"
        elif suffix == ".webp":
            mime = "image/webp"
        encoded = base64.b64encode(data).decode("ascii")
        return {
            "type": "image",
            "name": path.name,
            "path": str(path.resolve()),
            "data_url": f"data:{mime};base64,{encoded}",
            "meta": {},
        }

    return {
        "type": "file",
        "name": path.name,
        "path": str(path.resolve()),
        "message": "该文件类型暂不支持在线预览",
    }
# =========================
# 本地文件对话框接口
# =========================
def _safe_tk_root():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    return root


@app.post("/api/local/file")
def api_choose_local_file(authorization: str | None = Header(default=None)):
    get_current_user(authorization)
    try:
        from tkinter import filedialog

        root = _safe_tk_root()
        path = filedialog.askopenfilename()
        root.destroy()
        return {"path": path or ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"选择文件失败: {e}")


@app.post("/api/local/dir")
def api_choose_local_dir(authorization: str | None = Header(default=None)):
    get_current_user(authorization)
    try:
        from tkinter import filedialog

        root = _safe_tk_root()
        path = filedialog.askdirectory()
        root.destroy()
        return {"path": path or ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"选择文件夹失败: {e}")


@app.post("/api/local/save-file")
def api_choose_save_file(authorization: str | None = Header(default=None)):
    get_current_user(authorization)
    try:
        from tkinter import filedialog

        root = _safe_tk_root()
        path = filedialog.asksaveasfilename(defaultextension=".tif")
        root.destroy()
        return {"path": path or ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"选择保存文件失败: {e}")


# =========================
# 前端静态文件
# =========================
if FRONTEND_DIST_DIR.exists():
    assets_dir = FRONTEND_DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/favicon.ico")
    def favicon():
        ico = FRONTEND_DIST_DIR / "favicon.ico"
        if ico.exists():
            return FileResponse(str(ico))
        raise HTTPException(status_code=404, detail="favicon.ico not found")

    @app.get("/")
    def index():
        index_file = FRONTEND_DIST_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="前端未构建")
        return FileResponse(str(index_file))

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        candidate = FRONTEND_DIST_DIR / full_path
        if candidate.exists() and candidate.is_file():
            return FileResponse(str(candidate))

        index_file = FRONTEND_DIST_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        raise HTTPException(status_code=404, detail="前端未构建")

