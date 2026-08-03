(function () {
  document.documentElement.classList.add("has-js");

  const header = document.querySelector(".c-header");
  const primaryNavigation = document.querySelector("#primary-navigation");
  const primaryNavigationToggle = document.querySelector("[data-primary-nav-toggle]");
  const navButtons = Array.from(document.querySelectorAll("[data-nav-button]"));
  const searchButton = document.querySelector("[data-search-toggle]");
  const searchPanel = document.querySelector("#site-search-panel");

  function closeNavigation() {
    navButtons.forEach((button) => {
      const menu = document.querySelector(`#${button.getAttribute("aria-controls")}`);
      button.setAttribute("aria-expanded", "false");
      if (menu) menu.hidden = true;
    });
  }

  function closeSearch() {
    if (!searchButton || !searchPanel) return;
    searchButton.setAttribute("aria-expanded", "false");
    searchPanel.hidden = true;
  }

  function closePrimaryNavigation() {
    if (!primaryNavigation || !primaryNavigationToggle) return;
    primaryNavigationToggle.setAttribute("aria-expanded", "false");
    primaryNavigation.classList.remove("is-mobile-open");
    closeNavigation();
  }

  if (primaryNavigation && primaryNavigationToggle) {
    primaryNavigationToggle.addEventListener("click", () => {
      const willOpen = primaryNavigationToggle.getAttribute("aria-expanded") !== "true";
      closeSearch();
      primaryNavigationToggle.setAttribute("aria-expanded", String(willOpen));
      primaryNavigation.classList.toggle("is-mobile-open", willOpen);
      if (!willOpen) closeNavigation();
    });

    window.addEventListener("resize", () => {
      if (window.matchMedia("(min-width: 768px)").matches) {
        closePrimaryNavigation();
      }
    });
  }

  navButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const menu = document.querySelector(`#${button.getAttribute("aria-controls")}`);
      const willOpen = button.getAttribute("aria-expanded") !== "true";
      closeNavigation();
      closeSearch();
      button.setAttribute("aria-expanded", String(willOpen));
      if (menu) menu.hidden = !willOpen;
    });
  });

  if (searchButton && searchPanel) {
    searchButton.addEventListener("click", () => {
      const willOpen = searchButton.getAttribute("aria-expanded") !== "true";
      closeNavigation();
      searchButton.setAttribute("aria-expanded", String(willOpen));
      searchPanel.hidden = !willOpen;
      if (willOpen) {
        const input = searchPanel.querySelector("input[name='q']");
        if (input) input.focus();
      }
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closePrimaryNavigation();
      closeNavigation();
      closeSearch();
    }
  });

  document.addEventListener("pointerdown", (event) => {
    if (header && !header.contains(event.target)) {
      closePrimaryNavigation();
      closeNavigation();
      closeSearch();
    }
  });

  document.querySelectorAll("[data-static-filter-select]").forEach((select) => {
    select.addEventListener("change", () => {
      if (select.value) window.location.assign(select.value);
    });
  });

  document.querySelectorAll("[data-card-link]").forEach((card) => {
    card.addEventListener("click", (event) => {
      if (event.target.closest("a, button, input, select, textarea")) return;
      window.location.href = card.dataset.cardLink;
    });

    card.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (event.target.closest("a, button, input, select, textarea")) return;
      event.preventDefault();
      window.location.href = card.dataset.cardLink;
    });
  });


  function isEnglishPage() {
    return String(document.documentElement.lang || "").toLowerCase().startsWith("en");
  }

  function localizedPublicUrl(url) {
    if (!url) return url;
    const value = String(url);
    if (/^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith("#")) return value;
    if (isEnglishPage()) {
      if (value === "/") return "/en/";
      if (value === "/en" || value.startsWith("/en/")) return value;
      if (value.startsWith("/")) return `/en${value}`;
    }
    const stripped = value.replace(/^\/en(?=\/|$)/, "") || "/";
    return stripped;
  }

  function formatTemplate(template, values) {
    return String(template || "").replace(/\{(count|query)\}/g, (_match, key) => values[key] ?? "");
  }

  const searchPage = document.querySelector("[data-search-page]");
  if (searchPage) {
    hydrateSearchPage(searchPage);
  }

  function hydrateSearchPage(page) {
    const params = new URLSearchParams(window.location.search);
    const queryValue = params.get("q") || params.get("query") || "";
    const values = {
      q: queryValue,
      subject: params.get("subject") || "",
      author: params.get("author") || "",
      aiAuthor: params.get("aiAuthor") || "",
      journal: params.get("journal") || "",
    };

    Object.entries(values).forEach(([field, value]) => {
      page.querySelectorAll(`[name='${field}']`).forEach((input) => {
        input.value = value;
      });
    });

    const advanced = page.querySelector(".c-search-advanced");
    if (advanced && (values.subject || values.author || values.aiAuthor)) {
      advanced.open = true;
    }

    const normalize = (value) =>
      String(value || "")
        .normalize("NFKD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .trim();
    const queryTokens = normalize(values.q).split(/\s+/).filter(Boolean);
    const subject = normalize(values.subject);
    const author = normalize(values.author);
    const aiAuthor = normalize(values.aiAuthor);
    const journal = normalize(values.journal);
    const hasFilters = Boolean(
      queryTokens.length || subject || author || aiAuthor || journal,
    );

    const indexNode = document.getElementById("static-search-index");
    const resultList = page.querySelector("[data-search-results]");
    const countNode = page.querySelector("[data-result-count]");
    const emptyNode = page.querySelector("[data-empty-state]");
    const recommendations = page.querySelector("[data-search-recommendations]");
    const clearLink = page.querySelector("[data-search-clear]");
    let entries = [];

    try {
      entries = indexNode ? JSON.parse(indexNode.textContent) : [];
    } catch (_error) {
      if (countNode) {
        countNode.textContent = page.dataset.searchIndexUnavailable || "Search index is unavailable";
      }
      if (emptyNode) {
        emptyNode.textContent = page.dataset.searchIndexError || "The static search index could not be loaded.";
        emptyNode.hidden = false;
      }
      return;
    }

    if (clearLink) clearLink.hidden = !hasFilters;
    if (recommendations) recommendations.hidden = hasFilters;

    if (!hasFilters) {
      if (resultList) {
        resultList.hidden = true;
        resultList.replaceChildren();
      }
      if (emptyNode) emptyNode.hidden = true;
      if (countNode) {
        const unit = entries.length === 1
          ? page.dataset.searchableSingular || "searchable article"
          : page.dataset.searchablePlural || "searchable articles";
        countNode.textContent = `${entries.length} ${unit}`;
      }
      return;
    }

    const matches = entries.filter((entry) => {
      const searchableText = normalize(
        [
          entry.title,
          entry.summary,
          entry.article_type,
          entry.journal,
          entry.authors,
          entry.ai_authors,
          entry.keywords,
        ].join(" "),
      );
      const entryJournal = normalize(entry.journal);
      const entryJournalSlug = normalize(entry.journal_slug);
      return (
        queryTokens.every((token) => searchableText.includes(token)) &&
        (!subject || normalize(entry.keywords).includes(subject)) &&
        (!author || normalize(entry.authors).includes(author)) &&
        (!aiAuthor || normalize(entry.ai_authors).includes(aiAuthor)) &&
        (!journal || journal === entryJournal || journal === entryJournalSlug)
      );
    });

    if (resultList) {
      const fragment = document.createDocumentFragment();
      matches.forEach((entry) => {
        const article = document.createElement("article");
        article.className = "c-search-result-item";

        const type = document.createElement("span");
        type.textContent = entry.article_type || "Article";
        article.append(type);

        const heading = document.createElement("h2");
        const link = document.createElement("a");
        link.href = localizedPublicUrl(entry.url);
        link.textContent = entry.title;
        heading.append(link);
        article.append(heading);

        if (entry.summary) {
          const summary = document.createElement("p");
          summary.textContent = entry.summary;
          article.append(summary);
        }

        const metadata = document.createElement("p");
        metadata.className = "c-search-result-item__metadata";
        metadata.textContent = [entry.journal, entry.authors]
          .filter(Boolean)
          .join(" - ");
        article.append(metadata);
        fragment.append(article);
      });
      resultList.replaceChildren(fragment);
      resultList.hidden = matches.length === 0;
    }

    if (countNode) {
      const template = values.q
        ? page.dataset.resultForTemplate || '{count} result(s) for "{query}"'
        : page.dataset.resultCountTemplate || "{count} result(s)";
      countNode.textContent = formatTemplate(template, {
        count: matches.length,
        query: values.q,
      });
    }
    if (emptyNode) emptyNode.hidden = matches.length !== 0;
  }
})();
