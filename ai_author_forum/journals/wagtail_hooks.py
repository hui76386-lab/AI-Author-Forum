import uuid
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from ai_author_forum.journals.category_admin import category_admin, category_audit
from ai_author_forum.journals.forms import ConfirmImportForm, ImportPackageForm
from ai_author_forum.journals.import_templates import build_import_template_package
from ai_author_forum.journals.issues import (
    archive_issue,
    publish_issue,
    rollback_issue,
    set_current_issue,
)
from ai_author_forum.journals.models import (
    ArticleImportJob,
    ImportJobStatus,
    JournalImportJob,
    PublicationIssue,
)
from ai_author_forum.journals.publishing import (
    save_import_package_for_background,
    start_import_publish_process,
)
from ai_author_forum.journals.services import import_package
from ai_author_forum.journals.viewsets import JournalsViewSet
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event
from ai_author_forum.utils.admin_ui import (
    is_english_admin,
    translate_form_to_english,
)


def _configure_english_journal_import_forms(upload_form, confirm_form):
    package = upload_form.fields["package"]
    package.label = "Import package"
    package.help_text = (
        "Upload a ZIP containing journals/articles XLSX or CSV files and an "
        "optional media/ directory. The system validates every row before "
        "writing business data."
    )
    csv_encoding = upload_form.fields["csv_encoding"]
    csv_encoding.label = "CSV encoding strategy"
    csv_encoding.help_text = (
        "Applies to CSV files only. Automatic mode accepts UTF-8 and "
        "UTF-8-SIG and does not guess GBK."
    )
    csv_encoding.choices = (
        ("auto", "Automatic: UTF-8 / UTF-8-SIG"),
        ("gb18030", "Explicitly use GB18030"),
    )

    suspicious = confirm_form.fields["override_suspicious_text"]
    suspicious.label = "Import suspicious text unchanged"
    suspicious.help_text = (
        "Superusers only. The system will not automatically convert or guess "
        "a recovery for suspicious text."
    )
    confirm_form.fields["override_reason"].label = "Override reason"
    publish = confirm_form.fields["publish_static_site"]
    publish.label = "Generate the static site for existing approved content"
    publish.help_text = (
        "Newly imported articles remain drafts. Static generation includes "
        "only approved, placed, and currently effective articles."
    )


class JournalImportMenuItem(MenuItem):
    def is_shown(self, request):
        return request.user.is_superuser or request.user.has_perm(
            "site_settings.import_journals"
        )


class JournalCategoryMenuItem(MenuItem):
    def is_shown(self, request):
        return request.user.is_superuser or request.user.has_perm(
            "journals.view_journalcategory"
        )


class JournalCategoryAuditMenuItem(MenuItem):
    def is_shown(self, request):
        return request.user.is_superuser or (
            request.user.has_perm("site_settings.access_audit_log")
            and request.user.has_perm("journals.view_journalcategory")
        )


class PublicationIssueMenuItem(MenuItem):
    def is_shown(self, request):
        return request.user.is_superuser or request.user.has_perm(
            "journals.view_publicationissue"
        )


def _get_job(model, job_id):
    if not job_id:
        return None
    try:
        return model.objects.prefetch_related("rows").get(pk=job_id)
    except model.DoesNotExist as exc:
        raise Http404("Import preview not found") from exc


def _complete_preview_pair(journal_job, article_job):
    source_job = journal_job or article_job
    if source_job is None:
        return journal_job, article_job
    token = (source_job.summary or {}).get("preview_token")
    if not token:
        return journal_job, article_job
    if journal_job is None:
        journal_job = (
            JournalImportJob.objects.prefetch_related("rows")
            .filter(summary__preview_token=token)
            .order_by("-created_at", "-pk")
            .first()
        )
    if article_job is None:
        article_job = (
            ArticleImportJob.objects.prefetch_related("rows")
            .filter(summary__preview_token=token)
            .order_by("-created_at", "-pk")
            .first()
        )
    return journal_job, article_job


