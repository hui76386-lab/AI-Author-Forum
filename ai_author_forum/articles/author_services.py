from __future__ import annotations

import hashlib
import json
import os
import zipfile
from functools import wraps
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify
from wagtail.models import Collection, Page, WorkflowPage
from wagtail.whitelist import Whitelister

from ai_author_forum.journals.models import (
    Journal,
    JournalCategoryStatus,
    JournalEditorAssignment,
)
from ai_author_forum.journals.submission_services import (
    journal_accepts_author_submission,
)
from ai_author_forum.site_settings.access_control import (
    can_create_submission,
    can_edit_submission,
    can_submit_submission,
    get_journal_editor_assignment,
    is_super_admin,
)
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.users.services import revoke_user_sessions

from .category_services import validate_article_category_revision
from .models import (
    ArticleAuthorship,
    ArticleCategoryAssignment,
    ArticleContributor,
    ArticlePage,
    ArticleReviewRecord,
    ArticleRevisionConflict,
    AuthorSubmissionAsset,
    AuthorSubmissionOperation,
)
from .review_services import ArticleStateConflict, submit_article_for_initial_review

AUTHOR_EDITABLE_FIELDS = frozenset(
    {
        "title",
        "abstract",
        "body",
        "keywords",
        "responsibility_statement",
        "ai_co_authors",
        "ai_contribution_statement",
        "article_type",
        "featured_image",
        "featured_image_alt",
    }
)


def request_uuid(value) -> UUID:
    if not value:
        raise ValidationError({"request_id": "写操作必须提供 request id。"})
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"request_id": "request id 必须是有效 UUID。"}) from exc


def revision_sha256(revision) -> str:
    encoded = json.dumps(
        revision.content,
        cls=DjangoJSONEncoder,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _existing_operation(*, request_id, action, actor, article=None):
    operation = (
        AuthorSubmissionOperation.objects.select_related(
            "article", "authorship", "revision"
        )
        .filter(request_id=request_id)
        .first()
    )
    if operation is None:
        return None
    if operation.action != action or operation.actor_id != actor.pk:
        raise ArticleStateConflict("该 request id 已用于其他作者投稿操作。")
    if article is not None and operation.article_id != article.pk:
        raise ArticleStateConflict("该 request id 已用于其他文章。")
    return operation


def _record_operation(
    *,
    request_id,
    action,
    actor,
    article=None,
    authorship=None,
    revision=None,
    result=None,
):
    return AuthorSubmissionOperation.objects.create(
        request_id=request_id,
        action=action,
        actor=actor,
        article=article,
        authorship=authorship,
        revision=revision,
        result=result or {},
    )


def _audit(
    *, actor, article=None, authorship=None, request_id, message, status, metadata=None
):
    payload = {
        "operation_source": "author_workbench",
        "article_id": getattr(article, "pk", None),
        "journal_id": getattr(article, "primary_journal_id", None),
        "authorship_id": getattr(authorship, "pk", None),
        **(metadata or {}),
    }
    return AuditLog.record(
        action=AuditAction.PERMISSION,
        status=status,
        actor=actor,
        target=article or authorship,
        target_type="ArticlePage" if article is not None else "ArticleAuthorship",
        target_id=str(getattr(article or authorship, "pk", "")),
        message=message,
        request_id=str(request_id),
        metadata=payload,
    )


def audit_submission_failures(*, message, operation_source):
    """Persist denied/conflicting writes after their business transaction rolls back."""

    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except (PermissionDenied, ValidationError) as exc:
                article = kwargs.get("article")
                authorship = kwargs.get("authorship")
                if article is None and authorship is not None:
                    article = getattr(authorship, "article", None)
                try:
                    _audit(
                        actor=kwargs.get("actor"),
                        article=article,
                        authorship=authorship,
                        request_id=kwargs.get("request_id") or "",
                        message=message,
                        status=AuditStatus.FAILURE,
                        metadata={
                            "operation_source": operation_source,
                            "result": "failure",
                            "error_type": type(exc).__name__,
                            "old_state": getattr(article, "review_status", None),
                        },
                    )
                except Exception:
                    # An audit storage failure must not replace the original business error.
                    pass
                raise

        return wrapped

    return decorator


def _latest_revision_or_conflict(article, expected_revision_id):
    revision = article.get_latest_revision()
    if revision is None or str(revision.pk) != str(expected_revision_id or ""):
        raise ArticleRevisionConflict("投稿已产生新 revision，请重新加载后再保存。")
    return revision


def _validate_category(category, journal):
    if category is None:
        raise ValidationError({"category": "必须选择一个期刊栏目。"})
    if category.journal_id != journal.pk:
        raise ValidationError({"category": "所选栏目不属于目标期刊。"})
    if category.status != JournalCategoryStatus.ACTIVE:
        raise ValidationError({"category": "作者只能选择启用中的公开栏目。"})
    return category


def _replace_primary_category(article, category):
    article.category_assignments.all().delete()
    if category is not None:
        ArticleCategoryAssignment.objects.create(
            article=article,
            category=category,
            is_primary=True,
            sort_order=0,
        )


def _replace_contributors(article, contributors):
    rows = []
    for row in contributors or []:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "name": name,
                "affiliation": str(row.get("affiliation") or "").strip(),
                "is_corresponding": bool(row.get("is_corresponding")),
            }
        )
    if not rows:
        raise ValidationError({"contributors": "至少需要一名公开作者。"})
    if sum(1 for row in rows if row["is_corresponding"]) != 1:
        raise ValidationError({"contributors": "必须且只能指定一名通讯作者。"})
    article.contributors.all().delete()
    for index, row in enumerate(rows):
        contributor = ArticleContributor(
            article=article,
            identity=ArticleContributor.Identity.AUTHOR,
            name=row["name"],
            affiliation=row["affiliation"],
            is_corresponding=row["is_corresponding"],
            sort_order=index,
        )
        contributor.full_clean()
        contributor.save()
    article.refresh_from_db(fields=["authors"])


