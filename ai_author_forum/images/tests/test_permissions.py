from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image as PillowImage
from wagtail.images.permissions import permission_policy
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.editor_services import sync_editor_access_group
from ai_author_forum.journals.models import Journal, JournalEditorAssignment
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME

from ..models import CustomImage
from ..permissions import JournalImagePermissionPolicy


def uploaded_image(name, colour):
    stream = BytesIO()
    PillowImage.new("RGB", (32, 24), colour).save(stream, format="PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


class JournalImagePermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.user_model = get_user_model()
        cls.admin = cls.user_model.objects.create_user(
            username="image-permission-admin",
            email="image-permission-admin@example.com",
            display_name="Image Permission Admin",
            password="test-password",
            is_staff=True,
        )
        cls.admin.groups.add(
            cls.admin.groups.model.objects.get(name=SUPER_ADMIN_GROUP_NAME)
        )
        cls.journal_a = Journal.objects.create(
            name="Image Journal A", slug="image-journal-a", az_group="I"
        )
        cls.journal_b = Journal.objects.create(
            name="Image Journal B", slug="image-journal-b", az_group="I"
        )

    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def make_editor(self, username, *, journal=None, role=None, responsibilities=()):
        user = self.user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            display_name=username,
            password="test-password",
            is_staff=True,
        )
        role = role or JournalEditorAssignment.Role.ASSOCIATE_EDITOR
        JournalEditorAssignment.objects.create(
            user=user,
            journal=journal or self.journal_a,
            role=role,
            responsibilities=list(responsibilities),
            public_name=username,
            public_role_label=JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[role],
            created_by=self.admin,
        )
        sync_editor_access_group(user)
        return user

    def make_image(self, title, colour, *, owner=None):
        return CustomImage.objects.create(
            title=title,
            file=uploaded_image(f"{title}.png", colour),
            uploaded_by_user=owner,
        )

    def make_article(self, *, journal, image, title):
        article = ArticlePage(
            title=title,
            slug=title.lower().replace(" ", "-"),
            static_slug=title.lower().replace(" ", "-"),
            abstract=f"{title} abstract",
            featured_image=image,
            body=[("paragraph", "Body")],
            authors="Editor",
            article_type=ArticlePage.ArticleType.NEWS,
            primary_journal=journal,
            keywords="images",
        )
        Page.get_first_root_node().add_child(instance=article)
        return ArticlePage.objects.get(pk=article.pk)

    def test_project_policy_is_installed(self):
        self.assertIsInstance(permission_policy, JournalImagePermissionPolicy)

    def test_editor_can_choose_only_assigned_journal_and_owned_images(self):
        editor = self.make_editor(
            "article-image-editor",
            responsibilities=(
                JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE,
            ),
        )
        image_a = self.make_image("Journal A image", "red")
        image_b = self.make_image("Journal B image", "blue")
        owned = self.make_image("Owned image", "green", owner=editor)
        unrelated = self.make_image("Unrelated image", "yellow")
        self.make_article(journal=self.journal_a, image=image_a, title="Article A")
        self.make_article(journal=self.journal_b, image=image_b, title="Article B")

        visible_ids = set(
            permission_policy.instances_user_has_permission_for(
                editor, "choose"
            ).values_list("pk", flat=True)
        )

        self.assertTrue(permission_policy.user_has_permission(editor, "choose"))
        self.assertFalse(permission_policy.user_has_permission(editor, "add"))
        self.assertEqual(visible_ids, {image_a.pk, owned.pk})
        self.assertNotIn(image_b.pk, visible_ids)
        self.assertNotIn(unrelated.pk, visible_ids)

    def test_media_responsibility_can_manage_only_assigned_journal_images(self):
        editor = self.make_editor(
            "media-image-editor",
            responsibilities=(JournalEditorAssignment.Responsibility.MEDIA_ASSETS,),
        )
        image_a = self.make_image("Managed Journal A image", "red")
        image_b = self.make_image("Managed Journal B image", "blue")
        self.make_article(journal=self.journal_a, image=image_a, title="Managed A")
        self.make_article(journal=self.journal_b, image=image_b, title="Managed B")

        self.assertTrue(permission_policy.user_has_permission(editor, "add"))
        self.assertTrue(
            permission_policy.user_has_permission_for_instance(
                editor, "change", image_a
            )
        )
        self.assertFalse(
            permission_policy.user_has_permission_for_instance(
                editor, "change", image_b
            )
        )

    def test_chief_editor_receives_media_management_by_role(self):
        chief = self.make_editor(
            "chief-image-editor",
            role=JournalEditorAssignment.Role.CHIEF_EDITOR,
        )
        image = self.make_image("Chief journal image", "red")
        self.make_article(journal=self.journal_a, image=image, title="Chief A")

        self.assertTrue(permission_policy.user_has_permission(chief, "add"))
        self.assertTrue(
            permission_policy.user_has_permission_for_instance(chief, "delete", image)
        )

    def test_inactive_account_loses_image_access(self):
        editor = self.make_editor(
            "inactive-image-editor",
            responsibilities=(JournalEditorAssignment.Responsibility.MEDIA_ASSETS,),
        )
        image = self.make_image("Inactive image", "red")
        self.make_article(journal=self.journal_a, image=image, title="Inactive A")
        editor.is_active = False
        editor.account_status = editor.AccountStatus.SUSPENDED
        editor.save(update_fields=("is_active", "account_status"))

        self.assertFalse(permission_policy.user_has_permission(editor, "choose"))
        self.assertFalse(
            permission_policy.instances_user_has_permission_for(
                editor, "choose"
            ).exists()
        )

    def test_chooser_and_library_enforce_journal_scope(self):
        editor = self.make_editor(
            "chooser-image-editor",
            responsibilities=(JournalEditorAssignment.Responsibility.MEDIA_ASSETS,),
        )
        image_a = self.make_image("Chooser Journal A image", "red")
        image_b = self.make_image("Chooser Journal B image", "blue")
        self.make_article(journal=self.journal_a, image=image_a, title="Chooser A")
        self.make_article(journal=self.journal_b, image=image_b, title="Chooser B")
        client = Client()
        client.force_login(editor)

        for url in (
            reverse("wagtailimages_chooser:choose"),
            reverse("wagtailimages:index"),
        ):
            with self.subTest(url=url):
                response = client.get(url, secure=True)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, image_a.title)
                self.assertNotContains(response, image_b.title)
