from __future__ import annotations

from pathlib import Path

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ai_author_forum.journals.models import (
    ArticleImportJob,
    ArticleImportScope,
    ImportJobStatus,
    Journal,
    JournalStatus,
)
from ai_author_forum.journals.publishing import (
    save_import_package_for_background,
    start_article_import_preview_process,
    start_article_import_process,
)
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.permissions import is_global_admin
from ai_author_forum.site_settings.services import record_audit_event
from ai_author_forum.utils.admin_ui import (
    is_english_admin,
    translate_form_to_english,
)

from .import_forms import ArticleImportConfirmForm, ArticleImportUploadForm
from .import_permissions import (
    can_import_articles,
    can_override_suspicious_article_text,
    can_view_article_import_job,
)
from .import_services import (
    ArticleImportContext,
    confirm_article_import,
    create_article_import_preview_job,
    preview_article_import,
)
from .import_templates import (
    build_article_document_import_zip,
    build_article_import_csv,
    build_article_import_xlsx,
)


def _configure_english_article_import_forms(form, confirm_form, target_journal):
    labels = {
        "source_file": "Article import file",
        "default_journal": "Default journal",
        "document_title": "Document title",
        "document_slug": "Document slug",
        "document_article_type": "Article type",
        "document_authors": "Authors",
        "document_ai_co_authors": "AI co-authors",
        "document_publication_date": "Content publication date",
        "csv_encoding": "CSV encoding",
    }
    for field_name, label in labels.items():
        form.fields[field_name].label = label

    form.fields["source_file"].help_text = (
        "Supports XLSX, CSV, ZIP, DOCX, MD, and Markdown. Uploads run a "
        "security preflight first and write article drafts only after manual "
        "confirmation."
    )
    form.fields["default_journal"].help_text = (
        "Locked to the current journal by the server."
        if target_journal is not None
        else "Optional in global mode. Rows without journal_slug use this journal."
    )
    form.fields["default_journal"].label_from_instance = lambda journal: journal.name
    form.fields["document_article_type"].choices = (
        ("", "---------"),
        ("ai_article", "AI Article"),
        ("news", "News"),
        ("opinion", "Opinion"),
        ("review", "Review"),
        ("editorial", "Editorial"),
    )

    allow = confirm_form.fields["allow_suspicious_text"]
    allow.label = "Process suspicious text unchanged"
    allow.help_text = "Available only to the project lead or a superuser."
    confirm_form.fields["override_reason"].label = "Override reason"


def _import_permission_denied(request):
    if not can_import_articles(request.user):
        return HttpResponseForbidden("无文章导入权限。")
    return None


def _target_journal(request):
    value = request.GET.get("journal")
    if not value:
        return None
    return get_object_or_404(Journal, pk=value, status=JournalStatus.ACTIVE)


def _job_context(job):
    summary = job.summary or {}
    return {
        "job": job,
        "rows": job.rows.select_related("article", "article_page").all(),
        "metrics": {
            "total": job.total_rows,
            "created": summary.get("created_rows", 0),
            "updated": summary.get("updated_rows", 0),
            "skipped": summary.get("skipped_rows", 0),
            "failed": summary.get("failed_rows", job.failed_rows),
            "suspicious": summary.get("suspicious_text_count", 0),
        },
    }


def _mark_background_start_failed(job, *, actor, exc: Exception) -> None:
    job.status = ImportJobStatus.FAILED
    job.finished_at = timezone.now()
    job.notes = "后台任务启动失败，请检查服务日志后重试。"
    job.save(update_fields=["status", "finished_at", "notes", "updated_at"])
    record_audit_event(
        action=AuditAction.IMPORT,
        status=AuditStatus.FAILURE,
        actor=actor,
        target=job,
        message="文章导入后台任务启动失败",
        metadata={
            "error_type": exc.__class__.__name__,
            "source_format": job.source_format,
            "source_sha256": job.source_sha256,
        },
    )


def _document_defaults_from_form(form: ArticleImportUploadForm) -> dict:
    publication_date = form.cleaned_data.get("document_publication_date")
    return {
        "title": form.cleaned_data.get("document_title") or "",
        "slug": form.cleaned_data.get("document_slug") or "",
        "article_type": form.cleaned_data.get("document_article_type") or "",
        "authors": form.cleaned_data.get("document_authors") or "",
        "ai_co_authors": form.cleaned_data.get("document_ai_co_authors") or "",
        "publication_date": (publication_date.isoformat() if publication_date else ""),
    }


def _remove_queue_file(package_path: Path | None) -> None:
    if package_path is not None and package_path.is_file():
        package_path.unlink()