def _upload_sha256(uploaded_file):
    digest = hashlib.sha256()
    uploaded_file.seek(0)
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


def _author_asset_collection(article):
    root_collection = Collection.get_first_root_node()
    collection_name = f"Author submission {article.pk}"
    collection = root_collection.get_children().filter(name=collection_name).first()
    if collection is None:
        collection = root_collection.add_child(name=collection_name)
    return collection


def _validate_author_image_upload(uploaded_file, *, field_name):
    content_type = str(getattr(uploaded_file, "content_type", "") or "").lower()
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValidationError({field_name: "图片仅支持 JPEG、PNG 或 WebP。"})
    size = int(getattr(uploaded_file, "size", 0) or 0)
    if size <= 0 or size > 8 * 1024 * 1024:
        raise ValidationError({field_name: "图片大小必须在 1 字节到 8 MiB 之间。"})
    from PIL import Image, UnidentifiedImageError

    expected_formats = {
        "image/jpeg": "JPEG",
        "image/png": "PNG",
        "image/webp": "WEBP",
    }
    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as decoded:
            decoded.verify()
            actual_format = decoded.format
    except (OSError, UnidentifiedImageError) as exc:
        raise ValidationError({field_name: "图片文件不是有效图片。"}) from exc
    finally:
        uploaded_file.seek(0)
    if actual_format != expected_formats[content_type]:
        raise ValidationError({field_name: "图片文件内容与声明类型不一致。"})
    return content_type, size, _upload_sha256(uploaded_file)


def _store_author_inline_image(*, article, authorship, actor, uploaded_file):
    from ai_author_forum.images.models import CustomImage

    content_type, size, content_sha256 = _validate_author_image_upload(
        uploaded_file, field_name="body"
    )
    image = CustomImage(
        title=f"{article.title} - author body image",
        file=uploaded_file,
        collection=_author_asset_collection(article),
        uploaded_by_user=actor,
    )
    image.full_clean()
    image.save()
    return AuthorSubmissionAsset.objects.create(
        article=article,
        authorship=authorship,
        uploaded_by=actor,
        kind=AuthorSubmissionAsset.Kind.INLINE_IMAGE,
        image=image,
        original_name=str(uploaded_file.name)[:255],
        content_type=content_type,
        size=size,
        sha256=content_sha256,
        scan_status=AuthorSubmissionAsset.ScanStatus.CLEAN,
        scan_detail="signature, decoder and size validation passed",
    )


_AUTHOR_DOCUMENT_TYPES = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/octet-stream",
    },
    ".csv": {"text/csv", "text/plain", "application/csv"},
    ".txt": {"text/plain"},
}


def _validate_author_document_upload(uploaded_file):
    original_name = str(getattr(uploaded_file, "name", "") or "")
    extension = os.path.splitext(original_name)[1].lower()
    content_type = str(getattr(uploaded_file, "content_type", "") or "").lower()
    if extension not in _AUTHOR_DOCUMENT_TYPES:
        raise ValidationError({"body": "附件仅支持 PDF、DOC、DOCX、XLSX、CSV 或 TXT。"})
    if content_type not in _AUTHOR_DOCUMENT_TYPES[extension]:
        raise ValidationError({"body": "附件内容类型与文件扩展名不匹配。"})
    size = int(getattr(uploaded_file, "size", 0) or 0)
    if size <= 0 or size > 20 * 1024 * 1024:
        raise ValidationError({"body": "附件大小必须在 1 字节到 20 MiB 之间。"})
    try:
        uploaded_file.seek(0)
        prefix = uploaded_file.read(16)
        uploaded_file.seek(0)
        if extension == ".pdf" and not prefix.startswith(b"%PDF-"):
            raise ValidationError({"body": "PDF 附件签名无效。"})
        if extension == ".doc" and not prefix.startswith(
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        ):
            raise ValidationError({"body": "DOC 附件签名无效。"})
        if extension in {".docx", ".xlsx"} and not zipfile.is_zipfile(uploaded_file):
            raise ValidationError({"body": "Office 附件签名无效。"})
        if extension in {".csv", ".txt"}:
            uploaded_file.seek(0)
            sample = uploaded_file.read(32 * 1024)
            if b"\x00" in sample:
                raise ValidationError({"body": "文本附件不能包含二进制内容。"})
            sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError({"body": "文本附件必须使用 UTF-8 编码。"}) from exc
    finally:
        uploaded_file.seek(0)
    return content_type, size, _upload_sha256(uploaded_file)


def _store_author_document(*, article, authorship, actor, uploaded_file):
    from wagtail.documents import get_document_model

    content_type, size, content_sha256 = _validate_author_document_upload(uploaded_file)
    document = get_document_model()(
        title=str(uploaded_file.name)[:255],
        file=uploaded_file,
        collection=_author_asset_collection(article),
        uploaded_by_user=actor,
    )
    document.full_clean()
    document.save()
    return AuthorSubmissionAsset.objects.create(
        article=article,
        authorship=authorship,
        uploaded_by=actor,
        kind=AuthorSubmissionAsset.Kind.ATTACHMENT,
        document=document,
        original_name=str(uploaded_file.name)[:255],
        content_type=content_type,
        size=size,
        sha256=content_sha256,
        scan_status=AuthorSubmissionAsset.ScanStatus.CLEAN,
        scan_detail="signature and file policy validation passed",
    )


