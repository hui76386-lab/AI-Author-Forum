from __future__ import annotations

from collections import defaultdict

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import Http404
from django.utils.text import slugify

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.articles.services import (
    get_approved_articles,
    get_article_context,
)
from ai_author_forum.journals.catalog import group_journals_by_discipline
from ai_author_forum.journals.models import (
    Journal,
    PublicationIssue,
    PublicationIssueScope,
    PublicationIssueStatus,
)
from ai_author_forum.journals.services import get_active_journals, get_journal_context
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.placements.services import get_slot_items
from ai_author_forum.site_settings.models import (
    ContentColumnConfig,
    NavigationEntryStatus,
    NavigationItem,
    NavigationSetStatus,
    NavigationTargetType,
)
from ai_author_forum.site_settings.navigation import (
    get_active_navigation_set,
    get_navigation_context,
)
from ai_author_forum.utils.i18n import article_type_label, localized_journal_name

DEFAULT_STATIC_SECTIONS = (
    {
        "slug": "ai-article",
        "title": "AI Article",
        "description": "Selected articles about AI-assisted research, writing, and publishing.",
        "intro_title": "Editorial scope",
        "intro_body": (
            "This static channel collects AI-authored and AI-assisted articles after "
            "editorial approval and placement. Placeholder cards model how readers "
            "will browse titles, summaries, author responsibility notes, journals, "
            "keywords, and fixed article-detail links."
        ),
        "highlights": (
            "AI co-author disclosure and contribution statements",
            "Responsible human oversight for AI-assisted writing",
            "Fixed HTML article details generated after publishing approval",
        ),
    },
    {
        "slug": "news",
        "title": "News",
        "description": "Updates from the AI authorship and scholarly publishing community.",
        "intro_title": "News coverage",
        "intro_body": (
            "Use this area for platform announcements, editorial updates, policy "
            "developments, and community news related to AI authorship."
        ),
        "highlights": (
            "Forum announcements and release notes",
            "Publishing policy and standards updates",
            "Community milestones from participating journals",
        ),
    },
    {
        "slug": "opinion",
        "title": "Opinion",
        "description": "Commentary and perspectives on responsible AI authorship.",
        "intro_title": "Commentary focus",
        "intro_body": (
            "Opinion pages present editorials, invited perspectives, and expert "
            "commentary. All copy shown here is placeholder content until the "
            "editorial team supplies final text."
        ),
        "highlights": (
            "Editorial perspectives on AI co-authorship",
            "Ethics commentary from reviewers and researchers",
            "Reader guidance for interpreting AI-authored work",
        ),
    },
    {
        "slug": "research-analysis",
        "title": "Research Analysis",
        "description": "Evidence-led analysis of AI, authorship, and research practice.",
        "intro_title": "Analysis scope",
        "intro_body": (
            "This channel is reserved for deeper research analysis, evidence summaries, "
            "case studies, metrics interpretation, and cross-forum trend reports."
        ),
        "highlights": (
            "Data-led reports on AI use in scholarly production",
            "Cross-journal comparisons and responsible practice cases",
            "Methods notes that explain how evidence was assessed",
        ),
    },
    {
        "slug": "careers",
        "title": "Careers",
        "description": "Placeholder career guidance for AI-enabled scholarship and editorial work.",
        "intro_title": "Careers placeholder",
        "intro_body": (
            "This future content area can introduce editorial roles, reviewer training, "
            "AI research-support careers, and skills needed for responsible AI authorship."
        ),
        "highlights": (
            "Editorial and reviewer capability building",
            "Responsible AI writing skills and training paths",
            "Future calls for contributors or forum collaborators",
        ),
    },
    {
        "slug": "books-and-culture",
        "title": "Books & Culture",
        "description": "Placeholder coverage for books, culture, and public discussion of AI authorship.",
        "intro_title": "Books and culture placeholder",
        "intro_body": (
            "This page can later feature book reviews, culture essays, reading lists, "
            "and public-interest discussions about AI in knowledge creation."
        ),
        "highlights": (
            "Book review and reading-list placeholders",
            "Culture essays about human-AI collaboration",
            "Public communication examples for AI-authored research",
        ),
    },
    {
        "slug": "podcasts",
        "title": "Podcasts",
        "description": "Placeholder audio programme listings for AI Author Forum.",
        "intro_title": "Podcast placeholder",
        "intro_body": (
            "This page reserves a static listing format for future podcast episodes, "
            "editor interviews, and short audio explainers."
        ),
        "highlights": (
            "Episode title, guest, date, and summary slots",
            "Editorial interviews on AI-authored articles",
            "Responsible authorship explainer series",
        ),
    },
    {
        "slug": "videos",
        "title": "Videos",
        "description": "Placeholder video listings and visual explainers.",
        "intro_title": "Video placeholder",
        "intro_body": (
            "This page reserves a static format for future video abstracts, forum "
            "briefings, policy explainers, and editorial training materials."
        ),
        "highlights": (
            "Video abstract card layout",
            "Forum briefings and explainer placeholders",
            "Editorial training material slots",
        ),
    },
    {
        "slug": "current-issue",
        "title": "Current issue",
        "description": "A static placeholder for the latest main-site issue collection.",
        "intro_title": "Current issue placeholder",
        "intro_body": (
            "The main-site current issue page can collect the latest fixed HTML articles "
            "selected by editors. Individual journal issue pages remain under each journal."
        ),
        "highlights": (
            "Latest selected articles",
            "Issue-level editorial introduction",
            "Links to journal-specific current issues",
        ),
    },
    {
        "slug": "browse-issues",
        "title": "Browse issues",
        "description": "A static placeholder for browsing previous main-site issue collections.",
        "intro_title": "Issue archive placeholder",
        "intro_body": (
            "This future archive page can group static issue collections by year and month "
            "without requiring a runtime article database query."
        ),
        "highlights": (
            "Year and issue grouping placeholders",
            "Static archive links",
            "Editorial collection summaries",
        ),
    },
)

