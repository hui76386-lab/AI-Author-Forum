const fs = require("node:fs");
const path = require("node:path");
const { test, expect } = require("@playwright/test");

const baseOrigin = new URL(
  process.env.STATIC_SITE_URL || "http://127.0.0.1:4173",
).origin;

const acceptance = JSON.parse(
  fs.readFileSync(path.join(__dirname, "../../.e2e/acceptance.json"), "utf8"),
);
const staticReleaseVersion = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, "../../static_publish_output/current/manifest.json"),
    "utf8",
  ),
).version;

function englishPath(pagePath) {
  const url = new URL(pagePath, "https://acceptance.invalid");
  if (!url.pathname.startsWith("/en/")) {
    url.pathname = url.pathname === "/" ? "/en/" : `/en${url.pathname}`;
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

const formalPages = [
  {
    path: "/",
    title: /Home|AI Author Forum/,
    content: "Static acceptance home headline",
  },
  {
    path: "/journals/",
    title: /Journals A-Z/,
    content: "Acceptance Journal",
  },
  {
    path: "/journals/acceptance-journal/",
    title: /Acceptance Journal/,
    content: "Deterministic journal homepage acceptance content",
  },
  {
    path: acceptance.empty_journal_path,
    title: /Empty Navigation Journal/,
    content: "Journal acceptance fixture with no configured navigation columns",
  },
  {
    path: acceptance.content_column_path,
    title: /Research articles/,
    content: "Static acceptance content column headline",
  },
  {
    path: acceptance.empty_column_path,
    title: /News & Comment/,
    content: "No placed articles are currently available in this column",
  },
  {
    path: acceptance.current_issue_path,
    title: /Current issue/,
    content: "Static acceptance article",
  },
  {
    path: acceptance.issue_archive_path,
    title: /Browse issues/,
    content: "Acceptance current issue",
  },
  {
    path: acceptance.issue_detail_path,
    title: /Acceptance current issue/,
    content: "Static acceptance article",
  },
  {
    path: acceptance.category_path,
    title: /Machine Intelligence/,
    content: "Machine Intelligence",
  },
  {
    path: acceptance.child_category_path,
    title: /Neural Networks/,
    content: "Neural Networks",
  },
  {
    path: "/articles/static-acceptance-article/",
    title: /Static acceptance article/,
    content: "Static acceptance article body",
  },
  {
    path: "/sections/news/",
    title: /News/,
    content: acceptance.baseline_headline,
  },
  {
    path: "/search/",
    title: /Search AI Author Forum/,
    content: "Static acceptance search headline",
  },
];



test("static search finds a placed article without a database", async ({ page }) => {
  await page.goto(englishPath("/search/?q=Static%20acceptance"), {
    waitUntil: "networkidle",
  });
  await expect(page.getByRole("link", { name: "Static acceptance article" })).toBeVisible();
  await expect(page.locator("[data-result-count]")).toContainText("1");
  await expect(page.locator("[data-search-recommendations]")).toBeHidden();
});

function snapshotNameForPath(pagePath) {
  return pagePath === "/"
    ? "home.png"
    : `${pagePath.replace(/^\//, "").replace(/\/$/, "").replaceAll("/", "-")}.png`;
}

for (const formalPage of formalPages) {
  test(`${formalPage.path} renders from the static release with local assets`, async ({
    page,
  }) => {
    const failedResponses = [];
    const failedRequests = [];
    page.on("response", (response) => {
      const url = new URL(response.url());
      if (url.origin === baseOrigin && response.status() >= 400) {
        failedResponses.push(`${response.status()} ${url.pathname}`);
      }
    });
    page.on("requestfailed", (request) => {
      failedRequests.push(`${request.url()} ${request.failure()?.errorText || "failed"}`);
    });

    const response = await page.goto(englishPath(formalPage.path), {
      waitUntil: "networkidle",
    });
    expect(response).not.toBeNull();
    expect(response.status()).toBe(200);
    await expect(page.locator("html")).toHaveAttribute("lang", /.+/);
    await expect(page.locator("body")).toBeVisible();
    await expect(page.locator("main#main-content")).toBeVisible();
    await expect(page.locator("main")).toHaveCount(1);
    await expect(page.locator("main#main-content h1")).toHaveCount(1);
    await expect(page.locator("main#main-content img:not([alt])")).toHaveCount(0);
    await expect(page).toHaveTitle(formalPage.title);
    await expect(
      page.getByText(formalPage.content, { exact: false }).filter({ visible: true }).first(),
    ).toBeVisible();

    const localAssetReferences = await page
      .locator('link[rel="stylesheet"][href], script[src], img[src], source[src], source[srcset]')
      .evaluateAll((elements) =>
        elements.flatMap((element) => {
          const values = [element.getAttribute("href"), element.getAttribute("src")];
          const srcset = element.getAttribute("srcset");
          if (srcset) {
            values.push(...srcset.split(",").map((item) => item.trim().split(/\s+/)[0]));
          }
          return values.filter(
            (value) => value && !value.startsWith("data:") && !value.startsWith("http"),
          );
        }),
      );
    expect(localAssetReferences.length).toBeGreaterThan(0);
    expect(failedResponses).toEqual([]);
    expect(failedRequests).toEqual([]);
    await expect(page).toHaveScreenshot(snapshotNameForPath(formalPage.path));
  });
}

test("main-site desktop navigation has controlled dropdown lengths", async ({ page }) => {
  await page.goto(englishPath("/"), { waitUntil: "networkidle" });
  const navigation = page.locator('[data-managed-navigation-scope="main_site"]');
  const groups = navigation.locator("[data-navigation-group]");
  await expect(groups).toHaveCount(acceptance.main_navigation_group_lengths.length);

  for (let index = 0; index < acceptance.main_navigation_group_lengths.length; index += 1) {
    const group = groups.nth(index);
    const button = group.locator("[data-nav-button]");
    const menuId = await button.getAttribute("aria-controls");
    await button.click();
    await expect(button).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator(`#${menuId}`)).toBeVisible();
    await expect(page.locator(`#${menuId} a`)).toHaveCount(
      acceptance.main_navigation_group_lengths[index],
    );
    await expect(page).toHaveScreenshot(`main-navigation-group-${index + 1}.png`);
  }
});

test("journal navigation shows the long English group and long Chinese item", async ({ page }) => {
  await page.goto(englishPath("/journals/acceptance-journal/"), {
    waitUntil: "networkidle",
  });
  const navigation = page.locator('[data-managed-navigation-scope="journal"]');
  await expect(navigation.getByText(acceptance.long_group_label, { exact: true })).toBeVisible();

  await page.goto("/journals/acceptance-journal/", { waitUntil: "networkidle" });
  const chineseNavigation = page.locator('[data-managed-navigation-scope="journal"]');
  const firstGroupButton = chineseNavigation.locator("[data-nav-button]").first();
  await firstGroupButton.click();
  await expect(
    chineseNavigation.getByText("研究文章", { exact: true }),
  ).toBeVisible();
  await expect(page).toHaveScreenshot("journal-long-navigation.png");
});

test("second journal has a distinct empty navigation and article state", async ({ page }) => {
  await page.goto(englishPath(acceptance.empty_journal_path), { waitUntil: "networkidle" });
  const navigation = page.locator('[data-managed-navigation-scope="journal"]');
  await expect(navigation.locator("[data-navigation-group]")).toHaveCount(0);
  await expect(navigation.locator("[data-navigation-empty-state]")).toBeVisible();
  await expect(page.locator(".c-empty-state")).toBeVisible();
});

test("content column uses fixed filters and only placed articles", async ({ page }) => {
  await page.goto(englishPath(acceptance.content_column_path), { waitUntil: "networkidle" });
  await expect(page.locator(".c-column-filters")).toBeVisible();
  await expect(page.locator("[data-column-filter='type']")).toBeVisible();
  await expect(page.locator("[data-column-filter='year']")).toBeVisible();
  await expect(page.locator("[data-placement-article]")).toHaveCount(1);
  await expect(page.getByText("Static acceptance content column headline", { exact: true })).toBeVisible();
});

test("content column without placements shows the controlled empty state", async ({ page }) => {
  await page.goto(englishPath(acceptance.empty_column_path), { waitUntil: "networkidle" });
  await expect(page.locator("[data-placement-article]")).toHaveCount(0);
  await expect(page.locator(".c-empty-state")).toHaveText(
    "No placed articles are currently available in this column.",
  );
});

test("current issue and journal article detail both use journal navigation", async ({ page }) => {
  await page.goto(englishPath(acceptance.current_issue_path), { waitUntil: "networkidle" });
  await expect(page.locator('[data-managed-navigation-scope="journal"]')).toBeVisible();
  await expect(page.getByText("Static acceptance article", { exact: true })).toBeVisible();

  await page.goto(new URL(englishPath("/articles/static-acceptance-article/"), baseOrigin).href, {
    waitUntil: "networkidle",
  });
  await expect(page.locator('[data-managed-navigation-scope="journal"]')).toBeVisible();
});

test("mobile navigation collapses and opens as an accordion without horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(englishPath("/"), { waitUntil: "networkidle" });

  const toggle = page.locator("[data-primary-nav-toggle]");
  const navigation = page.locator("#primary-navigation");
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(navigation).not.toBeVisible();

  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(navigation).toBeVisible();

  const firstGroupButton = navigation.locator("[data-nav-button]").first();
  await firstGroupButton.click();
  await expect(firstGroupButton).toHaveAttribute("aria-expanded", "true");
  await expect(navigation.locator(".c-nav-dropdown").first()).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.documentWidth).toBeLessThanOrEqual(dimensions.viewportWidth);

  const mobileLayout = await page.evaluate(() => {
    const logo = document.querySelector(".c-logo");
    const columns = [...document.querySelectorAll(".c-three-column > *")];
    const columnRects = columns.map((column) => column.getBoundingClientRect());
    return {
      logoFits:
        logo.scrollWidth <= logo.clientWidth && logo.scrollHeight <= logo.clientHeight,
      columnsDoNotOverlap: columnRects.every(
        (rect, index) => index === 0 || rect.top >= columnRects[index - 1].bottom,
      ),
    };
  });
  expect(mobileLayout.logoFits).toBe(true);
  expect(mobileLayout.columnsDoNotOverlap).toBe(true);
  await expect(page).toHaveScreenshot("mobile-main-navigation.png", { fullPage: true });
});