def _current_body_resource_ids(article, *, block_type, value_key):
    ids = set()
    body = getattr(article, "body", None)
    raw_blocks = (
        body.get_prep_value() if hasattr(body, "get_prep_value") else body or []
    )
    for block in raw_blocks:
        if not isinstance(block, dict) or block.get("type") != block_type:
            continue
        value = block.get("value") or {}
        if isinstance(value, dict) and value.get(value_key):
            ids.add(int(value[value_key]))
    return ids


def _legacy_author_body(body):
    """Compatibility path for existing internal callers using tuple values.

    Browser submissions use the structured path below. This narrow adapter keeps
    established service callers working while still refusing Raw HTML and direct
    media-library references.
    """

    materialized = []
    for block in body or []:
        try:
            block_type, value = block
        except (TypeError, ValueError) as exc:
            raise ValidationError({"body": "正文内容格式无效。"}) from exc
        if block_type == "paragraph":
            cleaned = Whitelister().clean(str(value or ""))
            if not cleaned.strip():
                raise ValidationError({"body": "正文段落不能为空。"})
            materialized.append(("paragraph", cleaned))
        elif block_type == "heading":
            text = str(value or "").strip()
            if not text or len(text) > 160:
                raise ValidationError({"body": "章节标题无效。"})
            materialized.append(("heading", text))
        elif block_type == "quote" and isinstance(value, dict):
            quote = str(value.get("quote") or "").strip()
            if not quote:
                raise ValidationError({"body": "引用内容不能为空。"})
            materialized.append(
                (
                    "quote",
                    {
                        "quote": quote,
                        "attribution": str(value.get("attribution") or "")[:255],
                    },
                )
            )
        elif block_type == "list" and isinstance(value, dict):
            items = [
                Whitelister().clean(str(item or "")) for item in value.get("items", [])
            ]
            if not items or any(not item.strip() for item in items):
                raise ValidationError({"body": "列表至少需要一个非空列表项。"})
            materialized.append(
                (
                    "list",
                    {
                        "list_type": (
                            "ordered"
                            if value.get("list_type") == "ordered"
                            else "unordered"
                        ),
                        "items": items,
                    },
                )
            )
        elif block_type == "table" and isinstance(value, dict):
            materialized.append(("table", value))
        elif block_type in {"image", "document", "html"}:
            raise ValidationError(
                {"body": "作者图片、附件和正文只能通过受控投稿编辑器保存。"}
            )
        else:
            raise ValidationError({"body": "正文包含不支持的内容块。"})
    if not materialized:
        raise ValidationError({"body": "请至少添加一个正文内容块。"})
    return materialized


def _author_body_asset(
    *, article, asset_id, kind, actor, current_resource_ids, resource_field
):
    resource_lookup = {f"{resource_field}_id": asset_id}
    asset = (
        AuthorSubmissionAsset.objects.select_related("image", "document")
        .filter(
            article=article,
            kind=kind,
            is_active=True,
            scan_status=AuthorSubmissionAsset.ScanStatus.CLEAN,
            **resource_lookup,
        )
        .first()
    )
    resource_id = getattr(asset, f"{resource_field}_id", None) if asset else None
    if asset is not None and resource_id:
        return asset
    # Existing content may predate the author asset table. An editor may have
    # inserted it for this same draft; retaining that reference is safe, but a
    # foreign image/document ID is never accepted.
    if asset_id in current_resource_ids:
        return None
    raise ValidationError({"body": "正文资源不属于当前投稿，无法使用。"})


