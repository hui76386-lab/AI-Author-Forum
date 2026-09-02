from pathlib import Path

import yaml
from django.conf import settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_reader_feature_flags_default_to_fail_closed():
    assert settings.READER_INTERACTIONS_ENABLED is False
    assert settings.READER_EMAIL_VERIFICATION_ENABLED is False
    assert settings.READER_COMMENTS_WRITE_ENABLED is False
    assert settings.READER_PDF_GRANTS_ENABLED is False
    assert settings.READER_SHARE_UI_ENABLED is False
    assert settings.READER_SNAPSHOT_READ_FALLBACK is False


def test_reader_databases_are_distinct_and_router_is_registered():
    assert (
        settings.DATABASES["default"]["NAME"]
        != settings.DATABASES["interactions"]["NAME"]
    )
    assert settings.DATABASE_ROUTERS == [
        "ai_author_forum.reader_interactions.routers.ReaderInteractionsRouter"
    ]


def test_reader_and_static_publish_tasks_have_isolated_routes():
    assert settings.CELERY_TASK_ROUTES == {
        "ai_author_forum.static_publish.tasks.*": {"queue": "static_publish"},
        "ai_author_forum.reader_interactions.tasks.send_magic_link": {
            "queue": "reader_email"
        },
        "ai_author_forum.reader_interactions.tasks.cleanup_reader_security_records": {
            "queue": "reader_email"
        },
        "ai_author_forum.reader_interactions.tasks.refresh_comment_snapshot": {
            "queue": "reader_comments"
        },
        "ai_author_forum.reader_access.tasks.apply_capability_projection": {
            "queue": "reader_comments"
        },
        "ai_author_forum.reader_access.tasks.reconcile_capability_projections": {
            "queue": "reader_comments"
        },
        "ai_author_forum.reader_access.tasks.apply_moderation_command": {
            "queue": "reader_comments"
        },
        "ai_author_forum.reader_access.tasks.reconcile_moderation_commands": {
            "queue": "reader_comments"
        },
        "ai_author_forum.reader_access.tasks.render_pdf": {"queue": "reader_pdf"},
    }


def test_production_web_never_runs_release_mutations_on_startup():
    compose = yaml.safe_load(
        (PROJECT_ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    web_command = services["web"]["command"]
    release_command = services["release"]["command"]

    assert "gunicorn" in web_command
    assert "migrate" not in web_command
    assert "collectstatic" not in web_command
    assert "createcachetable" not in web_command
    assert services["release"]["profiles"] == ["release"]
    assert "migrate --database=default --noinput" in release_command
    assert "migrate --database=interactions --noinput" in release_command
    assert "INTERACTIONS_DATABASE_URL" in services["web"]["environment"]
    assert "collectstatic --noinput" in release_command
    assert "createcachetable" in release_command
    assert services["web"]["deploy"]["replicas"] == "${READER_API_REPLICAS:-2}"
    assert "--workers ${READER_API_GUNICORN_WORKERS:-2}" in web_command
    assert services["web"]["cpus"] == 1.0
    assert services["web"]["mem_limit"] == "1g"
    assert services["web"]["pids_limit"] == 256


def test_static_publish_worker_consumes_only_its_named_queue():
    compose = yaml.safe_load(
        (PROJECT_ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    )

    assert "--queues=static_publish" in compose["services"]["worker"]["command"]


def test_reader_email_worker_consumes_only_its_named_queue():
    compose = yaml.safe_load(
        (PROJECT_ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    )

    command = compose["services"]["reader-email-worker"]["command"]
    assert "--queues=reader_email" in command
    assert "static_publish" not in command
    assert "reader_comments" not in command
    assert "reader_pdf" not in command


def test_reader_comments_worker_consumes_only_its_named_queue():
    compose = yaml.safe_load(
        (PROJECT_ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    )

    command = compose["services"]["reader-comments-worker"]["command"]
    assert "--queues=reader_comments" in command
    assert "static_publish" not in command
    assert "reader_email" not in command
    assert "reader_pdf" not in command


def test_reader_pdf_worker_is_dedicated_and_resource_bounded():
    compose = yaml.safe_load(
        (PROJECT_ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    )
    worker = compose["services"]["reader-pdf-worker"]

    assert worker["build"]["dockerfile"] == "docker/pdf-worker/Dockerfile"
    assert "--queues=reader_pdf" in worker["command"]
    assert "reader_comments" not in worker["command"]
    assert "reader_email" not in worker["command"]
    assert worker["cpus"] == 1.0
    assert worker["mem_limit"] == "1g"
    assert worker["pids_limit"] == 256
    assert "protected-pdf-data:/data/protected-pdfs" in worker["volumes"]


def test_pdf_worker_image_and_python_packages_are_immutable_and_isolated():
    dockerfile = (PROJECT_ROOT / "docker/pdf-worker/Dockerfile").read_text(
        encoding="utf-8"
    )
    requirements = (PROJECT_ROOT / "requirements-pdf.txt").read_text(encoding="utf-8")

    assert "mcr.microsoft.com/playwright/python:v1.61.0-noble@sha256:" in dockerfile
    assert "docker/pdf-worker/entrypoint.sh" in dockerfile
    assert "--queues=reader_pdf" in dockerfile
    assert "playwright==1.61.0" in requirements
    assert "pypdf==6.1.3" in requirements


def test_container_build_declares_both_database_placeholders():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "DATABASE_URL=postgresql://build:build@database:5432/build" in dockerfile
    assert (
        "INTERACTIONS_DATABASE_URL="
        "postgresql://build:build@interactions-database:5432/build"
    ) in dockerfile
