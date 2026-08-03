from django.urls import path

from . import views

app_name = "static_publish"

urlpatterns = [
    path("", views.publish_center, name="center"),
    path("jobs/<int:job_id>/", views.job_detail, name="job_detail"),
    path("jobs/<int:job_id>/status/", views.job_status, name="job_status"),
    path(
        "jobs/<int:job_id>/approve/",
        views.approve_pending_job,
        name="approve_pending_job",
    ),
    path("jobs/<int:job_id>/retry/", views.retry_job, name="retry_job"),
    path("rollback/preview/", views.rollback_preview, name="rollback_preview"),
    path("rollback/", views.rollback_release, name="rollback"),
]