def _materialize_author_body(*, article, authorship, actor, body, body_uploads):
    """Resolve uploaded/scoped resources into the ArticlePage StreamField value."""

    body_uploads = body_uploads or {}
    if body and isinstance(body[0], (tuple, list)):
        return _legacy_author_body(body)
    current_image_ids = _current_body_resource_ids(
        article, block_type="image", value_key="image"
    )
    current_document_ids = _current_body_resource_ids(
        article, block_type="document", value_key="document"
    )
    used_asset_ids = set()
    materialized = []
    for block in body or []:
        block_type = block.get("type") if isinstance(block, dict) else None
        if block_type == "paragraph":
            html = Whitelister().clean(str(block.get("html") or ""))
            if not strip_tags(html).strip():
                raise ValidationError({"body": "正文段落不能为空。"})
            materialized.append(("paragraph", html))
        elif block_type == "heading":
            text = str(block.get("text") or "").strip()
            if not text or len(text) > 160:
                raise ValidationError({"body": "章节标题无效。"})
            materialized.append(("heading", text))
        elif block_type == "quote":
            quote = str(block.get("quote") or "").strip()
            if not quote:
                raise ValidationError({"body": "引用内容不能为空。"})
            materialized.append(
                (
                    "quote",
                    {
                        "quote": quote,
                        "attribution": str(block.get("attribution") or "")[:255],
                    },
                )
            )
        elif block_type == "list":
            items = [
                Whitelister().clean(str(item or ""))
                for item in (block.get("items") or [])
            ]
            if not items or any(not strip_tags(item).strip() for item in items):
                raise ValidationError({"body": "列表至少需要一个非空列表项。"})
            materialized.append(
                (
                    "list",
                    {
                        "list_type": (
                            "ordered"
                            if block.get("list_type") == "ordered"
                            else "unordered"
                        ),
                        "items": items,
                    },
                )
            )
        elif block_type == "table":
            rows = block.get("data") or []
            if not isinstance(rows, list) or not rows:
                raise ValidationError({"body": "表格至少需要一行。"})
            clean_rows = []
            expected_columns = None
            for row in rows:
                if not isinstance(row, list) or not row:
                    raise ValidationError({"body": "表格行不能为空。"})
                if expected_columns is None:
                    expected_columns = len(row)
                elif expected_columns != len(row):
                    raise ValidationError({"body": "表格每行列数必须一致。"})
                clean_rows.append([strip_tags(str(cell or ""))[:1000] for cell in row])
            materialized.append(
                (
                    "table",
                    {
                        "data": clean_rows,
                        "first_row_is_table_header": bool(
                            block.get("first_row_is_table_header")
                        ),
                        "first_col_is_header": bool(block.get("first_col_is_header")),
                        "table_header_choice": (
                            "row"
                            if block.get("first_row_is_table_header")
                            else "column"
                        ),
                        "table_caption": str(block.get("caption") or "")[:500],
                    },
                )
            )
        elif block_type == "image":
            upload = body_uploads.get(block.get("upload_key"))
            if upload is not None:
                asset = _store_author_inline_image(
                    article=article,
                    authorship=authorship,
                    actor=actor,
                    uploaded_file=upload,
                )
            else:
                asset = _author_body_asset(
                    article=article,
                    asset_id=block.get("image_asset_id"),
                    kind=AuthorSubmissionAsset.Kind.INLINE_IMAGE,
                    actor=actor,
                    current_resource_ids=current_image_ids,
                    resource_field="image",
                )
            if asset is not None:
                image = asset.image
            else:
                from ai_author_forum.images.models import CustomImage

                try:
                    image = CustomImage.objects.get(pk=block["image_asset_id"])
                except CustomImage.DoesNotExist as exc:
                    raise ValidationError({"body": "正文图片不存在。"}) from exc
            if asset is not None:
                used_asset_ids.add(asset.pk)
            materialized.append(
                (
                    "image",
                    {
                        "image": image,
                        "alt_text": str(block.get("alt_text") or "")[:255],
                        "caption": str(block.get("caption") or "")[:255],
                    },
                )
            )
        elif block_type == "document":
            upload = body_uploads.get(block.get("upload_key"))
            if upload is not None:
                asset = _store_author_document(
                    article=article,
                    authorship=authorship,
                    actor=actor,
                    uploaded_file=upload,
                )
            else:
                asset = _author_body_asset(
                    article=article,
                    asset_id=block.get("document_asset_id"),
                    kind=AuthorSubmissionAsset.Kind.ATTACHMENT,
                    actor=actor,
                    current_resource_ids=current_document_ids,
                    resource_field="document",
                )
            if asset is not None:
                document = asset.document
            else:
                from wagtail.documents import get_document_model

                Document = get_document_model()
                try:
                    document = Document.objects.get(pk=block["document_asset_id"])
                except Document.DoesNotExist as exc:
                    raise ValidationError({"body": "正文附件不存在。"}) from exc
            if asset is not None:
                used_asset_ids.add(asset.pk)
            materialized.append(
                (
                    "document",
                    {
                        "document": document,
                        "link_text": str(block.get("link_text") or "")[:160],
                        "description": str(block.get("description") or "")[:500],
                    },
                )
            )
        else:
            raise ValidationError({"body": "正文包含不受支持的内容块。"})

    AuthorSubmissionAsset.objects.filter(
        article=article,
        kind__in=[
            AuthorSubmissionAsset.Kind.INLINE_IMAGE,
            AuthorSubmissionAsset.Kind.ATTACHMENT,
        ],
        is_active=True,
    ).exclude(pk__in=used_asset_ids).update(is_active=False)
    return materialized


def _store_author_cover(*, article, authorship, actor, uploaded_file):
    if uploaded_file is None:
        return None
    from ai_author_forum.images.models import CustomImage

    content_type = str(getattr(uploaded_file, "content_type", "") or "").lower()
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValidationError({"cover_image": "封面仅支持 JPEG、PNG 或 WebP。"})
    size = int(getattr(uploaded_file, "size", 0) or 0)
    if size <= 0 or size > 8 * 1024 * 1024:
        raise ValidationError({"cover_image": "封面大小必须在 1 字节到 8 MiB 之间。"})
    from PIL import Image, UnidentifiedImageError

    expected_formats = {
        "image/jpeg": "JPEG",
        "image/png": "PNG",
        "image/webp": "WEBP",
    }
    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as decoded:
            decoded.verify()
            actual_format = decoded.format
    except (OSError, UnidentifiedImageError) as exc:
        raise ValidationError({"cover_image": "封面文件不是有效图片。"}) from exc
    finally:
        uploaded_file.seek(0)
    if actual_format != expected_formats[content_type]:
        raise ValidationError({"cover_image": "封面文件内容与声明类型不一致。"})
    content_sha256 = _upload_sha256(uploaded_file)
    root_collection = Collection.get_first_root_node()
    collection_name = f"Author submission {article.pk}"
    collection = root_collection.get_children().filter(name=collection_name).first()
    if collection is None:
        collection = root_collection.add_child(name=collection_name)
    image = CustomImage(
        title=f"{article.title} - author cover",
        file=uploaded_file,
        collection=collection,
        uploaded_by_user=actor,
    )
    image.full_clean()
    image.save()
    AuthorSubmissionAsset.objects.filter(
        article=article,
        kind=AuthorSubmissionAsset.Kind.COVER,
        is_active=True,
    ).update(is_active=False)
    asset = AuthorSubmissionAsset.objects.create(
        article=article,
        authorship=authorship,
        uploaded_by=actor,
        kind=AuthorSubmissionAsset.Kind.COVER,
        image=image,
        original_name=str(uploaded_file.name)[:255],
        content_type=content_type,
        size=size,
        sha256=content_sha256,
        scan_status=AuthorSubmissionAsset.ScanStatus.CLEAN,
        scan_detail="signature, decoder and size validation passed",
    )
    article.featured_image = image
    return asset


