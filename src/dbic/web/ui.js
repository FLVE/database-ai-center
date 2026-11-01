export const $ = (id) => document.getElementById(id);
export const problemNames = {
  lock_wait: "锁等待调查",
  slow_query: "慢查询调查",
  memory_pressure: "内存压力调查",
  activity_health: "活动与副本检查",
  maintenance_health: "阻塞与维护检查",
  cluster_health: "集群与查询检查",
};
export const databaseNames = {
  mysql: "MySQL",
  redis: "Redis",
  mongodb: "MongoDB",
  postgresql: "PostgreSQL",
  doris: "Doris",
};
export const descriptions = {
  lock_wait: "检查当前阻塞关系与会话等待，定位有证据支持的阻塞链。",
  slow_query: "检查当前健康与累计查询摘要；无样本时明确标记证据缺失。",
  memory_pressure: "查看内存构成、保留慢日志与已有大 Key 审计摘要。",
  activity_health: "采样当前操作与复制集状态，保留单节点和采样覆盖限制。",
  maintenance_health: "检查阻塞会话与维护统计，不执行维护或终止会话。",
  cluster_health: "检查 FE / BE 与诊断账号可见的查询摘要。",
};
const labels = {
  queued: "排队中",
  running: "调查中",
  completed: "检查完成",
  partial: "部分完成",
  failed: "执行失败",
  cancelled: "已取消",
  ok: "已获取",
  empty: "无匹配记录",
  unavailable: "不可用",
  timeout: "超时",
  forbidden: "权限不足",
  error: "检查错误",
  observed: "已有观测依据",
  inconclusive: "尚不能确定",
  disabled: "未启用",
  rejected: "建议未通过校验",
  supported_hypothesis: "有依据的假设",
  unverified_hypothesis: "待验证假设",
  confirmed_root_cause: "已确认根因",
  correct: "正确",
  incorrect: "错误",
  derived: "推导证据",
  inferred: "推测证据",
  database_state: "数据库状态",
  metric: "指标",
  log: "历史事件",
  historical_incident: "历史案例",
};
export const label = (value) => labels[value] || value;
export const terminal = (status) =>
  ["completed", "partial", "failed", "cancelled"].includes(status);
export function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
export function badge(status) {
  const tone = ["ok", "completed", "observed", "correct"].includes(status)
    ? "ok"
    : ["failed", "error", "incorrect"].includes(status)
      ? "error"
      : [
            "partial",
            "unavailable",
            "inconclusive",
            "timeout",
            "forbidden",
            "rejected",
          ].includes(status)
        ? "warn"
        : "neutral";
  return el("span", label(status), `badge ${tone}`);
}
export function time(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间不可用"
    : date.toLocaleString("zh-CN", { hour12: false });
}
export function button(text, action, className = "quiet") {
  const node = el("button", text, className);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}
