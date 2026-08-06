from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date
from io import BytesIO
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
E2E_ROOT = BASE_DIR / ".e2e"
DATABASE_PATH = E2E_ROOT / "db.sqlite3"
OFFLINE_DATABASE_PATH = E2E_ROOT / "db.sqlite3.offline"
MEDIA_ROOT = E2E_ROOT / "media"
OUTPUT_ROOT = BASE_DIR / "static_publish_output"
ACCEPTANCE_PATH = E2E_ROOT / "acceptance.json"

EXPECTED_PAGES = (
    "index.html",
    "journals/index.html",
    "journals/acceptance-journal/index.html",
    "journals/acceptance-journal/current-issue/index.html",
    "journals/acceptance-journal/issues/index.html",
    "journals/acceptance-journal/issues/volume-1-issue-1/index.html",
    "journals/acceptance-journal/sections/news-and-comment/index.html",
    "journals/acceptance-journal/sections/research-articles/index.html",
    "journals/empty-navigation-journal/index.html",
    "journals/acceptance-journal/categories/legacy-topic/index.html",
    "journals/acceptance-journal/categories/machine-intelligence/index.html",
    "journals/acceptance-journal/categories/machine-intelligence/neural-networks/index.html",
    "sections/ai-article/index.html",
    "sections/news/index.html",
    "sections/opinion/index.html",
    "sections/research-analysis/index.html",
    "explore-content/ai-article/index.html",
    "explore-content/news/index.html",
    "explore-content/opinion/index.html",
    "explore-content/research-analysis/index.html",
    "articles/static-acceptance-article/index.html",
    "search/index.html",
)


