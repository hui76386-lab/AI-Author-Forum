from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ai_author_forum.site_settings.access_control import is_super_admin
from ai_author_forum.site_settings.models import (
    AuditAction,
    AuditStatus,
)
from ai_author_forum.site_settings.services import record_audit_event

from .forms import (
    PublishForm,
    PublishJobFilterForm,
    RollbackForm,
    RollbackSelectForm,
    TargetFilterForm,
)
from .health import get_health_report
from .models import StaticManifest, StaticPublishJob, StaticPublishTarget
from .services import (
    create_publish_job,
    create_retry_job,
    create_rollback_job,
    estimate_publish_targets,
    manifest_diff,
    mark_publish_job_queue_failure,
)
from .tasks import retry_static_publish, rollback_static_publish, run_static_publish


def _can_publish(user):
    return is_super_admin(user)


def _require_publish_access(user):
    if not _can_publish(user):
        raise PermissionDenied


def _filtered_jobs(form):
    queryset = (
        StaticPublishJob.objects.select_related("triggered_by")
        .annotate(
            target_count=Count("targets"),
            success_count=Count(
                "targets",
                filter=Q(targets__status=StaticPublishTarget.Status.SUCCEEDED),
            ),
            failed_count=Count(
                "targets",
                filter=Q(targets__status=StaticPublishTarget.Status.FAILED),
            ),
        )
        .order_by("-created_at", "-pk")
    )
    if not form.is_valid():
        return queryset
    data = form.cleaned_data
    if data.get("status"):
        queryset = queryset.filter(status=data["status"])
    if data.get("scope"):
        queryset = queryset.filter(scope=data["scope"])
    if data.get("target_status"):
        matching_targets = StaticPublishTarget.objects.filter(
            job_id=OuterRef("pk"),
            status=data["target_status"],
        )
        queryset = queryset.filter(Exists(matching_targets))
    if data.get("manifest_status") == "active":
        queryset = queryset.filter(
            version__in=StaticManifest.objects.filter(is_active=True).values("version")
        )
    elif data.get("manifest_status") == "rollback":
        queryset = queryset.filter(
            version__in=StaticManifest.objects.filter(is_active=False).values("version")
        )
    if data.get("triggered_by"):
        queryset = queryset.filter(
            triggered_by__username__icontains=data["triggered_by"]
        )
    if data.get("created_from"):
        queryset = queryset.filter(created_at__date__gte=data["created_from"])
    if data.get("created_to"):
        queryset = queryset.filter(created_at__date__lte=data["created_to"])
    return queryset


@permission_required("static_publish.view_staticpublishjob", raise_exception=True)
@require_http_methods(["GET", "POST"])
def publish_center(request):
    _require_publish_access(request.user)
    publish_form = PublishForm(request.POST or None, prefix="publish")
    filter_form = PublishJobFilterForm(request.GET or None)
    rollback_select_form = RollbackSelectForm(prefix="rollback")
    estimate = None
    if request.method == "POST":
        if not _can_publish(request.user):
            raise PermissionDenied
        if publish_form.is_valid():
            estimate = estimate_publish_targets(publish_form.cleaned_data["paths"])
            if request.POST.get("action") == "estimate":
                messages.info(
                    request,
                    f"预计生成 {estimate['total']} 个目标，请确认影响范围后发布。",
                )
            else:
                job = create_publish_job(
                    scope=publish_form.cleaned_data["scope"],
                    paths=publish_form.cleaned_data["paths"],
                    actor=request.user,
                )
                try:
                    task_result = run_static_publish.delay(job.pk)
                except Exception as exc:
                    mark_publish_job_queue_failure(job, exc)
                    messages.error(request, job.error)
                    return redirect("static_publish:job_detail", job_id=job.pk)
                messages.success(
                    request, f"发布任务 #{job.pk} 已进入队列（{task_result.id}）。"
                )
                return redirect("static_publish:job_detail", job_id=job.pk)

    jobs_page = Paginator(_filtered_jobs(filter_form), 25).get_page(
        request.GET.get("page")
    )
    jobs_query = request.GET.copy()
    jobs_query.pop("page", None)
    jobs_querystring = jobs_query.urlencode()
    active_manifest = StaticManifest.objects.filter(is_active=True).first()
    return render(
        request,
        "static_publish/center.html",
        {
            "publish_form": publish_form,
            "filter_form": filter_form,
            "rollback_select_form": rollback_select_form,
            "jobs_page": jobs_page,
            "jobs_querystring": jobs_querystring,
            "active_manifest": active_manifest,
            "active_version": active_manifest.version if active_manifest else "",
            "health": get_health_report(include_release=True, include_broker=True),
            "estimate": estimate,
            "can_publish": _can_publish(request.user),
        },
    )