test("article exposes a stable UUID and fits the mobile viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(englishPath("/articles/static-acceptance-article/"), {
    waitUntil: "networkidle",
  });

  const manifestResponse = await page.request.get("/manifest.json");
  expect(manifestResponse.ok()).toBeTruthy();
  const manifest = await manifestResponse.json();

  await expect(page.locator("article[data-article-id]")).toHaveAttribute(
    "data-article-id",
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  await expect(page.locator("#reader-interactions")).toHaveAttribute(
    "data-release",
    manifest.version,
  );
  const dimensions = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.documentWidth).toBeLessThanOrEqual(dimensions.viewportWidth);
});

test("manifest records every formal page, zero failures, and media references", async ({
  request,
}) => {
  const response = await request.get("/manifest.json");
  expect(response.ok()).toBeTruthy();
  const manifest = await response.json();

  expect(manifest.schema_version).toBe(2);
  expect(manifest.version).toBe(acceptance.first_version);
  expect(manifest.version).not.toBe(acceptance.second_version);
  expect(manifest.summary.pages).toBe(acceptance.expected_page_count);
  expect(manifest.summary.failed).toBe(0);
  expect(manifest.asset_references.length).toBeGreaterThan(0);
  expect(
    manifest.asset_references.some(
      (reference) =>
        reference.path.startsWith("media/") &&
        reference.pages.includes("articles/static-acceptance-article/index.html"),
    ),
  ).toBeTruthy();

  const manifestPaths = new Set(manifest.files.map((item) => item.path));
  expect(manifestPaths.has(".nginx-direct-ready")).toBeTruthy();
  for (const expectedPage of acceptance.expected_pages) {
    expect(manifestPaths.has(expectedPage)).toBeTruthy();
  }
});