DEFAULT_INFO_PAGES = (
    {
        "group": "About the forum",
        "group_slug": "about-the-forum",
        "pages": (
            {
                "slug": "forum-staff",
                "title": "Forum Staff",
                "summary": "People and operating roles behind AI Author Forum.",
                "body": "Placeholder profiles for editorial operations, platform coordination, journal onboarding, and publication support.",
                "sections": (
                    "Leadership and forum operations",
                    "Editorial support and production contacts",
                    "Journal onboarding and content coordination",
                ),
            },
            {
                "slug": "about-the-editors",
                "title": "About the Editors",
                "summary": "Editorial responsibilities, review coordination, and decision boundaries.",
                "body": "This page will describe editor selection, scope assignment, review oversight, and conflict-of-interest handling.",
                "sections": (
                    "Editor responsibilities",
                    "Review oversight and approval route",
                    "Editorial independence and accountability",
                ),
            },
            {
                "slug": "research-cross-forum-editorial-team",
                "title": "Research Cross-Forum Editorial Team",
                "summary": "Cross-forum coordination for research-facing AI authorship content.",
                "body": "Placeholder copy for the team that coordinates standards and editorial consistency across all participating journals.",
                "sections": (
                    "Cross-journal standards alignment",
                    "Research category coordination",
                    "Escalation route for complex editorial decisions",
                ),
            },
            {
                "slug": "forum-information",
                "title": "Forum Information",
                "summary": "Mission, site scope, supported content types, and forum structure.",
                "body": "AI Author Forum is modelled as one main site plus many journal homes, using unified templates and fixed static HTML output.",
                "sections": (
                    "One main site plus 120 journal-ready homes",
                    "Unified navigation and Nature-style page layout",
                    "Static front-end publishing model",
                ),
            },
            {
                "slug": "forum-metrics",
                "title": "Forum Metrics",
                "summary": "Placeholder for article, journal, publishing, and readership indicators.",
                "body": "Metrics shown here should be editorially reviewed snapshots rather than live database searches in this release.",
                "sections": (
                    "Published AI article count",
                    "Active journal count and A-Z coverage",
                    "Static release and publication health indicators",
                ),
            },
            {
                "slug": "our-publishing-models",
                "title": "Our publishing models",
                "summary": "How AI-authored and AI-assisted content moves from draft to static publication.",
                "body": "This placeholder explains draft creation, review, approval, placement, static build, retry, and rollback concepts.",
                "sections": (
                    "Reviewed article publication",
                    "Editorially curated channel placement",
                    "Static HTML release with manifest and audit trail",
                ),
            },
            {
                "slug": "editorial-values-statement",
                "title": "Editorial Values Statement",
                "summary": "Values for transparency, responsibility, integrity, and reader clarity.",
                "body": "Final copy should state how human editors remain accountable for AI-assisted publication decisions.",
                "sections": (
                    "Transparency about AI contribution",
                    "Responsible human oversight",
                    "Reader-first clarity and evidence quality",
                ),
            },
            {
                "slug": "editorial-policies",
                "title": "Editorial policies",
                "summary": "Placeholder editorial rules for submission, review, AI disclosure, correction, and withdrawal.",
                "body": "This static policy page can later be maintained by approved administrators through controlled rich text.",
                "sections": (
                    "Submission and AI-use disclosure",
                    "Review, correction, and retraction handling",
                    "Image, data, and citation integrity",
                ),
            },
            {
                "slug": "journalistic-principles",
                "title": "Journalistic Principles",
                "summary": "Editorial principles for news, commentary, and public-facing explainers.",
                "body": "Placeholder guidance for accuracy, source handling, labelling, and separation of news from opinion.",
                "sections": (
                    "Accuracy and source transparency",
                    "Clear labelling of opinion and analysis",
                    "Corrections and reader trust",
                ),
            },
            {
                "slug": "development-of-the-forum",
                "title": "Development of the Forum",
                "summary": "Timeline placeholder for how AI Author Forum develops over time.",
                "body": "This page replaces history-oriented legacy journal copy with forum-specific milestones and release notes.",
                "sections": (
                    "Founding purpose and roadmap",
                    "Journal onboarding milestones",
                    "CMS and static publishing release history",
                ),
            },
            {
                "slug": "awards",
                "title": "Awards",
                "summary": "Placeholder for recognitions, awards, certifications, or community acknowledgements.",
                "body": "No awards are configured yet. This page reserves a structured area for future verified entries.",
                "sections": (
                    "Award name and issuing organisation",
                    "Date and evidence link",
                    "Related journal or article collection",
                ),
            },
            {
                "slug": "contact",
                "title": "Contact",
                "summary": "Contact routes for editorial, publishing, and operational questions.",
                "body": "Placeholder contact details can include editorial office email, support process, and response-time notes.",
                "sections": (
                    "Editorial enquiries",
                    "Journal onboarding questions",
                    "Static publishing or content correction requests",
                ),
            },
        ),
    },
    {
        "group": "Co authoring with AI",
        "group_slug": "co-authoring-with-ai",
        "pages": (
            {
                "slug": "definition-of-a-co-author-to-the-ai",
                "title": "Definition of a co author to the AI",
                "summary": "A placeholder definition page for AI co-author roles and boundaries.",
                "body": "Final policy copy should distinguish tool assistance, AI co-author labelling, human accountability, and disclosure requirements.",
                "sections": (
                    "What counts as AI co-author contribution",
                    "What remains human author responsibility",
                    "Disclosure wording and metadata expectations",
                ),
            },
            {
                "slug": "responsibility-of-the-co-author",
                "title": "Responsibility of the Co author",
                "summary": "Responsibilities for human co-authors when AI contributes to scholarly work.",
                "body": "This placeholder explains that humans remain accountable for accuracy, ethics, originality, citation quality, and reader guidance.",
                "sections": (
                    "Human verification of AI-assisted content",
                    "Ethics, data, image, and citation checks",
                    "Correction and accountability after publication",
                ),
            },
        ),
    },
    {
        "group": "For readers",
        "group_slug": "for-readers",
        "pages": (
            {
                "slug": "how-ai-authored-articles-produced",
                "title": "How AI authored Articles produced",
                "summary": "Reader-facing explanation of how AI-authored articles are produced and reviewed.",
                "body": "Placeholder content for the editorial workflow from draft input through review, placement, and static publication.",
                "sections": (
                    "Draft creation and AI assistance",
                    "Editorial review and approval",
                    "Placement before fixed HTML publication",
                ),
            },
            {
                "slug": "readers-responsibility",
                "title": "Readers responsibility",
                "summary": "Guidance for readers interpreting AI-authored and AI-assisted articles.",
                "body": "This page can explain how readers should interpret disclosures, check sources, and report concerns or corrections.",
                "sections": (
                    "Read AI contribution labels carefully",
                    "Evaluate evidence and citations",
                    "Report corrections or integrity concerns",
                ),
            },
        ),
    },
)


