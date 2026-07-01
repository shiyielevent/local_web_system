const API_BASE = '';


function formatApiErrorDetail(detail, fallback) {
  if (detail == null || detail === '') return fallback;
  if (typeof detail === 'string') return detail;

  if (typeof detail === 'object') {
    const parts = [];
    if (detail.message) parts.push(String(detail.message));

    if (Array.isArray(detail.errors) && detail.errors.length) {
      parts.push('错误：');
      detail.errors.slice(0, 30).forEach((item, idx) => {
        if (typeof item === 'object' && item) {
          parts.push(`${idx + 1}. ${item.field || '-'}：${item.message || ''}${item.suggestion ? `；建议：${item.suggestion}` : ''}`);
        } else {
          parts.push(`${idx + 1}. ${String(item)}`);
        }
      });
      if (detail.errors.length > 30) parts.push(`... 还有 ${detail.errors.length - 30} 项错误`);
    }

    if (Array.isArray(detail.warnings) && detail.warnings.length) {
      parts.push('警告：');
      detail.warnings.slice(0, 30).forEach((item, idx) => {
        if (typeof item === 'object' && item) {
          parts.push(`${idx + 1}. ${item.field || '-'}：${item.message || ''}${item.suggestion ? `；建议：${item.suggestion}` : ''}`);
        } else {
          parts.push(`${idx + 1}. ${String(item)}`);
        }
      });
      if (detail.warnings.length > 30) parts.push(`... 还有 ${detail.warnings.length - 30} 项警告`);
    }

    if (Array.isArray(detail.missing_files) && detail.missing_files.length) {
      parts.push('缺少文件/文件夹：');
      detail.missing_files.slice(0, 50).forEach((item, idx) => {
        if (typeof item === 'object' && item) {
          parts.push(`${idx + 1}. ${item.path || '-'}${item.reason ? `：${item.reason}` : ''}${item.suggestion ? `；建议：${item.suggestion}` : ''}`);
        } else {
          parts.push(`${idx + 1}. ${String(item)}`);
        }
      });
      if (detail.missing_files.length > 50) parts.push(`... 还有 ${detail.missing_files.length - 50} 项缺失`);
    }

    if (Array.isArray(detail.suggestions) && detail.suggestions.length) {
      parts.push('修改建议：');
      detail.suggestions.slice(0, 30).forEach((item, idx) => {
        parts.push(`${idx + 1}. ${String(item)}`);
      });
      if (detail.suggestions.length > 30) parts.push(`... 还有 ${detail.suggestions.length - 30} 条建议`);
    }

    if (detail.dependency_report && typeof detail.dependency_report === 'object') {
      const dep = detail.dependency_report;
      if (dep.message) parts.push(`依赖收集：${dep.message}`);
      if (Array.isArray(dep.copied) && dep.copied.length) {
        parts.push(`已自动收集依赖：${dep.copied.slice(0, 20).join(', ')}`);
      }
      if (Array.isArray(dep.missing_imports) && dep.missing_imports.length) {
        parts.push(`未找到的运行依赖：${dep.missing_imports.slice(0, 30).join(', ')}`);
      }
    }

    if (Array.isArray(detail.missing) && detail.missing.length) {
      parts.push('缺失匹配：');
      detail.missing.slice(0, 20).forEach((item, idx) => {
        if (typeof item === 'object' && item) {
          parts.push(
            `${idx + 1}. slot=${item.slot || '-'} role=${item.role || '-'} expected_from=${item.expected_from || '-'}`
          );
        } else {
          parts.push(`${idx + 1}. ${String(item)}`);
        }
      });
      if (detail.missing.length > 20) parts.push(`... 还有 ${detail.missing.length - 20} 项`);
    }

    if (Array.isArray(detail.extras) && detail.extras.length) {
      parts.push('未使用文件：');
      detail.extras.slice(0, 10).forEach((item, idx) => {
        if (typeof item === 'object' && item) {
          const files = Array.isArray(item.files) ? item.files.slice(0, 5).join(', ') : '';
          parts.push(`${idx + 1}. role=${item.role || '-'} files=${files}`);
        } else {
          parts.push(`${idx + 1}. ${String(item)}`);
        }
      });
      if (detail.extras.length > 10) parts.push(`... 还有 ${detail.extras.length - 10} 项`);
    }

    if (parts.length) return parts.join('\n');

    try {
      return JSON.stringify(detail, null, 2);
    } catch {
      return String(detail);
    }
  }

  return String(detail);
}