function block(title, items) {
  if (!items?.length) return null;
  const section = el("section", null, "report-block");
  section.append(el("h3", title));
  const list = el("ul", null, "text-list");
  items.forEach((item) => list.append(el("li", item)));
  section.append(list);
  return section;
}
function references(ids, evidence, openEvidence) {
  const fragment = document.createDocumentFragment();
  ids.forEach((id) => {
    const index = evidence.findIndex((item) => item.evidence_id === id);
    const node = button(
      index < 0 ? "证据暂不可见" : `证据 ${String(index + 1).padStart(2, "0")}`,
      () => openEvidence(id),
      "evidence-link",
    );
    node.disabled = index < 0;
    node.title = id;
    fragment.append(node);
  });
  return fragment;
}
export function renderReport(detail, evidence, openEvidence) {
  const target = $("report-content");
  const expanded = new Set(
    [...target.querySelectorAll("details[open]")].map(
      (node) => node.dataset.section,
    ),
  );
  target.replaceChildren();
  const report = detail.report;
  $("report-status").textContent = report ? label(report.status) : "尚未生成";
  if (!report) {
    target.append(
      el(
        "p",
        terminal(detail.task.status)
          ? "任务已结束，未生成报告。请查看任务状态与检查记录。"
          : "调查正在进行，报告将在检查结束后显示。",
        "muted",
      ),
    );
    return;
  }
  const layout = el("div", null, "report-layout");
  const main = el("div", null, "report-main");
  const context = el("aside", null, "report-context");
  context.setAttribute("aria-label", "覆盖限制与后续建议");
  layout.append(main, context);
  target.append(layout);
  main.append(el("p", report.summary, "summary"));
  if (!report.findings.length)
    main.append(el("p", "尚无可支持结论的证据，请查看缺失项与建议。", "muted"));
  report.findings.forEach((finding) => {
    const node = el("div", null, "finding");
    node.append(
      badge(finding.certainty),
      el("p", finding.statement),
      references(finding.evidence_ids, evidence, openEvidence),
    );
    if (finding.rule_id)
      node.append(el("p", `规则 · ${finding.rule_id}`, "field-hint mono"));
    main.append(node);
  });
  for (const [title, items] of [
    ["缺失证据与覆盖限制", report.missing_evidence],
    ["建议", report.recommendations],
    ["风险与注意事项", report.risks],
    ["调查时间线", report.timeline],
    ["使用的工具", report.tools],
  ]) {
    const node = block(title, items);
    if (!node) continue;
    if (["调查时间线", "使用的工具"].includes(title)) {
      const disclosure = el("details", null, "report-disclosure");
      disclosure.dataset.section = title;
      disclosure.open = expanded.has(title);
      disclosure.append(el("summary", `${title} · ${items.length}`), node);
      main.append(disclosure);
    } else {
      if (title === "缺失证据与覆盖限制") node.classList.add("report-limits");
      context.append(node);
    }
  }
  const agent = el("section", null, "report-block");
  const agentLabels = {
    ok: "建议已校验",
    disabled: "未启用",
    unavailable: "暂不可用",
    rejected: "建议未通过校验",
  };
  agent.append(
    el(
      "h3",
      `AI 辅助 · ${agentLabels[report.agent_status] || report.agent_status}`,
    ),
  );
  if (report.agent_reason)
    agent.append(
      el(
        "p",
        {
          backend_failure: "模型服务调用失败",
          invalid_advice: "模型输出未通过证据与权限校验",
          budget_exceeded: "建议超出调查预算",
        }[report.agent_reason] || report.agent_reason,
        "field-hint",
      ),
    );
  if (report.agent_suggestions.length) {
    agent.append(el("p", "以下为下一步调查建议，尚未执行。", "field-hint"));
    const reasons = {
      refresh_snapshot: "刷新当前快照",
      check_missing_evidence: "补充缺失证据",
      compare_current_state: "比较当前状态",
    };
    report.agent_suggestions.forEach((item) => {
      const node = el("div", null, "finding");
      node.append(
        el("p", `${reasons[item.reason] || item.reason} · ${item.tool_id}`),
        references(item.evidence_ids, evidence, openEvidence),
      );
      agent.append(node);
    });
  } else agent.append(el("p", "本报告保留确定性检查结果。", "field-hint"));
  context.append(agent);
  target.append(
    el(
      "p",
      `生成于 ${time(report.generated_at)} · ${report.generator_version}`,
      "field-hint report-generated",
    ),
  );
}
export function renderEvidence(detail, evidence, openEvidence) {
  $("steps").replaceChildren();
  detail.steps.forEach((step) => {
    const row = el("div", null, "step-row");
    const info = el("div");
    info.append(
      el("code", step.tool_id),
      el(
        "small",
        `${time(step.started_at)} · ${Math.round(step.elapsed_ms)} ms${step.error_code ? ` · ${step.error_code}` : ""}`,
      ),
    );
    row.append(info, badge(step.status));
    $("steps").append(row);
  });
  if (!detail.steps.length)
    $("steps").append(el("p", "尚无已归档检查步骤。", "field-hint"));
  $("evidence-count").textContent = `${evidence.length} 条可查看证据`;
  $("evidence-list").replaceChildren();
  evidence.forEach((item, index) => {
    const row = button(
      "",
      () => openEvidence(item.evidence_id),
      "evidence-row",
    );
    const info = el("span", item.source, "evidence-source");
    info.append(el("small", `${label(item.kind)} · ${time(item.observed_at)}`));
    row.append(
      el("span", String(index + 1).padStart(2, "0"), "evidence-number"),
      info,
      badge(item.status),
    );
    $("evidence-list").append(row);
  });
}
export function showEvidence(item) {
  $("evidence-title").textContent = item.source;
  const content = $("evidence-content");
  content.replaceChildren();
  const grid = el("dl", null, "detail-grid");
  for (const [key, value] of [
    ["证据 ID", item.evidence_id],
    ["实例", item.instance_id],
    ["状态", label(item.status)],
    ["类型 / 质量", `${label(item.kind)} / ${label(item.quality)}`],
    ["观测时间", time(item.observed_at)],
    ["归档时间", time(item.collected_at)],
    [
      "时间范围",
      item.window
        ? `${time(item.window.start)} — ${time(item.window.end)}`
        : "当前采样（无历史窗口）",
    ],
    ["工具版本", item.tool_version || "—"],
    ["脱敏", item.redacted ? "已脱敏" : "未标记"],
  ])
    grid.append(el("dt", key), el("dd", value));
  content.append(grid);
  const limits = block("证据限制", item.limitations);
  if (limits) {
    limits.className = "detail-limits";
    content.append(limits);
  }
  content.append(
    el("h3", "结构化证据"),
    el("pre", JSON.stringify(item.payload, null, 2)),
  );
  if (!$("evidence-dialog").open) $("evidence-dialog").showModal();
}
export function renderFeedback(items) {
  $("feedback-list").replaceChildren();
  if (!items.length)
    $("feedback-list").append(
      el("p", "还没有人工反馈。复核后可在此留下判断和依据。", "field-hint"),
    );
  items.forEach((item) => {
    const row = el("div", null, "feedback-entry");
    row.append(
      el(
        "strong",
        `人工评价 · ${item.verdict === "partial" ? "部分正确" : label(item.verdict)}`,
      ),
      el("p", time(item.created_at), "field-hint"),
    );
    if (item.confirmed_root_cause)
      row.append(el("p", `确认根因：${item.confirmed_root_cause}`));
    if (item.comment) row.append(el("p", item.comment));
    $("feedback-list").append(row);
  });
}

export function renderTaskHistory(items, openTask) {
  $("history-rows").replaceChildren();
  for (const item of items) {
    const row = el("tr");
    const created = el("td", time(item.created_at), "history-created");
    created.dataset.label = "创建时间";
    const instance = el("td");
    instance.dataset.label = "实例";
    instance.append(
      el("span", item.instance_id, "mono"),
      el("small", databaseNames[item.database_type] || item.database_type),
    );
    const problem = el("td");
    problem.dataset.label = "调查问题";
    const name = item.problem_type.startsWith("tool:")
      ? `单工具检查 · ${item.problem_type.slice(5)}`
      : problemNames[item.problem_type] || item.problem_type;
    problem.append(el("span", name), el("small", item.task_id, "mono"));
    const status = el("td");
    status.dataset.label = "状态";
    status.append(badge(item.status));
    const action = el("td");
    action.dataset.label = "操作";
    const open = button(
      "打开报告 →",
      () => openTask(item.task_id),
      "quiet history-open",
    );
    open.setAttribute("aria-label", `打开任务 ${item.task_id}`);
    action.append(open);
    row.append(created, instance, problem, status, action);
    $("history-rows").append(row);
  }
}