def validate_author_submission(article, revision=None):
    errors = {}
    for field_name, label in (
        ("title", "标题"),
        ("abstract", "摘要"),
        ("keywords", "关键词"),
        ("responsibility_statement", "作者声明"),
    ):
        if not str(getattr(article, field_name, "") or "").strip():
            errors[field_name] = f"{label}为必填项。"
    if not article.body or not list(article.body):
        errors["body"] = "正文为必填项。"
    contributors = list(article.contributors.all())
    if not contributors:
        errors["contributors"] = "至少需要一名公开作者。"
    elif sum(1 for item in contributors if item.is_corresponding) != 1:
        errors["contributors"] = "必须且只能指定一名通讯作者。"
    owner = (
        ArticleAuthorship.objects.effective()
        .filter(article=article, role=ArticleAuthorship.Role.OWNER)
        .first()
    )
    if owner is None:
        errors["authorship"] = "投稿缺少有效投稿负责人。"
    unscanned_assets = article.author_assets.filter(is_active=True).exclude(
        scan_status="clean"
    )
    if unscanned_assets.exists():
        errors["assets"] = "投稿资源尚未通过安全扫描。"
    if errors:
        raise ValidationError(errors)
    revision_content = revision.content if revision is not None else None
    validate_article_category_revision(
        article=article,
        revision_content=revision_content,
        action="submit",
    )
    return owner


def _available_page_slug(parent, base_slug):
    base_slug = (base_slug or "submission")[:255]
    candidate = base_slug
    suffix = 2
    while parent.get_children().filter(slug=candidate).exists():
        marker = f"-{suffix}"
        candidate = f"{base_slug[: 255 - len(marker)]}{marker}"
        suffix += 1
    return candidate


def create_author_submission(
    *,
    actor,
    journal,
    category,
    fields,
    contributors,
    request_id,
    cover_file=None,
    body_uploads=None,
):
    request_id = request_uuid(request_id)
    action = AuthorSubmissionOperation.Action.CREATE
    existing = _existing_operation(request_id=request_id, action=action, actor=actor)
    if existing is not None:
        return existing.article
    try:
        with transaction.atomic():
            locked_journal = Journal.objects.select_for_update().get(pk=journal.pk)
            if not can_create_submission(actor, locked_journal):
                raise PermissionDenied("该账号或期刊当前不能创建作者投稿。")
            category = _validate_category(category, locked_journal)
            title = str(fields.get("title") or "").strip()
            if not title:
                raise ValidationError({"title": "标题为必填项。"})
            base_slug = slugify(title) or f"submission-{request_id.hex[:12]}"
            parent = Page.get_first_root_node()
            if parent is None:
                raise ValidationError("系统缺少可用的文章页面根节点。")
            article = ArticlePage(
                title=title,
                slug=_available_page_slug(parent, base_slug),
                static_slug="",
                abstract=str(fields.get("abstract") or "").strip(),
                # Resources need a saved article scope before they can be
                # created. The final structured body is resolved below inside
                # this same transaction.
                body=[],
                keywords=str(fields.get("keywords") or "").strip(),
                responsibility_statement=str(
                    fields.get("responsibility_statement") or ""
                ).strip(),
                ai_co_authors=str(fields.get("ai_co_authors") or "").strip(),
                ai_contribution_statement=str(
                    fields.get("ai_contribution_statement") or ""
                ).strip(),
                article_type=fields.get("article_type")
                or ArticlePage.ArticleType.AI_ARTICLE,
                primary_journal=locked_journal,
                review_status=ArticlePage.ReviewStatus.DRAFT,
            )
            parent.add_child(instance=article)
            authorship = ArticleAuthorship.objects.create(
                article=article,
                user=actor,
                role=ArticleAuthorship.Role.OWNER,
                can_edit=True,
                is_corresponding=True,
                invited_by=actor,
                accepted_at=timezone.now(),
            )
            _replace_contributors(article, contributors)
            _replace_primary_category(article, category)
            article.body = _materialize_author_body(
                article=article,
                authorship=authorship,
                actor=actor,
                body=fields.get("body") or [],
                body_uploads=body_uploads,
            )
            if cover_file is not None:
                _store_author_cover(
                    article=article,
                    authorship=authorship,
                    actor=actor,
                    uploaded_file=cover_file,
                )
            article.full_clean(exclude=["path", "depth", "numchild", "url_path"])
            article.save(user=actor, bypass_article_permission_check=True)
            revision = article.save_revision(
                user=actor,
                changed=True,
                bypass_article_permission_check=True,
            )
            _record_operation(
                request_id=request_id,
                action=action,
                actor=actor,
                article=article,
                authorship=authorship,
                revision=revision,
                result={"article_id": article.pk, "revision_id": revision.pk},
            )
            _audit(
                actor=actor,
                article=article,
                authorship=authorship,
                request_id=request_id,
                message="作者创建投稿草稿。",
                status=AuditStatus.SUCCESS,
                metadata={
                    "old_state": None,
                    "new_state": article.review_status,
                    "revision_id": revision.pk,
                    "target_journal_id": locked_journal.pk,
                    "result": "success",
                },
            )
            return article
    except Exception as exc:
        _audit(
            actor=actor,
            request_id=request_id,
            message="作者创建投稿草稿失败。",
            status=AuditStatus.FAILURE,
            metadata={
                "result": "failure",
                "error_type": type(exc).__name__,
                "target_journal_id": getattr(journal, "pk", None),
                "old_state": None,
                "new_state": None,
            },
        )
        raise


