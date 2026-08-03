from django.urls import reverse
from wagtail.admin.panels import Panel


class PreviewButton(Panel):
    class BoundPanel(Panel.BoundPanel):
        template_name = "wagtailadmin/articles/preview_button_panel.html"

        def is_shown(self):
            return bool(
                self.instance and self.instance.pk and self.instance.is_previewable()
            )

        def get_context_data(self, parent_context=None):
            context = super().get_context_data(parent_context)
            context["preview_url"] = reverse(
                "wagtailadmin_pages:preview_on_edit",
                args=[self.instance.pk],
            )
            return context