function makeApiError(message, status, detail) {
  const err = new Error(message);
  err.status = status || 0;
  err.detail = detail;
  return err;
}

function isLikelyNetworkError(error) {
  const text = String(error?.message || error || '').toLowerCase();
  return text.includes('failed to fetch') || text.includes('networkerror') || text.includes('load failed');
}

function getToken() {
  return localStorage.getItem('token') || '';
}

export function setAuthToken(token) {
  localStorage.setItem('token', token);
}

export function clearAuthToken() {
  localStorage.removeItem('token');
}

export function getAuthToken() {
  return getToken();
}


async function requestBlob(url, options = {}) {
  const headers = {
    ...(options.headers || {}),
  };

  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  if (options.body && !(options.body instanceof FormData) && !(options.body instanceof Blob)) {
    headers['Content-Type'] = 'application/json';
  }

  let resp;
  try {
    resp = await fetch(`${API_BASE}${url}`, {
      ...options,
      headers,
    });
  } catch (error) {
    const baseMsg = isLikelyNetworkError(error)
      ? '请求没有到达后端：请确认后端服务正在运行、前端代理正确，并且已经重启加载新版 main.py。原始错误：Failed to fetch'
      : `网络请求失败：${error?.message || error}`;
    throw makeApiError(baseMsg, 0, null);
  }

  if (!resp.ok) {
    let msg = `请求失败: ${resp.status}`;
    let detail = null;
    try {
      const data = await resp.json();
      detail = data.detail ?? data;
      msg = formatApiErrorDetail(detail, msg);
    } catch {}
    throw makeApiError(msg, resp.status, detail);
  }

  return resp.blob();
}

async function request(url, options = {}) {
  const headers = {
    ...(options.headers || {}),
  };

  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  let resp;
  try {
    resp = await fetch(`${API_BASE}${url}`, {
      ...options,
      headers,
    });
  } catch (error) {
    const baseMsg = isLikelyNetworkError(error)
      ? '请求没有到达后端：请确认后端服务正在运行、前端代理正确，并且已经重启加载新版 main.py。原始错误：Failed to fetch'
      : `网络请求失败：${error?.message || error}`;
    throw makeApiError(baseMsg, 0, null);
  }

  if (!resp.ok) {
    let msg = `请求失败: ${resp.status}`;
    let detail = null;
    try {
      const data = await resp.json();
      detail = data.detail ?? data;
      msg = formatApiErrorDetail(detail, msg);
    } catch {}
    throw makeApiError(msg, resp.status, detail);
  }

  const contentType = resp.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return resp.json();
  }
  return resp.text();
}


export async function login(username, password, role) {
  return request('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password, role }),
  });
}