@audit_submission_failures(
    message="作者保存投稿被拒绝。", operation_source="author_workbench"
)
def save_author_submission(
    *,
    actor,
    article,
    expected_revision_id,
    fields,
    contributors,
    category,
    request_id,
    cover_file=None,
    remove_cover=False,
    body_uploads=None,
):
    request_id = request_uuid(request_id)
    action = AuthorSubmissionOperation.Action.SAVE
    existing = _existing_operation(
        request_id=request_id, action=action, actor=actor, article=article
    )
    if existing is not None:
        return existing.revision
    with transaction.atomic():
        locked = (
            ArticlePage.objects.select_for_update()
            .select_related("primary_journal")
            .get(pk=article.pk)
        )
        authorship = (
            ArticleAuthorship.objects.select_for_update()
            .filter(article=locked, user=actor)
            .first()
        )
        if authorship is None or not authorship.is_effective or not authorship.can_edit:
            raise PermissionDenied("无权编辑该投稿。")
        if not can_edit_submission(actor, locked):
            raise ArticleStateConflict("投稿当前已锁定，不能保存作者修改。")
        _latest_revision_or_conflict(locked, expected_revision_id)
        category = _validate_category(category, locked.primary_journal)
        unknown_fields = set(fields) - AUTHOR_EDITABLE_FIELDS
        if unknown_fields:
            raise ValidationError(
                {
                    "fields": f"包含不可由作者修改的字段：{', '.join(sorted(unknown_fields))}"
                }
            )
        for field_name, value in fields.items():
            if field_name == "body":
                continue
            setattr(locked, field_name, value)
        locked.body = _materialize_author_body(
            article=locked,
            authorship=authorship,
            actor=actor,
            body=fields.get("body") or [],
            body_uploads=body_uploads,
        )
        if remove_cover:
            locked.featured_image = None
            AuthorSubmissionAsset.objects.filter(
                article=locked,
                kind=AuthorSubmissionAsset.Kind.COVER,
                is_active=True,
            ).update(is_active=False)
        if cover_file is not None:
            _store_author_cover(
                article=locked,
                authorship=authorship,
                actor=actor,
                uploaded_file=cover_file,
            )
        locked.full_clean(exclude=["path", "depth", "numchild", "url_path"])
        locked.save(
            user=actor,
            bypass_article_permission_check=True,
        )
        _replace_contributors(locked, contributors)
        _replace_primary_category(locked, category)
        revision = locked.save_revision(
            user=actor,
            changed=True,
            bypass_article_permission_check=True,
        )
        _record_operation(
            request_id=request_id,
            action=action,
            actor=actor,
            article=locked,
            authorship=authorship,
            revision=revision,
            result={"revision_id": revision.pk},
        )
        _audit(
            actor=actor,
            article=locked,
            authorship=authorship,
            request_id=request_id,
            message="作者保存投稿 revision。",
            status=AuditStatus.SUCCESS,
            metadata={
                "old_state": locked.review_status,
                "new_state": locked.review_status,
                "revision_id": revision.pk,
                "result": "success",
            },
        )
        return revision


@audit_submission_failures(
    message="作者更换主投期刊被拒绝。", operation_source="author_workbench"
)
def change_author_submission_journal(
    *, actor, article, target_journal, target_category, expected_revision_id, request_id
):
    request_id = request_uuid(request_id)
    action = AuthorSubmissionOperation.Action.CHANGE_JOURNAL
    existing = _existing_operation(
        request_id=request_id, action=action, actor=actor, article=article
    )
    if existing is not None:
        return existing.revision
    with transaction.atomic():
        locked = (
            ArticlePage.objects.select_for_update()
            .select_related("primary_journal")
            .get(pk=article.pk)
        )
        authorship = (
            ArticleAuthorship.objects.select_for_update()
            .filter(article=locked, user=actor)
            .first()
        )
        if (
            authorship is None
            or not authorship.is_effective
            or authorship.role != ArticleAuthorship.Role.OWNER
        ):
            raise PermissionDenied("只有投稿负责人可以更换未提交草稿的期刊。")
        if locked.has_ever_been_submitted:
            raise ArticleStateConflict("文章首次提交后不能由作者自行更换期刊。")
        if not can_edit_submission(actor, locked):
            raise PermissionDenied("投稿当前已锁定，不能更换主投期刊。")
        _latest_revision_or_conflict(locked, expected_revision_id)
        target_journal = Journal.objects.select_for_update().get(pk=target_journal.pk)
        if target_journal.pk == locked.primary_journal_id:
            raise ValidationError(
                {"target_journal": "目标期刊必须不同于当前主投期刊。"}
            )
        if not journal_accepts_author_submission(target_journal):
            raise PermissionDenied("目标期刊当前不接受作者投稿。")
        target_category = _validate_category(target_category, target_journal)
        old_journal_id = locked.primary_journal_id
        locked.primary_journal = target_journal
        locked.save(user=actor, bypass_article_permission_check=True)
        _replace_primary_category(locked, target_category)
        revision = locked.save_revision(
            user=actor,
            changed=True,
            bypass_article_permission_check=True,
        )
        _record_operation(
            request_id=request_id,
            action=action,
            actor=actor,
            article=locked,
            authorship=authorship,
            revision=revision,
            result={"revision_id": revision.pk, "journal_id": target_journal.pk},
        )
        _audit(
            actor=actor,
            article=locked,
            authorship=authorship,
            request_id=request_id,
            message="作者确认更换未提交草稿的主投期刊。",
            status=AuditStatus.SUCCESS,
            metadata={
                "old_journal_id": old_journal_id,
                "target_journal_id": target_journal.pk,
                "revision_id": revision.pk,
                "result": "success",
            },
        )
        return revision


