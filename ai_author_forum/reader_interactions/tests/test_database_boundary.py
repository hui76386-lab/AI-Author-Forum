from django.apps import apps
from django.contrib import admin
from django.db import connections
from django.test import TestCase

from ai_author_forum.reader_access.models import ControlPlaneOutbox

from ..models import Comment, CommentModerationEvent, ReaderIdentity
from ..routers import ReaderInteractionsRouter


class ReaderInteractionsDatabaseBoundaryTests(TestCase):
    databases = {"default", "interactions"}

    def test_interaction_admin_is_read_only(self):
        for model in (ReaderIdentity, Comment, CommentModerationEvent):
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(None))
            self.assertFalse(model_admin.has_change_permission(None))
            self.assertFalse(model_admin.has_delete_permission(None))

    def test_router_keeps_interaction_models_on_interactions(self):
        router = ReaderInteractionsRouter()

        self.assertEqual(router.db_for_read(Comment), "interactions")
        self.assertEqual(router.db_for_write(ReaderIdentity), "interactions")
        self.assertIsNone(router.db_for_read(ControlPlaneOutbox))
        self.assertTrue(router.allow_migrate("interactions", "reader_interactions"))
        self.assertFalse(router.allow_migrate("default", "reader_interactions"))
        self.assertFalse(router.allow_migrate("interactions", "reader_access"))
        self.assertTrue(router.allow_relation(Comment(), ReaderIdentity()))
        self.assertFalse(router.allow_relation(Comment(), ControlPlaneOutbox()))

    def test_tables_exist_only_on_their_owned_database(self):
        default_tables = set(connections["default"].introspection.table_names())
        interaction_tables = set(
            connections["interactions"].introspection.table_names()
        )

        self.assertIn("reader_access_controlplaneoutbox", default_tables)
        self.assertNotIn("reader_access_controlplaneoutbox", interaction_tables)
        self.assertIn("reader_interactions_comment", interaction_tables)
        self.assertNotIn("reader_interactions_comment", default_tables)

    def test_interaction_relations_never_target_control_plane_models(self):
        app_config = apps.get_app_config("reader_interactions")

        for model in app_config.get_models():
            for field in model._meta.get_fields():
                related_model = getattr(field, "related_model", None)
                if related_model is None:
                    continue
                self.assertEqual(
                    related_model._meta.app_label,
                    "reader_interactions",
                    f"{model._meta.label}.{field.name} crosses the database boundary",
                )
