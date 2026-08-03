from django.db.models import Q
from modelcluster.forms import BaseChildFormSet
from wagtail.admin.forms import WagtailAdminPageForm
from wagtail.admin.panels import InlinePanel

from .editor_services import validate_raw_html_permission
from .integrations import get_active_journal_queryset


class ArticleCategoryAssignmentFormSet(BaseChildFormSet):
    """Keep category inline saves idempotent when a restored draft loses child IDs."""

    def save(self, commit=True):
        self._ensure_single_assignment_is_primary()
        self._reuse_existing_assignments_by_category()
        return super().save(commit=commit)

    def _ensure_single_assignment_is_primary(self):
        deleted_forms = set(self.deleted_forms)
        assignment_forms = [
            form
            for form in self.forms
            if form not in deleted_forms
            and not form.errors
            and form.cleaned_data.get("category") is not None
        ]
        if len(assignment_forms) != 1:
            return

        form = assignment_forms[0]
        if form.cleaned_data.get("is_primary"):
            return

        form.cleaned_data["is_primary"] = True
        form.instance.is_primary = True
        if "is_primary" not in form.changed_data:
            form.changed_data.append("is_primary")

    def _reuse_existing_assignments_by_category(self):
        # A newly created page has no primary key until Wagtail saves the page
        # row; there cannot be existing assignments to reconcile at this stage.
        if getattr(self.instance, "pk", None) is None:
            return

        manager = getattr(self.instance, self.rel_name)
        get_live_queryset = getattr(manager, "get_live_queryset", None)
        if get_live_queryset is None:
            return

        existing_by_category = {
            assignment.category_id: assignment for assignment in get_live_queryset()
        }
        represented_ids = {
            form.instance.pk for form in self.forms if form.instance.pk is not None
        }
        deleted_forms = set(self.deleted_forms)

        for form in self.forms:
            if form in deleted_forms or form.errors or form.instance.pk is not None:
                continue
            category = form.cleaned_data.get("category")
            if category is None:
                continue
            existing = existing_by_category.get(category.pk)
            if existing is None or existing.pk in represented_ids:
                continue

            # Local draft recovery intentionally does not restore hidden inline IDs.
            # Reconnect a submitted row to its existing assignment instead of trying
            # to INSERT the same article/category pair again.
            form.instance.pk = existing.pk
            form.instance._state.adding = False
            form.instance._state.db = existing._state.db
            represented_ids.add(existing.pk)


class ArticleCategoryAssignmentInlinePanel(InlinePanel):
    def get_form_options(self):
        options = super().get_form_options()
        options["formsets"][self.relation_name][
            "formset"
        ] = ArticleCategoryAssignmentFormSet
        return options


class ArticlePageForm(WagtailAdminPageForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._set_default_body_initial()
        self._limit_journal_fields_to_active_journals()

    def clean_body(self):
        body = self.cleaned_data["body"]
        instance = getattr(self, "instance", None)
        original_body = (
            getattr(instance, "body", None)
            if instance is not None and getattr(instance, "pk", None)
            else None
        )
        validate_raw_html_permission(
            user=getattr(self, "for_user", None),
            body=body,
            original_body=original_body,
        )
        return body

    def _set_default_body_initial(self):
        if self.is_bound:
            return

        instance = getattr(self, "instance", None)
        if instance is not None and getattr(instance, "pk", None):
            return
        if self.initial.get("body"):
            return

        body_field = self.fields.get("body")
        if body_field is None:
            return
        self.initial["body"] = body_field.block.to_python([("paragraph", "")])

    def _limit_journal_fields_to_active_journals(self):
        primary_journal_field = self.fields.get("primary_journal")
        if primary_journal_field is None:
            return

        active_journals = get_active_journal_queryset(
            journal_model=primary_journal_field.queryset.model,
        )
        if active_journals is None:
            return

        active_journals = self._include_current_journals(active_journals)
        primary_journal_field.queryset = active_journals

        related_journals_field = self.fields.get("related_journals")
        if related_journals_field is not None:
            related_journals_field.queryset = active_journals

    def _include_current_journals(self, active_journals):
        if not hasattr(active_journals, "all"):
            return active_journals

        active_journals = active_journals.all()
        selected_ids = self._get_selected_journal_ids()
        if not selected_ids:
            return active_journals

        return active_journals.model.objects.filter(
            Q(pk__in=active_journals.values("pk")) | Q(pk__in=selected_ids)
        )

    def _get_selected_journal_ids(self):
        instance = getattr(self, "instance", None)
        if instance is None:
            return []

        selected_ids = []
        primary_journal_id = getattr(instance, "primary_journal_id", None)
        if primary_journal_id:
            selected_ids.append(primary_journal_id)

        if getattr(instance, "pk", None):
            try:
                selected_ids.extend(
                    instance.related_journals.values_list("pk", flat=True)
                )
            except ValueError:
                pass

        return selected_ids