def _job_payload(job, scope):
    if job is None:
        return None
    summary = dict(job.summary or {})
    publish_job = summary.get("static_publish_job") or {}
    return {
        "scope": scope,
        "id": job.pk,
        "package_name": job.package_name,
        "status": job.status,
        "status_label": {
            "pending": "等待处理",
            "validating": "校验中",
            "ready": "待确认",
            "importing": "导入中",
            "completed": "已完成",
            "failed": "失败",
        }.get(job.status, job.get_status_display()),
        "total_rows": job.total_rows,
        "created": summary.get("created", 0),
        "updated": summary.get("updated", 0),
        "skipped": summary.get("skipped", 0),
        "failed": summary.get("failed", job.failed_rows),
        "suspicious_text_count": summary.get("suspicious_text_count", 0),
        "suspicious_rows": summary.get("suspicious_rows", []),
        "process_error": summary.get("process_error", ""),
        "confirmed_import_job": summary.get("confirmed_import_job"),
        "static_publish_job": publish_job or None,
        "static_publish_url": (
            reverse("static_publish:job_detail", kwargs={"job_id": publish_job["id"]})
            if publish_job.get("id")
            else ""
        ),
        "error_report_url": (
            reverse(
                "journals_import_error_report",
                kwargs={"scope": scope, "job_id": job.pk},
            )
            if job.error_report
            else ""
        ),
        "rows": list(
            job.rows.order_by("row_no", "pk").values(
                "row_no", "status", "action", "error_message", "raw_data"
            )
        ),
    }


def _preview_context(journal_job=None, article_job=None):
    jobs = [
        payload
        for payload in (
            _job_payload(journal_job, "journals"),
            _job_payload(article_job, "articles"),
        )
        if payload is not None
    ]
    return {
        "preview_jobs": jobs,
        "preview_ready": bool(jobs)
        and all(job["status"] == ImportJobStatus.READY for job in jobs),
        "has_blocking_suspicious_text": any(
            job.get("suspicious_text_count", 0) for job in jobs
        ),
        "preview_in_progress": any(
            job["status"]
            in {
                ImportJobStatus.PENDING,
                ImportJobStatus.IMPORTING,
            }
            for job in jobs
        ),
        "journal_job": journal_job,
        "article_job": article_job,
        "status_url": (
            reverse("journals_import_status")
            + f"?journal_job={journal_job.pk if journal_job else ''}"
            + f"&article_job={article_job.pk if article_job else ''}"
            if jobs
            else ""
        ),
    }


def _stamp_preview(result):
    preview_token = uuid.uuid4().hex
    for job in (result.journal_job, result.article_job):
        if job is None:
            continue
        summary = dict(job.summary or {})
        summary["preview_token"] = preview_token
        job.summary = summary
        job.save(update_fields=("summary",))


def _validate_preview_pair(journal_job, article_job, *, allow_suspicious_text=False):
    jobs = [job for job in (journal_job, article_job) if job is not None]
    if not jobs:
        raise ValidationError("A validated import preview is required.")
    tokens = {job.summary.get("preview_token") for job in jobs}
    if None in tokens or len(tokens) != 1:
        raise ValidationError("The selected preview jobs do not belong to one package.")
    if any(not job.summary.get("dry_run") for job in jobs):
        raise ValidationError("Only validated preview jobs can be confirmed.")
    if any(job.status != ImportJobStatus.READY for job in jobs):
        raise ValidationError("只有校验通过并处于待确认状态的预览任务可以导入。")
    if not allow_suspicious_text and any(
        (job.summary or {}).get("suspicious_text_count", 0) for job in jobs
    ):
        raise ValidationError(
            "预览中存在可疑文本，必须由超级管理员确认按原文导入并填写理由。"
        )
    return jobs


@permission_required("site_settings.import_journals", raise_exception=True)
def import_dashboard(request):
    journal_job = _get_job(JournalImportJob, request.GET.get("journal_job"))
    article_job = _get_job(ArticleImportJob, request.GET.get("article_job"))
    journal_job, article_job = _complete_preview_pair(journal_job, article_job)
    upload_form = ImportPackageForm()

    if request.method == "POST":
        upload_form = ImportPackageForm(request.POST, request.FILES)
        if upload_form.is_valid():
            try:
                result = import_package(
                    upload_form.cleaned_data["package"],
                    operator=request.user,
                    dry_run=True,
                    csv_encoding=upload_form.cleaned_data.get("csv_encoding") or "auto",
                )
            except Exception as exc:
                upload_form.add_error("package", str(exc))
            else:
                _stamp_preview(result)
                return redirect(
                    reverse("journals_import_dashboard")
                    + f"?journal_job={result.journal_job.pk if result.journal_job else ''}"
                    + f"&article_job={result.article_job.pk if result.article_job else ''}"
                )

    confirm_form = ConfirmImportForm(
        initial={
            "journal_job_id": journal_job.pk if journal_job else None,
            "article_job_id": article_job.pk if article_job else None,
            "publish_static_site": request.user.has_perm(
                "static_publish.publish_static_site"
            ),
            "csv_encoding": (
                ((journal_job or article_job).summary or {}).get("csv_encoding", "auto")
                if (journal_job or article_job)
                else "auto"
            ),
        }
    )
    if is_english_admin():
        translate_form_to_english(upload_form)
        translate_form_to_english(confirm_form)
        _configure_english_journal_import_forms(upload_form, confirm_form)

    context = {
        "form": upload_form,
        "confirm_form": confirm_form,
        "can_publish": request.user.has_perm("static_publish.publish_static_site"),
        "recent_jobs": JournalImportJob.objects.order_by("-created_at")[:10],
    }
    context.update(_preview_context(journal_job, article_job))
    template_name = (
        "journals/admin/import_dashboard.en.html"
        if is_english_admin()
        else "journals/admin/import_dashboard.html"
    )
    return render(request, template_name, context)


