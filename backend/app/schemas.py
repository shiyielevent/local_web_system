from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict


ParallelMode = Literal["none", "auto", "single_file", "folder_chunks", "module_internal"]


class ModuleInputField(BaseModel):
    key: str
    label: str
    type: Literal["text", "textarea", "number", "integer", "file_path", "dir_path", "password"] = "text"
    required: bool = True
    placeholder: str = ""
    default: str | int | float | None = None
    help_text: str = ""
    visible_to_user: bool = True
    admin_fixed: bool = False
    path_mode: Literal["absolute", "relative_to_module"] = "absolute"
    batch_role: str = ""
    match_mode: str = "none"
    output_ext: str = ".tif"
    control_only: bool = False
    # 用于明确区分输入/输出，推荐值：auto / input / output。
    # output 字段会在任务成功后登记到数据管理；input 字段不会进入数据管理。
    io_role: str = "auto"
    data_role: str = "auto"

    model_config = ConfigDict(extra="allow")


class ModuleDefinition(BaseModel):
    id: str
    name: str
    description: str = ""
    executable: str
    working_dir: str = "."
    config_mode: Literal["none", "json_file"] = "none"
    command_template: list[str] = Field(default_factory=list)
    inputs: list[ModuleInputField] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    tool_type: str = "cloud"

    # 并行执行配置放进 module.json 的 parallel 字段。
    parallel: dict[str, Any] = Field(default_factory=lambda: {
        "mode": "auto",
        "input_key": "",
        "output_key": "",
        "file_patterns": "*.tif;*.tiff;*.nc;*.hdf;*.h5",
        "output_suffix": ".tif",
    })

    enabled: bool = True

    model_config = ConfigDict(extra="allow")


class ModuleRunRequest(BaseModel):
    module_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    parallel_workers: int = 1


class WorkflowStep(BaseModel):
    module_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunRequest(BaseModel):
    name: str = "workflow"
    mode: Literal["sequential", "parallel"] = "sequential"
    steps: list[WorkflowStep]


class TaskInfo(BaseModel):
    id: str
    module_id: str
    module_name: str
    kind: Literal["module", "workflow", "parallel", "batch_parent"] = "module"
    status: Literal["queued", "running", "success", "failed", "cancelled"] = "queued"
    created_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    command: list[str] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    return_code: int | None = None
    pid: int | None = None
    minimized: bool = False
    children: list[str] = Field(default_factory=list)
    parallel_total: int | None = None
    parallel_done: int | None = None
    parallel_failed: int | None = None
    requested_workers: int | None = None
    cpu_affinity_cores: list[int] = Field(default_factory=list)
    cpu_affinity_label: str = ""
    runtime_threads: int | None = None
    queued_at: str | None = None
    scheduled_at: str | None = None
    queue_position: int | None = None
    queue_reason: str = ""
    max_workers: int | None = None
    owner_username: str = ""
    parent_id: str | None = None
