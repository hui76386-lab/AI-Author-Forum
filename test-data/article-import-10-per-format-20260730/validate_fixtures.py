from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
TEMP_ROOT = Path(tempfile.mkdtemp(prefix="article-import-fixture-validation-"))
TEMP_DB = TEMP_ROOT / "validation.sqlite3"
TEMP_MEDIA = TEMP_ROOT / "media"
shutil.copy2(PROJECT_ROOT / "db.sqlite3", TEMP_DB)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai_author_forum.settings.dev")
os.environ["DATABASE_URL"] = "sqlite:///" + TEMP_DB.as_posix()
os.environ["MEDIA_ROOT"] = str(TEMP_MEDIA)

import django

django.setup()

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from ai_author_forum.articles.import_services import (
    ArticleImportContext,
    preview_article_import,
)
from ai_author_forum.journals.models import (
    ArticleImportScope,
    ImportJobStatus,
    Journal,
    JournalStatus,
)

CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".zip": "application/zip",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
}


def validate_file(
    path: Path, *, journal_id: int | None = None, docx: bool = False
) -> dict:
    defaults = {"article_type": "ai_article"} if docx else {}
    context = ArticleImportContext(
        scope=ArticleImportScope.GLOBAL,
        default_journal_id=journal_id,
        csv_encoding="auto",
        document_defaults=defaults,
    )
    upload = SimpleUploadedFile(
        path.name, path.read_bytes(), content_type=CONTENT_TYPES[path.suffix.lower()]
    )
    job = preview_article_import(upload, context=context, operator=OPERATOR)
    rows = list(job.rows.order_by("row_no"))
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "status": job.status,
        "row_count": len(rows),
        "failed_rows": sum(1 for row in rows if row.error_code),
        "errors": [
            {"row": row.row_no, "code": row.error_code, "message": row.error_message}
            for row in rows
            if row.error_code
        ],
        "summary": job.summary,
    }


try:
    journal = Journal.objects.get(
        slug="foundation-model-systems", status=JournalStatus.ACTIVE
    )
    user_model = get_user_model()
    OPERATOR = user_model.objects.filter(is_superuser=True, is_active=True).first()
    if OPERATOR is None:
        OPERATOR = user_model.objects.create_superuser(
            username="fixture-validator",
            email="fixture-validator@example.invalid",
            password=None,
        )

    targets: list[tuple[Path, int | None, bool]] = [
        (ROOT / "01-xlsx" / "articles-10.xlsx", None, False),
        (ROOT / "02-csv" / "articles-10.csv", None, False),
        (ROOT / "03-zip" / "articles-10-mixed-documents.zip", None, False),
    ]
    targets.extend(
        (path, journal.pk, True) for path in sorted((ROOT / "04-docx").glob("*.docx"))
    )
    targets.extend(
        (path, journal.pk, False)
        for path in sorted((ROOT / "05-markdown").glob("*.md"))
    )

    results = []
    for path, journal_id, docx in targets:
        result = validate_file(path, journal_id=journal_id, docx=docx)
        results.append(result)
        print(
            f"{result['status']:>8} rows={result['row_count']:>2} failed={result['failed_rows']:>2} {result['path']}"
        )

    report = {
        "validated_on": "2026-07-30",
        "database": "temporary copy; production/local database not modified",
        "total_uploads": len(results),
        "total_preview_rows": sum(item["row_count"] for item in results),
        "ready_uploads": sum(
            item["status"] == ImportJobStatus.READY for item in results
        ),
        "failed_rows": sum(item["failed_rows"] for item in results),
        "all_ready": all(
            item["status"] == ImportJobStatus.READY and item["failed_rows"] == 0
            for item in results
        ),
        "results": results,
    }
    (ROOT / "validation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "total_uploads",
                    "total_preview_rows",
                    "ready_uploads",
                    "failed_rows",
                    "all_ready",
                )
            },
            ensure_ascii=False,
        )
    )
    if not report["all_ready"]:
        raise SystemExit(1)
finally:
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