@permission_required("site_settings.import_journals", raise_exception=True)
def confirm_import(request):
    if request.method != "POST":
        raise Http404
    form = ConfirmImportForm(request.POST)
    if not form.is_valid():
        messages.error(request, "确认信息无效，请检查必填项和覆盖理由。")
        return redirect("journals_import_dashboard")

    journal_job = _get_job(JournalImportJob, form.cleaned_data.get("journal_job_id"))
    article_job = _get_job(ArticleImportJob, form.cleaned_data.get("article_job_id"))
    journal_job, article_job = _complete_preview_pair(journal_job, article_job)
    allow_suspicious_text = form.cleaned_data["override_suspicious_text"]
    override_reason = form.cleaned_data["override_reason"].strip()
    if allow_suspicious_text and not request.user.is_superuser:
        raise PermissionDenied
    try:
        jobs = _validate_preview_pair(
            journal_job, article_job, allow_suspicious_text=allow_suspicious_text
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("journals_import_dashboard")

    if allow_suspicious_text:
        record_audit_event(
            request=request,
            actor=request.user,
            action=AuditAction.IMPORT,
            status=AuditStatus.SUCCESS,
            target=journal_job or article_job,
            target_type="ImportPackage",
            target_label=(journal_job or article_job).package_name,
            message="超级管理员确认按原文导入可疑文本",
            metadata={
                "operation": "import_override_suspicious_text",
                "reason": override_reason,
                "journal_job_id": getattr(journal_job, "pk", None),
                "article_job_id": getattr(article_job, "pk", None),
                "suspicious_text_count": sum(
                    (job.summary or {}).get("suspicious_text_count", 0) for job in jobs
                ),
            },
        )
    publish_static_site = form.cleaned_data["publish_static_site"]
    if publish_static_site and not request.user.has_perm(
        "static_publish.publish_static_site"
    ):
        messages.error(
            request, "You do not have permission to publish the static site."
        )
        return redirect(
            reverse("journals_import_dashboard")
            + f"?journal_job={journal_job.pk if journal_job else ''}"
            + f"&article_job={article_job.pk if article_job else ''}"
        )

    source_job = journal_job or article_job
    with source_job.source_file.open("rb") as source_file:
        package_path = save_import_package_for_background(source_file)

    for job in jobs:
        summary = dict(job.summary or {})
        summary["publish_static_site"] = publish_static_site
        job.summary = summary
        job.status = ImportJobStatus.PENDING
        job.finished_at = None
        job.save(update_fields=("summary", "status", "finished_at", "updated_at"))

    process = start_import_publish_process(
        package_path=package_path,
        dry_run=False,
        publish_static_site=publish_static_site,
        operator_id=request.user.pk,
        preview_journal_job_id=journal_job.pk if journal_job else None,
        preview_article_job_id=article_job.pk if article_job else None,
        allow_suspicious_text=allow_suspicious_text,
        override_reason=override_reason,
        csv_encoding=form.cleaned_data.get("csv_encoding") or "auto",
    )
    for job in jobs:
        job.refresh_from_db()
        summary = dict(job.summary or {})
        summary["background_pid"] = process.pid
        job.summary = summary
        job.save(update_fields=("summary", "updated_at"))

    messages.success(request, "导入任务已启动，可稍后刷新查看进度。")
    return redirect(
        reverse("journals_import_dashboard")
        + f"?journal_job={journal_job.pk if journal_job else ''}"
        + f"&article_job={article_job.pk if article_job else ''}"
    )


@permission_required("site_settings.import_journals", raise_exception=True)
def import_status(request):
    journal_job = _get_job(JournalImportJob, request.GET.get("journal_job"))
    article_job = _get_job(ArticleImportJob, request.GET.get("article_job"))
    journal_job, article_job = _complete_preview_pair(journal_job, article_job)
    payload = _preview_context(journal_job, article_job)
    jobs = payload["preview_jobs"]
    return JsonResponse(
        {
            "jobs": jobs,
            "terminal": bool(jobs)
            and all(
                job["status"] in {ImportJobStatus.COMPLETED, ImportJobStatus.FAILED}
                for job in jobs
            ),
        }
    )


@permission_required("site_settings.import_journals", raise_exception=True)
def download_import_template(request):
    response = HttpResponse(
        build_import_template_package(), content_type="application/zip"
    )
    response["Content-Disposition"] = (
        'attachment; filename="ai-author-forum-import-template.zip"'
    )
    return response


@permission_required("site_settings.import_journals", raise_exception=True)
def download_error_report(request, scope, job_id):
    model = {"journals": JournalImportJob, "articles": ArticleImportJob}.get(scope)
    if model is None:
        raise Http404
    job = _get_job(model, job_id)
    if not job.error_report:
        raise Http404("This import job has no error report.")
    filename = Path(job.error_report.name).name
    return FileResponse(
        job.error_report.open("rb"), as_attachment=True, filename=filename
    )


def publication_issue_admin(request):
    if not (
        request.user.is_superuser
        or request.user.has_perm("journals.view_publicationissue")
    ):
        raise PermissionDenied
    if request.method == "POST":
        try:
            issue = PublicationIssue.objects.get(pk=request.POST.get("issue_id"))
            action = request.POST.get("action")
            handlers = {
                "publish": publish_issue,
                "set_current": set_current_issue,
                "archive": archive_issue,
                "rollback": rollback_issue,
            }
            handler = handlers.get(action)
            if handler is None:
                raise ValidationError("Unsupported publication issue action.")
            handler(issue, actor=request.user)
        except (
            PermissionDenied,
            ValidationError,
            PublicationIssue.DoesNotExist,
        ) as exc:
            messages.error(request, f"Publication issue action failed: {exc}")
        else:
            messages.success(request, "Publication issue action completed.")
        return redirect("journals_publication_issue_admin")
    issues = PublicationIssue.objects.select_related("journal").all()
    return render(
        request,
        "wagtailadmin/journals/publication_issue_index.html",
        {
            "title": "Publication issues",
            "issues": issues,
            "can_publish": request.user.is_superuser
            or request.user.has_perm("journals.publish_publication_issue"),
            "can_set_current": request.user.is_superuser
            or request.user.has_perm("journals.set_current_publication_issue"),
            "can_rollback": request.user.is_superuser
            or request.user.has_perm("journals.rollback_publication_issue"),
            "add_url": reverse("wagtailsnippets_journals_publicationissue:add"),
            "edit_url_name": "wagtailsnippets_journals_publicationissue:edit",
            "issue_articles_url": reverse("wagtailsnippets_journals_issuearticle:list"),
            "add_issue_article_url": reverse(
                "wagtailsnippets_journals_issuearticle:add"
            ),
        },
    )


@hooks.register("register_admin_urls")
def register_admin_urls():
    return [
        path(
            "journals/issues/",
            publication_issue_admin,
            name="journals_publication_issue_admin",
        ),
        path("journals/categories/", category_admin, name="journals_category_admin"),
        path(
            "journals/categories/audit/",
            category_audit,
            name="journals_category_audit",
        ),
        path("journals/import/", import_dashboard, name="journals_import_dashboard"),
        path(
            "journals/import/confirm/",
            confirm_import,
            name="journals_import_confirm",
        ),
        path("journals/import/status/", import_status, name="journals_import_status"),
        path(
            "journals/import/template/",
            download_import_template,
            name="journals_import_template",
        ),
        path(
            "journals/import/errors/<str:scope>/<int:job_id>/",
            download_error_report,
            name="journals_import_error_report",
        ),
    ]


@hooks.register("register_admin_menu_item")
def register_publication_issue_menu_item():
    return PublicationIssueMenuItem(
        "Publication issues",
        reverse("journals_publication_issue_admin"),
        icon_name="date",
        order=210,
    )


@hooks.register("register_admin_menu_item")
def register_category_menu_item():
    return JournalCategoryMenuItem(
        "栏目管理",
        reverse("journals_category_admin"),
        icon_name="folder-open-inverse",
        order=211,
    )


@hooks.register("register_admin_menu_item")
def register_category_audit_menu_item():
    return JournalCategoryAuditMenuItem(
        "栏目变更记录",
        reverse("journals_category_audit"),
        icon_name="history",
        order=212,
    )


@hooks.register("register_admin_menu_item")
def register_import_menu_item():
    return JournalImportMenuItem(
        "批量导入",
        reverse("journals_import_dashboard"),
        icon_name="upload",
        order=213,
    )


@hooks.register("register_admin_viewset")
def register_journals_viewset():
    return JournalsViewSet()