test("active section content is restored from the first release after rollback", async ({
  page,
}) => {
  const response = await page.goto(englishPath("/sections/news/"), {
    waitUntil: "networkidle",
  });
  expect(response.status()).toBe(200);
  await expect(page.getByText(acceptance.baseline_headline, { exact: false })).toBeVisible();
  await expect(page.getByText(acceptance.second_release_headline, { exact: false })).toHaveCount(0);
});

test("legacy core-column path returns a real HTTP 301", async ({ request }) => {
  const response = await request.get(englishPath("/explore-content/news/"), {
    maxRedirects: 0,
  });
  expect(response.status()).toBe(301);
  expect(response.headers().location).toBe(englishPath("/sections/news/"));
});

test("navigation and static filters are keyboard operable", async ({ page }) => {
  await page.goto(englishPath("/"), { waitUntil: "networkidle" });
  const firstNavigationButton = page.locator("[data-nav-button]").first();
  await firstNavigationButton.focus();
  await page.keyboard.press("Enter");
  await expect(firstNavigationButton).toHaveAttribute("aria-expanded", "true");

  await page.goto(englishPath(acceptance.content_column_path), { waitUntil: "networkidle" });
  const yearFilter = page.locator("[data-column-filter='year']");
  await yearFilter.focus();
  await page.keyboard.press("End");
  await expect(page).toHaveURL(/\/year\/2026\/$/);
});

