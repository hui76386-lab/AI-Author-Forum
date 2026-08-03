from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from ai_author_forum.articles.services import sync_imported_article
from ai_author_forum.journals.demo_packages import (
    DemoPackageSpec,
    build_demo_import_package,
)
from ai_author_forum.journals.models import StaticArticle


class Command(BaseCommand):
    help = (
        "Generate a deterministic journal import package and run the normal "
        "journal/article import workflow."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--journals",
            type=int,
            default=120,
            help="Number of sub-journals to generate. Default: 120; maximum: 200.",
        )
        parser.add_argument(
            "--articles-per-journal",
            type=int,
            default=100,
            help="Number of fixed static HTML articles per journal. Default: 100.",
        )
        parser.add_argument(
            "--prefix",
            default="ai-demo",
            help="Slug prefix used for deterministic, repeatable demo data.",
        )
        parser.add_argument(
            "--home-feature-count",
            type=int,
            default=24,
            help="Number of imported articles placed on main-site feature slots.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate generated workbooks without writing business records.",
        )
        parser.add_argument(
            "--publish-static-site",
            action="store_true",
            help="Run centralized static publishing after import succeeds.",
        )
        parser.add_argument(
            "--operator-id",
            type=int,
            help="User ID recorded as the import and static publish operator.",
        )
        parser.add_argument(
            "--package-out",
            help="Optional path where the generated zip package should be saved.",
        )

    def handle(self, *args, **options):
        operator_id = options.get("operator_id")
        if operator_id:
            self._validate_operator(operator_id)

        spec = DemoPackageSpec(
            journal_count=options["journals"],
            articles_per_journal=options["articles_per_journal"],
            prefix=options["prefix"].strip(),
            home_feature_count=options["home_feature_count"],
        )
        try:
            package_bytes = build_demo_import_package(spec)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        package_out = options.get("package_out")
        if package_out:
            output_path = Path(package_out).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(package_bytes)
            package_path = output_path
        else:
            with TemporaryDirectory(prefix="ai-author-forum-demo-") as temp_dir:
                package_path = Path(temp_dir) / "journal-demo-package.zip"
                package_path.write_bytes(package_bytes)
                self._run_import_command(package_path, options)
                return

        self._run_import_command(package_path, options)

    def _run_import_command(self, package_path: Path, options):
        command_args = ["--package", str(package_path)]
        if options["dry_run"]:
            command_args.append("--dry-run")
        if options.get("operator_id"):
            command_args.extend(["--operator-id", str(options["operator_id"])])

        call_command("import_journal_package", *command_args, stdout=self.stdout)
        if options["dry_run"]:
            return

        operator = (
            get_user_model().objects.get(pk=options["operator_id"])
            if options.get("operator_id")
            else None
        )
        prefix = options["prefix"].strip()
        articles = StaticArticle.objects.filter(
            journal__slug__startswith=f"{prefix}-journal-"
        ).select_related("journal")
        for article in articles.iterator():
            sync_imported_article(article, owner=operator)

        if options["publish_static_site"]:
            call_command("build_static_site", stdout=self.stdout)

    def _validate_operator(self, operator_id):
        user_model = get_user_model()
        if not user_model.objects.filter(pk=operator_id).exists():
            raise CommandError(f"Operator user {operator_id} does not exist")
