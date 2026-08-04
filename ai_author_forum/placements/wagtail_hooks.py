from wagtail import hooks

from .viewsets import (
    HomepageCompositionViewSet,
    PlacementsViewSet,
    SlotsViewSet,
    SystemCategoryPlacementsViewSet,
)
from .workflow_viewset import PlacementsWorkflowV2ViewSet


@hooks.register("register_admin_viewset")
def register_placement_viewsets():
    return [
        HomepageCompositionViewSet(),
        PlacementsWorkflowV2ViewSet(),
        PlacementsViewSet(),
        SystemCategoryPlacementsViewSet(),
        SlotsViewSet(),
    ]