test("core content and static filter paths remain available without JavaScript", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto(englishPath(acceptance.content_column_path), { waitUntil: "load" });
  await expect(page.getByText("Static acceptance content column headline", { exact: true })).toBeVisible();
  await expect(page.locator(".c-column-filters__fallback a[href*='/type/news/']")).toBeVisible();
  await expect(page.locator(".c-column-filters__fallback a[href*='/year/2026/']")).toBeVisible();
  await context.close();
});

test("legacy category path returns a real HTTP 301 before following the redirect", async ({
  request,
  page,
}) => {
  expect(acceptance.database_disconnected).toBe(true);
  expect(fs.existsSync(path.join(__dirname, "../../.e2e/db.sqlite3"))).toBe(false);
  expect(
    fs.existsSync(path.join(__dirname, "../..", acceptance.offline_database_path)),
  ).toBe(true);

  const directResponse = await request.get(englishPath(acceptance.redirect_path), {
    maxRedirects: 0,
  });
  expect(directResponse.status()).toBe(301);
  expect(directResponse.headers().location).toBe(englishPath(acceptance.redirect_to));

  const followedResponse = await page.goto(englishPath(acceptance.redirect_path), {
    waitUntil: "networkidle",
  });
  expect(followedResponse.status()).toBe(200);
  expect(new URL(page.url()).pathname).toBe(englishPath(acceptance.redirect_to));
  await expect(page.getByText("Machine Intelligence", { exact: false }).first()).toBeVisible();
});

test("managed journal hero keeps fallback media, quick links, and nested navigation", async ({
  page,
}) => {
  const response = await page.goto(englishPath("/journals/acceptance-journal/"), {
    waitUntil: "networkidle",
  });
  expect(response.status()).toBe(200);
  await expect(page.locator(".c-journal-home__hero-media")).toHaveAttribute(
    "style",
    /hero-visual\.png/,
  );
  await expect(page.locator(".c-journal-home__quick-links a")).toHaveCount(6);
  await expect(
    page.locator(".c-journal-home__subtopic", { hasText: "Neural Networks" }),
  ).toBeVisible();
});

