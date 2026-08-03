from __future__ import annotations

import csv
import io
import tracemalloc
import zipfile
from pathlib import Path
from time import perf_counter

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

from ai_author_forum.articles.document_importers import (
    MAX_CONVERTED_HTML_SIZE,
    MAX_DOCX_SIZE,
    MAX_LOGICAL_EXTRACTED_SIZE,
    MAX_MARKDOWN_SIZE,
    ConvertedArticleDocument,
    DocumentImportError,
    ImportExtractionBudget,
    _finalize,
    convert_article_document,
    detect_document_format,
)
from ai_author_forum.articles.import_services import (
    MAX_ZIP_TOTAL_SIZE,
    ArticleImportContext,
    ArticleImportValidationError,
    _extract_zip,
    _validate_html,
    confirm_article_import,
    execute_confirmed_article_import,
    preview_article_import,
)
from ai_author_forum.journals.models import ArticleImportScope, ImportJobStatus

DOCUMENT_SCENARIOS = (
    "5mb-markdown",
    "25mb-docx",
    "100-docx",
    "200-docx",
    "200-markdown",
    "201-documents",
    "200-documents-500-images",
    "250mb-logical-extraction",
    "10mb-converted-html",
    "100-percent-errors",
    "terminal-reentry",
)

CSV_FIELDS = (
    "journal_slug",
    "title",
    "slug",
    "article_type",
    "authors",
    "body_html",
    "html_file",
    "docx_file",
    "markdown_file",
)

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63f8ffffff7f0009fb03fd2a86e38a0000000049454e44ae426082"
)


class QueryCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        return execute(sql, params, many, context)


def _seconds(started: float) -> float:
    return round(perf_counter() - started, 6)


def _disk_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _error_code(exc: Exception) -> str:
    return str(getattr(exc, "code", "") or exc.__class__.__name__)


