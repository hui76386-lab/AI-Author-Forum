const { test, expect } = require("@playwright/test");

const username = process.env.AUTHOR_E2E_USERNAME;
const password = process.env.AUTHOR_E2E_PASSWORD;
const journalId = process.env.AUTHOR_E2E_JOURNAL_ID;
const runId = process.env.AUTHOR_E2E_RUN_ID || Date.now().toString();

test.describe("author submission workbench", () => {
  test.skip(
    !username || !password || !journalId,
    "Live author acceptance credentials and journal id are required.",
  );
  test.describe.configure({ mode: "serial" });

  async function login(page) {
    const response = await page.goto("/author/login/", {
      waitUntil: "networkidle",
    });
    expect(response.status()).toBe(200);
    await page.getByLabel("用户名").fill(username);
    await page.getByLabel("密码").fill(password);
    await page.getByRole("button", { name: "登录作者工作台" }).click();
    await expect(page).toHaveURL(/\/author\/$/);
    await expect(page.getByRole("heading", { name: "我的投稿" })).toBeVisible();
  }

  async function expectNoHorizontalOverflow(page) {
    const dimensions = await page.evaluate(() => ({
      body: document.body.scrollWidth,
      document: document.documentElement.scrollWidth,
      viewport: document.documentElement.clientWidth,
    }));
    expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport + 1);
    expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport + 1);
  }

  test("creates and submits a locked author draft without admin access", async ({
    page,
  }, testInfo) => {
    await login(page);
    await page.getByRole("link", { name: "新建投稿" }).first().click();
    await expect(page.getByRole("heading", { name: "新建投稿" })).toBeVisible();

    await page.locator("#id_journal").selectOption(journalId);
    const category = page.locator("#id_category");
    await expect
      .poll(async () =>
        category.locator("option:not([disabled])").evaluateAll((options) =>
          options.filter((option) => option.value).map((option) => option.value),
        ),
      )
      .not.toEqual([]);
    const categoryValue = await category
      .locator("option:not([disabled])")
      .evaluateAll((options) => options.find((option) => option.value)?.value);
    await category.selectOption(categoryValue);

    const title = `Author workbench acceptance ${runId}`;
    await page.locator("#id_title").fill(title);
    await page.locator("#id_abstract").fill("Browser acceptance abstract.");
    await page.getByRole("button", { name: "正文", exact: true }).click();
    await page
      .locator('[data-rich-editor="html"]')
      .first()
      .fill("Browser acceptance body with a controlled paragraph.");
    await page.locator("#id_keywords").fill("author, acceptance");
    await page
      .locator("#id_responsibility_statement")
      .fill("I accept responsibility for this browser acceptance submission.");
    await page.locator("#id_article_type").selectOption("Research Analysis");
    await page.locator("#id_contributors-0-name").fill("Acceptance Author");
    await page
      .locator("#id_contributors-0-affiliation")
      .fill("Acceptance Institute");
    await page.locator("#id_contributors-0-is_corresponding").check();
    await page.getByRole("button", { name: "保存草稿" }).click();

    await expect(page.getByRole("heading", { name: title })).toBeVisible();
    await expect(page.getByRole("link", { name: "编辑草稿" })).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("author-workbench-desktop-draft.png"),
      fullPage: true,
    });

    await page.getByRole("link", { name: "提交初审" }).click();
    await expect(page.getByText("已通过", { exact: true })).toBeVisible();
    await page.locator("#id_confirmed").check();
    await page.getByRole("button", { name: "提交并锁定" }).click();
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
    await expect(page.getByRole("link", { name: "编辑草稿" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "提交初审" })).toHaveCount(0);

    const adminResponse = await page.goto("/admin/placements/", {
      waitUntil: "domcontentloaded",
    });
    expect(adminResponse.status()).toBe(403);
  });

  test("renders dashboard and submission form on mobile without overflow", async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    await expectNoHorizontalOverflow(page);
    await page.getByRole("link", { name: "新建投稿" }).first().click();
    await expect(page.getByRole("heading", { name: "新建投稿" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath("author-workbench-mobile-form.png"),
      fullPage: true,
    });
  });
});
