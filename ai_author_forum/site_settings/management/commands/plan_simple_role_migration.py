import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ai_author_forum.site_settings.role_migration import build_report, load_mapping


class Command(BaseCommand):
    help = "Report legacy roles and validate an explicit simple-RBAC mapping."

    def add_arguments(self, parser):
        parser.add_argument("--mapping")
        parser.add_argument("--output")

    def handle(self, *args, **options):
        try:
            mapping = load_mapping(options["mapping"])
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        report = build_report(mapping)
        text = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
        if options["output"]:
            Path(options["output"]).write_text(text, encoding="utf-8")
            self.stdout.write(f"Wrote role migration report to {options['output']}")
        else:
            self.stdout.write(text)
        if report["mapping_validation"]["errors"]:
            self.stderr.write(
                self.style.WARNING(
                    f"Mapping has {len(report['mapping_validation']['errors'])} blocking error(s)."
                )
            )