def _minimal_docx(*, text: str = "Document benchmark body", padding: int = 0) -> bytes:
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="bin" ContentType="application/octet-stream"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    document = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:sectPr/></w:body>
</w:document>"""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types, zipfile.ZIP_DEFLATED)
        archive.writestr("_rels/.rels", root_rels, zipfile.ZIP_DEFLATED)
        archive.writestr("word/document.xml", document, zipfile.ZIP_DEFLATED)
        archive.writestr("customXml/padding.bin", b"\0" * padding, zipfile.ZIP_STORED)
    return stream.getvalue()


def _docx_at_size(size: int) -> bytes:
    empty = _minimal_docx(padding=0)
    if len(empty) > size:
        raise ValueError("DOCX fixture overhead exceeds requested size")
    payload = _minimal_docx(padding=size - len(empty))
    if len(payload) != size:
        correction = size - len(payload)
        payload = _minimal_docx(padding=size - len(empty) + correction)
    if len(payload) != size:
        raise ValueError("Could not construct exact-size DOCX fixture")
    return payload


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def _zip_bytes(files: dict[str, bytes], rows: list[dict[str, str]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("articles.csv", _csv_bytes(rows))
        for name, payload in files.items():
            archive.writestr(name, payload)
    return stream.getvalue()


def _row(
    *, journal_slug: str, scenario: str, index: int, source_field: str, source: str
):
    row = {field: "" for field in CSV_FIELDS}
    row.update(
        {
            "journal_slug": journal_slug,
            "title": f"Benchmark {scenario} {index}",
            "slug": f"benchmark-{scenario}-{index}",
            "article_type": "news",
            "authors": "Capacity benchmark",
            source_field: source,
        }
    )
    return row


def _document_package(
    *, journal_slug: str, scenario: str, count: int, format_name: str
):
    files: dict[str, bytes] = {}
    rows = []
    if format_name == "docx":
        field = "docx_file"
        suffix = "docx"
    else:
        field = "markdown_file"
        suffix = "md"
    for index in range(1, count + 1):
        name = f"documents/{index:04d}.{suffix}"
        if format_name == "docx":
            payload = _minimal_docx(text=f"Document benchmark body {index}")
        else:
            payload = f"# Benchmark document {index}\n\nSafe body {index}.\n".encode()
        files[name] = payload
        rows.append(
            _row(
                journal_slug=journal_slug,
                scenario=scenario,
                index=index,
                source_field=field,
                source=name,
            )
        )
    return _zip_bytes(files, rows)


def _image_package(*, journal_slug: str):
    files = {"media/pixel.png": PNG_1X1}
    rows = []
    for index in range(1, 201):
        image_count = 3 if index <= 100 else 2
        name = f"documents/{index:04d}.md"
        references = "\n".join(
            f"![pixel {item}](../media/pixel.png)" for item in range(1, image_count + 1)
        )
        files[name] = f"# Image benchmark {index}\n\n{references}\n".encode()
        rows.append(
            _row(
                journal_slug=journal_slug,
                scenario="200-documents-500-images",
                index=index,
                source_field="markdown_file",
                source=name,
            )
        )
    return _zip_bytes(files, rows)


def _error_package(*, journal_slug: str):
    files = {}
    rows = []
    for index in range(1, 201):
        name = f"documents/{index:04d}.md"
        files[name] = b"# Invalid\n\n![remote](https://example.invalid/image.png)\n"
        rows.append(
            _row(
                journal_slug=journal_slug,
                scenario="100-percent-errors",
                index=index,
                source_field="markdown_file",
                source=name,
            )
        )
    return _zip_bytes(files, rows)


def _logical_boundary_package(*, journal_slug: str):
    rows = [
        _row(
            journal_slug=journal_slug,
            scenario="250mb-logical-extraction",
            index=1,
            source_field="html_file",
            source="documents/body.html",
        )
    ]
    manifest = _csv_bytes(rows)
    html = b"<p>Logical extraction boundary.</p>"
    remaining = MAX_ZIP_TOTAL_SIZE - len(manifest) - len(html)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("articles.csv", manifest)
        archive.writestr("documents/body.html", html)
        index = 1
        while remaining:
            chunk_size = min(MAX_DOCX_SIZE, remaining)
            archive.writestr(f"padding/{index:02d}.dat", b"\0" * chunk_size)
            remaining -= chunk_size
            index += 1
    return stream.getvalue()


def build_scenario_source(name: str, *, journal_slug: str) -> dict:
    if name == "5mb-markdown":
        return {
            "filename": "five-megabytes.md",
            "data": b"a" * MAX_MARKDOWN_SIZE,
            "expected": "ready",
            "expected_rows": 1,
            "expected_failed_rows": 1,
            "expected_row_error_code": "ARTICLE_DOCUMENT_CONVERSION_FAILED",
            "note": "The 900,000-character limit is intentionally stricter than the 5 MB byte limit, so preview completes with one failed row.",
        }
    if name == "25mb-docx":
        return {
            "filename": "twenty-five-megabytes.docx",
            "data": _docx_at_size(MAX_DOCX_SIZE),
            "expected": "ready",
        }
    if name in {"100-docx", "200-docx"}:
        count = int(name.split("-", 1)[0])
        return {
            "filename": f"{name}.zip",
            "data": _document_package(
                journal_slug=journal_slug,
                scenario=name,
                count=count,
                format_name="docx",
            ),
            "expected": "ready",
            "expected_rows": count,
            "expected_success_rows": count,
        }
    if name == "200-markdown":
        return {
            "filename": "200-markdown.zip",
            "data": _document_package(
                journal_slug=journal_slug,
                scenario=name,
                count=200,
                format_name="markdown",
            ),
            "expected": "ready",
            "expected_rows": 200,
            "expected_success_rows": 200,
        }
    if name == "201-documents":
        return {
            "filename": "201-documents.zip",
            "data": _document_package(
                journal_slug=journal_slug,
                scenario=name,
                count=201,
                format_name="markdown",
            ),
            "expected": "rejected",
            "expected_error_code": "ARTICLE_DOCX_LIMIT_EXCEEDED",
        }
    if name == "200-documents-500-images":
        return {
            "filename": "200-documents-500-images.zip",
            "data": _image_package(journal_slug=journal_slug),
            "expected": "ready",
            "expected_rows": 200,
            "expected_success_rows": 200,
            "expected_images": 500,
        }
    if name == "250mb-logical-extraction":
        return {
            "filename": "250mb-logical-extraction.zip",
            "data": _logical_boundary_package(journal_slug=journal_slug),
            "expected": "ready",
            "expected_rows": 1,
        }
    if name == "100-percent-errors":
        return {
            "filename": "100-percent-errors.zip",
            "data": _error_package(journal_slug=journal_slug),
            "expected": "ready",
            "expected_rows": 200,
            "expected_failed_rows": 200,
        }
    if name == "terminal-reentry":
        return {
            "filename": "terminal-reentry.md",
            "data": b"# Terminal reentry\n\nDraft body.\n",
            "expected": "completed",
            "execute": True,
        }
    if name == "10mb-converted-html":
        return {"synthetic": True, "expected": "boundary-accepted"}
    raise ValueError(f"Unknown document benchmark scenario: {name}")


class DocumentCapacityRunner:
    def __init__(self, *, user, journal, temp_root: Path):
        self.user = user
        self.journal = journal
        self.temp_root = temp_root

    def run(self, scenario_names: list[str]) -> list[dict]:
        return [self.run_one(name) for name in scenario_names]

    def run_one(self, name: str) -> dict:
        source = build_scenario_source(name, journal_slug=self.journal.slug)
        if source.get("synthetic"):
            return self._run_html_boundary(name, source)
        return self._run_source(name, source)

    def _run_html_boundary(self, name: str, source: dict) -> dict:
        metrics = self._empty_metrics()
        tracemalloc.start()
        started = perf_counter()
        base = "<p>x</p><!--"
        suffix = "-->"
        payload_size = (
            MAX_CONVERTED_HTML_SIZE - len(base.encode()) - len(suffix.encode())
        )
        exact = base + ("a" * payload_size) + suffix
        conversion_started = perf_counter()
        accepted = _finalize(
            ConvertedArticleDocument(
                source_format="markdown",
                source_path="synthetic-boundary.md",
                source_sha256="synthetic",
                converter_name="capacity-synthetic",
                converter_version="1",
                html=exact,
            )
        )
        metrics["document_conversion_seconds"] = _seconds(conversion_started)
        over_code = ""
        over_started = perf_counter()
        try:
            _finalize(
                ConvertedArticleDocument(
                    source_format="markdown",
                    source_path="synthetic-over-limit.md",
                    source_sha256="synthetic",
                    converter_name="capacity-synthetic",
                    converter_version="1",
                    html=exact + "a",
                )
            )
        except DocumentImportError as exc:
            over_code = exc.code
        metrics["html_validation_seconds"] = _seconds(over_started)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        metrics["peak_memory_bytes"] = peak
        metrics["preview_total_seconds"] = _seconds(started)
        passed = (
            len(accepted.html.encode("utf-8")) == MAX_CONVERTED_HTML_SIZE
            and over_code == "ARTICLE_DOCUMENT_HTML_TOO_LARGE"
        )
        return {
            "scenario": name,
            "expected": source["expected"],
            "observed": "boundary-accepted" if passed else "failed",
            "passed": passed,
            "source_bytes": 0,
            "boundary_bytes": MAX_CONVERTED_HTML_SIZE,
            "over_limit_error_code": over_code,
            "metrics": metrics,
        }

    def _run_source(self, name: str, source: dict) -> dict:
        scenario_root = self.temp_root / name
        scenario_root.mkdir(parents=True, exist_ok=True)
        metrics = self._empty_metrics()
        peak_disk = 0
        stage_errors = []
        converted = []
        tracemalloc.start()

        upload_started = perf_counter()
        saved = scenario_root / source["filename"]
        saved.write_bytes(source["data"])
        metrics["upload_save_seconds"] = _seconds(upload_started)
        peak_disk = max(peak_disk, _disk_bytes(scenario_root))

        stage_root = scenario_root / "stage"
        stage_root.mkdir()
        package_root = stage_root
        document_paths = []

        extraction_started = perf_counter()
        try:
            if saved.suffix.lower() == ".zip":
                package_root = _extract_zip(
                    source["data"], stage_root, ImportExtractionBudget()
                )
                document_paths = sorted(
                    path
                    for path in package_root.rglob("*")
                    if path.is_file()
                    and path.suffix.lower() in {".docx", ".md", ".markdown"}
                )
            else:
                copied = stage_root / saved.name
                copied.write_bytes(source["data"])
                document_paths = [copied]
        except Exception as exc:
            stage_errors.append(
                {"stage": "extraction_preflight", "error_code": _error_code(exc)}
            )
        metrics["extraction_preflight_seconds"] = _seconds(extraction_started)
        peak_disk = max(peak_disk, _disk_bytes(scenario_root))

        detection_started = perf_counter()
        for path in document_paths:
            try:
                detect_document_format(path, path.suffix)
            except Exception as exc:
                stage_errors.append(
                    {"stage": "format_detection", "error_code": _error_code(exc)}
                )
        metrics["format_detection_seconds"] = _seconds(detection_started)

        conversion_budget = ImportExtractionBudget(
            max_total_bytes=int(
                getattr(
                    settings,
                    "ARTICLE_IMPORT_MAX_LOGICAL_EXTRACTED_SIZE",
                    MAX_LOGICAL_EXTRACTED_SIZE,
                )
            )
        )
        conversion_started = perf_counter()
        for path in document_paths:
            try:
                converted.append(
                    convert_article_document(
                        path,
                        package_root=package_root,
                        generated_root=package_root / "_converted_assets",
                        budget=conversion_budget,
                        direct_upload=saved.suffix.lower() != ".zip",
                        source_bytes_counted=saved.suffix.lower() == ".zip",
                    )
                )
            except Exception as exc:
                stage_errors.append(
                    {"stage": "document_conversion", "error_code": _error_code(exc)}
                )
        metrics["document_conversion_seconds"] = _seconds(conversion_started)
        peak_disk = max(peak_disk, _disk_bytes(scenario_root))

        html_started = perf_counter()
        for item in converted:
            try:
                _validate_html(
                    item.html,
                    root=package_root,
                    preview=True,
                    image_cache={},
                )
            except Exception as exc:
                stage_errors.append(
                    {"stage": "html_validation", "error_code": _error_code(exc)}
                )
        metrics["html_validation_seconds"] = _seconds(html_started)

        document_defaults = None
        if saved.suffix.lower() != ".zip":
            document_defaults = {
                "title": f"Benchmark {name}",
                "slug": f"benchmark-{name}-direct",
                "article_type": "news",
                "authors": "Capacity benchmark",
            }
        context = ArticleImportContext(
            scope=ArticleImportScope.GLOBAL,
            default_journal_id=self.journal.pk,
            document_defaults=document_defaults,
        )
        upload = SimpleUploadedFile(
            source["filename"], source["data"], content_type="application/octet-stream"
        )
        counter = QueryCounter()
        preview_started = perf_counter()
        job = None
        preview_error_code = ""
        try:
            with connection.execute_wrapper(counter):
                job = preview_article_import(
                    upload, context=context, operator=self.user
                )
        except Exception as exc:
            preview_error_code = _error_code(exc)
        metrics["preview_total_seconds"] = _seconds(preview_started)
        metrics["sql_queries"] = counter.count

        observed = "rejected" if job is None else str(job.status)
        preview = {
            "status": observed,
            "error_code": preview_error_code,
            "total_rows": 0,
            "success_rows": 0,
            "failed_rows": 0,
            "document_count": 0,
            "generated_image_count": 0,
            "error_codes": [],
        }
        if job is not None:
            job.refresh_from_db()
            preview.update(
                {
                    "status": job.status,
                    "total_rows": job.total_rows,
                    "success_rows": job.success_rows,
                    "failed_rows": job.failed_rows,
                    "document_count": int((job.summary or {}).get("document_count", 0)),
                    "generated_image_count": int(
                        (job.summary or {}).get("generated_image_count", 0)
                    ),
                }
            )
            metrics["error_report_bytes"] = (
                job.error_report.size
                if job.error_report and job.error_report.name
                else 0
            )
            if job.error_report and job.error_report.name:
                with job.error_report.open("rb") as report_file:
                    report_text = report_file.read().decode("utf-8-sig")
                preview["error_codes"] = sorted(
                    {
                        row.get("error_code", "")
                        for row in csv.DictReader(io.StringIO(report_text))
                        if row.get("error_code")
                    }
                )
            metrics["generated_image_count"] = int(
                (job.summary or {}).get("generated_image_count", 0)
            )

        write = None
        reentry = None
        if (
            source.get("execute")
            and job is not None
            and job.status == ImportJobStatus.READY
        ):
            confirm_article_import(job, operator=self.user)
            write_counter = QueryCounter()
            write_started = perf_counter()
            with connection.execute_wrapper(write_counter):
                execute_confirmed_article_import(job, operator=self.user)
            metrics["write_total_seconds"] = _seconds(write_started)
            metrics["sql_queries"] += write_counter.count
            job.refresh_from_db()
            observed = job.status
            write = {"status": job.status, "summary": job.summary}
            before = (job.status, job.total_rows, job.success_rows, job.failed_rows)
            reentry_code = ""
            reentry_started = perf_counter()
            try:
                execute_confirmed_article_import(job, operator=self.user)
            except ArticleImportValidationError as exc:
                reentry_code = exc.code
            job.refresh_from_db()
            after = (job.status, job.total_rows, job.success_rows, job.failed_rows)
            reentry = {
                "seconds": _seconds(reentry_started),
                "error_code": reentry_code,
                "terminal_state_unchanged": before == after,
            }

        peak_disk = max(peak_disk, _disk_bytes(scenario_root))
        _, peak_memory = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        metrics["peak_memory_bytes"] = peak_memory
        metrics["temporary_disk_peak_bytes"] = peak_disk

        passed = self._matches_expected(source, observed, preview, reentry)
        return {
            "scenario": name,
            "expected": source["expected"],
            "expected_error_code": source.get("expected_error_code", ""),
            "observed": observed,
            "passed": passed,
            "source_name": source["filename"],
            "source_bytes": len(source["data"]),
            "note": source.get("note", ""),
            "stage_errors": stage_errors,
            "preview": preview,
            "write": write,
            "reentry": reentry,
            "metrics": metrics,
        }

    @staticmethod
    def _empty_metrics():
        return {
            "upload_save_seconds": 0.0,
            "format_detection_seconds": 0.0,
            "extraction_preflight_seconds": 0.0,
            "document_conversion_seconds": 0.0,
            "html_validation_seconds": 0.0,
            "preview_total_seconds": 0.0,
            "write_total_seconds": None,
            "peak_memory_bytes": 0,
            "sql_queries": 0,
            "temporary_disk_peak_bytes": 0,
            "generated_image_count": 0,
            "error_report_bytes": 0,
        }

    @staticmethod
    def _matches_expected(source, observed, preview, reentry):
        expected = source["expected"]
        if expected == "rejected":
            return observed == "rejected" and preview["error_code"] == source.get(
                "expected_error_code"
            )
        if expected == "ready":
            if observed != ImportJobStatus.READY:
                return False
            if source.get("expected_rows") is not None and (
                preview["total_rows"] != source["expected_rows"]
            ):
                return False
            if source.get("expected_failed_rows") is not None and (
                preview["failed_rows"] != source["expected_failed_rows"]
            ):
                return False
            if source.get("expected_success_rows") is not None and (
                preview["success_rows"] != source["expected_success_rows"]
            ):
                return False
            if source.get("expected_images") is not None and (
                preview.get("generated_image_count", 0) != source["expected_images"]
            ):
                return False
            if source.get("expected_row_error_code") is not None and (
                source["expected_row_error_code"] not in preview.get("error_codes", [])
            ):
                return False
            return True
        if expected == "completed":
            return bool(
                observed == ImportJobStatus.COMPLETED
                and reentry
                and reentry["error_code"] == "ARTICLE_IMPORT_STATE_INVALID"
                and reentry["terminal_state_unchanged"]
            )
        return False
