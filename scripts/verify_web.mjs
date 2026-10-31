// This live check creates a diagnostic task and feedback in the local DBIC lab only.
// Credentials stay in process memory; no browser tracing or authentication output.
import { execFileSync } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { chromium, expect } from "@playwright/test";

const compose = ["compose", "--env-file", ".env", "-f", "deploy/compose.yaml"];
const token = execFileSync(
  "docker",
  [
    ...compose,
    "exec",
    "-T",
    "dbic-api",
    "python",
    "-c",
    "import os; assert os.environ['DBIC_ENV'] == 'lab'; print(os.environ['DBIC_API_TOKEN'])",
  ],
  { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
).trim();
const browser = await chromium.launch();
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("http://127.0.0.1:8086/", { waitUntil: "networkidle" });
  await page.getByLabel("访问令牌", { exact: true }).fill(token);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator("#workspace")).toBeVisible();
  await expect(page.locator(".instance-button")).toHaveCount(5);
  await mkdir("dist/web", { recursive: true });
  await page.screenshot({ path: "dist/web/overview-live.png", fullPage: true });
  await page.getByRole("button", { name: "新建诊断" }).click();
  await page.screenshot({ path: "dist/web/create-live.png" });
  await page.locator("#instance").selectOption("mysql-lab-01");
  await page.locator("#problem").selectOption("lock_wait");
  await page.getByRole("button", { name: "开始诊断" }).click();
  await expect(page.locator("#task-status")).toHaveText(/检查完成|部分完成/, {
    timeout: 100000,
  });
  const taskId = await page.locator("#current-task-id").innerText();
  await expect(page.locator("#evidence-list .evidence-row")).toHaveCount(2);
  await page.getByRole("tab", { name: "检查与证据" }).click();
  await page.locator("#evidence-list button").first().click();
  await expect(page.locator("#evidence-content")).toContainText("已脱敏");
  await page.screenshot({ path: "dist/web/evidence-live.png" });
  await page.getByRole("button", { name: "关闭证据详情" }).click();
  await page.getByRole("tab", { name: "人工反馈" }).click();
  await page.locator("#verdict").selectOption("partial");
  const comment = `网页流程验收 ${new Date().toISOString()}：仅确认页面、证据引用与反馈归档，不代表人工确认根因。`;
  await page.locator("#comment").fill(comment);
  await page.getByRole("button", { name: "保存反馈" }).click();
  await expect(page.locator("#notice")).toContainText("反馈已保存");
  await expect(page.locator("#feedback-list")).toContainText(comment);
  const response = await page.request.get(
    `http://127.0.0.1:8086/api/v1/diagnoses/${taskId}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  const detail = await response.json();
  if (!detail.feedback.some((item) => item.comment === comment))
    throw new Error("Feedback was not persisted");
  if (detail.report.generator_version !== "0.9.0")
    throw new Error("Expected web V1 runtime");
  if (errors.length) throw new Error(`Browser script errors: ${errors.length}`);
  await page.getByRole("tab", { name: "调查报告" }).click();
  await page.screenshot({ path: "dist/web/desktop-live.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "dist/web/mobile-live.png", fullPage: true });
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(page.locator("#report-content")).toBeEmpty();
  console.log(
    `PASS: live lab browser login, five instances, MySQL task ${taskId}, evidence, persisted feedback, logout`,
  );
} finally {
  await browser.close();
}