DEFAULT_STATIC_SEARCH = {
    "title": "Search AI Author Forum",
    "introduction": (
        "Search approved, currently placed articles by title, summary, author, "
        "AI co-author, keyword, article type, or journal. Search runs entirely "
        "from the static article index generated during publishing."
    ),
    "empty_message": "No searchable articles are currently available.",
    "keywords": (
        "AI authorship",
        "research integrity",
        "scholarly publishing",
        "responsible AI",
    ),
}


def get_static_sections():
    configured = getattr(settings, "STATIC_PUBLISH_SECTIONS", DEFAULT_STATIC_SECTIONS)
    sections = []
    seen = set()
    for value in configured:
        section = dict(value)
        slug = str(section.get("slug", "")).strip().strip("/")
        if not slug or slug in seen:
            continue
        section["slug"] = slug
        section.setdefault("title", slug.replace("-", " ").title())
        section.setdefault("description", "")
        sections.append(section)
        seen.add(slug)
    return tuple(sections)


def get_section_definition(slug):
    normalized = str(slug or "").strip().strip("/")
    for section in get_static_sections():
        if section["slug"] == normalized:
            return section
    raise Http404("Unknown section")


def get_static_info_pages():
    pages = []
    seen = set()
    for group in getattr(settings, "STATIC_INFO_PAGES", DEFAULT_INFO_PAGES):
        group_slug = str(group.get("group_slug", "")).strip().strip("/")
        if not group_slug:
            continue
        for value in group.get("pages", ()):
            page = dict(value)
            slug = str(page.get("slug", "")).strip().strip("/")
            key = (group_slug, slug)
            if not slug or key in seen:
                continue
            page.setdefault("title", slug.replace("-", " ").title())
            page.setdefault("summary", "Content pending final editorial copy.")
            page.setdefault(
                "body",
                "This page is a managed placeholder for future rich text content.",
            )
            page.setdefault("sections", ())
            page["group"] = group.get("group", group_slug.replace("-", " ").title())
            page["group_slug"] = group_slug
            page["path"] = f"/{group_slug}/{slug}/"
            pages.append(page)
            seen.add(key)
    return tuple(pages)


