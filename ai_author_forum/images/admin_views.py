from django.shortcuts import redirect
from wagtail.admin import messages
from wagtail.images.views.bulk_actions.delete import DeleteBulkAction
from wagtail.images.views.images import DeleteView

from .references import get_image_references


class ProtectedImageDeleteView(DeleteView):
    template_name = "images/confirm_delete.html"

    def get_custom_references(self):
        if not hasattr(self, "_custom_references"):
            self._custom_references = get_image_references(self.object)
        return self._custom_references

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        references = self.get_custom_references()
        context["custom_references"] = references
        context["usage_count"] = context.get("usage_count", 0) + len(references)
        context["is_protected"] = context.get("is_protected", False) or bool(references)
        return context

    def form_valid(self, form):
        references = self.get_custom_references()
        if references:
            messages.error(
                self.request,
                "This image is still referenced and cannot be deleted. "
                "Remove every listed reference first.",
            )
            return self.render_to_response(self.get_context_data(form=form), status=409)
        return super().form_valid(form)


class ProtectedImageDeleteBulkAction(DeleteBulkAction):
    template_name = "images/confirm_bulk_delete.html"

    def annotate_items(self, items):
        items = super().annotate_items(items)
        self.has_custom_protection = False
        for item in items:
            item._custom_image_references = get_image_references(item)
            if item._custom_image_references:
                self.has_custom_protection = True
                self.is_protected = True
        return items

    def object_context(self, item):
        context = super().object_context(item)
        references = getattr(item, "_custom_image_references", ())
        context["custom_references"] = references
        context["usage_count"] += len(references)
        context["is_protected"] = context["is_protected"] or bool(references)
        return context

    def prepare_action(self, objects, objects_without_access):
        if self.has_custom_protection:
            messages.error(
                self.request,
                "One or more selected images are still referenced and were not deleted.",
            )
            return redirect(self.next_url)
        return super().prepare_action(objects, objects_without_access)