test("reader comments are safe, operable, and responsive", async ({ page }) => {
  const articleId = "11111111-1111-4111-8111-111111111111";
  const readerId = "22222222-2222-4222-8222-222222222222";
  const publicReaderId = "33333333-3333-4333-8333-333333333333";
  const now = "2026-08-16T12:00:00+00:00";
  let items = [
    {
      id: "44444444-4444-4444-8444-444444444444",
      parent_id: null,
      author: { id: publicReaderId, display_name: "Public Reader" },
      body: "<img src=x onerror=window.commentXss=true> https://example.org",
      withdrawn: false,
      state: "published",
      version: 1,
      created_at: now,
      updated_at: now,
      owned_by_viewer: false,
      pending_for_viewer: false,
      replies: [],
    },
    {
      id: "55555555-5555-4555-8555-555555555555",
      parent_id: null,
      author: { id: readerId, display_name: "Acceptance Reader" },
      body: "My published comment",
      withdrawn: false,
      state: "published",
      version: 1,
      created_at: now,
      updated_at: now,
      owned_by_viewer: true,
      pending_for_viewer: false,
      replies: [],
    },
  ];

  await page.route("**/reader-api/v1/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const respond = (data, status = 200, headers = {}) =>
      route.fulfill({
        status,
        contentType: "application/json",
        headers,
        body: JSON.stringify({ data, request_id: "e2e-reader-comments" }),
      });

    if (pathname.endsWith("/session/")) {
      await respond({
        authenticated: true,
        reader: { id: readerId, display_name: "Acceptance Reader", version: 1 },
      });
      return;
    }
    if (pathname.endsWith("/capabilities/")) {
      await respond({
        article_public_id: articleId,
        active_release: staticReleaseVersion,
        comments_mode: "open",
        can_comment: true,
        verification_required: false,
        policy_version: 7,
      });
      return;
    }
    if (request.method() === "GET" && pathname.endsWith("/comments/")) {
      await respond(
        { items, next_cursor: null, etag: '"e2e-comments"' },
        200,
        { ETag: '"e2e-comments"' },
      );
      return;
    }
    if (request.method() === "POST" && pathname.endsWith("/comments/")) {
      const body = request.postDataJSON();
      const pending = {
        id: "66666666-6666-4666-8666-666666666666",
        parent_id: null,
        author: { id: readerId, display_name: "Acceptance Reader" },
        body: body.body,
        withdrawn: false,
        state: "pending",
        version: 1,
        created_at: now,
        updated_at: now,
        owned_by_viewer: true,
        pending_for_viewer: true,
        replies: [],
      };
      items = items.concat(pending);
      await respond(pending, 202);
      return;
    }
    if (pathname.endsWith("/reports/")) {
      await respond({ id: "77777777-7777-4777-8777-777777777777", status: "open" }, 201);
      return;
    }
    if (pathname.endsWith("/withdrawal/")) {
      items = items.map((item) =>
        item.id === "55555555-5555-4555-8555-555555555555"
          ? { ...item, body: null, withdrawn: true, state: "withdrawn", version: 2 }
          : item,
      );
      await respond(items[1]);
      return;
    }
    await route.fulfill({ status: 404, body: "{}" });
  });

  await page.goto(englishPath("/articles/static-acceptance-article/"), {
    waitUntil: "networkidle",
  });
  const comments = page.locator("#reader-interactions");
  await comments.scrollIntoViewIfNeeded();
  await expect(comments.getByText("Public Reader", { exact: true })).toBeVisible();
  await expect(comments.getByText("<img src=x", { exact: false })).toBeVisible();
  await expect(comments.locator("img")).toHaveCount(0);
  await expect(comments.locator('a[href="https://example.org"]')).toHaveCount(0);
  expect(await page.evaluate(() => window.commentXss)).toBeUndefined();

  await comments.getByRole("button", { name: "Reply" }).first().click();
  const reply = comments.getByLabel("Reply to Public Reader");
  await expect(reply).toBeFocused();
  await comments.getByRole("button", { name: "Cancel" }).click();

  await comments.getByRole("button", { name: "Report" }).click();
  await comments.getByRole("button", { name: "Submit report" }).click();
  await expect(comments.locator("[data-reader-interactions-status]")).toHaveText(
    "Report received.",
  );

  await comments.getByLabel("Add a comment").fill("Pending acceptance comment");
  await comments.getByRole("button", { name: "Submit" }).first().click();
  await expect(comments.getByText("Pending acceptance comment", { exact: true })).toBeVisible();
  await expect(comments.getByText("Awaiting review", { exact: true })).toBeVisible();

  await comments.getByRole("button", { name: "Withdraw" }).first().click();
  await expect(
    comments.getByText("This comment was withdrawn by its author.", { exact: true }),
  ).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport + 1);
});

async function installReaderActionApi(page, state) {
  await page.route("**/reader-api/v1/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const respond = (data, status = 200, headers = {}) =>
      route.fulfill({
        status,
        contentType: "application/json",
        headers,
        body: JSON.stringify({ data, request_id: "e2e-reader-actions" }),
      });

    if (pathname.endsWith("/session/")) {
      await respond(
        state.authenticated
          ? {
              authenticated: true,
              reader: {
                id: "22222222-2222-4222-8222-222222222222",
                display_name: "Acceptance Reader",
                version: 1,
              },
            }
          : { authenticated: false, verification_required: true },
      );
      return;
    }
    if (pathname.endsWith("/capabilities/")) {
      await respond({
        article_public_id: "11111111-1111-4111-8111-111111111111",
        active_release: staticReleaseVersion,
        comments_mode: state.commentsMode || "read_only",
        pdf_available: true,
        can_comment: state.authenticated,
        can_download: state.authenticated,
        share_available: true,
        can_share: state.authenticated,
        verification_required: !state.authenticated,
        policy_version: 7,
        applying: false,
        service_degraded: false,
      });
      return;
    }
    if (request.method() === "GET" && pathname.endsWith("/comments/")) {
      await respond({ items: [], next_cursor: null, etag: '"reader-actions"' });
      return;
    }
    if (request.method() === "POST" && pathname.endsWith("/share-events/")) {
      state.shareEvents.push(request.postDataJSON());
      await respond({ recorded: true, coalesced: false }, 202);
      return;
    }
    if (request.method() === "POST" && pathname.endsWith("/download-grants/")) {
      state.downloadRequests += 1;
      await respond({ download_url: "/reader-api/v1/downloads/fake/token/" }, 201);
      return;
    }
    if (request.method() === "POST" && pathname.endsWith("/email-verifications/")) {
      state.verificationRequests.push(request.postDataJSON());
      if (state.deviceFlow) {
        state.deviceFlow.statusCalls = 0;
        return respond({
          accepted: true,
          flow_id: "11111111-1111-4111-8111-111111111111",
          expires_in: 900,
          interval: 1,
        }, 202);
      }
      await respond({ accepted: true }, 202);
      return;
    }
    if (request.method() === "GET" && pathname.endsWith("/status/")) {
      if (!state.deviceFlow) {
        await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
        return;
      }
      state.deviceFlow.statusCalls += 1;
      await respond({
        status: state.deviceFlow.approved ? "approved" : "pending",
        retry_after: 1,
        expires_in: Math.max(0, 900 - state.deviceFlow.statusCalls),
      });
      return;
    }
    if (request.method() === "POST" && pathname.endsWith("/claim/")) {
      if (!state.deviceFlow) {
        await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
        return;
      }
      if (!state.deviceFlow.approved) {
        await route.fulfill({ status: 409, contentType: "application/json", body: "{}" });
        return;
      }
      state.deviceFlow.claims += 1;
      state.authenticated = true;
      await respond({
        status: "claimed",
        flow_id: "11111111-1111-4111-8111-111111111111",
        authenticated: true,
        already_claimed: false,
      }, 200, { "Set-Cookie": "reader_session=desktop-session; Path=/; HttpOnly; SameSite=Lax" });
      return;
    }
    if (request.method() === "POST" && pathname.endsWith("/comments/")) {
      state.commentRequests += 1;
      await respond({}, 201);
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });
}

