from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone, translation

from ai_author_forum.articles.display import resolve_article_image
from ai_author_forum.articles.integrations import get_site_settings
from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.site_settings.access_control import is_super_admin
from ai_author_forum.site_settings.admin_views import PermissionedModuleViewSet
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.site_settings.permissions import get_admin_permission_context
from ai_author_forum.static_publish.models import StaticPublishJob
from ai_author_forum.utils.admin_i18n import admin_text
from ai_author_forum.utils.admin_ui import translate_form_to_english

from .category_services import sync_category_placements
from .forms import (
    SLOT_SCOPE_CHOICES,
    BulkJournalPlacementForm,
    LayoutSlotAdminForm,
    LayoutSlotFilterForm,
    PlacementAdminForm,
    PlacementFilterForm,
    get_target_label,
    split_target_value,
)
from .models import ArticlePlacement, LayoutSlot
from .services import (
    bulk_place_articles_in_journal,
    deactivate_manual_placement,
    get_journal_placeable_articles,
    get_placeable_articles,
    get_slot_items,
    has_placement_permission,
    placement_capacity,
    reorder_placements,
    save_manual_placement,
)


def _has_model_permission(user, action):
    return has_placement_permission(user, action)


def _has_slot_model_permission(user, action):
    return is_super_admin(user)


def _slot_metadata(slot):
    return {
        "title": slot.title,
        "code": slot.code,
        "scope": slot.scope,
        "max_items": slot.max_items,
        "fill_mode": slot.fill_mode,
        "description": slot.description,
        "is_active": slot.is_active,
        "sort_order": slot.sort_order,
    }


def _placement_metadata(placement):
    return {
        "article_id": placement.article_id,
        "article_static_slug": placement.article.static_slug,
        "slot": placement.slot.code,
        "target_type": placement.target_type,
        "target_slug": placement.target_slug,
        "is_pinned": placement.is_pinned,
        "sort_order": placement.sort_order,
        "starts_at": placement.starts_at.isoformat() if placement.starts_at else None,
        "ends_at": placement.ends_at.isoformat() if placement.ends_at else None,
        "is_active": placement.is_active,
    }


HOME_COMPOSITION_SLOT_CODES = (
    "home_hero",
    "home_visual_stories",
    "home_featured",
)
HOME_COMPOSITION_REQUIRED_COUNTS = {"home_hero": 1, "home_visual_stories": 2}
HOME_COMPOSITION_TITLE_WARNING_LENGTH = 120
HOME_COMPOSITION_SUMMARY_WARNING_LENGTH = 280


def _schedule_overlaps(left, right):
    return (
        left.ends_at is None
        or right.starts_at is None
        or right.starts_at < left.ends_at
    ) and (
        right.ends_at is None
        or left.starts_at is None
        or left.starts_at < right.ends_at
    )


def _homepage_duplicate_placement_ids(placements):
    duplicate_ids = set()
    for index, placement in enumerate(placements):
        for peer in placements[index + 1 :]:
            if placement.article_id == peer.article_id and _schedule_overlaps(
                placement, peer
            ):
                duplicate_ids.update((placement.pk, peer.pk))
    return duplicate_ids


def _has_valid_homepage_alt(placement, visual):
    image = visual.image
    return bool(
        (placement.override_image_alt or "").strip()
        or (placement.article.featured_image_alt or "").strip()
        or (getattr(image, "description", "") or "").strip()
        or (getattr(image, "title", "") or "").strip()
    )


def _pending_homepage_publish_job():
    jobs = StaticPublishJob.objects.filter(
        status=StaticPublishJob.Status.PENDING, is_automatic=False
    ).order_by("-created_at")[:50]
    for job in jobs:
        summary = job.summary or {}
        if summary.get("requires_publisher_approval") and "/" in (
            job.requested_paths or []
        ):
            return job
    return None


