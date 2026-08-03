from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.text import get_valid_filename

STATIC_SITE_ROOT_SETTING = "AI_AUTHOR_FORUM_STATIC_SITE_ROOT"
IMPORT_QUEUE_ROOT_SETTING = "AI_AUTHOR_FORUM_IMPORT_QUEUE_ROOT"


def get_static_site_root() -> Path:
    configured = getattr(
        settings,
        STATIC_SITE_ROOT_SETTING,
        Path(settings.BASE_DIR) / "output" / "static-site",
    )
    return Path(configured).resolve()


def get_import_queue_root() -> Path:
    configured = getattr(
        settings,
        IMPORT_QUEUE_ROOT_SETTING,
        Path(settings.BASE_DIR) / "output" / "import-queue",
    )
    return Path(configured).resolve()


def _ensure_within_root(path: Path, root: Path, message: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValidationError(message) from exc
    return resolved


def resolve_static_output_dir(value: str | os.PathLike | None) -> Path:
    root = get_static_site_root()
    raw_value = str(value or "").strip()
    candidate = Path(raw_value) if raw_value else root
    if not candidate.is_absolute():
        candidate = root / candidate
    return _ensure_within_root(
        candidate,
        root,
        "Static output directory must stay inside the configured static site root.",
    )


def resolve_static_file_path(output_dir: Path, static_path: str) -> Path:
    cleaned = str(static_path or "").strip().lstrip("/\\") or "index.html"
    relative_path = Path(cleaned)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValidationError(f"Unsafe static output path: {static_path}")
    return _ensure_within_root(
        output_dir / relative_path,
        output_dir,
        f"Unsafe static output path: {static_path}",
    )


def save_import_package_for_background(uploaded_file) -> Path:
    queue_root = get_import_queue_root()
    queue_root.mkdir(parents=True, exist_ok=True)
    original_name = get_valid_filename(
        Path(uploaded_file.name or "journal-import-package.zip").name
    )
    target_path = queue_root / f"{uuid.uuid4().hex}-{original_name}"
    with target_path.open("wb") as target:
        for chunk in uploaded_file.chunks():
            target.write(chunk)
    return _ensure_within_root(
        target_path,
        queue_root,
        "Import package must stay inside the configured import queue root.",
    )


def start_import_publish_process(
    *,
    package_path: Path,
    dry_run: bool,
    publish_static_site: bool,
    operator_id: int | None,
    preview_journal_job_id: int | None = None,
    preview_article_job_id: int | None = None,
    allow_suspicious_text: bool = False,
    override_reason: str = "",
    csv_encoding: str = "auto",
) -> subprocess.Popen:
    manage_py = Path(settings.BASE_DIR) / "manage.py"
    args = [
        sys.executable,
        str(manage_py),
        "import_journal_package",
        "--package",
        str(package_path),
    ]
    if operator_id:
        args.extend(["--operator-id", str(operator_id)])
    if preview_journal_job_id:
        args.extend(["--preview-journal-job-id", str(preview_journal_job_id)])
    if preview_article_job_id:
        args.extend(["--preview-article-job-id", str(preview_article_job_id)])
    args.extend(["--csv-encoding", csv_encoding])
    if allow_suspicious_text:
        args.append("--allow-suspicious-text")
        args.extend(["--override-reason", override_reason])
    if dry_run:
        args.append("--dry-run")
    elif publish_static_site:
        args.append("--publish-static-site")

    popen_kwargs = {
        "cwd": str(settings.BASE_DIR),
        "env": os.environ.copy(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": os.name != "nt",
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    return subprocess.Popen(args, **popen_kwargs)


def start_article_import_preview_process(
    *,
    package_path: Path,
    job_id: int,
    operator_id: int | None,
) -> subprocess.Popen:
    """Start the locked article preview command without a shell or visible window."""
    queue_root = get_import_queue_root()
    safe_package = _ensure_within_root(
        Path(package_path),
        queue_root,
        "Article preview package must stay inside the configured import queue root.",
    )
    if not safe_package.is_file():
        raise ValidationError("Article preview package does not exist.")

    manage_py = Path(settings.BASE_DIR) / "manage.py"
    args = [
        sys.executable,
        str(manage_py),
        "preview_article_package",
        "--package",
        str(safe_package),
        "--job-id",
        str(job_id),
    ]
    if operator_id:
        args.extend(["--operator-id", str(operator_id)])

    popen_kwargs = {
        "cwd": str(settings.BASE_DIR),
        "env": os.environ.copy(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": os.name != "nt",
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(args, **popen_kwargs)


def start_article_import_process(
    *,
    package_path: Path,
    operator_id: int | None,
    preview_job_id: int,
) -> subprocess.Popen:
    """Start execution from immutable job state without command-line overrides."""
    queue_root = get_import_queue_root()
    safe_package = _ensure_within_root(
        Path(package_path),
        queue_root,
        "Article import package must stay inside the configured import queue root.",
    )
    if not safe_package.is_file():
        raise ValidationError("Article import package does not exist.")
    manage_py = Path(settings.BASE_DIR) / "manage.py"
    args = [
        sys.executable,
        str(manage_py),
        "import_article_package",
        "--package",
        str(safe_package),
        "--preview-job-id",
        str(preview_job_id),
    ]
    if operator_id:
        args.extend(["--operator-id", str(operator_id)])

    popen_kwargs = {
        "cwd": str(settings.BASE_DIR),
        "env": os.environ.copy(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": os.name != "nt",
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(args, **popen_kwargs)