def get_static_info_page_definition(group_slug, slug):
    normalized_group = str(group_slug or "").strip().strip("/")
    normalized_slug = str(slug or "").strip().strip("/")
    for page in get_static_info_pages():
        if page["group_slug"] == normalized_group and page["slug"] == normalized_slug:
            return page
    raise Http404("Unknown information page")


def _main_navigation(current_path=""):
    return get_navigation_context(current_path=current_path, strict=True)


def _journal_navigation(journal, current_path=""):
    return get_navigation_context(
        journal=journal,
        current_path=current_path,
        strict=True,
    )


def get_journal_index_context():
    journals = list(get_active_journals())
    return {
        "journals": journals,
        "journal_groups": group_journals_by_discipline(journals),
        "page_title": "Journals by discipline",
        "managed_navigation": _main_navigation("/journals/"),
    }


def get_journal_page_context(slug, at=None):
    try:
        context = get_journal_context(slug, at=at)
    except Journal.DoesNotExist as exc:
        raise Http404("Journal not found") from exc

    journal = context["journal"]
    target = ArticlePlacement.TargetType.JOURNAL
    highlighted_placements = list(
        get_slot_items("journal_highlights", target, slug, at=at)
    )
    featured_placements = list(get_slot_items("journal_featured", target, slug, at=at))
    context.update(
        {
            "hero_placements": list(
                get_slot_items("journal_hero", target, slug, at=at)
            ),
            "featured_placements": _deduplicate_placements(
                highlighted_placements,
                featured_placements,
            ),
            "latest_placements": list(
                get_slot_items("journal_latest", target, slug, at=at)
            ),
            "managed_navigation": _journal_navigation(
                journal, f"/journals/{journal.slug}/"
            ),
        }
    )
    return context


