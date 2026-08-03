from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ai_author_forum.articles.category_services import (
    validate_article_category_revision,
)
from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.journals.category_services import resolve_category
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

TRUTHY = {"1", "true", "yes", "y"}


class Command(BaseCommand):
    help = "Validate, apply, resume, or roll back a reviewed category migration CSV."

    def add_arguments(self, parser):
        parser.add_argument("--input")
        parser.add_argument("--batch-id", required=True)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--rollback", action="store_true")
        parser.add_argument("--article-id-from", type=int)
        parser.add_argument("--article-id-to", type=int)
        parser.add_argument("--confirm-production", action="store_true")

    def handle(self, *args, **options):
        batch_id = options["batch_id"]
        archive = Path(settings.BASE_DIR, "backups", "category-migrations", batch_id)
        archive.mkdir(parents=True, exist_ok=True)
        if options["rollback"]:
            return self._rollback(archive, batch_id)
        if not options.get("input"):
            raise CommandError("--input is required unless --rollback is used.")
        source = Path(options["input"]).resolve()
        if not source.is_file():
            raise CommandError(f"Input CSV does not exist: {source}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        rows = self._rows(source, batch_id, options)
        receipt_path = archive / "validation.json"
        if options["dry_run"]:
            report = self._validate(rows)
            receipt = {
                "batch_id": batch_id,
                "sha256": digest,
                "input": source.name,
                **report,
            }
            receipt_path.write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            shutil.copy2(source, archive / "reviewed-input.csv")
            self._audit(
                batch_id,
                AuditStatus.SUCCESS if not report["failed"] else AuditStatus.FAILURE,
                "dry_run",
                receipt,
            )
            self.stdout.write(json.dumps(receipt, ensure_ascii=False))
            if report["failed"]:
                raise CommandError(
                    f"Migration dry-run failed for {report['failed']} row(s)."
                )
            return
        if not settings.DEBUG and not options["confirm_production"]:
            raise CommandError("Production apply requires --confirm-production.")
        if not receipt_path.is_file():
            raise CommandError(
                "Run --dry-run successfully before apply; validation receipt is missing."
            )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("batch_id") != batch_id or receipt.get("sha256") != digest:
            raise CommandError(
                "Input hash or batch ID does not match the validated dry-run receipt."
            )
        if receipt.get("failed"):
            raise CommandError(
                "Validated receipt contains failed rows; correct and dry-run again."
            )
        shutil.copy2(source, archive / "applied-input.csv")
        progress_path = archive / "progress.json"
        rollback_path = archive / "rollback.json"
        progress = self._load(
            progress_path, {"completed_article_ids": [], "results": []}
        )
        rollback = self._load(rollback_path, {"articles": {}})
        completed = set(progress["completed_article_ids"])
        for row in rows:
            article_id = int(row["article_id"])
            if article_id in completed:
                continue
            try:
                with transaction.atomic():
                    article, primary, related = self._resolve(row)
                    rollback["articles"].setdefault(
                        str(article_id), self._snapshot(article)
                    )
                    payload = [{"category_id": primary.pk, "is_primary": True}] + [
                        {"category_id": item.pk, "is_primary": False}
                        for item in related
                    ]
                    validate_article_category_revision(
                        article=article,
                        revision_content={"category_assignments": payload},
                        action="submit",
                    )
                    article.category_assignments.all().delete()
                    for index, item in enumerate([primary, *related]):
                        ArticleCategoryAssignment.objects.create(
                            article=article,
                            category=item,
                            is_primary=index == 0,
                            sort_order=index,
                        )
                    revision = article.save_revision(
                        changed=True, bypass_article_permission_check=True
                    )
                completed.add(article_id)
                progress["completed_article_ids"] = sorted(completed)
                progress["results"].append(
                    {
                        "article_id": article_id,
                        "status": "applied",
                        "revision_id": revision.pk,
                    }
                )
                rollback_path.write_text(
                    json.dumps(rollback, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                progress_path.write_text(
                    json.dumps(progress, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception as exc:
                error = self._error_payload(exc)
                progress["results"].append(
                    {"article_id": article_id, "status": "failed", **error}
                )
                progress_path.write_text(
                    json.dumps(progress, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                self._audit(
                    batch_id,
                    AuditStatus.FAILURE,
                    "apply",
                    {"article_id": article_id, **error, "sha256": digest},
                )
                raise CommandError(
                    f"Article {article_id} failed [{error['code']}]: {error['error']}"
                ) from exc
        summary = {
            "batch_id": batch_id,
            "sha256": digest,
            "applied": len(completed),
            "progress": str(progress_path),
        }
        self._audit(batch_id, AuditStatus.SUCCESS, "apply", summary)
        self.stdout.write(json.dumps(summary, ensure_ascii=False))

    def _rows(self, source, batch_id, options):
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        selected = []
        for row in rows:
            if row.get("migration_batch_id") != batch_id:
                raise CommandError("CSV migration_batch_id does not match --batch-id.")
            article_id = int(row["article_id"])
            if (
                options["article_id_from"] is not None
                and article_id < options["article_id_from"]
            ):
                continue
            if (
                options["article_id_to"] is not None
                and article_id > options["article_id_to"]
            ):
                continue
            selected.append(row)
        return selected

    def _validate(self, rows):
        failures = []
        for row in rows:
            try:
                self._resolve(row)
            except Exception as exc:
                failures.append(
                    {"article_id": row.get("article_id"), **self._error_payload(exc)}
                )
        return {
            "total": len(rows),
            "valid": len(rows) - len(failures),
            "failed": len(failures),
            "failures": failures,
        }

    def _error_payload(self, exc):
        code = getattr(exc, "code", None)
        message = str(exc)
        if not code:
            match = re.match(r"^([A-Z][A-Z0-9_]+)(?::|$)", message)
            code = match.group(1) if match else "CATEGORY_MIGRATION_FAILED"
        return {"code": str(code), "error": message}

    def _resolve(self, row):
        article = ArticlePage.objects.select_related("primary_journal").get(
            pk=int(row["article_id"])
        )
        if article.primary_journal.slug != row.get("primary_journal_code"):
            raise CommandError(
                "CATEGORY_CROSS_JOURNAL: article journal differs from CSV."
            )
        confidence = float(row.get("confidence") or 0)
        requires_manual = (row.get("requires_manual_review") or "").casefold() in TRUTHY
        confirmed = (row.get("manual_confirmed") or "").casefold() in TRUTHY
        if (confidence < 0.80 or requires_manual) and not confirmed:
            raise CommandError(
                "MANUAL_REVIEW_REQUIRED: confidence below 0.80 or row flagged for review."
            )
        primary_code = (row.get("suggested_primary_category_code") or "").strip()
        if not primary_code:
            raise CommandError("ARTICLE_PRIMARY_CATEGORY_REQUIRED")
        primary = resolve_category(
            journal=article.primary_journal, code=primary_code
        ).category
        related = []
        seen = {primary.pk}
        for code in filter(
            None,
            (
                item.strip()
                for item in (row.get("suggested_related_category_codes") or "").split(
                    ";"
                )
            ),
        ):
            category = resolve_category(
                journal=article.primary_journal, code=code
            ).category
            if category.pk in seen:
                raise CommandError("ARTICLE_DUPLICATE_CATEGORY")
            seen.add(category.pk)
            related.append(category)
        payload = [{"category_id": primary.pk, "is_primary": True}] + [
            {"category_id": item.pk, "is_primary": False} for item in related
        ]
        validate_article_category_revision(
            article=article,
            revision_content={"category_assignments": payload},
            action="submit",
        )
        return article, primary, related

    def _snapshot(self, article):
        return [
            {
                "category_id": item.category_id,
                "is_primary": item.is_primary,
                "sort_order": item.sort_order,
            }
            for item in article.category_assignments.order_by("sort_order", "pk")
        ]

    def _rollback(self, archive, batch_id):
        path = archive / "rollback.json"
        if not path.is_file():
            raise CommandError("Rollback snapshot does not exist for this batch.")
        data = json.loads(path.read_text(encoding="utf-8"))
        restored = 0
        for article_id, assignments in data.get("articles", {}).items():
            with transaction.atomic():
                article = ArticlePage.objects.get(pk=int(article_id))
                article.category_assignments.all().delete()
                for item in assignments:
                    ArticleCategoryAssignment.objects.create(article=article, **item)
                article.save_revision(
                    changed=True, bypass_article_permission_check=True
                )
            restored += 1
        summary = {"batch_id": batch_id, "restored": restored}
        self._audit(batch_id, AuditStatus.SUCCESS, "rollback", summary)
        self.stdout.write(json.dumps(summary, ensure_ascii=False))

    def _load(self, path, default):
        return (
            json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
        )

    def _audit(self, batch_id, status, operation, metadata):
        AuditLog.record(
            action=(
                AuditAction.ROLLBACK
                if operation == "rollback"
                else AuditAction.CONFIGURE
            ),
            status=status,
            target_type="ArticleCategoryMigration",
            target_id=batch_id,
            target_label=batch_id,
            request_id=batch_id,
            message=f"Historical category migration {operation}.",
            metadata={"operation": operation, **metadata},
        )