def _dashboard_job_url(job: ArticleImportJob) -> str:
    url = reverse("article_admin:import")
    query = f"job={job.pk}"
    if job.target_journal_id:
        query = f"journal={job.target_journal_id}&{query}"
    return f"{url}?{query}"


@require_http_methods(["GET", "POST"])
def article_import_dashboard(request):
    if denied := _import_permission_denied(request):
        return denied
    target_journal = _target_journal(request)
    scope = ArticleImportScope.JOURNAL if target_journal else ArticleImportScope.GLOBAL
    job = None
    if request.method == "POST":
        form = ArticleImportUploadForm(
            request.POST, request.FILES, target_journal=target_journal
        )
        if form.is_valid():
            source_file = form.cleaned_data["source_file"]
            suffix = Path(source_file.name or "").suffix.lower()
            is_direct_document = suffix in {".docx", ".md", ".markdown"}
            context = ArticleImportContext(
                scope=scope,
                target_journal_id=target_journal.pk if target_journal else None,
                default_journal_id=(
                    form.cleaned_data["default_journal"].pk
                    if form.cleaned_data.get("default_journal")
                    else None
                ),
                csv_encoding=form.cleaned_data.get("csv_encoding") or "auto",
                document_defaults=(
                    _document_defaults_from_form(form) if is_direct_document else {}
                ),
            )
            package_path = None
            try:
                if suffix in {".docx", ".md", ".markdown", ".zip"}:
                    job = create_article_import_preview_job(
                        source_file, context=context, operator=request.user
                    )
                    with job.source_file.open("rb") as locked_source:
                        package_path = save_import_package_for_background(locked_source)
                    start_article_import_preview_process(
                        package_path=package_path,
                        job_id=job.pk,
                        operator_id=request.user.pk,
                    )
                    messages.success(
                        request,
                        "后台预检已启动。页面会显示 VALIDATING 进度，完成后请人工确认导入草稿。",
                    )
                else:
                    job = preview_article_import(
                        source_file,
                        context=context,
                        operator=request.user,
                    )
                    messages.success(
                        request, "预校验完成。请核对逐行结果后再确认导入。"
                    )
                return redirect(_dashboard_job_url(job))
            except ValidationError as exc:
                if job is not None and job.status == ImportJobStatus.PENDING:
                    _mark_background_start_failed(job, actor=request.user, exc=exc)
                _remove_queue_file(package_path)
                form.add_error("source_file", "; ".join(exc.messages))
            except Exception as exc:
                if job is not None and job.status == ImportJobStatus.PENDING:
                    _mark_background_start_failed(job, actor=request.user, exc=exc)
                _remove_queue_file(package_path)
                form.add_error(
                    "source_file",
                    "后台预检启动失败，任务已标记为失败，请检查服务日志后重试。",
                )
        else:
            messages.error(request, "上传信息有误，请修正后重试。")
    else:
        form = ArticleImportUploadForm(target_journal=target_journal)
        job_id = request.GET.get("job")
        if job_id:
            candidate = get_object_or_404(ArticleImportJob, pk=job_id)
            if not can_view_article_import_job(request.user, candidate):
                return HttpResponseForbidden("无权查看该导入任务。")
            if candidate.import_scope == ArticleImportScope.JOURNAL:
                if (
                    target_journal is None
                    or candidate.target_journal_id != target_journal.pk
                ):
                    return HttpResponseForbidden("导入任务与当前子期刊范围不匹配。")
            elif target_journal is not None:
                return HttpResponseForbidden("全局导入任务不能在本刊模式中打开。")
            job = candidate

    recent = ArticleImportJob.objects.select_related(
        "target_journal", "operator"
    ).order_by("-created_at")
    if not is_global_admin(request.user):
        recent = recent.filter(operator=request.user)
    if target_journal:
        recent = recent.filter(target_journal=target_journal)
    confirm_form = ArticleImportConfirmForm(initial={"job_id": job.pk if job else None})
    if is_english_admin():
        translate_form_to_english(form)
        translate_form_to_english(confirm_form)
        _configure_english_article_import_forms(form, confirm_form, target_journal)

    context = {
        "title": "文章批量导入中心",
        "form": form,
        "confirm_form": confirm_form,
        "target_journal": target_journal,
        "scope": scope,
        "is_journal_scope": bool(target_journal),
        "can_override_suspicious_text": can_override_suspicious_article_text(
            request.user
        ),
        "upload_url": request.get_full_path(),
        "confirm_url": reverse("article_admin:import_confirm"),
        "status_url": reverse("article_admin:import_status"),
        "template_url": f"{reverse('article_admin:import_template')}?scope={scope}"
        + (f"&journal={target_journal.pk}" if target_journal else ""),
        "back_url": (
            reverse("journals:workspace", args=[target_journal.pk])
            if target_journal
            else reverse("article_admin:index")
        ),
        "recent_jobs": recent[:10],
    }
    if job:
        context.update(_job_context(job))
    template_name = (
        "wagtailadmin/articles/import_dashboard.en.html"
        if is_english_admin()
        else "wagtailadmin/articles/import_dashboard.html"
    )
    return render(request, template_name, context)


