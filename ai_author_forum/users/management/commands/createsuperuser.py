from django.contrib.auth import get_user_model
from django.contrib.auth.management.commands.createsuperuser import (
    Command as DjangoCreateSuperuserCommand,
)
from django.core.management.base import CommandError

from ai_author_forum.users.services import (
    SUPER_ADMIN_GROUP_NAME,
    initialize_super_admin_group,
)


class Command(DjangoCreateSuperuserCommand):
    help = (
        "Create the first technical recovery superuser and synchronize it to "
        "the sole super-administrator business group."
    )

    def handle(self, *args, **options):
        User = get_user_model()
        if User.objects.filter(
            is_active=True,
            account_status="active",
            groups__name=SUPER_ADMIN_GROUP_NAME,
        ).exists():
            raise CommandError(
                "An active super administrator already exists; use /admin/accounts/new/."
            )
        result = super().handle(*args, **options)
        username = options.get(User.USERNAME_FIELD)
        queryset = User.objects.filter(is_superuser=True, is_active=True).order_by(
            "-date_joined", "-pk"
        )
        if username:
            queryset = queryset.filter(**{User.USERNAME_FIELD: username})
        user = queryset.first()
        if user is None:
            raise CommandError("The created recovery superuser could not be resolved.")
        initialize_super_admin_group(user)
        self.stdout.write(
            self.style.SUCCESS("Synchronized the initial super administrator group.")
        )
        return result
