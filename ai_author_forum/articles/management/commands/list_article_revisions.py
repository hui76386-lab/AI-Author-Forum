import json

from django.core.management.base import BaseCommand, CommandError
from wagtail.models import Page

from ...models import ArticlePage


class Command(BaseCommand):
    help = "List all Wagtail revisions for an ArticlePage."

    def add_arguments(self, parser):
        parser.add_argument("page_id", type=int, help="ArticlePage id.")
        parser.add_argument(
            "--show-body",
            action="store_true",
            help="Print the raw StreamField body data for each revision.",
        )

    def handle(self, *args, **options):
        article = self.get_article(options["page_id"])
        revisions = article.revisions.select_related("user").order_by(
            "-created_at",
            "-id",
        )

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Revisions for ArticlePage #{article.pk}: {article.title}"
            )
        )

        if not revisions.exists():
            self.stdout.write("No revisions found.")
            return

        for revision in revisions:
            self.write_revision(article, revision, show_body=options["show_body"])

    def get_article(self, page_id):
        try:
            page = Page.objects.get(pk=page_id).specific
        except Page.DoesNotExist as error:
            raise CommandError(f"Page #{page_id} does not exist.") from error

        if not isinstance(page, ArticlePage):
            raise CommandError(f"Page #{page_id} is not an ArticlePage.")

        return page

    def write_revision(self, article, revision, show_body=False):
        content = self.get_revision_content(revision)
        markers = self.get_revision_markers(article, revision)
        marker_text = f" [{', '.join(markers)}]" if markers else ""
        user = revision.user or "-"
        title = content.get("title") or revision.object_str or "-"
        body = content.get("body")

        self.stdout.write(
            f"#{revision.pk}{marker_text} | "
            f"{revision.created_at:%Y-%m-%d %H:%M:%S} | "
            f"user={user} | title={title}"
        )
        self.stdout.write(f"  body: {self.summarise_body(body)}")

        if show_body:
            self.stdout.write(
                json.dumps(body or [], ensure_ascii=False, indent=2, default=str)
            )

    def get_revision_content(self, revision):
        content = revision.content or {}

        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                return {}

        if not isinstance(content, dict):
            return {}

        return content

    def get_revision_markers(self, article, revision):
        markers = []

        if revision.pk == article.latest_revision_id:
            markers.append("latest")
        if revision.pk == article.approved_version_id:
            markers.append("approved")
        if revision.pk == article.rejected_version_id:
            markers.append("rejected")

        return markers

    def summarise_body(self, body):
        if not body:
            return "empty"

        if not isinstance(body, list):
            return type(body).__name__

        block_counts = {}
        for block in body:
            block_type = block.get("type", "unknown")
            block_counts[block_type] = block_counts.get(block_type, 0) + 1

        return ", ".join(
            f"{block_type}={count}"
            for block_type, count in sorted(block_counts.items())
        )