def _deduplicate_placements(*groups):
    placements = []
    seen_article_ids = set()
    for group in groups:
        for placement in group:
            if placement.article_id in seen_article_ids:
                continue
            placements.append(placement)
            seen_article_ids.add(placement.article_id)
    return placements


def get_section_page_context(slug, at=None):
    section = get_section_definition(slug)
    target = ArticlePlacement.TargetType.SECTION
    current_path = f"/explore-content/{section['slug']}/"
    return {
        "section": section,
        "page_title": section["title"],
        "top_story_placements": list(
            get_slot_items("section_top_story", target, section["slug"], at=at)
        ),
        "article_placements": list(
            get_slot_items("section_article_list", target, section["slug"], at=at)
        ),
        "sidebar_placements": list(
            get_slot_items("section_sidebar", target, section["slug"], at=at)
        ),
        "managed_navigation": _main_navigation(current_path),
    }


def get_static_info_page_context(group_slug, slug):
    page = get_static_info_page_definition(group_slug, slug)
    return {
        "info_page": page,
        "page_title": page["title"],
        "managed_navigation": _main_navigation(page["path"]),
    }


def get_managed_navigation_info_context(*, internal_path, journal_slug=None):
    """Return controlled placeholder content for a managed internal navigation URL."""
    journal = None
    if journal_slug:
        try:
            journal = get_active_journals().get(slug=journal_slug)
        except Journal.DoesNotExist as exc:
            raise Http404("Journal not found") from exc
        path = f"/journals/{journal.slug}/{str(internal_path).strip('/')}/"
    else:
        path = f"/{str(internal_path).strip('/')}/"

    item = (
        NavigationItem.objects.filter(
            group__navigation_set__status=NavigationSetStatus.ACTIVE,
            group__navigation_set__journal=journal,
            target_type=NavigationTargetType.INTERNAL_PATH,
            internal_path=path,
            is_active=True,
            status__in=(NavigationEntryStatus.ACTIVE, NavigationEntryStatus.HIDDEN),
        )
        .select_related("group")
        .order_by("group__navigation_set__is_template", "pk")
        .first()
    )
    if item is None or (not item.is_visible and not item.allow_direct_access):
        raise Http404("Managed navigation page not found")

    page_title = item.label
    scope_label = "AI Author Forum"
    summary = "Controlled information for this navigation destination."
    body = (
        "This fixed page is generated from the active navigation configuration and "
        "is ready for approved editorial copy."
    )
    sections = ("Editorially approved information", "Static publishing and audit trail")
    if journal:
        journal_name = localized_journal_name(journal)
        page_title = f"{journal_name}: {item.label}"
        scope_label = journal_name
        summary = journal.seo_description or (
            f"Information and resources for {journal_name}."
        )
        body = (
            "This journal-specific page is generated from its active managed "
            "navigation and can be updated through the controlled editorial workflow."
        )
        sections = (
            "Journal scope and editorial focus",
            "Publication resources and current content",
            "Editorial contact and publishing workflow",
        )

    code = item.managed_code
    if code == "contact":
        summary = (
            f"Contact routes for {scope_label} editorial and publishing questions."
        )
        sections = (
            "Editorial enquiries",
            "Journal onboarding and publishing support",
            "Static publishing or content correction requests",
        )
    elif code == "author-guidelines":
        summary = f"Author guidance and submission expectations for {scope_label}."
        sections = (
            "Submission and AI-use disclosure",
            "Editorial review and approval route",
            "Corrections, image, and citation integrity",
        )

    info_page = {
        "group": item.group.label,
        "group_slug": f"managed-navigation-{item.group_id}",
        "slug": item.managed_code,
        "path": path,
        "title": page_title,
        "summary": summary,
        "body": body,
        "sections": sections,
    }
    return {
        "info_page": info_page,
        "page_title": page_title,
        "journal": journal,
        "managed_navigation": (
            _journal_navigation(journal, path) if journal else _main_navigation(path)
        ),
    }