@audit_submission_failures(
    message="作者提交初审被拒绝。", operation_source="author_workbench"
)
def submit_author_submission(
    *, actor, article, expected_revision_id, request_id, comment=""
):
    request_id = request_uuid(request_id)
    action = AuthorSubmissionOperation.Action.SUBMIT
    existing = _existing_operation(
        request_id=request_id, action=action, actor=actor, article=article
    )
    if existing is not None:
        return ArticleReviewRecord.objects.get(pk=existing.result["review_record_id"])
    with transaction.atomic():
        locked = ArticlePage.objects.select_for_update().get(pk=article.pk)
        authorship = (
            ArticleAuthorship.objects.select_for_update()
            .filter(article=locked, user=actor)
            .first()
        )
        if authorship is None or not authorship.is_effective or not authorship.can_edit:
            raise PermissionDenied("无权提交该投稿。")
        if not can_submit_submission(actor, locked):
            raise ArticleStateConflict("投稿当前状态不能提交初审。")
        revision = _latest_revision_or_conflict(locked, expected_revision_id)
        validate_author_submission(locked, revision)
        record = submit_article_for_initial_review(
            actor=actor,
            article=locked,
            expected_state=ArticlePage.ReviewStatus.DRAFT,
            expected_revision_id=revision.pk,
            request_id=request_id,
            comment=comment,
            source="author",
        )
        _record_operation(
            request_id=request_id,
            action=action,
            actor=actor,
            article=locked,
            authorship=authorship,
            revision=revision,
            result={"review_record_id": record.pk, "revision_id": revision.pk},
        )
        return record


def _can_manage_authorship(actor, article):
    if is_super_admin(actor):
        return True
    assignment = get_journal_editor_assignment(actor, article.primary_journal)
    return bool(
        assignment and assignment.role == JournalEditorAssignment.Role.CHIEF_EDITOR
    )


@audit_submission_failures(
    message="授予投稿关系被拒绝。", operation_source="editor_admin"
)
def grant_article_authorship(
    *,
    actor,
    article,
    user,
    role=ArticleAuthorship.Role.CO_AUTHOR,
    can_edit=False,
    is_corresponding=False,
    request_id,
):
    request_id = request_uuid(request_id)
    action = AuthorSubmissionOperation.Action.GRANT
    existing = _existing_operation(
        request_id=request_id, action=action, actor=actor, article=article
    )
    if existing is not None:
        return existing.authorship
    with transaction.atomic():
        locked = ArticlePage.objects.select_for_update().get(pk=article.pk)
        if not _can_manage_authorship(actor, locked):
            raise PermissionDenied("只有本刊主编辑或超级管理员可以授予投稿关系。")
        user = type(user).objects.select_for_update().get(pk=user.pk)
        if not user.is_active or user.account_status != "active" or not user.is_author:
            raise ValidationError("只能向状态正常的作者账号授予投稿关系。")
        authorship = (
            ArticleAuthorship.objects.select_for_update()
            .filter(article=locked, user=user)
            .first()
        )
        other_effective = ArticleAuthorship.objects.select_for_update().filter(
            article=locked,
            revoked_at__isnull=True,
        )
        if authorship is not None:
            other_effective = other_effective.exclude(pk=authorship.pk)
            if (
                authorship.revoked_at is None
                and authorship.role == ArticleAuthorship.Role.OWNER
                and role != ArticleAuthorship.Role.OWNER
            ):
                raise ValidationError(
                    "投稿负责人不能直接改为共同作者；请先撤销原关系，再授予新的负责人。"
                )
        if (
            role == ArticleAuthorship.Role.OWNER
            and other_effective.filter(role=ArticleAuthorship.Role.OWNER).exists()
        ):
            raise ValidationError("该文章已有有效投稿负责人。")
        if is_corresponding and other_effective.filter(is_corresponding=True).exists():
            raise ValidationError("该文章已有有效通讯作者投稿关系。")
        values = {
            "role": role,
            "can_edit": role == ArticleAuthorship.Role.OWNER or bool(can_edit),
            "is_corresponding": bool(is_corresponding),
            "invited_by": actor,
            "accepted_at": timezone.now(),
            "revoked_at": None,
        }
        if authorship is None:
            authorship = ArticleAuthorship(article=locked, user=user, **values)
        else:
            for field_name, value in values.items():
                setattr(authorship, field_name, value)
        authorship.save()
        _record_operation(
            request_id=request_id,
            action=action,
            actor=actor,
            article=locked,
            authorship=authorship,
            result={"authorship_id": authorship.pk},
        )
        _audit(
            actor=actor,
            article=locked,
            authorship=authorship,
            request_id=request_id,
            message="授予或恢复文章投稿关系。",
            status=AuditStatus.SUCCESS,
            metadata={
                "role": authorship.role,
                "can_edit": authorship.can_edit,
                "is_corresponding": authorship.is_corresponding,
                "result": "success",
                "operation_source": "editor_admin",
            },
        )
        return authorship


@audit_submission_failures(
    message="撤销投稿关系被拒绝。", operation_source="editor_admin"
)
def revoke_article_authorship(*, actor, authorship, reason, request_id):
    request_id = request_uuid(request_id)
    action = AuthorSubmissionOperation.Action.REVOKE
    existing = _existing_operation(
        request_id=request_id,
        action=action,
        actor=actor,
        article=authorship.article,
    )
    if existing is not None:
        return existing.authorship
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "撤销投稿关系必须填写原因。"})
    with transaction.atomic():
        locked = (
            ArticleAuthorship.objects.select_for_update()
            .select_related("article", "article__primary_journal", "user")
            .get(pk=authorship.pk)
        )
        if not _can_manage_authorship(actor, locked.article):
            raise PermissionDenied("只有本刊主编辑或超级管理员可以撤销投稿关系。")
        if locked.revoked_at is None:
            locked.revoked_at = timezone.now()
            locked.can_edit = False
            locked.save(update_fields=["revoked_at", "can_edit", "updated_at"])
        sessions_revoked = revoke_user_sessions(locked.user)
        _record_operation(
            request_id=request_id,
            action=action,
            actor=actor,
            article=locked.article,
            authorship=locked,
            result={"authorship_id": locked.pk},
        )
        _audit(
            actor=actor,
            article=locked.article,
            authorship=locked,
            request_id=request_id,
            message="撤销文章投稿关系。",
            status=AuditStatus.SUCCESS,
            metadata={
                "reason": reason,
                "sessions_revoked": sessions_revoked,
                "result": "success",
                "operation_source": "editor_admin",
            },
        )
        return locked