def assert_workspace_path(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(BASE_DIR)
    except ValueError as exc:
        raise RuntimeError(
            f"Refusing to modify path outside workspace: {resolved}"
        ) from exc
    if resolved == BASE_DIR:
        raise RuntimeError("Refusing to modify the workspace root")
    return resolved


def reset_path(path: Path) -> None:
    resolved = assert_workspace_path(path)
    if resolved.is_dir():
        shutil.rmtree(resolved)
    elif resolved.exists():
        resolved.unlink()


def configure_django() -> None:
    E2E_ROOT.mkdir(parents=True, exist_ok=True)
    os.environ["DJANGO_SETTINGS_MODULE"] = "ai_author_forum.settings.dev"
    os.environ["DATABASE_URL"] = f"sqlite:///{DATABASE_PATH.as_posix()}"
    os.environ["MEDIA_ROOT"] = str(MEDIA_ROOT)
    os.environ["STATIC_PUBLISH_ROOT"] = str(OUTPUT_ROOT)
    os.environ["STATIC_PUBLISH_AUTO_ON_PLACEMENT_CHANGE"] = "false"


def create_image():
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image as PillowImage

    from ai_author_forum.images.models import CustomImage

    data = BytesIO()
    PillowImage.new("RGB", (320, 180), "navy").save(data, format="PNG")
    return CustomImage.objects.create(
        title="Static frontend acceptance image",
        file=SimpleUploadedFile(
            "static-frontend-acceptance.png",
            data.getvalue(),
            content_type="image/png",
        ),
    )


def seed_content():
    from django.contrib.auth import get_user_model
    from wagtail.models import Page

    from ai_author_forum.articles.models import ArticlePage
    from ai_author_forum.home.models import HomePage
    from ai_author_forum.journals.category_services import (
        create_category,
        update_category,
    )
    from ai_author_forum.journals.models import (
        IssueArticle,
        Journal,
        PublicationIssue,
        PublicationIssueScope,
        PublicationIssueStatus,
    )
    from ai_author_forum.placements.category_services import sync_category_placements
    from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
    from ai_author_forum.site_settings.models import (
        NavigationGroup,
        NavigationItem,
        NavigationSet,
        NavigationSetStatus,
    )
    from ai_author_forum.test_helpers import (
        ensure_test_primary_category,
        formally_approve_test_article,
        grant_business_super_admin,
    )

    home = HomePage.objects.first()
    if home is None:
        raise RuntimeError("HomePage migration did not create the site root")
    home.introduction = (
        "Deterministic static frontend acceptance release for AI Author Forum."
    )
    home.save(clean=False)

    image = create_image()
    review_actor = grant_business_super_admin(
        get_user_model().objects.create_user(
            username="static-e2e-review-actor",
            email="static-e2e-review-actor@example.com",
            display_name="Static E2E Review Actor",
            password="static-e2e-review-password",
            is_staff=True,
        )
    )
    journal = Journal.objects.create(
        name="Acceptance Journal",
        name_cn="验收期刊",
        slug="acceptance-journal",
        az_group="A",
        status="active",
        homepage_intro="Deterministic journal homepage acceptance content.",
        hero_quick_links=[
            ("link", {"label": "AI Article", "url": "/explore-content/ai-article/"}),
            ("link", {"label": "Co authoring with AI", "url": "#co-authoring-with-ai"}),
            (
                "link",
                {"label": "Reader responsibility", "url": "#reader-responsibility"},
            ),
            ("link", {"label": "Submission guide", "url": "/submission-guide/"}),
            ("link", {"label": "Editorial policy", "url": "/editorial-policy/"}),
            ("link", {"label": "About this journal", "url": "/journals/"}),
        ],
        cover_image=image,
        seo_title="Acceptance Journal",
        seo_description="Static journal acceptance page.",
    )

    navigation_set = NavigationSet.objects.get(
        journal=journal,
        status=NavigationSetStatus.ACTIVE,
        is_template=False,
    )
    long_group = NavigationGroup.objects.get(
        navigation_set=navigation_set,
        code="explore-content",
    )
    long_group.label = "Interdisciplinary research and responsible AI collaboration"
    long_group.save(update_fields=("label", "updated_at"))
    research_item = NavigationItem.objects.get(
        group__navigation_set=navigation_set,
        code="research-articles",
    )
    research_item.label = "超长中文栏目名称：人工智能作者协作与责任研究"
    research_item.save(update_fields=("label", "updated_at"))
    empty_column_item = NavigationItem.objects.get(
        group__navigation_set=navigation_set,
        code="news-and-comment",
    )

    empty_journal = Journal.objects.create(
        name="Empty Navigation Journal",
        name_cn="无栏目期刊",
        slug="empty-navigation-journal",
        az_group="E",
        status="active",
        homepage_intro="Journal acceptance fixture with no configured navigation columns.",
        seo_title="Empty Navigation Journal",
        seo_description="Static empty navigation and empty article acceptance page.",
    )
    empty_navigation_set = NavigationSet.objects.get(
        journal=empty_journal,
        status=NavigationSetStatus.ACTIVE,
        is_template=False,
    )
    empty_navigation_set.groups.all().delete()

    category = create_category(
        journal=journal,
        actor=review_actor,
        data={
            "name": "Machine Intelligence",
            "code": "MACHINE-INTELLIGENCE",
            "slug": "legacy-topic",
            "show_in_navigation": True,
            "generate_static_page": True,
        },
    ).category
    update_category(
        category_id=category.pk,
        changes={"slug": "machine-intelligence"},
        actor=review_actor,
        request_id="static-e2e-category-redirect",
    )
    category.refresh_from_db()
    child_category = create_category(
        journal=journal,
        parent=category,
        actor=review_actor,
        data={
            "name": "Neural Networks",
            "code": "NEURAL-NETWORKS",
            "slug": "neural-networks",
            "show_in_navigation": True,
            "generate_static_page": True,
        },
    ).category

    article = ArticlePage(
        title="Static acceptance article",
        slug="static-acceptance-article-page",
        static_slug="static-acceptance-article",
        abstract="Deterministic article detail and placement acceptance content.",
        body=[
            ("paragraph", "<p>Static acceptance article body.</p>"),
            (
                "image",
                {
                    "image": image,
                    "caption": "Static frontend acceptance image",
                },
            ),
        ],
        authors="Acceptance editorial team",
        article_type=ArticlePage.ArticleType.NEWS,
        primary_journal=journal,
        keywords="static publishing, acceptance",
    )
    root = Page.get_first_root_node()
    root.add_child(instance=article)
    ensure_test_primary_category(article)
    # Establish the deterministic Wagtail publication timestamp while the page
    # is still a draft; formal approval and static delivery happen afterwards.
    article.save_revision().publish()
    article.refresh_from_db()
    article = formally_approve_test_article(article, actor=review_actor)
    sync_category_placements(
        article_id=article.pk,
        revision_id=article.approved_version_id,
        actor=review_actor,
        request_id="static-e2e-category-placement-sync",
    )

    issue = PublicationIssue.objects.create(
        scope=PublicationIssueScope.JOURNAL,
        journal=journal,
        slug="volume-1-issue-1",
        volume_label="Volume 1",
        issue_number="Issue 1",
        title="Acceptance current issue",
        summary="Deterministic real issue acceptance content.",
        cover_image=image,
        publication_date=date(2026, 7, 31),
        status=PublicationIssueStatus.PUBLISHED,
        is_current=True,
    )
    IssueArticle.objects.create(
        issue=issue,
        article=article,
        section_label="Research",
    )

    placements = {}
    placement_specs = (
        (
            "home",
            "home_hero",
            ArticlePlacement.TargetType.MAIN_SITE,
            "",
            "Static acceptance home headline",
        ),
        (
            "journal",
            "journal_latest",
            ArticlePlacement.TargetType.JOURNAL,
            journal.slug,
            "Static acceptance journal headline",
        ),
        (
            "column",
            "column_list",
            ArticlePlacement.TargetType.SECTION,
            research_item.placement_target_slug,
            "Static acceptance content column headline",
        ),
        (
            "section",
            "column_list",
            ArticlePlacement.TargetType.SECTION,
            "news",
            "E2E rollback baseline headline",
        ),
        (
            "search",
            "search_recommended",
            ArticlePlacement.TargetType.SEARCH,
            "search",
            "Static acceptance search headline",
        ),
    )
    for key, slot_code, target_type, target_slug, override_title in placement_specs:
        placements[key] = ArticlePlacement.objects.create(
            article=article,
            slot=LayoutSlot.objects.get(code=slot_code),
            target_type=target_type,
            target_slug=target_slug,
            override_title=override_title,
            override_image=image,
        )
    fixture = {
        "journal": journal,
        "empty_journal": empty_journal,
        "navigation_set": navigation_set,
        "research_item": research_item,
        "empty_column_item": empty_column_item,
        "long_group_label": long_group.label,
        "long_item_label": research_item.label,
    }
    return placements, category, child_category, fixture, review_actor


def build_and_rollback(
    section_placement,
    category,
    child_category,
    fixture,
    actor,
):
    from ai_author_forum.static_publish.models import StaticPublishJob
    from ai_author_forum.static_publish.services import StaticPublisher

    publisher = StaticPublisher(OUTPUT_ROOT)
    first_job = StaticPublishJob.objects.create(
        scope=StaticPublishJob.Scope.FULL,
        triggered_by=actor,
    )
    first_manifest = publisher.build(first_job)

    section_placement.override_title = "E2E second release headline"
    section_placement.save(update_fields=("override_title",))
    second_job = StaticPublishJob.objects.create(
        scope=StaticPublishJob.Scope.FULL,
        triggered_by=actor,
    )
    publisher.build(second_job)

    rollback_job = publisher.rollback(
        first_job.version,
        user=actor,
        reason="静态验收回滚到首个发布版本",
    )
    current = OUTPUT_ROOT / "current"
    manifest = json.loads((current / "manifest.json").read_text(encoding="utf-8"))

    missing = [path for path in EXPECTED_PAGES if not (current / path).is_file()]
    if missing:
        raise RuntimeError(f"Static E2E release is missing pages: {missing}")
    if manifest["version"] != first_job.version:
        raise RuntimeError("Rollback did not restore the first release manifest")
    manifest_page_count = manifest["summary"]["pages"]
    target_count = len(manifest.get("targets", ()))
    html_file_count = sum(
        1
        for item in manifest.get("files", ())
        if item.get("path", "").endswith(".html")
    )
    if manifest_page_count != target_count or manifest_page_count != html_file_count:
        raise RuntimeError(
            "Manifest page accounting is inconsistent: "
            f"summary={manifest_page_count}, targets={target_count}, "
            f"html_files={html_file_count}"
        )
    if manifest["summary"]["failed"] != 0:
        raise RuntimeError("Static E2E release contains failed pages")
    if not manifest.get("asset_references"):
        raise RuntimeError("Static E2E release did not record media references")

    acceptance = {
        "expected_pages": list(EXPECTED_PAGES),
        "expected_page_count": manifest_page_count,
        "baseline_headline": "E2E rollback baseline headline",
        "second_release_headline": "E2E second release headline",
        "first_version": first_job.version,
        "second_version": second_job.version,
        "rollback_job": rollback_job.pk,
        "manifest_record": first_manifest.pk,
        "category_path": category.get_absolute_url(),
        "child_category_path": child_category.get_absolute_url(),
        "content_column_path": fixture["research_item"].target_url,
        "empty_column_path": fixture["empty_column_item"].target_url,
        "current_issue_path": "/journals/acceptance-journal/current-issue/",
        "issue_archive_path": "/journals/acceptance-journal/issues/",
        "issue_detail_path": "/journals/acceptance-journal/issues/volume-1-issue-1/",
        "empty_journal_path": f"/journals/{fixture['empty_journal'].slug}/",
        "long_group_label": fixture["long_group_label"],
        "long_item_label": fixture["long_item_label"],
        "main_navigation_group_lengths": [8, 1, 12, 2, 2],
        "redirect_path": "/journals/acceptance-journal/categories/legacy-topic/",
        "redirect_to": category.get_absolute_url(),
        "database_disconnected": True,
        "offline_database_path": OFFLINE_DATABASE_PATH.relative_to(BASE_DIR).as_posix(),
    }
    ACCEPTANCE_PATH.write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return acceptance


def main() -> None:
    for path in (DATABASE_PATH, OFFLINE_DATABASE_PATH, MEDIA_ROOT, OUTPUT_ROOT):
        reset_path(path)
    configure_django()

    import django

    django.setup()

    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=0)
    placements, category, child_category, fixture, review_actor = seed_content()
    acceptance = build_and_rollback(
        placements["section"],
        category,
        child_category,
        fixture,
        review_actor,
    )

    from django.db import connections

    connections.close_all()
    DATABASE_PATH.replace(OFFLINE_DATABASE_PATH)
    if DATABASE_PATH.exists() or not OFFLINE_DATABASE_PATH.is_file():
        raise RuntimeError("Static E2E database disconnection could not be proven")

    print(
        json.dumps(
            {
                "status": "ready",
                "version": acceptance["first_version"],
                "pages": acceptance["expected_page_count"],
                "output": str(OUTPUT_ROOT / "current"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
