from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from PIL import Image as PillowImage, UnidentifiedImageError

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.images.models import CustomImage
from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalCategoryStatus,
    JournalStatus,
)
from ai_author_forum.site_settings.access_control import (
    filter_accessible_articles,
    filter_accessible_journals,
    filter_accessible_placements,
    is_super_admin,
)
from ai_author_forum.site_settings.admin_views import PermissionedModuleViewSet
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.utils.admin_i18n import admin_text
from ai_author_forum.utils.admin_ui import english_admin_text

from .batch_operations import (
    create_maintenance_batch,
    execute_maintenance_batch,
    export_placements_csv,
    precheck_maintenance_batch,
)
from .batch_services import (
    BatchValidationError,
    create_draft,
    execute_create_batch,
    precheck_batch,
    require_batch_scope,
    update_draft,
)
from .models import ArticlePlacement, LayoutSlot, PlacementBatch, PlacementBatchItem
from .publishing import sync_batch_publish_status
from .selectors import (
    article_payload,
    journal_payload,
    mark_journal_used,
    select_articles,
    select_journals,
)
from .services import (
    PLACEABLE_REVIEW_STATUSES,
    has_placement_permission,
    placement_capacity,
    require_placement_scope,
)

PLACEMENT_IMAGE_UPLOAD_MAX_BYTES = 10 * 1024 * 1024


