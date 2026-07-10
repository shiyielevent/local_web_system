import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  login,
  registerUser,
  getForgotPasswordQuestion,
  resetForgotPassword,
  logout,
  getMe,
  getUsers,
  addUser,
  deleteUser,
  updateUserRole,
  updateUserEnabled,
  adminResetPassword,
  getModules,
  getAdminModules,
  getToolbars,
  addToolbar,
  updateToolbar,
  deleteToolbar,
  getTasks,
  getSystemResources,
  runModule,
  saveModule,
  deleteModule as deleteModuleApi,
  uploadModuleFolder,
  validateCppModuleFolder,
  parseModuleParamJson,
  parsePythonModuleConfig,
  validatePythonModuleConfig,
  uploadPythonModuleConfig,
  validatePythonModuleFolder,
  uploadPythonFolderModule,
  listDropZips,
  installLocalDropModules,
  getTask,
  cancelTask,
  deleteTask,
  chooseLocalFile,
  chooseLocalDir,
  chooseSaveFile,
  setAuthToken,
  clearAuthToken,
  getAuthToken,
  listDataFiles,
  previewDataFile,
  revealDataFile,
  deleteDataFile,
  getHTCondorSharedIO,
  getHTCondorStatus,
  setHTCondorExecutionMode,
  runHTCondorSmokeTest,
  createHTCondorParent,
  joinHTCondorParent,
  leaveHTCondorPool,
  saveHTCondorNodeWeights,
  prepareHTCondorSharedIO,
  deleteHTCondorSharedIO,
  testHTCondorSharedIO,
} from './api';

import clusterEarthImage from './assets/earth.jpg';


const defaultParallelConfig = {
  mode: 'auto',
  input_key: '',
  output_key: '',
  file_patterns: '*.tif;*.tiff;*.nc;*.hdf;*.h5',
  output_suffix: '.tif',
};

const emptyModuleForm = {
  id: '',
  name: '',
  description: '',
  executable: '',
  working_dir: '.',
  config_mode: 'none',
  command_template_text: '["{executable}"]',
  inputs_text: '[]',
  tags_text: '',
  tool_type: '',
  parallel_json_text: JSON.stringify(defaultParallelConfig, null, 2),
  extra_json_text: '{}',
  enabled: true,
};


const cppExecutableModuleTemplate = {
  module_id: 'my_executable_module',
  module_name: '我的可执行模块',
  tool_type: 'cloud',
  runtime: 'executable',
  entry_file: 'MyExecutableModule.exe',
  source_dir: '.',
  param_json_path: 'config.json',
  description: '可执行模块示例：输入方式与 Python 源码模块一致。系统读取 config.json 自动生成输入/输出表单，运行时把平台生成的 config.json 传给 exe。',
  runtime_env_path: 'D:/YourRuntime/bin',
  dependency_dirs: ['deps'],
  dependency_search_dirs: [],
  resource_dirs: ['resources'],
  auto_collect_deps: true,
  parallel: {
    mode: 'auto',
    file_patterns: '*.tif;*.tiff;*.nc;*.hdf;*.h5',
    output_suffix: '.tif',
    output_naming: 'source_stem',
  },
  tags: ['executable', 'native', 'remote-sensing'],
  enabled: true,
};

function getCppExecutableModuleTemplateText() {
  return JSON.stringify(cppExecutableModuleTemplate, null, 2);
}


const pythonModuleConfigTemplate = {
  module_id: 'H8_CLOUD_TYPE',
  module_name: '葵花8号云类型反演',
  tool_type: 'cloud',
  entry_file: 'CM_CTH.py',
  source_dir: '.',
  param_json_path: 'config.json',
  description: '葵花8号卫星云类型反演 Python 源码模块',
  python_env_mode: 'create_venv',
  python_executable: 'D:/Python/Python38/python.exe',
};

function getPythonModuleConfigTemplateText() {
  return JSON.stringify(pythonModuleConfigTemplate, null, 2);
}

function downloadTextFile(filename, text) {
  const blob = new Blob([text], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const styles = {
  page: {
    minHeight: '100vh',
    width: '100%',
    maxWidth: '100vw',
    overflowX: 'hidden',
    boxSizing: 'border-box',
    background: 'linear-gradient(180deg, #eef4fa 0%, #e7f0f8 100%)',
    color: '#113459',
  },
  topbar: {
    minHeight: 74,
    height: 'auto',
    background: 'linear-gradient(135deg, #0b315a 0%, #12487f 55%, #1a67b6 100%)',
    color: '#fff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: 12,
    width: '100%',
    minWidth: 0,
    boxSizing: 'border-box',
    padding: '10px 18px',
    boxShadow: '0 8px 22px rgba(7,39,76,0.22)',
  },
  topBtn: {
    border: '1px solid rgba(255,255,255,0.25)',
    background: 'rgba(255,255,255,0.08)',
    color: '#fff',
    borderRadius: 10,
    padding: '10px 14px',
    fontWeight: 800,
    cursor: 'pointer',
  },
  topBtnActive: {
    border: 'none',
    background: 'linear-gradient(135deg, #4aa2ff 0%, #2d7cf6 100%)',
    color: '#fff',
    borderRadius: 10,
    padding: '10px 14px',
    fontWeight: 800,
    cursor: 'pointer',
  },
  blueBtn: {
    border: 'none',
    background: 'linear-gradient(135deg, #2d7cf6 0%, #235ed8 100%)',
    color: '#fff',
    borderRadius: 10,
    padding: '10px 16px',
    fontWeight: 800,
    cursor: 'pointer',
  },
  whiteBtn: {
    border: '1px solid #cdd8ea',
    background: '#fff',
    color: '#17406b',
    borderRadius: 10,
    padding: '10px 16px',
    fontWeight: 800,
    cursor: 'pointer',
  },
  redBtn: {
    border: 'none',
    background: 'linear-gradient(135deg, #df4b4b 0%, #c53232 100%)',
    color: '#fff',
    borderRadius: 10,
    padding: '10px 16px',
    fontWeight: 800,
    cursor: 'pointer',
  },
  card: {
    background: 'rgba(248,251,255,0.98)',
    borderRadius: 18,
    border: '1px solid rgba(208,225,241,0.95)',
    boxShadow: '0 10px 24px rgba(8,34,70,0.08)',
  },
  input: {
    width: '100%',
    minHeight: 44,
    borderRadius: 10,
    border: '1px solid #d2dfec',
    padding: '0 12px',
    fontSize: 14,
    boxSizing: 'border-box',
    background: '#fff',
  },
  textarea: {
    width: '100%',
    minHeight: 90,
    borderRadius: 10,
    border: '1px solid #d2dfec',
    padding: '10px 12px',
    fontSize: 14,
    boxSizing: 'border-box',
    background: '#fff',
  },
};

function normalize(v) {
  return String(v || '').toLowerCase();
}

function containsChineseChar(value) {
  return /[\u4e00-\u9fff]/.test(String(value ?? ''));
}

function isPathLikeKey(key) {
  return /(path|dir|file|folder|executable|working_dir|source_dir|outpath|out_dir|output|input|config|runtime_env|python_executable|python_path)/i.test(String(key || ''));
}

function isPathLikeValue(value) {
  const s = String(value ?? '').trim();
  if (!s) return false;
  return (
    /^[A-Za-z]:[\\/]/.test(s) ||
    s.startsWith('\\\\') ||
    s.includes('\\') ||
    s.includes('/') ||
    s.startsWith('./') ||
    s.startsWith('../') ||
    s.startsWith('.\\') ||
    s.startsWith('..\\')
  );
}

function collectChinesePathItems(value, prefix = '路径') {
  const items = [];

  function walk(v, keyPath) {
    if (v == null) return;

    if (typeof v === 'string') {
      const text = v.trim();
      if (
        text &&
        containsChineseChar(text) &&
        (isPathLikeValue(text) || isPathLikeKey(keyPath))
      ) {
        items.push({
          field: keyPath || '路径',
          path: text,
        });
      }
      return;
    }

    if (Array.isArray(v)) {
      v.forEach((item, index) => walk(item, `${keyPath}[${index}]`));
      return;
    }

    if (typeof v === 'object') {
      Object.entries(v).forEach(([key, item]) => {
        const nextKey = keyPath ? `${keyPath}.${key}` : key;
        walk(item, nextKey);
      });
    }
  }

  walk(value, prefix);
  return items;
}

function showChinesePathWarning(items) {
  if (!items.length) return false;

  const lines = items.slice(0, 8).map((item, index) => {
    return `${index + 1}. ${item.field}：${item.path}`;
  });

  const more = items.length > 8 ? `\n……还有 ${items.length - 8} 条中文路径` : '';

  alert(
    [
      '检测到中文路径，当前系统暂不支持中文路径运行。',
      '',
      '为避免 netCDF4、xarray、GDAL、HDF5 等底层库读取失败，请把数据、模块和输出目录放到纯英文路径下。',
      '',
      '建议示例：',
      'D:\\H8\\input',
      'D:\\H8\\output',
      'D:\\local_web_modules\\H8_CLOUD_TYPE',
      '',
      '检测到的中文路径：',
      ...lines,
      more,
    ].filter(Boolean).join('\n')
  );

  return true;
}

function blockIfChinesePath(value, prefix = '路径') {
  return showChinesePathWarning(collectChinesePathItems(value, prefix));
}


// 默认工具栏由后端首次初始化 toolbars.json 时提供。
// 前端不再强制追加 cloud/aerosol，避免删除后又在页面上复活。
const DEFAULT_TOOLBARS = [];
const ACTIVE_TAB_STORAGE_KEY = 'local_web_active_tab';

function getSavedActiveTab() {
  try {
    return localStorage.getItem(ACTIVE_TAB_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function saveActiveTab(tab) {
  try {
    if (tab) {
      localStorage.setItem(ACTIVE_TAB_STORAGE_KEY, tab);
    }
  } catch {}
}

function clearSavedActiveTab() {
  try {
    localStorage.removeItem(ACTIVE_TAB_STORAGE_KEY);
  } catch {}
}

function getDefaultActiveTabForRole(role) {
  return role === 'admin' ? 'module_mgmt' : 'tool:cloud';
}

function getFirstActiveTabForUser(user) {
  const saved = getSavedActiveTab();
  if (saved) return saved;
  return getDefaultActiveTabForRole(user?.role);
}
function normalizeToolKey(v) {
  return String(v || '')
    .trim()
    .replace(/\.\./g, '_')
    .replace(/[\\/\s]+/g, '_');
}

function guessToolType(module) {
  const explicit = normalizeToolKey(module?.tool_type || module?.category || '');
  if (explicit) return explicit;

  const text = `${normalize(module?.id)} ${normalize(module?.name)} ${normalize(module?.description)} ${normalize((module?.tags || []).join(' '))}`;

  if (['aod', 'aerosol', '气溶胶', 'h8', 'polar', '偏振'].some((x) => text.includes(x))) {
    return 'aerosol';
  }
  if (['cloud', '云', 'cloud_type', 'cth'].some((x) => text.includes(x))) {
    return 'cloud';
  }
  return 'cloud';
}

function getModuleToolType(module) {
  return guessToolType(module);
}

function getModuleParallelConfig(module) {
  const raw = module?.parallel && typeof module.parallel === 'object' ? module.parallel : {};
  return {
    mode: raw.mode || module?.parallel_mode || 'auto',
    input_key: raw.input_key || module?.parallel_input_key || '',
    output_key: raw.output_key || module?.parallel_output_key || '',
    file_patterns: raw.file_patterns || module?.parallel_file_patterns || '*.tif;*.tiff;*.nc;*.hdf;*.h5',
    output_suffix: raw.output_suffix || module?.parallel_output_suffix || '.tif',
  };
}

function isFieldVisibleToUser(field) {
  return field?.visible_to_user !== false && field?.admin_fixed !== true;
}

function isParallelWorkerField(field) {
  const key = normalize(field?.key);
  const label = String(field?.label || '');
  const text = `${key} ${label}`;
  return (
    key === 'parallel_workers' ||
    key === '_parallel_workers' ||
    key === 'workers' ||
    key === 'worker_count' ||
    key === 'process_count' ||
    key === 'processes' ||
    key === 'num_processes' ||
    key === 'n_processes' ||
    key === 'nproc' ||
    (text.includes('进程数') && (text.includes('并行') || text.includes('并发'))) ||
    text.includes('parallel worker') ||
    text.includes('parallel_workers')
  );
}

function clampParallelWorkersValue(value, maxWorkers = 64) {
  const max = Math.max(1, Number.parseInt(String(maxWorkers || 64), 10) || 64);
  const n = Number.parseInt(String(value ?? '1').trim(), 10);
  if (!Number.isFinite(n)) return 1;
  return Math.max(1, Math.min(n, max));
}

function getConservativeSuggestedWorkers(cpuCount) {
  const cpu = Math.max(1, Number.parseInt(String(cpuCount || 1), 10) || 1);
  // 遥感反演通常是内存/磁盘重任务，前端兜底值保守：16/24 核也默认建议 2。
  return Math.max(1, Math.min(2, Math.ceil(cpu / 8)));
}

function getConservativeMaxWorkers(cpuCount, suggestedWorkers) {
  const cpu = Math.max(1, Number.parseInt(String(cpuCount || 1), 10) || 1);
  const suggested = Math.max(1, Number.parseInt(String(suggestedWorkers || getConservativeSuggestedWorkers(cpu)), 10) || 1);
  // 上限也降低：16/24 核默认最高 4；后端仍会按 CPU/内存/磁盘/模型大小自动降到更安全值。
  return Math.max(suggested, Math.min(4, Math.max(1, Math.ceil(cpu / 4))));
}

const defaultSystemResources = {
  cpu_count: 1,
  suggested_workers: 1,
  max_workers: 1,
  running_workers: 0,
  available_workers: 1,
  active_task_count: 0,
  queued_task_count: 0,
  cpu_percent: null,
  running_process_cpu_percent: null,
  memory_percent: null,
  memory_available_gb: null,
  disk_percent: null,
  disk_free_gb: null,
  cpu_busy_threshold: 85,
  active_tasks: [],
};

function normalizeSystemResources(data) {
  const cpuCount = Math.max(1, Number.parseInt(String(data?.cpu_count || 1), 10) || 1);
  const fallbackSuggested = getConservativeSuggestedWorkers(cpuCount);
  const fallbackMax = getConservativeMaxWorkers(cpuCount, fallbackSuggested);
  const maxWorkers = Math.max(1, Number.parseInt(String(data?.max_workers || fallbackMax), 10) || fallbackMax);
  const suggestedWorkers = Math.max(1, Math.min(
    maxWorkers,
    Number.parseInt(String(data?.suggested_workers || fallbackSuggested), 10) || fallbackSuggested
  ));

  return {
    ...defaultSystemResources,
    ...(data || {}),
    cpu_count: cpuCount,
    max_workers: maxWorkers,
    suggested_workers: suggestedWorkers,
    running_workers: Math.max(0, Number.parseInt(String(data?.running_workers || 0), 10) || 0),
    available_workers: Math.max(0, Number.parseInt(String(data?.available_workers ?? Math.max(0, maxWorkers)), 10) || 0),
    active_task_count: Math.max(0, Number.parseInt(String(data?.active_task_count || 0), 10) || 0),
    queued_task_count: Math.max(0, Number.parseInt(String(data?.queued_task_count || 0), 10) || 0),
    memory_percent: data?.memory_percent ?? null,
    memory_available_gb: data?.memory_available_gb ?? null,
    disk_percent: data?.disk_percent ?? null,
    disk_free_gb: data?.disk_free_gb ?? null,
    active_tasks: Array.isArray(data?.active_tasks) ? data.active_tasks : [],
  };
}

function getParallelWorkerOptions(systemResources) {
  const info = normalizeSystemResources(systemResources);
  return Array.from({ length: info.max_workers }, (_, idx) => {
    const value = idx + 1;
    const marks = [];
    if (value === info.suggested_workers) marks.push('建议');
    if (value === info.max_workers) marks.push('上限');
    return {
      value,
      label: marks.length ? `${value}（${marks.join('/')}）` : String(value),
    };
  });
}

function makeEmptyInputField() {
  return {
    key: '',
    label: '',
    type: 'file_path',
    required: true,
    placeholder: '',
    default: '',
    help_text: '',
    visible_to_user: true,
    admin_fixed: false,
    path_mode: 'absolute',
    batch_role: '',
    match_mode: 'none',
    io_role: 'auto',
  };
}

function pickModuleExtraFields(module) {
  const managed = new Set([
    'id', 'name', 'description', 'executable', 'working_dir', 'config_mode',
    'command_template', 'inputs', 'tags', 'tool_type', 'category', 'parallel',
    'parallel_mode', 'parallel_input_key', 'parallel_output_key', 'parallel_file_patterns',
    'parallel_output_suffix', 'enabled',
  ]);
  const extra = {};
  Object.entries(module || {}).forEach(([key, value]) => {
    if (!managed.has(key)) extra[key] = value;
  });
  return extra;
}

function uniqToolbars(toolbars, modules) {
  const map = new Map();
  (toolbars || []).forEach((t) => {
    const key = normalizeToolKey(t.key || t.label);
    if (key) map.set(key, { key, label: t.label || key, system: !!t.system });
  });
  (modules || []).forEach((m) => {
    const key = getModuleToolType(m);
    if (key && !map.has(key)) map.set(key, { key, label: key, system: false });
  });
  return Array.from(map.values()).sort((a, b) => {
    const aw = a.key === 'cloud' ? 0 : a.key === 'aerosol' ? 1 : 2;
    const bw = b.key === 'cloud' ? 0 : b.key === 'aerosol' ? 1 : 2;
    if (aw !== bw) return aw - bw;
    return String(a.label).localeCompare(String(b.label), 'zh-CN');
  });
}

function guessModuleByKeywords(modules, keywords) {
  return (
    modules.find((m) => {
      const text = `${normalize(m.id)} ${normalize(m.name)} ${normalize(
        m.description
      )} ${normalize((m.tags || []).join(' '))}`;
      return keywords.some((k) => text.includes(normalize(k)));
    }) || null
  );
}

function statusBadge(status) {
  let bg = '#e6eef8';
  let color = '#2d5177';

  if (status === 'success') {
    bg = '#daf5df';
    color = '#1f7f36';
  } else if (status === 'failed') {
    bg = '#f9dbdb';
    color = '#bb2c2c';
  } else if (status === 'running') {
    bg = '#ddecff';
    color = '#185cbc';
  } else if (status === 'queued') {
    bg = '#efe8ff';
    color = '#6e47be';
  }

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '7px 13px',
        borderRadius: 999,
        background: bg,
        color,
        fontWeight: 800,
        fontSize: 14,
      }}
    >
      {status}
    </span>
  );
}

const TASK_TERMINAL_STATUSES = new Set([
  'success',
  'failed',
  'cancelled',
  'canceled',
  'error',
  'stopped',
  'timeout',
]);

function normalizeTaskStatus(status) {
  return String(status || '').trim().toLowerCase();
}

function isTerminalTaskStatus(status) {
  return TASK_TERMINAL_STATUSES.has(normalizeTaskStatus(status));
}

function isActiveTaskStatus(status) {
  return ['queued', 'running'].includes(normalizeTaskStatus(status));
}

function getTaskLogText(taskOrLogs) {
  const pieces = [];

  if (Array.isArray(taskOrLogs)) {
    pieces.push(...taskOrLogs.map((x) => String(x || '')));
  } else if (taskOrLogs && typeof taskOrLogs === 'object') {
    const task = taskOrLogs;
    ['message', 'error', 'stderr', 'stdout', 'detail', 'status_message'].forEach((key) => {
      if (task[key]) pieces.push(String(task[key]));
    });
    if (Array.isArray(task.logs)) {
      pieces.push(...task.logs.map((x) => String(x || '')));
    } else if (task.logs) {
      pieces.push(String(task.logs));
    }
    if (Array.isArray(task.children)) {
      task.children.forEach((child) => {
        if (Array.isArray(child?.logs)) pieces.push(...child.logs.map((x) => String(x || '')));
        else if (child?.logs) pieces.push(String(child.logs));
      });
    }
  } else if (taskOrLogs != null) {
    pieces.push(String(taskOrLogs));
  }

  return pieces.join('\n');
}

function isMemoryFailureText(text) {
  const raw = String(text || '');
  const lower = raw.toLowerCase();
  return (
    lower.includes('memoryerror') ||
    lower.includes('arraymemoryerror') ||
    lower.includes('unable to allocate') ||
    lower.includes('failed to allocate') ||
    lower.includes('cannot allocate memory') ||
    lower.includes('bad allocation') ||
    lower.includes('out of memory') ||
    lower.includes('not enough memory') ||
    lower.includes('memory allocation') ||
    raw.includes('内存不足') ||
    raw.includes('内存不够') ||
    raw.includes('内存溢出') ||
    raw.includes('无法分配内存') ||
    raw.includes('申请内存失败')
  );
}

function isMemoryFailureTask(task) {
  if (!task) return false;
  return isMemoryFailureText(getTaskLogText(task));
}

function getMemoryFailureExcerpt(task, maxLines = 10) {
  const lines = getTaskLogText(task)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const important = lines.filter((line) => isMemoryFailureText(line) || /traceback|stderr|exception|failed/i.test(line));
  const selected = important.length ? important.slice(-maxLines) : lines.slice(-maxLines);
  return selected.join('\n');
}

function RunningDots({ active }) {
  const [dots, setDots] = useState('');
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      setDots((prev) => (prev.length >= 3 ? '' : prev + '.'));
    }, 450);
    return () => clearInterval(timer);
  }, [active]);
  return <span>{dots}</span>;
}


