import threading
from queue import Queue
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase

from ai_author_forum.journals.category_services import (
    CategoryError,
    create_category,
    move_category,
)
from ai_author_forum.journals.models import Journal, JournalCategory
from ai_author_forum.test_helpers import create_test_user, grant_business_super_admin


@skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL is required to verify row locks and concurrent constraints.",
)
class PostgreSQLCategoryConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = grant_business_super_admin(
            create_test_user("concurrent-category-admin")
        )
        self.journal = Journal.objects.create(
            name="Concurrent Journal", slug="concurrent-journal", az_group="C"
        )

    def _run_concurrently(self, workers):
        barrier = threading.Barrier(len(workers))
        results = Queue()

        def run(worker):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                results.put(("success", worker()))
            except CategoryError as exc:
                results.put(("category_error", exc.code))
            except Exception as exc:  # pragma: no cover - surfaced by assertion
                results.put(("unexpected_error", repr(exc)))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run, args=(worker,)) for worker in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        return [results.get_nowait() for _ in workers]

    def test_concurrent_same_parent_slug_allows_only_one_create(self):
        journal_id = self.journal.pk
        actor_id = self.admin.pk

        def create(code):
            def worker():
                journal = Journal.objects.get(pk=journal_id)
                return create_category(
                    actor=get_user_model().objects.get(pk=actor_id),
                    journal=journal,
                    data={"name": code, "code": code, "slug": "same-slug"},
                    request_id=f"concurrent-create-{code}",
                ).category.pk

            return worker

        results = self._run_concurrently([create("FIRST"), create("SECOND")])

        self.assertEqual(
            sorted(status for status, _ in results),
            ["category_error", "success"],
        )
        self.assertIn(
            ("category_error", "CATEGORY_DUPLICATE_SLUG"),
            results,
        )
        self.assertEqual(
            JournalCategory.objects.filter(
                journal=self.journal, parent__isnull=True, slug="same-slug"
            ).count(),
            1,
        )

    def test_concurrent_moves_use_expected_version_without_path_drift(self):
        source = create_category(
            actor=self.admin,
            journal=self.journal,
            data={"name": "Source", "code": "SOURCE", "slug": "source"},
        ).category
        target_a = create_category(
            actor=self.admin,
            journal=self.journal,
            data={"name": "Target A", "code": "TARGET-A", "slug": "target-a"},
        ).category
        target_b = create_category(
            actor=self.admin,
            journal=self.journal,
            data={"name": "Target B", "code": "TARGET-B", "slug": "target-b"},
        ).category
        moving = create_category(
            actor=self.admin,
            journal=self.journal,
            parent=source,
            data={"name": "Moving", "code": "MOVING", "slug": "moving"},
        ).category
        descendant = create_category(
            actor=self.admin,
            journal=self.journal,
            parent=moving,
            data={"name": "Leaf", "code": "LEAF", "slug": "leaf"},
        ).category
        expected_version = moving.version
        actor_id = self.admin.pk

        def move(new_parent_id, request_id):
            def worker():
                return move_category(
                    actor=get_user_model().objects.get(pk=actor_id),
                    category_id=moving.pk,
                    new_parent_id=new_parent_id,
                    expected_version=expected_version,
                    request_id=request_id,
                ).category.path_cache

            return worker

        results = self._run_concurrently(
            [
                move(target_a.pk, "concurrent-move-a"),
                move(target_b.pk, "concurrent-move-b"),
            ]
        )

        self.assertEqual(
            sorted(status for status, _ in results),
            ["category_error", "success"],
        )
        self.assertIn(
            ("category_error", "CATEGORY_VERSION_CONFLICT"),
            results,
        )
        moving.refresh_from_db()
        descendant.refresh_from_db()
        self.assertIn(moving.parent_id, {target_a.pk, target_b.pk})
        expected_prefix = "target-a" if moving.parent_id == target_a.pk else "target-b"
        self.assertEqual(moving.path_cache, f"{expected_prefix}/moving")
        self.assertEqual(moving.depth, 2)
        self.assertEqual(descendant.path_cache, f"{expected_prefix}/moving/leaf")
        self.assertEqual(descendant.depth, 3)
        self.assertEqual(moving.version, expected_version + 1)
