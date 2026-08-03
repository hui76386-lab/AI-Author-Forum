from wagtail import hooks

from .viewsets import (
    HomepageCompositionViewSet,
    PlacementsViewSet,
    SlotsViewSet,
    SystemCategoryPlacementsViewSet,
)


@hooks.register("register_admin_viewset")
def register_placement_viewsets():
    return [
        HomepageCompositionViewSet(),
        PlacementsViewSet(),
        SystemCategoryPlacementsViewSet(),
        SlotsViewSet(),
    ]
