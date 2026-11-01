import {
  $,
  el,
  badge,
  button,
  databaseNames,
  problemNames,
  descriptions,
  terminal,
  time,
  renderReport,
  renderEvidence,
  renderFeedback,
  showEvidence,
  renderTaskHistory,
} from "./ui.js";

const state = {
  token: "",
  auth: 0,
  generation: 0,
  instances: [],
  capabilities: null,
  taskId: null,
  detail: null,
  evidence: [],
  sessions: [],
  creating: false,
  feedbackBusy: false,
};
const requests = new Set();
const taskHistory = {
  generation: 0,
  filters: null,
  cursors: [null],
  page: 0,
  next: null,
  busy: false,
};
let pollTimer;
let pollingSince = 0;
const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}
function notice(message, success = false) {
  $("notice").textContent = message;
  $("notice").className = `notice${success ? " success" : ""}`;
  $("notice").hidden = !message;
  $("create-notice").textContent = message;
  $("create-notice").hidden = !message;
}
const tabNames = ["report", "evidence", "feedback"];
function selectTab(name, focus = false) {
  for (const item of tabNames) {
    const selected = item === name;
    $(`tab-${item}`).setAttribute("aria-selected", String(selected));
    $(`tab-${item}`).tabIndex = selected ? 0 : -1;
    $(`panel-${item}`).hidden = !selected;
  }
  if (focus) $(`tab-${name}`).focus();
}
function showCreate() {
  notice("");
  $("create-dialog").showModal();
  $("problem").focus();
}
function clearTask() {
  clearTimeout(pollTimer);
  state.generation += 1;
  state.taskId = null;
  state.detail = null;
  state.evidence = [];
  $("evidence-dialog").close();
  selectTab("report");
  $("tab-evidence-count").textContent = "0";
  $("tab-feedback-count").textContent = "0";
  for (const id of [
    "report-content",
    "steps",
    "evidence-list",
    "feedback-list",
    "evidence-content",
    "task-instance",
    "task-title",
    "task-description",
    "current-task-id",
    "task-times",
    "poll-status",
    "task-status",
    "report-status",
    "evidence-count",
    "evidence-title",
  ])
    $(id).replaceChildren();
  $("feedback-form").reset();
  $("feedback-fields").disabled = true;
  $("task-view").hidden = true;
  $("return-history").hidden = true;
  $("empty-state").hidden = false;
}
function logout(message = "") {
  state.auth += 1;
  state.token = "";
  requests.forEach((controller) => controller.abort());
  clearTask();
  leaveHistory();
  taskHistory.filters = null;
  taskHistory.cursors = [null];
  taskHistory.page = 0;
  taskHistory.next = null;
  $("history-form").reset();
  for (const id of [
    "history-rows",
    "history-message",
    "history-page",
    "history-instance-options",
  ])
    $(id).replaceChildren();
  $("create-dialog").close();
  state.instances = [];
  state.capabilities = null;
  state.sessions = [];
  state.creating = false;
  state.feedbackBusy = false;
  for (const id of [
    "instance-list",
    "session-list",
    "instance",
    "problem",
    "tool-list",
    "backend-note",
    "problem-description",
    "instance-count",
    "overview-name",
    "overview-id",
    "overview-status",
    "overview-description",
    "overview-problems",
    "overview-meta",
  ])
    $(id).replaceChildren();
  $("session-count").textContent = "0";
  $("login-form").reset();
  $("diagnosis-form").reset();
  $("lookup-form").reset();
  $("workspace").hidden = true;
  $("login-view").hidden = false;
  $("login-button").disabled = false;
  $("create-button").textContent = "开始诊断 →";
  $("login-error").textContent = message;
  history.replaceState(null, "", location.pathname);
  notice("");
  $("token").focus();
}
function errorMessage(error) {
  return error instanceof Error ? error.message : "请求失败，请刷新后重试。";
}
function showError(error) {
  if (error.name === "AbortError") return;
  if (state.token) notice(errorMessage(error));
  else $("login-error").textContent = errorMessage(error);
}
async function api(path, { method = "GET", body } = {}) {
  const generation = state.auth;
  const controller = new AbortController();
  requests.add(controller);
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 15000);
  try {
    const response = await fetch(path, {
      method,
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      signal: controller.signal,
      headers: {
        Authorization: `Bearer ${state.token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (generation !== state.auth)
      throw new DOMException("Session changed", "AbortError");
    if (!response.ok) {
      const messages = {
        401: "令牌无效或已更换，请重新输入。",
        403: "该实例当前不允许诊断，请刷新实例能力。",
        404: "未找到任务，请检查任务 ID。",
        409: "任务状态不允许此操作，请刷新任务。",
        422: "请求未通过校验，请检查实例、问题和时间窗口。",
        429: "任务容量已满，请稍后再试。",
        500: "服务内部错误，请稍后刷新。",
        503: "服务依赖暂不可用，请检查服务就绪状态。",
      };
      const message =
        messages[response.status] || `请求失败（HTTP ${response.status}）。`;
      const requestId = response.headers.get("X-Request-ID");
      if (response.status === 401) logout(message);
      throw new ApiError(
        message + (requestId ? ` 请求 ID：${requestId}` : ""),
        response.status,
      );
    }
    const data = await response.json();
    if (generation !== state.auth)
      throw new DOMException("Session changed", "AbortError");
    return data;
  } catch (error) {
    if (generation !== state.auth && error.status !== 401)
      throw new DOMException("Session changed", "AbortError");
    if (error instanceof ApiError) throw error;
    if (error.name === "AbortError" && !timedOut) throw error;
    throw new ApiError(
      timedOut
        ? "请求超时，请检查服务后手动刷新。"
        : "无法读取服务响应，请检查连接后手动刷新。",
    );
  } finally {
    clearTimeout(timeout);
    requests.delete(controller);
  }
}
function selectedInstance() {
  return state.instances.find((item) => item.id === $("instance").value);
}
function updateInstance() {
  const instance = selectedInstance();
  const previous = $("problem").value;
  $("problem").replaceChildren();
  (instance?.problem_types || []).forEach((problem) => {
    const option = el("option", problemNames[problem] || problem);
    option.value = problem;
    $("problem").append(option);
  });
  if (instance?.problem_types.includes(previous)) $("problem").value = previous;
  $("problem-description").textContent =
    descriptions[$("problem").value] || "此实例当前未开放诊断。";
  $("history-controls").hidden = !instance?.supports_history;
  $("use-window").checked = false;
  $("window-fields").hidden = true;
  $("create-button").disabled = state.creating || !instance?.enabled;
  $("new-diagnosis").disabled = state.creating || !instance?.enabled;
  $("tool-list").replaceChildren();
  const capability = state.capabilities?.databases.find(
    (item) => item.database_type === instance?.database_type,
  );
  (capability?.tools || []).forEach((tool) =>
    $("tool-list").append(el("li", tool)),
  );
  if (!capability?.tools.length)
    $("tool-list").append(el("li", "暂无已启用工具"));
  $("backend-note").textContent = instance?.supports_history
    ? `历史指标：${state.capabilities.historical_backends.metrics === "null" ? "未配置" : "已配置"} · 事件检索：${state.capabilities.historical_backends.search === "null" ? "未配置" : "已配置"}。实际可用性以证据为准。`
    : "本类调查使用当前状态与有限保留样本。";
  for (const node of $("instance-list").children)
    node.setAttribute(
      "aria-current",
      String(
        $("history-view").hidden && node.dataset.instance === instance?.id,
      ),
    );
  renderOverview(instance);
}
function renderOverview(instance) {
  $("overview-name").textContent =
    databaseNames[instance?.database_type] || "暂无实例";
  $("overview-id").textContent = instance?.id || "";
  const status = el(
    "span",
    instance?.enabled ? "已启用诊断" : "未启用",
    "badge neutral",
  );
  $("overview-status").replaceChildren(status);
  $("overview-description").textContent = instance?.enabled
    ? "从一个具体问题开始。检查将采集只读证据，并明确标出未覆盖的部分。"
    : "此实例尚未开放诊断。启用后可查看支持的问题与检查能力。";
  $("overview-problems").replaceChildren();
  for (const problem of instance?.problem_types || []) {
    const choice = button(
      "",
      () => {
        $("problem").value = problem;
        $("problem-description").textContent = descriptions[problem] || "";
        showCreate();
      },
      "problem-choice",
    );
    const info = el("span");
    info.append(
      el("strong", problemNames[problem] || problem),
      el("small", descriptions[problem] || ""),
    );
    const arrow = el("span", "→", "problem-arrow");
    arrow.setAttribute("aria-hidden", "true");
    choice.append(info, arrow);
    choice.disabled = state.creating;
    $("overview-problems").append(choice);
  }
  if (!instance?.problem_types.length)
    $("overview-problems").append(el("p", "暂无可用调查", "field-hint"));
  $("overview-meta").replaceChildren();
  for (const [key, value] of [
    ["环境", instance?.environment || "—"],
    ["数据库版本", instance?.version || "—"],
    [
      "历史窗口",
      instance?.supports_history ? "可选关联" : "当前快照 / 保留样本",
    ],
    ["执行方式", "预定义只读检查"],
  ])
    $("overview-meta").append(el("dt", key), el("dd", value));
}
function renderInstances(previous) {
  $("instance-list").replaceChildren();
  $("instance").replaceChildren();
  state.instances.forEach((instance) => {
    const name =
      databaseNames[instance.database_type] || instance.database_type;
    const option = el(
      "option",
      `${instance.id}${instance.enabled ? "" : "（未启用）"}`,
    );
    option.value = instance.id;
    $("instance").append(option);
    const node = button(
      "",
      () => {
        leaveHistory();
        clearTask();
        notice("");
        history.replaceState(null, "", location.pathname);
        $("instance").value = instance.id;
        updateInstance();
        renderSessions();
      },
      "instance-button",
    );
    node.dataset.instance = instance.id;
    const caption = el("span");
    caption.append(
      el("strong", name),
      el(
        "small",
        `${instance.version} · ${instance.enabled ? "已启用" : "未启用"}`,
      ),
    );
    node.append(
      el("span", name.slice(0, 2).toUpperCase(), "instance-icon"),
      caption,
      el("span", "", `instance-dot${instance.enabled ? "" : " off"}`),
    );
    node.setAttribute(
      "aria-label",
      `${name} ${instance.id}${instance.enabled ? "" : " 未启用"}`,
    );
    $("instance-list").append(node);
  });
  $("instance-count").textContent =
    `${state.instances.filter((item) => item.enabled).length} / ${state.instances.length}`;
  $("instance").value = state.instances.some((item) => item.id === previous)
    ? previous
    : state.instances.find((item) => item.enabled)?.id ||
      state.instances[0]?.id ||
      "";
  updateInstance();
}
async function loadCapabilities() {
  const previous = $("instance").value;
  const [instances, capabilities] = await Promise.all([
    api("/api/v1/instances"),
    api("/api/v1/capabilities"),
  ]);
  state.instances = instances;
  state.capabilities = capabilities;
  renderInstances(previous);
  $("history-instance-options").replaceChildren();
  for (const instance of instances) {
    const option = el("option");
    option.value = instance.id;
    option.label = `${databaseNames[instance.database_type]}${instance.enabled ? "" : "（未启用，历史可读）"}`;
    $("history-instance-options").append(option);
  }
}

function leaveHistory() {
  taskHistory.generation++;
  taskHistory.busy = false;
  $("history-view").hidden = true;
  $("history-nav").setAttribute("aria-current", "false");
}
function historyFilters() {
  const filters = { limit: $("history-limit").value };
  if ($("history-instance").value.trim())
    filters.instance_id = $("history-instance").value.trim();
  if ($("history-status").value) filters.status = $("history-status").value;
  for (const [id, key] of [
    ["history-from", "created_from"],
    ["history-until", "created_until"],
  ]) {
    if ($(id).value) {
      const date = new Date($(id).value);
      if (!Number.isFinite(date.getTime()))
        throw new ApiError("请填写有效的任务创建时间。");
      filters[key] = date.toISOString();
    }
  }
  if (
    filters.created_from &&
    filters.created_until &&
    filters.created_until <= filters.created_from
  )
    throw new ApiError("创建终点必须晚于创建起点。筛选的是任务创建时间。");
  return filters;
}
function historyButtons() {
  $("history-prev").disabled = taskHistory.busy || taskHistory.page === 0;
  $("history-next").disabled = taskHistory.busy || !taskHistory.next;
}
async function loadHistoryPage(index = taskHistory.page) {
  const generation = ++taskHistory.generation;
  const auth = state.auth;
  taskHistory.page = index;
  taskHistory.busy = true;
  taskHistory.next = null;
  historyButtons();
  $("history-view").setAttribute("aria-busy", "true");
  $("history-table-wrap").hidden = true;
  $("history-rows").replaceChildren();
  $("history-page").textContent = "";
  $("history-message").textContent = "正在读取服务器任务历史…";
  const params = new URLSearchParams(taskHistory.filters);
  if (taskHistory.cursors[index])
    params.set("cursor", taskHistory.cursors[index]);
  const current = () =>
    state.auth === auth &&
    generation === taskHistory.generation &&
    !$("history-view").hidden;
  try {
    const page = await api(`/api/v1/diagnoses?${params}`);
    if (!current()) return;
    taskHistory.next = page.next_cursor;
    renderTaskHistory(page.items, (id) => openTask(id).catch(showError));
    $("history-table-wrap").hidden = !page.items.length;
    $("history-message").textContent = page.items.length
      ? ""
      : "没有符合条件的任务。可清除筛选或发起一次新诊断。";
    $("history-page").textContent =
      `第 ${index + 1} 页 · 本页 ${page.items.length} 条`;
  } catch (error) {
    if (!current() || error.name === "AbortError") return;
    $("history-message").textContent =
      `${errorMessage(error)} 点击“刷新列表”可重新读取。`;
  } finally {
    if (current()) {
      taskHistory.busy = false;
      $("history-view").setAttribute("aria-busy", "false");
      historyButtons();
    }
  }
}
function applyHistoryFilters() {
  taskHistory.generation++;
  taskHistory.busy = false;
  taskHistory.next = null;
  $("history-view").setAttribute("aria-busy", "false");
  $("history-table-wrap").hidden = true;
  $("history-rows").replaceChildren();
  $("history-page").textContent = "";
  taskHistory.cursors = [null];
  taskHistory.page = 0;
  historyButtons();
  try {
    taskHistory.filters = historyFilters();
  } catch (error) {
    taskHistory.filters = null;
    $("history-message").textContent = errorMessage(error);
    return;
  }
  void loadHistoryPage(0);
}
function openHistory() {
  clearTask();
  $("create-dialog").close();
  notice("");
  $("empty-state").hidden = true;
  $("history-view").hidden = false;
  $("history-nav").setAttribute("aria-current", "page");
  for (const node of $("instance-list").children)
    node.setAttribute("aria-current", "false");
  renderSessions();
  history.replaceState(null, "", "#history");
  $("history-title").focus({ preventScroll: true });
  if (taskHistory.filters) void loadHistoryPage();
  else applyHistoryFilters();
}
async function checkReadiness() {
  try {
    await api("/readyz");
    $("service-status").textContent = "服务已就绪";
    $("service-status").className = "service-status";
  } catch (error) {
    if (error.name === "AbortError" || !state.token) return;
    $("service-status").textContent = "服务暂未就绪";
    $("service-status").className = "service-status warn";
    notice("服务暂未就绪，任务读取或提交可能失败。可点击刷新能力重新检查。");
  }
}
function renderSessions() {
  $("session-count").textContent = String(state.sessions.length);
  $("session-list").replaceChildren();
  if (!state.sessions.length)
    $("session-list").append(el("p", "还没有打开的调查", "session-empty"));
  state.sessions.forEach((item) => {
    const node = button(
      problemNames[item.problem] || item.problem,
      () => openTask(item.id).catch(showError),
      "session-button",
    );
    node.append(el("small", `${item.instance} · ${item.id.slice(0, 8)}`));
    node.setAttribute("aria-current", String(item.id === state.taskId));
    $("session-list").append(node);
  });
}
function openEvidence(id) {
  const evidence = state.evidence.find((item) => item.evidence_id === id);
  if (evidence) showEvidence(evidence);
}
async function refreshTask(generation = state.generation) {
  const id = state.taskId;
  if (!id || !state.token) return;
  clearTimeout(pollTimer);
  const detail = await api(`/api/v1/diagnoses/${id}`);
  if (generation !== state.generation) return;
  const evidence = await api(`/api/v1/diagnoses/${id}/evidence`);
  if (generation !== state.generation) return;
  if (
    !state.detail &&
    state.instances.some((item) => item.id === detail.task.instance_id)
  ) {
    $("instance").value = detail.task.instance_id;
    updateInstance();
  }
  state.detail = detail;
  state.evidence = evidence;
  const task = detail.task;
  $("task-instance").textContent = task.instance_id;
  $("task-title").textContent =
    problemNames[task.problem_type] || task.problem_type;
  $("task-status").replaceChildren(badge(task.status));
  $("task-status").className = "";
  const statusText = {
    completed: "检查已结束；是否存在问题请以报告结论为准。",
    partial: "部分检查存在缺失或覆盖限制，请结合证据判断。",
    failed: "任务未能完成，请查看已归档步骤与错误信息。",
    cancelled: "任务已取消，已采集证据仍可查看。",
    queued: "任务已接收，正在等待执行。",
    running: "正在采集并归档证据，页面将自动更新。",
  };
  $("task-description").textContent =
    (statusText[task.status] || task.status) +
    (task.error_code ? ` 错误：${task.error_code}` : "");
  $("current-task-id").textContent = id;
  $("task-times").textContent =
    `创建 ${time(task.created_at)}${task.finished_at ? ` · 结束 ${time(task.finished_at)}` : ""}${task.window ? ` · 历史 ${time(task.window.start)} — ${time(task.window.end)}` : " · 当前视角"}`;
  renderReport(detail, evidence, openEvidence);
  renderEvidence(detail, evidence, openEvidence);
  renderFeedback(detail.feedback);
  $("tab-evidence-count").textContent = String(evidence.length);
  $("tab-feedback-count").textContent = String(detail.feedback.length);
  $("feedback-fields").disabled = !terminal(task.status) || state.feedbackBusy;
  if (!state.sessions.some((item) => item.id === id))
    state.sessions.unshift({
      id,
      problem: task.problem_type,
      instance: task.instance_id,
    });
  state.sessions = state.sessions.slice(0, 20);
  renderSessions();
  if (terminal(task.status))
    $("poll-status").textContent =
      "报告已归档。可保存地址栏中的任务链接，重新登录后查看。";
  else if (Date.now() - pollingSince > 120000)
    $("poll-status").textContent =
      "等待已超过两分钟，自动刷新已暂停。任务状态未改变，可手动刷新。";
  else {
    $("poll-status").textContent =
      "每 2 秒刷新；执行过程不会因关闭页面而由浏览器取消。";
    pollTimer = setTimeout(
      () =>
        refreshTask(generation).catch((error) => {
          if (generation !== state.generation) return;
          $("poll-status").textContent = "自动刷新已暂停，请手动刷新任务。";
          showError(error);
        }),
      2000,
    );
  }
}
async function openTask(id) {
  if (!uuidPattern.test(id))
    throw new ApiError("请输入完整、有效的任务 UUID。");
  const fromHistory = !$("history-view").hidden;
  leaveHistory();
  clearTask();
  $("return-history").hidden = !fromHistory;
  notice("");
  state.taskId = id.toLowerCase();
  pollingSince = Date.now();
  history.replaceState(null, "", `#task/${state.taskId}`);
  $("empty-state").hidden = true;
  $("task-view").hidden = false;
  $("task-title").textContent = "正在读取任务";
  $("current-task-id").textContent = state.taskId;
  $("feedback-fields").disabled = true;
  $("create-dialog").close();
  $("task-title").tabIndex = -1;
  $("task-title").focus({ preventScroll: true });
  renderSessions();
  const generation = state.generation;
  try {
    await refreshTask(generation);
  } catch (error) {
    if (generation === state.generation) {
      $("poll-status").textContent = "读取未完成，可手动刷新。";
      throw error;
    }
  }
}
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-button").disabled = true;
  $("login-error").textContent = "";
  state.token = $("token").value.trim();
  $("token").value = "";
  const auth = ++state.auth;
  try {
    await loadCapabilities();
    if (state.auth !== auth) return;
    $("login-view").hidden = true;
    $("workspace").hidden = false;
    renderSessions();
    $("main").focus();
    await checkReadiness();
    const id = location.hash.startsWith("#task/") ? location.hash.slice(6) : "";
    if (id && state.token) await openTask(id);
    else if (state.token && location.hash === "#history") openHistory();
  } catch (error) {
    if (!$("login-view").hidden) {
      state.token = "";
      $("login-error").textContent = errorMessage(error);
    } else showError(error);
  } finally {
    $("login-button").disabled = false;
  }
});
$("logout").addEventListener("click", () => logout());
$("history-nav").addEventListener("click", openHistory);
$("return-history").addEventListener("click", openHistory);
$("history-form").addEventListener("submit", (event) => {
  event.preventDefault();
  applyHistoryFilters();
});
$("history-reset").addEventListener("click", () => {
  $("history-form").reset();
  applyHistoryFilters();
});
$("history-refresh").addEventListener("click", applyHistoryFilters);
$("history-prev").addEventListener("click", () => {
  if (!taskHistory.busy && taskHistory.page > 0)
    void loadHistoryPage(taskHistory.page - 1);
});
$("history-next").addEventListener("click", () => {
  if (taskHistory.busy || !taskHistory.next) return;
  taskHistory.cursors = taskHistory.cursors.slice(0, taskHistory.page + 1);
  taskHistory.cursors.push(taskHistory.next);
  void loadHistoryPage(taskHistory.page + 1);
});
$("history-timezone").textContent =
  `按本机时区 ${Intl.DateTimeFormat().resolvedOptions().timeZone} 筛选任务创建时间，与报告内的历史证据窗口无关。`;
$("new-diagnosis").addEventListener("click", showCreate);
$("close-create").addEventListener("click", () => $("create-dialog").close());
for (const [index, name] of tabNames.entries()) {
  $(`tab-${name}`).addEventListener("click", () => selectTab(name));
  $(`tab-${name}`).addEventListener("keydown", (event) => {
    const next = {
      ArrowRight: (index + 1) % tabNames.length,
      ArrowLeft: (index + tabNames.length - 1) % tabNames.length,
      Home: 0,
      End: tabNames.length - 1,
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    selectTab(tabNames[next], true);
  });
}
$("reload").addEventListener("click", async () => {
  $("reload").disabled = true;
  notice("");
  try {
    await loadCapabilities();
    await checkReadiness();
  } catch (error) {
    showError(error);
  } finally {
    $("reload").disabled = false;
  }
});
$("instance").addEventListener("change", updateInstance);
$("problem").addEventListener("change", () => {
  $("problem-description").textContent = descriptions[$("problem").value] || "";
});
$("use-window").addEventListener("change", () => {
  $("window-fields").hidden = !$("use-window").checked;
});
$("timezone-note").textContent =
  `按本机时区 ${Intl.DateTimeFormat().resolvedOptions().timeZone} 输入，提交时转换为 UTC。`;
$("diagnosis-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.creating || !selectedInstance()?.enabled) return;
  notice("");
  const body = {
    instance_id: $("instance").value,
    problem_type: $("problem").value,
  };
  if (selectedInstance().supports_history && $("use-window").checked) {
    const start = new Date($("window-start").value),
      end = new Date($("window-end").value);
    if (
      !Number.isFinite(start.getTime()) ||
      !Number.isFinite(end.getTime()) ||
      end <= start ||
      end - start > 86400000 ||
      end > new Date()
    ) {
      notice(
        "请填写有效时间窗口：结束晚于开始、不在未来，且跨度不超过 24 小时。",
      );
      return;
    }
    body.window = { start: start.toISOString(), end: end.toISOString() };
  }
  state.creating = true;
  $("create-button").disabled = true;
  $("new-diagnosis").disabled = true;
  renderOverview(selectedInstance());
  $("create-button").textContent = "正在提交…";
  const auth = state.auth;
  const generation = state.generation;
  let accepted = false;
  try {
    const response = await api("/api/v1/diagnoses", { method: "POST", body });
    accepted = true;
    if (generation !== state.generation) {
      state.sessions.unshift({
        id: response.task_id,
        problem: body.problem_type,
        instance: body.instance_id,
      });
      state.sessions = state.sessions.slice(0, 20);
      renderSessions();
      notice("任务已创建，可从本次会话打开；当前查看内容保持不变。", true);
      return;
    }
    await openTask(response.task_id);
  } catch (error) {
    if (auth === state.auth && error.name !== "AbortError") {
      showError(error);
      if (!accepted && (!error.status || error.status >= 500))
        notice(
          `${errorMessage(error)} 提交结果未知，服务可能已创建任务。请先核实，避免重复提交。`,
        );
    }
  } finally {
    if (auth === state.auth) {
      state.creating = false;
      $("create-button").disabled = !selectedInstance()?.enabled;
      $("new-diagnosis").disabled = !selectedInstance()?.enabled;
      renderOverview(selectedInstance());
      $("create-button").textContent = "开始诊断 →";
    }
  }
});
$("lookup-form").addEventListener("submit", (event) => {
  event.preventDefault();
  openTask($("task-id").value.trim()).catch(showError);
});
$("refresh-task").addEventListener("click", async () => {
  if (!state.taskId || $("refresh-task").disabled) return;
  $("refresh-task").disabled = true;
  notice("");
  pollingSince = Date.now();
  const generation = ++state.generation;
  try {
    await refreshTask(generation);
  } catch (error) {
    if (generation === state.generation) showError(error);
  } finally {
    $("refresh-task").disabled = false;
  }
});
$("feedback-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (
    state.feedbackBusy ||
    !state.detail ||
    !terminal(state.detail.task.status)
  )
    return;
  const id = state.taskId,
    generation = state.generation,
    auth = state.auth;
  state.feedbackBusy = true;
  $("feedback-fields").disabled = true;
  notice("");
  let saved = false;
  try {
    await api(`/api/v1/diagnoses/${id}/feedback`, {
      method: "POST",
      body: {
        verdict: $("verdict").value,
        confirmed_root_cause: $("root-cause").value.trim() || null,
        comment: $("comment").value.trim() || null,
      },
    });
    saved = true;
    if (generation !== state.generation) return;
    $("feedback-form").reset();
    await refreshTask(generation);
    if (generation === state.generation)
      notice("反馈已保存，原诊断报告保持不变。", true);
  } catch (error) {
    if (generation === state.generation && error.name !== "AbortError") {
      showError(error);
      if (saved) notice("反馈已保存，但回读失败。请刷新任务查看，勿重复提交。");
      else if (!error.status || error.status >= 500)
        notice(
          `${errorMessage(error)} 反馈保存结果未知，请刷新任务核实，勿重复提交。`,
        );
    }
  } finally {
    if (auth === state.auth) {
      state.feedbackBusy = false;
      $("feedback-fields").disabled =
        !state.detail || !terminal(state.detail.task.status);
    }
  }
});
$("close-evidence").addEventListener("click", () =>
  $("evidence-dialog").close(),
);
window.addEventListener("hashchange", () => {
  if (state.token && location.hash.startsWith("#task/"))
    openTask(location.hash.slice(6)).catch(showError);
  else if (state.token && location.hash === "#history") openHistory();
});
window.addEventListener("pagehide", () => {
  clearTimeout(pollTimer);
  requests.forEach((controller) => controller.abort());
});