def _get_content_column_item(*, column_slug, journal=None):
    try:
        nav_set = get_active_navigation_set(journal=journal, strict=True)
    except ValidationError as exc:
        raise Http404(str(exc)) from exc
    item = (
        NavigationItem.objects.select_related(
            "group__navigation_set",
            "category",
            "content_column_config",
            "content_column_config__cover_image",
            "content_column_config__category",
        )
        .filter(
            group__navigation_set=nav_set,
            code=column_slug,
            target_type=NavigationTargetType.CONTENT_COLUMN,
            is_active=True,
        )
        .first()
    )
    if item is None:
        raise Http404("Content column not found")
    if item.status == NavigationEntryStatus.ARCHIVED:
        raise Http404("Content column is archived")
    if (
        item.status == NavigationEntryStatus.HIDDEN or not item.is_visible
    ) and not item.allow_direct_access:
        raise Http404("Content column is hidden")
    try:
        config = item.content_column_config
    except ContentColumnConfig.DoesNotExist as exc:
        raise Http404("Content column configuration is missing") from exc
    return item, config


def _column_filter_path(item, *, article_type="", year=None, page_number=1):
    path = item.target_url
    if article_type:
        path += f"type/{slugify(article_type)}/"
    if year:
        path += f"year/{int(year)}/"
    if page_number > 1:
        path += f"page/{page_number}/"
    return path


def _filter_column_placements(placements, *, article_type="", year=None):
    result = []
    for placement in placements:
        article = placement.article
        published_at = article.first_published_at
        if article_type and slugify(article.article_type) != article_type:
            continue
        if year and (published_at is None or published_at.year != int(year)):
            continue
        result.append(placement)
    return result