@permission_required("static_publish.view_staticpublishjob", raise_exception=True)
def job_detail(request, job_id):
    _require_publish_access(request.user)
    job = get_object_or_404(
        StaticPublishJob.objects.select_related("triggered_by", "retry_of"), pk=job_id
    )
    target_filter = TargetFilterForm(request.GET or None)
    targets = job.targets.all()
    if target_filter.is_valid():
        data = target_filter.cleaned_data
        if data.get("status"):
            targets = targets.filter(status=data["status"])
        if data.get("path"):
            targets = targets.filter(path__icontains=data["path"])
        if data.get("target_type"):
            targets = targets.filter(target_type__icontains=data["target_type"])
        if data.get("error_category"):
            targets = targets.filter(error_category=data["error_category"])
    targets_page = Paginator(targets, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "static_publish/job_detail.html",
        {
            "job": job,
            "target_filter": target_filter,
            "targets_page": targets_page,
            "logs": job.logs.order_by("-created_at")[:100],
            "requires_publisher_approval": bool(
                (job.summary or {}).get("requires_publisher_approval")
            ),
            "approval_queued_at": (job.summary or {}).get("approval_queued_at"),
            "can_approve": (
                _can_publish(request.user)
                and job.status == StaticPublishJob.Status.PENDING
                and bool((job.summary or {}).get("requires_publisher_approval"))
                and not (job.summary or {}).get("approval_queued_at")
            ),
        },
    )


@permission_required("static_publish.view_staticpublishjob", raise_exception=True)
@require_POST
def approve_pending_job(request, job_id):
    _require_publish_access(request.user)

    with transaction.atomic():
        job = get_object_or_404(StaticPublishJob.objects.select_for_update(), pk=job_id)
        summary = dict(job.summary or {})
        if job.status != StaticPublishJob.Status.PENDING or not summary.get(
            "requires_publisher_approval"
        ):
            messages.error(request, "该任务不是可审批的首页投放发布待办。")
            return redirect("static_publish:job_detail", job_id=job.pk)
        if summary.get("approval_queued_at"):
            messages.info(request, "该任务已由发布管理员批准并进入执行队列。")
            return redirect("static_publish:job_detail", job_id=job.pk)

        approved_at = timezone.now()
        summary.update(
            {
                "requested_by_id": job.triggered_by_id,
                "publisher_approved_by_id": request.user.pk,
                "publisher_approved_at": approved_at.isoformat(),
                "approval_queued_at": approved_at.isoformat(),
            }
        )
        job.summary = summary
        job.triggered_by = request.user
        job.save(update_fields=("summary", "triggered_by"))
        record_audit_event(
            action=AuditAction.PUBLISH,
            status=AuditStatus.STARTED,
            actor=request.user,
            target=job,
            message="Publisher approved pending placement publish",
            metadata={
                "stage": "publisher_approval",
                "requested_by_id": summary.get("requested_by_id"),
                "paths": list(job.requested_paths or []),
                "placement_ids": list(summary.get("placement_ids") or []),
            },
        )

    try:
        task_result = run_static_publish.delay(job.pk)
    except Exception as exc:
        mark_publish_job_queue_failure(job, exc)
        messages.error(request, job.error)
        return redirect("static_publish:job_detail", job_id=job.pk)

    messages.success(
        request, f"发布任务 #{job.pk} 已批准并进入执行队列（{task_result.id}）。"
    )
    return redirect("static_publish:job_detail", job_id=job.pk)


@permission_required("static_publish.view_staticpublishjob", raise_exception=True)
@require_GET
def job_status(request, job_id):
    _require_publish_access(request.user)
    job = get_object_or_404(StaticPublishJob, pk=job_id)
    counts = {choice: 0 for choice, _label in StaticPublishTarget.Status.choices}
    for row in job.targets.values("status").annotate(total=Count("pk")):
        counts[row["status"]] = row["total"]
    total = sum(counts.values())
    completed = (
        counts[StaticPublishTarget.Status.SUCCEEDED]
        + counts[StaticPublishTarget.Status.FAILED]
        + counts[StaticPublishTarget.Status.SKIPPED]
    )
    terminal = job.status in {
        StaticPublishJob.Status.SUCCEEDED,
        StaticPublishJob.Status.PARTIAL,
        StaticPublishJob.Status.FAILED,
        StaticPublishJob.Status.ROLLED_BACK,
    }
    percent = round((completed / total) * 100) if total else (100 if terminal else 0)
    end = job.finished_at or timezone.now()
    elapsed = (
        max(0, int((end - job.started_at).total_seconds())) if job.started_at else 0
    )
    return JsonResponse(
        {
            "job_id": job.pk,
            "status": job.status,
            "status_label": job.get_status_display(),
            "total": total,
            "pending": counts[StaticPublishTarget.Status.PENDING],
            "running": counts[StaticPublishTarget.Status.RUNNING],
            "succeeded": counts[StaticPublishTarget.Status.SUCCEEDED],
            "failed": counts[StaticPublishTarget.Status.FAILED],
            "skipped": counts[StaticPublishTarget.Status.SKIPPED],
            "percent": percent,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "elapsed": elapsed,
            "terminal": terminal,
        }
    )


