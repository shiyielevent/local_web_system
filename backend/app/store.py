from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .schemas import ModuleDefinition, TaskInfo

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
MODULES_FILE = DATA_DIR / "modules.json"
TASKS_FILE = DATA_DIR / "tasks.json"
RUNTIME_DIR = BASE_DIR / "runtime"

DATA_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, value: Any):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load_modules() -> list[ModuleDefinition]:
    raw = _read_json(MODULES_FILE, [])
    return [ModuleDefinition.model_validate(item) for item in raw]


def save_modules(modules: list[ModuleDefinition]):
    _write_json(MODULES_FILE, [m.model_dump() for m in modules])


def upsert_module(module: ModuleDefinition):
    modules = load_modules()
    found = False
    for idx, item in enumerate(modules):
        if item.id == module.id:
            modules[idx] = module
            found = True
            break
    if not found:
        modules.append(module)
    save_modules(modules)


def delete_module(module_id: str):
    modules = [m for m in load_modules() if m.id != module_id]
    save_modules(modules)


def load_task_snapshots() -> list[TaskInfo]:
    raw = _read_json(TASKS_FILE, [])
    return [TaskInfo.model_validate(item) for item in raw]


def save_task_snapshots(tasks: list[TaskInfo]):
    _write_json(TASKS_FILE, [t.model_dump() for t in tasks])