export async function registerUser(payload) {
  return request('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function getForgotPasswordQuestion(username) {
  return request(`/api/auth/forgot-password/question?username=${encodeURIComponent(username)}`);
}

export async function resetForgotPassword(payload) {
  return request('/api/auth/forgot-password/reset', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function logout() {
  return request('/api/auth/logout', { method: 'POST' });
}

export async function getMe() {
  return request('/api/auth/me');
}

export async function getUsers() {
  return request('/api/admin/users');
}

export async function addUser(payload) {
  return request('/api/admin/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function deleteUser(username) {
  return request(`/api/admin/users/${encodeURIComponent(username)}`, {
    method: 'DELETE',
  });
}

export async function updateUserRole(username, role) {
  return request(`/api/admin/users/${encodeURIComponent(username)}/role`, {
    method: 'PUT',
    body: JSON.stringify({ role }),
  });
}

export async function updateUserEnabled(username, enabled) {
  return request(`/api/admin/users/${encodeURIComponent(username)}/enabled`, {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  });
}

export async function adminResetPassword(username, new_password) {
  return request(`/api/admin/users/${encodeURIComponent(username)}/password`, {
    method: 'PUT',
    body: JSON.stringify({ new_password }),
  });
}

export async function getModules() {
  return request('/api/modules');
}

export async function getAdminModules() {
  return request('/api/admin/modules');
}

export async function getToolbars() {
  return request('/api/toolbars');
}

export async function addToolbar(payload) {
  return request('/api/admin/toolbars', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateToolbar(toolbarKey, payload) {
  return request(`/api/admin/toolbars/${encodeURIComponent(toolbarKey)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function deleteToolbar(toolbarKey) {
  // 使用 POST 删除，兼容部分本地服务/代理环境对 DELETE 方法的限制。
  return request(`/api/admin/toolbars/${encodeURIComponent(toolbarKey)}/delete`, {
    method: 'POST',
  });
}

export async function saveModule(payload) {
  return request('/api/admin/modules', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function deleteModule(moduleId) {
  return request(`/api/admin/modules/${encodeURIComponent(moduleId)}`, {
    method: 'DELETE',
  });
}

export async function uploadModuleZip(file, toolType = 'cloud') {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('tool_type', toolType);
  return request('/api/admin/modules/upload', {
    method: 'POST',
    body: fd,
  });
}

export async function listDropZips() {
  return request('/api/admin/modules/drop-zips');
}

export async function installLocalDropModules(toolType = 'cloud', filename = '') {
  return request('/api/admin/modules/install-local-drop', {
    method: 'POST',
    body: JSON.stringify({ tool_type: toolType, filename }),
  });
}

export async function getSystemResources() {
  return request('/api/system/resources');
}

export async function getTasks() {
  return request('/api/tasks');
}

export async function getTask(taskId) {
  return request(`/api/tasks/${taskId}`);
}

export async function runModule(moduleId, inputs, parallelWorkers = 1) {
  return request('/api/tasks/run', {
    method: 'POST',
    body: JSON.stringify({
      module_id: moduleId,
      inputs,
      parallel_workers: Number(parallelWorkers || 1),
    }),
  });
}

export async function cancelTask(taskId) {
  return request(`/api/tasks/${taskId}/cancel`, {
    method: 'POST',
  });
}

export async function deleteTask(taskId) {
  return request(`/api/tasks/${taskId}`, {
    method: 'DELETE',
  });
}

/* 下面三个如果你本地浏览按钮已经接好，就保留；如果后端没有这些接口，可以继续用你原来的 */
export async function chooseLocalFile() {
  return request('/api/local/file', { method: 'POST' });
}

export async function chooseLocalDir() {
  return request('/api/local/dir', { method: 'POST' });
}

export async function chooseSaveFile() {
  return request('/api/local/save-file', { method: 'POST' });
}

export async function listUserFiles() {
  return request('/api/files');
}

export async function uploadUserFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  return request('/api/files/upload', {
    method: 'POST',
    body: fd,
  });
}

export async function deleteUserFile(filename) {
  return request(`/api/files/${encodeURIComponent(filename)}`, {
    method: 'DELETE',
  });
}

export async function getUserFilePreviewData(filename) {
  return request(`/api/files/${encodeURIComponent(filename)}/preview`);
}

// 兼容旧调用名：现在返回预览 JSON，不再返回本地路径 blob URL。
export async function getUserFilePreviewUrl(filename) {
  return getUserFilePreviewData(filename);
}

export async function listDataFiles() {
  return request('/api/data/files');
}

export async function previewDataFile(fileId) {
  return request(`/api/data/files/${encodeURIComponent(fileId)}/preview`);
}

export async function revealDataFile(fileId) {
  return request(`/api/data/files/${encodeURIComponent(fileId)}/reveal`, {
    method: 'POST',
  });
}

export async function deleteDataFile(fileId) {
  return request(`/api/data/files/${encodeURIComponent(fileId)}`, {
    method: 'DELETE',
  });
}
export async function uploadPythonModule(file, options) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('module_id', options.module_id || '');
  fd.append('module_name', options.module_name || '');
  fd.append('entry_file', options.entry_file || 'main.py');

  if (options.tool_type) {
    fd.append('tool_type', options.tool_type);
  }

  return request('/api/admin/modules/upload-python', {
    method: 'POST',
    body: fd,
  });
}
export async function parseModuleParamJson(path) {
  return request('/api/admin/modules/parse-param-json', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });
}

export async function validatePythonModuleFolder(folderPath) {
  return request('/api/admin/modules/validate-python-folder', {
    method: 'POST',
    body: JSON.stringify({
      folder_path: folderPath || '',
      config_filename: 'python_module.json',
    }),
  });
}

export async function uploadPythonFolderModule(folderPath) {
  return request('/api/admin/modules/upload-python-folder', {
    method: 'POST',
    body: JSON.stringify({
      folder_path: folderPath || '',
      config_filename: 'python_module.json',
    }),
  });
}

export async function uploadModuleFolder(payload) {
  return request('/api/admin/modules/install-folder', {
    method: 'POST',
    body: JSON.stringify({
      folder_path: payload.folder_path || '',
      tool_type: payload.tool_type || '',
      runtime: payload.runtime || 'cpp_native',
      auto_collect_dependencies: payload.auto_collect_dependencies !== false,
    }),
  });
}

export async function validateCppModuleFolder(payload) {
  return request('/api/admin/modules/validate-cpp-folder', {
    method: 'POST',
    body: JSON.stringify({
      folder_path: payload.folder_path || '',
      tool_type: payload.tool_type || '',
      auto_collect_dependencies: payload.auto_collect_dependencies !== false,
    }),
  });
}

export async function validatePythonModuleConfig(path) {
  return request('/api/admin/modules/validate-python-module-config', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });
}

export async function parsePythonModuleConfig(path) {
  return request('/api/admin/modules/parse-python-module-config', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });
}

export async function uploadPythonModuleConfig(path) {
  return request('/api/admin/modules/upload-python-config', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });
}

// =========================
// Dask 分布式集群
// =========================
export async function getDistributedStatus() {
  return request('/api/distributed/status');
}

export async function installDaskRuntime(payload = {}) {
  return request('/api/distributed/install', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function openDaskFirewall(payload = {}) {
  return request('/api/distributed/firewall', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function startDaskHead(payload) {
  return request('/api/distributed/start-head', {
    method: 'POST',
    body: JSON.stringify(payload || {}),
  });
}

export async function joinDaskCluster(payload) {
  return request('/api/distributed/join', {
    method: 'POST',
    body: JSON.stringify(payload || {}),
  });
}

export async function leaveDaskCluster() {
  return request('/api/distributed/leave', {
    method: 'POST',
  });
}

export async function stopDaskCluster() {
  return request('/api/distributed/stop', {
    method: 'POST',
  });
}

export async function setDistributedExecutionMode(mode, sharedRuntimeRoot = '') {
  return request('/api/distributed/execution-mode', {
    method: 'POST',
    body: JSON.stringify({
      mode,
      shared_runtime_root: sharedRuntimeRoot || '',
    }),
  });
}

export async function testDaskSharedPath(path) {
  return request('/api/distributed/test-shared-path', {
    method: 'POST',
    body: JSON.stringify({ path: path || '' }),
  });
}

export async function getDaskLogs() {
  return request('/api/distributed/logs');
}

export async function getHTCondorStatus() {
  return request('/api/htcondor/status');
}

export async function setHTCondorExecutionMode(mode) {
  return request('/api/htcondor/execution-mode', {
    method: 'POST',
    body: JSON.stringify({ mode }),
  });
}

export async function runHTCondorSmokeTest() {
  return request('/api/htcondor/smoke-test', {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export async function getHTCondorLogs() {
  return request('/api/htcondor/logs');
}

export async function getHTCondorNodes() {
  return request('/api/htcondor/nodes');
}

export async function createHTCondorParent(payload = {}) {
  return request('/api/htcondor/create-parent', {
    method: 'POST',
    body: JSON.stringify(payload || {}),
  });
}

export async function joinHTCondorParent(payload = {}) {
  return request('/api/htcondor/join-parent', {
    method: 'POST',
    body: JSON.stringify(payload || {}),
  });
}

export async function leaveHTCondorPool() {
  return request('/api/htcondor/leave-pool', {
    method: 'POST',
    body: JSON.stringify({}),
  });
}
