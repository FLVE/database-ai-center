import { test, expect } from "@playwright/test";

// This token authenticates only the isolated in-memory test server.
const token = "unit-test-token-not-a-real-secret";
const headers = { Authorization: `Bearer ${token}` };
async function login(page) {
  await page.goto("/");
  await page.getByLabel("访问令牌", { exact: true }).fill(token);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator("#workspace")).toBeVisible();
}
async function create(page, problem = "lock_wait") {
  if (!(await page.locator("#create-dialog").isVisible()))
    await page.getByRole("button", { name: "新建诊断" }).click();
  await page.locator("#problem").selectOption(problem);
  await page.getByRole("button", { name: "开始诊断" }).click();
  await expect(page.locator("#task-status")).toHaveText(
    problem === "lock_wait" ? "检查完成" : "部分完成",
  );
  return page.locator("#current-task-id").innerText();
}
async function finishedTask(request) {
  const response = await request.post("/api/v1/diagnoses", {
    headers,
    data: { instance_id: "mysql-lab-01", problem_type: "lock_wait" },
  });
  const { task_id: id } = await response.json();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(`/api/v1/diagnoses/${id}`, { headers })
          ).json()
        ).task.status,
    )
    .toBe("completed");
  return id;
}

test("真实 API：登录、能力、诊断、证据、反馈与任务链接", async ({
  page,
  request,
}, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page);
  await expect(page.locator(".instance-button")).toHaveCount(5);
  await page.getByRole("button", { name: "Doris doris-lab-01 未启用" }).click();
  await expect(page.locator("#create-button")).toBeDisabled();
  await page
    .getByRole("button", { name: "PostgreSQL postgresql-lab-01" })
    .click();
  await expect(page.locator("#problem")).toHaveValue("maintenance_health");
  await expect(page.locator("#history-controls")).toBeHidden();
  await page.getByRole("button", { name: "MySQL mysql-lab-01" }).click();
  const id = await create(page);
  await expect(page.locator("#evidence-list .evidence-row")).toHaveCount(2);
  await page.locator("#report-content .evidence-link").first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator("#evidence-content")).toContainText("已脱敏");
  await page.keyboard.press("Escape");
  await expect(page.locator("#evidence-dialog")).toBeHidden();
  await expect(
    page.locator("#report-content .evidence-link").first(),
  ).toBeFocused();
  const xss = '<img src=x onerror="window.injected=true">';
  await page.getByRole("tab", { name: "人工反馈" }).click();
  await page.getByLabel("补充说明（选填）").fill(xss);
  await page.getByRole("button", { name: "保存反馈" }).click();
  await expect(page.locator("#notice")).toContainText("反馈已保存");
  await expect(page.locator("#feedback-list")).toContainText(xss);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
  expect(await page.locator("#feedback-list img").count()).toBe(0);
  const archived = await (
    await request.get(`/api/v1/diagnoses/${id}`, { headers })
  ).json();
  expect(archived.feedback.at(-1).comment).toBe(xss);
  await page.screenshot({
    path: testInfo.outputPath("desktop.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(page.locator("#login-view")).toBeVisible();
  await page.getByLabel("访问令牌", { exact: true }).fill(token);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator("#current-task-id")).toHaveText(id);
  await expect(page.locator("#feedback-list")).toContainText(xss);
  await page.getByRole("tab", { name: "调查报告" }).click();
  expect(errors).toEqual([]);
});

test("历史时间校验与证据不足不会显示正常", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "新建诊断" }).click();
  await page.locator("#use-window").check();
  await page.locator("#window-start").fill("2020-01-02T10:00");
  await page.locator("#window-end").fill("2020-01-01T10:00");
  await page.getByRole("button", { name: "开始诊断" }).click();
  await expect(page.locator("#create-notice")).toContainText("有效时间窗口");
  await expect(page.locator("#create-notice")).toBeVisible();
  await expect(page.locator("#task-view")).toBeHidden();
  await page.locator("#window-start").fill("2020-01-01T09:00");
  const submitted = page.waitForRequest(
    (req) => req.method() === "POST" && req.url().endsWith("/api/v1/diagnoses"),
  );
  await page.getByRole("button", { name: "开始诊断" }).click();
  const body = (await submitted).postDataJSON();
  expect(body.window.start).toMatch(/Z$/);
  await expect(page.locator("#task-status")).toHaveText("部分完成");
  await expect(page.locator("#evidence-list .evidence-row")).toHaveCount(4);
  await expect(page.locator("#report-content")).toContainText(
    "缺失证据与覆盖限制",
  );
  await page.getByRole("button", { name: "新建诊断" }).click();
  await page.locator("#use-window").uncheck();
  await create(page, "slow_query");
  await expect(page.locator("#report-content")).toContainText(
    "no timed application digest samples",
  );
});

