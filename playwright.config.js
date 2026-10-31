import { defineConfig } from "@playwright/test";

// Readiness probes must reach the loopback fixture, not a developer's HTTP proxy.
process.env.NO_PROXY = [process.env.NO_PROXY, "127.0.0.1", "localhost"]
  .filter(Boolean)
  .join(",");
process.env.no_proxy = process.env.NO_PROXY;

export default defineConfig({
  testDir: "./tests/web",
  timeout: 30000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:8097",
    browserName: "chromium",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "uv run uvicorn tests.web.server:app --factory --host 127.0.0.1 --port 8097 --no-access-log",
    url: "http://127.0.0.1:8097/healthz",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
