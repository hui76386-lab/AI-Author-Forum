from pathlib import Path

from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase
from wagtail.models import Site

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.home.models import HomePage
from ai_author_forum.journals.models import Journal
from ai_author_forum.news.models import NewsListingPage
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.standardpages.models import StandardPage


class TemplateCompilationTests(SimpleTestCase):
    def test_project_templates_have_no_verbatim_wrappers_and_compile(self):
        template_root = Path(settings.BASE_DIR) / "templates"
        template_paths = sorted(template_root.rglob("*.html"))

        self.assertTrue(template_paths)
        for template_path in template_paths:
            with self.subTest(template=str(template_path.relative_to(template_root))):
                source = template_path.read_text(encoding="utf-8")
                self.assertNotIn("{% verbatim %}", source)
                self.assertNotIn("{% endverbatim %}", source)
                get_template(template_path.relative_to(template_root).as_posix())


class FrontendTemplateRenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.site = Site.objects.get(is_default_site=True)
        cls.site.hostname = "testserver"
        cls.site.site_name = "Template Rendering Test"
        cls.site.save()

        cls.home = HomePage.objects.first()
        cls.home.title = "Rendered home title"
        cls.home.introduction = "Rendered home introduction"
        cls.home.body = []
        cls.home.save_revision().publish()

        cls.standard_page = StandardPage(
            title="Rendered standard page",
            slug="rendered-standard-page",
            introduction="Rendered standard introduction",
            body=[],
        )
        cls.home.add_child(instance=cls.standard_page)
        cls.standard_page.save_revision().publish()

        cls.news_listing = NewsListingPage(
            title="Rendered news listing",
            slug="rendered-news",
            introduction="Rendered news introduction",
        )
        cls.home.add_child(instance=cls.news_listing)
        cls.news_listing.save_revision().publish()

        cls.journal = Journal.objects.create(
            name="Rendered Journal",
            slug="rendered-journal",
            az_group="R",
        )
        cls.article = ArticlePage(
            title="Rendered canonical article",
            slug="rendered-canonical-article",
            static_slug="rendered-canonical-article",
            abstract="Rendered article introduction",
            body=[("paragraph", "<p>Rendered article body</p>")],
            authors="Rendered Author",
            article_type=ArticlePage.ArticleType.NEWS,
            primary_journal=cls.journal,
            keywords="rendered",
            review_status=ArticlePage.ReviewStatus.APPROVED,
        )
        cls.home.add_child(instance=cls.article)
        cls.article.save_revision().publish()
        ArticlePlacement.objects.create(
            article=cls.article,
            slot=LayoutSlot.objects.get(code="section_article_list"),
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="news",
        )
        ArticlePlacement.objects.create(
            article=cls.article,
            slot=LayoutSlot.objects.get(code="home_hero"),
        )

    def assert_page_was_rendered(self, response, expected_text):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, expected_text)
        self.assertContains(response, "Template Rendering Test")
        self.assertContains(response, "<header", html=False)
        self.assertContains(response, "<footer", html=False)
        self.assertNotContains(response, "{%")
        self.assertNotContains(response, "{{")

    def test_home_page_renders_variables_and_includes(self):
        response = self.client.get(self.home.url)
        self.assert_page_was_rendered(response, "Rendered home title")
        self.assertContains(response, "Rendered canonical article")
        self.assertNotContains(response, "Rendered home introduction")

    def test_standard_page_renders_variables_and_includes(self):
        response = self.client.get(self.standard_page.url)
        self.assert_page_was_rendered(response, "Rendered standard page")
        self.assertContains(response, "Rendered standard introduction")

    def test_news_listing_renders_variables_and_article_card_include(self):
        response = self.client.get(self.news_listing.url)
        self.assert_page_was_rendered(response, "Rendered news listing")
        self.assertContains(response, "Rendered canonical article")

    def test_news_article_renders_variables_and_navigation_includes(self):
        response = self.client.get(self.article.get_absolute_url())
        self.assert_page_was_rendered(response, "Rendered canonical article")
        self.assertContains(response, "Rendered Author")
        self.assertContains(response, "Rendered Journal")