@permission_required("static_publish.retry_category_publish", raise_exception=True)
@require_POST
def retry_job(request, job_id):
    _require_publish_access(request.user)
    failed_job = get_object_or_404(StaticPublishJob, pk=job_id)
    raw_selected_ids = [value for value in request.POST.getlist("target_ids") if value]
    try:
        selected_ids = [int(value) for value in raw_selected_ids]
        target_pk_field = StaticPublishTarget._meta.pk
        _, max_target_id = connection.ops.integer_field_range(
            target_pk_field.get_internal_type()
        )
        if any(
            value <= 0 or (max_target_id is not None and value > max_target_id)
            for value in selected_ids
        ):
            raise ValueError("target id is outside the database primary-key range")
    except (TypeError, ValueError):
        messages.error(request, "选择的重试目标无效，请重新选择。")
        return redirect("static_publish:job_detail", job_id=failed_job.pk)
    failed_targets = failed_job.targets.filter(status=StaticPublishTarget.Status.FAILED)
    if selected_ids:
        failed_targets = failed_targets.filter(pk__in=selected_ids)
        if failed_targets.count() != len(set(selected_ids)):
            messages.error(request, "只能重试属于当前任务的失败目标。")
            return redirect("static_publish:job_detail", job_id=failed_job.pk)
    target_ids = list(failed_targets.values_list("pk", flat=True))
    paths = list(failed_targets.values_list("path", flat=True))
    if not paths:
        paths = list(failed_job.requested_paths or [])
        target_ids = []
    if not paths and failed_job.scope != StaticPublishJob.Scope.FULL:
        messages.error(request, "该任务在生成页面前失败，且没有可恢复的发布范围。")
        return redirect("static_publish:job_detail", job_id=failed_job.pk)
    retry_job_record = create_retry_job(
        failed_job=failed_job,
        actor=request.user,
        paths=paths,
        target_ids=target_ids,
        scope=(StaticPublishJob.Scope.RETRY if paths else StaticPublishJob.Scope.FULL),
    )
    try:
        task_result = retry_static_publish.delay(retry_job_record.pk, request.user.pk)
    except Exception as exc:
        mark_publish_job_queue_failure(retry_job_record, exc)
        messages.error(request, retry_job_record.error)
        return redirect("static_publish:job_detail", job_id=retry_job_record.pk)
    messages.success(
        request,
        f"已创建 {len(paths) if paths else '全部'} 个目标的重试任务（{task_result.id}）。",
    )
    return redirect("static_publish:job_detail", job_id=retry_job_record.pk)


@permission_required("static_publish.rollback_category_publish", raise_exception=True)
@require_GET
def rollback_preview(request):
    _require_publish_access(request.user)
    select_form = RollbackSelectForm(request.GET, prefix="rollback")
    if not select_form.is_valid():
        messages.error(request, "请选择有效的历史版本。")
        return redirect("static_publish:center")
    target_manifest = select_form.cleaned_data["version"]
    active_manifest = StaticManifest.objects.filter(is_active=True).first()
    diff = manifest_diff(active_manifest, target_manifest)
    form = RollbackForm(prefix="rollback", initial={"version": target_manifest.pk})
    return render(
        request,
        "static_publish/rollback_confirm.html",
        {
            "form": form,
            "target_manifest": target_manifest,
            "active_manifest": active_manifest,
            "diff": diff,
        },
    )


@permission_required("static_publish.rollback_category_publish", raise_exception=True)
@require_POST
def rollback_release(request):
    _require_publish_access(request.user)
    form = RollbackForm(request.POST, prefix="rollback")
    if not form.is_valid():
        messages.error(request, "请选择有效版本并填写至少 5 个字符的回滚原因。")
        return redirect("static_publish:center")
    version = form.cleaned_data["version"]
    reason = form.cleaned_data["reason"]
    rollback_job = create_rollback_job(
        version=version,
        actor=request.user,
        reason=reason,
    )
    try:
        task_result = rollback_static_publish.delay(rollback_job.pk, request.user.pk)
    except Exception as exc:
        mark_publish_job_queue_failure(rollback_job, exc)
        messages.error(request, rollback_job.error)
        return redirect("static_publish:job_detail", job_id=rollback_job.pk)
    messages.success(
        request, f"回滚到 {version} 的任务已进入队列（{task_result.id}）。"
    )
    return redirect("static_publish:job_detail", job_id=rollback_job.pk)
