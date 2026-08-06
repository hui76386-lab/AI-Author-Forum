const fs = require("node:fs");
const path = require("node:path");
const { test, expect } = require("@playwright/test");

const baseOrigin = new URL(
  process.env.STATIC_SITE_URL || "http://127.0.0.1:4173",
).origin;

const acceptance = JSON.parse(
  fs.readFileSync(path.join(__dirname, "../../.e2e/acceptance.json"), "utf8"),
);

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
    title: /Content unavailable in English/,
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

  await page.goto(englishPath("/articles/static-acceptance-article/"), {
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