test("reader actions use Web Share in the gesture and report outcomes after completion", async ({
  page,
}) => {
  const state = {
    authenticated: true,
    shareEvents: [],
    verificationRequests: [],
    downloadRequests: 0,
    commentRequests: 0,
  };
  await page.addInitScript(() => {
    window.shareCalls = [];
    window.shareMode = "completed";
    Object.defineProperty(navigator, "canShare", {
      configurable: true,
      value: () => true,
    });
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: (payload) => {
        window.shareCalls.push(payload);
        if (window.shareMode === "cancelled") {
          return Promise.reject(new DOMException("cancelled", "AbortError"));
        }
        return Promise.resolve();
      },
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.reject(new Error("clipboard denied")) },
    });
  });
  await installReaderActionApi(page, state);

  await page.goto(englishPath("/articles/static-acceptance-article/"), {
    waitUntil: "networkidle",
  });
  const actions = page.locator("#reader-interactions");
  await actions.scrollIntoViewIfNeeded();
  const share = actions.getByRole("button", { name: "Share", exact: true });
  await expect(share).toBeEnabled();
  await share.click();
  await expect(actions.locator("[data-reader-actions-status]")).toHaveText(
    "System share completed.",
  );
  await expect.poll(() => state.shareEvents.length).toBe(1);

  await page.evaluate(() => {
    window.shareMode = "cancelled";
  });
  await share.click();
  await expect(actions.locator("[data-reader-actions-status]")).toHaveText(
    "System share cancelled.",
  );
  await expect.poll(() => state.shareEvents.length).toBe(2);

  await actions.getByRole("button", { name: "Copy link" }).click();
  await expect(actions.locator("[data-reader-actions-status]")).toHaveText(
    "Link could not be copied.",
  );
  const urlFallback = actions.locator("[data-reader-share-url]");
  await expect(urlFallback).toBeVisible();
  await expect(urlFallback).toHaveAttribute("readonly", "");
  await expect(urlFallback).toHaveValue(/^https?:\/\//);
  await expect(urlFallback).toBeFocused();
  await expect.poll(() => state.shareEvents.length).toBe(3);
  expect(state.shareEvents).toEqual([
    { action: "system_share", outcome: "completed" },
    { action: "system_share", outcome: "cancelled" },
    { action: "copy_link", outcome: "failed" },
  ]);
  const calls = await page.evaluate(() => window.shareCalls);
  expect(Object.keys(calls[0]).sort()).toEqual(["title", "url"]);
  expect(calls[0].url).toMatch(/^https?:\/\//);
});