class HomepageCompositionViewSet(PermissionedModuleViewSet):
    name = "homepage-composition"
    menu_label = admin_text("placements.homepage")
    menu_name = "homepage-composition"
    menu_icon = "home"
    menu_order = 229
    permission = "site_settings.access_placements"
    title = admin_text("placements.homepage")

    def has_access(self, request) -> bool:
        return is_super_admin(request.user)

    def index_view(self, request):
        if not self.has_access(request):
            raise PermissionDenied
        now = timezone.now()
        slots = {
            slot.code: slot
            for slot in LayoutSlot.objects.filter(code__in=HOME_COMPOSITION_SLOT_CODES)
        }
        scheduled = list(
            ArticlePlacement.objects.filter(
                target_type=ArticlePlacement.TargetType.MAIN_SITE,
                target_slug="",
                slot__code__in=HOME_COMPOSITION_SLOT_CODES,
                source=ArticlePlacement.Source.MANUAL,
                is_active=True,
            )
            .select_related(
                "slot",
                "article",
                "article__primary_journal",
                "article__featured_image",
                "override_image",
            )
            .order_by("slot__sort_order", "-is_pinned", "sort_order", "pk")
        )
        effective_ids = set(
            ArticlePlacement.objects.available(at=now)
            .filter(pk__in=[item.pk for item in scheduled])
            .values_list("pk", flat=True)
        )
        duplicate_ids = _homepage_duplicate_placement_ids(scheduled)
        site_settings = get_site_settings(request)
        panels = []
        can_edit_articles = get_admin_permission_context(request.user)[
            "can_edit_article"
        ]
        for code in HOME_COMPOSITION_SLOT_CODES:
            slot = slots.get(code)
            placements = [item for item in scheduled if item.slot.code == code]
            effective = [item for item in placements if item.pk in effective_ids]
            required = HOME_COMPOSITION_REQUIRED_COUNTS.get(code)
            panel_warnings = []
            if slot is None:
                panel_warnings.append("The controlled slot preset is missing.")
            elif required is not None and len(effective) != required:
                panel_warnings.append(
                    f"Formal publication requires exactly {required} effective item(s); "
                    f"currently {len(effective)}."
                )
            rows = []
            for placement in placements:
                visual = resolve_article_image(
                    placement.article,
                    placement=placement,
                    request=request,
                    site_settings=site_settings,
                )
                warnings = []
                if visual.is_placeholder:
                    warnings.append("The image resolves to the legacy placeholder.")
                if not _has_valid_homepage_alt(placement, visual):
                    warnings.append(
                        "A valid image Alt is required for formal publication."
                    )
                if placement.pk in duplicate_ids:
                    warnings.append(
                        "This article overlaps another principal homepage slot."
                    )
                if (
                    len(placement.display_title or "")
                    > HOME_COMPOSITION_TITLE_WARNING_LENGTH
                ):
                    warnings.append("The display title is long; review both previews.")
                if (
                    len(placement.display_summary or "")
                    > HOME_COMPOSITION_SUMMARY_WARNING_LENGTH
                ):
                    warnings.append(
                        "The display summary is long; review both previews."
                    )
                if placement.starts_at and placement.starts_at > now:
                    schedule_state = "Scheduled"
                elif placement.ends_at and placement.ends_at <= now:
                    schedule_state = "Expired"
                elif placement.pk in effective_ids:
                    schedule_state = "Effective now"
                else:
                    schedule_state = "Unavailable now"
                rows.append(
                    {
                        "placement": placement,
                        "visual": visual,
                        "schedule_state": schedule_state,
                        "warnings": warnings,
                        "can_edit_article": (
                            can_edit_articles
                            and placement.article.permissions_for_user(
                                request.user
                            ).can_edit()
                        ),
                    }
                )
            panels.append(
                {
                    "code": code,
                    "slot": slot,
                    "placements": rows,
                    "preview_placements": effective,
                    "effective_count": len(effective),
                    "missing_count": max(0, (required or 0) - len(effective)),
                    "warnings": panel_warnings,
                }
            )
        return render(
            request,
            (
                "placements/admin/homepage_composition.en.html"
                if (translation.get_language() or "").lower().startswith("en")
                else "placements/admin/homepage_composition.html"
            ),
            {
                "title": self.title,
                "panels": panels,
                "pending_publish_job": _pending_homepage_publish_job(),
                "can_change": _has_model_permission(request.user, "change"),
                "can_add": _has_model_permission(request.user, "add"),
                "now": now,
            },
        )

    def get_urlpatterns(self):
        return [path("", self.index_view, name="index")]