def get_content_column_context(
    *,
    column_slug,
    journal_slug=None,
    page_number=1,
    article_type="",
    year=None,
    at=None,
):
    journal = None
    if journal_slug:
        try:
            journal = get_active_journals().get(slug=journal_slug)
        except Journal.DoesNotExist as exc:
            raise Http404("Journal not found") from exc
    item, config = _get_content_column_item(column_slug=column_slug, journal=journal)
    if article_type and not config.enable_type_filter:
        raise Http404("Article type filtering is disabled for this column")
    if year is not None and not config.enable_year_filter:
        raise Http404("Article year filtering is disabled for this column")
    target = ArticlePlacement.TargetType.SECTION
    target_slug = item.placement_target_slug
    featured_all = list(get_slot_items("column_featured", target, target_slug, at=at))
    secondary_all = list(get_slot_items("column_secondary", target, target_slug, at=at))
    article_list_all = list(get_slot_items("column_list", target, target_slug, at=at))
    sidebar_all = list(get_slot_items("column_sidebar", target, target_slug, at=at))
    filter_source = featured_all + secondary_all + article_list_all + sidebar_all

    type_options = []
    for placement in filter_source:
        value = placement.article.article_type
        option = {
            "value": slugify(value),
            "label": article_type_label(value),
        }
        if option not in type_options:
            type_options.append(option)
    type_options.sort(key=lambda option: option["label"])
    year_values = sorted(
        {
            placement.article.first_published_at.year
            for placement in filter_source
            if placement.article.first_published_at
        },
        reverse=True,
    )
    valid_types = {option["value"] for option in type_options}
    if article_type and article_type not in valid_types:
        raise Http404("Article type filter not found")
    if year is not None and int(year) not in year_values:
        raise Http404("Article year filter not found")
    for option in type_options:
        option["url"] = _column_filter_path(
            item,
            article_type=option["value"],
            year=year,
        )
    year_options = [
        {
            "value": value,
            "url": _column_filter_path(
                item,
                article_type=article_type,
                year=value,
            ),
        }
        for value in year_values
    ]

    featured = _filter_column_placements(
        featured_all, article_type=article_type, year=year
    )
    secondary = _filter_column_placements(
        secondary_all, article_type=article_type, year=year
    )
    article_list = _filter_column_placements(
        article_list_all, article_type=article_type, year=year
    )
    sidebar = _filter_column_placements(
        sidebar_all, article_type=article_type, year=year
    )

    page_size = max(1, int(config.page_size or 20))
    page_count = max(1, (len(article_list) + page_size - 1) // page_size)
    if page_number < 1 or page_number > page_count:
        raise Http404("Content column page not found")
    offset = (page_number - 1) * page_size
    paged_articles = article_list[offset : offset + page_size]
    current_path = _column_filter_path(
        item,
        article_type=article_type,
        year=year,
        page_number=page_number,
    )
    return {
        "journal": journal,
        "navigation_item": item,
        "column_config": config,
        "page_title": config.seo_title or item.label,
        "featured_placements": featured[:1],
        "secondary_placements": secondary[:3],
        "article_placements": paged_articles,
        "sidebar_placements": sidebar[:8],
        "article_types": type_options,
        "article_years": year_options,
        "selected_article_type": article_type,
        "selected_year": int(year) if year else None,
        "all_articles_url": item.target_url,
        "page_number": page_number,
        "page_count": page_count,
        "previous_page_url": (
            _column_filter_path(
                item,
                article_type=article_type,
                year=year,
                page_number=page_number - 1,
            )
            if page_number > 1
            else ""
        ),
        "next_page_url": (
            _column_filter_path(
                item,
                article_type=article_type,
                year=year,
                page_number=page_number + 1,
            )
            if page_number < page_count
            else ""
        ),
        "managed_navigation": (
            _journal_navigation(journal, current_path)
            if journal
            else _main_navigation(current_path)
        ),
    }


def _issue_queryset(*, journal=None):
    queryset = PublicationIssue.objects.filter(
        status=PublicationIssueStatus.PUBLISHED
    ).select_related("journal", "cover_image")
    if journal is None:
        return queryset.filter(
            scope=PublicationIssueScope.MAIN_SITE,
            journal__isnull=True,
        )
    return queryset.filter(
        scope=PublicationIssueScope.JOURNAL,
        journal=journal,
    )


def _issue_navigation(*, journal, current_path):
    return (
        _journal_navigation(journal, current_path)
        if journal
        else _main_navigation(current_path)
    )


def _issue_archive_path(journal):
    if journal:
        return f"/journals/{journal.slug}/issues/"
    return "/explore-content/browse-issues/"


def _group_issue_articles(issue_articles):
    grouped = []
    for assignment in issue_articles:
        label = (assignment.section_label or "").strip()
        if not grouped or grouped[-1][0] != label:
            grouped.append((label, []))
        grouped[-1][1].append(assignment)
    return tuple((label, tuple(items)) for label, items in grouped)


def _issue_cover_alt(issue):
    if not issue.cover_image_id:
        return ""
    return (
        (issue.cover_image.description or "").strip()
        or (issue.cover_image.title or "").strip()
        or issue.title
    )


def get_current_issue_context(journal_slug=None, at=None):
    journal = None
    if journal_slug:
        try:
            journal = get_active_journals().get(slug=journal_slug)
        except Journal.DoesNotExist as exc:
            raise Http404("Journal not found") from exc
    try:
        issue = _issue_queryset(journal=journal).get(is_current=True)
    except PublicationIssue.DoesNotExist as exc:
        raise Http404("Current issue not found") from exc
    path = (
        f"/journals/{journal.slug}/current-issue/"
        if journal
        else "/explore-content/current-issue/"
    )
    issue_articles = list(
        issue.issue_articles.select_related(
            "article", "article__primary_journal"
        ).order_by("sort_order", "pk")
    )
    scope_issues = list(_issue_queryset(journal=journal))
    index = next(
        i for i, candidate in enumerate(scope_issues) if candidate.pk == issue.pk
    )
    previous_issue = scope_issues[index + 1] if index + 1 < len(scope_issues) else None
    return {
        "journal": journal,
        "issue": issue,
        "page_title": f"Current issue | {issue.title}",
        "issue_articles": issue_articles,
        "issue_article_groups": _group_issue_articles(issue_articles),
        "issue_cover_alt": _issue_cover_alt(issue),
        "previous_issue": previous_issue,
        "browse_issues_url": _issue_archive_path(journal),
        "managed_navigation": _issue_navigation(journal=journal, current_path=path),
    }


def get_issue_archive_context(journal_slug=None, at=None):
    journal = None
    if journal_slug:
        try:
            journal = get_active_journals().get(slug=journal_slug)
        except Journal.DoesNotExist as exc:
            raise Http404("Journal not found") from exc
    issues = list(_issue_queryset(journal=journal))
    grouped = defaultdict(list)
    for issue in issues:
        grouped[issue.publication_date.year].append(issue)
    years = sorted(grouped, reverse=True)
    path = _issue_archive_path(journal)
    return {
        "journal": journal,
        "page_title": "Browse issues",
        "archive_groups": tuple((year, grouped[year]) for year in years),
        "managed_navigation": _issue_navigation(journal=journal, current_path=path),
    }


def get_issue_detail_context(*, issue_slug, journal_slug=None, at=None):
    journal = None
    if journal_slug:
        try:
            journal = get_active_journals().get(slug=journal_slug)
        except Journal.DoesNotExist as exc:
            raise Http404("Journal not found") from exc
    try:
        issue = _issue_queryset(journal=journal).get(slug=issue_slug)
    except PublicationIssue.DoesNotExist as exc:
        raise Http404("Issue not found") from exc
    issue_articles = list(
        issue.issue_articles.select_related(
            "article", "article__primary_journal"
        ).order_by("sort_order", "pk")
    )
    scope_issues = list(_issue_queryset(journal=journal))
    index = next(
        i for i, candidate in enumerate(scope_issues) if candidate.pk == issue.pk
    )
    newer_issue = scope_issues[index - 1] if index > 0 else None
    older_issue = scope_issues[index + 1] if index + 1 < len(scope_issues) else None
    return {
        "journal": journal,
        "issue": issue,
        "issue_articles": issue_articles,
        "issue_article_groups": _group_issue_articles(issue_articles),
        "issue_cover_alt": _issue_cover_alt(issue),
        "browse_issues_url": _issue_archive_path(journal),
        "newer_issue": newer_issue,
        "older_issue": older_issue,
        "page_title": issue.title,
        "managed_navigation": _issue_navigation(
            journal=journal, current_path=issue.scope_path
        ),
    }


def _static_search_entry(article):
    return {
        "title": article.title,
        "summary": article.abstract,
        "url": article.get_absolute_url(),
        "article_type": article_type_label(article.article_type),
        "journal": localized_journal_name(article.primary_journal),
        "journal_slug": article.primary_journal.slug,
        "authors": article.authors,
        "ai_authors": article.ai_co_authors,
        "keywords": article.keywords,
    }


def get_static_search_context(at=None):
    configured = dict(
        getattr(settings, "STATIC_SEARCH_RECOMMENDATIONS", DEFAULT_STATIC_SEARCH)
    )
    configured.setdefault("title", DEFAULT_STATIC_SEARCH["title"])
    configured.setdefault("introduction", DEFAULT_STATIC_SEARCH["introduction"])
    configured.setdefault("empty_message", DEFAULT_STATIC_SEARCH["empty_message"])
    configured["keywords"] = tuple(
        keyword
        for keyword in configured.get("keywords", DEFAULT_STATIC_SEARCH["keywords"])
        if str(keyword).strip()
    )
    searchable_articles = (
        get_approved_articles(at=at)
        .select_related("primary_journal")
        .order_by("-first_published_at", "-pk")
    )
    search_index = [_static_search_entry(article) for article in searchable_articles]
    return {
        "search_config": configured,
        "search_index": search_index,
        "recommended_placements": list(
            get_slot_items(
                "search_recommended",
                ArticlePlacement.TargetType.SEARCH,
                "search",
                at=at,
            )
        ),
        "SEO_NOINDEX": False,
        "managed_navigation": _main_navigation("/search/"),
    }


def get_static_article_context(slug, at=None):
    try:
        context = get_article_context(slug, at=at)
    except ArticlePage.DoesNotExist as exc:
        raise Http404("Article not found") from exc
    article = context["article"]
    journal = article.primary_journal
    current_path = f"/articles/{article.static_slug}/"
    journal_id = getattr(article, "primary_journal_id", None)
    context["managed_navigation"] = (
        _journal_navigation(journal, current_path)
        if journal_id
        else _main_navigation(current_path)
    )
    if journal_id:
        context["navigation_journal"] = journal
    return context