test("令牌不持久化，401 清空报告与对话框", async ({ page }) => {
  await page.goto("/");
  await page.locator("#token").fill("wrong");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator("#login-error")).toContainText("令牌无效");
  await login(page);
  await create(page);
  await page.getByRole("tab", { name: "检查与证据" }).click();
  await page.locator("#evidence-list button").first().click();
  expect(
    await page.evaluate(() => [
      localStorage.length,
      sessionStorage.length,
      document.cookie,
    ]),
  ).toEqual([0, 0, ""]);
  await page.keyboard.press("Escape");
  await page.route("**/api/v1/instances", (route) =>
    route.fulfill({ status: 401, json: { message: "Unauthorized" } }),
  );
  await page.getByRole("button", { name: "刷新能力" }).click();
  await expect(page.locator("#login-view")).toBeVisible();
  await expect(page.locator("#report-content")).toBeEmpty();
  await expect(page.locator("#evidence-content")).toBeEmpty();
  await expect(page.locator("#token")).toHaveValue("");
  expect(new URL(page.url()).hash).toBe("");
});

test("429、断网和 HTML 错误不误报已提交、不自动重试", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "新建诊断" }).click();
  let posts = 0;
  await page.route("**/api/v1/diagnoses", (route) => {
    posts++;
    return route.fulfill({ status: 429, body: "<h1>busy</h1>" });
  });
  await page.getByRole("button", { name: "开始诊断" }).click();
  await expect(page.locator("#notice")).toContainText("任务容量已满");
  await expect(page.locator("#create-notice")).toBeVisible();
  await expect(page.locator("#task-view")).toBeHidden();
  expect(posts).toBe(1);
  await page.unroute("**/api/v1/diagnoses");
  await page.route("**/api/v1/diagnoses", (route) => {
    posts++;
    return route.abort("connectionfailed");
  });
  await page.getByRole("button", { name: "开始诊断" }).click();
  await expect(page.locator("#notice")).toContainText("提交结果未知");
  expect(posts).toBe(2);
  await page.unroute("**/api/v1/diagnoses");
  await create(page);
  await page.getByRole("tab", { name: "人工反馈" }).click();
  await page.route("**/api/v1/diagnoses/*/feedback", (route) =>
    route.fulfill({ status: 503, body: "<h1>proxy secret error</h1>" }),
  );
  await page.getByRole("button", { name: "保存反馈" }).click();
  await expect(page.locator("#notice")).toContainText("反馈保存结果未知");
  await expect(page.locator("#notice")).not.toContainText("proxy secret");
});

