import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from ...models import StaticPublishJob
from ...services import PublishError, StaticPublisher


class Command(BaseCommand):
    help = "Build, retry, or roll back a versioned static site release."

    def add_arguments(self, parser):
        parser.add_argument("--path", action="append", dest="paths", default=[])
        parser.add_argument("--retry-job", type=int)
        parser.add_argument("--rollback")
        parser.add_argument(
            "--rollback-reason",
            help="回滚原因，使用 --rollback 时必填，至少 5 个字符。",
        )
        parser.add_argument("--output-root")

    def handle(self, *args, **options):
        modes = bool(options["retry_job"]) + bool(options["rollback"])
        if modes > 1 or (modes and options["paths"]):
            raise CommandError("Use exactly one of --path, --retry-job, or --rollback")
        publisher = StaticPublisher(options["output_root"])
        try:
            if options["rollback"]:
                reason = (options["rollback_reason"] or "").strip()
                if len(reason) < 5:
                    raise CommandError(
                        "使用 --rollback 时必须提供至少 5 个字符的 --rollback-reason"
                    )
                job = publisher.rollback(options["rollback"], reason=reason)
            elif options["retry_job"]:
                failed_job = StaticPublishJob.objects.get(pk=options["retry_job"])
                job = publisher.retry(failed_job)
            else:
                job = StaticPublishJob.objects.create(
                    scope=(
                        StaticPublishJob.Scope.SELECTIVE
                        if options["paths"]
                        else StaticPublishJob.Scope.FULL
                    ),
                    requested_paths=options["paths"],
                )
                publisher.build(job)
        except StaticPublishJob.DoesNotExist as exc:
            raise CommandError(
                f"Publish job {options['retry_job']} does not exist"
            ) from exc
        except (PublishError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                json.dumps(
                    {"job": job.pk, "status": job.status, "version": job.version},
                    ensure_ascii=False,
                )
            )
        )
