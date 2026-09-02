import json

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME

from ...models import StaticPublishJob
from ...services import PublishError, StaticPublisher, create_publish_job


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
        parser.add_argument(
            "--actor",
            help=(
                "Username or email of the active super administrator accountable "
                "for this operation. May be omitted only when exactly one exists."
            ),
        )

    def _resolve_actor(self, value):
        users = get_user_model().objects.filter(
            is_active=True,
            account_status="active",
            groups__name=SUPER_ADMIN_GROUP_NAME,
        )
        if value:
            actor = users.filter(Q(username=value) | Q(email__iexact=value)).first()
            if actor is None:
                raise CommandError(
                    "--actor must identify an active super administrator"
                )
            return actor
        matches = list(users[:2])
        if len(matches) != 1:
            raise CommandError(
                "Specify --actor unless exactly one active super administrator exists"
            )
        return matches[0]

    def handle(self, *args, **options):
        modes = bool(options["retry_job"]) + bool(options["rollback"])
        if modes > 1 or (modes and options["paths"]):
            raise CommandError("Use exactly one of --path, --retry-job, or --rollback")
        actor = self._resolve_actor(options["actor"])
        publisher = StaticPublisher(options["output_root"])
        try:
            if options["rollback"]:
                reason = (options["rollback_reason"] or "").strip()
                if len(reason) < 5:
                    raise CommandError(
                        "使用 --rollback 时必须提供至少 5 个字符的 --rollback-reason"
                    )
                job = publisher.rollback(options["rollback"], user=actor, reason=reason)
            elif options["retry_job"]:
                failed_job = StaticPublishJob.objects.get(pk=options["retry_job"])
                job = publisher.retry(failed_job, user=actor)
            else:
                job = create_publish_job(
                    scope=(
                        StaticPublishJob.Scope.SELECTIVE
                        if options["paths"]
                        else StaticPublishJob.Scope.FULL
                    ),
                    paths=options["paths"],
                    actor=actor,
                )
                publisher.build(job)
        except StaticPublishJob.DoesNotExist as exc:
            raise CommandError(
                f"Publish job {options['retry_job']} does not exist"
            ) from exc
        except (PermissionDenied, PublishError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                json.dumps(
                    {"job": job.pk, "status": job.status, "version": job.version},
                    ensure_ascii=False,
                )
            )
        )