test("切换任务后，迟到响应不覆盖当前报告", async ({ page, request }) => {
  const oldId = await finishedTask(request),
    newId = await finishedTask(request);
  await login(page);
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/v1/diagnoses/${oldId}`, async (route) => {
    await gate;
    await route.continue();
  });
  await page.locator("#task-id").fill(oldId);
  await page.getByRole("button", { name: "打开任务", exact: true }).click();
  await page.locator("#task-id").fill(newId);
  await page.getByRole("button", { name: "打开任务", exact: true }).click();
  await expect(page.locator("#task-status")).toHaveText("检查完成");
  release();
  await expect(page.locator("#current-task-id")).toHaveText(newId);
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(page.locator("#workspace")).toBeHidden();
  await expect(page.locator("#session-list")).toBeEmpty();
});

test("轮询执行中、失败与任务不存在", async ({ page, request }) => {
  const id = await finishedTask(request);
  const detail = await (
    await request.get(`/api/v1/diagnoses/${id}`, { headers })
  ).json();
  await login(page);
  let reads = 0;
  await page.route(`**/api/v1/diagnoses/${id}`, (route) => {
    reads++;
    return route.fulfill({
      json: {
        ...detail,
        report: null,
        task: {
          ...detail.task,
          status: reads === 1 ? "running" : "failed",
          error_code: reads === 1 ? null : "execution_failed",
        },
      },
    });
  });
  await page.locator("#task-id").fill(id);
  await page.getByRole("button", { name: "打开任务", exact: true }).click();
  await expect(page.locator("#task-status")).toHaveText("调查中");
  await page.getByRole("tab", { name: "人工反馈" }).click();
  await expect(page.getByRole("button", { name: "保存反馈" })).toBeDisabled();
  await expect(page.locator("#task-status")).toHaveText("执行失败");
  await expect(page.getByRole("tab", { name: "人工反馈" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.locator("#task-description")).toContainText(
    "execution_failed",
  );
  expect(reads).toBe(2);
  await page.locator("#task-id").fill("00000000-0000-0000-0000-000000000000");
  await page.getByRole("button", { name: "打开任务", exact: true }).click();
  await expect(page.locator("#notice")).toContainText("未找到任务");
  await expect(page.locator("#report-content")).toBeEmpty();
});

test("手机布局完整可用，无横向溢出", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await create(page);
  await page.getByRole("tab", { name: "检查与证据" }).click();
  await page.locator("#evidence-list button").first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("mobile-evidence.png") });
  await page.getByRole("button", { name: "关闭证据详情" }).click();
  await page.getByRole("tab", { name: "人工反馈" }).click();
  await page.getByLabel("补充说明（选填）").fill("移动端复核");
  await page.getByRole("button", { name: "保存反馈" }).click();
  await expect(page.locator("#feedback-list")).toContainText("移动端复核");
});

test("新建对话框、实例上下文和标签页键盘导航", async ({ page }, testInfo) => {
  await login(page);
  await expect(page.locator("#overview-name")).toHaveText("MySQL");
  await page.screenshot({
    path: testInfo.outputPath("overview.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "新建诊断" }).click();
  await expect(page.locator("#problem")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator("#create-dialog")).toBeHidden();
  await expect(page.getByRole("button", { name: "新建诊断" })).toBeFocused();
  await create(page);
  await page.screenshot({
    path: testInfo.outputPath("report.png"),
    fullPage: true,
  });
  await page.getByRole("tab", { name: "调查报告" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.locator("#tab-evidence")).toBeFocused();
  await expect(page.locator("#panel-report")).toBeHidden();
  await expect(page.locator("#panel-evidence")).toBeVisible();
  await page.locator("#evidence-list button").first().click();
  await page.screenshot({ path: testInfo.outputPath("drawer.png") });
  await page.keyboard.press("Escape");
  await expect(page.locator("#evidence-list button").first()).toBeFocused();
  await page.locator("#tab-evidence").focus();
  await page.keyboard.press("End");
  await expect(page.locator("#tab-feedback")).toBeFocused();
  await page.keyboard.press("Home");
  await expect(page.locator("#tab-report")).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(page.locator("#tab-feedback")).toBeFocused();
  await page.getByRole("button", { name: "Redis redis-lab-01" }).click();
  await expect(page.locator("#overview-name")).toHaveText("Redis");
  await expect(page.locator("#task-view")).toBeHidden();
  expect(new URL(page.url()).hash).toBe("");
  await page.locator("#overview-problems button").click();
  await expect(page.locator("#problem")).toHaveValue("memory_pressure");
  await page.getByRole("button", { name: "关闭新建诊断" }).click();
  await page.locator(".session-button").first().click();
  await expect(page.locator("#task-instance")).toHaveText("mysql-lab-01");
  await expect(
    page.getByRole("button", { name: "MySQL mysql-lab-01" }),
  ).toHaveAttribute("aria-current", "true");
  await expect(page.locator("#tab-report")).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("提交中关闭对话框并切换实例，迟到的创建结果保留在会话", async ({
  page,
}) => {
  await login(page);
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/diagnoses", async (route) => {
    const response = await route.fetch();
    await gate;
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "新建诊断" }).click();
  await page.getByRole("button", { name: "开始诊断" }).click();
  await expect(page.locator("#create-button")).toHaveText("正在提交…");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Redis redis-lab-01" }).click();
  release();
  await expect(page.locator("#notice")).toContainText("任务已创建");
  await expect(page.locator("#overview-name")).toHaveText("Redis");
  await expect(page.locator("#task-view")).toBeHidden();
  await page.locator(".session-button").first().click();
  await expect(page.locator("#task-instance")).toHaveText("mysql-lab-01");
  await expect(page.locator("#task-status")).toHaveText("检查完成");
});
