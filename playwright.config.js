const { defineConfig } = require("@playwright/test");

const staticE2EPort = Number(process.env.STATIC_E2E_PORT || 4173);
const managedStaticSiteURL = `http://127.0.0.1:${staticE2EPort}`;
const staticSiteURL = process.env.STATIC_SITE_URL || managedStaticSiteURL;

module.exports = defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: {
    toHaveScreenshot: {
      animations: "disabled",
      maxDiffPixelRatio: 0.01,
    },
  },
  use: {
    baseURL: staticSiteURL,
    httpCredentials: process.env.TEST_BASIC_AUTH_USERNAME
      ? {
          username: process.env.TEST_BASIC_AUTH_USERNAME,
          password: process.env.TEST_BASIC_AUTH_PASSWORD,
        }
      : undefined,
    ignoreHTTPSErrors: process.env.PLAYWRIGHT_IGNORE_HTTPS_ERRORS === "1",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : undefined,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    viewport: { width: 1280, height: 720 },
  },
  webServer: process.env.STATIC_SITE_URL
    ? undefined
    : {
        command:
          `node scripts/run_python.js -m ai_author_forum.static_publish.static_server ` +
          `--root static_publish_output --port ${staticE2EPort}`,
        url: managedStaticSiteURL,
        // Never silently test an unrelated or stale release already bound to the port.
        reuseExistingServer: process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER === "1",
      },
});