test("unsupported Web Share keeps copy fallback and responsive keyboard targets", async ({ page }) => {
  const state = {
    authenticated: true,
    shareEvents: [],
    verificationRequests: [],
    downloadRequests: 0,
    commentRequests: 0,
  };
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "share", { configurable: true, value: undefined });
    window.copiedUrls = [];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: (url) => {
          window.copiedUrls.push(url);
          return Promise.resolve();
        },
      },
    });
  });
  await installReaderActionApi(page, state);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(englishPath("/articles/static-acceptance-article/"), {
    waitUntil: "networkidle",
  });
  const actions = page.locator("#reader-interactions");
  await actions.scrollIntoViewIfNeeded();
  await expect(actions.getByRole("button", { name: "Share", exact: true })).toBeHidden();
  const copy = actions.getByRole("button", { name: "Copy link" });
  await expect(copy).toBeEnabled();
  await copy.click();
  await expect(actions.locator("[data-reader-actions-status]")).toHaveText("Link copied.");
  await expect.poll(() => state.shareEvents).toEqual([
    { action: "copy_link", outcome: "completed" },
  ]);
  expect(await page.evaluate(() => window.copiedUrls.length)).toBe(1);
  await copy.focus();
  await expect(copy).toBeFocused();
  const sizes = await actions.locator("button:visible").evaluateAll((buttons) =>
    buttons.map((button) => {
      const box = button.getBoundingClientRect();
      return { width: box.width, height: box.height };
    }),
  );
  expect(sizes.every(({ width, height }) => width >= 44 && height >= 44)).toBeTruthy();
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport + 1);
});

test("email link automatically approves desktop flow and restores the draft globally", async ({
  browser,
}) => {
  const desktopContext = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    userAgent: "reader-e2e-mobile",
  });
  const page = await desktopContext.newPage();
  const mobile = await mobileContext.newPage();
  const state = {
    authenticated: false,
    commentsMode: "open",
    shareEvents: [],
    verificationRequests: [],
    downloadRequests: 0,
    commentRequests: 0,
    deviceFlow: { approved: false, consumes: 0, statusCalls: 0, claims: 0 },
  };
  await installReaderActionApi(page, state);
  await page.goto(new URL(englishPath("/articles/static-acceptance-article/"), baseOrigin).href, {
    waitUntil: "networkidle",
  });
  const actions = page.locator("#reader-interactions");
  await actions.scrollIntoViewIfNeeded();
  const composer = actions.getByLabel("Add a comment");
  await composer.fill("Draft retained for explicit confirmation");
  await actions.getByRole("button", { name: "Submit" }).click();
  await expect(actions.locator("[data-reader-verification-email]")).toBeFocused();
  expect(state.commentRequests).toBe(0);

  await actions.locator("[data-reader-verification-email]").fill("reader@example.org");
  await expect(actions.locator("[data-reader-verification]")).toHaveAttribute("role", "dialog");
  await expect(actions.locator("[data-reader-verification-close]")).toBeVisible();
  expect(
    await actions.locator("[data-reader-verification]").evaluate((element) => getComputedStyle(element).position),
  ).toBe("fixed");
  await actions.getByRole("button", { name: "Send verification link" }).click();
  await expect.poll(() => state.verificationRequests.length).toBe(1);
  expect(state.verificationRequests[0].intent).toBe("comment");
  expect(state.verificationRequests[0].return_to).toMatch(
    /^\/en\/articles\/static-acceptance-article\/#reader-interactions$/,
  );

  await expect.poll(() => state.deviceFlow.statusCalls).toBeGreaterThan(0);
  expect(state.deviceFlow.claims).toBe(0);

  const challengeId = "99999999-9999-4999-8999-999999999999";
  const verificationPath = `/reader-api/v1/email-verifications/${challengeId}/`;
  const consumePath = `${verificationPath}consume/`;
  await mobile.route(`**${verificationPath}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      headers: { "Cache-Control": "no-store" },
      body: `<!doctype html>
        <html><head><meta charset="utf-8"><title>Confirm reader access</title></head>
        <body><main>
          <h1>Confirm reader access</h1>
          <p>Verification requested for <strong>r*****@example.org</strong>.</p>
          <p>Opening this link automatically unlocks the requesting computer.</p>
          <form method="post" action="${consumePath}" data-reader-verification-form>
            <input name="csrfmiddlewaretoken" value="mobile-csrf">
            <input type="hidden" name="token" data-reader-verification-token>
            <button type="submit" data-reader-verification-submit>Continue verification</button>
          </form>
          <p data-reader-verification-status aria-live="polite" hidden></p>
          <script src="/static/reader_interactions/verify.js" defer></script>
        </main></body></html>`,
    });
  });
  await mobile.route(`**${consumePath}`, async (route) => {
    const body = route.request().postDataJSON();
    expect(body.token).toBe("mobile-email-token");
    expect(body.user_code).toBeUndefined();
    state.deviceFlow.consumes += 1;
    state.deviceFlow.approved = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: {
        "Cache-Control": "no-store",
        "Set-Cookie": "reader_session=mobile-session; Path=/; HttpOnly; SameSite=Lax",
      },
      body: JSON.stringify({
        data: {
          paired: true,
          return_to: "/en/articles/static-acceptance-article/#reader-interactions",
        },
        request_id: "e2e-mobile-consume",
      }),
    });
  });
  await mobile.goto(new URL(`${verificationPath}#token=mobile-email-token`, baseOrigin).href, {
    waitUntil: "networkidle",
  });
  await expect.poll(() => state.deviceFlow.consumes).toBe(1);
  await expect(mobile).toHaveURL(/\/en\/articles\/static-acceptance-article\/#reader-interactions$/);

  await expect.poll(() => state.deviceFlow.claims).toBe(1);
  const restored = page.locator("#reader-interactions").getByLabel("Add a comment");
  await expect(restored).toHaveValue("Draft retained for explicit confirmation");
  await expect(restored).toBeFocused();
  expect(state.commentRequests).toBe(0);
  expect(state.downloadRequests).toBe(0);
  expect(state.shareEvents).toEqual([]);

  await actions.getByRole("button", { name: "Submit" }).click();
  await expect.poll(() => state.commentRequests).toBe(1);

  await page.goto(new URL(englishPath(acceptance.second_article_path), baseOrigin).href, {
    waitUntil: "networkidle",
  });
  const secondArticleInteractions = page.locator("#reader-interactions");
  await secondArticleInteractions.scrollIntoViewIfNeeded();
  await expect(secondArticleInteractions.getByLabel("Add a comment")).toBeVisible();
  await expect(secondArticleInteractions.locator("[data-reader-verification]")).toBeHidden();

  const articleId = await page.locator("#reader-interactions").getAttribute("data-article-id");
  await page.evaluate((id) => {
    sessionStorage.setItem(
      `reader-interactions:pending-intent:${id}:none`,
      JSON.stringify({
        articleId: id,
        action: "download",
        flowId: "",
        email: "",
        expiresAt: Date.now() + 60_000,
      }),
    );
  }, articleId);
  await page.reload({ waitUntil: "networkidle" });
  await expect(
    page.locator("#reader-interactions").getByRole("button", { name: "Download PDF" }),
  ).toBeFocused();
  expect(state.downloadRequests).toBe(0);
  expect(state.commentRequests).toBe(1);

  const desktopSession = (await desktopContext.cookies()).find(
    (cookie) => cookie.name === "reader_session",
  );
  const mobileSession = (await mobileContext.cookies()).find(
    (cookie) => cookie.name === "reader_session",
  );
  expect(desktopSession?.value).toBe("desktop-session");
  expect(mobileSession?.value).toBe("mobile-session");
  expect(desktopSession?.value).not.toBe(mobileSession?.value);
  await desktopContext.close();
  await mobileContext.close();
});

