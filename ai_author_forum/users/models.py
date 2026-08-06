from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class User(AbstractUser):
    class AccountStatus(models.TextChoices):
        ACTIVE = "active", "正常"
        SUSPENDED = "suspended", "已暂停"
        DEACTIVATED = "deactivated", "已停用"

    email = models.EmailField(max_length=254)
    display_name = models.CharField(max_length=120)
    institution = models.CharField(max_length=255, blank=True)
    job_title = models.CharField(max_length=120, blank=True)
    account_status = models.CharField(
        max_length=16,
        choices=AccountStatus.choices,
        default=AccountStatus.ACTIVE,
        db_index=True,
    )
    must_change_password = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_accounts",
    )
    suspended_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    status_reason = models.TextField(blank=True)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(email=""),
                name="users_user_email_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(display_name=""),
                name="users_user_display_name_not_empty",
            ),
            models.UniqueConstraint(
                Lower("email"),
                name="users_user_email_ci_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(account_status="active", is_active=True)
                    | models.Q(
                        account_status__in=(
                            "suspended",
                            "deactivated",
                        ),
                        is_active=False,
                    )
                ),
                name="users_account_status_matches_is_active",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        if not self.display_name:
            self.display_name = self.get_full_name().strip() or self.username
        self.is_active = self.account_status == self.AccountStatus.ACTIVE
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.display_name or self.username
