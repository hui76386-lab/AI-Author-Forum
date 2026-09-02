from django.urls import path

from . import api

urlpatterns = [
    path("session/", api.session_view, name="reader_session"),
    path(
        "email-verifications/",
        api.request_verification_view,
        name="reader_email_verification_request",
    ),
    path(
        "email-verifications/<uuid:challenge_id>/",
        api.verify_email_view,
        name="reader_email_verification_confirm",
    ),
    path(
        "email-verifications/<uuid:challenge_id>/consume/",
        api.consume_verification_view,
        name="reader_email_verification_consume",
    ),
    path(
        "device-flows/<uuid:flow_id>/status/",
        api.device_flow_status_view,
        name="reader_device_flow_status",
    ),
    path(
        "device-flows/<uuid:flow_id>/claim/",
        api.device_flow_claim_view,
        name="reader_device_flow_claim",
    ),
    path(
        "device-flows/<uuid:flow_id>/cancel/",
        api.device_flow_cancel_view,
        name="reader_device_flow_cancel",
    ),
    path("verify-email/", api.verify_email_view, name="reader_verify_email"),
    path("session/profile/", api.profile_view, name="reader_session_profile"),
    path("session/logout/", api.logout_view, name="reader_session_logout"),
    path(
        "articles/<uuid:article_public_id>/capabilities/",
        api.capabilities_view,
        name="reader_article_capabilities",
    ),
    path(
        "articles/<uuid:article_public_id>/comments/",
        api.comments_view,
        name="reader_article_comments",
    ),
    path(
        "articles/<uuid:article_public_id>/comments/<uuid:comment_public_id>/replies/",
        api.comment_reply_view,
        name="reader_comment_reply",
    ),
    path(
        "articles/<uuid:article_public_id>/comments/<uuid:comment_public_id>/withdrawal/",
        api.comment_withdrawal_view,
        name="reader_comment_withdrawal",
    ),
    path(
        "articles/<uuid:article_public_id>/comments/<uuid:comment_public_id>/reports/",
        api.comment_report_view,
        name="reader_comment_report",
    ),
    path(
        "articles/<uuid:article_public_id>/download-grants/",
        api.download_grant_view,
        name="reader_download_grant",
    ),
    path(
        "articles/<uuid:article_public_id>/share-events/",
        api.share_event_view,
        name="reader_share_event",
    ),
    path(
        "downloads/<uuid:grant_public_id>/<str:token>/",
        api.download_view,
        name="reader_download",
    ),
]