class PlacementsWorkflowV2ViewSet(PermissionedModuleViewSet):
    """Task-oriented replacement for the legacy placement workbench."""

    name = "placements"
    url_prefix = "placements"
    menu_label = admin_text("placements.manage")
    menu_name = "placements"
    menu_icon = "pick"
    menu_order = 230
    permission = "site_settings.access_placements"
    title = admin_text("placements.manage")
    description = "Task-oriented placement workflows with strict validation and static publishing."
    owner = (
        "Placements domain (D); architecture, permissions, and publishing closure (A)."
    )
    integration_points = (
        "ArticlePlacement",
        "PlacementBatch",
        "get_slot_items(slot_code, journal=None)",
    )

    def _guard(self, request):
        if not self.has_access(request) or not has_placement_permission(
            request.user, "view"
        ):
            raise PermissionDenied
        if not getattr(settings, "PLACEMENTS_V2_ENABLED", False):
            if is_super_admin(request.user):
                return redirect("placements_legacy:index")
            raise PermissionDenied
        return None

    def _batch(self, request, batch_id, *, modes=None):
        batch = get_object_or_404(
            PlacementBatch.objects.select_related(
                "slot", "created_by", "publish_job", "target_category"
            ).prefetch_related("items__article__primary_journal", "items__placement"),
            pk=batch_id,
        )
        if modes and batch.mode not in modes:
            raise PermissionDenied
        if (
            batch.created_by_id
            and batch.created_by_id != request.user.pk
            and not is_super_admin(request.user)
        ):
            raise PermissionDenied
        if not is_super_admin(request.user):
            if batch.mode == PlacementBatch.Mode.BULK_MAINTENANCE:
                for item in batch.items.select_related(
                    "placement__article__primary_journal",
                    "placement__slot",
                ):
                    if item.placement_id is None:
                        raise PermissionDenied
                    require_placement_scope(request.user, item.placement)
            else:
                require_batch_scope(batch, request.user)
        sync_batch_publish_status(batch)
        return batch

    @staticmethod
    def _journal_queryset(user):
        return filter_accessible_journals(
            user, Journal.objects.filter(status=JournalStatus.ACTIVE)
        )

    @staticmethod
    def _placement_queryset(user):
        queryset = ArticlePlacement.objects.filter(
            source=ArticlePlacement.Source.MANUAL
        )
        return filter_accessible_placements(user, queryset)

    def _require_target_access(self, user, target_type, target_slug):
        if is_super_admin(user):
            return None
        accessible_journals = self._journal_queryset(user)
        if target_type == ArticlePlacement.TargetType.JOURNAL:
            target = accessible_journals.filter(slug=target_slug).first()
        elif target_type == ArticlePlacement.TargetType.CATEGORY:
            target = JournalCategory.objects.filter(
                Q(slug=target_slug) | Q(path_cache=target_slug),
                journal__in=accessible_journals,
                status__in=(
                    JournalCategoryStatus.ACTIVE,
                    JournalCategoryStatus.HIDDEN,
                ),
            ).first()
        elif target_type == ArticlePlacement.TargetType.ARTICLE:
            target = filter_accessible_articles(
                user,
                ArticlePage.objects.filter(static_slug=target_slug),
            ).first()
        else:
            target = None
        if target is None:
            raise PermissionDenied
        return target

    def _render(self, request, template, **context):
        params = request.GET.copy()
        params.pop("page", None)
        return render(
            request,
            template,
            {
                "title": context.pop("title", self.title),
                "viewset": self,
                "pagination_query": urlencode(params, doseq=True),
                "can_add_placements": has_placement_permission(request.user, "add"),
                **context,
            },
        )

    @staticmethod
    def _article_selector_journal(request, batch):
        if "journal" in request.GET:
            return request.GET.get("journal", "")
        return batch.target_slug

    @staticmethod
    def _image_payload(image):
        """Return only presentation-safe image information for the rules picker."""
        image_file = image.file
        is_available = bool(image_file and image_file.name)
        if is_available:
            try:
                is_available = image_file.storage.exists(image_file.name)
            except (OSError, ValueError):
                is_available = False

        url = ""
        if is_available:
            try:
                url = image_file.url
            except (OSError, ValueError):
                is_available = False

        return {
            "id": image.pk,
            "title": image.title or "未命名图片",
            "description": image.description or "",
            "url": url,
            "is_available": is_available,
        }

    def _override_image_context(self, batch):
        raw_image_id = (batch.options or {}).get("override_image_id")
        if raw_image_id in (None, ""):
            return None
        try:
            image_id = int(raw_image_id)
        except (TypeError, ValueError):
            return {"id": raw_image_id, "is_available": False, "is_invalid": True}

        image = CustomImage.objects.filter(pk=image_id).first()
        if image is None:
            return {"id": image_id, "is_available": False, "is_missing": True}
        return self._image_payload(image)

    def _article_cover_context(self, batch):
        item = (
            batch.items.select_related("article__featured_image")
            .order_by("sort_order", "pk")
            .first()
        )
        if item is None or not item.article.featured_image_id:
            return None
        payload = self._image_payload(item.article.featured_image)
        payload["has_alt"] = bool((item.article.featured_image_alt or "").strip())
        return payload

    def _slots_for_target(self, target_type):
        scope = {
            ArticlePlacement.TargetType.MAIN_SITE: LayoutSlot.Scope.HOME,
            ArticlePlacement.TargetType.SECTION: LayoutSlot.Scope.SECTION,
            ArticlePlacement.TargetType.JOURNAL: LayoutSlot.Scope.JOURNAL,
            ArticlePlacement.TargetType.ARTICLE: LayoutSlot.Scope.ARTICLE,
            ArticlePlacement.TargetType.SEARCH: LayoutSlot.Scope.SEARCH,
            ArticlePlacement.TargetType.CATEGORY: LayoutSlot.Scope.CATEGORY,
        }.get(target_type)
        return LayoutSlot.objects.filter(scope=scope, is_active=True).order_by(
            "sort_order", "code"
        )

    @staticmethod
    def _slot_payload(slot, *, target_type, target_slug=""):
        candidate = ArticlePlacement(
            slot=slot,
            target_type=target_type,
            target_slug=target_slug,
            starts_at=None,
            ends_at=None,
            is_active=True,
        )
        return {
            "id": slot.pk,
            "code": slot.code,
            "title": slot.title,
            **placement_capacity(candidate),
        }

    def index_view(self, request):
        redirect_response = self._guard(request)
        if redirect_response:
            return redirect_response
        now = timezone.now()
        placements = self._placement_queryset(request.user)
        active = (
            placements.filter(is_active=True, starts_at__lte=now)
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
            .count()
        )
        future = placements.filter(is_active=True, starts_at__gt=now).count()
        expiring = placements.filter(
            is_active=True, ends_at__gt=now, ends_at__lte=now + timedelta(days=14)
        ).count()
        abnormal_groups = (
            placements.filter(is_active=True, slot__is_active=True)
            .values("slot_id", "target_type", "target_slug", "target_category_id")
            .annotate(count=Count("pk"))
            .filter(slot__max_items__lt=F("count"))
        )
        capacity_exceptions = abnormal_groups.count()
        drafts = PlacementBatch.objects.filter(
            status=PlacementBatch.Status.DRAFT
        ).count()
        failed_publishes = PlacementBatch.objects.filter(
            publish_status=PlacementBatch.PublishStatus.FAILED
        ).count()
        inactive_article_references = placements.filter(
            is_active=True, article__live=False
        ).count()
        inactive_journal_references = (
            placements.filter(is_active=True)
            .filter(
                Q(article__primary_journal__isnull=True)
                | ~Q(article__primary_journal__status=JournalStatus.ACTIVE)
            )
            .count()
        )
        missing_image_alt = (
            placements.filter(is_active=True, override_image__isnull=False)
            .filter(Q(override_image_alt__isnull=True) | Q(override_image_alt=""))
            .count()
        )
        recent_batches = PlacementBatch.objects.select_related("created_by", "slot")
        if not is_super_admin(request.user):
            recent_batches = recent_batches.filter(created_by=request.user)
        recent_batches = recent_batches.order_by("-updated_at")[:10]
        issues = []
        if capacity_exceptions:
            issues.append(
                (
                    "容量异常 / Capacity issues",
                    capacity_exceptions,
                    reverse("placements:list") + "?capacity=over",
                )
            )
        if failed_publishes:
            issues.append(
                (
                    "静态发布失败 / Static publish failures",
                    failed_publishes,
                    reverse("placements:batches"),
                )
            )
        if inactive_article_references:
            issues.append(
                (
                    "已引用文章已停用 / Referenced articles are not live",
                    inactive_article_references,
                    reverse("placements:list") + "?issue=article_inactive",
                )
            )
        if inactive_journal_references:
            issues.append(
                (
                    "所属子期刊已停用 / Source journals are inactive",
                    inactive_journal_references,
                    reverse("placements:list") + "?issue=journal_inactive",
                )
            )
        if missing_image_alt:
            issues.append(
                (
                    "图片 Alt 缺失 / Missing image Alt",
                    missing_image_alt,
                    reverse("placements:list") + "?issue=missing_alt",
                )
            )
        if expiring:
            issues.append(
                (
                    "即将失效 / Expiring soon",
                    expiring,
                    reverse("placements:list") + "?expires_within=14",
                )
            )
        old_drafts = PlacementBatch.objects.filter(
            status=PlacementBatch.Status.DRAFT, updated_at__lt=now - timedelta(days=14)
        ).count()
        if old_drafts:
            issues.append(
                (
                    "长期未处理草稿 / Stale drafts",
                    old_drafts,
                    reverse("placements:batches") + "?status=draft",
                )
            )
        return self._render(
            request,
            "placements/admin/v2/overview.html",
            title="投放总览 / Placement overview",
            stats={
                "active": active,
                "future": future,
                "expiring": expiring,
                "capacity": capacity_exceptions,
                "drafts": drafts,
                "failed_publishes": failed_publishes,
            },
            issues=issues,
            recent_batches=recent_batches,
        )

    # --- common, server-side selector APIs ---
    def journals_api(self, request):
        if self._guard(request):
            raise PermissionDenied
        page_obj, paginator = select_journals(
            user=request.user,
            query=request.GET.get("q", ""),
            scope=request.GET.get("scope", "all"),
            page=request.GET.get("page", 1),
            page_size=request.GET.get("page_size", 20),
        )
        return JsonResponse(
            {
                "results": [
                    journal_payload(journal, user=request.user)
                    for journal in page_obj.object_list
                ],
                "page": page_obj.number,
                "page_size": paginator.per_page,
                "pages": paginator.num_pages,
                "total": paginator.count,
            }
        )

    def journal_favorite_api(self, request, journal_id):
        if request.method != "POST":
            return JsonResponse({"error": "POST required"}, status=405)
        if self._guard(request):
            raise PermissionDenied
        journal = get_object_or_404(self._journal_queryset(request.user), pk=journal_id)
        from .models import JournalUserPreference

        preference, _ = JournalUserPreference.objects.get_or_create(
            user=request.user, journal=journal
        )
        preference.is_favorite = (
            not preference.is_favorite
            if "is_favorite" not in request.POST
            else request.POST.get("is_favorite") in {"1", "true", "True"}
        )
        preference.save(update_fields=("is_favorite",))
        return JsonResponse({"id": journal.pk, "is_favorite": preference.is_favorite})

    def articles_api(self, request):
        if self._guard(request):
            raise PermissionDenied
        page_obj, paginator = select_articles(
            user=request.user,
            query=request.GET.get("q", ""),
            journal_slug=request.GET.get("journal", ""),
            page=request.GET.get("page", 1),
            page_size=request.GET.get("page_size", 20),
        )
        return JsonResponse(
            {
                "results": [
                    article_payload(article) for article in page_obj.object_list
                ],
                "page": page_obj.number,
                "page_size": paginator.per_page,
                "pages": paginator.num_pages,
                "total": paginator.count,
            }
        )

    def images_api(self, request):
        """Search usable Wagtail images without exposing file-system identifiers."""
        if self._guard(request):
            raise PermissionDenied
        query = (request.GET.get("q") or "").strip()
        try:
            page = max(1, int(request.GET.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        page_size = 24
        images = CustomImage.objects.all()
        if query:
            images = images.filter(
                Q(title__icontains=query) | Q(description__icontains=query)
            )
        total = images.count()
        start = (page - 1) * page_size
        # Older database rows can point to deleted files, so omit them from the
        # picker even though they still exist as image records.
        results = []
        for image in images.order_by("-created_at", "-pk")[start : start + page_size]:
            payload = self._image_payload(image)
            if payload["is_available"]:
                results.append(payload)
        return JsonResponse(
            {
                "results": results,
                "page": page,
                "page_size": page_size,
                "has_more": start + page_size < total,
            }
        )

    def image_upload_api(self, request):
        """Add one validated image to the shared Wagtail library for this placement."""
        if self._guard(request):
            raise PermissionDenied
        if request.method != "POST":
            return JsonResponse(
                {"message": english_admin_text("请使用上传操作提交图片。")},
                status=405,
            )
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            return JsonResponse(
                {"message": english_admin_text("请选择一张本地图片。")},
                status=400,
            )
        if uploaded_file.size > PLACEMENT_IMAGE_UPLOAD_MAX_BYTES:
            return JsonResponse(
                {"message": english_admin_text("图片文件不能超过 10 MB。")},
                status=400,
            )
        try:
            uploaded_file.seek(0)
            with PillowImage.open(uploaded_file) as probe:
                probe.verify()
            uploaded_file.seek(0)
        except (OSError, UnidentifiedImageError, ValueError):
            return JsonResponse(
                {
                    "message": english_admin_text(
                        "无法识别该图片。请上传 JPG、PNG、WebP 或 GIF 文件。"
                    )
                },
                status=400,
            )

        title = (request.POST.get("title") or "").strip()[:255]
        if not title:
            title = Path(uploaded_file.name).name[:255] or "未命名图片"
        try:
            image = CustomImage(title=title, file=uploaded_file)
            image.full_clean()
            image.save()
        except (OSError, ValidationError, ValueError):
            return JsonResponse(
                {
                    "message": english_admin_text(
                        "图片无法保存。请更换为有效的常见图片格式后重试。"
                    )
                },
                status=400,
            )

        return JsonResponse({"image": self._image_payload(image)}, status=201)

    def capacity_api(self, request):
        if self._guard(request):
            raise PermissionDenied
        target_type = request.GET.get(
            "target_type", ArticlePlacement.TargetType.JOURNAL
        )
        target_slug = request.GET.get("target_slug", "").strip().strip("/")
        self._require_target_access(request.user, target_type, target_slug)
        slots = LayoutSlot.objects.filter(is_active=True)
        if not is_super_admin(request.user):
            slots = slots.filter(scope=LayoutSlot.Scope.JOURNAL)
        slot = get_object_or_404(slots, pk=request.GET.get("slot"))
        candidate = ArticlePlacement(
            slot=slot,
            target_type=target_type,
            target_slug=target_slug,
            starts_at=None,
            ends_at=None,
            is_active=True,
        )
        return JsonResponse({"slot": slot.code, **placement_capacity(candidate)})

    def slots_api(self, request):
        if self._guard(request):
            raise PermissionDenied
        target_type = request.GET.get(
            "target_type", ArticlePlacement.TargetType.JOURNAL
        )
        target_slug = request.GET.get("target_slug", "").strip().strip("/")
        self._require_target_access(request.user, target_type, target_slug)
        return JsonResponse(
            {
                "results": [
                    self._slot_payload(
                        slot,
                        target_type=target_type,
                        target_slug=target_slug,
                    )
                    for slot in self._slots_for_target(target_type)
                ]
            }
        )

    # --- single placement wizard ---
    def new_single(self, request):
        if response := self._guard(request):
            return response
        if not has_placement_permission(request.user, "add"):
            raise PermissionDenied
        requested_journal = (request.GET.get("journal") or "").strip().strip("/")
        journal = (
            self._journal_queryset(request.user)
            .filter(Q(slug__iexact=requested_journal))
            .first()
            if requested_journal
            else None
        )
        article_id = request.GET.get("article")
        try:
            article_id = int(article_id) if article_id else None
        except (TypeError, ValueError):
            article_id = None
        article = None
        if article_id:
            article = (
                filter_accessible_articles(
                    request.user,
                    ArticlePage.objects.filter(
                        pk=article_id,
                        review_status__in=PLACEABLE_REVIEW_STATUSES,
                        primary_journal__status=JournalStatus.ACTIVE,
                    ),
                )
                .select_related("primary_journal")
                .first()
            )
            if article and journal and article.primary_journal_id != journal.pk:
                article = None
            if article and journal is None:
                journal = article.primary_journal

        batch = create_draft(
            actor=request.user,
            mode=PlacementBatch.Mode.SINGLE,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=journal.slug if journal else "",
            current_step="article",
        )
        if article and journal:
            update_draft(
                batch,
                actor=request.user,
                step="target",
                selected_article_ids=[article.pk],
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=journal.slug,
            )
            return redirect("placements:single_target", batch_id=batch.pk)

        query = {"journal": journal.slug} if journal else {}
        selector_url = reverse(
            "placements:single_article", kwargs={"batch_id": batch.pk}
        )
        if query:
            selector_url = f"{selector_url}?{urlencode(query)}"
        return redirect(selector_url)

    def single_article(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(request, batch_id, modes={PlacementBatch.Mode.SINGLE})
        if request.method == "POST":
            article_id = request.POST.get("article_id")
            if not article_id:
                messages.error(request, "请选择一篇文章后继续。")
            else:
                try:
                    update_draft(
                        batch,
                        actor=request.user,
                        step="target",
                        selected_article_ids=[article_id],
                    )
                except (TypeError, ValueError, ValidationError) as exc:
                    messages.error(
                        request, "; ".join(getattr(exc, "messages", [str(exc)]))
                    )
                else:
                    return redirect("placements:single_target", batch_id=batch.pk)
        page_obj, paginator = select_articles(
            user=request.user,
            query=request.GET.get("q", ""),
            journal_slug=self._article_selector_journal(request, batch),
            page=request.GET.get("page", 1),
            page_size=request.GET.get("page_size", 20),
        )
        return self._render(
            request,
            "placements/admin/v2/article_selector.html",
            title="选择文章 / Select article",
            batch=batch,
            page_obj=page_obj,
            paginator=paginator,
            selected_ids=set(batch.items.values_list("article_id", flat=True)),
            next_url=reverse(
                "placements:single_article", kwargs={"batch_id": batch.pk}
            ),
            journals=self._journal_queryset(request.user).order_by("name")[:0],
            selected_journal=self._journal_queryset(request.user)
            .filter(slug=batch.target_slug, status=JournalStatus.ACTIVE)
            .first(),
            single=True,
        )

    def single_target(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(request, batch_id, modes={PlacementBatch.Mode.SINGLE})
        if not batch.items.exists():
            return redirect("placements:single_article", batch_id=batch.pk)
        if request.method == "POST":
            target_type = request.POST.get("target_type")
            target_slug = request.POST.get("target_slug", "").strip().strip("/")
            if target_type not in dict(ArticlePlacement.TargetType.choices):
                messages.error(request, "Select a valid placement target type.")
            else:
                target = None
                try:
                    target = self._require_target_access(
                        request.user, target_type, target_slug
                    )
                except PermissionDenied:
                    messages.error(request, "Select a target in your journal scope.")
                slot = (
                    self._slots_for_target(target_type)
                    .filter(pk=request.POST.get("slot"))
                    .first()
                )
                if target is None and not is_super_admin(request.user):
                    pass
                elif not slot:
                    messages.error(
                        request,
                        "The selected slot does not match the target or is inactive.",
                    )
                else:
                    if target_type == ArticlePlacement.TargetType.MAIN_SITE:
                        target_slug = ""
                    target_category = None
                    if target_type == ArticlePlacement.TargetType.CATEGORY:
                        if is_super_admin(request.user):
                            target_category = JournalCategory.objects.filter(
                                Q(slug=target_slug) | Q(path_cache=target_slug)
                            ).first()
                            if target_category is None:
                                raise PermissionDenied
                        else:
                            target_category = target
                        target_slug = ""
                    try:
                        update_draft(
                            batch,
                            actor=request.user,
                            step="rules",
                            target_type=target_type,
                            target_slug=target_slug,
                            target_category=target_category,
                            slot=slot,
                        )
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                    else:
                        if target_type == ArticlePlacement.TargetType.JOURNAL:
                            mark_journal_used(
                                user=request.user,
                                journal=self._journal_queryset(request.user).get(
                                    slug=target_slug
                                ),
                            )
                        return redirect("placements:single_rules", batch_id=batch.pk)
        return self._render(
            request,
            "placements/admin/v2/target.html",
            title="选择投放目标 / Select placement target",
            batch=batch,
            target_types=(
                ArticlePlacement.TargetType.choices
                if is_super_admin(request.user)
                else [
                    (
                        ArticlePlacement.TargetType.JOURNAL,
                        ArticlePlacement.TargetType.JOURNAL.label,
                    ),
                    (
                        ArticlePlacement.TargetType.CATEGORY,
                        ArticlePlacement.TargetType.CATEGORY.label,
                    ),
                    (
                        ArticlePlacement.TargetType.ARTICLE,
                        ArticlePlacement.TargetType.ARTICLE.label,
                    ),
                ]
            ),
            slot_options=[
                self._slot_payload(
                    slot,
                    target_type=batch.target_type,
                    target_slug=batch.target_slug,
                )
                for slot in self._slots_for_target(batch.target_type)
            ],
            selected_journal=(
                self._journal_queryset(request.user)
                .filter(slug=batch.target_slug, status=JournalStatus.ACTIVE)
                .first()
                if batch.target_type == ArticlePlacement.TargetType.JOURNAL
                else None
            ),
            back_url=reverse(
                "placements:single_article", kwargs={"batch_id": batch.pk}
            ),
            post_url=reverse("placements:single_target", kwargs={"batch_id": batch.pk}),
        )

    def single_rules(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(request, batch_id, modes={PlacementBatch.Mode.SINGLE})
        if not batch.slot_id:
            return redirect("placements:single_target", batch_id=batch.pk)
        if request.method == "POST":
            options = dict(batch.options or {})
            for field in ("override_title", "override_summary", "override_image_id"):
                options[field] = request.POST.get(field, "")
            # Alt text belongs to a placement-specific override only. Clearing
            # that image must not retain an invisible, stale override alt value.
            options["override_image_alt"] = (
                request.POST.get("override_image_alt", "")
                if options["override_image_id"]
                else ""
            )
            try:
                starts_at = self._parse_datetime(request.POST.get("starts_at"))
                ends_at = self._parse_datetime(request.POST.get("ends_at"))
                update_draft(
                    batch,
                    actor=request.user,
                    step="review",
                    starts_at=starts_at,
                    ends_at=ends_at,
                    is_pinned=request.POST.get("is_pinned") == "on",
                    options=options,
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                return redirect("placements:single_review", batch_id=batch.pk)
        return self._render(
            request,
            "placements/admin/v2/rules.html",
            title="设置展示规则 / Set display rules",
            batch=batch,
            back_url=reverse("placements:single_target", kwargs={"batch_id": batch.pk}),
            post_url=reverse("placements:single_rules", kwargs={"batch_id": batch.pk}),
            image_search_url=reverse("placements:images_api"),
            image_upload_url=reverse("placements:image_upload_api"),
            selected_override_image=self._override_image_context(batch),
            article_cover_image=self._article_cover_context(batch),
            single=True,
        )

    def single_review(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(request, batch_id, modes={PlacementBatch.Mode.SINGLE})
        if batch.is_executed:
            return redirect("placements:batch_result", batch_id=batch.pk)
        result = precheck_batch(batch, actor=request.user)
        # precheck_batch persists fresh validation states. Reload the batch so the
        # review table never renders stale prefetched item errors from this request.
        batch = self._batch(request, batch_id, modes={PlacementBatch.Mode.SINGLE})
        if request.method == "POST" and result["ok"]:
            try:
                execute_create_batch(
                    batch,
                    actor=request.user,
                    ip_address=request.META.get("REMOTE_ADDR") or None,
                )
            except BatchValidationError as exc:
                messages.error(
                    request, "; ".join(error["message"] for error in exc.errors)
                )
            except ValidationError as exc:
                # A concurrent confirmation can finish while this request waits for
                # the batch row lock. Treat that replay as an idempotent result view.
                batch.refresh_from_db()
                if batch.is_executed:
                    return redirect("placements:batch_result", batch_id=batch.pk)
                messages.error(request, "; ".join(exc.messages))
            else:
                return redirect("placements:batch_result", batch_id=batch.pk)
        return self._render(
            request,
            "placements/admin/v2/review.html",
            title="预检查与确认 / Preflight review",
            batch=batch,
            result=result,
            back_url=reverse("placements:single_rules", kwargs={"batch_id": batch.pk}),
            execute_url=reverse(
                "placements:single_review", kwargs={"batch_id": batch.pk}
            ),
            result_url=reverse(
                "placements:batch_result", kwargs={"batch_id": batch.pk}
            ),
        )

    # --- journal curation ---
    def journals(self, request):
        if response := self._guard(request):
            return response
        page_obj, paginator = select_journals(
            user=request.user,
            query=request.GET.get("q", ""),
            scope=request.GET.get("scope", "all"),
            page=request.GET.get("page", 1),
            page_size=request.GET.get("page_size", 20),
        )
        return self._render(
            request,
            "placements/admin/v2/journal_selector.html",
            title="子期刊编排 / Select journal",
            page_obj=page_obj,
            paginator=paginator,
        )

    def journal_overview(self, request, journal_slug):
        if response := self._guard(request):
            return response
        journal = get_object_or_404(
            self._journal_queryset(request.user), slug=journal_slug
        )
        mark_journal_used(user=request.user, journal=journal)
        slots = LayoutSlot.objects.filter(
            scope=LayoutSlot.Scope.JOURNAL, is_active=True
        ).order_by("sort_order", "code")
        rows = []
        now = timezone.now()
        for slot in slots:
            qs = self._placement_queryset(request.user).filter(
                slot=slot,
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=journal.slug,
                is_active=True,
            )
            rows.append(
                {
                    "slot": slot,
                    "current": qs.filter(
                        Q(starts_at__isnull=True) | Q(starts_at__lte=now)
                    )
                    .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
                    .count(),
                    "future": qs.filter(starts_at__gt=now).count(),
                    "expiring": qs.filter(
                        ends_at__gt=now, ends_at__lte=now + timedelta(days=14)
                    ).count(),
                }
            )
        return self._render(
            request,
            "placements/admin/v2/journal_overview.html",
            title=f"子期刊编排 / {journal}",
            journal=journal,
            rows=rows,
        )

    def journal_slot(self, request, journal_slug, slot_code):
        if response := self._guard(request):
            return response
        journal = get_object_or_404(
            self._journal_queryset(request.user), slug=journal_slug
        )
        slot = get_object_or_404(
            LayoutSlot, code=slot_code, scope=LayoutSlot.Scope.JOURNAL
        )
        placements = (
            self._placement_queryset(request.user)
            .filter(
                slot=slot,
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=journal.slug,
            )
            .select_related("article", "article__primary_journal")
            .order_by("-is_active", "-is_pinned", "sort_order")
        )
        return self._render(
            request,
            "placements/admin/v2/journal_slot.html",
            title=f"{journal} / {slot.title} / 版位管理",
            journal=journal,
            slot=slot,
            placements=placements,
        )

    def journal_add_articles(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(
            request, batch_id, modes={PlacementBatch.Mode.JOURNAL_CURATION}
        )
        if request.method == "POST":
            update_draft(
                batch,
                actor=request.user,
                step="rules",
                selected_article_ids=request.POST.getlist("article_ids"),
            )
            return redirect("placements:batch_rules", batch_id=batch.pk)
        page_obj, paginator = select_articles(
            user=request.user,
            query=request.GET.get("q", ""),
            journal_slug=self._article_selector_journal(request, batch),
            page=request.GET.get("page", 1),
            page_size=request.GET.get("page_size", 20),
        )
        return self._render(
            request,
            "placements/admin/v2/article_selector.html",
            title="选择文章 / Select articles",
            batch=batch,
            page_obj=page_obj,
            paginator=paginator,
            selected_ids=set(batch.items.values_list("article_id", flat=True)),
            next_url=reverse(
                "placements:journal_add_articles", kwargs={"batch_id": batch.pk}
            ),
            selected_journal=self._journal_queryset(request.user)
            .filter(slug=batch.target_slug, status=JournalStatus.ACTIVE)
            .first(),
            single=False,
        )

    def journal_slot_add(self, request, journal_slug, slot_code):
        if response := self._guard(request):
            return response
        if not has_placement_permission(request.user, "add"):
            raise PermissionDenied
        journal = get_object_or_404(
            self._journal_queryset(request.user), slug=journal_slug
        )
        slot = get_object_or_404(
            LayoutSlot, code=slot_code, scope=LayoutSlot.Scope.JOURNAL, is_active=True
        )
        batch = create_draft(
            actor=request.user,
            mode=PlacementBatch.Mode.JOURNAL_CURATION,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=journal.slug,
            slot=slot,
            current_step="articles",
        )
        mark_journal_used(user=request.user, journal=journal)
        return redirect("placements:journal_add_articles", batch_id=batch.pk)

    # --- bulk create wizard ---
    def bulk_new(self, request):
        if response := self._guard(request):
            return response
        if not has_placement_permission(request.user, "add"):
            raise PermissionDenied
        batch = create_draft(
            actor=request.user,
            mode=PlacementBatch.Mode.BULK_CREATE,
            current_step="target",
        )
        return redirect("placements:bulk_target", batch_id=batch.pk)

    def bulk_target(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(request, batch_id, modes={PlacementBatch.Mode.BULK_CREATE})
        if request.method == "POST":
            journal = get_object_or_404(
                self._journal_queryset(request.user), pk=request.POST.get("journal_id")
            )
            update_draft(
                batch,
                actor=request.user,
                step="slot",
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=journal.slug,
            )
            mark_journal_used(user=request.user, journal=journal)
            return redirect("placements:bulk_slot", batch_id=batch.pk)
        page_obj, paginator = select_journals(
            user=request.user,
            query=request.GET.get("q", ""),
            scope=request.GET.get("scope", "all"),
            page=request.GET.get("page", 1),
            page_size=request.GET.get("page_size", 20),
        )
        return self._render(
            request,
            "placements/admin/v2/journal_selector.html",
            title="选择目标子期刊 / Select target journal",
            page_obj=page_obj,
            paginator=paginator,
            batch=batch,
            choose_url=reverse("placements:bulk_target", kwargs={"batch_id": batch.pk}),
        )

    def bulk_slot(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(request, batch_id, modes={PlacementBatch.Mode.BULK_CREATE})
        if not batch.target_slug:
            return redirect("placements:bulk_target", batch_id=batch.pk)
        slots = LayoutSlot.objects.filter(
            scope=LayoutSlot.Scope.JOURNAL, is_active=True
        ).order_by("sort_order", "code")
        if request.method == "POST":
            slot = get_object_or_404(slots, pk=request.POST.get("slot"))
            update_draft(batch, actor=request.user, step="articles", slot=slot)
            return redirect("placements:bulk_articles", batch_id=batch.pk)
        return self._render(
            request,
            "placements/admin/v2/slot_selector.html",
            title="选择版位 / Select slot",
            batch=batch,
            slots=slots,
        )

    def bulk_articles(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(request, batch_id, modes={PlacementBatch.Mode.BULK_CREATE})
        if not batch.slot_id:
            return redirect("placements:bulk_slot", batch_id=batch.pk)
        if request.method == "POST":
            update_draft(
                batch,
                actor=request.user,
                step="rules",
                selected_article_ids=request.POST.getlist("article_ids"),
            )
            return redirect("placements:batch_rules", batch_id=batch.pk)
        page_obj, paginator = select_articles(
            user=request.user,
            query=request.GET.get("q", ""),
            journal_slug=self._article_selector_journal(request, batch),
            page=request.GET.get("page", 1),
            page_size=request.GET.get("page_size", 20),
        )
        return self._render(
            request,
            "placements/admin/v2/article_selector.html",
            title="选择文章 / Select articles",
            batch=batch,
            page_obj=page_obj,
            paginator=paginator,
            selected_ids=set(batch.items.values_list("article_id", flat=True)),
            next_url=reverse("placements:bulk_articles", kwargs={"batch_id": batch.pk}),
            selected_journal=self._journal_queryset(request.user)
            .filter(slug=batch.target_slug, status=JournalStatus.ACTIVE)
            .first(),
            single=False,
        )

    def batch_rules(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(
            request,
            batch_id,
            modes={
                PlacementBatch.Mode.BULK_CREATE,
                PlacementBatch.Mode.JOURNAL_CURATION,
            },
        )
        if request.method == "POST":
            try:
                starts_at = self._parse_datetime(request.POST.get("starts_at"))
                ends_at = self._parse_datetime(request.POST.get("ends_at"))
                options = {**(batch.options or {}), "duplicate_handling": "strict"}
                update_draft(
                    batch,
                    actor=request.user,
                    step="review",
                    starts_at=starts_at,
                    ends_at=ends_at,
                    is_pinned=request.POST.get("is_pinned") == "on",
                    options=options,
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                return redirect("placements:batch_review", batch_id=batch.pk)
        return self._render(
            request,
            "placements/admin/v2/rules.html",
            title="设置统一规则 / Set shared rules",
            batch=batch,
            back_url=request.META.get("HTTP_REFERER")
            or reverse("placements:bulk_articles", kwargs={"batch_id": batch.pk}),
            post_url=reverse("placements:batch_rules", kwargs={"batch_id": batch.pk}),
            single=False,
        )

    def batch_review(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(
            request,
            batch_id,
            modes={
                PlacementBatch.Mode.BULK_CREATE,
                PlacementBatch.Mode.JOURNAL_CURATION,
            },
        )
        if batch.is_executed:
            return redirect("placements:batch_result", batch_id=batch.pk)
        result = precheck_batch(batch, actor=request.user)
        if request.method == "POST" and result["ok"]:
            try:
                execute_create_batch(
                    batch,
                    actor=request.user,
                    ip_address=request.META.get("REMOTE_ADDR") or None,
                )
            except BatchValidationError as exc:
                messages.error(
                    request, "; ".join(error["message"] for error in exc.errors)
                )
            except ValidationError as exc:
                batch.refresh_from_db()
                if batch.is_executed:
                    return redirect("placements:batch_result", batch_id=batch.pk)
                messages.error(request, "; ".join(exc.messages))
            else:
                return redirect("placements:batch_result", batch_id=batch.pk)
        return self._render(
            request,
            "placements/admin/v2/review.html",
            title="预检查与确认 / Preflight review",
            batch=batch,
            result=result,
            back_url=reverse("placements:batch_rules", kwargs={"batch_id": batch.pk}),
            execute_url=reverse(
                "placements:batch_review", kwargs={"batch_id": batch.pk}
            ),
            result_url=reverse(
                "placements:batch_result", kwargs={"batch_id": batch.pk}
            ),
        )

    def batch_result(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(request, batch_id)
        return self._render(
            request,
            "placements/admin/v2/result.html",
            title="执行结果 / Execution result",
            batch=batch,
        )

    # --- placement list and maintenance ---
    def placement_list(self, request):
        if response := self._guard(request):
            return response
        latest_item = (
            PlacementBatchItem.objects.filter(placement_id=OuterRef("pk"))
            .select_related("batch")
            .order_by("-batch__executed_at", "-batch__updated_at")
        )
        qs = (
            self._placement_queryset(request.user)
            .select_related("article", "article__primary_journal", "slot")
            .annotate(
                latest_batch_number=Subquery(
                    latest_item.values("batch__batch_number")[:1]
                ),
                latest_publish_status=Subquery(
                    latest_item.values("batch__publish_status")[:1]
                ),
            )
        )
        query = (request.GET.get("q") or "").strip()
        if query:
            qs = qs.filter(
                Q(article__title__icontains=query)
                | Q(article__static_slug__icontains=query)
            )
        if request.GET.get("journal"):
            qs = qs.filter(article__primary_journal__slug=request.GET["journal"])
        if request.GET.get("target_type"):
            qs = qs.filter(target_type=request.GET["target_type"])
        if request.GET.get("target_slug"):
            qs = qs.filter(target_slug=request.GET["target_slug"].strip("/"))
        if request.GET.get("slot"):
            qs = qs.filter(slot__code=request.GET["slot"])
        if request.GET.get("active") in {"0", "1"}:
            qs = qs.filter(is_active=request.GET["active"] == "1")
        issue = request.GET.get("issue")
        if issue == "article_inactive":
            qs = qs.filter(article__live=False)
        elif issue == "journal_inactive":
            qs = qs.filter(
                Q(article__primary_journal__isnull=True)
                | ~Q(article__primary_journal__status=JournalStatus.ACTIVE)
            )
        elif issue == "missing_alt":
            qs = qs.filter(override_image__isnull=False).filter(
                Q(override_image_alt__isnull=True) | Q(override_image_alt="")
            )
        if request.GET.get("pinned") in {"0", "1"}:
            qs = qs.filter(is_pinned=request.GET["pinned"] == "1")
        now = timezone.now()
        if request.GET.get("expired") == "1":
            qs = qs.filter(ends_at__lte=now)
        try:
            if request.GET.get("expires_within"):
                days = max(1, int(request.GET["expires_within"]))
                qs = qs.filter(ends_at__gt=now, ends_at__lte=now + timedelta(days=days))
        except (TypeError, ValueError):
            messages.error(
                request, "Expiry window must be a positive whole number of days."
            )
        for param, lookup in (
            ("starts_after", "starts_at__gte"),
            ("starts_before", "starts_at__lte"),
            ("ends_after", "ends_at__gte"),
            ("ends_before", "ends_at__lte"),
        ):
            if request.GET.get(param):
                try:
                    qs = qs.filter(**{lookup: self._parse_datetime(request.GET[param])})
                except ValidationError:
                    messages.error(
                        request,
                        f"{param.replace('_', ' ').title()} must be a valid date and time.",
                    )
        if request.GET.get("publish_status"):
            qs = qs.filter(
                batch_items__batch__publish_status=request.GET["publish_status"]
            )
        if request.GET.get("batch_number"):
            qs = qs.filter(
                batch_items__batch__batch_number__icontains=request.GET["batch_number"]
            )
        if request.GET.get("operator"):
            qs = qs.filter(
                batch_items__batch__created_by__username__icontains=request.GET[
                    "operator"
                ]
            )
        page_obj, paginator = self._paginate(
            qs.distinct().order_by("-updated_at", "-pk"), request
        )
        return self._render(
            request,
            "placements/admin/v2/placement_list.html",
            title="投放清单 / Placement list",
            page_obj=page_obj,
            paginator=paginator,
            slots=(
                LayoutSlot.objects.filter(is_active=True).order_by("code")
                if is_super_admin(request.user)
                else LayoutSlot.objects.filter(
                    is_active=True, scope=LayoutSlot.Scope.JOURNAL
                ).order_by("code")
            ),
            target_types=ArticlePlacement.TargetType.choices,
            publish_statuses=PlacementBatch.PublishStatus.choices,
        )

    def bulk_action(self, request):
        if response := self._guard(request):
            return response
        batch = None
        if request.method == "POST" and request.POST.get("batch_id"):
            batch = self._batch(
                request,
                request.POST["batch_id"],
                modes={PlacementBatch.Mode.BULK_MAINTENANCE},
            )
            intent = request.POST.get("intent", "execute")
            if intent == "prepare":
                options = dict(batch.options or {})
                try:
                    if batch.operation == PlacementBatch.Operation.UPDATE_SCHEDULE:
                        starts_at = self._parse_datetime(request.POST.get("starts_at"))
                        ends_at = (
                            None
                            if request.POST.get("open_ended") == "on"
                            else self._parse_datetime(request.POST.get("ends_at"))
                        )
                        if starts_at and ends_at and ends_at <= starts_at:
                            raise ValidationError(
                                "The end time must be later than the start time."
                            )
                        # PlacementBatch.options is JSON: persist ISO values rather than
                        # Python datetime objects so drafts remain durable and portable.
                        options["starts_at"] = (
                            starts_at.isoformat() if starts_at else None
                        )
                        options["ends_at"] = ends_at.isoformat() if ends_at else None
                        options["schedule_configured"] = True
                    elif batch.operation in {
                        PlacementBatch.Operation.MOVE,
                        PlacementBatch.Operation.COPY,
                    }:
                        options.update(
                            {
                                "target_type": ArticlePlacement.TargetType.JOURNAL,
                                "target_slug": request.POST.get("target_slug", ""),
                                "slot_id": request.POST.get("slot_id"),
                            }
                        )
                    elif batch.operation in {
                        PlacementBatch.Operation.DEACTIVATE,
                        PlacementBatch.Operation.CANCEL_FUTURE,
                    }:
                        options["reason"] = request.POST.get("reason", "")
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                else:
                    batch.options = options
                    batch.updated_by = request.user
                    batch.save(update_fields=("options", "updated_by", "updated_at"))
                    return redirect(
                        f"{reverse('placements:bulk_action')}?batch={batch.pk}"
                    )
            elif intent == "execute":
                errors = precheck_maintenance_batch(batch, actor=request.user)
                if errors:
                    messages.error(request, "; ".join(errors))
                else:
                    try:
                        execute_maintenance_batch(
                            batch,
                            actor=request.user,
                            ip_address=request.META.get("REMOTE_ADDR") or None,
                        )
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                    else:
                        return redirect("placements:batch_detail", batch_id=batch.pk)
        elif request.method == "POST":
            try:
                batch = create_maintenance_batch(
                    actor=request.user,
                    operation=request.POST.get("operation"),
                    placement_ids=request.POST.getlist("placement_ids"),
                    options={"reason": request.POST.get("reason", "")},
                )
            except (PermissionDenied, ValidationError) as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("placements:list")
            return redirect(f"{reverse('placements:bulk_action')}?batch={batch.pk}")
        else:
            batch_id = request.GET.get("batch")
            if not batch_id:
                return redirect("placements:list")
            batch = self._batch(
                request, batch_id, modes={PlacementBatch.Mode.BULK_MAINTENANCE}
            )

        requires_schedule = batch.operation == PlacementBatch.Operation.UPDATE_SCHEDULE
        requires_target = batch.operation in {
            PlacementBatch.Operation.MOVE,
            PlacementBatch.Operation.COPY,
        }
        requires_reason = batch.operation in {
            PlacementBatch.Operation.DEACTIVATE,
            PlacementBatch.Operation.CANCEL_FUTURE,
        }
        options = batch.options or {}
        configured = (
            (not requires_schedule or options.get("schedule_configured"))
            and (
                not requires_target
                or (options.get("target_slug") and options.get("slot_id"))
            )
            and (not requires_reason or (options.get("reason") or "").strip())
        )
        errors = (
            precheck_maintenance_batch(batch, actor=request.user) if configured else []
        )
        return self._render(
            request,
            "placements/admin/v2/bulk_action.html",
            title="批量操作复核 / Batch action review",
            batch=batch,
            errors=errors,
            ready_to_execute=configured and not errors,
            requires_schedule=requires_schedule,
            requires_target=requires_target,
            requires_reason=requires_reason,
            journal_slots=LayoutSlot.objects.filter(
                scope=LayoutSlot.Scope.JOURNAL, is_active=True
            ).order_by("sort_order", "code"),
        )

    def placement_export(self, request):
        if response := self._guard(request):
            return response
        queryset = self._placement_queryset(request.user).select_related(
            "article", "article__primary_journal", "slot"
        )
        response = HttpResponse(
            export_placements_csv(queryset), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = "attachment; filename=placements.csv"
        return response

    def batches(self, request):
        if response := self._guard(request):
            return response
        qs = PlacementBatch.objects.select_related("created_by", "slot", "publish_job")
        if request.GET.get("status"):
            qs = qs.filter(status=request.GET["status"])
        if not is_super_admin(request.user):
            accessible_articles = filter_accessible_articles(
                request.user, ArticlePage.objects.all()
            )
            accessible_slugs = self._journal_queryset(request.user).values("slug")
            qs = (
                qs.filter(created_by=request.user)
                .filter(
                    Q(items__article__in=accessible_articles)
                    | Q(
                        items__isnull=True,
                        target_type=ArticlePlacement.TargetType.JOURNAL,
                        target_slug__in=accessible_slugs,
                    )
                )
                .distinct()
            )
        page_obj, paginator = self._paginate(qs, request)
        return self._render(
            request,
            "placements/admin/v2/batches.html",
            title="投放批次 / Placement batches",
            page_obj=page_obj,
            paginator=paginator,
            batch_statuses=PlacementBatch.Status.choices,
        )

    def batch_detail(self, request, batch_id):
        if response := self._guard(request):
            return response
        batch = self._batch(request, batch_id)
        return self._render(
            request,
            "placements/admin/v2/batch_detail.html",
            title=f"批次详情 / Batch details / {batch.batch_number}",
            batch=batch,
        )

    def delete_draft(self, request, batch_id):
        """Delete an unexecuted draft without ever touching formal placements."""
        if request.method != "POST":
            return HttpResponse(status=405)
        if response := self._guard(request):
            return response
        batch = self._batch(request, batch_id)
        permission_action = (
            "change" if batch.mode == PlacementBatch.Mode.BULK_MAINTENANCE else "add"
        )
        if not has_placement_permission(request.user, permission_action):
            raise PermissionDenied
        if batch.is_executed:
            messages.error(request, "Executed batches cannot be deleted.")
            return redirect("placements:batch_detail", batch_id=batch.pk)

        batch_id_value = str(batch.pk)
        batch_number = batch.batch_number
        with transaction.atomic():
            AuditLog.record(
                action=AuditAction.CONFIGURE,
                status=AuditStatus.SUCCESS,
                actor=request.user,
                target=batch,
                message="Placement draft deleted.",
                metadata={
                    "batch_id": batch_id_value,
                    "batch_number": batch_number,
                    "mode": batch.mode,
                    "operation": batch.operation,
                    "item_count": batch.items.count(),
                    "ip_address": request.META.get("REMOTE_ADDR") or None,
                },
                ip_address=request.META.get("REMOTE_ADDR") or None,
            )
            batch.delete()
        messages.success(request, "Draft deleted.")
        return redirect("placements:batches")

    def _paginate(self, queryset, request):
        from django.core.paginator import Paginator

        try:
            page_size = int(request.GET.get("page_size", 20) or 20)
        except (TypeError, ValueError):
            page_size = 20
        page_size = page_size if page_size in {20, 50, 100} else 20
        paginator = Paginator(queryset, page_size)
        return paginator.get_page(request.GET.get("page", 1)), paginator

    @staticmethod
    def _parse_datetime(value):
        if not value:
            return None
        from django.forms import DateTimeField

        return DateTimeField().clean(value)

    def get_urlpatterns(self):
        return [
            path("", self.index_view, name="index"),
            path("new/article/", self.new_single, name="new_single"),
            path(
                "drafts/<uuid:batch_id>/article/",
                self.single_article,
                name="single_article",
            ),
            path(
                "drafts/<uuid:batch_id>/target/",
                self.single_target,
                name="single_target",
            ),
            path(
                "drafts/<uuid:batch_id>/rules/", self.single_rules, name="single_rules"
            ),
            path(
                "drafts/<uuid:batch_id>/review/",
                self.single_review,
                name="single_review",
            ),
            path("journals/", self.journals, name="journals"),
            path(
                "journals/<slug:journal_slug>/",
                self.journal_overview,
                name="journal_overview",
            ),
            path(
                "journals/<slug:journal_slug>/slots/<slug:slot_code>/",
                self.journal_slot,
                name="journal_slot",
            ),
            path(
                "journals/<slug:journal_slug>/slots/<slug:slot_code>/add/",
                self.journal_slot_add,
                name="journal_slot_add",
            ),
            path(
                "journal-drafts/<uuid:batch_id>/articles/",
                self.journal_add_articles,
                name="journal_add_articles",
            ),
            path("bulk/new/", self.bulk_new, name="bulk_new"),
            path(
                "batches/<uuid:batch_id>/target/", self.bulk_target, name="bulk_target"
            ),
            path("batches/<uuid:batch_id>/slot/", self.bulk_slot, name="bulk_slot"),
            path(
                "batches/<uuid:batch_id>/articles/",
                self.bulk_articles,
                name="bulk_articles",
            ),
            path(
                "batches/<uuid:batch_id>/rules/", self.batch_rules, name="batch_rules"
            ),
            path(
                "batches/<uuid:batch_id>/review/",
                self.batch_review,
                name="batch_review",
            ),
            path(
                "batches/<uuid:batch_id>/result/",
                self.batch_result,
                name="batch_result",
            ),
            path("list/", self.placement_list, name="list"),
            path("list/bulk-action/", self.bulk_action, name="bulk_action"),
            path("list/export/", self.placement_export, name="placement_export"),
            path("batches/", self.batches, name="batches"),
            path("batches/<uuid:batch_id>/", self.batch_detail, name="batch_detail"),
            path(
                "batches/<uuid:batch_id>/delete/",
                self.delete_draft,
                name="delete_draft",
            ),
            path("api/journals/", self.journals_api, name="journals_api"),
            path(
                "api/journals/<int:journal_id>/favorite/",
                self.journal_favorite_api,
                name="journal_favorite_api",
            ),
            path("api/articles/", self.articles_api, name="articles_api"),
            path("api/images/", self.images_api, name="images_api"),
            path("api/images/upload/", self.image_upload_api, name="image_upload_api"),
            path("api/capacity/", self.capacity_api, name="capacity_api"),
            path("api/slots/", self.slots_api, name="slots_api"),
        ]