function formatElapsedSeconds(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds || 0)));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}小时${m}分${s}秒`;
  if (m > 0) return `${m}分${s}秒`;
  return `${s}秒`;
}

function parseTaskTimestamp(value) {
  if (!value) return null;
  const t = new Date(String(value).replace(' ', 'T')).getTime();
  return Number.isFinite(t) ? t : null;
}

function getTaskCacheKey(task) {
  return String(task?.id || task?.task_id || `${task?.module_id || 'task'}_${task?.created_at || ''}`);
}

function getTaskBackendStartMs(task) {
  return parseTaskTimestamp(task?.started_at || task?.scheduled_at || task?.created_at);
}

function getTaskBackendEndMs(task) {
  return parseTaskTimestamp(
    task?.ended_at ||
      task?.finished_at ||
      task?.completed_at ||
      task?.cancelled_at ||
      task?.canceled_at ||
      task?.stopped_at
  );
}

function normalizeFrontendMs(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function getTaskStartMs(task) {
  return normalizeFrontendMs(task?._frontend_start_ms) || getTaskBackendStartMs(task);
}

function getTaskEndMs(task) {
  return normalizeFrontendMs(task?._frontend_end_ms) || getTaskBackendEndMs(task);
}

function shouldTaskTimerRun(status) {
  return normalizeTaskStatus(status) === 'running';
}

function stampTaskTiming(previousTask, nextTask) {
  if (!nextTask) return nextTask;

  const now = Date.now();
  const previousStatus = normalizeTaskStatus(previousTask?.status);
  const nextStatus = normalizeTaskStatus(nextTask?.status);

  const previousStartMs = normalizeFrontendMs(previousTask?._frontend_start_ms);
  const nextStartMs = normalizeFrontendMs(nextTask?._frontend_start_ms);
  const backendStartMs = getTaskBackendStartMs(nextTask);

  let startMs = previousStartMs || nextStartMs || backendStartMs || null;
  let endMs =
    normalizeFrontendMs(previousTask?._frontend_end_ms) ||
    normalizeFrontendMs(nextTask?._frontend_end_ms) ||
    getTaskBackendEndMs(nextTask) ||
    null;

  // 进入 running 的那一刻记录前端开始时间；各个任务独立保存到自己的 task 对象上。
  if (shouldTaskTimerRun(nextStatus) && !startMs) {
    startMs = backendStartMs || now;
  }

  // 如果后端有 started_at，以后端时间为准，但不会破坏已经独立保存的前端开始时间。
  if (!startMs && backendStartMs) {
    startMs = backendStartMs;
  }

  const statusChanged = previousStatus && nextStatus && previousStatus !== nextStatus;
  const wasRunning = shouldTaskTimerRun(previousStatus);
  const stillRunning = shouldTaskTimerRun(nextStatus);

  // 只要任务曾经处于 running，之后状态发生变化并离开 running，就冻结结束时间。
  // success / failed / cancelled / stopped / timeout / 其他非 running 状态都会停止计时。
  if (startMs && !endMs && statusChanged && wasRunning && !stillRunning) {
    endMs = getTaskBackendEndMs(nextTask) || now;
  }

  // 如果第一次拿到任务时已经是终止状态，也必须补一个结束时间，避免拖动弹窗时继续用 Date.now()。
  if (startMs && !endMs && isTerminalTaskStatus(nextStatus)) {
    endMs = getTaskBackendEndMs(nextTask) || now;
  }

  const timedTask = { ...nextTask };

  if (startMs) {
    timedTask._frontend_start_ms = startMs;
  }

  if (endMs) {
    timedTask._frontend_end_ms = Math.max(endMs, startMs || endMs);
  }

  return timedTask;
}

function getTaskElapsedSeconds(task, nowMs = Date.now()) {
  if (!task) return null;

  const startMs = getTaskStartMs(task);
  if (!startMs) return null;

  const endMs = getTaskEndMs(task);
  const finalMs = endMs || nowMs;

  if (!finalMs || finalMs < startMs) return 0;

  return Math.max(0, (finalMs - startMs) / 1000);
}

function getTaskElapsedText(task, nowMs = Date.now()) {
  const elapsedSeconds = getTaskElapsedSeconds(task, nowMs);
  if (elapsedSeconds == null) return '';
  return formatElapsedSeconds(elapsedSeconds);
}

function countLogMatches(logs, pattern) {
  return logs.reduce((n, line) => n + (pattern.test(String(line || '')) ? 1 : 0), 0);
}

function getLastTqdmProgress(logs) {
  const items = Array.isArray(logs) ? logs : [];
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const raw = String(items[i] || '');
    const parts = raw.split(/\r|\n/).reverse();
    for (const part of parts) {
      const line = String(part || '').trim();
      if (!line) continue;

      const percentMatch = line.match(/(\d{1,3})\s*%\s*\|/);
      const countMatch = line.match(/(\d+)\s*\/\s*(\d+)/);

      if (percentMatch) {
        const percent = Math.max(0, Math.min(100, Number.parseInt(percentMatch[1], 10) || 0));
        let current = null;
        let total = null;
        if (countMatch) {
          current = Number.parseInt(countMatch[1], 10) || null;
          total = Number.parseInt(countMatch[2], 10) || null;
        }
        return { percent, current, total };
      }

      if (countMatch && /it\/s|s\/it|elapsed|remaining|\[.*\]/i.test(line)) {
        const current = Number.parseInt(countMatch[1], 10) || 0;
        const total = Number.parseInt(countMatch[2], 10) || 0;
        if (total > 0) {
          const percent = Math.max(0, Math.min(100, Math.round((current / total) * 100)));
          return { percent, current, total };
        }
      }
    }
  }
  return null;
}

function findLastNumberInLogs(logs, patterns) {
  for (let i = logs.length - 1; i >= 0; i -= 1) {
    const line = String(logs[i] || '');
    for (const pattern of patterns) {
      const m = line.match(pattern);
      if (m && m[1]) {
        const value = Number.parseInt(m[1], 10);
        if (Number.isFinite(value) && value > 0) return value;
      }
    }
  }
  return 0;
}

function getTaskProgressInfo(task, taskLogs, elapsedTextOverride = '') {
  const status = normalizeTaskStatus(task?.status);
  const logs = Array.isArray(taskLogs) ? taskLogs : [];
  const elapsedText = elapsedTextOverride || '';

  if (!task) {
    return {
      percent: 0,
      label: '正在加载任务信息',
      detail: '',
      mode: 'unknown',
      color: '#2d7cf6',
    };
  }

  if (status === 'queued') {
    const queueText = task.queue_position ? `当前排队第 ${task.queue_position} 位` : '等待调度';
    return {
      percent: 0,
      label: queueText,
      detail: task.queue_reason || '系统正在等待可用进程槽',
      mode: 'queued',
      color: '#7c5cd6',
    };
  }

  if (status === 'success') {
    return {
      percent: 100,
      label: '任务已完成',
      detail: elapsedText ? `总耗时：${elapsedText}` : '输出结果已进入登记流程',
      mode: 'done',
      color: '#1f9d55',
    };
  }

  if (status === 'failed' || status === 'error' || status === 'timeout') {
    if (isMemoryFailureText(getTaskLogText(logs))) {
      return {
        percent: 100,
        label: '任务因内存不足失败',
        detail: elapsedText
          ? `总耗时：${elapsedText}；系统检测到 MemoryError / Unable to allocate，请清理内存后重新运行`
          : '系统检测到 MemoryError / Unable to allocate，请清理内存后重新运行',
        mode: 'failed_memory',
        color: '#d64545',
      };
    }

    return {
      percent: 100,
      label: '任务运行失败',
      detail: elapsedText
        ? `总耗时：${elapsedText}；请查看下方运行日志中的 STDERR、ERROR 或 Traceback 信息`
        : '请查看下方运行日志中的 STDERR、ERROR 或 Traceback 信息',
      mode: 'failed',
      color: '#d64545',
    };
  }

  if (status === 'cancelled' || status === 'canceled' || status === 'stopped') {
    return {
      percent: 100,
      label: '任务已取消',
      detail: elapsedText ? `已运行：${elapsedText}` : '',
      mode: 'cancelled',
      color: '#6b7280',
    };
  }

  const parallelTotal = Number.parseInt(String(task?.parallel_total || 0), 10) || 0;
  if (parallelTotal > 0) {
    const completed = Number.parseInt(String(task?.parallel_done || 0), 10) || 0;
    const failed = Number.parseInt(String(task?.parallel_failed || 0), 10) || 0;
    const finished = Math.min(parallelTotal, completed);
    const succeeded = Math.max(0, finished - failed);
    const percent = Math.max(0, Math.min(99, Math.round((finished / parallelTotal) * 100)));
    return {
      percent,
      label: `子任务进度：${finished}/${parallelTotal}`,
      detail: `成功 ${succeeded} 个，失败 ${failed} 个${elapsedText ? `，已运行 ${elapsedText}` : ''}`,
      mode: 'parallel',
      color: '#2d7cf6',
    };
  }

  const tqdmProgress = getLastTqdmProgress(logs);
  if (tqdmProgress) {
    const percent = status === 'running'
      ? Math.max(1, Math.min(99, tqdmProgress.percent))
      : tqdmProgress.percent;

    const label = tqdmProgress.total
      ? `算法进度：${tqdmProgress.current || 0}/${tqdmProgress.total}`
      : `算法进度：${percent}%`;

    return {
      percent,
      label,
      detail: `${elapsedText ? `已运行 ${elapsedText}；` : ''}进度来自程序输出的 tqdm 进度条`,
      mode: 'tqdm',
      color: '#2d7cf6',
    };
  }

  const totalFiles = findLastNumberInLogs(logs, [
    /共找到\s*(\d+)\s*个/i,
    /找到\s*(\d+)\s*个/i,
    /total\s*[:=]\s*(\d+)/i,
    /files?\s*[:=]\s*(\d+)/i,
  ]);

  const savedCount = countLogMatches(
    logs,
    /(文件已保存|保存完成|输出完成|处理完成|write complete|saved|finished)/i
  );
  const processingCount = countLogMatches(
    logs,
    /(正在处理|开始处理|processing|running file)/i
  );

  if (totalFiles > 0) {
    const current = Math.min(totalFiles, Math.max(savedCount, processingCount));
    const percent = Math.max(1, Math.min(99, Math.round((current / totalFiles) * 100)));
    const label = savedCount > 0
      ? `文件进度：已完成 ${Math.min(savedCount, totalFiles)}/${totalFiles}`
      : `文件进度：正在处理 ${current}/${totalFiles}`;

    return {
      percent,
      label,
      detail: `${elapsedText ? `已运行 ${elapsedText}；` : ''}进度根据运行日志自动识别`,
      mode: 'log_files',
      color: '#2d7cf6',
    };
  }

  const started = logs.some((line) => /\[INFO\]\s*进程已启动|pid\s*=/i.test(String(line || '')));
  if (started || status === 'running') {
    return {
      percent: null,
      label: '任务正在运行',
      detail: `${elapsedText ? `已运行 ${elapsedText}；` : ''}当前模块未输出可识别的百分比，系统正在持续记录日志`,
      mode: 'indeterminate',
      color: '#2d7cf6',
    };
  }

  return {
    percent: 5,
    label: '任务准备中',
    detail: elapsedText ? `已等待 ${elapsedText}` : '',
    mode: 'preparing',
    color: '#2d7cf6',
  };
}
function mergeTaskForWindow(previousTask, nextTask) {
  return stampTaskTiming(previousTask, nextTask);
}


function TaskProgressPanel({ task, taskLogs }) {
  const status = normalizeTaskStatus(task?.status);
  const running = shouldTaskTimerRun(status);
  const [nowMs, setNowMs] = useState(Date.now());

  useEffect(() => {
    if (!running) return undefined;

    const timer = setInterval(() => {
      setNowMs(Date.now());
    }, 1000);

    return () => clearInterval(timer);
  }, [running, getTaskCacheKey(task)]);

  const elapsedText = getTaskElapsedText(task, nowMs);
  const progress = getTaskProgressInfo(task, taskLogs, elapsedText);
  const isIndeterminate = progress.percent == null;
  const percent = isIndeterminate ? 45 : Math.max(0, Math.min(100, progress.percent));

  return (
    <div
      style={{
        marginTop: 12,
        padding: 12,
        borderRadius: 12,
        background: '#f7fbff',
        border: '1px solid #d7e6f7',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
        <div style={{ fontWeight: 800, color: '#12385f' }}>运行进度</div>
        <div style={{ fontWeight: 900, color: progress.color }}>
          {isIndeterminate ? '运行中' : `${percent}%`}
        </div>
      </div>

      <div
        style={{
          marginTop: 9,
          height: 12,
          borderRadius: 999,
          overflow: 'hidden',
          background: '#e7eef7',
          border: '1px solid rgba(20,80,140,0.08)',
        }}
      >
        <div
          style={{
            width: `${percent}%`,
            height: '100%',
            borderRadius: 999,
            background: isIndeterminate
              ? 'linear-gradient(90deg, rgba(45,124,246,0.25), rgba(45,124,246,0.95), rgba(45,124,246,0.25))'
              : `linear-gradient(90deg, ${progress.color}, ${progress.color})`,
            transition: 'width 0.45s ease',
          }}
        />
      </div>

      <div style={{ marginTop: 8, fontWeight: 800, color: '#163f68', fontSize: 13 }}>
        {progress.label}
        {progress.mode === 'indeterminate' && <RunningDots active={true} />}
      </div>
      {progress.detail && (
        <div style={{ marginTop: 4, color: '#63758c', fontSize: 12, lineHeight: 1.5 }}>
          {progress.detail}
        </div>
      )}
    </div>
  );
}

function SimpleOverlay({ title, onClose, children, width = 'min(960px, 96vw)' }) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(7,22,44,0.32)',
        zIndex: 7000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 12,
      }}
    >
      <div
        style={{
          width,
          maxHeight: '94vh',
          overflow: 'hidden',
          borderRadius: 14,
          background: 'rgba(245,250,255,0.98)',
          boxShadow: '0 18px 46px rgba(5,25,55,0.28)',
          border: '1px solid rgba(255,255,255,0.35)',
        }}
      >
        <div
          style={{
            background: 'linear-gradient(135deg,#0d4f92 0%,#1565c0 50%,#2c8ae8 100%)',
            color: '#fff',
            padding: '10px 14px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <div style={{ fontWeight: 900 }}>{title}</div>
          <button style={{ ...styles.topBtn, padding: '6px 10px' }} onClick={onClose}>
            关闭
          </button>
        </div>
        <div style={{ padding: 16, maxHeight: 'calc(94vh - 46px)', overflow: 'auto' }}>
          {children}
        </div>
      </div>
    </div>
  );
}

function TaskWindow({ win, onMin, onClose, onFront, onMove, onStop }) {
  const dragRef = useRef(null);
  const task = win.task;
  const running = task && isActiveTaskStatus(task.status);
  const taskLogs = Array.isArray(task?.logs)
    ? task.logs
    : task?.logs
      ? [String(task.logs)]
      : [];

  function onMouseDown(e) {
    if (e.button !== 0) return;
    onFront(win.id);
    dragRef.current = {
      x: e.clientX,
      y: e.clientY,
      left: win.left,
      top: win.top,
    };

    function onMoveDoc(ev) {
      if (!dragRef.current) return;
      const dx = ev.clientX - dragRef.current.x;
      const dy = ev.clientY - dragRef.current.y;
      onMove(win.id, dragRef.current.left + dx, dragRef.current.top + dy);
    }

    function onUpDoc() {
      dragRef.current = null;
      document.removeEventListener('mousemove', onMoveDoc);
      document.removeEventListener('mouseup', onUpDoc);
    }

    document.addEventListener('mousemove', onMoveDoc);
    document.addEventListener('mouseup', onUpDoc);
  }

  return (
    <div
      style={{
        position: 'fixed',
        left: win.left,
        top: win.top,
        width: 420,
        zIndex: win.zIndex,
        borderRadius: 14,
        overflow: 'hidden',
        boxShadow: '0 18px 46px rgba(5,25,55,0.28)',
        background: 'rgba(245,250,255,0.98)',
        border: '1px solid rgba(255,255,255,0.35)',
      }}
    >
      <div
        onMouseDown={onMouseDown}
        style={{
          cursor: 'move',
          background: 'linear-gradient(135deg,#0d4f92 0%,#1565c0 50%,#2c8ae8 100%)',
          color: '#fff',
          padding: '10px 14px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <div style={{ fontWeight: 800 }}>{win.title}</div>
        <div style={{ display: 'flex', gap: 8 }}>
          {running ? (
            <button style={{ ...styles.topBtn, padding: '6px 10px' }} onClick={() => onMin(win.id)}>
              最小化
            </button>
          ) : (
            <button style={{ ...styles.topBtn, padding: '6px 10px' }} onClick={() => onClose(win.id)}>
              关闭
            </button>
          )}
        </div>
      </div>

      <div style={{ padding: 16 }}>
        <div
          style={{
            padding: 12,
            borderRadius: 12,
            background: 'linear-gradient(135deg, rgba(25,118,210,0.10), rgba(54,162,235,0.08))',
            border: '1px solid rgba(39,110,188,0.14)',
          }}
        >
          <div style={{ fontSize: 13, color: '#5f7088' }}>当前状态</div>
          <div style={{ fontSize: 20, fontWeight: 800, marginTop: 8 }}>
            {task?.status || '加载中'}
            {running && <RunningDots active={true} />}
          </div>
        </div>

        {isMemoryFailureTask(task) && (
          <div
            style={{
              marginTop: 12,
              padding: 12,
              borderRadius: 12,
              background: 'rgba(220,38,38,0.07)',
              border: '1px solid rgba(220,38,38,0.22)',
              color: '#991b1b',
              lineHeight: 1.65,
              fontSize: 13,
            }}
          >
            <div style={{ fontWeight: 900, marginBottom: 4 }}>检测到内存不足</div>
            <div>该任务日志中出现 MemoryError / Unable to allocate。请先清理父节点或子节点内存，再重新运行任务。</div>
          </div>
        )}

        <div style={{ marginTop: 12, fontSize: 14, lineHeight: 1.7 }}>
          <div><strong>任务ID：</strong>{task?.id || '-'}</div>
          <div><strong>模块：</strong>{task?.module_name || '-'}</div>
          <div><strong>PID：</strong>{task?.pid || '-'}</div>
          {task?.status === 'queued' && (
            <div><strong>排队：</strong>{task?.queue_position ? `第 ${task.queue_position} 位` : '等待中'}{task?.queue_reason ? `，${task.queue_reason}` : ''}</div>
          )}
          {Array.isArray(task?.temporary_outputs) && task.temporary_outputs.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <strong>临时输出：</strong>{task.temporary_outputs.length} 个，父任务成功后登记到数据管理
              <div style={{ color: '#5f7088', fontSize: 12, marginTop: 4, maxHeight: 56, overflow: 'auto' }}>
                {task.temporary_outputs.slice(0, 5).map((item) => item.name || item.path).join('；')}
                {task.temporary_outputs.length > 5 ? `；...还有 ${task.temporary_outputs.length - 5} 个` : ''}
              </div>
            </div>
          )}
        </div>

        <TaskProgressPanel task={task} taskLogs={taskLogs} />

        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>运行日志</div>
          <div
            style={{
              background: '#0a1730',
              color: '#dfe9ff',
              borderRadius: 12,
              padding: 12,
              minHeight: 84,
              maxHeight: 180,
              overflow: 'auto',
              fontSize: 12,
              whiteSpace: 'pre-wrap',
              fontFamily: 'Consolas, "Microsoft YaHei UI", monospace',
              lineHeight: 1.45,
            }}
          >
            {taskLogs.length ? taskLogs.join('\n') : '暂无日志'}
          </div>
        </div>

        {running && (
          <div style={{ marginTop: 12 }}>
            <button style={styles.redBtn} onClick={() => onStop(win.id)}>
              停止任务
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function MemoryWarningWindow({ win, onClose, onFront, onMove }) {
  const dragRef = useRef(null);

  function onMouseDown(e) {
    if (e.button !== 0) return;
    onFront();
    dragRef.current = {
      x: e.clientX,
      y: e.clientY,
      left: win.left,
      top: win.top,
    };

    function onMoveDoc(ev) {
      if (!dragRef.current) return;
      const dx = ev.clientX - dragRef.current.x;
      const dy = ev.clientY - dragRef.current.y;
      onMove(dragRef.current.left + dx, dragRef.current.top + dy);
    }

    function onUpDoc() {
      dragRef.current = null;
      document.removeEventListener('mousemove', onMoveDoc);
      document.removeEventListener('mouseup', onUpDoc);
    }

    document.addEventListener('mousemove', onMoveDoc);
    document.addEventListener('mouseup', onUpDoc);
  }

  return (
    <div
      style={{
        position: 'fixed',
        left: win.left,
        top: win.top,
        width: 420,
        zIndex: win.zIndex,
        borderRadius: 14,
        overflow: 'hidden',
        boxShadow: '0 18px 46px rgba(5,25,55,0.28)',
        background: 'rgba(245,250,255,0.98)',
        border: '1px solid rgba(255,255,255,0.35)',
      }}
    >
      <div
        onMouseDown={onMouseDown}
        style={{
          cursor: 'move',
          background: 'linear-gradient(135deg,#0d4f92 0%,#1565c0 50%,#2c8ae8 100%)',
          color: '#fff',
          padding: '10px 14px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <div style={{ fontWeight: 900 }}>内存不足提醒</div>
        <button style={{ ...styles.topBtn, padding: '6px 10px' }} onClick={onClose}>
          关闭
        </button>
      </div>

      <div style={{ padding: 16 }}>
        <div
          style={{
            padding: 12,
            borderRadius: 12,
            background: 'rgba(220,38,38,0.07)',
            border: '1px solid rgba(220,38,38,0.22)',
            color: '#991b1b',
            lineHeight: 1.65,
          }}
        >
          <div style={{ fontSize: 18, fontWeight: 900, marginBottom: 6 }}>检测到任务因内存不足失败</div>
          <div>系统在任务日志中识别到 MemoryError / Unable to allocate。请先清理父节点或子节点内存后再重新运行。</div>
        </div>

        <div style={{ marginTop: 12, fontSize: 14, lineHeight: 1.7, color: '#173353' }}>
          <div><strong>任务：</strong>{win.moduleName || '-'}</div>
          <div><strong>任务ID：</strong>{win.taskId || '-'}</div>
        </div>

        <div
          style={{
            marginTop: 12,
            padding: 12,
            borderRadius: 12,
            background: '#f7fbff',
            border: '1px solid #d7e6f7',
            color: '#334155',
            lineHeight: 1.7,
            fontSize: 13,
          }}
        >
          <div style={{ fontWeight: 900, color: '#12385f', marginBottom: 6 }}>建议处理</div>
          <div>1. 关闭子节点上的浏览器、VSCode、Anaconda、残留 python.exe 等大内存程序。</div>
          <div>2. 确认可用物理内存至少 6GB，最好 8GB 以上。</div>
          <div>3. 如仍失败，重启对应节点后再运行；必要时把远程 EXE 线程限制设为 1。</div>
        </div>

        {win.excerpt && (
          <div style={{ marginTop: 12 }}>
            <div style={{ fontWeight: 800, marginBottom: 8, color: '#173353' }}>识别到的日志片段</div>
            <div
              style={{
                background: '#0a1730',
                color: '#dfe9ff',
                borderRadius: 12,
                padding: 12,
                maxHeight: 150,
                overflow: 'auto',
                fontSize: 12,
                whiteSpace: 'pre-wrap',
                fontFamily: 'Consolas, "Microsoft YaHei UI", monospace',
                lineHeight: 1.45,
              }}
            >
              {win.excerpt}
            </div>
          </div>
        )}

        <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
          <button style={styles.blueBtn} onClick={onClose}>
            我知道了
          </button>
        </div>
      </div>
    </div>
  );
}

function TaskTrayFloatingWindow({ count, children, minimized, onToggleMinimize }) {
  const trayWidth = 300;
  const trayHeight = 360;
  const trayMargin = 20;

  const [dragged, setDragged] = useState(false);
  const [pos, setPos] = useState({ left: 0, top: 0 });
  const trayRef = useRef(null);
  const dragRef = useRef(null);

  // 每次从“图标状态”展开时，重新回到右下角
  useEffect(() => {
    if (!minimized) {
      setDragged(false);
    }
  }, [minimized]);

  function onMouseDown(e) {
    if (e.button !== 0) return;

    const rect = trayRef.current?.getBoundingClientRect();
    if (!rect) return;

    setDragged(true);

    dragRef.current = {
      x: e.clientX,
      y: e.clientY,
      left: rect.left,
      top: rect.top,
    };

    function onMoveDoc(ev) {
      if (!dragRef.current) return;

      const dx = ev.clientX - dragRef.current.x;
      const dy = ev.clientY - dragRef.current.y;

      setPos({
        left: Math.max(
          8,
          Math.min(window.innerWidth - trayWidth - 8, dragRef.current.left + dx)
        ),
        top: Math.max(
          8,
          Math.min(window.innerHeight - 80, dragRef.current.top + dy)
        ),
      });
    }

    function onUpDoc() {
      dragRef.current = null;
      document.removeEventListener('mousemove', onMoveDoc);
      document.removeEventListener('mouseup', onUpDoc);
    }

    document.addEventListener('mousemove', onMoveDoc);
    document.addEventListener('mouseup', onUpDoc);
  }

  // 最小化后只显示右下角图标
  if (minimized) {
    return (
      <button
        onClick={onToggleMinimize}
        title="展开任务托盘"
        style={{
          position: 'fixed',
          right: 16,
          bottom: 16,
          width: 54,
          height: 54,
          borderRadius: 16,
          border: '1px solid rgba(255,255,255,0.45)',
          background: 'linear-gradient(135deg,#0d4f92 0%,#1565c0 55%,#2c8ae8 100%)',
          color: '#fff',
          fontSize: 22,
          fontWeight: 900,
          cursor: 'pointer',
          zIndex: 6600,
          boxShadow: '0 16px 36px rgba(5,25,55,0.26)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        ≡
      </button>
    );
  }

  return (
    <div
      ref={trayRef}
      style={{
        position: 'fixed',

        // 没拖动过：强制右下角
        ...(dragged
          ? {
              left: pos.left,
              top: pos.top,
            }
          : {
              right: trayMargin,
              bottom: trayMargin,
            }),

        width: trayWidth,
        maxHeight: 'min(430px, calc(100vh - 90px))',
        zIndex: 6500,
        borderRadius: 16,
        overflow: 'hidden',
        background: 'rgba(255,255,255,0.98)',
        border: '1px solid rgba(255,255,255,0.45)',
        boxShadow: '0 18px 46px rgba(5,25,55,0.26)',
      }}
    >
      <div
        onMouseDown={onMouseDown}
        style={{
          cursor: 'move',
          background: 'linear-gradient(135deg,#0d4f92 0%,#1565c0 55%,#2c8ae8 100%)',
          color: '#fff',
          padding: '10px 12px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          userSelect: 'none',
        }}
      >
        <div style={{ fontWeight: 900, fontSize: 16 }}>任务托盘</div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ fontSize: 12, opacity: 0.92 }}>{count} 个</div>
          <button
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onToggleMinimize();
            }}
            title="最小化任务托盘"
            style={{
              border: '1px solid rgba(255,255,255,0.35)',
              background: 'rgba(255,255,255,0.12)',
              color: '#fff',
              borderRadius: 6,
              padding: '2px 10px',
              cursor: 'pointer',
              fontSize: 16,
              fontWeight: 800,
              lineHeight: 1,
            }}
          >
            –
          </button>
        </div>
      </div>

      <div
        style={{
          padding: 12,
          maxHeight: 'calc(min(430px, calc(100vh - 90px)) - 44px)',
          overflow: 'auto',
        }}
      >
        {children}
      </div>
    </div>
  );
}

function LoginPage(props) {
  const {
    authMode,
    setAuthMode,
    loginType,
    setLoginType,
    loginForm,
    setLoginForm,
    registerForm,
    setRegisterForm,
    forgotForm,
    setForgotForm,
    loginError,
    handleLogin,
    handleRegister,
    handleForgotQuestion,
    handleForgotReset,
  } = props;

  const [showPassword, setShowPassword] = useState(false);
  const [showRegisterPassword, setShowRegisterPassword] = useState(false);
  const [showRegisterConfirmPassword, setShowRegisterConfirmPassword] = useState(false);
  const [showForgotPassword, setShowForgotPassword] = useState(false);

  const outerCardStyle = {
    width: 'min(1050px, 96vw)',
    minHeight: 620,
    display: 'grid',
    gridTemplateColumns: '1.05fr 0.95fr',
    borderRadius: 24,
    overflow: 'hidden',
    boxShadow: '0 28px 90px rgba(0,0,0,0.36)',
    background: 'rgba(255,255,255,0.10)',
    border: '1px solid rgba(255,255,255,0.20)',
    backdropFilter: 'blur(6px)',
  };

  const innerFormCard = {
    width: '100%',
    maxWidth: 380,
    background: '#fff',
    borderRadius: 18,
    padding: '24px 26px 22px',
    boxShadow: '0 10px 30px rgba(25, 56, 120, 0.08)',
    border: '1px solid #eef2f7',
  };

  const fieldWrap = {
    display: 'flex',
    alignItems: 'center',
    minHeight: 44,
    border: '1px solid #cfd8e6',
    borderRadius: 8,
    padding: '0 14px',
    background: '#fff',
  };

  const fieldInput = {
    flex: 1,
    border: 'none',
    outline: 'none',
    fontSize: 15,
    height: 40,
    background: 'transparent',
    color: '#22324a',
  };

  const suffixText = {
    color: '#6e8097',
    fontSize: 14,
    marginLeft: 10,
    whiteSpace: 'nowrap',
  };

  const linkBtn = {
    border: 'none',
    background: 'transparent',
    color: '#4a78e8',
    cursor: 'pointer',
    fontSize: 14,
    padding: 0,
  };

  const roleBtn = {
    border: '1px solid #d8e1ef',
    background: '#fff',
    color: '#173353',
    borderRadius: 10,
    padding: '12px 0',
    fontWeight: 700,
    cursor: 'pointer',
  };

  const roleBtnActive = {
    ...roleBtn,
    border: '1px solid #4a84ff',
    background: '#eef4ff',
    color: '#235ed8',
  };

  const titleMap = {
    login: '账号登录',
    register: '账号注册',
    forgot: '找回密码',
  };

  return (
      <div
          style={{
            minHeight: '100vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundImage:
                'linear-gradient(135deg, rgba(5, 22, 48, 0.78), rgba(4, 38, 72, 0.58)), url("/images/login-bg.png")',
            backgroundSize: 'cover',
            backgroundPosition: 'center',
            backgroundRepeat: 'no-repeat',
            backgroundAttachment: 'fixed',
            padding: 20,
          }}
      >
        <div style={outerCardStyle}>
          {/* 左侧介绍区 */}
            <div
              style={{
                position: 'relative',
                padding: '48px 42px',
                color: '#fff',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
                backgroundImage:
                  'linear-gradient(180deg, rgba(4, 18, 42, 0.35), rgba(4, 18, 42, 0.68)), url("/images/login-left-hero.png")',
                backgroundSize: 'cover',
                backgroundPosition: 'center bottom',
                backgroundRepeat: 'no-repeat',
              }}
            >

            <div>
              <div
                  style={{
                    display: 'inline-flex',
                    padding: '8px 14px',
                    borderRadius: 999,
                    background: 'rgba(255,255,255,0.10)',
                    fontSize: 14,
                    marginBottom: 28,
                  }}
              >
                遥感反演 · 本地运行平台
              </div>

              <h1 style={{fontSize: 42, lineHeight: 1.25, margin: 0, fontWeight: 800}}>
                云和气溶胶反演系统
              </h1>

              <p
                  style={{
                    marginTop: 22,
                    fontSize: 18,
                    lineHeight: 1.9,
                    color: 'rgba(255,255,255,0.86)',
                  }}
              >
                面向遥感业务场景的本地模块化运行平台，支持云检测、
                气溶胶反演、模块接入、任务并行调度与结果追踪。
              </p>
            </div>

            <div
                style={{
                  display: 'flex',
                  gap: 14,
                  flexWrap: 'wrap',
                  color: 'rgba(255,255,255,0.78)',
                  fontSize: 14,
                }}
            >
              <span>H8</span>
              <span>FY</span>
              <span>AOD</span>
              <span>Cloud Mask</span>
              <span>Remote Sensing</span>
            </div>
          </div>

          {/* 右侧登录区域 */}
          <div
              style={{
                background: 'rgba(248,251,255,0.90)',
                backdropFilter: 'blur(10px)',
                padding: '52px 42px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
              }}
          >
            <div style={{width: '100%', maxWidth: 420}}>
              <div style={{marginBottom: 18}}>
                <h2
                    style={{
                      margin: 0,
                      fontSize: 28,
                      fontWeight: 800,
                      color: '#10233f',
                    }}
                >
                  欢迎进入系统
                </h2>
              </div>

              <div style={innerFormCard}>
                {authMode !== 'login' && (
                    <div style={{marginBottom: 10}}>
                      <button style={linkBtn} onClick={() => setAuthMode('login')}>
                        返回登录
                      </button>
                    </div>
                )}

                <div
                    style={{
                      textAlign: 'center',
                      fontSize: 18,
                      fontWeight: 800,
                      color: '#111',
                      marginBottom: 22,
                    }}
                >
                  {titleMap[authMode]}
                </div>

                {/* 登录 */}
                {authMode === 'login' && (
                    <>
                      <div style={fieldWrap}>
                        <input
                            value={loginForm.username}
                            onChange={(e) =>
                                setLoginForm({...loginForm, username: e.target.value})
                            }
                            placeholder="请输入用户名"
                            style={fieldInput}
                        />
                        <span style={suffixText}>账号</span>
                      </div>

                      <div style={{...fieldWrap, marginTop: 14}}>
                        <input
                            type={showPassword ? 'text' : 'password'}
                            value={loginForm.password}
                            onChange={(e) =>
                                setLoginForm({...loginForm, password: e.target.value})
                            }
                            placeholder="输入密码"
                            style={fieldInput}
                        />
                        <button
                            type="button"
                            style={{...linkBtn, color: '#8fa0b4'}}
                            onClick={() => setShowPassword((v) => !v)}
                        >
                          {showPassword ? '隐藏' : '显示'}
                        </button>
                      </div>

                      <div
                          style={{
                            marginTop: 12,
                            display: 'flex',
                            justifyContent: 'flex-end',
                            alignItems: 'center',
                            fontSize: 14,
                            color: '#5f7088',
                          }}
                      >
                        <button
                            type="button"
                            style={linkBtn}
                            onClick={() => setAuthMode('forgot')}
                        >
                          忘记密码
                        </button>
                      </div>

                      <div
                          style={{
                            marginTop: 16,
                            display: 'grid',
                            gridTemplateColumns: '1fr 1fr',
                            gap: 12,
                          }}
                      >
                        <button
                            type="button"
                            style={loginType === 'user' ? roleBtnActive : roleBtn}
                            onClick={() => setLoginType('user')}
                        >
                          用户
                        </button>

                        <button
                            type="button"
                            style={loginType === 'admin' ? roleBtnActive : roleBtn}
                            onClick={() => setLoginType('admin')}
                        >
                          管理员
                        </button>
                      </div>

                      <button
                          style={{...widePrimaryBtn, marginTop: 20}}
                          onClick={handleLogin}
                      >
                        登 录
                      </button>

                      <div style={{textAlign: 'center', marginTop: 14}}>
                        <button
                            type="button"
                            style={linkBtn}
                            onClick={() => setAuthMode('register')}
                        >
                          注册新账号
                        </button>
                      </div>
                    </>
                )}

                {/* 注册 */}
                {authMode === 'register' && (
                    <>
                      <div style={{display: 'grid', gap: 12}}>
                        <div style={fieldWrap}>
                          <input
                              value={registerForm.username}
                              onChange={(e) =>
                                  setRegisterForm({...registerForm, username: e.target.value})
                              }
                              placeholder="请输入用户名"
                              style={fieldInput}
                          />
                        </div>

                        <div style={fieldWrap}>
                          <input
                              type={showRegisterPassword ? 'text' : 'password'}
                              value={registerForm.password}
                              onChange={(e) =>
                                  setRegisterForm({...registerForm, password: e.target.value})
                              }
                              placeholder="请输入密码"
                              style={fieldInput}
                          />
                          <button
                              type="button"
                              style={{...linkBtn, color: '#8fa0b4'}}
                              onClick={() => setShowRegisterPassword((v) => !v)}
                          >
                            {showRegisterPassword ? '隐藏' : '显示'}
                          </button>
                        </div>

                        <div style={fieldWrap}>
                          <input
                              type={showRegisterConfirmPassword ? 'text' : 'password'}
                              value={registerForm.confirm_password}
                              onChange={(e) =>
                                  setRegisterForm({
                                    ...registerForm,
                                    confirm_password: e.target.value,
                                  })
                              }
                              placeholder="请输入确认密码"
                              style={fieldInput}
                          />
                          <button
                              type="button"
                              style={{...linkBtn, color: '#8fa0b4'}}
                              onClick={() => setShowRegisterConfirmPassword((v) => !v)}
                          >
                            {showRegisterConfirmPassword ? '隐藏' : '显示'}
                          </button>
                        </div>

                        <div style={fieldWrap}>
                          <input
                              value={registerForm.security_question}
                              onChange={(e) =>
                                  setRegisterForm({
                                    ...registerForm,
                                    security_question: e.target.value,
                                  })
                              }
                              placeholder="请输入安全问题"
                              style={fieldInput}
                          />
                        </div>

                        <div style={fieldWrap}>
                          <input
                              value={registerForm.security_answer}
                              onChange={(e) =>
                                  setRegisterForm({
                                    ...registerForm,
                                    security_answer: e.target.value,
                                  })
                              }
                              placeholder="请输入安全答案"
                              style={fieldInput}
                          />
                        </div>
                      </div>

                      <button
                          style={{...widePrimaryBtn, marginTop: 20}}
                          onClick={handleRegister}
                      >
                        注 册
                      </button>
                    </>
                )}

                {/* 找回密码 */}
                {authMode === 'forgot' && (
                    <>
                      <div style={{display: 'grid', gap: 12}}>
                        <div style={fieldWrap}>
                          <input
                              value={forgotForm.username}
                              onChange={(e) =>
                                  setForgotForm({...forgotForm, username: e.target.value})
                              }
                              placeholder="请输入用户名"
                              style={fieldInput}
                          />
                        </div>

                        <button
                            style={{...styles.whiteBtn, width: '100%'}}
                            onClick={handleForgotQuestion}
                        >
                          获取安全问题
                        </button>

                        <div style={fieldWrap}>
                          <input
                              value={forgotForm.question}
                              readOnly
                              placeholder="安全问题"
                              style={fieldInput}
                          />
                        </div>

                        <div style={fieldWrap}>
                          <input
                              value={forgotForm.answer}
                              onChange={(e) =>
                                  setForgotForm({...forgotForm, answer: e.target.value})
                              }
                              placeholder="请输入安全答案"
                              style={fieldInput}
                          />
                        </div>

                        <div style={fieldWrap}>
                          <input
                              type={showForgotPassword ? 'text' : 'password'}
                              value={forgotForm.new_password}
                              onChange={(e) =>
                                  setForgotForm({
                                    ...forgotForm,
                                    new_password: e.target.value,
                                  })
                              }
                              placeholder="请输入新密码"
                              style={fieldInput}
                          />
                          <button
                              type="button"
                              style={{...linkBtn, color: '#8fa0b4'}}
                              onClick={() => setShowForgotPassword((v) => !v)}
                          >
                            {showForgotPassword ? '隐藏' : '显示'}
                          </button>
                        </div>
                      </div>

                      <button
                          style={{...widePrimaryBtn, marginTop: 20}}
                          onClick={handleForgotReset}
                      >
                        重置密码
                      </button>
                    </>
                )}

                {loginError && (
                    <div
                        style={{
                          marginTop: 16,
                          padding: '10px 12px',
                          borderRadius: 10,
                          background: 'rgba(220,38,38,0.06)',
                          color: '#d43838',
                          fontSize: 13,
                          lineHeight: 1.6,
                        }}
                    >
                      {loginError}
                    </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
  );
}

const typeCard = {
  flex: 1,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 8,
  padding: '14px 12px',
  borderRadius: 12,
  border: '1px solid #d7dfeb',
  background: '#fff',
  cursor: 'pointer',
  color: '#173353',
  fontWeight: 700,
};

const selectedTypeCard = {
  ...typeCard,
  border: '2px solid #3b82f6',
  background: 'rgba(59,130,246,0.08)',
};

const widePrimaryBtn = {
  width: '100%',
  height: 48,
  fontSize: 16,
  fontWeight: 700,
  border: 'none',
  borderRadius: 12,
  cursor: 'pointer',
  color: '#fff',
  background: 'linear-gradient(135deg, #2d7cf6 0%, #235ed8 100%)',
};

const labelStyle = {
  marginBottom: 8,
  fontWeight: 700,
  color: '#173353',
};
const TASK_TRAY_WIDTH = 300;
const TASK_TRAY_RIGHT = 12;
const TASK_TRAY_BOTTOM = 12;
const TASK_TRAY_RESERVED_RIGHT = TASK_TRAY_WIDTH + TASK_TRAY_RIGHT + 24;
const TASK_TRAY_RESERVED_BOTTOM = 150;



function HTCondorPage({
  status,
  busy,
  message,
  clusterForm,
  setClusterForm,
  onRefresh,
  onSetMode,
  onSmokeTest,
  onCreateParent,
  onJoinParent,
  onLeavePool,
  onSaveWeights,
  onPrepareShare,
  onShowShares,
  onTestShare,
}) {
  const info = status || {};
  const install = info.install_result || {};
  const runtime = info.runtime || {};
  const installedRuntime = runtime.installed_runtime || {};
  const service = installedRuntime.service || {};
  const ping = info.ping || {};
  const slot = info.slot_status || {};
  const queue = info.queue || {};
  const nodes = info.nodes || {};
  const localIps = Array.isArray(info.local_ips) ? info.local_ips : [];
  const mode = info.execution_mode || 'local';
  const nodeItems = Array.isArray(nodes.items) ? nodes.items : [];
  const uniqueMachines = Array.from(new Set(nodeItems.map((item) => item.machine).filter(Boolean)));
  const poolRole = info.pool_role || 'standalone';
  const roleText = poolRole === 'parent' ? '父节点' : (poolRole === 'child' ? '子节点' : '单机 / 未加入集群');
  const clusterStarted = poolRole === 'parent' || poolRole === 'child';
  const clusterHealthy = clusterStarted && !!info.service_running && !!nodes.ok;
  const clusterStatusText = clusterHealthy
    ? '已组建 / 正常'
    : (clusterStarted
      ? '已组建 / 检查异常'
      : '未组建');
  const nodeCount = uniqueMachines.length || nodeItems.length || 0;
  const parentAddress = info.parent_ip || info.bind_ip || '-';
  const sharedIo = info.shared_io || {};
  const sharedShares = Array.isArray(sharedIo.shares) ? sharedIo.shares : (sharedIo.unc_root ? [sharedIo] : []);
  const sharedEnabled = !!sharedIo.enabled || sharedShares.length > 0;
  const sharedUnc = sharedIo.unc_root || '';
  const sharedRole = sharedIo.role || '';
  const autoChildUnc = clusterForm.parent_ip && clusterForm.shared_share_name
    ? `\\${clusterForm.parent_ip}\${clusterForm.shared_share_name}`
    : '';

  const versionOutput = String(installedRuntime.version_output || '');
  const versionLine = versionOutput.split('\n').find((line) => line.includes('CondorVersion')) || '';
  const platformLine = versionOutput.split('\n').find((line) => line.includes('CondorPlatform')) || '';
  const shortVersion = [
    versionLine
      .replace(/^\$?CondorVersion:\s*/i, '')
      .replace(/^SCondorVersion:\s*/i, '')
      .replace(/\s+BuildID:.*$/i, '')
      .replace(/\s+GitSHA:.*$/i, '')
      .trim(),
    platformLine
      .replace(/^\$?CondorPlatform:\s*/i, '')
      .replace(/^SCondorPlatform:\s*/i, '')
      .trim(),
  ].filter(Boolean).join(' ｜ ');

  const parentInfo = poolRole === 'parent'
    ? `机器：${info.machine || '-'}\nIP：${parentAddress}\n端口：${info.collector_port || 9618}`
    : (poolRole === 'child'
      ? `父节点 IP：${parentAddress}\n端口：${info.collector_port || 9618}`
      : '-');

  const childMachines = poolRole === 'parent'
    ? uniqueMachines.filter((machine) => machine !== info.machine)
    : (poolRole === 'child' ? [info.machine].filter(Boolean) : []);

  const childInfo = childMachines.length
    ? childMachines.map((machine) => {
        const node = nodeItems.find((item) => item.machine === machine) || {};
        const cpuText = node.cpus ? `CPU：${node.cpus}` : '';
        const memText = node.memory ? `内存：${node.memory}MB` : '';
        return [machine, node.state, node.activity, cpuText, memText].filter(Boolean).join(' / ');
      }).join('\n')
    : (poolRole === 'parent' ? '暂无子节点接入' : '-');

  const leaveDisabled = !!busy || !clusterStarted;
  const leaveButtonStyle = leaveDisabled
    ? {
        ...styles.whiteBtn,
        background: '#e5e7eb',
        color: '#6b7280',
        border: '1px solid #d1d5db',
        cursor: 'not-allowed',
        opacity: 0.85,
      }
    : styles.redBtn;

  const okBadge = (ok, yesText, noText) => (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 6,
      padding: '6px 10px',
      borderRadius: 999,
      fontSize: 12,
      fontWeight: 800,
      background: ok ? '#dcfce7' : '#fee2e2',
      color: ok ? '#166534' : '#991b1b',
      whiteSpace: 'nowrap',
    }}>
      <span style={{
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: ok ? '#22c55e' : '#ef4444',
      }} />
      {ok ? yesText : noText}
    </span>
  );

  const cardTitle = (text, subText = '') => (
    <div style={{ minHeight: 44, marginBottom: 12 }}>
      <div style={{ fontSize: 20, fontWeight: 900, color: '#12385f', lineHeight: 1.2 }}>{text}</div>
      {subText && <div style={{ marginTop: 5, color: '#64748b', fontSize: 13, lineHeight: 1.45 }}>{subText}</div>}
    </div>
  );

  const statCard = (label, value) => (
    <div style={{
      minHeight: 66,
      padding: '10px 12px',
      borderRadius: 14,
      border: '1px solid #dce8f3',
      background: '#fff',
      boxSizing: 'border-box',
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
    }}>
      <div style={{ fontSize: 12, color: '#6a7f96', fontWeight: 800 }}>{label}</div>
      <div style={{
        marginTop: 5,
        fontWeight: 900,
        color: '#173b61',
        overflowWrap: 'anywhere',
        whiteSpace: 'pre-wrap',
        lineHeight: 1.35,
        fontSize: 14,
      }}>
        {value || '-'}
      </div>
    </div>
  );

  const infoCard = (label, value) => (
    <div style={{
      minHeight: 92,
      padding: '10px 12px',
      borderRadius: 14,
      border: '1px solid #dce8f3',
      background: '#fff',
      boxSizing: 'border-box',
    }}>
      <div style={{ fontSize: 12, color: '#6a7f96', fontWeight: 800 }}>{label}</div>
      <div style={{
        marginTop: 5,
        fontWeight: 900,
        color: '#173b61',
        overflowWrap: 'anywhere',
        whiteSpace: 'pre-wrap',
        lineHeight: 1.45,
        fontSize: 13,
      }}>
        {value || '-'}
      </div>
    </div>
  );

  const logBlock = (title, value, maxHeight = 160) => (
    <div>
      <div style={{ fontWeight: 900, color: '#17406b', marginBottom: 6 }}>{title}</div>
      <pre style={{
        maxHeight,
        overflow: 'auto',
        whiteSpace: 'pre-wrap',
        background: '#0f172a',
        color: '#dbeafe',
        padding: 12,
        borderRadius: 12,
        fontSize: 12,
        lineHeight: 1.45,
        margin: 0,
      }}>
        {value || '-'}
      </pre>
    </div>
  );

  const commonColumnStyle = {
    ...styles.card,
    padding: 18,
    height: '100%',
    minHeight: 640,
    boxSizing: 'border-box',
    display: 'flex',
    flexDirection: 'column',
  };

  const weightPlan = info.node_weight_plan || info.node_weight_config || {};
  const weightRows = Array.isArray(weightPlan.items) ? weightPlan.items : [];
  const [weightMode, setWeightMode] = useState(weightPlan.mode || 'weighted');
  const [nodeWeightsDraft, setNodeWeightsDraft] = useState({});
  const [nodeProcessSlotsDraft, setNodeProcessSlotsDraft] = useState({});

  const normalizePercentDraft = (rawMap = {}) => {
    const machines = weightRows
      .map((item) => String(item.machine || '').trim())
      .filter(Boolean);
    if (!machines.length) return {};

    const values = machines.map((machine) => {
      const raw = Number(rawMap[machine]);
      return Number.isFinite(raw) ? Math.max(0, raw) : 0;
    });
    let total = values.reduce((sum, value) => sum + value, 0);
    const working = total > 0 ? values : machines.map(() => 1);
    total = working.reduce((sum, value) => sum + value, 0);

    const exact = working.map((value) => (value * 100) / total);
    const result = exact.map((value) => Math.floor(value));
    let remaining = 100 - result.reduce((sum, value) => sum + value, 0);
    const order = exact
      .map((value, index) => ({ index, remainder: value - result[index], source: working[index] }))
      .sort((a, b) => (b.remainder - a.remainder) || (b.source - a.source) || (a.index - b.index));

    for (let i = 0; i < remaining; i += 1) {
      result[order[i % order.length].index] += 1;
    }

    return Object.fromEntries(machines.map((machine, index) => [machine, result[index]]));
  };

  const makeEqualPercentDraft = () => {
    const raw = {};
    weightRows.forEach((item) => {
      const machine = String(item.machine || '').trim();
      if (machine) raw[machine] = 1;
    });
    return normalizePercentDraft(raw);
  };

  useEffect(() => {
    const nextWeightsRaw = {};
    const nextProcessSlots = {};
    weightRows.forEach((item) => {
      const machine = String(item.machine || '').trim();
      if (!machine) return;
      const rawWeight = item.weight_percent ?? item.weight ?? item.suggested_weight_percent ?? item.suggested_weight ?? 0;
      const value = Number.parseInt(String(rawWeight), 10);
      const slotValue = Number.parseInt(String(item.process_slots ?? item.suggested_process_slots ?? 1), 10) || 1;
      nextWeightsRaw[machine] = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
      nextProcessSlots[machine] = Math.max(1, Math.min(8, slotValue));
    });

    const nextMode = weightPlan.mode || 'weighted';
    setNodeWeightsDraft(
      nextMode === 'equal'
        ? normalizePercentDraft(Object.fromEntries(weightRows.map((item) => [String(item.machine || '').trim(), 1])))
        : normalizePercentDraft(nextWeightsRaw)
    );
    setNodeProcessSlotsDraft(nextProcessSlots);
    setWeightMode(nextMode);
  }, [JSON.stringify(weightRows.map((item) => [
    item.machine,
    item.weight_percent,
    item.weight,
    item.suggested_weight_percent,
    item.suggested_weight,
    item.process_slots,
    item.suggested_process_slots,
  ])), weightPlan.mode]);

  const setDraftWeight = (machine, value) => {
    const parsed = Number.parseInt(String(value ?? '0'), 10);
    const nextValue = Number.isFinite(parsed) ? Math.max(0, Math.min(100, parsed)) : 0;

    // 编辑任意一个节点时，自动按现有比例调整其他节点，确保合计始终为 100%。
    setNodeWeightsDraft((old) => {
      const machines = weightRows
        .map((item) => String(item.machine || '').trim())
        .filter(Boolean);
      const others = machines.filter((name) => name !== machine);
      if (!others.length) return { ...old, [machine]: 100 };

      const remaining = 100 - nextValue;
      const sourceValues = others.map((name) => Math.max(0, Number(old[name]) || 0));
      let sourceTotal = sourceValues.reduce((sum, current) => sum + current, 0);
      const working = sourceTotal > 0 ? sourceValues : others.map(() => 1);
      sourceTotal = working.reduce((sum, current) => sum + current, 0);

      const exact = working.map((current) => (current * remaining) / sourceTotal);
      const allocated = exact.map((current) => Math.floor(current));
      let left = remaining - allocated.reduce((sum, current) => sum + current, 0);
      const order = exact
        .map((current, index) => ({
          index,
          remainder: current - allocated[index],
          source: working[index],
        }))
        .sort((a, b) => (b.remainder - a.remainder) || (b.source - a.source) || (a.index - b.index));

      for (let index = 0; index < left; index += 1) {
        allocated[order[index % order.length].index] += 1;
      }

      const next = { ...old, [machine]: nextValue };
      others.forEach((name, index) => {
        next[name] = allocated[index];
      });
      return next;
    });
  };

  const setDraftProcessSlot = (machine, value) => {
    const n = Number.parseInt(String(value || '1'), 10) || 1;
    setNodeProcessSlotsDraft((old) => ({
      ...old,
      [machine]: Math.max(1, Math.min(8, n)),
    }));
  };

  const changeWeightMode = (mode) => {
    setWeightMode(mode);
    if (mode === 'equal') {
      setNodeWeightsDraft(makeEqualPercentDraft());
    }
  };

  const resetWeightsToSuggested = () => {
    const nextWeightsRaw = {};
    const nextProcessSlots = {};
    weightRows.forEach((item) => {
      const machine = String(item.machine || '').trim();
      if (!machine) return;
      const suggested = item.suggested_weight_percent ?? item.suggested_weight ?? 0;
      nextWeightsRaw[machine] = Math.max(0, Math.min(100, Number.parseInt(String(suggested), 10) || 0));
      nextProcessSlots[machine] = Math.max(1, Math.min(8, Number.parseInt(String(item.suggested_process_slots || 1), 10) || 1));
    });
    setNodeWeightsDraft(normalizePercentDraft(nextWeightsRaw));
    setNodeProcessSlotsDraft(nextProcessSlots);
    setWeightMode('weighted');
  };

  const activeWeightMachines = weightRows
    .map((item) => String(item.machine || '').trim())
    .filter(Boolean);
  const weightTotal = activeWeightMachines.reduce(
    (sum, machine) => sum + (Number(nodeWeightsDraft[machine]) || 0),
    0
  );
  const weightsValid = weightMode === 'equal' || weightTotal === 100;

  const saveWeights = () => {
    if (!weightsValid) {
      window.alert(`所有当前节点的分配比例之和必须为 100%，当前合计为 ${weightTotal}%。`);
      return;
    }
    if (typeof onSaveWeights === 'function') {
      onSaveWeights({
        mode: weightMode,
        weight_unit: 'percent',
        weights: weightMode === 'equal' ? makeEqualPercentDraft() : nodeWeightsDraft,
        process_slots: nodeProcessSlotsDraft,
      });
    }
  };

  const processSlotOptions = Array.from({ length: 8 }, (_, idx) => idx + 1);

  return (
    <section style={{ display: 'grid', gap: 16, minHeight: 'calc(100vh - 98px)' }}>
      <div style={{ ...styles.card, padding: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: 28, fontWeight: 900, color: '#12385f' }}>HTCondor 分布式</div>
            <div style={{ marginTop: 8, color: '#5c7189', lineHeight: 1.7 }}>
              当前接入 HTCondor 提交与执行模式，用于父节点调度、子节点执行和多节点任务分配。
            </div>
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            {okBadge(info.install_validated, '一键安装已验证', '安装未完全验证')}
            {okBadge(info.service_running, 'Condor 服务运行中', 'Condor 服务未运行')}
            {okBadge(ping.ok, 'WRITE 权限通过', 'WRITE 权限失败')}
            {okBadge(info.enabled, 'HTCondor 执行已启用', '当前未启用 HTCondor')}
            {okBadge(sharedEnabled, '共享目录已启用', '共享目录未启用')}
          </div>
        </div>
      </div>

      {message && (
        <div style={{
          ...styles.card,
          padding: '12px 16px',
          whiteSpace: 'pre-wrap',
          color: message.type === 'error' ? '#991b1b' : '#166534',
          background: message.type === 'error' ? '#fff1f2' : '#f0fdf4',
        }}>
          {message.text}
        </div>
      )}

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(340px, 0.92fr) minmax(330px, 0.88fr) minmax(430px, 1.2fr)',
        gap: 16,
        alignItems: 'stretch',
      }}>
        <div style={commonColumnStyle}>
          {cardTitle('运行状态', '集中展示集群角色、节点数量和安装结果。')}

          <div style={{
            padding: '10px 12px',
            borderRadius: 14,
            border: '1px solid #dce8f3',
            background: 'linear-gradient(135deg, #ffffff 0%, #f5f9ff 100%)',
            marginBottom: 10,
          }}>
            <div style={{ fontSize: 12, color: '#6a7f96', fontWeight: 800 }}>HTCondor 版本</div>
            <div style={{ marginTop: 5, fontSize: 14, fontWeight: 900, color: '#173b61', overflowWrap: 'anywhere' }}>
              {shortVersion || '-'}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
            {statCard('运行模式', mode === 'htcondor' ? 'HTCondor 分布式执行' : '本机 local')}
            {statCard('集群状态', clusterStatusText)}
            {statCard('节点数量', String(nodeCount))}
            {statCard('当前角色', roleText)}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 }}>
            {infoCard('父节点信息', parentInfo)}
            {infoCard('子节点信息', childInfo)}
          </div>

          <div style={{
            marginTop: 10,
            padding: '10px 12px',
            borderRadius: 12,
            background: '#f8fbff',
            border: '1px dashed #cfe0f2',
            color: '#5b6f86',
            fontSize: 13,
            lineHeight: 1.55,
          }}>
            <div><strong style={{ color: '#17406b' }}>当前机器：</strong>{info.machine || '-'}</div>
            <div><strong style={{ color: '#17406b' }}>服务状态：</strong>{service.state || (info.service_running ? 'running' : 'stopped')}</div>
            <div><strong style={{ color: '#17406b' }}>安装结果：</strong>{install.message || install.status || '暂无安装结果'}</div>
          </div>

          <div style={{
            marginTop: 12,
            padding: '12px 12px',
            borderRadius: 14,
            background: '#ffffff',
            border: '1px solid #dce8f3',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 900, color: '#12385f' }}>节点信息、任务分配比例与进程槽</div>
                <div style={{ marginTop: 4, fontSize: 12, color: '#64748b', lineHeight: 1.45 }}>
                  分配比例控制节点承担的预计输入工作量，所有节点合计必须为 100%；编辑任一节点时会自动调整其他节点。进程槽控制同一节点同时运行的 EXE 数量。
                </div>
              </div>
              <select
                style={{ ...styles.input, width: 128, minHeight: 38, fontSize: 13 }}
                value={weightMode}
                onChange={(e) => changeWeightMode(e.target.value)}
              >
                <option value="weighted">按百分比分配</option>
                <option value="equal">平均分配</option>
              </select>
            </div>

            {weightRows.length ? (
              <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
                {weightRows.map((node) => {
                  const machine = String(node.machine || '').trim();
                  const draftValue = nodeWeightsDraft[machine] ?? node.weight_percent ?? node.weight ?? node.suggested_weight_percent ?? node.suggested_weight ?? 0;
                  const draftProcessSlot = nodeProcessSlotsDraft[machine] ?? node.process_slots ?? node.suggested_process_slots ?? 1;
                  const memGb = node.memory ? (Number(node.memory) / 1024).toFixed(1) : '-';
                  const isCurrent = machine && machine === info.machine;
                  return (
                    <div
                      key={machine || node.name}
                      style={{
                        display: 'grid',
                        gridTemplateColumns: 'minmax(0, 1.12fr) 0.7fr 0.78fr 144px 92px',
                        gap: 8,
                        alignItems: 'center',
                        padding: '9px 10px',
                        borderRadius: 12,
                        border: '1px solid #e3edf7',
                        background: isCurrent ? '#f0f7ff' : '#f8fbff',
                      }}
                    >
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontWeight: 900, color: '#17406b', overflowWrap: 'anywhere', fontSize: 13 }}>
                          {machine || '-'}{isCurrent ? '（本机）' : ''}
                        </div>
                        <div style={{ marginTop: 3, fontSize: 12, color: '#64748b' }}>
                          {node.state || '-'} / {node.activity || '-'}
                        </div>
                      </div>
                      <div style={{ fontSize: 12, color: '#475569', lineHeight: 1.45 }}>
                        <div>CPU：{node.cpus || '-'}</div>
                        <div>内存：{memGb}GB</div>
                      </div>
                      <div style={{ fontSize: 12, color: '#475569', lineHeight: 1.45 }}>
                        <div>比例建议：<strong style={{ color: '#17406b' }}>{node.suggested_weight_percent ?? node.suggested_weight ?? 0}%</strong></div>
                        <div>槽建议：<strong style={{ color: '#17406b' }}>{node.suggested_process_slots || 1}</strong></div>
                        <div>来源：{node.source === 'manual' ? '手动' : (node.source === 'equal' ? '平均' : '建议')}</div>
                      </div>
                      <div>
                        <div style={{ fontSize: 11, fontWeight: 900, color: '#64748b', marginBottom: 4 }}>分配比例</div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <button
                            type="button"
                            title="减少 1%"
                            aria-label={`${machine} 分配比例减少 1%`}
                            disabled={weightMode === 'equal' || Number(draftValue) <= 0}
                            onClick={() => setDraftWeight(machine, (Number(draftValue) || 0) - 1)}
                            style={{
                              width: 30,
                              minWidth: 30,
                              height: 36,
                              borderRadius: 8,
                              border: '1px solid #b9cce0',
                              background: '#ffffff',
                              color: '#17406b',
                              fontSize: 22,
                              fontWeight: 900,
                              lineHeight: 1,
                              padding: 0,
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              cursor: weightMode === 'equal' || Number(draftValue) <= 0 ? 'not-allowed' : 'pointer',
                              opacity: weightMode === 'equal' || Number(draftValue) <= 0 ? 0.45 : 1,
                              userSelect: 'none',
                            }}
                          >
                            −
                          </button>
                          <input
                            type="text"
                            inputMode="numeric"
                            pattern="[0-9]*"
                            style={{
                              ...styles.input,
                              width: 48,
                              minWidth: 48,
                              minHeight: 36,
                              padding: '0 5px',
                              fontSize: 14,
                              fontWeight: 800,
                              textAlign: 'center',
                            }}
                            value={draftValue}
                            disabled={weightMode === 'equal'}
                            onChange={(e) => setDraftWeight(machine, e.target.value.replace(/\D/g, ''))}
                          />
                          <button
                            type="button"
                            title="增加 1%"
                            aria-label={`${machine} 分配比例增加 1%`}
                            disabled={weightMode === 'equal' || Number(draftValue) >= 100}
                            onClick={() => setDraftWeight(machine, (Number(draftValue) || 0) + 1)}
                            style={{
                              width: 30,
                              minWidth: 30,
                              height: 36,
                              borderRadius: 8,
                              border: '1px solid #b9cce0',
                              background: '#ffffff',
                              color: '#17406b',
                              fontSize: 22,
                              fontWeight: 900,
                              lineHeight: 1,
                              padding: 0,
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              cursor: weightMode === 'equal' || Number(draftValue) >= 100 ? 'not-allowed' : 'pointer',
                              opacity: weightMode === 'equal' || Number(draftValue) >= 100 ? 0.45 : 1,
                              userSelect: 'none',
                            }}
                          >
                            +
                          </button>
                          <span style={{ fontSize: 13, fontWeight: 900, color: '#475569' }}>%</span>
                        </div>
                      </div>
                      <div>
                        <div style={{ fontSize: 11, fontWeight: 900, color: '#64748b', marginBottom: 4 }}>进程槽</div>
                        <select
                          style={{ ...styles.input, minHeight: 36, padding: '0 8px', fontSize: 13 }}
                          value={draftProcessSlot}
                          onChange={(e) => setDraftProcessSlot(machine, e.target.value)}
                        >
                          {processSlotOptions.map((value) => (
                            <option key={value} value={value}>{value}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                  );
                })}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 10,
                  flexWrap: 'wrap',
                  marginTop: 2,
                }}>
                  <div style={{
                    padding: '7px 10px',
                    borderRadius: 10,
                    border: `1px solid ${weightsValid ? '#b7e4c7' : '#fecaca'}`,
                    background: weightsValid ? '#f0fdf4' : '#fff1f2',
                    color: weightsValid ? '#166534' : '#b91c1c',
                    fontSize: 12,
                    fontWeight: 900,
                  }}>
                    当前分配比例合计：{weightTotal}% {weightsValid ? '✓' : '（必须为100%）'}
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    <button style={{ ...styles.whiteBtn, padding: '8px 12px' }} disabled={!!busy} onClick={resetWeightsToSuggested}>
                      恢复建议比例/槽
                    </button>
                    <button
                      style={{ ...styles.blueBtn, padding: '8px 12px', opacity: weightsValid ? 1 : 0.55 }}
                      disabled={!!busy || !weightsValid}
                      onClick={saveWeights}
                    >
                      保存分配比例与进程槽
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ marginTop: 10, color: '#64748b', fontSize: 13 }}>
                暂未发现执行节点。启动父节点或加入子节点后，这里会显示节点分配比例设置。
              </div>
            )}
          </div>

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 'auto', paddingTop: 14 }}>
            <button style={styles.blueBtn} disabled={!!busy} onClick={onRefresh}>刷新状态</button>
            <button style={styles.whiteBtn} disabled={!!busy} onClick={() => onSetMode('htcondor')}>启用 HTCondor 执行</button>
            <button style={styles.whiteBtn} disabled={!!busy} onClick={() => onSetMode('local')}>切回本机执行</button>
            <button style={styles.whiteBtn} disabled={!!busy} onClick={onSmokeTest}>提交自检任务</button>
          </div>
        </div>

        <div style={commonColumnStyle}>
          {cardTitle('集群配置', '父节点负责调度，子节点负责执行。')}

          <div style={{ display: 'grid', gap: 10 }}>
            <label>
              <div style={labelStyle}>父节点 IP</div>
              <input
                style={styles.input}
                value={clusterForm.parent_ip}
                placeholder="例如 192.168.2.136"
                onChange={(e) => setClusterForm({ ...clusterForm, parent_ip: e.target.value })}
              />
            </label>
            <label>
              <div style={labelStyle}>本机绑定 IP，可空</div>
              <input
                style={styles.input}
                value={clusterForm.bind_ip}
                placeholder={localIps[0] || '可留空，系统自动选择'}
                onChange={(e) => setClusterForm({ ...clusterForm, bind_ip: e.target.value, child_ip: e.target.value })}
              />
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <label>
                <div style={labelStyle}>动态端口起始</div>
                <input
                  style={styles.input}
                  value={clusterForm.low_port}
                  onChange={(e) => setClusterForm({ ...clusterForm, low_port: e.target.value })}
                />
              </label>
              <label>
                <div style={labelStyle}>动态端口结束</div>
                <input
                  style={styles.input}
                  value={clusterForm.high_port}
                  onChange={(e) => setClusterForm({ ...clusterForm, high_port: e.target.value })}
                />
              </label>
            </div>
          </div>

          <div style={{
            marginTop: 14,
            padding: '12px 14px',
            borderRadius: 14,
            background: '#ffffff',
            border: '1px solid #dce8f3',
          }}>
            <div style={{ fontSize: 16, fontWeight: 900, color: '#12385f' }}>共享目录</div>
            <div style={{ marginTop: 4, fontSize: 12, color: '#64748b', lineHeight: 1.55 }}>
              父节点点击“添加共享目录”后选择本地数据目录；系统会自动创建 Windows 共享。允许添加多个共享目录。
            </div>
            <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button style={styles.blueBtn} disabled={!!busy} onClick={onPrepareShare}>添加共享目录</button>
              <button style={styles.whiteBtn} disabled={!!busy} onClick={onShowShares}>查看当前配置的共享目录</button>
              <button style={styles.whiteBtn} disabled={!!busy || !sharedEnabled} onClick={onTestShare}>测试共享目录</button>
            </div>
            <div style={{ marginTop: 10, fontSize: 12, color: '#475569', lineHeight: 1.55, overflowWrap: 'anywhere' }}>
              <div><strong>当前状态：</strong>{sharedEnabled ? `已配置 ${sharedShares.length || 1} 个共享目录` : '未配置共享目录'}{sharedRole ? ` / ${sharedRole}` : ''}</div>
              {sharedIo.connect_message && <div><strong>最近结果：</strong>{sharedIo.connect_message}</div>}
            </div>
          </div>

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 16 }}>
            <button style={styles.blueBtn} disabled={!!busy} onClick={onCreateParent}>启动集群</button>
            <button style={styles.whiteBtn} disabled={!!busy} onClick={onJoinParent}>加入集群</button>
            <button
              style={leaveButtonStyle}
              disabled={leaveDisabled}
              onClick={leaveDisabled ? undefined : onLeavePool}
              title={clusterStarted ? '退出当前 HTCondor 集群' : '集群未启动，不能退出'}
            >
              退出 HTCondor 集群
            </button>
          </div>

          <div style={{
            marginTop: 14,
            padding: '12px 14px',
            borderRadius: 14,
            background: '#ffffff',
            border: '1px solid #dce8f3',
            lineHeight: 1.65,
            color: '#5b6f86',
            fontSize: 13,
          }}>
            <div style={{ fontWeight: 900, color: '#17406b', marginBottom: 6 }}>当前配置</div>
            <div>父节点：{clusterForm.parent_ip || '-'}</div>
            <div>本机绑定：{clusterForm.bind_ip || localIps[0] || '-'}</div>
            <div>端口范围：{clusterForm.low_port || 9700} - {clusterForm.high_port || 9800}</div>
            <div>共享目录数量：{sharedShares.length || 0}</div>
          </div>

          <div style={{
            marginTop: 'auto',
            padding: '14px 16px',
            borderRadius: 14,
            background: '#f8fbff',
            border: '1px dashed #cfe0f2',
            color: '#5b6f86',
            lineHeight: 1.7,
            fontSize: 13,
          }}>
            <div style={{ fontWeight: 900, color: '#17406b', marginBottom: 6 }}>操作说明</div>
            <div>1. 父节点点击“启动集群”。</div>
            <div>2. 父节点填写共享目录后点击“添加共享目录”。</div>
            <div>3. 子节点填写父节点 IP 后点击“加入集群”，系统会自动连接共享目录。</div>
            <div>4. 父节点刷新状态，在执行节点列表中确认子节点机器名。</div>
          </div>
        </div>

        <div style={commonColumnStyle}>
          {cardTitle('队列和 Slot', '查看执行节点、队列和 WRITE 权限状态。')}
          <div style={{ display: 'grid', gap: 12 }}>
            {logBlock('执行节点列表', nodes.text, 120)}
            {logBlock('condor_status', slot.text, 190)}
            {logBlock('condor_q', queue.text, 165)}
            {logBlock('condor_ping WRITE', ping.text, 120)}
          </div>
        </div>
      </div>
    </section>
  );
}

function App() {
  const [currentUser, setCurrentUser] = useState(null);
  const [authMode, setAuthMode] = useState('login');
  const [moduleFolderPath, setModuleFolderPath] = useState('');
  const [loginType, setLoginType] = useState('user');
  const [activeCloudId, setActiveCloudId] = useState('');
  const [activeAerosolId, setActiveAerosolId] = useState('');
  const [loginForm, setLoginForm] = useState({ username: '', password: '' });
  const [registerForm, setRegisterForm] = useState({
    username: '',
    password: '',
    confirm_password: '',
    security_question: '',
    security_answer: '',
  });
  const [forgotForm, setForgotForm] = useState({
    username: '',
    question: '',
    answer: '',
    new_password: '',
  });
  const [loginError, setLoginError] = useState('');
  const [startupError, setStartupError] = useState('');

  const [modules, setModules] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [systemResources, setSystemResources] = useState(defaultSystemResources);
  const [users, setUsers] = useState([]);
  const [toolbars, setToolbars] = useState(DEFAULT_TOOLBARS);
  const [dataFiles, setDataFiles] = useState([]);
  const [dataPreview, setDataPreview] = useState(null);
  const [dataPreviewLoading, setDataPreviewLoading] = useState(false);
  const [dataPreviewScale, setDataPreviewScale] = useState(1);
  const [dataPreviewScaleInput, setDataPreviewScaleInput] = useState('100');
  const [htcondorStatus, setHTCondorStatus] = useState(null);
  const [htcondorBusy, setHTCondorBusy] = useState('');
  const [htcondorMessage, setHTCondorMessage] = useState(null);
  const [htcondorClusterForm, setHTCondorClusterForm] = useState({
    parent_ip: '',
    bind_ip: '',
    child_ip: '',
    low_port: '9700',
    high_port: '9800',
    shared_local_root: 'D:\\H8\\data',
    shared_share_name: 'H8Data',
    shared_unc_root: '',
    auto_shared_io: true,
  });
  const [htcondorShareNameModal, setHTCondorShareNameModal] = useState(null);
  const [htcondorShareListModal, setHTCondorShareListModal] = useState(null);
  const [htcondorShareDeleteModal, setHTCondorShareDeleteModal] = useState(null);


  const [activeTab, setActiveTab] = useState(() => getSavedActiveTab() || 'module_mgmt');
  const [activeModuleByTool, setActiveModuleByTool] = useState({});
  const [expandedToolTypes, setExpandedToolTypes] = useState({ cloud: true, aerosol: true });
  const [cloudForms, setCloudForms] = useState({});

  const [runtimeForms, setRuntimeForms] = useState({});
  const [moduleForm, setModuleForm] = useState(emptyModuleForm);
  const [editingModuleId, setEditingModuleId] = useState('');
  const [moduleEditOpen, setModuleEditOpen] = useState(false);
  const [inputEditorOpen, setInputEditorOpen] = useState(false);
  const [inputEditorFields, setInputEditorFields] = useState([]);
  const [uploadToolType, setUploadToolType] = useState('');
  const [dropInfo, setDropInfo] = useState({ drop_dir: '', items: [] });
  const [uploadMsg, setUploadMsg] = useState('');
  const [cppValidation, setCppValidation] = useState(null);
  const [cppValidationLoading, setCppValidationLoading] = useState(false);
  const [moduleMgmtAction, setModuleMgmtAction] = useState('cpp_upload');
  const [pythonSourceDir, setPythonSourceDir] = useState('');
  const [pythonParamJsonPath, setPythonParamJsonPath] = useState('');
  const [pythonModuleId, setPythonModuleId] = useState('');
  const [pythonModuleName, setPythonModuleName] = useState('');
  const [pythonEntryFile, setPythonEntryFile] = useState('main.py');
  const [pythonModuleConfigPath, setPythonModuleConfigPath] = useState('');
  const [pythonModuleConfigPreview, setPythonModuleConfigPreview] = useState(null);
  const [pythonParamInputs, setPythonParamInputs] = useState([]);
  const [pythonUploadMsg, setPythonUploadMsg] = useState('');
  const [pythonValidation, setPythonValidation] = useState(null);
  const [pythonValidationLoading, setPythonValidationLoading] = useState(false);
  const [newToolbarForm, setNewToolbarForm] = useState({ key: '', label: '' });
  const [editingToolbarKey, setEditingToolbarKey] = useState('');
  const [toolbarEditForm, setToolbarEditForm] = useState({ key: '', label: '' });
  const [newUserForm, setNewUserForm] = useState({
    username: '',
    password: '',
    role: 'user',
    security_question: '',
    security_answer: '',
  });
  const [showDropHint, setShowDropHint] = useState(false);

  const [windows, setWindows] = useState([]);
  const [taskTrayMinimized, setTaskTrayMinimized] = useState(false);
  const [memoryWarningWindow, setMemoryWarningWindow] = useState(null);
  const zRef = useRef(2000);
  const pollTimerRef = useRef(null);
  const memoryWarningSeenRef = useRef(new Set());

  const isAdmin = currentUser?.role === 'admin';
  const minimizedTaskCount = windows.filter((w) => w.minimized).length;

  const taskTrayReserveStyle = {
    boxSizing: 'border-box',
  };

  const visibleToolbars = useMemo(() => uniqToolbars(toolbars, modules), [toolbars, modules]);

  const modulesByTool = useMemo(() => {
    const grouped = {};
    visibleToolbars.forEach((t) => {
      grouped[t.key] = [];
    });
    modules.forEach((m) => {
      const key = getModuleToolType(m);
      if (!grouped[key]) grouped[key] = [];
      grouped[key].push(m);
    });
    Object.keys(grouped).forEach((key) => {
      grouped[key].sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id), 'zh-CN'));
    });
    return grouped;
  }, [modules, visibleToolbars]);

  const navItems = useMemo(() => {
    const arr = [];

    // 顶部导航固定顺序：模块管理 → 云反演 → 气溶胶反演 → 分布式 → 任务管理 → 数据管理 → 用户管理。
    if (isAdmin) {
      arr.push({ key: 'module_mgmt', label: '模块管理' });
    }

    const toolRank = (key) => {
      if (key === 'cloud') return 0;
      if (key === 'aerosol') return 1;
      return 2;
    };

    [...visibleToolbars]
      .sort((a, b) => {
        const rankDiff = toolRank(a.key) - toolRank(b.key);
        if (rankDiff !== 0) return rankDiff;
        return String(a.label || a.key).localeCompare(String(b.label || b.key), 'zh-CN');
      })
      .forEach((t) => arr.push({ key: `tool:${t.key}`, label: t.label }));

    if (isAdmin) {
      arr.push({ key: 'htcondor', label: '分布式' });
    }

    arr.push({ key: 'tasks', label: '任务管理' });
    arr.push({ key: 'data_mgmt', label: '数据管理' });

    if (isAdmin) {
      arr.push({ key: 'user_mgmt', label: '用户管理' });
    }
    return arr;
  }, [isAdmin, visibleToolbars]);


  useEffect(() => {
    const init = async () => {
      if (!getAuthToken()) return;
      try {
        const me = await getMe();
        setCurrentUser(me);
        setActiveTab(getFirstActiveTabForUser(me));

        const [toolbarList, mods, taskList, dataList, resources] = await Promise.all([
          getToolbars(),
          me.role === 'admin' ? getAdminModules() : getModules(),
          getTasks(),
          listDataFiles(),
          getSystemResources().catch(() => defaultSystemResources),
        ]);

        setDataFiles(Array.isArray(dataList) ? dataList : []);
        setToolbars(Array.isArray(toolbarList) ? toolbarList : DEFAULT_TOOLBARS);
        setModules(Array.isArray(mods) ? mods : []);
        setTasks(Array.isArray(taskList) ? taskList : []);
        setSystemResources(normalizeSystemResources(resources));

        if (me.role === 'admin') {
          const [userList, drop] = await Promise.all([getUsers(), listDropZips().catch(() => null)]);
          setUsers(Array.isArray(userList) ? userList : []);
          if (drop) setDropInfo(drop);
        }
      } catch (e) {
        clearAuthToken();
        if (e?.status !== 401) {
          setStartupError(e?.message || '系统初始化失败');
        }
      }
    };
    init();
  }, []);

  useEffect(() => {
    if (!currentUser || activeTab !== 'htcondor') return undefined;

    let cancelled = false;
    const refresh = async () => {
      try {
        const data = await getHTCondorStatus();
        if (!cancelled) setHTCondorStatus(data);
      } catch (e) {
        if (!cancelled) {
          setHTCondorMessage({ type: 'error', text: e?.message || '读取 HTCondor 状态失败' });
        }
      }
    };

    refresh();
    const timer = window.setInterval(refresh, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [currentUser, activeTab]);

useEffect(() => {
  modules.forEach((m) => {
    setRuntimeForms((prev) => {
      if (prev[m.id]) return prev;
      const init = { task_name: m.name, _parallel_workers: systemResources.suggested_workers || 1 };
      (m.inputs || []).filter((f) => isFieldVisibleToUser(f) && !isParallelWorkerField(f)).forEach((f) => {
        init[f.key] = f.default ?? '';
      });
      return { ...prev, [m.id]: init };
    });
  });
}, [modules, systemResources.suggested_workers]);

useEffect(() => {
  setActiveModuleByTool((prev) => {
    const next = { ...prev };
    visibleToolbars.forEach((tb) => {
      const list = modulesByTool[tb.key] || [];
      if (!list.length) return;
      if (!next[tb.key] || !list.some((m) => m.id === next[tb.key])) {
        next[tb.key] = list[0].id;
      }
    });
    return next;
  });
}, [visibleToolbars, modulesByTool]);

useEffect(() => {
  if (!currentUser) return;

  // 工具栏还没加载完成时，不要把 tool:cloud 错误切到任务管理或模块管理
  if (visibleToolbars.length === 0) return;

  const hasTool = (key) => visibleToolbars.some((tb) => tb.key === key);
  const firstKey = visibleToolbars[0]?.key || '';

  if (activeTab.startsWith('tool:')) {
    const key = activeTab.slice('tool:'.length);

    if (!hasTool(key)) {
      const fallback = hasTool('cloud')
        ? 'tool:cloud'
        : firstKey
          ? `tool:${firstKey}`
          : 'tasks';

      setActiveTab(fallback);
      saveActiveTab(fallback);
    }
  }

  if (!uploadToolType || !hasTool(uploadToolType)) {
    setUploadToolType(firstKey);
  }

  setModuleForm((prev) => {
    if (prev.tool_type && hasTool(prev.tool_type)) return prev;
    if (!firstKey) return prev;
    return { ...prev, tool_type: firstKey };
  });
}, [currentUser, visibleToolbars, activeTab, uploadToolType]);
  useEffect(() => {
  if (!currentUser) return;
  saveActiveTab(activeTab);
  }, [currentUser, activeTab]);
  useEffect(() => {
    if (!currentUser) {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      return;
    }


    const hasRunningTask =
      tasks.some((t) => t.status === 'queued' || t.status === 'running') ||
      windows.some((w) => {
        const s = w.task?.status;
        return s === 'queued' || s === 'running';
      });

    if (!hasRunningTask) {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      return;
    }

    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }

    pollTimerRef.current = setInterval(async () => {
      try {
        const [latestTasks, resources] = await Promise.all([
          getTasks(),
          getSystemResources().catch(() => null),
        ]);
        setTasks(Array.isArray(latestTasks) ? latestTasks : []);
        if (resources) setSystemResources(normalizeSystemResources(resources));
        await refreshDataFiles();

        for (const w of windows) {
            if (!w.taskId) continue;

            try {
              const detail = await getTask(w.taskId);
              showMemoryWarningForTask(detail);

              const oldStatus = w.task?.status;
              const newStatus = detail?.status;

              const justFinished =
                isActiveTaskStatus(oldStatus) && isTerminalTaskStatus(newStatus);

              const shouldPopupFinishedWindow = justFinished && w.minimized;

              if (shouldPopupFinishedWindow) {
                setTaskTrayMinimized(false);
              }

              setWindows((prev) =>
                prev.map((x) => {
                  if (x.id !== w.id) return x;

                  if (shouldPopupFinishedWindow) {
                    zRef.current += 1;
                    const { left, top } = getCenteredTaskWindowPosition(0);

                    return {
                      ...x,
                      task: mergeTaskForWindow(x.task, detail),
                      minimized: false,
                      left,
                      top,
                      zIndex: zRef.current,
                    };
                  }

                  if (justFinished) {
                    zRef.current += 1;
                    return {
                      ...x,
                      task: mergeTaskForWindow(x.task, detail),
                      zIndex: zRef.current,
                    };
                  }

                  return {
                    ...x,
                    task: mergeTaskForWindow(x.task, detail),
                  };
                })
              );
            } catch {}
          }
      } catch {}
    }, 3000);

    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [currentUser, tasks, windows]);
  useEffect(() => {
    const minimizedCount = windows.filter((w) => w.minimized).length;
    if (minimizedCount === 0) {
      setTaskTrayMinimized(false);
    }
  }, [windows]);

  async function handleLogin() {
    try {
      setLoginError('');
      const data = await login(loginForm.username, loginForm.password, loginType);
      setAuthToken(data.token);
      setCurrentUser(data.user);
      const nextActiveTab = getDefaultActiveTabForRole(data.user?.role);
      setActiveTab(nextActiveTab);
      saveActiveTab(nextActiveTab);

      const [toolbarList, mods, taskList, dataList, resources] = await Promise.all([
        getToolbars(),
        data.user.role === 'admin' ? getAdminModules() : getModules(),
        getTasks(),
        listDataFiles(),
        getSystemResources().catch(() => defaultSystemResources),
      ]);

      setToolbars(Array.isArray(toolbarList) ? toolbarList : DEFAULT_TOOLBARS);
      setModules(Array.isArray(mods) ? mods : []);
      setTasks(Array.isArray(taskList) ? taskList : []);
      setDataFiles(Array.isArray(dataList) ? dataList : []);
      setSystemResources(normalizeSystemResources(resources));

      if (data.user.role === 'admin') {
        const [userList, drop] = await Promise.all([getUsers(), listDropZips().catch(() => null)]);
        setUsers(Array.isArray(userList) ? userList : []);
        if (drop) setDropInfo(drop);
      }
    } catch (e) {
      setLoginError(e?.message || '登录失败，请检查账号、密码或登录身份是否匹配');
    }
  }

async function handleRegister() {
  try {
    setLoginError('');

    if (!registerForm.username.trim()) {
      setLoginError('请输入用户名');
      return;
    }

    if (!registerForm.password) {
      setLoginError('请输入密码');
      return;
    }

    if (!registerForm.confirm_password) {
      setLoginError('请输入确认密码');
      return;
    }

    if (registerForm.password !== registerForm.confirm_password) {
      setLoginError('两次输入的密码不一致');
      return;
    }

    await registerUser({
      username: registerForm.username,
      password: registerForm.password,
      security_question: registerForm.security_question,
      security_answer: registerForm.security_answer,
    });

    setRegisterForm({
      username: '',
      password: '',
      confirm_password: '',
      security_question: '',
      security_answer: '',
    });

    setAuthMode('login');
    alert('注册成功，请登录');
  } catch (e) {
    setLoginError(e?.message || '注册失败');
  }
}

  async function handleForgotQuestion() {
    try {
      const data = await getForgotPasswordQuestion(forgotForm.username);
      setForgotForm((p) => ({ ...p, question: data.question || '' }));
    } catch (e) {
      alert(e?.message || '获取安全问题失败');
    }
  }
  async function browseModuleFolder() {
  try {
    const result = await chooseLocalDir({
      title: '选择可执行模块文件夹',
    });

    if (result?.path) {
      if (blockIfChinesePath(result.path, '可执行模块文件夹')) return;
      setModuleFolderPath(result.path);
      setCppValidation(null);
      await validateCppModuleFolderPath(result.path, { silent: false });
    }
  } catch (e) {
    setUploadMsg(e?.message || '选择模块文件夹失败');
  }
}
async function browsePythonModuleConfigJson() {
  try {
    const result = await chooseLocalFile({
      title: '选择 Python 模块配置 JSON',
      filetypes: [['JSON 文件', '*.json'], ['All Files', '*.*']],
    });

    if (!result?.path) return;
    if (blockIfChinesePath(result.path, 'Python 模块配置 JSON')) return;

    setPythonModuleConfigPath(result.path);
    await validatePythonModuleConfigPath(result.path, { silent: false });
  } catch (e) {
    setPythonUploadMsg(e?.message || '选择 Python 模块配置 JSON 失败');
  }
}
async function browsePythonModuleFolder() {
  try {
    const result = await chooseLocalDir({
      title: '选择 Python 模块文件夹',
    });

    if (!result?.path) return;
    if (blockIfChinesePath(result.path, 'Python 模块文件夹')) return;

    setPythonSourceDir(result.path);
    setPythonModuleConfigPath('');
    setPythonModuleConfigPreview(null);
    setPythonParamInputs([]);
    setPythonValidation(null);

    await validatePythonModuleFolderPath(result.path, { silent: false });
  } catch (e) {
    setPythonUploadMsg(e?.message || '选择 Python 模块文件夹失败');
  }
}
async function validatePythonModuleConfigPath(pathValue = pythonModuleConfigPath, options = {}) {
  const path = String(pathValue || '').trim();
  if (!path) {
    setPythonUploadMsg('请选择 Python 模块配置 JSON');
    setPythonValidation(null);
    setPythonModuleConfigPreview(null);
    setPythonParamInputs([]);
    return null;
  }

  if (blockIfChinesePath(path, 'Python 模块配置 JSON')) {
    setPythonUploadMsg('检测到中文路径，请把 Python 模块配置 JSON 放到纯英文路径后再检查。');
    setPythonValidation(null);
    setPythonModuleConfigPreview(null);
    setPythonParamInputs([]);
    return null;
  }

  setPythonValidationLoading(true);
  if (!options.silent) setPythonUploadMsg('正在检查 Python 模块配置 JSON...');

  try {
    const data = await validatePythonModuleConfig(path);
    setPythonValidation(data);
    setPythonModuleConfigPreview(data?.module || null);
    setPythonParamInputs(Array.isArray(data?.inputs) ? data.inputs : []);

    const errorCount = Array.isArray(data?.errors) ? data.errors.length : 0;
    const warningCount = Array.isArray(data?.warnings) ? data.warnings.length : 0;
    const missingCount = Array.isArray(data?.missing_files) ? data.missing_files.length : 0;
    const inputCount = Array.isArray(data?.inputs) ? data.inputs.length : 0;

    if (data?.can_install) {
      setPythonUploadMsg(`Python 配置检查通过：错误 0 个，警告 ${warningCount} 个，缺失 ${missingCount} 个，已识别 ${inputCount} 个参数。可以安装。`);
    } else {
      setPythonUploadMsg(`Python 配置检查未通过：错误 ${errorCount} 个，警告 ${warningCount} 个，缺失 ${missingCount} 个。请先按下方提示修改。`);
    }

    return data;
  } catch (e) {
    setPythonValidation(null);
    setPythonModuleConfigPreview(null);
    setPythonParamInputs([]);
    setPythonUploadMsg(e?.message || 'Python 模块配置 JSON 检查失败');
    return null;
  } finally {
    setPythonValidationLoading(false);
  }
}
async function validatePythonModuleFolderPath(pathValue = pythonSourceDir, options = {}) {
  const folderPath = String(pathValue || '').trim();

  if (!folderPath) {
    setPythonUploadMsg('请选择 Python 模块文件夹');
    setPythonValidation(null);
    setPythonModuleConfigPreview(null);
    setPythonParamInputs([]);
    return null;
  }

  if (blockIfChinesePath(folderPath, 'Python 模块文件夹')) {
    setPythonUploadMsg('检测到中文路径，请把 Python 模块文件夹放到纯英文路径后再检查。');
    setPythonValidation(null);
    setPythonModuleConfigPreview(null);
    setPythonParamInputs([]);
    return null;
  }

  setPythonValidationLoading(true);
  if (!options.silent) {
    setPythonUploadMsg('正在检查 Python 模块文件夹...');
  }

  try {
    const data = await validatePythonModuleFolder(folderPath);

    setPythonValidation(data);
    setPythonModuleConfigPreview(data?.module || null);
    setPythonParamInputs(Array.isArray(data?.inputs) ? data.inputs : []);

    const errorCount = Array.isArray(data?.errors) ? data.errors.length : 0;
    const warningCount = Array.isArray(data?.warnings) ? data.warnings.length : 0;
    const missingCount = Array.isArray(data?.missing_files) ? data.missing_files.length : 0;
    const inputCount = Array.isArray(data?.inputs) ? data.inputs.length : 0;

    if (data?.can_install) {
      setPythonUploadMsg(
        `Python 模块文件夹检查通过：错误 0 个，警告 ${warningCount} 个，缺失 ${missingCount} 个，已识别 ${inputCount} 个参数。可以安装。`
      );
    } else {
      setPythonUploadMsg(
        `Python 模块文件夹检查未通过：错误 ${errorCount} 个，警告 ${warningCount} 个，缺失 ${missingCount} 个。请先按下方提示修改。`
      );
    }

    return data;
  } catch (e) {
    setPythonValidation(null);
    setPythonModuleConfigPreview(null);
    setPythonParamInputs([]);
    setPythonUploadMsg(e?.message || 'Python 模块文件夹检查失败');
    return null;
  } finally {
    setPythonValidationLoading(false);
  }
}
function looksLikeCppValidationReport(value) {
  return !!(
    value &&
    typeof value === 'object' &&
    (Array.isArray(value.errors) || Array.isArray(value.missing_files) || Array.isArray(value.warnings))
  );
}

function getCppValidationReportFromError(error) {
  const detail = error?.detail;
  if (looksLikeCppValidationReport(detail)) return detail;
  if (looksLikeCppValidationReport(detail?.detail)) return detail.detail;
  return null;
}

function buildFetchFailedUploadMessage(error) {
  const msg = String(error?.message || '请求失败');
  if (msg.includes('Failed to fetch') || error?.status === 0) {
    return [
      '请求没有到达后端，所以前端拿不到 可执行模块配置的具体错误。',
      '请确认：1）后端服务正在运行；2）已经用新版 main.py 重启后端；3）前端请求地址和后端端口一致；4）浏览器控制台 Network 里 /api/admin/modules/validate-cpp-folder 不是红色网络失败。',
      `原始错误：${msg}`,
    ].join('\n');
  }
  return msg;
}

async function validateCppModuleFolderPath(pathValue = moduleFolderPath, options = {}) {
  const path = String(pathValue || '').trim();
  if (!path) {
    setUploadMsg('请选择可执行模块文件夹');
    setCppValidation(null);
    return null;
  }

  if (blockIfChinesePath(path, '可执行模块文件夹')) {
    setUploadMsg('检测到中文路径，请把可执行模块文件夹放到纯英文路径后再检查。');
    setCppValidation(null);
    return null;
  }

  setCppValidationLoading(true);
  if (!options.silent) setUploadMsg('正在检查可执行模块配置...');

  try {
    const data = await validateCppModuleFolder({
      folder_path: path,
      tool_type: uploadToolType,
      auto_collect_dependencies: true,
    });
    setCppValidation(data);

    const errorCount = Array.isArray(data?.errors) ? data.errors.length : 0;
    const warningCount = Array.isArray(data?.warnings) ? data.warnings.length : 0;
    const missingCount = Array.isArray(data?.missing_files) ? data.missing_files.length : 0;

    if (data?.can_install) {
      setUploadMsg(`可执行模块配置检查通过：错误 0 个，警告 ${warningCount} 个，缺失 ${missingCount} 个。可以安装。`);
    } else {
      setUploadMsg(`可执行模块配置检查未通过：错误 ${errorCount} 个，警告 ${warningCount} 个，缺失 ${missingCount} 个。请先按提示修改。`);
    }

    return data;
  } catch (e) {
    const detailReport = getCppValidationReportFromError(e);
    if (detailReport) {
      setCppValidation(detailReport);
      setUploadMsg(detailReport.message || '可执行模块检查未通过，请按下方提示修改。');
      return detailReport;
    }
    setCppValidation(null);
    setUploadMsg(buildFetchFailedUploadMessage(e));
    return null;
  } finally {
    setCppValidationLoading(false);
  }
}

async function installModuleFolder() {
  if (!moduleFolderPath.trim()) {
    setUploadMsg('请选择可执行模块文件夹');
    return;
  }

  if (blockIfChinesePath(moduleFolderPath, '可执行模块文件夹')) {
    setUploadMsg('检测到中文路径，请把可执行模块文件夹放到纯英文路径后再安装。');
    return;
  }

  const validation = await validateCppModuleFolderPath(moduleFolderPath, { silent: true });
  if (!validation?.can_install) {
    setUploadMsg('可执行模块配置没有通过检查，已阻止安装。请根据下方错误、缺失文件和修改建议处理后再安装。');
    return;
  }

  setUploadMsg('正在安装可执行模块，并按 config.json 自动识别输入/输出参数...');

  try {
    await uploadModuleFolder({
      folder_path: moduleFolderPath.trim(),
      tool_type: uploadToolType,
      runtime: 'native',
      auto_collect_dependencies: true,
    });

    setModuleFolderPath('');
    setCppValidation(null);
    setUploadMsg('可执行模块安装成功');

    await Promise.all([refreshModules(), refreshToolbars(), refreshDropZipList()]);
  } catch (e) {
    const detailReport = getCppValidationReportFromError(e);
    if (detailReport) {
      setCppValidation(detailReport);
      setUploadMsg(detailReport.message || '可执行模块安装失败，请按下方提示修改。');
      return;
    }
    setUploadMsg(buildFetchFailedUploadMessage(e));
  }
}

  async function handleForgotReset() {
    try {
      await resetForgotPassword({
        username: forgotForm.username,
        answer: forgotForm.answer,
        new_password: forgotForm.new_password,
      });
      alert('密码已重置');
      setAuthMode('login');
    } catch (e) {
      alert(e?.message || '重置密码失败');
    }
  }

  async function handleLogout() {
    try {
      await logout();
    } catch {}

    clearAuthToken();
    clearSavedActiveTab();

    setCurrentUser(null);
    setActiveTab('module_mgmt');
    setModules([]);
    setTasks([]);
    setUsers([]);
    setWindows([]);
  }

  async function refreshModules() {
    const list = isAdmin ? await getAdminModules() : await getModules();
    setModules(Array.isArray(list) ? list : []);
  }

  async function refreshToolbars() {
    const list = await getToolbars();
    const next = Array.isArray(list) ? list : DEFAULT_TOOLBARS;
    setToolbars(next);
    return next;
  }

  async function refreshDropZipList() {
    if (!isAdmin) return;
    try {
      const data = await listDropZips();
      setDropInfo(data || { drop_dir: '', items: [] });
    } catch {}
  }

  async function refreshUsers() {
    const list = await getUsers();
    setUsers(Array.isArray(list) ? list : []);
  }

  async function refreshTasks() {
    const [list, resources] = await Promise.all([
      getTasks(),
      getSystemResources().catch(() => null),
    ]);
    setTasks(Array.isArray(list) ? list : []);
    if (resources) setSystemResources(normalizeSystemResources(resources));
  }
  async function refreshDataFiles() {
    const list = await listDataFiles();
    setDataFiles(Array.isArray(list) ? list : []);
  }

  async function refreshHTCondorStatus(silent = false) {
    try {
      const data = await getHTCondorStatus();
      setHTCondorStatus(data);
      if (data) {
        setHTCondorClusterForm((old) => ({
          ...old,
          parent_ip: old.parent_ip || data.parent_ip || '',
          bind_ip: old.bind_ip || data.bind_ip || (Array.isArray(data.local_ips) ? (data.local_ips[0] || '') : ''),
          child_ip: old.child_ip || data.bind_ip || (Array.isArray(data.local_ips) ? (data.local_ips[0] || '') : ''),
          low_port: String(data.low_port || old.low_port || '9700'),
          high_port: String(data.high_port || old.high_port || '9800'),
          shared_local_root: old.shared_local_root || data.shared_io?.local_root || 'D:\\H8\\data',
          shared_share_name: old.shared_share_name || data.shared_io?.share_name || 'H8Data',
          shared_unc_root: data.shared_io?.unc_root || old.shared_unc_root || '',
          auto_shared_io: old.auto_shared_io !== false,
        }));
      }
      return data;
    } catch (e) {
      if (!silent) {
        setHTCondorMessage({ type: 'error', text: e?.message || '读取 HTCondor 状态失败' });
      }
      throw e;
    }
  }

  async function runHTCondorAction(actionName, callback) {
    if (htcondorBusy) return null;
    setHTCondorBusy(actionName);
    setHTCondorMessage(null);
    try {
      const data = await callback();
      await refreshHTCondorStatus(true);
      setHTCondorMessage({ type: 'success', text: data?.message || `${actionName}完成` });
      return data;
    } catch (e) {
      setHTCondorMessage({ type: 'error', text: e?.message || `${actionName}失败` });
      return null;
    } finally {
      setHTCondorBusy('');
    }
  }

  async function handleHTCondorSetMode(mode) {
    return runHTCondorAction(
      mode === 'htcondor' ? '启用 HTCondor 执行' : '切回本机执行',
      () => setHTCondorExecutionMode(mode),
    );
  }

  async function handleHTCondorSmokeTest() {
    return runHTCondorAction('提交 HTCondor 自检任务', () => runHTCondorSmokeTest());
  }

  async function handleHTCondorCreateParent() {
    const payload = {
      bind_ip: htcondorClusterForm.bind_ip || '',
      low_port: Number(htcondorClusterForm.low_port || 9700),
      high_port: Number(htcondorClusterForm.high_port || 9800),
    };
    return runHTCondorAction('创建 HTCondor 父节点', () => createHTCondorParent(payload));
  }

  async function handleHTCondorPrepareShare() {
    let selectedPath = '';
    try {
      const result = await chooseLocalDir();
      selectedPath = result?.path || '';
    } catch (e) {
      setHTCondorMessage({ type: 'error', text: e?.message || '选择共享目录失败' });
      return null;
    }
    if (!selectedPath) return null;
    if (blockIfChinesePath(selectedPath, 'HTCondor共享目录')) return null;

    const rawDefaultName = String(selectedPath).split(/[\/]+/).filter(Boolean).pop() || 'H8Data';
    const defaultName = rawDefaultName.replace(/[^0-9A-Za-z_.-]+/g, '_').replace(/^[_\-.]+|[_\-.]+$/g, '') || 'H8Data';
    setHTCondorShareNameModal({
      local_root: selectedPath,
      share_name: defaultName,
    });
    return null;
  }

  async function confirmHTCondorPrepareShare() {
    if (!htcondorShareNameModal) return null;
    const selectedPath = String(htcondorShareNameModal.local_root || '').trim();
    if (!selectedPath) {
      setHTCondorMessage({ type: 'error', text: '共享目录不能为空' });
      return null;
    }
    const rawName = String(htcondorShareNameModal.share_name || '').trim();
    const shareName = rawName.replace(/[^0-9A-Za-z_.-]+/g, '_').replace(/^[_\-.]+|[_\-.]+$/g, '') || 'H8Data';
    const bindIp = htcondorClusterForm.bind_ip || htcondorStatus?.bind_ip || (Array.isArray(htcondorStatus?.local_ips) ? (htcondorStatus.local_ips[0] || '') : '');
    const payload = {
      local_root: selectedPath,
      share_name: shareName,
      unc_host: bindIp,
    };
    const data = await runHTCondorAction('添加 HTCondor 共享目录', () => prepareHTCondorSharedIO(payload));
    if (data?.unc_root) {
      setHTCondorClusterForm((old) => ({
        ...old,
        shared_local_root: selectedPath,
        shared_unc_root: data.unc_root,
        shared_share_name: data.share_name || shareName,
      }));
      setHTCondorShareNameModal(null);
    }
    return data;
  }

  async function handleHTCondorShowShares() {
    try {
      const data = await getHTCondorSharedIO();
      const shares = Array.isArray(data?.shares) ? data.shares : (data?.unc_root ? [data] : []);
      setHTCondorShareListModal({ data, shares });
      return data;
    } catch (e) {
      setHTCondorMessage({ type: 'error', text: e?.message || '读取共享目录配置失败' });
      return null;
    }
  }

  function handleHTCondorAskDeleteShare(item, idx = 0) {
    setHTCondorShareDeleteModal({ item, idx });
  }

  async function confirmHTCondorDeleteShare() {
    const item = htcondorShareDeleteModal?.item || {};
    const payload = {
      share_name: item.share_name || '',
      unc_root: item.unc_root || '',
      local_root: item.local_root || '',
      delete_windows_share: true,
    };
    const data = await runHTCondorAction('删除 HTCondor 共享目录', () => deleteHTCondorSharedIO(payload));
    const shares = Array.isArray(data?.shares) ? data.shares : (data?.unc_root ? [data] : []);
    setHTCondorShareDeleteModal(null);
    setHTCondorShareListModal((old) => (old ? { data, shares } : old));
    setHTCondorClusterForm((old) => {
      const deletedUnc = String(item.unc_root || '').toLowerCase();
      if (deletedUnc && String(old.shared_unc_root || '').toLowerCase() === deletedUnc) {
        return { ...old, shared_local_root: '', shared_unc_root: '', shared_share_name: '' };
      }
      return old;
    });
    return data;
  }

  async function handleHTCondorTestShare() {
    return runHTCondorAction('测试 HTCondor 共享目录', () => testHTCondorSharedIO());
  }

  async function handleHTCondorJoinParent() {
    const shareName = htcondorClusterForm.shared_share_name || 'H8Data';
    const parentIp = htcondorClusterForm.parent_ip || '';
    const payload = {
      parent_ip: parentIp,
      child_ip: htcondorClusterForm.child_ip || htcondorClusterForm.bind_ip || '',
      low_port: Number(htcondorClusterForm.low_port || 9700),
      high_port: Number(htcondorClusterForm.high_port || 9800),
      auto_shared_io: htcondorClusterForm.auto_shared_io !== false,
      share_name: shareName,
      shared_unc_root: parentIp && shareName ? `\\${parentIp}\${shareName}` : '',
    };
    return runHTCondorAction('加入 HTCondor 父节点并自动连接共享目录', () => joinHTCondorParent(payload));
  }

  async function handleHTCondorLeavePool() {
    if (!window.confirm('确定退出 HTCondor 集群吗？系统会重启 Condor 服务。')) return null;
    return runHTCondorAction('退出 HTCondor 集群', () => leaveHTCondorPool());
  }

  async function handleHTCondorSaveWeights(payload) {
    return runHTCondorAction('保存节点任务分配比例与进程槽', () => saveHTCondorNodeWeights(payload));
  }

function showMemoryWarningForTask(task) {
  if (!isMemoryFailureTask(task)) return;

  const taskId = String(task?.id || task?.task_id || 'unknown');
  const key = `memory_warning_${taskId}`;
  if (memoryWarningSeenRef.current.has(key)) return;
  memoryWarningSeenRef.current.add(key);

  zRef.current += 1;
  const { left, top } = getCenteredTaskWindowPosition(36);
  setTaskTrayMinimized(false);
  setMemoryWarningWindow({
    id: key,
    taskId,
    moduleName: task?.module_name || task?.module_id || '未知任务',
    excerpt: getMemoryFailureExcerpt(task),
    left,
    top,
    zIndex: zRef.current,
  });
}

function getCenteredTaskWindowPosition(offset = 0) {
  const popupWidth = 420;
  const popupHeight = 520;

  return {
    left: Math.max(16, (window.innerWidth - popupWidth) / 2 + offset),
    top: Math.max(90, (window.innerHeight - popupHeight) / 2 + offset),
  };
}
function addTaskWindow(task, title) {
  const timedTask = stampTaskTiming(null, task);
  showMemoryWarningForTask(timedTask);
  zRef.current += 1;
  setWindows((prev) => {
    const offset = (prev.length % 4) * 24;
    const popupWidth = 420;
    const popupHeight = 520;

    const left = Math.max(16, (window.innerWidth - popupWidth) / 2 + offset);
    const top = Math.max(90, (window.innerHeight - popupHeight) / 2 + offset);

    return [
      ...prev,
      {
        id: `w_${task.id}`,
        taskId: task.id,
        task: timedTask,
        title,
        minimized: false,
        left,
        top,
        zIndex: zRef.current,
      },
    ];
  });
}

  function bringFront(id) {
    zRef.current += 1;
    setWindows((prev) => prev.map((x) => (x.id === id ? { ...x, zIndex: zRef.current } : x)));
  }

  function moveWindow(id, left, top) {
    setWindows((prev) => prev.map((x) => (x.id === id ? { ...x, left, top } : x)));
  }

  async function stopTaskWindow(id) {
    const target = windows.find((x) => x.id === id);
    if (!target) return;

    try {
      await cancelTask(target.taskId);

      setWindows((prev) =>
        prev.map((x) =>
          x.id === id
            ? {
                ...x,
                task: mergeTaskForWindow(x.task, {
                  ...(x.task || {}),
                  status: 'cancelled',
                  ended_at: new Date().toISOString().slice(0, 19),
                  logs: [
                    ...((x.task && Array.isArray(x.task.logs)) ? x.task.logs : []),
                    '[SYSTEM] 已发送停止任务请求',
                  ],
                }),
                zIndex: ++zRef.current,
              }
            : x
        )
      );

      try {
        const detail = await getTask(target.taskId);
        setWindows((prev) =>
          prev.map((x) =>
            x.id === id
              ? { ...x, task: mergeTaskForWindow(x.task, detail), zIndex: ++zRef.current }
              : x
          )
        );
      } catch {}

      await refreshTasks();
    } catch (e) {
      alert(e?.message || '停止任务失败');
      await refreshTasks();
    }
  }

  async function handleDeleteTask(taskId) {
    const ok = window.confirm(`确定删除任务 ${taskId} 吗？`);
    if (!ok) return;

    try {
      await deleteTask(taskId);
      setWindows((prev) => prev.filter((w) => w.taskId !== taskId));
      setTasks((prev) => prev.filter((t) => t.id !== taskId));
      const latestTasks = await getTasks();
      setTasks(Array.isArray(latestTasks) ? latestTasks : []);
      await refreshTasks();
      await refreshDataFiles();
    } catch (e) {
      alert(e?.message || '删除失败');
    }
  }

  async function browseCloud(key, field) {
    try {
      const result = await chooseLocalDir({
        title: field === 'output_dir' ? '选择输出文件夹' : '选择输入文件夹',
      });
      if (result?.path) {
        if (blockIfChinesePath(result.path, field === 'output_dir' ? '输出文件夹' : '输入文件夹')) return;
        setCloudForms((prev) => ({
          ...prev,
          [key]: {
            ...prev[key],
            [field]: result.path,
          },
        }));
      }
    } catch (e) {
      alert(e?.message || '选择路径失败');
    }
  }

  async function browseField(module, field) {
    try {
      let result;
      const isOutput =
        normalize(field.key) === 'output' || String(field.label || '').includes('输出');

      if (field.type === 'dir_path') {
        result = await chooseLocalDir({ title: `选择${field.label || field.key}` });
      } else if (isOutput) {
        result = await chooseSaveFile({
          title: `选择${field.label || field.key}`,
          defaultextension: '.tif',
          filetypes: [['GeoTIFF', '*.tif'], ['All Files', '*.*']],
        });
      } else {
        result = await chooseLocalFile({
          title: `选择${field.label || field.key}`,
          filetypes: [['All Files', '*.*']],
        });
      }

      if (result?.path) {
        if (blockIfChinesePath(result.path, field.label || field.key || '模块参数路径')) return;
        setRuntimeForms((prev) => ({
          ...prev,
          [module.id]: {
            ...prev[module.id],
            [field.key]: result.path,
          },
        }));
      }
    } catch (e) {
      alert(e?.message || '浏览失败');
    }
  }

  async function runCloud(item) {
    try {
      if (!item.module) {
        alert('未找到对应模块');
        return;
      }

      const form = cloudForms[item.key];
      const inputs = {};
      const inputField = (item.module.inputs || []).find((f) => normalize(f.key).includes('input'));
      const outputField = (item.module.inputs || []).find((f) => normalize(f.key).includes('output'));

      if (inputField) inputs[inputField.key] = form.input_path;
      if (outputField) inputs[outputField.key] = form.output_dir;

      if (blockIfChinesePath(inputs, `${item.title || item.module.name || '模块'} 参数`)) return;

      const task = await runModule(item.module.id, inputs);
      const detail = await getTask(task.id);
      addTaskWindow(detail, form.task_name || item.title);
      await refreshTasks();
    } catch (e) {
      alert(e?.message || '运行失败');
    }
  }

  async function runGeneric(module) {
    try {
      if (!module) return;
      const form = runtimeForms[module.id] || {};
      const inputs = { ...form };
      const title = form.task_name || module.name;
      const parallelWorkers = clampParallelWorkersValue(form._parallel_workers, systemResources.max_workers);
      delete inputs.task_name;
      delete inputs._parallel_workers;

      if (blockIfChinesePath(inputs, `${module.name || module.id || '模块'} 参数`)) return;

      const task = await runModule(module.id, inputs, parallelWorkers);
      const detail = await getTask(task.id);
      addTaskWindow(detail, title);
      await refreshTasks();
    } catch (e) {
      alert(e?.message || '运行失败');
    }
  }



  function fillModuleForm(module) {
    setEditingModuleId(module.id);
    setModuleForm({
      id: module.id || '',
      name: module.name || '',
      description: module.description || '',
      executable: module.executable || '',
      working_dir: module.working_dir || '.',
      config_mode: module.config_mode || 'none',
      command_template_text: JSON.stringify(module.command_template || ['{executable}'], null, 2),
      inputs_text: JSON.stringify(module.inputs || [], null, 2),
      tags_text: (module.tags || []).join(','),
      tool_type: getModuleToolType(module),
      parallel_json_text: JSON.stringify(getModuleParallelConfig(module), null, 2),
      extra_json_text: JSON.stringify(pickModuleExtraFields(module), null, 2),
      enabled: module.enabled !== false,
    });
    setModuleEditOpen(true);
  }

  async function saveCurrentModule() {
    try {
      const extraModuleFields = JSON.parse(moduleForm.extra_json_text || '{}');
      const modulePayload = {
        ...extraModuleFields,
        id: moduleForm.id.trim(),
        name: moduleForm.name.trim(),
        description: moduleForm.description,
        executable: moduleForm.executable,
        working_dir: moduleForm.working_dir,
        config_mode: moduleForm.config_mode,
        command_template: JSON.parse(moduleForm.command_template_text || '[]'),
        inputs: JSON.parse(moduleForm.inputs_text || '[]'),
        tags: moduleForm.tags_text
          .split(',')
          .map((x) => x.trim())
          .filter(Boolean),
        tool_type: moduleForm.tool_type || visibleToolbars[0]?.key || 'uncategorized',
        parallel: JSON.parse(moduleForm.parallel_json_text || '{}'),
        enabled: moduleForm.enabled,
      };

      const pathCheckPayload = {
        executable: modulePayload.executable,
        working_dir: modulePayload.working_dir,
        command_template: modulePayload.command_template,
        inputs: modulePayload.inputs,
      };
      if (blockIfChinesePath(pathCheckPayload, '模块配置路径')) return;

      await saveModule(modulePayload);
      setModuleForm(emptyModuleForm);
      setEditingModuleId('');
      setModuleEditOpen(false);
      await Promise.all([refreshModules(), refreshToolbars()]);
      alert('模块已保存');
    } catch (e) {
      alert(e?.message || '保存模块失败');
    }
  }

function renderValidationItems(title, items, color = '#4f6682') {
  if (!Array.isArray(items) || items.length === 0) return null;
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontWeight: 900, color, marginBottom: 6 }}>{title}：{items.length} 项</div>
      <div style={{ display: 'grid', gap: 6 }}>
        {items.map((item, idx) => (
          <div
            key={`${title}_${idx}`}
            style={{
              border: '1px solid #d7e3f0',
              background: '#fff',
              borderRadius: 10,
              padding: '8px 10px',
              fontSize: 13,
              lineHeight: 1.65,
              color: '#37536f',
            }}
          >
            {typeof item === 'object' && item ? (
              <>
                <div><strong>{item.field || item.path || `第 ${idx + 1} 项`}</strong></div>
                <div>{item.message || item.reason || ''}</div>
                {(item.line || item.column) && (
                  <div style={{ color: '#b45309' }}>位置：第 {item.line || '-'} 行，第 {item.column || '-'} 列</div>
                )}
                {item.encoding && <div style={{ color: '#64748b' }}>文件编码：{item.encoding}</div>}
                {item.suggestion && <div style={{ color: '#64748b' }}>建议：{item.suggestion}</div>}
                {item.snippet && (
                  <pre
                    style={{
                      marginTop: 6,
                      background: '#0f172a',
                      color: '#e2e8f0',
                      borderRadius: 8,
                      padding: 10,
                      overflow: 'auto',
                      whiteSpace: 'pre-wrap',
                    }}
                  >
                    {item.snippet}
                  </pre>
                )}
                {item.traceback && (
                  <pre
                    style={{
                      marginTop: 6,
                      background: '#fff7ed',
                      color: '#9a3412',
                      borderRadius: 8,
                      padding: 10,
                      overflow: 'auto',
                      whiteSpace: 'pre-wrap',
                    }}
                  >
                    {item.traceback}
                  </pre>
                )}
              </>
            ) : (
              <div>{String(item)}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}



function renderPythonValidationReport() {
  if (!pythonValidation) return null;
  const canInstall = !!pythonValidation.can_install;
  const module = pythonValidation.module || {};

  return (
    <div
      style={{
        border: '1px solid #d7e3f0',
        borderRadius: 14,
        background: canInstall ? 'rgba(34,197,94,0.06)' : 'rgba(239,68,68,0.06)',
        padding: 14,
        color: '#173353',
        lineHeight: 1.75,
      }}
    >
      <div style={{ fontWeight: 900, color: canInstall ? '#1f7f36' : '#b42318', marginBottom: 6 }}>
        {canInstall ? 'Python 配置检查通过，可以安装' : 'Python 配置检查未通过，请先修改'}
      </div>
      <div style={{ fontSize: 13, color: '#4f6682', wordBreak: 'break-all' }}>
        配置 JSON：{pythonValidation.config_path || '-'}
      </div>
      <div style={{ fontSize: 13, color: '#4f6682', wordBreak: 'break-all' }}>
        源码目录：{module.source_dir || '-'}
      </div>

      {module && (
        <div style={{ marginTop: 8, fontSize: 13 }}>
          <div><strong>识别模块：</strong>{module.module_name || '-'}（{module.module_id || '-'}）</div>
          <div>所属工具栏：{module.tool_type || '未填写，安装时会尝试推断'}</div>
          <div>入口脚本：{module.entry_file || '-'}</div>
          <div style={{ wordBreak: 'break-all' }}>参数 JSON：{module.param_json_path || '使用 param_template 或默认 config.json'}</div>
          <div>Python 环境模式：{module.python_env_mode || 'create_venv'}</div>
          <div style={{ wordBreak: 'break-all' }}>Python 解释器：{module.python_executable || '未指定'}</div>
        </div>
      )}

      {renderValidationItems('错误', pythonValidation.errors, '#b42318')}
      {renderValidationItems('缺少文件/文件夹', pythonValidation.missing_files, '#b45309')}
      {renderValidationItems('警告', pythonValidation.warnings, '#815b00')}
      {renderValidationItems('修改建议', pythonValidation.suggestions, '#235ed8')}

      {Array.isArray(pythonValidation.inputs) && pythonValidation.inputs.length > 0 && (
        <div style={{ marginTop: 12, borderTop: '1px solid #d7e3f0', paddingTop: 10 }}>
          <div style={{ fontWeight: 900, color: '#12385f', marginBottom: 6 }}>
            参数识别：{pythonValidation.inputs.length} 个
          </div>
          <div style={{ fontSize: 13, color: '#4f6682' }}>
            系统会根据参数 JSON 自动生成运行表单；路径类字段会按 key 和默认值自动判断。
          </div>
        </div>
      )}
    </div>
  );
}

function renderCppValidationReport() {
  if (!cppValidation) return null;
  const dep = cppValidation.dependency_report || {};
  const canInstall = !!cppValidation.can_install;

  return (
    <div
      style={{
        border: '1px solid #d7e3f0',
        borderRadius: 14,
        background: canInstall ? 'rgba(34,197,94,0.06)' : 'rgba(239,68,68,0.06)',
        padding: 14,
        color: '#173353',
        lineHeight: 1.75,
      }}
    >
      <div style={{ fontWeight: 900, color: canInstall ? '#1f7f36' : '#b42318', marginBottom: 6 }}>
        {canInstall ? '可执行模块检查通过，可以安装' : '可执行模块检查未通过，请先修改'}
      </div>
      <div style={{ fontSize: 13, color: '#4f6682', wordBreak: 'break-all' }}>
        module.json：{cppValidation.module_json_path || '-'}
      </div>
      <div style={{ fontSize: 13, color: '#4f6682', wordBreak: 'break-all' }}>
        模块根目录：{cppValidation.module_root || '-'}
      </div>

      {cppValidation.module && (
        <div style={{ marginTop: 8, fontSize: 13 }}>
          <strong>识别模块：</strong>
          {cppValidation.module.name || '-'}（{cppValidation.module.id || '-'}）
        </div>
      )}

      {renderValidationItems('错误', cppValidation.errors, '#b42318')}
      {renderValidationItems('缺少文件/文件夹', cppValidation.missing_files, '#b45309')}
      {renderValidationItems('警告', cppValidation.warnings, '#815b00')}
      {renderValidationItems('修改建议', cppValidation.suggestions, '#235ed8')}

      <div style={{ marginTop: 12, borderTop: '1px solid #d7e3f0', paddingTop: 10 }}>
        <div style={{ fontWeight: 900, color: '#12385f', marginBottom: 6 }}>运行时依赖检查</div>
        <div style={{ fontSize: 13, color: '#4f6682' }}>
          分析器：{dep.analyzer || '未识别'}；目标目录：{dep.target_dir || 'deps/auto'}
        </div>
        {dep.message && <div style={{ fontSize: 13, color: '#4f6682' }}>{dep.message}</div>}
        {Array.isArray(dep.copied) && dep.copied.length > 0 && (
          <div style={{ fontSize: 13, color: '#1f7f36' }}>可自动复制：{dep.copied.join(', ')}</div>
        )}
        {Array.isArray(dep.missing_imports) && dep.missing_imports.length > 0 && (
          <div style={{ fontSize: 13, color: '#b45309' }}>
            未找到 DLL：{dep.missing_imports.join(', ')}
          </div>
        )}
      </div>
    </div>
  );
}

function renderModuleMgmtButton(key, title, desc, onClick) {
  const active = moduleMgmtAction === key;

  return (
    <button
      type="button"
      onClick={() => {
        setModuleMgmtAction(key);
        onClick?.();
      }}
      style={{
        width: '100%',
        textAlign: 'left',
        border: active ? '2px solid #2d7cf6' : '1px solid #d7e3f1',
        background: active
          ? 'linear-gradient(135deg, rgba(45,124,246,0.12), rgba(45,124,246,0.04))'
          : '#fff',
        borderRadius: 14,
        padding: '18px 18px',
        cursor: 'pointer',
        boxShadow: active ? '0 10px 22px rgba(45,124,246,0.12)' : 'none',
      }}
    >
      <div style={{ fontSize: 18, fontWeight: 900, color: '#12385f', marginBottom: 8 }}>
        {title}
      </div>
      <div style={{ fontSize: 13, lineHeight: 1.6, color: '#6a7f96' }}>
        {desc}
      </div>
    </button>
  );
}
  async function installFromDrop(filename = '') {
    if (!uploadToolType) {
      alert('请先添加或选择一个工具栏');
      return;
    }
    setUploadMsg('正在扫描本地投放目录...');
    try {
      const data = await installLocalDropModules(uploadToolType, filename);
      const okCount = data?.installed?.length || 0;
      const failCount = data?.failed?.length || 0;
      setUploadMsg('本地目录安装完成：成功 ' + okCount + ' 个，失败 ' + failCount + ' 个');
      await Promise.all([refreshModules(), refreshToolbars(), refreshDropZipList()]);
      if (failCount) {
        const formatFailure = (item) => {
          const err = item?.error;
          if (err && typeof err === 'object') {
            const parts = [];
            if (err.message) parts.push(err.message);
            if (Array.isArray(err.errors) && err.errors.length) {
              parts.push('错误：' + err.errors.map((e) => `${e.field || '-'}：${e.message || ''}${e.suggestion ? `；建议：${e.suggestion}` : ''}`).join('；'));
            }
            if (Array.isArray(err.missing_files) && err.missing_files.length) {
              parts.push('缺少：' + err.missing_files.map((e) => `${e.path || '-'}${e.reason ? `：${e.reason}` : ''}`).join('；'));
            }
            if (Array.isArray(err.warnings) && err.warnings.length) {
              parts.push('警告：' + err.warnings.map((e) => `${e.field || '-'}：${e.message || ''}`).join('；'));
            }
            return `${item.name}: ${parts.join('\n') || JSON.stringify(err, null, 2)}`;
          }
          return `${item.name}: ${String(err || '未知错误')}`;
        };
        alert((data.failed || []).map(formatFailure).join('\n\n'));
      }
    } catch (e) {
      setUploadMsg(e?.message || '本地目录安装失败');
    }
  }

async function uploadPythonConfigJson() {
  if (!pythonModuleConfigPath.trim()) {
    setPythonUploadMsg('请选择 Python 模块配置 JSON');
    return;
  }

  if (blockIfChinesePath(pythonModuleConfigPath, 'Python 模块配置 JSON')) {
    setPythonUploadMsg('检测到中文路径，请把 Python 模块配置 JSON 放到纯英文路径后再安装。');
    return;
  }

  const validation = await validatePythonModuleConfigPath(pythonModuleConfigPath, { silent: true });
  if (!validation?.can_install) {
    setPythonUploadMsg('Python 配置 JSON 没有通过检查，已阻止安装。请根据下方错误、缺失文件和修改建议处理后再安装。');
    return;
  }

  setPythonUploadMsg('正在读取配置 JSON、创建独立 Python 环境并安装模块，请稍等...');

  try {
    await uploadPythonModuleConfig(pythonModuleConfigPath.trim());

    setPythonModuleConfigPath('');
    setPythonModuleConfigPreview(null);
    setPythonParamInputs([]);
    setPythonValidation(null);
    setPythonUploadMsg('');

    await Promise.all([refreshModules(), refreshToolbars(), refreshDropZipList()]);

    alert('Python 模块已根据配置 JSON 安装成功');
  } catch (e) {
    setPythonUploadMsg(e?.message || 'Python 模块配置 JSON 安装失败');
  }
}
async function uploadPythonFolder() {
  const folderPath = String(pythonSourceDir || '').trim();

  if (!folderPath) {
    setPythonUploadMsg('请选择 Python 模块文件夹');
    return;
  }

  if (blockIfChinesePath(folderPath, 'Python 模块文件夹')) {
    setPythonUploadMsg('检测到中文路径，请把 Python 模块文件夹放到纯英文路径后再安装。');
    return;
  }

  const validation = await validatePythonModuleFolderPath(folderPath, { silent: true });
  if (!validation?.can_install) {
    setPythonUploadMsg('Python 模块文件夹没有通过检查，已阻止安装。请根据下方错误、缺失文件和修改建议处理后再安装。');
    return;
  }

  setPythonUploadMsg('正在根据 python_module.json、config.json、requirements.txt 安装 Python 模块，请稍等...');

  try {
    await uploadPythonFolderModule(folderPath);

    setPythonSourceDir('');
    setPythonModuleConfigPath('');
    setPythonModuleConfigPreview(null);
    setPythonParamInputs([]);
    setPythonValidation(null);
    setPythonUploadMsg('');

    await Promise.all([refreshModules(), refreshToolbars(), refreshDropZipList()]);

    alert('Python 模块文件夹安装成功');
  } catch (e) {
    setPythonUploadMsg(e?.message || 'Python 模块文件夹安装失败');
  }
}
  async function handleAddToolbar() {
    try {
      const label = newToolbarForm.label.trim();
      if (!label) {
        alert('请输入工具类型名称');
        return;
      }
      await addToolbar({
        key: normalizeToolKey(newToolbarForm.key || label),
        label,
      });
      setNewToolbarForm({ key: '', label: '' });
      const createdKey = normalizeToolKey(newToolbarForm.key || label);
      await refreshToolbars();
      if (!uploadToolType) setUploadToolType(createdKey);
      alert('工具栏已添加');
    } catch (e) {
      alert(e?.message || '添加工具栏失败');
    }
  }

  function startEditToolbar(toolbar) {
    setEditingToolbarKey(toolbar.key);
    setToolbarEditForm({ key: toolbar.key, label: toolbar.label || toolbar.key });
  }

  function cancelEditToolbar() {
    setEditingToolbarKey('');
    setToolbarEditForm({ key: '', label: '' });
  }

  async function handleUpdateToolbar() {
    try {
      const label = toolbarEditForm.label.trim();
      if (!editingToolbarKey || !label) {
        alert('请输入工具类型名称');
        return;
      }
      const data = await updateToolbar(editingToolbarKey, {
        key: normalizeToolKey(toolbarEditForm.key || label),
        label,
      });
      const updatedKey = data?.toolbar?.key || normalizeToolKey(toolbarEditForm.key || label);
      if (activeTab === `tool:${editingToolbarKey}`) {
        setActiveTab(`tool:${updatedKey}`);
      }
      if (uploadToolType === editingToolbarKey) {
        setUploadToolType(updatedKey);
      }
      setActiveModuleByTool((prev) => {
        if (updatedKey === editingToolbarKey || !prev[editingToolbarKey]) return prev;
        const next = { ...prev, [updatedKey]: prev[editingToolbarKey] };
        delete next[editingToolbarKey];
        return next;
      });
      setExpandedToolTypes((prev) => {
        if (updatedKey === editingToolbarKey) return prev;
        const next = { ...prev, [updatedKey]: prev[editingToolbarKey] };
        delete next[editingToolbarKey];
        return next;
      });
      cancelEditToolbar();
      await Promise.all([refreshToolbars(), refreshModules()]);
      alert('工具栏已更新');
    } catch (e) {
      alert(e?.message || '更新工具栏失败');
    }
  }

  async function handleDeleteToolbar(toolbar) {
    try {
      const list = modulesByTool[toolbar.key] || [];
      const extra = list.length > 0
        ? `\n该工具栏下有 ${list.length} 个模块，删除工具栏后这些模块会自动移动到其它工具栏；如果没有其它工具栏，会自动移动到“未分类”。`
        : '';
      if (!window.confirm(`确定删除工具栏「${toolbar.label || toolbar.key}」吗？${extra}`)) return;
      const data = await deleteToolbar(toolbar.key);
      const targetKey = data?.target_tool_type || '';
      const latestToolbars = await refreshToolbars();
      await refreshModules();

      if (activeTab === `tool:${toolbar.key}`) {
        const nextKey = targetKey || latestToolbars?.[0]?.key || '';
        setActiveTab(nextKey ? `tool:${nextKey}` : 'module_mgmt');
      }
      if (uploadToolType === toolbar.key) {
        const nextKey = targetKey || latestToolbars?.[0]?.key || '';
        setUploadToolType(nextKey);
      }
      if (editingToolbarKey === toolbar.key) {
        cancelEditToolbar();
      }
      if (data?.moved_count) {
        alert(`工具栏已删除，${data.moved_count} 个模块已移动到其它工具栏`);
      } else {
        alert('工具栏已删除');
      }
    } catch (e) {
      alert(e?.message || '删除工具栏失败');
    }
  }

  function openInputEditor() {
    try {
      const fields = JSON.parse(moduleForm.inputs_text || '[]');
      if (!Array.isArray(fields)) {
        alert('输入字段必须是 JSON 数组');
        return;
      }
      setInputEditorFields(fields.map((f) => ({ ...makeEmptyInputField(), ...f })));
      setInputEditorOpen(true);
    } catch (e) {
      alert('输入字段 JSON 格式错误：' + (e?.message || e));
    }
  }

  function updateInputEditorField(index, patch) {
    setInputEditorFields((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  }

  function saveInputEditor() {
    const cleaned = inputEditorFields.map((item) => {
      const next = { ...item };
      next.key = String(next.key || '').trim();
      next.label = String(next.label || '').trim() || next.key;
      next.type = next.type || 'text';
      next.required = !!next.required;
      next.visible_to_user = next.visible_to_user !== false;
      next.admin_fixed = !!next.admin_fixed;
      next.path_mode = next.path_mode === 'relative_to_module' ? 'relative_to_module' : 'absolute';
      next.io_role = ['input', 'output'].includes(String(next.io_role || '').toLowerCase())
        ? String(next.io_role).toLowerCase()
        : 'auto';
      return next;
    }).filter((item) => item.key);

    setModuleForm((prev) => ({ ...prev, inputs_text: JSON.stringify(cleaned, null, 2) }));
    setInputEditorOpen(false);
  }

  async function handleDeleteModule(moduleId) {
    if (!window.confirm(`确定删除模块 ${moduleId} 吗？`)) return;
    try {
      await deleteModuleApi(moduleId);
      await refreshModules();
    } catch (e) {
      alert(e?.message || '删除模块失败');
    }
  }

  async function handleAddUser() {
    try {
      await addUser(newUserForm);
      setNewUserForm({
        username: '',
        password: '',
        role: 'user',
        security_question: '',
        security_answer: '',
      });
      await refreshUsers();
    } catch (e) {
      alert(e?.message || '新增用户失败');
    }
  }

  async function handleDeleteUser(username) {
    try {
      await deleteUser(username);
      await refreshUsers();
    } catch (e) {
      alert(e?.message || '删除用户失败');
    }
  }

  async function handleRoleChange(username, role) {
    try {
      await updateUserRole(username, role);
      await refreshUsers();
    } catch (e) {
      alert(e?.message || '更新角色失败');
    }
  }

  async function handleEnabledChange(username, enabled) {
    try {
      await updateUserEnabled(username, enabled);
      await refreshUsers();
    } catch (e) {
      alert(e?.message || '更新状态失败');
    }
  }

  async function handleAdminResetPassword(username) {
    const newPassword = prompt(`请输入 ${username} 的新密码`);
    if (!newPassword) return;
    try {
      await adminResetPassword(username, newPassword);
      alert('密码已重置');
    } catch (e) {
      alert(e?.message || '重置密码失败');
    }
  }
  function renderModuleRuntime(module) {

    if (!module) {
      return <div style={{ padding: 20 }}>当前没有匹配到可运行模块</div>;
    }

    const form = runtimeForms[module.id] || {
      task_name: module.name,
      _parallel_workers: systemResources.suggested_workers || 1,
    };
    const resourceInfo = normalizeSystemResources(systemResources);
    const parallelWorkerOptions = getParallelWorkerOptions(resourceInfo);
    const selectedParallelWorkers = clampParallelWorkersValue(
      form._parallel_workers || resourceInfo.suggested_workers || 1,
      resourceInfo.max_workers
    );

    return (
      <>
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 30, fontWeight: 900, color: '#0b2d51' }}>{module.name}</div>
          <div style={{ color: '#617892', marginTop: 6 }}>参数选择与本路径配置</div>
        </div>

        <div style={{ display: 'grid', gap: 18, maxWidth: 980 }}>
          <label>
            <div style={{ fontWeight: 800, color: '#173353', marginBottom: 8 }}>任务名称</div>
            <input
              value={form.task_name || ''}
              onChange={(e) =>
                setRuntimeForms((prev) => ({
                  ...prev,
                  [module.id]: {
                    ...prev[module.id],
                    task_name: e.target.value,
                  },
                }))
              }
              style={styles.input}
            />
          </label>

          <label>
            <div style={{ fontWeight: 800, color: '#173353', marginBottom: 8 }}>
              并行进程数
            </div>
            <select
              value={selectedParallelWorkers}
              onChange={(e) =>
                setRuntimeForms((prev) => ({
                  ...prev,
                  [module.id]: {
                    ...prev[module.id],
                    _parallel_workers: clampParallelWorkersValue(e.target.value, resourceInfo.max_workers),
                  },
                }))
              }
              style={styles.input}
            >
              {parallelWorkerOptions.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>

            <div
              style={{
                marginTop: 10,
                padding: 12,
                borderRadius: 12,
                background: 'rgba(45,124,246,0.06)',
                border: '1px solid rgba(45,124,246,0.13)',
                color: '#45627f',
                fontSize: 13,
                lineHeight: 1.7,
              }}
            >
              <div>本机 CPU 核数：<strong>{resourceInfo.cpu_count}</strong>；建议进程数：<strong>{resourceInfo.suggested_workers}</strong>；上限进程数：<strong>{resourceInfo.max_workers}</strong></div>
              <div style={{ marginTop: 4 }}>建议值按重型遥感模块保守计算：默认最高 2；上限默认最高 4。后端仍会根据 CPU、内存、磁盘压力自动降低或排队。</div>
              <div>当前已占用进程槽：<strong>{resourceInfo.running_workers}/{resourceInfo.max_workers}</strong>；等待队列：<strong>{resourceInfo.queued_task_count}</strong></div>
              <div>系统 CPU 使用率：<strong>{resourceInfo.cpu_percent == null ? '-' : `${Number(resourceInfo.cpu_percent).toFixed(1)}%`}</strong>；模块进程 CPU：<strong>{resourceInfo.running_process_cpu_percent == null ? '-' : `${Number(resourceInfo.running_process_cpu_percent).toFixed(1)}%`}</strong></div>
              <div>内存：<strong>{resourceInfo.memory_percent == null ? '-' : `${Number(resourceInfo.memory_percent).toFixed(1)}%`}</strong>；可用内存：<strong>{resourceInfo.memory_available_gb == null ? '-' : `${Number(resourceInfo.memory_available_gb).toFixed(1)}GB`}</strong>；磁盘：<strong>{resourceInfo.disk_percent == null ? '-' : `${Number(resourceInfo.disk_percent).toFixed(1)}%`}</strong>；剩余：<strong>{resourceInfo.disk_free_gb == null ? '-' : `${Number(resourceInfo.disk_free_gb).toFixed(1)}GB`}</strong></div>
              <div style={{ marginTop: 4 }}>运行前后端会按 CPU、内存、磁盘和模型大小自动降低进程数；运行中负载过高时会暂停启动新子任务，防止电脑卡死。</div>
            </div>
          </label>

          {(module.inputs || []).filter((f) => isFieldVisibleToUser(f) && !isParallelWorkerField(f)).map((field) => (
            <label key={field.key}>
              <div style={{ fontWeight: 800, color: '#173353', marginBottom: 8 }}>
                {field.label || field.key}
              </div>
              {field.type === 'file_path' || field.type === 'dir_path' ? (
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <input
                    value={form[field.key] || ''}
                    onChange={(e) =>
                      setRuntimeForms((prev) => ({
                        ...prev,
                        [module.id]: {
                          ...prev[module.id],
                          [field.key]: e.target.value,
                        },
                      }))
                    }
                    style={{ ...styles.input, flex: 1 }}
                  />
                  <button style={styles.whiteBtn} onClick={() => browseField(module, field)}>
                    浏览
                  </button>
                </div>
              ) : (
                <input
                  value={form[field.key] || ''}
                  onChange={(e) =>
                    setRuntimeForms((prev) => ({
                      ...prev,
                      [module.id]: {
                        ...prev[module.id],
                        [field.key]: e.target.value,
                      },
                    }))
                  }
                  style={styles.input}
                />
              )}
            </label>
          ))}
        </div>

        <div style={{ marginTop: 22 }}>
          <button style={{ ...styles.blueBtn, padding: '12px 28px' }} onClick={() => runGeneric(module)}>
            运行
          </button>
        </div>
      </>
    );
  }

  function renderToolbarOptions() {
    return visibleToolbars.map((tb) => (
      <option key={tb.key} value={tb.key}>
        {tb.label}
      </option>
    ));
  }

  function renderToolbarAdminList() {
    return (
      <div
        style={{
          border: '1px solid #d7e3f0',
          borderRadius: 12,
          background: '#fff',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1.3fr 1fr 70px 128px',
            gap: 8,
            padding: '10px 12px',
            background: 'rgba(240,246,252,0.95)',
            color: '#1a3c63',
            fontWeight: 900,
            fontSize: 13,
          }}
        >
          <div>名称</div>
          <div>标识</div>
          <div>模块</div>
          <div>操作</div>
        </div>

        {visibleToolbars.map((tb) => {
          const list = modulesByTool[tb.key] || [];
          const isEditing = editingToolbarKey === tb.key;

          return (
            <div
              key={tb.key}
              style={{
                display: 'grid',
                gridTemplateColumns: '1.3fr 1fr 70px 128px',
                gap: 8,
                alignItems: 'center',
                padding: '10px 12px',
                borderTop: '1px solid #edf2f7',
                fontSize: 13,
              }}
            >
              {isEditing ? (
                <>
                  <input
                    placeholder="工具类型名称"
                    value={toolbarEditForm.label}
                    onChange={(e) => setToolbarEditForm({ ...toolbarEditForm, label: e.target.value })}
                    style={{ ...styles.input, minHeight: 36, fontSize: 13 }}
                  />
                  <input
                    placeholder="工具类型标识"
                    value={toolbarEditForm.key}
                    onChange={(e) => setToolbarEditForm({ ...toolbarEditForm, key: e.target.value })}
                    style={{ ...styles.input, minHeight: 36, fontSize: 13 }}
                  />
                  <div style={{ color: '#6a7f96' }}>{list.length}</div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button style={{ ...styles.blueBtn, padding: '8px 10px', fontSize: 13 }} onClick={handleUpdateToolbar}>保存</button>
                    <button style={{ ...styles.whiteBtn, padding: '8px 10px', fontSize: 13 }} onClick={cancelEditToolbar}>取消</button>
                  </div>
                </>
              ) : (
                <>
                  <div style={{ fontWeight: 800, color: '#12385f' }}>
                    {tb.label}
                  </div>
                  <div style={{ color: '#6a7f96', wordBreak: 'break-all' }}>{tb.key}</div>
                  <div style={{ color: '#6a7f96' }}>{list.length}</div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button style={{ ...styles.whiteBtn, padding: '8px 10px', fontSize: 13 }} onClick={() => startEditToolbar(tb)}>编辑</button>
                    <button
                      style={{ ...styles.redBtn, padding: '8px 10px', fontSize: 13 }}
                      title={list.length > 0 ? '删除工具栏后模块会自动移动到其它工具栏' : ''}
                      onClick={() => handleDeleteToolbar(tb)}
                    >
                      删除
                    </button>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  function renderModuleEditForm() {
    return (
      <div style={{ display: 'grid', gap: 12 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <select
            value={moduleForm.tool_type}
            onChange={(e) => setModuleForm({ ...moduleForm, tool_type: e.target.value })}
            style={styles.input}
          >
            {renderToolbarOptions()}
          </select>
          <input
            placeholder="ID"
            value={moduleForm.id}
            readOnly
            style={{ ...styles.input, background: '#f3f7fb', color: '#62738a' }}
          />
          <input placeholder="名称" value={moduleForm.name} onChange={(e) => setModuleForm({ ...moduleForm, name: e.target.value })} style={styles.input} />
          <input placeholder="可执行文件 / Python 解释器" value={moduleForm.executable} onChange={(e) => setModuleForm({ ...moduleForm, executable: e.target.value })} style={styles.input} />
          <input placeholder="工作目录" value={moduleForm.working_dir} onChange={(e) => setModuleForm({ ...moduleForm, working_dir: e.target.value })} style={styles.input} />
          <input placeholder="标签，英文逗号分隔" value={moduleForm.tags_text} onChange={(e) => setModuleForm({ ...moduleForm, tags_text: e.target.value })} style={styles.input} />
          <textarea placeholder="描述" value={moduleForm.description} onChange={(e) => setModuleForm({ ...moduleForm, description: e.target.value })} style={{ ...styles.textarea, gridColumn: '1 / span 2', minHeight: 80 }} />
          <textarea placeholder="命令模板(JSON数组)" value={moduleForm.command_template_text} onChange={(e) => setModuleForm({ ...moduleForm, command_template_text: e.target.value })} style={{ ...styles.textarea, gridColumn: '1 / span 2' }} />
          <textarea placeholder="输入字段(JSON数组)：包含输入/输出路径、是否用户可见、管理员预填 resources 等" value={moduleForm.inputs_text} onChange={(e) => setModuleForm({ ...moduleForm, inputs_text: e.target.value })} style={{ ...styles.textarea, gridColumn: '1 / span 2', minHeight: 180 }} />
          <textarea placeholder="并行配置(JSON对象)，保存在 module.json 的 parallel 字段" value={moduleForm.parallel_json_text} onChange={(e) => setModuleForm({ ...moduleForm, parallel_json_text: e.target.value })} style={{ ...styles.textarea, gridColumn: '1 / span 2', minHeight: 110 }} />
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <button style={styles.blueBtn} onClick={saveCurrentModule}>保存模块</button>
          <button style={styles.whiteBtn} onClick={openInputEditor}>编辑输入文件</button>
          <button
            style={styles.whiteBtn}
            onClick={() => {
              setModuleEditOpen(false);
              setEditingModuleId('');
              setModuleForm(emptyModuleForm);
            }}
          >
            取消
          </button>
        </div>
      </div>
    );
  }

  function renderInstalledModulesTree() {
    return (
      <div
        style={{
          display: 'grid',
          gap: 10,
          marginTop: 12,
          flex: 1,
          minHeight: 0,
          overflow: 'auto',
          alignContent: 'start',
          paddingRight: 4,
        }}
      >
        {visibleToolbars.map((tb) => {
          const list = modulesByTool[tb.key] || [];
          const expanded = expandedToolTypes[tb.key] !== false;
          return (
            <div key={tb.key} style={{ border: '1px solid #d6e2ef', background: '#fff', borderRadius: 12, overflow: 'hidden' }}>
              <button
                style={{ ...styles.whiteBtn, width: '100%', border: 'none', borderRadius: 0, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                onClick={() => setExpandedToolTypes((prev) => ({ ...prev, [tb.key]: !expanded }))}
              >
                <span>{expanded ? '▼' : '▶'} {tb.label}</span>
                <span style={{ color: '#6a7f96' }}>{list.length} 个模块</span>
              </button>

              {expanded && (
                <div style={{ padding: 10, display: 'grid', gap: 10 }}>
                  {list.length === 0 && <div style={{ color: '#9aa8b8', fontSize: 13 }}>暂无模块</div>}
                  {list.map((m) => (
                    <div key={m.id} style={{ border: '1px solid #e2ebf5', background: '#fbfdff', borderRadius: 10, padding: 10 }}>
                      <div style={{ fontWeight: 800, color: '#12385f' }}>{m.name}</div>
                      <div style={{ color: '#6a7f96', marginTop: 4, wordBreak: 'break-all' }}>{m.id}</div>
                      {m.enabled === false && <div style={{ color: '#b45309', marginTop: 4 }}>已禁用</div>}
                      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                        <button style={styles.whiteBtn} onClick={() => fillModuleForm(m)}>编辑</button>
                        <button style={styles.redBtn} onClick={() => handleDeleteModule(m.id)}>删除</button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  function renderToolPage(toolKey) {
    const toolbar = visibleToolbars.find((t) => t.key === toolKey) || { key: toolKey, label: toolKey };
    const list = modulesByTool[toolKey] || [];
    const selectedId = activeModuleByTool[toolKey] || list[0]?.id || '';
    const selectedModule = list.find((m) => m.id === selectedId) || list[0] || null;

    return (
      <section
        style={{
          display: 'grid',
          gridTemplateColumns: '300px minmax(0, 1fr) 280px',
          gap: 12,
          minHeight: 'calc(100vh - 98px)',
        }}
      >
        <div style={{ ...styles.card, padding: 18 }}>
          <div style={{ fontSize: 22, fontWeight: 900, color: '#0b2d51', marginBottom: 16 }}>
            {toolbar.label}模块
          </div>

          <div style={{ display: 'grid', gap: 12 }}>
            {list.length === 0 && (
              <div style={{ color: '#8998a8', lineHeight: 1.8 }}>
                这个工具栏下还没有模块。管理员可以在“模块管理”中选择该工具类型后安装或手工添加模块。
              </div>
            )}
            {list.map((m) => (
              <button
                key={m.id}
                onClick={() => setActiveModuleByTool((prev) => ({ ...prev, [toolKey]: m.id }))}
                style={{
                  textAlign: 'left',
                  padding: '18px 16px',
                  borderRadius: 14,
                  border: selectedModule?.id === m.id ? '2px solid #2b73db' : '1px solid #d7e3f0',
                  background:
                    selectedModule?.id === m.id
                      ? 'linear-gradient(135deg, rgba(41,118,210,0.13), rgba(89,176,255,0.08))'
                      : '#fff',
                  cursor: 'pointer',
                }}
              >
                <div style={{ fontWeight: 800, fontSize: 20, color: '#13385f' }}>{m.name}</div>
                <div style={{ marginTop: 8, color: '#60748b', lineHeight: 1.7 }}>{m.description || m.id}</div>
              </button>
            ))}
          </div>
        </div>

        <div style={{ ...styles.card, padding: 22 }}>
          {selectedModule
              ? renderModuleRuntime(selectedModule)
              : <div style={{ padding: 20, color: '#999' }}>当前工具栏暂无可运行模块</div>}
        </div>


      </section>
    );
  }
function renderTaskTrayPanel() {
  const minimizedWindows = windows.filter((w) => w.minimized);

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {minimizedWindows.length === 0 && (
        <div
          style={{
            color: '#6b8097',
            fontSize: 13,
            lineHeight: 1.6,
            padding: '8px 2px',
          }}
        >
          当前无最小化任务
        </div>
      )}

      {minimizedWindows.map((w) => {
        const terminal = isTerminalTaskStatus(w.task?.status);
        const trayTaskId = w.task?.id || w.taskId || '';
        const trayTitle = trayTaskId ? `${w.title} · ${trayTaskId}` : w.title;
        return (
          <div
            key={w.id}
            style={{
              border: '1px solid #d6e2ef',
              background: '#fff',
              borderRadius: 12,
              padding: '10px 12px',
              boxShadow: '0 4px 12px rgba(15,45,80,0.04)',
            }}
          >
            <button
              onClick={() =>
                setWindows((prev) =>
                  prev.map((x) => (x.id === w.id ? { ...x, minimized: false, zIndex: ++zRef.current } : x))
                )
              }
              style={{
                border: 'none',
                background: 'transparent',
                padding: 0,
                margin: 0,
                width: '100%',
                textAlign: 'left',
                cursor: 'pointer',
              }}
            >
              <div
                style={{
                  fontWeight: 800,
                  color: '#12385f',
                  fontSize: 13,
                  lineHeight: 1.35,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
                title={trayTaskId ? `${w.title}（任务ID：${trayTaskId}）` : w.title}
              >
                {trayTitle}
              </div>
              <div style={{ color: '#6a7f96', marginTop: 4, fontSize: 12 }}>
                {w.task?.status || '-'}
              </div>
            </button>

            {terminal && (
              <button
                style={{
                  ...tableDangerBtnStyle,
                  padding: '4px 8px',
                  fontSize: 12,
                  marginTop: 8,
                }}
                onClick={() => setWindows((prev) => prev.filter((x) => x.id !== w.id))}
              >
                关闭
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function renderDataManagementPage() {
  async function handlePreview(file) {
    try {
      setDataPreviewLoading(true);
      const data = await previewDataFile(file.id);
      setDataPreviewScale(1);
      setDataPreviewScaleInput('100');
      setDataPreview(data);
    } catch (e) {
      alert(e?.message || '预览失败');
    } finally {
      setDataPreviewLoading(false);
    }
  }

  async function handleReveal(file) {
    try {
      await revealDataFile(file.id);
    } catch (e) {
      alert(e?.message || '打开文件所在位置失败');
    }
  }

  async function handleDelete(file) {
    if (!window.confirm(`确定删除文件：${file.name || file.file_name || file.id} 吗？`)) return;

    try {
      await deleteDataFile(file.id);
      await refreshDataFiles();
    } catch (e) {
      alert(e?.message || '删除失败');
    }
  }

  return (
    <>
      <section
        style={{
          minHeight: 'calc(100vh - 98px)',
          ...taskTrayReserveStyle,
        }}
      >
        <div
          style={{
            ...styles.card,
            padding: 16,
            minWidth: 0,
            overflow: 'hidden',
          }}
        >
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            marginBottom: 12,
            gap: 12,
          }}>
            <div>
              <div style={{ fontSize: 22, fontWeight: 900, color: '#12385f', letterSpacing: '0.2px' }}>
                数据管理
              </div>
              <div style={{ color: '#6a7f96', marginTop: 4, fontSize: 13 }}>
                只展示模块运行成功后登记的输出文件；文件仍保留在原始输出路径，不会被移动。
              </div>
            </div>

            <button style={{ ...styles.whiteBtn, padding: '8px 18px', fontSize: 13 }} onClick={refreshDataFiles}>
              刷新
            </button>
          </div>

          <div style={{
            overflow: 'auto',
            background: '#fff',
            borderRadius: 10,
            border: '1px solid #dfe8f2',
            boxShadow: '0 8px 22px rgba(15, 45, 80, 0.05)',
          }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1160, tableLayout: 'fixed' }}>
              <thead>
                <tr>
                  <th style={{...thStyle, width: 58}}>文件ID</th>
                  {isAdmin && <th style={{...thStyle, width: 110}}>用户ID</th>}
                  <th style={{...thStyle, width: 250}}>文件名</th>
                  <th style={{...thStyle, width: 78}}>类型</th>
                  <th style={{...thStyle, width: 150}}>所属模块</th>
                  <th style={{...thStyle, width: 88}}>大小</th>
                  <th style={{...thStyle, width: 145}}>创建时间</th>
                  <th style={thStyle}>本地路径</th>
                  <th style={{...thStyle, width: 210}}>操作</th>
                </tr>
              </thead>

              <tbody>
                {dataFiles.length === 0 && (
                  <tr>
                    <td style={tdStyle} colSpan={isAdmin ? 9 : 8}>
                      暂无输出结果文件。运行模块后，系统会自动登记输出路径下的文件。
                    </td>
                  </tr>
                )}

                {dataFiles.map((file, index) => (
                  <tr
                    key={`${file.id}_${file.path}`}
                    style={{
                      background: index % 2 === 0 ? '#f8fbff' : '#ffffff',
                    }}
                  >
                    <td style={tdStyle}>{file.id}</td>

                    {isAdmin && (
                        <td style={tdEllipsisStyle} title={file.owner_username || '-'}>
                          {file.owner_username || '-'}
                        </td>
                    )}

                    <td style={tdEllipsisStyle} title={file.file_name || file.name || '-'}>
                      {file.file_name || file.name || '-'}
                    </td>
                    <td style={tdStyle}>{file.file_type}</td>
                    <td style={tdEllipsisStyle} title={file.module_name || file.module_id || '-'}>
                      {file.module_name || file.module_id}
                    </td>
                    <td style={tdStyle}>{file.size_text || file.size}</td>
                    <td style={tdStyle}>{file.created_at || '-'}</td>
                    <td style={tdEllipsisStyle} title={file.path}>
                      {file.path}
                    </td>
                    <td style={tdStyle}>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'nowrap', alignItems: 'center' }}>
                        <button style={tableActionBtnStyle} onClick={() => handlePreview(file)}>
                          预览
                        </button>
                        <button style={tableActionBtnStyle} onClick={() => handleReveal(file)}>
                          打开位置
                        </button>
                        <button style={taskTableDangerBtnStyle} onClick={() => handleDelete(file)}>
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>


      </section>

      {dataPreview && (() => {
        const meta = dataPreview.meta || {};
        const rawW = Number(meta.preview_width || meta.width || 900) || 900;
        const rawH = Number(meta.preview_height || meta.height || 650) || 650;
        const vw = typeof window !== 'undefined' ? window.innerWidth : 1400;
        const vh = typeof window !== 'undefined' ? window.innerHeight : 900;
        // 预览框默认接近数据管理表格中间区域大小。
        // 图片默认按等比例“适应预览框”：小图会自动放大，大图会自动缩小，尽量铺满预览框且不变形。
        const targetW = Math.floor(vw * 0.72);
        const targetH = Math.floor(vh * 0.76);
        const baseW = Math.min(Math.max(980, targetW), Math.floor(vw * 0.92));
        const baseH = Math.min(Math.max(620, targetH), Math.floor(vh * 0.90));
        const previewPanelW = Math.max(360, baseW - 64);
        const previewPanelH = Math.max(360, baseH - 168);
        const fitScale = Math.max(
          0.01,
          Math.min(
            (previewPanelW - 28) / Math.max(1, rawW),
            (previewPanelH - 28) / Math.max(1, rawH)
          )
        );
        const renderedW = Math.max(1, Math.round(rawW * fitScale * dataPreviewScale));
        const renderedH = Math.max(1, Math.round(rawH * fitScale * dataPreviewScale));
        const actualPercent = Math.round(fitScale * dataPreviewScale * 100);

        function clampPreviewScale(value) {
          const n = Number(value);
          if (!Number.isFinite(n)) return 1;
          return Math.max(0.05, Math.min(20, n));
        }

        function formatScalePercent(scale) {
          return String(Math.round(clampPreviewScale(scale) * 100));
        }

        function updatePreviewScale(next) {
          const nextScale = clampPreviewScale(next);
          setDataPreviewScale(nextScale);
          setDataPreviewScaleInput(formatScalePercent(nextScale));
        }

        function updatePreviewScalePercentText(value) {
          const raw = String(value ?? '').replace('%', '').trim();
          setDataPreviewScaleInput(raw);

          // 允许用户先清空输入框，再输入 120 这种数字。
          // 只有输入的是有效数字时，才实时更新图片缩放。
          if (raw === '') return;
          const n = Number(raw);
          if (!Number.isFinite(n)) return;
          const nextScale = clampPreviewScale(n / 100);
          setDataPreviewScale(nextScale);
        }

        function commitPreviewScalePercentText() {
          const raw = String(dataPreviewScaleInput ?? '').replace('%', '').trim();
          const n = Number(raw);
          if (!raw || !Number.isFinite(n)) {
            setDataPreviewScaleInput(formatScalePercent(dataPreviewScale));
            return;
          }
          updatePreviewScale(n / 100);
        }

        return (
          <div
            style={{
              position: 'fixed',
              inset: 0,
              background: 'rgba(7,22,44,0.32)',
              zIndex: 7000,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: 12,
            }}
          >
            <div
              style={{
                width: baseW,
                height: baseH,
                maxWidth: '96vw',
                maxHeight: '94vh',
                minWidth: 520,
                minHeight: 380,
                resize: 'both',
                overflow: 'auto',
                borderRadius: 18,
                background: 'rgba(248,251,255,0.98)',
                boxShadow: '0 22px 60px rgba(0,0,0,0.22)',
                padding: 16,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, marginBottom: 14 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 20, fontWeight: 900, color: '#102a4a', wordBreak: 'break-all' }}>
                    文件预览：{dataPreview.name || ''}
                  </div>
                  <div style={{ fontSize: 13, color: '#6a7f96', marginTop: 4 }}>
                    原始预览尺寸：{rawW} × {rawH}；默认已等比适应预览框；当前实际显示约为原图 {actualPercent}%
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                  {dataPreview.type === 'image' && dataPreview.data_url && (
                    <>
                      <button
                        style={{ ...styles.whiteBtn, padding: '9px 14px', fontSize: 14 }}
                        onClick={() => updatePreviewScale(dataPreviewScale - 0.1)}
                      >
                        缩小
                      </button>

                      <label
                        style={{
                          display: 'flex',
                          flexDirection: 'row',
                          alignItems: 'center',
                          gap: 6,
                          fontSize: 13,
                          color: '#17406b',
                          fontWeight: 800,
                        }}
                      >
                        比例
                        <input
                          type="text"
                          inputMode="numeric"
                          value={dataPreviewScaleInput}
                          onChange={(e) => updatePreviewScalePercentText(e.target.value)}
                          onBlur={commitPreviewScalePercentText}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.currentTarget.blur();
                            }
                          }}
                          placeholder="100"
                          style={{
                            width: 86,
                            height: 36,
                            borderRadius: 9,
                            border: '1px solid #cdd8ea',
                            padding: '0 8px',
                            fontWeight: 800,
                            color: '#17406b',
                          }}
                        />
                        %
                      </label>

                      <button
                        style={{ ...styles.whiteBtn, padding: '9px 14px', fontSize: 14 }}
                        onClick={() => updatePreviewScale(dataPreviewScale + 0.1)}
                      >
                        放大
                      </button>

                      <button
                        style={{ ...styles.whiteBtn, padding: '9px 14px', fontSize: 14 }}
                        onClick={() => updatePreviewScale(1)}
                      >
                        适应窗口
                      </button>
                    </>
                  )}
                  <button
                    style={{
                      ...styles.redBtn,
                      padding: '12px 22px',
                      fontSize: 16,
                      borderRadius: 12,
                      boxShadow: '0 8px 18px rgba(197, 50, 50, 0.25)',
                    }}
                    onClick={() => setDataPreview(null)}
                  >
                    关闭预览
                  </button>
                </div>
              </div>

              {dataPreviewLoading && <div>加载中...</div>}

              {dataPreview.type === 'image' && dataPreview.data_url ? (
                <div>
                  <div style={{ marginBottom: 10, color: '#6a7f96', wordBreak: 'break-all', fontSize: 12 }}>
                    {dataPreview.path}
                  </div>
                  <div
                    style={{
                      height: previewPanelH,
                      width: '100%',
                      overflow: 'auto',
                      border: '1px solid #d8e3f0',
                      borderRadius: 14,
                      background: '#f8fbff',
                      padding: 14,
                      boxSizing: 'border-box',
                    }}
                  >
                    <div
                      style={{
                        minWidth: '100%',
                        minHeight: '100%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      <img
                        src={dataPreview.data_url}
                        alt={dataPreview.name}
                        style={{
                          width: renderedW,
                          height: renderedH,
                          maxWidth: 'none',
                          maxHeight: 'none',
                          objectFit: 'contain',
                          borderRadius: 12,
                          border: '1px solid #d8e3f0',
                          background: '#fff',
                          boxShadow: '0 6px 18px rgba(15,45,80,0.12)',
                        }}
                      />
                    </div>
                  </div>
                </div>
              ) : (
                <div style={{ lineHeight: 1.8 }}>
                  <div>{dataPreview.message || '该文件暂不支持在线预览'}</div>
                  <div style={{ color: '#6a7f96', wordBreak: 'break-all', marginTop: 8 }}>
                    {dataPreview.path}
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })()}
    </>
  );
}

function renderTaskManagementPage() {
  return (
    <section
      style={{
        minHeight: 'calc(100vh - 98px)',
        ...taskTrayReserveStyle,
      }}
    >
      <div
        style={{
          ...styles.card,
          padding: 16,
          minWidth: 0,
          overflow: 'hidden',
        }}
      >
        <div style={{ fontSize: 22, fontWeight: 900, color: '#12385f', letterSpacing: '0.2px', marginBottom: 12 }}>
          任务管理
        </div>

        <div style={{
          overflow: 'auto',
          background: '#fff',
          borderRadius: 10,
          border: '1px solid #dfe8f2',
          boxShadow: '0 8px 22px rgba(15, 45, 80, 0.05)',
        }}>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              minWidth: 900,
              tableLayout: 'fixed',
            }}
          >
            <thead>
            <tr>
              <th style={{...taskThStyle, width: 130}}>任务ID</th>
              {isAdmin && <th style={{...taskThStyle, width: 110}}>用户ID</th>}
              <th style={{...taskThStyle, width: 190}}>模块</th>
              <th style={{...taskThStyle, width: 90}}>类型</th>
              <th style={{...taskThStyle, width: 115}}>状态</th>
              <th style={{...taskThStyle, width: 165}}>开始时间</th>
              <th style={{...taskThStyle, width: 165}}>结束时间</th>
              <th style={{...taskThStyle, width: 150}}>操作</th>
            </tr>
            </thead>

            <tbody>
            {tasks.filter((item) => !item.parent_id).map((task, index) => (
                <tr
                    key={task.id}
                    style={{
                      background: index % 2 === 0 ? '#f8fbff' : '#ffffff',
                    }}
                >
                  <td style={taskTdEllipsisStyle} title={task.id}>{task.id}</td>

                  {isAdmin && (
                      <td style={taskTdEllipsisStyle} title={task.owner_username || '-'}>
                        {task.owner_username || '-'}
                      </td>
                  )}

                  <td style={taskTdEllipsisStyle} title={task.module_name || '-'}>
                    {task.module_name}
                  </td>
                  <td style={taskTdStyle}>{task.kind === 'parallel' || task.kind === 'batch_parent' ? 'module' : task.kind}</td>
                  <td style={taskTdStyle}>
                    {statusBadge(task.status)}
                    {task.status === 'queued' && (task.queue_position || task.queue_reason) && (
                      <div style={{ marginTop: 6, fontSize: 12, color: '#6b5aa8', lineHeight: 1.45 }}>
                        {task.queue_position ? `排队第 ${task.queue_position} 位` : '排队中'}
                        {task.queue_reason ? `：${task.queue_reason}` : ''}
                      </div>
                    )}
                  </td>
                  <td style={taskTdStyle}>{task.started_at || '-'}</td>
                  <td style={taskTdStyle}>{task.ended_at || '-'}</td>
                  <td style={taskTdStyle}>
                    <div style={{display: 'flex', gap: 6, flexWrap: 'nowrap', alignItems: 'center' }}>
                      <button
                        style={taskTableActionBtnStyle}
                        onClick={async () => {
                          try {
                            const detail = await getTask(task.id);
                            addTaskWindow(detail, task.module_name || task.id);
                          } catch (e) {
                            alert(e?.message || '获取任务详情失败');
                          }
                        }}
                      >
                        查看
                      </button>

                      {(task.status === 'running' || task.status === 'queued') && (
                        <button
                          style={taskTableDangerBtnStyle}
                          onClick={async () => {
                            try {
                              await cancelTask(task.id);
                              await refreshTasks();
                            } catch (e) {
                              alert(e?.message || '关闭失败');
                            }
                          }}
                        >
                          关闭
                        </button>
                      )}

                      <button style={taskTableDangerBtnStyle} onClick={() => handleDeleteTask(task.id)}>
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}

              {tasks.filter((item) => !item.parent_id).length === 0 && (
                <tr>
                  <td colSpan={isAdmin ? 8 : 7} style={{...taskTdStyle, padding: 30, textAlign: 'center', color: '#6c8098'}}>
                    暂无任务
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
  if (!currentUser) {
    return (
      <>
        {startupError && (
          <div
            style={{
              position: 'fixed',
              top: 16,
              right: 16,
              zIndex: 9999,
              background: '#fff4e5',
              color: '#8a4b08',
              border: '1px solid #f3d3a4',
              padding: '10px 14px',
              borderRadius: 10,
              fontSize: 14,
            }}
          >
            {startupError}
          </div>
        )}
        <LoginPage
          authMode={authMode}
          setAuthMode={setAuthMode}
          loginType={loginType}
          setLoginType={setLoginType}
          loginForm={loginForm}
          setLoginForm={setLoginForm}
          registerForm={registerForm}
          setRegisterForm={setRegisterForm}
          forgotForm={forgotForm}
          setForgotForm={setForgotForm}
          loginError={loginError}
          handleLogin={handleLogin}
          handleRegister={handleRegister}
          handleForgotQuestion={handleForgotQuestion}
          handleForgotReset={handleForgotReset}
        />
      </>
    );
  }

  return (
    <div style={styles.page}>
      <div style={styles.topbar}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap', minWidth: 0, flex: '1 1 auto' }}>
          <div style={{ fontSize: 26, fontWeight: 900, whiteSpace: 'nowrap', flexShrink: 0 }}>云和气溶胶反演系统</div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', minWidth: 0 }}>
            {navItems.map((item) => (
                <button
                    key={item.key}
                    onClick={() => {
                      setActiveTab(item.key);
                      saveActiveTab(item.key);
                    }}
                    style={activeTab === item.key ? styles.topBtnActive : styles.topBtn}
                >
                  {item.label}
                </button>
            ))}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', minWidth: 0, flexShrink: 0 }}>
          <div style={{fontWeight: 700}}>
            当前用户：{currentUser.username}（{currentUser.role}）
          </div>
          <button style={styles.topBtn} onClick={handleLogout}>退出登录</button>
        </div>
      </div>

      <div
        style={{
          padding: 12,
          width: '100%',
          maxWidth: '100%',
          minWidth: 0,
          overflowX: 'hidden',
          boxSizing: 'border-box',
        }}
      >
        {activeTab === 'module_mgmt' && isAdmin && (
          <section
            style={{
              ...styles.card,
              padding: 16,
              minHeight: 'calc(100vh - 98px)',
              display: 'flex',
            }}
          >
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '380px minmax(0, 1fr)',
                gap: 16,
                width: '100%',
                minHeight: 'calc(100vh - 130px)',
                alignItems: 'stretch',
              }}
            >
              <div style={{ ...styles.card, padding: 16 }}>
                <div style={{ fontSize: 22, fontWeight: 900, color: '#12385f', marginBottom: 16 }}>
                  模块管理功能
                </div>

                <div style={{ display: 'grid', gap: 12 }}>
                  {renderModuleMgmtButton(
                    'python_upload',
                    'Python 源代码环境上传',
                    '选择 Python 源码文件夹和参数 JSON，系统自动创建独立环境并注册模块。'
                  )}

                  {renderModuleMgmtButton(
                    'cpp_upload',
                    '可执行模块上传',
                    '选择包含 executable_module.json、config.json、可执行程序、resources 和 deps 的模块文件夹，输入方式与 Python 模块一致。'
                  )}
                  {renderModuleMgmtButton(
                    'installed_modules',
                    '已安装模块',
                    '查看当前已经安装到系统中的模块，并进行编辑或删除。'
                  )}
                </div>
              </div>

              <div style={{ display: 'grid', gap: 16, minWidth: 0, minHeight: 'calc(100vh - 130px)', alignItems: 'stretch' }}>
                {moduleMgmtAction === 'python_upload' && (
                  <div style={{ ...styles.card, padding: 22 }}>
                    <div style={{ fontSize: 24, fontWeight: 900, color: '#12385f', marginBottom: 10 }}>
                      Python 源代码环境上传
                    </div>

                    <div style={{color: '#6a7f96', lineHeight: 1.8, marginBottom: 18}}>
                      选择 Python 模块文件夹。该文件夹应包含 python_module.json、config.json、requirements.txt 和入口 .py
                      文件。
                      系统会自动读取配置、识别参数、创建独立 Python 环境，并注册成可运行模块。
                    </div>

                    <div style={{display: 'grid', gap: 16, maxWidth: 960}}>
                      <div>
                        <div style={labelStyle}>Python 模块文件夹</div>
                        <div style={{display: 'flex', gap: 10}}>
                          <input
                              style={{...styles.input, flex: 1}}
                              value={pythonSourceDir}
                              readOnly
                              placeholder="请选择包含 python_module.json、config.json、requirements.txt 和入口 .py 的文件夹"
                          />
                          <button style={styles.whiteBtn} onClick={browsePythonModuleFolder}
                                  disabled={pythonValidationLoading}>
                            {pythonValidationLoading ? '检查中...' : '浏览文件夹并检查'}
                          </button>
                        </div>
                      </div>

                      {renderPythonValidationReport()}

                      {pythonModuleConfigPreview && (
                        <div
                          style={{
                            border: '1px solid #d7e3f0',
                            borderRadius: 12,
                            background: '#fff',
                            padding: 12,
                            color: '#37536f',
                            lineHeight: 1.8,
                          }}
                        >
                          <div style={{ fontWeight: 900, color: '#12385f', marginBottom: 8 }}>
                            模块配置预览
                          </div>
                          <div>模块 ID：{pythonModuleConfigPreview.module_id}</div>
                          <div>模块名称：{pythonModuleConfigPreview.module_name}</div>
                          <div>所属工具栏：{pythonModuleConfigPreview.tool_type}</div>
                          <div>入口文件：{pythonModuleConfigPreview.entry_file}</div>
                          <div style={{ wordBreak: 'break-all' }}>源码文件夹：{pythonModuleConfigPreview.source_dir}</div>
                          <div style={{ wordBreak: 'break-all' }}>参数 JSON：{pythonModuleConfigPreview.param_json_path || '已内嵌 param_template'}</div>
                        </div>
                      )}

                      {pythonParamInputs.length > 0 && (
                        <div
                          style={{
                            border: '1px solid #d7e3f0',
                            borderRadius: 12,
                            background: '#fff',
                            padding: 12,
                          }}
                        >
                          <div style={{ fontWeight: 900, color: '#12385f', marginBottom: 8 }}>
                            已识别参数：{pythonParamInputs.length} 个
                          </div>

                          <div style={{ display: 'grid', gap: 6 }}>
                            {pythonParamInputs.map((item) => (
                              <div
                                key={item.key}
                                style={{
                                  display: 'grid',
                                  gridTemplateColumns: '1fr 120px 1.5fr',
                                  gap: 10,
                                  fontSize: 13,
                                  color: '#37536f',
                                  borderTop: '1px solid #edf2f7',
                                  paddingTop: 6,
                                }}
                              >
                                <div>{item.label || item.key}</div>
                                <div>{item.type}</div>
                                <div style={{ wordBreak: 'break-all' }}>{String(item.default ?? '')}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                        <button
                          style={styles.whiteBtn}
                          onClick={() => downloadTextFile('python_module.json', getPythonModuleConfigTemplateText())}
                        >
                          下载 Python JSON 模板
                        </button>

                        <button
                          style={styles.whiteBtn}
                          onClick={async () => {
                            try {
                              await navigator.clipboard.writeText(getPythonModuleConfigTemplateText());
                              setPythonUploadMsg('已复制 Python 模块配置 JSON 模板。用户需要按自己的源码目录、入口脚本、参数 JSON 和 Python 环境路径修改。');
                            } catch {
                              setPythonUploadMsg(getPythonModuleConfigTemplateText());
                            }
                          }}
                        >
                          复制模板内容
                        </button>

                        <button
                            style={styles.whiteBtn}
                            onClick={() => validatePythonModuleFolderPath(pythonSourceDir)}
                            disabled={pythonValidationLoading}
                        >
                          {pythonValidationLoading ? '检查中...' : '检查文件夹规范'}
                        </button>

                        <button style={styles.blueBtn} onClick={uploadPythonFolder} disabled={pythonValidationLoading}>
                          根据模块文件夹安装模块
                        </button>

                        <button
                            style={styles.whiteBtn}
                            onClick={() => {
                              setPythonSourceDir('');
                              setPythonModuleConfigPath('');
                              setPythonModuleConfigPreview(null);
                              setPythonParamInputs([]);
                              setPythonValidation(null);
                              setPythonUploadMsg('');
                            }}
                        >
                          清空
                        </button>
                      </div>

                      {pythonUploadMsg && (
                          <div
                              style={{
                                whiteSpace: 'pre-wrap',
                                color:
                                    pythonUploadMsg.includes('失败') ||
                              pythonUploadMsg.includes('错误')
                                ? '#bb2c2c'
                                : '#4f6682',
                            lineHeight: 1.7,
                          }}
                        >
                          {pythonUploadMsg}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {moduleMgmtAction === 'cpp_upload' && (
                    <div style={{ ...styles.card, padding: 18 }}>
                      <div style={{ fontSize: 22, fontWeight: 900, color: '#12385f', marginBottom: 14 }}>
                        可执行模块上传
                      </div>

                      <div style={{ color: '#6a7f96', lineHeight: 1.8, marginBottom: 14 }}>
                        请选择一个已经准备好的可执行模块文件夹。新版可执行模块的输入方式与 Python 源码模块一致：
                        文件夹中放 executable_module.json、config.json、可执行程序、resources 固定资源目录、deps 运行时依赖目录。
                        系统会读取 config.json 自动生成输入文件夹、输出文件夹等表单；运行时把平台生成的 config.json 路径传给 exe。
                      </div>

                      <div
                        style={{
                          border: '1px solid #d7e3f0',
                          background: 'rgba(45,124,246,0.05)',
                          borderRadius: 12,
                          padding: 12,
                          color: '#37536f',
                          lineHeight: 1.8,
                          marginBottom: 14,
                        }}
                      >
                        <div style={{ fontWeight: 900, color: '#12385f', marginBottom: 6 }}>依赖说明</div>
                        <div>deps 主要放 <strong>运行时 DLL 依赖</strong>，也就是 exe 启动时还需要但没有打进 exe 的动态库。</div>
                        <div>运行环境路径由用户在 executable_module.json 的 <strong>runtime_env_path</strong> 中填写，例如 MATLAB Runtime、OSGeo4W、其它 bin/runtime 目录；纯独立 exe 可以留空。</div>
                        <div>输入文件夹、输出文件夹不再写 command_template/inputs，而是写在 config.json 里，系统会像 Python 模块一样自动识别并生成表单。</div>
                      </div>

                      <div style={{ display: 'grid', gap: 12 }}>
                        <div>
                          <div style={labelStyle}>可执行模块文件夹</div>
                          <div style={{ display: 'flex', gap: 10 }}>
                            <input
                              style={{ ...styles.input, flex: 1 }}
                              value={moduleFolderPath}
                              onChange={(e) => {
                                setModuleFolderPath(e.target.value);
                                setCppValidation(null);
                              }}
                              placeholder="请选择或粘贴包含 executable_module.json、config.json 和可执行程序的模块文件夹路径"
                            />
                            <button style={styles.whiteBtn} onClick={browseModuleFolder}>
                              浏览并检查
                            </button>
                          </div>
                        </div>

                        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                          <button
                            style={styles.whiteBtn}
                            onClick={() => downloadTextFile('executable_module.json', getCppExecutableModuleTemplateText())}
                          >
                            下载 executable_module.json 模板
                          </button>

                          <button
                            style={styles.whiteBtn}
                            onClick={async () => {
                              try {
                                await navigator.clipboard.writeText(getCppExecutableModuleTemplateText());
                                setUploadMsg('已复制可执行模块 executable_module.json 模板。用户需要按自己的 exe、config.json、运行环境路径和资源目录修改。');
                              } catch {
                                setUploadMsg(getCppExecutableModuleTemplateText());
                              }
                            }}
                          >
                            复制模板内容
                          </button>

                          <button
                            style={styles.whiteBtn}
                            onClick={() => validateCppModuleFolderPath(moduleFolderPath)}
                            disabled={cppValidationLoading}
                          >
                            {cppValidationLoading ? '检查中...' : '检查模块配置'}
                          </button>

                          <button style={styles.blueBtn} onClick={installModuleFolder} disabled={cppValidationLoading}>
                            安装可执行模块
                          </button>

                          <button
                            style={styles.whiteBtn}
                            onClick={() => {
                              setModuleFolderPath('');
                              setUploadMsg('');
                              setCppValidation(null);
                            }}
                          >
                            清空
                          </button>

                          <button style={styles.whiteBtn} onClick={refreshDropZipList}>
                            刷新投放目录
                          </button>

                          <button style={styles.whiteBtn} onClick={() => installFromDrop('')}>
                            扫描本地目录安装
                          </button>

                          <button style={styles.whiteBtn} onClick={() => setShowDropHint(true)}>
                            可执行模块目录说明
                          </button>
                        </div>

                        {uploadMsg && (
                          <div
                            style={{
                              color: uploadMsg.includes('失败') || uploadMsg.includes('未通过') || uploadMsg.includes('阻止') ? '#bb2c2c' : '#4f6682',
                              whiteSpace: 'pre-wrap',
                              lineHeight: 1.7,
                            }}
                          >
                            {uploadMsg}
                          </div>
                        )}

                        {renderCppValidationReport()}

                        {dropInfo.drop_dir && (
                          <div style={{ color: '#6a7f96', fontSize: 13, wordBreak: 'break-all' }}>
                            本地投放目录：{dropInfo.drop_dir}
                          </div>
                        )}

                        {Array.isArray(dropInfo.items) && dropInfo.items.length > 0 && (
                          <div
                            style={{
                              border: '1px solid #d7e3f0',
                              borderRadius: 12,
                              background: '#fff',
                              padding: 12,
                            }}
                          >
                            <div style={{ fontWeight: 900, color: '#12385f', marginBottom: 8 }}>
                              待投放可执行模块 zip：{dropInfo.items.length} 个
                            </div>
                            <div style={{ display: 'grid', gap: 8 }}>
                              {dropInfo.items.map((item) => (
                                <div
                                  key={item.name}
                                  style={{
                                    display: 'grid',
                                    gridTemplateColumns: 'minmax(0,1fr) auto',
                                    gap: 10,
                                    alignItems: 'center',
                                    borderTop: '1px solid #edf2f7',
                                    paddingTop: 8,
                                  }}
                                >
                                  <div style={{ minWidth: 0 }}>
                                    <div style={{ fontWeight: 800, color: '#173353', wordBreak: 'break-all' }}>{item.name}</div>
                                    <div style={{ fontSize: 12, color: '#6a7f96', wordBreak: 'break-all' }}>{item.path}</div>
                                  </div>
                                  <button style={styles.whiteBtn} onClick={() => installFromDrop(item.name)}>
                                    安装这个 zip
                                  </button>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                {moduleMgmtAction === 'installed_modules' && (
                  <>
                    <div
                      style={{
                        ...styles.card,
                        padding: 18,
                        minHeight: 'calc(100vh - 150px)',
                        height: '100%',
                        display: 'flex',
                        flexDirection: 'column',
                        overflow: 'hidden',
                      }}
                    >
                      <div style={{ fontSize: 22, fontWeight: 900, color: '#12385f', marginBottom: 12, flexShrink: 0 }}>
                        已安装模块
                      </div>
                      {renderInstalledModulesTree()}
                    </div>
                  </>
                )}
              </div>
            </div>
          </section>
        )}
        {activeTab === 'user_mgmt' && isAdmin && (
          <section style={{ ...styles.card, padding: 16, minHeight: 'calc(100vh - 98px)' }}>
            <div style={{ fontSize: 22, fontWeight: 900, color: '#12385f', marginBottom: 14 }}>
              用户管理
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 180px', gap: 12 }}>
              <input placeholder="用户名" value={newUserForm.username} onChange={(e) => setNewUserForm({ ...newUserForm, username: e.target.value })} style={styles.input} />
              <input placeholder="密码" type="password" value={newUserForm.password} onChange={(e) => setNewUserForm({ ...newUserForm, password: e.target.value })} style={styles.input} />
              <select value={newUserForm.role} onChange={(e) => setNewUserForm({ ...newUserForm, role: e.target.value })} style={styles.input}>
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
              <input placeholder="安全问题" value={newUserForm.security_question} onChange={(e) => setNewUserForm({ ...newUserForm, security_question: e.target.value })} style={{ ...styles.input, gridColumn: '1 / span 2' }} />
              <input placeholder="安全答案" value={newUserForm.security_answer} onChange={(e) => setNewUserForm({ ...newUserForm, security_answer: e.target.value })} style={styles.input} />
            </div>

            <div style={{ marginTop: 12 }}>
              <button style={styles.blueBtn} onClick={handleAddUser}>新增用户</button>
            </div>

            <div style={{ overflowX: 'auto', marginTop: 16 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', background: '#fff' }}>
                <thead>
                  <tr>
                    <th style={thStyle}>用户名</th>
                    <th style={thStyle}>角色</th>
                    <th style={thStyle}>状态</th>
                    <th style={thStyle}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.username}>
                      <td style={tdStyle}>{u.username}</td>
                      <td style={tdStyle}>
                        <select value={u.role} onChange={(e) => handleRoleChange(u.username, e.target.value)} style={styles.input}>
                          <option value="user">user</option>
                          <option value="admin">admin</option>
                        </select>
                      </td>
                      <td style={tdStyle}>
                        <select value={u.enabled ? 'enabled' : 'disabled'} onChange={(e) => handleEnabledChange(u.username, e.target.value === 'enabled')} style={styles.input}>
                          <option value="enabled">enabled</option>
                          <option value="disabled">disabled</option>
                        </select>
                      </td>
                      <td style={tdStyle}>
                        <div style={{ display: 'flex', gap: 8 }}>
                          <button style={styles.whiteBtn} onClick={() => handleAdminResetPassword(u.username)}>重置密码</button>
                          {u.username !== 'admin' && (
                            <button style={styles.redBtn} onClick={() => handleDeleteUser(u.username)}>删除</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        {activeTab.startsWith('tool:') && renderToolPage(activeTab.slice('tool:'.length))}
        {activeTab === 'data_mgmt' && renderDataManagementPage()}
        {activeTab === 'tasks' && renderTaskManagementPage()}
        {activeTab === 'htcondor' && isAdmin && (
          <HTCondorPage
            status={htcondorStatus}
            busy={htcondorBusy}
            message={htcondorMessage}
            clusterForm={htcondorClusterForm}
            setClusterForm={setHTCondorClusterForm}
            onRefresh={() => refreshHTCondorStatus(false)}
            onSetMode={handleHTCondorSetMode}
            onSmokeTest={handleHTCondorSmokeTest}
            onCreateParent={handleHTCondorCreateParent}
            onJoinParent={handleHTCondorJoinParent}
            onLeavePool={handleHTCondorLeavePool}
            onSaveWeights={handleHTCondorSaveWeights}
            onPrepareShare={handleHTCondorPrepareShare}
            onShowShares={handleHTCondorShowShares}
            onTestShare={handleHTCondorTestShare}
          />
        )}
      </div>

      {windows.filter((w) => !w.minimized).map((w) => (
        <TaskWindow
          key={w.id}
          win={w}
          onMin={(id) => {
            setWindows((prev) => prev.map((x) => (x.id === id ? { ...x, minimized: true } : x)));
            setTaskTrayMinimized(false);
          }}
          onClose={(id) => setWindows((prev) => prev.filter((x) => x.id !== id))}
          onFront={bringFront}
          onMove={moveWindow}
          onStop={stopTaskWindow}
        />
      ))}

      {memoryWarningWindow && (
        <MemoryWarningWindow
          win={memoryWarningWindow}
          onClose={() => setMemoryWarningWindow(null)}
          onFront={() => {
            zRef.current += 1;
            setMemoryWarningWindow((old) => (old ? { ...old, zIndex: zRef.current } : old));
          }}
          onMove={(left, top) => setMemoryWarningWindow((old) => (old ? { ...old, left, top } : old))}
        />
      )}

      
      {windows.some((w) => w.minimized) && (
          <TaskTrayFloatingWindow
            count={windows.filter((w) => w.minimized).length}
            minimized={taskTrayMinimized}
            onToggleMinimize={() => setTaskTrayMinimized((prev) => !prev)}
          >
            {renderTaskTrayPanel()}
          </TaskTrayFloatingWindow>
        )}

      {htcondorShareNameModal && (
        <SimpleOverlay
          title="添加共享目录"
          onClose={() => setHTCondorShareNameModal(null)}
          width="420px"
        >
          <div style={{ color: '#173353', lineHeight: 1.7 }}>
            <div
              style={{
                padding: 12,
                borderRadius: 12,
                background: 'linear-gradient(135deg, rgba(25,118,210,0.10), rgba(54,162,235,0.08))',
                border: '1px solid rgba(39,110,188,0.14)',
                marginBottom: 12,
              }}
            >
              <div style={{ fontSize: 13, color: '#5f7088' }}>已选择本地目录</div>
              <div style={{ fontSize: 15, fontWeight: 900, color: '#173b61', marginTop: 6, overflowWrap: 'anywhere' }}>
                {htcondorShareNameModal.local_root}
              </div>
            </div>

            <label>
              <div style={labelStyle}>共享名</div>
              <input
                style={styles.input}
                value={htcondorShareNameModal.share_name || ''}
                placeholder="例如 H8Data"
                onChange={(e) => setHTCondorShareNameModal((old) => ({ ...old, share_name: e.target.value }))}
              />
            </label>
            <div style={{ marginTop: 8, color: '#64748b', fontSize: 12, lineHeight: 1.6 }}>
              共享名建议只使用英文、数字、下划线、短横线或点号。确认后系统会请求一次管理员权限创建 Windows 共享目录。
            </div>

            <div style={{ display: 'flex', gap: 10, marginTop: 16, flexWrap: 'wrap' }}>
              <button style={styles.blueBtn} disabled={!!htcondorBusy} onClick={confirmHTCondorPrepareShare}>
                确认添加共享目录
              </button>
              <button style={styles.whiteBtn} disabled={!!htcondorBusy} onClick={() => setHTCondorShareNameModal(null)}>
                取消
              </button>
            </div>
          </div>
        </SimpleOverlay>
      )}

      {htcondorShareListModal && (
        <SimpleOverlay
          title="当前配置的共享目录"
          onClose={() => setHTCondorShareListModal(null)}
          width="min(820px, 96vw)"
        >
          <div style={{ color: '#173353', lineHeight: 1.7 }}>
            <div
              style={{
                padding: 12,
                borderRadius: 12,
                background: 'linear-gradient(135deg, rgba(25,118,210,0.10), rgba(54,162,235,0.08))',
                border: '1px solid rgba(39,110,188,0.14)',
                marginBottom: 12,
              }}
            >
              <div style={{ fontSize: 13, color: '#5f7088' }}>共享目录状态</div>
              <div style={{ fontSize: 18, fontWeight: 900, color: '#173b61', marginTop: 6 }}>
                已配置 {htcondorShareListModal.shares?.length || 0} 个共享目录
              </div>
              {htcondorShareListModal.data?.role && (
                <div style={{ marginTop: 4, color: '#5f7088', fontSize: 12 }}>当前角色：{htcondorShareListModal.data.role}</div>
              )}
            </div>

            {htcondorShareListModal.shares?.length ? (
              <div style={{ display: 'grid', gap: 12 }}>
                {htcondorShareListModal.shares.map((item, idx) => (
                  <div
                    key={`${item.unc_root || item.local_root || idx}`}
                    style={{
                      border: '1px solid #d7e6f7',
                      background: '#fff',
                      borderRadius: 12,
                      padding: 12,
                      boxShadow: '0 6px 16px rgba(8,34,70,0.05)',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'flex-start',
                        gap: 12,
                        flexWrap: 'wrap',
                      }}
                    >
                      <div style={{ fontWeight: 900, color: '#17406b', minWidth: 0 }}>
                        共享目录 {idx + 1}：{item.share_name || '-'}
                      </div>
                      <span style={{
                        padding: '4px 9px',
                        borderRadius: 999,
                        background: item.enabled !== false ? '#dcfce7' : '#fee2e2',
                        color: item.enabled !== false ? '#166534' : '#991b1b',
                        fontSize: 12,
                        fontWeight: 800,
                        whiteSpace: 'nowrap',
                      }}>
                        {item.enabled !== false ? '已启用' : '未启用'}
                      </span>
                    </div>
                    <div style={{ marginTop: 8, fontSize: 13, color: '#475569', overflowWrap: 'anywhere' }}>
                      <div><strong>父节点本地目录：</strong>{item.local_root || '-'}</div>
                      <div><strong>UNC 路径：</strong>{item.unc_root || '-'}</div>
                      <div><strong>共享名：</strong>{item.share_name || '-'}</div>
                      {item.connect_message && <div><strong>连接结果：</strong>{item.connect_message}</div>}
                    </div>
                    <div
                      style={{
                        marginTop: 10,
                        paddingTop: 10,
                        borderTop: '1px dashed #d7e6f7',
                        display: 'flex',
                        justifyContent: 'flex-end',
                      }}
                    >
                      <button
                        style={{
                          ...styles.redBtn,
                          padding: '7px 12px',
                          borderRadius: 8,
                          fontSize: 12,
                          minWidth: 108,
                        }}
                        disabled={!!htcondorBusy}
                        onClick={() => handleHTCondorAskDeleteShare(item, idx)}
                      >
                        删除此共享目录
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ padding: 12, borderRadius: 12, background: '#fff', border: '1px solid #d7e6f7', color: '#64748b' }}>
                当前还没有配置共享目录。请先点击“添加共享目录”。
              </div>
            )}
          </div>
        </SimpleOverlay>
      )}

      {htcondorShareDeleteModal && (
        <SimpleOverlay
          title="删除共享目录"
          onClose={() => setHTCondorShareDeleteModal(null)}
          width="430px"
        >
          <div style={{ color: '#173353', lineHeight: 1.7 }}>
            <div
              style={{
                padding: 12,
                borderRadius: 12,
                background: 'linear-gradient(135deg, rgba(220,38,38,0.08), rgba(245,158,11,0.08))',
                border: '1px solid rgba(220,38,38,0.14)',
                marginBottom: 12,
              }}
            >
              <div style={{ fontSize: 13, color: '#8a5b5b' }}>即将删除共享配置</div>
              <div style={{ fontSize: 16, fontWeight: 900, color: '#7f1d1d', marginTop: 6 }}>
                {htcondorShareDeleteModal.item?.share_name || '-'}
              </div>
              <div style={{ marginTop: 8, fontSize: 13, color: '#475569', overflowWrap: 'anywhere' }}>
                <div><strong>本地目录：</strong>{htcondorShareDeleteModal.item?.local_root || '-'}</div>
                <div><strong>UNC 路径：</strong>{htcondorShareDeleteModal.item?.unc_root || '-'}</div>
              </div>
            </div>
            <div style={{ color: '#64748b', fontSize: 13 }}>
              删除操作只移除系统中的共享目录配置，并尝试删除 Windows 共享映射；不会删除本地目录和里面的数据文件。
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 16, flexWrap: 'wrap' }}>
              <button style={styles.redBtn} disabled={!!htcondorBusy} onClick={confirmHTCondorDeleteShare}>
                确认删除
              </button>
              <button style={styles.whiteBtn} disabled={!!htcondorBusy} onClick={() => setHTCondorShareDeleteModal(null)}>
                取消
              </button>
            </div>
          </div>
        </SimpleOverlay>
      )}

      {moduleEditOpen && (
        <SimpleOverlay
          title={`编辑模块：${editingModuleId || moduleForm.id || ''}`}
          onClose={() => {
            setModuleEditOpen(false);
            setEditingModuleId('');
            setModuleForm(emptyModuleForm);
          }}
          width="min(1120px, 96vw)"
        >
          {renderModuleEditForm()}
        </SimpleOverlay>
      )}

{inputEditorOpen && (
        <SimpleOverlay
          title="编辑输入文件"
          onClose={() => setInputEditorOpen(false)}
          width="min(1180px, 96vw)"
        >
          <div style={{ color: '#173353', lineHeight: 1.7 }}>
            <div style={{ marginBottom: 12, color: '#5f7088' }}>
              这里设置每个输入字段是否需要用户填写。选择“管理员预填/隐藏”后，用户运行界面不会显示该字段；默认值可以写 resources 里的相对路径，例如 resources/ConfigXMLFile.xml。
            </div>

            <div style={{ display: 'grid', gap: 12 }}>
              {inputEditorFields.map((field, index) => (
                <div
                  key={index}
                  style={{
                    border: '1px solid #d7e3f0',
                    background: '#fff',
                    borderRadius: 12,
                    padding: 12,
                  }}
                >
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 130px 110px', gap: 10 }}>
                    <input
                      placeholder="key，例如 input_file"
                      value={field.key || ''}
                      onChange={(e) => updateInputEditorField(index, { key: e.target.value })}
                      style={styles.input}
                    />
                    <input
                      placeholder="显示名称"
                      value={field.label || ''}
                      onChange={(e) => updateInputEditorField(index, { label: e.target.value })}
                      style={styles.input}
                    />
                    <select
                      value={field.type || 'text'}
                      onChange={(e) => updateInputEditorField(index, { type: e.target.value })}
                      style={styles.input}
                    >
                      <option value="text">text</option>
                      <option value="textarea">textarea</option>
                      <option value="number">number</option>
                      <option value="file_path">file_path</option>
                      <option value="dir_path">dir_path</option>
                      <option value="password">password</option>
                    </select>
                    <select
                      value={field.required ? 'true' : 'false'}
                      onChange={(e) => updateInputEditorField(index, { required: e.target.value === 'true' })}
                      style={styles.input}
                    >
                      <option value="true">必填</option>
                      <option value="false">非必填</option>
                    </select>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 180px 170px 160px', gap: 10, marginTop: 10 }}>
                    <input
                      placeholder="默认值 / 管理员预填路径，例如 resources/ConfigXMLFile.xml"
                      value={field.default ?? ''}
                      onChange={(e) => updateInputEditorField(index, { default: e.target.value })}
                      style={styles.input}
                    />
                    <select
                      value={field.visible_to_user === false ? 'hidden' : 'visible'}
                      onChange={(e) => {
                        const visible = e.target.value === 'visible';
                        updateInputEditorField(index, { visible_to_user: visible, admin_fixed: !visible });
                      }}
                      style={styles.input}
                    >
                      <option value="visible">用户输入</option>
                      <option value="hidden">用户隐藏</option>
                    </select>
                    <select
                      value={field.path_mode || 'absolute'}
                      onChange={(e) => updateInputEditorField(index, { path_mode: e.target.value })}
                      style={styles.input}
                    >
                      <option value="absolute">绝对路径/原样</option>
                      <option value="relative_to_module">相对模块目录</option>
                    </select>
                    <select
                      value={field.io_role || 'auto'}
                      onChange={(e) => updateInputEditorField(index, { io_role: e.target.value })}
                      style={styles.input}
                      title="用于数据管理：只有 output 字段的结果会登记到数据管理"
                    >
                      <option value="auto">自动判断输入/输出</option>
                      <option value="input">输入文件/资源</option>
                      <option value="output">输出文件/目录</option>
                    </select>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, marginTop: 10 }}>
                    <input
                      placeholder="说明 help_text"
                      value={field.help_text || ''}
                      onChange={(e) => updateInputEditorField(index, { help_text: e.target.value })}
                      style={styles.input}
                    />
                    <button
                      style={styles.redBtn}
                      onClick={() => setInputEditorFields((prev) => prev.filter((_, i) => i !== index))}
                    >
                      删除
                    </button>
                  </div>

                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, flexDirection: 'row' }}>
                    <input
                      type="checkbox"
                      checked={!!field.admin_fixed}
                      onChange={(e) => updateInputEditorField(index, { admin_fixed: e.target.checked, visible_to_user: e.target.checked ? false : field.visible_to_user !== false })}
                      style={{ width: 'auto' }}
                    />
                    <span>管理员预填/隐藏：适合 resources 文件夹里的固定 XML、LUT、模型、掩膜等资源</span>
                  </label>
                </div>
              ))}
            </div>

            <div style={{ display: 'flex', gap: 10, marginTop: 14, flexWrap: 'wrap' }}>
              <button style={styles.whiteBtn} onClick={() => setInputEditorFields((prev) => [...prev, makeEmptyInputField()])}>新增输入字段</button>
              <button style={styles.blueBtn} onClick={saveInputEditor}>保存输入配置</button>
              <button style={styles.whiteBtn} onClick={() => setInputEditorOpen(false)}>取消</button>
            </div>
          </div>
        </SimpleOverlay>
      )}

      {showDropHint && (
        <SimpleOverlay
          title="可执行模块目录投放说明"
          onClose={() => setShowDropHint(false)}
          width="min(820px, 96vw)"
        >
          <div style={{ lineHeight: 1.9, color: '#173353' }}>
            <p>这里用于本地可执行模块投放。zip 内部建议包含 executable_module.json、config.json、可执行程序、固定资源 resources，以及可选的运行时依赖 deps。输入方式与 Python 模块一致：系统从 config.json 识别输入/输出表单，运行时传入平台生成的 config.json。</p>
            <p>
              当前后端会自动创建并扫描本地投放目录：
              <code>{dropInfo.drop_dir || '项目根目录/module_drop'}</code>
            </p>
            <ol>
              <li>在 executable_module.json 里填写 tool_type，例如 cloud 或 aerosol。</li>
              <li>把可执行模块 zip 直接放进这个目录，不需要在网页里选择文件。</li>
              <li>点击“扫描本地目录安装”，后端会先校验 executable_module.json/config.json 和缺失文件，再安装通过的 zip。</li>
              <li>系统会把 dependency_dirs、dependency_search_dirs 和 runtime_env_path 加入运行 PATH，并可尝试识别 DLL。</li>
            </ol>
            <p>注意：deps 是运行时依赖目录，只放 exe 运行时缺少的 DLL。runtime_env_path 用来填写本机运行环境路径，例如 MATLAB Runtime 或其它运行库目录。</p>
          </div>
        </SimpleOverlay>
      )}
    </div>
  );
}

const thStyle = {
  textAlign: 'left',
  padding: '9px 10px',
  color: '#49627f',
  fontSize: 12,
  fontWeight: 700,
  lineHeight: 1.35,
  borderBottom: '1px solid #dfe8f2',
  background: '#f3f7fb',
  whiteSpace: 'nowrap',
};

const tdStyle = {
  padding: '9px 10px',
  borderBottom: '1px solid #edf2f7',
  color: '#233b56',
  fontSize: 12,
  lineHeight: 1.45,
  verticalAlign: 'middle',
};

const tdEllipsisStyle = {
  ...tdStyle,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
};

const tableActionBtnStyle = {
  ...styles.whiteBtn,
  padding: '6px 10px',
  fontSize: 12,
  borderRadius: 8,
  minWidth: 0,
  whiteSpace: 'nowrap',
};

const tableDangerBtnStyle = {
  ...styles.redBtn,
  padding: '6px 10px',
  fontSize: 12,
  borderRadius: 8,
  minWidth: 0,
  whiteSpace: 'nowrap',
};
const taskThStyle = {
  ...thStyle,
  padding: '12px 12px',
  fontSize: 14,
  fontWeight: 800,
  color: '#24486d',
};

const taskTdStyle = {
  ...tdStyle,
  padding: '12px 12px',
  fontSize: 14,
  lineHeight: 1.55,
  color: '#16385c',
};

const taskTdEllipsisStyle = {
  ...taskTdStyle,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
};

const taskTableActionBtnStyle = {
  ...tableActionBtnStyle,
  padding: '7px 12px',
  fontSize: 13,
  borderRadius: 8,
};

const taskTableDangerBtnStyle = {
  ...tableDangerBtnStyle,
  padding: '7px 12px',
  fontSize: 13,
  borderRadius: 8,
};
export default App;