@require_POST
def article_import_confirm(request):
    if denied := _import_permission_denied(request):
        return denied
    form = ArticleImportConfirmForm(request.POST)
    if not form.is_valid():
        messages.error(request, "确认信息无效。")
        return redirect("article_admin:import")
    job = get_object_or_404(ArticleImportJob, pk=form.cleaned_data["job_id"])
    if not can_view_article_import_job(request.user, job):
        return HttpResponseForbidden("无权访问该导入任务。")
    confirmed = False
    package_path = None
    try:
        job = confirm_article_import(
            job,
            operator=request.user,
            allow_suspicious_text=form.cleaned_data.get("allow_suspicious_text", False),
            override_reason=form.cleaned_data.get("override_reason", ""),
        )
        confirmed = True
        with job.source_file.open("rb") as source_file:
            package_path = save_import_package_for_background(source_file)
        start_article_import_process(
            package_path=package_path,
            operator_id=request.user.pk,
            preview_job_id=job.pk,
        )
        messages.success(request, "导入任务已启动，页面会自动更新执行状态。")
    except ValidationError as exc:
        job.refresh_from_db()
        if confirmed or job.status == ImportJobStatus.PENDING:
            _mark_background_start_failed(job, actor=request.user, exc=exc)
        _remove_queue_file(package_path)
        messages.error(request, "; ".join(exc.messages))
    except Exception as exc:
        job.refresh_from_db()
        if confirmed or job.status == ImportJobStatus.PENDING:
            _mark_background_start_failed(job, actor=request.user, exc=exc)
        _remove_queue_file(package_path)
        messages.error(
            request, "后台任务启动失败，任务已标记为失败，请检查日志后重试。"
        )
    return redirect(_dashboard_job_url(job))


@require_GET
def article_import_status(request):
    if denied := _import_permission_denied(request):
        return denied
    job = get_object_or_404(ArticleImportJob, pk=request.GET.get("job_id"))
    if not can_view_article_import_job(request.user, job):
        return HttpResponseForbidden("无权访问该导入任务。")
    summary = job.summary or {}
    return JsonResponse(
        {
            "id": job.pk,
            "status": job.status,
            "terminal": job.status
            in {
                ImportJobStatus.READY,
                ImportJobStatus.COMPLETED,
                ImportJobStatus.FAILED,
            },
            "total_rows": job.total_rows,
            "success_rows": job.success_rows,
            "failed_rows": job.failed_rows,
            "preview_processed_rows": summary.get("preview_processed_rows", 0),
            "preview_total_rows": summary.get("preview_total_rows", 0),
            "conversion_warning_count": summary.get("conversion_warning_count", 0),
            "source_format": job.source_format,
            "summary": summary,
            "notes": job.notes,
            "errors_url": (
                reverse("article_admin:import_errors", args=[job.pk])
                if job.error_report
                else ""
            ),
        }
    )


@require_GET
def article_import_template(request):
    if denied := _import_permission_denied(request):
        return denied
    scope = request.GET.get("scope", ArticleImportScope.GLOBAL)
    journal_slug = ""
    if scope == ArticleImportScope.JOURNAL:
        journal = get_object_or_404(
            Journal, pk=request.GET.get("journal"), status=JournalStatus.ACTIVE
        )
        journal_slug = journal.slug
    else:
        scope = ArticleImportScope.GLOBAL
    format_name = request.GET.get("format", "xlsx").lower()
    if format_name == "csv":
        content = build_article_import_csv(scope=scope, journal_slug=journal_slug)
        content_type = "text/csv; charset=utf-8"
        filename = f"article-import-{scope}.csv"
    elif format_name == "document-zip":
        content = build_article_document_import_zip(
            scope=scope, journal_slug=journal_slug
        )
        content_type = "application/zip"
        filename = "article-document-import-template.zip"
    else:
        content = build_article_import_xlsx(scope=scope, journal_slug=journal_slug)
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        filename = f"article-import-{scope}.xlsx"
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_GET
def article_import_errors(request, job_id):
    if denied := _import_permission_denied(request):
        return denied
    job = get_object_or_404(ArticleImportJob, pk=job_id)
    if not can_view_article_import_job(request.user, job):
        return HttpResponseForbidden("无权访问该导入任务。")
    if not job.error_report:
        raise Http404
    response = FileResponse(
        job.error_report.open("rb"), content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="article-import-{job.pk}-errors.csv"'
    )
    return response