test("CSP, no-JavaScript, and API failure keep the static article readable", async ({
  browser,
  page,
}) => {
  const response = await page.goto(englishPath("/articles/static-acceptance-article/"));
  expect(response.headers()["content-security-policy"]).toContain("script-src 'self'");
  expect(response.headers()["content-security-policy"]).toContain("connect-src 'self'");

  const noScript = await browser.newContext({ javaScriptEnabled: false });
  const noScriptPage = await noScript.newPage();
  await noScriptPage.goto(new URL(englishPath("/articles/static-acceptance-article/"), baseOrigin).href);
  await expect(noScriptPage.getByRole("heading", { level: 1 })).toContainText(
    "Static acceptance article",
  );
  await expect(noScriptPage.locator(".c-article-body")).toContainText(
    "Static acceptance article body",
  );
  expect(
    await noScriptPage
      .locator("#reader-interactions .c-reader-interactions__actions button")
      .evaluateAll((buttons) => buttons.every((button) => button.disabled)),
  ).toBeTruthy();
  await expect(noScriptPage.locator("[data-reader-verification]")).toBeHidden();
  await noScript.close();

  await page.route("**/reader-api/v1/**", (route) => route.abort("failed"));
  await page.reload({ waitUntil: "domcontentloaded" });
  const articleBody = page.locator(".c-article-body");
  const interactions = page.locator("#reader-interactions");
  await interactions.scrollIntoViewIfNeeded();
  await expect(articleBody).toContainText("Static acceptance article body");
  await expect(interactions.locator("[data-reader-actions-status]")).toHaveText(
    "Reader actions are temporarily unavailable.",
  );
  expect(
    await interactions
      .locator(".c-reader-interactions__actions button")
      .evaluateAll((buttons) => buttons.every((button) => button.disabled)),
  ).toBeTruthy();
  await expect(interactions.locator("[data-reader-verification]")).toBeHidden();
});
