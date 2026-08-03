import json

from django.core.management.base import BaseCommand, CommandError

from ...health import get_health_report


class Command(BaseCommand):
    help = "Check database, active static release, and task broker readiness."

    def add_arguments(self, parser):
        parser.add_argument("--skip-release", action="store_true")
        parser.add_argument("--skip-broker", action="store_true")

    def handle(self, *args, **options):
        report = get_health_report(
            include_release=not options["skip_release"],
            include_broker=not options["skip_broker"],
        )
        output = json.dumps(report, ensure_ascii=False)
        if report["status"] != "ok":
            raise CommandError(output)
        self.stdout.write(self.style.SUCCESS(output))