@audit_submission_failures(message="受控转投被拒绝。", operation_source="editor_admin")
def controlled_transfer_submission(
    *,
    actor,
    article,
    target_journal,
    target_category,
    reason,
    expected_state,
    expected_revision_id,
    request_id,
):
    request_id = request_uuid(request_id)
    action = AuthorSubmissionOperation.Action.TRANSFER
    existing = _existing_operation(
        request_id=request_id, action=action, actor=actor, article=article
    )
    if existing is not None:
        return existing.revision
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "受控转投必须填写原因。"})
    with transaction.atomic():
        locked = (
            ArticlePage.objects.select_for_update()
            .select_related("primary_journal")
            .get(pk=article.pk)
        )
        source_assignment = get_journal_editor_assignment(actor, locked.primary_journal)
        if not is_super_admin(actor) and not (
            source_assignment
            and source_assignment.role == JournalEditorAssignment.Role.CHIEF_EDITOR
        ):
            raise PermissionDenied("只有原期刊主编辑或超级管理员可以执行受控转投。")
        if not locked.has_ever_been_submitted:
            raise ArticleStateConflict("尚未提交的草稿应由投稿负责人自行更换期刊。")
        if expected_state and locked.review_status != expected_state:
            raise ArticleStateConflict("文章状态已变化，请刷新后重试。")
        if locked.review_status in {
            ArticlePage.ReviewStatus.APPROVED,
            ArticlePage.ReviewStatus.PUBLISHED,
        } or locked.publication_status in {
            ArticlePage.PublicationStatus.PLACED,
            ArticlePage.PublicationStatus.BUILT,
            ArticlePage.PublicationStatus.PUBLISHED,
        }:
            raise ArticleStateConflict(
                "已终审通过、投放、构建或发布的文章不能直接转投。"
            )
        _latest_revision_or_conflict(locked, expected_revision_id)
        target_journal = Journal.objects.select_for_update().get(pk=target_journal.pk)
        if target_journal.pk == locked.primary_journal_id:
            raise ValidationError(
                {"target_journal": "目标期刊必须不同于当前主投期刊。"}
            )
        if not journal_accepts_author_submission(target_journal):
            raise PermissionDenied("目标期刊当前不接受作者投稿。")
        target_category = _validate_category(target_category, target_journal)
        old_journal_id = locked.primary_journal_id
        workflow_state = locked.current_workflow_state
        if workflow_state is not None:
            workflow_state.cancel(user=actor)
        locked.primary_journal = target_journal
        locked.review_status = ArticlePage.ReviewStatus.SUBMITTED
        locked.approved_version = None
        locked.rejected_version = None
        locked.assigned_initial_editor = None
        locked.assigned_by = None
        locked.assigned_at = None
        locked.assignment_request_id = None
        locked.last_submitted_at = timezone.now()
        locked.save(bypass_article_permission_check=True)
        _replace_primary_category(locked, target_category)
        revision = locked.save_revision(
            user=actor,
            changed=True,
            bypass_article_permission_check=True,
        )
        owner = (
            ArticleAuthorship.objects.effective()
            .filter(article=locked, role=ArticleAuthorship.Role.OWNER)
            .first()
        )
        record = ArticleReviewRecord.objects.create(
            article=locked,
            stage=ArticleReviewRecord.Stage.INITIAL,
            action=ArticleReviewRecord.Action.TRANSFER,
            revision=revision,
            reviewer=actor,
            journal_editor_assignment=source_assignment,
            reviewer_role=(
                source_assignment.role if source_assignment else "super_admin"
            ),
            request_id=request_id,
            comment=reason,
            author_visible_comment=reason,
            content_sha256=revision_sha256(revision),
            submission_owner=owner,
            submission_journal=target_journal,
            authorship_updated_at=getattr(owner, "updated_at", None),
        )
        from .wagtail_hooks import _get_or_create_article_workflow

        workflow = _get_or_create_article_workflow()
        WorkflowPage.objects.update_or_create(
            page=locked,
            defaults={"workflow": workflow},
        )
        if workflow is not None and not locked.workflow_in_progress:
            workflow.start(locked, actor)
        _record_operation(
            request_id=request_id,
            action=action,
            actor=actor,
            article=locked,
            authorship=owner,
            revision=revision,
            result={
                "revision_id": revision.pk,
                "review_record_id": record.pk,
                "journal_id": target_journal.pk,
            },
        )
        _audit(
            actor=actor,
            article=locked,
            authorship=owner,
            request_id=request_id,
            message="编辑执行受控转投并重新进入目标期刊初审。",
            status=AuditStatus.SUCCESS,
            metadata={
                "old_journal_id": old_journal_id,
                "target_journal_id": target_journal.pk,
                "old_state": expected_state,
                "new_state": locked.review_status,
                "revision_id": revision.pk,
                "review_record_id": record.pk,
                "reason": reason,
                "result": "success",
                "operation_source": "editor_admin",
            },
        )
        return revision
