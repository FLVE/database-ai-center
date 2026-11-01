import { test, expect } from "@playwright/test";

const token = "unit-test-token-not-a-real-secret";
async function login(page) {
  await page.getByLabel("访问令牌", { exact: true }).fill(token);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator("#workspace")).toBeVisible();
}
async function history(page) {
  await page.goto("/");
  await login(page);
  await page.getByRole("button", { name: "任务历史", exact: false }).click();
  await expect(page.locator("#history-view")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  await page.locator("#history-from").fill("2024-01-01T00:00");
  await page.locator("#history-until").fill("2024-01-02T00:00");
  await page.locator("#history-limit").selectOption("10");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.locator("#history-rows tr")).toHaveCount(10);
}

test("服务器历史：分页、报告往返、重登与窄屏", async ({ page }, testInfo) => {
  await history(page);
  const firstId = await page
    .locator("#history-rows tr")
    .first()
    .locator("td:nth-child(3) small")
    .innerText();
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator("#history-page")).toHaveText(
    "第 2 页 · 本页 10 条",
  );
  const secondId = await page
    .locator("#history-rows tr")
    .first()
    .locator("td:nth-child(3) small")
    .innerText();
  expect(secondId).not.toBe(firstId);
  await page.locator(".history-open").first().click();
  await expect(page.locator("#current-task-id")).toHaveText(secondId);
  await page.getByRole("button", { name: "返回任务历史" }).click();
  await expect(page.locator("#history-page")).toHaveText(
    "第 2 页 · 本页 10 条",
  );
  await expect(page.locator("#history-from")).toHaveValue("2024-01-01T00:00");
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator("#history-page")).toHaveText("第 3 页 · 本页 5 条");
  await expect(page.getByRole("button", { name: "下一页" })).toBeDisabled();
  await page.getByRole("button", { name: "上一页" }).click();
  await expect(page.locator("#history-page")).toHaveText(
    "第 2 页 · 本页 10 条",
  );
  await page.getByRole("button", { name: "刷新列表" }).click();
  await expect(page.locator("#history-page")).toHaveText(
    "第 1 页 · 本页 10 条",
  );
  await page.screenshot({
    path: testInfo.outputPath("history-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: testInfo.outputPath("history-mobile.png"),
    fullPage: true,
  });
  await page.locator("#history-status").selectOption("completed");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.locator("#history-rows tr")).toHaveCount(9);
  await page.locator("#history-instance").fill("redis-lab-01");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.locator("#history-rows tr")).toHaveCount(2);
  await page.reload();
  await login(page);
  await expect(page.locator("#history-view")).toBeVisible();
  await expect(page.locator("#history-rows tr")).toHaveCount(20);
  await expect(page.locator("#history-instance")).toHaveValue("");
  await expect(page.locator("#session-list")).toContainText("还没有打开的调查");
});

test("历史筛选校验、空页、查询失败与 401 清理", async ({ page }) => {
  await history(page);
  await page.locator("#history-until").fill("2023-12-31T00:00");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.locator("#history-message")).toContainText(
    "创建终点必须晚于",
  );
  await expect(page.locator("#history-rows")).toBeEmpty();
  await page.getByRole("button", { name: "清除", exact: true }).click();
  await expect(page.locator("#history-rows tr")).toHaveCount(20);
  await page.locator("#history-instance").fill("not-a-configured-instance");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.locator("#history-message")).toContainText(
    "没有符合条件的任务",
  );
  await expect(page.getByRole("button", { name: "下一页" })).toBeDisabled();
  await page.route("**/api/v1/diagnoses?*", (route) =>
    route.fulfill({ status: 503, body: "private upstream response" }),
  );
  await page.getByRole("button", { name: "刷新列表" }).click();
  await expect(page.locator("#history-message")).toContainText("刷新列表");
  await expect(page.locator("#history-message")).not.toContainText(
    "private upstream",
  );
  await page.unroute("**/api/v1/diagnoses?*");
  await page.getByRole("button", { name: "清除", exact: true }).click();
  await expect(page.locator("#history-rows tr")).toHaveCount(20);
  await page.route("**/api/v1/diagnoses?*", (route) =>
    route.fulfill({ status: 401, json: {} }),
  );
  await page.getByRole("button", { name: "刷新列表" }).click();
  await expect(page.locator("#login-view")).toBeVisible();
  await expect(page.locator("#history-rows")).toBeEmpty();
  await expect(page.locator("#history-message")).toBeEmpty();
});

test("历史晚到响应不覆盖新筛选或退出后的页面", async ({ page }) => {
  await history(page);
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/diagnoses?*", async (route) => {
    if (
      new URL(route.request().url()).searchParams.get("status") === "partial"
    ) {
      const response = await route.fetch();
      await gate;
      await route.fulfill({ response });
    } else await route.continue();
  });
  await page.locator("#history-status").selectOption("partial");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.locator("#history-view")).toHaveAttribute(
    "aria-busy",
    "true",
  );
  await page.locator("#history-status").selectOption("completed");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.locator("#history-rows tr")).toHaveCount(9);
  const arrived = page.waitForResponse((r) =>
    r.url().includes("status=partial"),
  );
  release();
  await arrived;
  await expect(page.locator("#history-rows tr")).toHaveCount(9);
  await expect(page.locator("#history-rows .badge").first()).toHaveText(
    "检查完成",
  );
  await page.unroute("**/api/v1/diagnoses?*");
  let releaseLogout;
  const logoutGate = new Promise((resolve) => {
    releaseLogout = resolve;
  });
  await page.route("**/api/v1/diagnoses?*", async (route) => {
    const response = await route.fetch();
    await logoutGate;
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "刷新列表" }).click();
  await expect(page.locator("#history-view")).toHaveAttribute(
    "aria-busy",
    "true",
  );
  await page.getByRole("button", { name: "退出", exact: true }).click();
  releaseLogout();
  await expect(page.locator("#login-view")).toBeVisible();
  await expect(page.locator("#history-rows")).toBeEmpty();
  expect(
    await page.evaluate(() => [localStorage.length, sessionStorage.length]),
  ).toEqual([0, 0]);
});
