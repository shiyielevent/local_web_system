from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from .schemas import ModuleDefinition, ModuleInputField
from .store import upsert_module

BASE_DIR = Path(__file__).resolve().parents[1]
MODULES_ROOT = BASE_DIR / "installed_modules"
MODULES_ROOT.mkdir(parents=True, exist_ok=True)

DEFAULT_EMBEDDED_DIRS = [
    "deps",
    "bin",
    "dlls",
    "libs",
    "runtime",
    "redist",
    "dependencies",
    "third_party",
]


class ModuleInstallError(Exception):
    pass


def _python_exe(venv_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def collect_embedded_runtime_files(
    module_home: Path,
    entry_path: Path,
    dependency_dirs: list[str] | None = None,
) -> list[str]:
    dependency_dirs = dependency_dirs or DEFAULT_EMBEDDED_DIRS
    copied: list[str] = []
    target_dir = entry_path.parent

    allowed_suffixes = {".dll", ".exe", ".pyd", ".manifest"}

    for dirname in dependency_dirs:
        dep_dir = module_home / dirname
        if not dep_dir.exists() or not dep_dir.is_dir():
            continue

        for f in dep_dir.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in allowed_suffixes:
                continue

            dst = target_dir / f.name
            if not dst.exists():
                shutil.copy2(f, dst)
                copied.append(f.name)

    return sorted(set(copied))


def collect_native_deps_msys2(
    module_home: Path,
    entry_path: Path,
    msys2_env: str = "ucrt64",
    msys2_root: str = "C:/msys64",
) -> list[str]:
    if msys2_env not in {"ucrt64", "mingw64", "clang64"}:
        raise ModuleInstallError(f"unsupported msys2_env: {msys2_env}")

    bash_path = Path(msys2_root) / "usr" / "bin" / "bash.exe"
    if not bash_path.exists():
        raise ModuleInstallError(f"MSYS2 bash not found: {bash_path}")

    cmd = f'cd "{module_home.as_posix()}" && ldd "{entry_path.name}"'
    try:
        result = subprocess.run(
            [str(bash_path), "-lc", cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            shell=False,
            check=True,
        )
    except Exception as exc:
        raise ModuleInstallError(f"ldd failed: {exc!r}")

    dlls: list[str] = []
    prefix = f"/{msys2_env}/bin/"

    for line in result.stdout.splitlines():
        match = re.search(r"=>\s+([^\s]+)", line)
        if not match:
            continue
        dep_path = match.group(1).strip()
        if dep_path.startswith(prefix) and dep_path.lower().endswith(".dll"):
            dlls.append(dep_path)

    dlls = sorted(set(dlls))

    copied: list[str] = []
    target_dir = entry_path.parent
    for dep in dlls:
        win_path = Path(msys2_root) / dep.lstrip("/").replace("/", "\\")
        if win_path.exists():
            dst = target_dir / win_path.name
            if not dst.exists():
                shutil.copy2(win_path, dst)
                copied.append(win_path.name)

    return copied


def install_module_zip(zip_path: Path) -> ModuleDefinition:
    if not zip_path.exists():
        raise ModuleInstallError(f"zip not found: {zip_path}")

    temp_dir = MODULES_ROOT / f"_tmp_{zip_path.stem}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(temp_dir)

    manifest_path = None
    for p in temp_dir.rglob("module.json"):
        manifest_path = p
        break

    if not manifest_path:
        raise ModuleInstallError("module.json not found in zip")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    module_id = manifest.get("id")
    if not module_id:
        raise ModuleInstallError("module.json missing 'id'")

    runtime = manifest.get("runtime", "native")
    entry = manifest.get("entry")
    if not entry:
        raise ModuleInstallError("module.json missing 'entry'")

    module_home = MODULES_ROOT / module_id
    if module_home.exists():
        shutil.rmtree(module_home)

    shutil.move(str(manifest_path.parent), str(module_home))

    entry_path = (module_home / entry).resolve()
    if not entry_path.exists():
        raise ModuleInstallError(f"entry not found: {entry_path}")

    executable = str(entry_path)
    working_dir = str(module_home)

    if runtime == "python":
        venv_dir = module_home / ".venv"
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)], cwd=module_home)
        py = _python_exe(venv_dir)

        req_name = manifest.get("requirements_file", "requirements.txt")
        req_path = module_home / req_name
        if req_path.exists():
            subprocess.check_call([str(py), "-m", "pip", "install", "--upgrade", "pip"], cwd=module_home)
            subprocess.check_call([str(py), "-m", "pip", "install", "-r", str(req_path)], cwd=module_home)

        executable = str(py)

    elif runtime == "native":
        dependency_mode = manifest.get("dependency_mode", "embedded_folder")

        if dependency_mode == "embedded_folder":
            collect_embedded_runtime_files(
                module_home=module_home,
                entry_path=entry_path,
                dependency_dirs=manifest.get("dependency_dirs", DEFAULT_EMBEDDED_DIRS),
            )
        elif dependency_mode == "msys2_auto":
            collect_native_deps_msys2(
                module_home=module_home,
                entry_path=entry_path,
                msys2_env=manifest.get("msys2_env", "ucrt64"),
                msys2_root=manifest.get("msys2_root", "C:/msys64"),
            )
        elif dependency_mode in {"manual_bundle", "self_contained"}:
            pass
        else:
            raise ModuleInstallError(f"unsupported dependency_mode: {dependency_mode}")

        executable = str(entry_path)

    else:
        raise ModuleInstallError(f"unsupported runtime: {runtime}")

    inputs: list[ModuleInputField] = []
    for item in manifest.get("inputs", []):
        # 保留 visible_to_user/admin_fixed/path_mode 等管理员输入控制字段。
        inputs.append(ModuleInputField.model_validate(item))

    command_template = manifest.get("command_template")
    if not command_template:
        if runtime == "python":
            if manifest.get("config_mode") == "json_file":
                command_template = ["{executable}", entry, "{config_path}"]
            else:
                command_template = ["{executable}", entry]
        else:
            if manifest.get("config_mode") == "json_file":
                command_template = ["{executable}", "{config_path}"]
            else:
                command_template = ["{executable}"]

    module = ModuleDefinition(
        id=module_id,
        name=manifest.get("name", module_id),
        description=manifest.get("description", ""),
        executable=executable,
        working_dir=working_dir,
        config_mode=manifest.get("config_mode", "none"),
        command_template=command_template,
        inputs=inputs,
        tags=manifest.get("tags", []),
        tool_type=manifest.get("tool_type", manifest.get("category", "cloud")),
        parallel=manifest.get("parallel") or {
            "mode": manifest.get("parallel_mode", "auto"),
            "input_key": manifest.get("parallel_input_key", ""),
            "output_key": manifest.get("parallel_output_key", ""),
            "file_patterns": manifest.get("parallel_file_patterns", "*.tif;*.tiff;*.nc;*.hdf;*.h5"),
            "output_suffix": manifest.get("parallel_output_suffix", ".tif"),
        },
        enabled=True,
    )

    upsert_module(module)
    return module