class PlacementsViewSet(PermissionedModuleViewSet):
    """Legacy workbench retained only as a superuser compatibility fallback."""

    name = "placements_legacy"
    url_prefix = "placements/legacy"
    add_to_admin_menu = False
    menu_label = admin_text("placements.manage")
    menu_name = "placements_legacy"
    menu_icon = "pick"
    menu_order = 230
    permission = "site_settings.access_placements"
    title = admin_text("placements.manage")
    description = admin_text("placements.manage.description")
    owner = "D：placements 应用；A 提供菜单、权限边界和跨模块接入点。"
    integration_points = ("ArticlePlacement", "get_slot_items(slot_code, journal=None)")

    def has_access(self, request) -> bool:
        return is_super_admin(request.user)

    def _get_instance(self, request):
        placement_id = request.POST.get("placement_id") or request.GET.get("edit")
        if not placement_id:
            return None
        return get_object_or_404(
            ArticlePlacement.objects.filter(
                source=ArticlePlacement.Source.MANUAL
            ).select_related(
                "article", "article__primary_journal", "slot", "override_image"
            ),
            pk=placement_id,
        )

    def _get_current_placements(self, filter_form):
        now = timezone.now()
        data = filter_form.cleaned_data if filter_form.is_valid() else {}
        has_dashboard_filter = any(
            data.get(name)
            for name in ("expires_within", "expired", "active", "capacity")
        )
        manager = ArticlePlacement.objects
        if has_dashboard_filter:
            queryset = manager.filter(source=ArticlePlacement.Source.MANUAL)
        else:
            queryset = manager.available(at=now).filter(
                source=ArticlePlacement.Source.MANUAL
            )
        queryset = queryset.select_related(
            "article", "article__primary_journal", "slot", "override_image"
        ).order_by(
            "target_type",
            "target_slug",
            "slot__sort_order",
            "slot__code",
            "-is_pinned",
            "sort_order",
            "pk",
        )
        if not filter_form.is_valid():
            return queryset

        target_value = data.get("target")
        slot = data.get("slot")
        if target_value:
            target_type, target_slug = split_target_value(target_value)
            if slot and not has_dashboard_filter:
                return get_slot_items(
                    slot.code,
                    target_type=target_type,
                    target_slug=target_slug,
                    at=now,
                )
            queryset = queryset.for_target(target_type, target_slug)
        if slot:
            queryset = queryset.filter(slot=slot)

        active = data.get("active")
        if active in {"0", "1"}:
            queryset = queryset.filter(is_active=active == "1")
        if data.get("expired") == "1":
            queryset = queryset.filter(ends_at__lte=now)
        if data.get("expires_within"):
            expires_before = now + timedelta(days=data["expires_within"])
            queryset = queryset.filter(
                is_active=True,
                ends_at__gt=now,
                ends_at__lte=expires_before,
            )
        if data.get("capacity") == "over":
            groups = (
                ArticlePlacement.objects.filter(
                    source=ArticlePlacement.Source.MANUAL,
                    is_active=True,
                    slot__is_active=True,
                )
                .values("slot_id", "target_type", "target_slug", "target_category_id")
                .annotate(item_count=Count("pk"), capacity=F("slot__max_items"))
                .filter(item_count__gt=F("capacity"))
            )
            capacity_filter = Q(pk__in=[])
            for group in groups:
                group_filter = Q(
                    slot_id=group["slot_id"],
                    target_type=group["target_type"],
                    target_slug=group["target_slug"],
                )
                if group["target_category_id"] is None:
                    group_filter &= Q(target_category_id__isnull=True)
                else:
                    group_filter &= Q(target_category_id=group["target_category_id"])
                capacity_filter |= group_filter
            queryset = queryset.filter(
                is_active=True,
                slot__is_active=True,
            ).filter(capacity_filter)
        return queryset

    def _render(
        self, request, *, form, filter_form, bulk_form=None, preview_placement=None
    ):
        current_queryset = self._get_current_placements(filter_form)
        current_total = current_queryset.count()
        current_placements = list(current_queryset[:100])
        article_query = ""
        if filter_form.is_bound and filter_form.is_valid():
            article_query = filter_form.cleaned_data.get("article_query", "")
        instance = form.instance if form.instance and form.instance.pk else None
        if bulk_form is None:
            bulk_form = BulkJournalPlacementForm(auto_id="id_bulk_%s")
        is_english = (translation.get_language() or "").lower().startswith("en")
        if is_english:
            translate_form_to_english(form)
            translate_form_to_english(filter_form)
            translate_form_to_english(bulk_form)
        template_name = (
            "placements/admin/dashboard.en.html"
            if is_english
            else "placements/admin/dashboard.html"
        )
        return render(
            request,
            template_name,
            {
                "title": self.title,
                "form": form,
                "filter_form": filter_form,
                "bulk_form": bulk_form,
                "editing_placement": instance,
                "preview_placement": preview_placement,
                "preview_target_label": (
                    get_target_label(
                        preview_placement.target_type, preview_placement.target_slug
                    )
                    if preview_placement
                    else ""
                ),
                "current_placements": current_placements,
                "current_total": current_total,
                "current_truncated": current_total > len(current_placements),
                "approved_article_count": get_placeable_articles(article_query).count(),
                "article_query": article_query,
                "can_add": _has_model_permission(request.user, "add"),
                "can_change": _has_model_permission(request.user, "change"),
                "now": timezone.now(),
            },
        )

    def index_view(self, request):
        if not self.has_access(request):
            raise PermissionDenied

        filter_form = PlacementFilterForm(request.GET or None)
        instance = self._get_instance(request)
        if request.method == "POST":
            if request.POST.get("mode") == "bulk_journal":
                if not _has_model_permission(request.user, "add"):
                    raise PermissionDenied
                bulk_form = BulkJournalPlacementForm(request.POST, auto_id="id_bulk_%s")
                article_query = (request.GET.get("article_query") or "").strip()
                form = PlacementAdminForm(
                    instance=instance,
                    article_search=article_query,
                )
                if bulk_form.is_valid():
                    try:
                        result = bulk_place_articles_in_journal(
                            articles=bulk_form.cleaned_data["articles"],
                            journal=bulk_form.cleaned_data["journal"],
                            slot=bulk_form.cleaned_data["slot"],
                            starts_at=bulk_form.cleaned_data.get("starts_at"),
                            ends_at=bulk_form.cleaned_data.get("ends_at"),
                            is_pinned=bulk_form.cleaned_data.get("is_pinned", False),
                            actor=request.user,
                            ip_address=request.META.get("REMOTE_ADDR") or None,
                        )
                    except forms.ValidationError as exc:
                        bulk_form.add_error(None, "; ".join(exc.messages))
                    else:
                        messages.success(
                            request,
                            "批量投放完成：新建 "
                            f"{len(result['created'])} 篇，重新启用 "
                            f"{len(result['reactivated'])} 篇，跳过已投放 "
                            f"{len(result['skipped'])} 篇。",
                        )
                        pending_job = result.get("pending_publish_job")
                        if pending_job is not None:
                            messages.info(
                                request,
                                f"Static publish job #{pending_job.pk} is waiting for publishing-administrator approval.",
                            )
                        else:
                            messages.info(
                                request, "Automatic static publication was queued."
                            )
                        query = urlencode(
                            {
                                "target": (
                                    f"{ArticlePlacement.TargetType.JOURNAL}:"
                                    f"{bulk_form.cleaned_data['journal'].slug}"
                                ),
                                "slot": bulk_form.cleaned_data["slot"].pk,
                            }
                        )
                        return redirect(f"{reverse('placements:index')}?{query}")
                return self._render(
                    request,
                    form=form,
                    filter_form=filter_form,
                    bulk_form=bulk_form,
                )

            required_action = "change" if instance else "add"
            if not _has_model_permission(request.user, required_action):
                raise PermissionDenied
            article_query = (request.POST.get("article_query") or "").strip()
            form = PlacementAdminForm(
                request.POST,
                request.FILES,
                instance=instance,
                article_search=article_query,
            )
            action = request.POST.get("action", "save")
            if form.is_valid():
                if action == "preview":
                    preview_placement = form.save(commit=False)
                    return self._render(
                        request,
                        form=form,
                        filter_form=filter_form,
                        preview_placement=preview_placement,
                    )
                placement = form.save(commit=False)
                placement = save_manual_placement(
                    placement,
                    actor=request.user,
                    ip_address=request.META.get("REMOTE_ADDR") or None,
                )
                messages.success(request, "投放配置已保存。")
                pending_job = getattr(placement, "pending_publish_job", None)
                if pending_job is not None:
                    messages.info(
                        request,
                        f"Static publish job #{pending_job.pk} is waiting for publishing-administrator approval.",
                    )
                else:
                    messages.info(request, "Automatic static publication was queued.")
                return redirect(f"{reverse('placements:index')}?edit={placement.pk}")
        else:
            article_query = (request.GET.get("article_query") or "").strip()
            if instance and not _has_model_permission(request.user, "change"):
                raise PermissionDenied
            form = PlacementAdminForm(
                instance=instance,
                article_search=article_query,
            )

        return self._render(request, form=form, filter_form=filter_form)

    def bulk_articles_view(self, request):
        if not self.has_access(request):
            raise PermissionDenied
        journal_slug = (request.GET.get("journal") or "").strip()
        from ai_author_forum.journals.models import Journal, JournalStatus

        journal = get_object_or_404(
            Journal,
            slug=journal_slug,
            status=JournalStatus.ACTIVE,
        )
        articles = get_journal_placeable_articles(
            journal,
            (request.GET.get("q") or "").strip(),
        )[:500]
        return JsonResponse(
            {
                "journal": journal.slug,
                "articles": [
                    {
                        "id": article.pk,
                        "label": f"{article.title}（{article.static_slug}）",
                    }
                    for article in articles
                ],
            }
        )

    def capacity_view(self, request):
        if not self.has_access(request):
            raise PermissionDenied
        try:
            target_type, target_slug = split_target_value(request.GET.get("target", ""))
            slot = get_object_or_404(LayoutSlot, pk=request.GET.get("slot"))
            placement = ArticlePlacement(
                pk=request.GET.get("placement_id") or None,
                slot=slot,
                target_type=target_type,
                target_slug=target_slug,
                starts_at=forms.DateTimeField(required=False).clean(
                    request.GET.get("starts_at") or None
                ),
                ends_at=forms.DateTimeField(required=False).clean(
                    request.GET.get("ends_at") or None
                ),
                is_active=True,
            )
            capacity = placement_capacity(placement)
            capacity["slot"] = slot.code
            capacity["target"] = get_target_label(target_type, target_slug)
            return JsonResponse(capacity)
        except (forms.ValidationError, ValueError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)

    def reorder_view(self, request):
        if request.method != "POST":
            return JsonResponse({"error": "仅支持 POST。"}, status=405)
        if not self.has_access(request):
            raise PermissionDenied
        try:
            ordered = reorder_placements(
                request.POST.getlist("placement_ids"),
                actor=request.user,
                ip_address=request.META.get("REMOTE_ADDR") or None,
            )
        except (forms.ValidationError, ValueError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        pending_job = (
            getattr(ordered[0], "pending_publish_job", None) if ordered else None
        )
        return JsonResponse(
            {
                "ok": True,
                "placement_ids": [placement.pk for placement in ordered],
                "pending_publish_job_id": pending_job.pk if pending_job else None,
                "publish_status": (
                    "pending_publisher_approval" if pending_job else "automatic_publish"
                ),
            }
        )

    def deactivate_view(self, request, placement_id):
        if request.method != "POST":
            return JsonResponse({"error": "仅支持 POST。"}, status=405)
        if not self.has_access(request):
            raise PermissionDenied
        placement = deactivate_manual_placement(
            placement_id,
            actor=request.user,
            ip_address=request.META.get("REMOTE_ADDR") or None,
        )
        messages.success(request, "投放已停用。")
        pending_job = getattr(placement, "pending_publish_job", None)
        if pending_job is not None:
            messages.info(
                request,
                f"Static publish job #{pending_job.pk} is waiting for publishing-administrator approval.",
            )
        else:
            messages.info(request, "Automatic static publication was queued.")
        return redirect("placements:index")

    def get_urlpatterns(self):
        return [
            path("", self.index_view, name="index"),
            path("bulk-articles/", self.bulk_articles_view, name="bulk_articles"),
            path("capacity/", self.capacity_view, name="capacity"),
            path("reorder/", self.reorder_view, name="reorder"),
            path(
                "<int:placement_id>/deactivate/",
                self.deactivate_view,
                name="deactivate",
            ),
        ]


class SlotsViewSet(PermissionedModuleViewSet):
    name = "layout-slots"
    menu_label = admin_text("placements.slots")
    menu_name = "layout-slots"
    menu_icon = "list-ul"
    menu_order = 240
    permission = "site_settings.access_slots"
    title = admin_text("placements.slots.title")
    description = admin_text("placements.slots.description")
    owner = "D：placements 应用；A 提供受控后台入口、权限和审计。"
    integration_points = (
        "LayoutSlot",
        "ArticlePlacement",
        "get_slot_items(slot_code, journal=None)",
    )

    def has_access(self, request) -> bool:
        return is_super_admin(request.user)

    def _normalise_target(self, scope, target_value):
        if target_value:
            try:
                target_type, target_slug = split_target_value(target_value)
            except forms.ValidationError:
                return ""
            expected_scope = {
                ArticlePlacement.TargetType.MAIN_SITE: LayoutSlot.Scope.HOME,
                ArticlePlacement.TargetType.SECTION: LayoutSlot.Scope.SECTION,
                ArticlePlacement.TargetType.JOURNAL: LayoutSlot.Scope.JOURNAL,
                ArticlePlacement.TargetType.SEARCH: LayoutSlot.Scope.SEARCH,
            }.get(target_type)
            if expected_scope == scope:
                return target_value
        if scope == LayoutSlot.Scope.HOME:
            return "main_site:"
        if scope == LayoutSlot.Scope.SEARCH:
            return "search:search"
        return ""

    def _get_filter_state(self, request):
        filter_form = LayoutSlotFilterForm(request.GET or None)
        valid_scopes = dict(SLOT_SCOPE_CHOICES)
        selected_scope = request.GET.get("scope", LayoutSlot.Scope.HOME)
        if selected_scope not in valid_scopes:
            selected_scope = LayoutSlot.Scope.HOME

        selected_slot = None
        target_value = ""
        if filter_form.is_bound and filter_form.is_valid():
            selected_scope = filter_form.cleaned_data["scope"]
            selected_slot = filter_form.cleaned_data.get("slot")
            target_value = filter_form.cleaned_data.get("target", "")

        if selected_slot is None:
            slots = LayoutSlot.objects.filter(scope=selected_scope)
            if filter_form.is_bound and filter_form.is_valid():
                active = filter_form.cleaned_data.get("active")
                if active in {"0", "1"}:
                    slots = slots.filter(is_active=active == "1")
            selected_slot = slots.order_by("sort_order", "code").first()
        target_value = self._normalise_target(selected_scope, target_value)
        return filter_form, selected_scope, selected_slot, target_value

    @staticmethod
    def _scope_slots(filter_form, selected_scope):
        queryset = LayoutSlot.objects.filter(scope=selected_scope)
        if filter_form.is_bound and filter_form.is_valid():
            active = filter_form.cleaned_data.get("active")
            if active in {"0", "1"}:
                queryset = queryset.filter(is_active=active == "1")
        return queryset.order_by("sort_order", "code")

    def _render(
        self,
        request,
        *,
        filter_form,
        selected_scope,
        selected_slot,
        target_value,
        slot_form=None,
    ):
        current_placements = []
        current_total = 0
        target_label = ""
        if selected_slot and target_value:
            target_type, target_slug = split_target_value(target_value)
            current_queryset = get_slot_items(
                selected_slot.code,
                target_type=target_type,
                target_slug=target_slug,
                at=timezone.now(),
            )
            current_total = current_queryset.count()
            current_placements = list(current_queryset[: selected_slot.max_items])
            target_label = get_target_label(target_type, target_slug)

        if (
            slot_form is None
            and selected_slot
            and _has_slot_model_permission(request.user, "change")
        ):
            slot_form = LayoutSlotAdminForm(instance=selected_slot)

        return render(
            request,
            "placements/admin/slots_dashboard.html",
            {
                "title": self.title,
                "description": self.description,
                "filter_form": filter_form,
                "scope_choices": SLOT_SCOPE_CHOICES,
                "selected_scope": selected_scope,
                "selected_slot": selected_slot,
                "scope_slots": self._scope_slots(filter_form, selected_scope),
                "target_value": target_value,
                "target_label": target_label,
                "slot_form": slot_form,
                "can_change": _has_slot_model_permission(request.user, "change"),
                "current_placements": current_placements,
                "current_total": current_total,
                "current_truncated": current_total > len(current_placements),
                "now": timezone.now(),
            },
        )

    def index_view(self, request):
        if not self.has_access(request):
            raise PermissionDenied

        if request.method == "POST":
            if not _has_slot_model_permission(request.user, "change"):
                raise PermissionDenied
            selected_slot = get_object_or_404(
                LayoutSlot, pk=request.POST.get("slot_id")
            )
            selected_scope = selected_slot.scope
            target_value = self._normalise_target(
                selected_scope, request.POST.get("target", "")
            )
            filter_form = LayoutSlotFilterForm(
                initial={
                    "scope": selected_scope,
                    "slot": selected_slot,
                    "target": target_value,
                }
            )
            before = _slot_metadata(selected_slot)
            slot_form = LayoutSlotAdminForm(request.POST, instance=selected_slot)
            if slot_form.is_valid():
                with transaction.atomic():
                    selected_slot = slot_form.save()
                    AuditLog.record(
                        action=AuditAction.CONFIGURE,
                        status=AuditStatus.SUCCESS,
                        actor=request.user,
                        target=selected_slot,
                        message="更新受控版位配置。",
                        metadata={
                            "before": before,
                            "after": _slot_metadata(selected_slot),
                        },
                        ip_address=request.META.get("REMOTE_ADDR") or None,
                    )
                messages.success(request, "版位配置已保存。")
                query = urlencode(
                    {
                        "scope": selected_scope,
                        "slot": selected_slot.pk,
                        "target": target_value,
                    }
                )
                return redirect(f"{reverse('layout-slots:index')}?{query}")
            return self._render(
                request,
                filter_form=filter_form,
                selected_scope=selected_scope,
                selected_slot=selected_slot,
                target_value=target_value,
                slot_form=slot_form,
            )

        filter_form, selected_scope, selected_slot, target_value = (
            self._get_filter_state(request)
        )
        return self._render(
            request,
            filter_form=filter_form,
            selected_scope=selected_scope,
            selected_slot=selected_slot,
            target_value=target_value,
        )

    def get_urlpatterns(self):
        return [path("", self.index_view, name="index")]


class SystemCategoryPlacementsViewSet(PermissionedModuleViewSet):
    name = "system-category-placements"
    menu_label = admin_text("placements.system_categories")
    menu_name = "system-category-placements"
    menu_icon = "view"
    menu_order = 232
    permission = "placements.view_system_categoryplacement"
    title = admin_text("placements.system_categories")
    description = admin_text("placements.system_categories.description")

    def has_access(self, request) -> bool:
        return is_super_admin(request.user)

    def index_view(self, request):
        if not self.has_access(request):
            raise PermissionDenied
        if request.method == "POST":
            if not is_super_admin(request.user):
                raise PermissionDenied
            article_id = request.POST.get("article_id")
            article = get_object_or_404(ArticlePage, pk=article_id)
            result = sync_category_placements(
                article_id=article.pk,
                actor=request.user,
                request_id=request.POST.get("request_id") or None,
            )
            messages.success(
                request,
                f"Article #{article.pk} synchronization completed: "
                f"created {result.get('created', 0)}, enabled {result.get('enabled', 0)}, "
                f"disabled {result.get('disabled', 0)}.",
            )
            return redirect("system-category-placements:index")

        queryset = ArticlePlacement.objects.filter(
            source=ArticlePlacement.Source.SYSTEM,
            placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
            target_type=ArticlePlacement.TargetType.CATEGORY,
        )
        if request.GET.get("errors") == "1":
            queryset = queryset.filter(article__placement_sync_status="failed")
        placements = list(
            queryset.select_related(
                "article",
                "article__live_revision",
                "target_category",
                "target_category__journal",
            ).order_by("article_id", "target_category__path_cache", "pk")[:500]
        )
        return render(
            request,
            "placements/admin/system_category_placements.html",
            {
                "title": self.title,
                "placements": placements,
                "can_retry": is_super_admin(request.user),
            },
        )

    def get_urlpatterns(self):
        return [path("", self.index_view, name="index")]
