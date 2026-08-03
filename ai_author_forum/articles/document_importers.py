from __future__ import annotations

import hashlib
import io
import re
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import mammoth
import yaml
from defusedxml import ElementTree as DefusedET
from django.conf import settings
from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from PIL import Image as PILImage

DOCUMENT_CONVERTER_NAME = "ai_author_forum.document_importers"
DOCUMENT_CONVERTER_VERSION = "1.0"
DOCX_FORMAT = "docx"
MARKDOWN_FORMAT = "markdown"
MAX_DOCX_MEMBERS = 2000
MAX_NESTED_MEMBERS_PER_JOB = 20000
MAX_LOGICAL_EXTRACTED_SIZE = 250 * 1024 * 1024
MAX_DOCX_SIZE = 25 * 1024 * 1024
MAX_MARKDOWN_SIZE = 5 * 1024 * 1024
MAX_CONVERTED_HTML_SIZE = 10 * 1024 * 1024
MAX_XML_NODES = 200_000
MAX_XML_DEPTH = 128
MAX_MARKDOWN_LINES = 200_000
MAX_MARKDOWN_CHARACTERS_WARNING = 800_000
MAX_MARKDOWN_CHARACTERS = 900_000
MAX_VISIBLE_CHARACTERS_WARNING = 100_000
MAX_VISIBLE_CHARACTERS = 1_000_000
MAX_TOTAL_IMAGE_PIXELS = 250_000_000
MAX_FRONT_MATTER_SIZE = 64 * 1024
MAX_FRONT_MATTER_KEYS = 50
MAX_FRONT_MATTER_DEPTH = 5
MAX_IMAGES = 500
MAX_IMAGE_FILE_SIZE = 10 * 1024 * 1024
MAX_IMAGE_WIDTH = 12_000
MAX_IMAGE_HEIGHT = 12_000
MAX_IMAGE_PIXELS = 50_000_000
ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
FORBIDDEN_DOCX_NAMES = (
    "vbaproject.bin",
    "activex",
    "embeddings/",
    "altchunk",
    "encryptioninfo",
    "encryptedpackage",
)
FORBIDDEN_EXECUTABLE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".jar",
    ".js",
    ".msi",
    ".ps1",
    ".scr",
}
OLE_COMPOUND_FILE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FORBIDDEN_METADATA_KEYS = {
    "status",
    "approved",
    "published",
    "placement",
    "is_pinned",
    "sort_order",
    "build_version",
}
SUPPORTED_FRONT_MATTER_KEYS = {
    "title",
    "slug",
    "journal_slug",
    "article_type",
    "authors",
    "ai_co_authors",
    "abstract",
    "keywords",
    "publication_date",
    "cover_image",
    "primary_category_code",
    "primary_category_path",
    "related_category_codes",
    "related_category_paths",
    "notes",
}


def _limit(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


STYLE_MAP = """
'p[style-name="Heading 1"]' => h1
'p[style-name="Heading 2"]' => h2
'p[style-name="Heading 3"]' => h3
'p[style-name="Heading 4"]' => h4
'p[style-name="Heading 5"]' => h5
'p[style-name="Heading 6"]' => h6
'p[style-name="Quote"]' => blockquote
'p[style-name="Intense Quote"]' => blockquote
'p[style-name="Caption"]' => figcaption
'p[style-name="Code"]' => code
"""


class DocumentImportError(ValueError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DocumentConversionWarning:
    code: str
    message: str
    source_path: str = ""
    element: str = ""


@dataclass
class ConvertedArticleDocument:
    source_format: str
    source_path: str
    source_sha256: str
    converter_name: str
    converter_version: str
    html: str
    metadata: dict = field(default_factory=dict)
    generated_assets: list[str] = field(default_factory=list)
    warnings: list[DocumentConversionWarning] = field(default_factory=list)
    statistics: dict = field(default_factory=dict)


@dataclass
class ImportExtractionBudget:
    max_total_bytes: int = MAX_LOGICAL_EXTRACTED_SIZE
    max_nested_members: int = MAX_NESTED_MEMBERS_PER_JOB
    used_bytes: int = 0
    used_nested_members: int = 0

    def consume(self, *, bytes_: int = 0, nested_members: int = 0) -> None:
        if bytes_ < 0 or nested_members < 0:
            raise ValueError("budget increments must be non-negative")
        if self.used_bytes + bytes_ > self.max_total_bytes:
            raise DocumentImportError(
                "文档逻辑解压量超过 250 MB 上限。",
                code="ARTICLE_DOCX_LIMIT_EXCEEDED",
            )
        if self.used_nested_members + nested_members > self.max_nested_members:
            raise DocumentImportError(
                "文档内部成员累计超过任务上限。",
                code="ARTICLE_DOCX_LIMIT_EXCEEDED",
            )
        self.used_bytes += bytes_
        self.used_nested_members += nested_members


def _safe_member_name(name: str) -> str:
    raw = name.replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or "\x00" in raw
        or raw.startswith("/")
        or path.is_absolute()
        or ".." in path.parts
        or re.match(r"^[A-Za-z]:", raw)
    ):
        raise DocumentImportError(
            "文档内部路径不安全。", code="ARTICLE_DOCX_INVALID_PACKAGE"
        )
    return path.as_posix()


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return (mode & 0o170000) == 0o120000


def _zip_member_bytes(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: ImportExtractionBudget
) -> bytes:
    if info.flag_bits & 0x1:
        raise DocumentImportError(
            "不接受加密或密码保护文档。", code="ARTICLE_DOCX_ENCRYPTED"
        )
    if info.file_size > _limit("ARTICLE_IMPORT_MAX_DOCX_SIZE", MAX_DOCX_SIZE):
        raise DocumentImportError(
            "DOCX 内部成员超过安全限制。", code="ARTICLE_DOCX_LIMIT_EXCEEDED"
        )
    try:
        return zf.read(info)
    except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
        raise DocumentImportError(
            "DOCX 内部成员无法读取。", code="ARTICLE_DOCX_INVALID_PACKAGE"
        ) from exc


def _parse_xml_limited(data: bytes, *, label: str):
    """Parse untrusted OPC XML while enforcing node and nesting budgets."""
    max_nodes = _limit("ARTICLE_IMPORT_MAX_XML_NODES", MAX_XML_NODES)
    max_depth = _limit("ARTICLE_IMPORT_MAX_XML_DEPTH", MAX_XML_DEPTH)
    node_count = 0
    depth = 0
    root = None
    try:
        iterator = DefusedET.iterparse(
            io.BytesIO(data),
            events=("start", "end"),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
        for event, element in iterator:
            if event == "start":
                node_count += 1
                depth += 1
                if root is None:
                    root = element
                if node_count > max_nodes or depth > max_depth:
                    raise DocumentImportError(
                        f"{label} XML 节点或深度超过安全限制。",
                        code="ARTICLE_DOCX_LIMIT_EXCEEDED",
                    )
            else:
                depth -= 1
    except DocumentImportError:
        raise
    except Exception as exc:
        raise DocumentImportError(
            f"{label} XML 无效。", code="ARTICLE_DOCX_INVALID_PACKAGE"
        ) from exc
    if root is None:
        raise DocumentImportError(
            f"{label} XML 为空。", code="ARTICLE_DOCX_INVALID_PACKAGE"
        )
    return root


def _relationship_is_allowed_external(rel) -> bool:
    rel_type = str(rel.attrib.get("Type", "")).lower()
    target = str(rel.attrib.get("Target", "")).strip()
    scheme = urlsplit(re.sub(r"[\x00-\x20\x7f]+", "", target)).scheme.lower()
    return rel_type.endswith("/hyperlink") and scheme in {"http", "https", "mailto"}


def _validate_docx_package(
    path: Path, budget: ImportExtractionBudget
) -> dict[str, bytes]:
    if path.stat().st_size > _limit("ARTICLE_IMPORT_MAX_DOCX_SIZE", MAX_DOCX_SIZE):
        raise DocumentImportError(
            "DOCX 文件超过 25 MB。", code="ARTICLE_DOCX_LIMIT_EXCEEDED"
        )
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > _limit("ARTICLE_IMPORT_MAX_DOCX_MEMBERS", MAX_DOCX_MEMBERS):
                raise DocumentImportError(
                    "DOCX 内部成员超过 2,000 个。", code="ARTICLE_DOCX_LIMIT_EXCEEDED"
                )
            names: set[str] = set()
            normalized_infos: list[tuple[str, zipfile.ZipInfo]] = []
            for info in infos:
                name = _safe_member_name(info.filename)
                lower = name.lower()
                if lower in names:
                    raise DocumentImportError(
                        "DOCX 存在重复归一化路径。", code="ARTICLE_DOCX_INVALID_PACKAGE"
                    )
                names.add(lower)
                normalized_infos.append((lower, info))
                if _is_symlink(info):
                    raise DocumentImportError(
                        "DOCX 不允许符号链接。", code="ARTICLE_DOCX_INVALID_PACKAGE"
                    )
                if any(token in lower for token in FORBIDDEN_DOCX_NAMES):
                    if "vba" in lower or "activex" in lower:
                        code = "ARTICLE_DOCX_MACRO_UNSAFE"
                    elif "encrypt" in lower:
                        code = "ARTICLE_DOCX_ENCRYPTED"
                    else:
                        code = "ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE"
                    raise DocumentImportError(
                        "DOCX 包含宏、ActiveX、加密内容或嵌入对象。", code=code
                    )
                if PurePosixPath(lower).suffix in FORBIDDEN_EXECUTABLE_SUFFIXES:
                    raise DocumentImportError(
                        "DOCX 包含可执行内容。",
                        code="ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE",
                    )
                if info.file_size > _limit(
                    "ARTICLE_IMPORT_MAX_DOCX_SIZE", MAX_DOCX_SIZE
                ):
                    raise DocumentImportError(
                        "DOCX 内部成员超过安全限制。",
                        code="ARTICLE_DOCX_LIMIT_EXCEEDED",
                    )
            required = {"[content_types].xml", "word/document.xml"}
            if not required.issubset(names):
                raise DocumentImportError(
                    "DOCX 缺少必要的包成员。", code="ARTICLE_DOCX_INVALID_PACKAGE"
                )

            data: dict[str, bytes] = {}
            for lower, info in normalized_infos:
                if info.is_dir():
                    continue
                budget.consume(bytes_=info.file_size, nested_members=1)
                if lower.endswith(".xml") or lower.endswith(".rels"):
                    member_data = _zip_member_bytes(zf, info, budget)
                    root = _parse_xml_limited(member_data, label=lower)
                    data[lower] = member_data
                    if lower.endswith(".rels"):
                        for rel in root.iter():
                            target_mode = str(rel.attrib.get("TargetMode", "")).lower()
                            if (
                                target_mode == "external"
                                and not _relationship_is_allowed_external(rel)
                            ):
                                raise DocumentImportError(
                                    "DOCX 存在危险外部关系。",
                                    code="ARTICLE_DOCX_EXTERNAL_RELATIONSHIP_UNSAFE",
                                )
            content_types = data.get("[content_types].xml", b"").lower()
            if b"macroenabled" in content_types or b"activex" in content_types:
                raise DocumentImportError(
                    "DOCX 内容类型声明包含宏或 ActiveX。",
                    code="ARTICLE_DOCX_MACRO_UNSAFE",
                )
            if b"oleobject" in content_types:
                raise DocumentImportError(
                    "DOCX 内容类型声明包含 OLE 对象。",
                    code="ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE",
                )
            document_root = _parse_xml_limited(
                data["word/document.xml"], label="word/document.xml"
            )
            unsafe_elements = {
                element.tag.rsplit("}", 1)[-1].lower()
                for element in document_root.iter()
            }
            if "altchunk" in unsafe_elements:
                raise DocumentImportError(
                    "DOCX 包含 altChunk 外部内容。",
                    code="ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE",
                )
            if unsafe_elements.intersection({"object", "oleobject", "control"}):
                raise DocumentImportError(
                    "DOCX 包含 OLE、ActiveX 或嵌入对象。",
                    code="ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE",
                )
            return data
    except DocumentImportError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocumentImportError(
            "DOCX 包结构无效。", code="ARTICLE_DOCX_INVALID_PACKAGE"
        ) from exc


def detect_document_format(path: Path, declared_suffix: str) -> str:
    raw_suffix = str(declared_suffix or path.suffix).strip().lower()
    suffix = (
        raw_suffix if raw_suffix.startswith(".") else Path(raw_suffix).suffix.lower()
    )
    if suffix not in {".docx", ".md", ".markdown"}:
        raise DocumentImportError(
            "不支持的文档格式。", code="ARTICLE_DOCUMENT_FORMAT_UNSUPPORTED"
        )
    try:
        with path.open("rb") as stream:
            prefix = stream.read(8)
    except OSError as exc:
        raise DocumentImportError(
            "文档无法读取。", code="ARTICLE_DOCUMENT_FILE_NOT_FOUND"
        ) from exc
    if suffix == ".docx":
        if prefix == OLE_COMPOUND_FILE_SIGNATURE:
            raise DocumentImportError(
                "DOCX 已加密或受密码保护。", code="ARTICLE_DOCX_ENCRYPTED"
            )
        if not prefix.startswith(b"PK"):
            raise DocumentImportError(
                "DOCX 扩展名与实际内容不匹配。", code="ARTICLE_DOCUMENT_MIME_MISMATCH"
            )
        return DOCX_FORMAT
    if prefix.startswith(b"PK") or prefix == OLE_COMPOUND_FILE_SIGNATURE:
        raise DocumentImportError(
            "Markdown 扩展名与实际内容不匹配。", code="ARTICLE_DOCUMENT_MIME_MISMATCH"
        )
    return MARKDOWN_FORMAT


def _core_metadata(data: bytes) -> tuple[dict, dict]:
    if not data:
        return {}, {}
    root = _parse_xml_limited(data, label="docProps/core.xml")
    raw_values: dict[str, str] = {}
    for element in root.iter():
        key = element.tag.rsplit("}", 1)[-1]
        if (
            key
            in {
                "title",
                "creator",
                "lastModifiedBy",
                "keywords",
                "created",
                "modified",
                "subject",
                "description",
            }
            and element.text
        ):
            raw_values[key] = element.text.strip()

    metadata: dict[str, str] = {}
    if raw_values.get("title"):
        metadata["title"] = raw_values["title"]
    if raw_values.get("creator"):
        metadata["authors"] = raw_values["creator"]
    if raw_values.get("keywords"):
        metadata["keywords"] = raw_values["keywords"]
    abstract = raw_values.get("description") or raw_values.get("subject")
    if abstract:
        metadata["abstract"] = abstract

    source_info = {
        "last_modified_by": raw_values.get("lastModifiedBy", ""),
        "created": raw_values.get("created", ""),
        "modified": raw_values.get("modified", ""),
        "subject": raw_values.get("subject", ""),
        "description": raw_values.get("description", ""),
    }
    return metadata, {key: value for key, value in source_info.items() if value}


def _visible_text(html: str) -> str:
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def _finalize(result: ConvertedArticleDocument) -> ConvertedArticleDocument:
    html_bytes = len(result.html.encode("utf-8"))
    visible_chars = len(_visible_text(result.html))
    image_count = max(
        len(result.generated_assets),
        len(re.findall(r"<img\b", result.html, flags=re.IGNORECASE)),
    )
    if image_count > _limit("ARTICLE_IMPORT_MAX_IMAGES", MAX_IMAGES):
        raise DocumentImportError(
            "文档图片数量超过 500 张。",
            code="ARTICLE_DOCUMENT_IMAGE_UNSAFE",
        )
    total_image_pixels = int(result.statistics.get("total_image_pixels", 0) or 0)
    if total_image_pixels > _limit(
        "ARTICLE_IMPORT_MAX_TOTAL_IMAGE_PIXELS", MAX_TOTAL_IMAGE_PIXELS
    ):
        raise DocumentImportError(
            "文档图片总像素超过安全限制。",
            code="ARTICLE_DOCUMENT_IMAGE_UNSAFE",
        )
    if not result.html.strip() or not visible_chars:
        raise DocumentImportError(
            "转换后没有可见正文。", code="ARTICLE_DOCUMENT_BODY_EMPTY"
        )
    max_visible_chars = _limit(
        "ARTICLE_IMPORT_MAX_VISIBLE_CHARACTERS", MAX_VISIBLE_CHARACTERS
    )
    if visible_chars > max_visible_chars:
        raise DocumentImportError(
            "文档可见正文字符数超过 1,000,000。",
            code="ARTICLE_DOCUMENT_CONVERSION_FAILED",
        )
    warning_visible_chars = _limit(
        "ARTICLE_IMPORT_VISIBLE_CHARACTERS_WARNING",
        MAX_VISIBLE_CHARACTERS_WARNING,
    )
    if warning_visible_chars > 0 and visible_chars > warning_visible_chars:
        result.warnings.append(
            DocumentConversionWarning(
                "ARTICLE_DOCUMENT_FORMAT_DEGRADED",
                f"可见正文超过 {warning_visible_chars:,} 个字符，预览和转换可能较慢。",
            )
        )
    if html_bytes > _limit(
        "ARTICLE_IMPORT_MAX_CONVERTED_HTML_SIZE", MAX_CONVERTED_HTML_SIZE
    ):
        raise DocumentImportError(
            "转换后 HTML 超过 10 MB。", code="ARTICLE_DOCUMENT_HTML_TOO_LARGE"
        )
    result.statistics.update(
        {
            "visible_characters": visible_chars,
            "html_bytes": html_bytes,
            "image_count": image_count,
            "total_image_pixels": total_image_pixels,
        }
    )
    return result


def _actual_image_type(raw: bytes) -> tuple[str, str, int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", PILImage.DecompressionBombWarning)
            with PILImage.open(io.BytesIO(raw)) as parsed:
                width, height = parsed.size
                image_format = (parsed.format or "").upper()
                suffix_by_format = {
                    "JPEG": ".jpg",
                    "PNG": ".png",
                    "GIF": ".gif",
                    "WEBP": ".webp",
                }
                content_type_by_format = {
                    "JPEG": "image/jpeg",
                    "PNG": "image/png",
                    "GIF": "image/gif",
                    "WEBP": "image/webp",
                }
                if image_format not in suffix_by_format:
                    raise ValueError("unsupported image format")
                if width <= 0 or height <= 0:
                    raise ValueError("invalid image dimensions")
                if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
                    raise ValueError("image dimensions exceed limit")
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("image pixel count exceeds limit")
                parsed.verify()
                return (
                    suffix_by_format[image_format],
                    content_type_by_format[image_format],
                    width,
                    height,
                )
    except Exception as exc:
        raise DocumentImportError(
            "DOCX 图片格式、尺寸或像素不安全。",
            code="ARTICLE_DOCUMENT_IMAGE_UNSAFE",
        ) from exc


def _asset_path_for(
    image, generated_root: Path, package_root: Path, digest: str, sequence: int
) -> tuple[str, str, str, str, int, int]:
    declared_content_type = str(getattr(image, "content_type", "") or "").lower()
    with image.open() as stream:
        raw = stream.read()
    if len(raw) > MAX_IMAGE_FILE_SIZE:
        raise DocumentImportError(
            "文档图片超过 10 MB。", code="ARTICLE_DOCUMENT_IMAGE_UNSAFE"
        )
    suffix, actual_content_type, width, height = _actual_image_type(raw)
    if declared_content_type and declared_content_type != actual_content_type:
        raise DocumentImportError(
            "DOCX 图片声明类型与实际内容不匹配。",
            code="ARTICLE_DOCUMENT_IMAGE_UNSAFE",
        )
    image_digest = hashlib.sha256(raw).hexdigest()
    target = generated_root / digest / f"{sequence:04d}{suffix}"
    try:
        target.resolve().relative_to(package_root.resolve())
    except ValueError as exc:
        raise DocumentImportError(
            "转换图片输出路径不安全。", code="ARTICLE_DOCUMENT_IMAGE_UNSAFE"
        ) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(raw)
    rel = target.relative_to(package_root).as_posix()
    return rel, str(target), image_digest, actual_content_type, width, height


def _docx_image_relationship_ids(document_xml: bytes) -> list[str]:
    if not document_xml:
        return []
    root = _parse_xml_limited(document_xml, label="word/document.xml")
    relationship_ids: list[str] = []
    for element in root.iter():
        for name, value in element.attrib.items():
            if name.rsplit("}", 1)[-1] == "embed" and value:
                relationship_ids.append(str(value))
    return relationship_ids


def convert_docx_to_html(
    path: Path,
    *,
    package_root: Path,
    generated_root: Path,
    budget: ImportExtractionBudget,
) -> ConvertedArticleDocument:
    data = _validate_docx_package(path, budget)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata, source_metadata = _core_metadata(data.get("docprops/core.xml", b""))
    xml = data.get("word/document.xml", b"")
    warnings_payload: list[DocumentConversionWarning] = []
    if any(
        token in xml.lower()
        for token in (b"<w:ins", b"<w:del", b"<w:comment", b"<w:vanish")
    ):
        warnings_payload.append(
            DocumentConversionWarning(
                "ARTICLE_DOCX_REVISIONS_PRESENT",
                "检测到修订、批注或隐藏文本；仅导入可见正文。",
            )
        )
    relationship_ids = _docx_image_relationship_ids(xml)
    assets: list[str] = []
    asset_records: list[dict[str, str | int]] = []
    sequence = 0

    def convert_image(image):
        nonlocal sequence
        sequence += 1
        (
            rel,
            _,
            image_digest,
            actual_content_type,
            width,
            height,
        ) = _asset_path_for(image, generated_root, package_root, digest, sequence)
        assets.append(rel)
        asset_records.append(
            {
                "sequence": sequence,
                "source_document_sha256": digest,
                "relationship_id": (
                    relationship_ids[sequence - 1]
                    if sequence <= len(relationship_ids)
                    else ""
                ),
                "converted_reference": rel,
                "image_sha256": image_digest,
                "content_type": actual_content_type,
                "width": width,
                "height": height,
                "pixels": width * height,
            }
        )
        return {"src": rel}

    try:
        with path.open("rb") as stream:
            conversion = mammoth.convert_to_html(
                stream,
                style_map=STYLE_MAP,
                convert_image=mammoth.images.img_element(convert_image),
            )
    except DocumentImportError:
        raise
    except Exception as exc:
        raise DocumentImportError(
            "DOCX 转换失败。", code="ARTICLE_DOCUMENT_CONVERSION_FAILED"
        ) from exc
    for message in conversion.messages:
        text = str(getattr(message, "message", message))
        warnings_payload.append(
            DocumentConversionWarning(
                "ARTICLE_DOCUMENT_UNSUPPORTED_ELEMENT", text[:500]
            )
        )
    try:
        source_path = path.resolve().relative_to(package_root.resolve()).as_posix()
    except ValueError as exc:
        raise DocumentImportError(
            "DOCX 文件不在导入隔离目录中。", code="ARTICLE_DOCUMENT_FILE_NOT_FOUND"
        ) from exc
    result = ConvertedArticleDocument(
        source_format=DOCX_FORMAT,
        source_path=source_path,
        source_sha256=digest,
        converter_name="mammoth",
        converter_version=getattr(mammoth, "__version__", "1.11.0"),
        html=conversion.value,
        metadata=metadata,
        generated_assets=assets,
        warnings=warnings_payload,
        statistics={
            "source_bytes": path.stat().st_size,
            "source_metadata": source_metadata,
            "generated_assets": asset_records,
            "total_image_pixels": sum(
                int(record["pixels"]) for record in asset_records
            ),
        },
    )
    return _finalize(result)


class _SafeFrontMatterLoader(yaml.SafeLoader):
    pass


def _construct_mapping_no_duplicates(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DocumentImportError(
                "Front Matter 包含重复键。",
                code="ARTICLE_MARKDOWN_FRONT_MATTER_INVALID",
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_SafeFrontMatterLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_no_duplicates,
)


def _reject_yaml_tag(loader, tag_suffix, node):
    raise DocumentImportError(
        "Front Matter 不允许自定义 YAML 标签。",
        code="ARTICLE_MARKDOWN_FRONT_MATTER_INVALID",
    )


_SafeFrontMatterLoader.add_multi_constructor("!", _reject_yaml_tag)


def _yaml_depth(value, depth=0) -> int:
    if depth > MAX_FRONT_MATTER_DEPTH:
        return depth
    if isinstance(value, dict):
        return max([depth] + [_yaml_depth(v, depth + 1) for v in value.values()])
    if isinstance(value, list):
        return max([depth] + [_yaml_depth(v, depth + 1) for v in value])
    return depth


def _parse_front_matter(text: str) -> tuple[dict, str, list[DocumentConversionWarning]]:
    if not text.startswith("---"):
        return {}, text, []
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, flags=re.DOTALL)
    if not match:
        raise DocumentImportError(
            "Markdown Front Matter 无效。", code="ARTICLE_MARKDOWN_FRONT_MATTER_INVALID"
        )
    raw = match.group(1).encode("utf-8")
    if len(raw) > MAX_FRONT_MATTER_SIZE:
        raise DocumentImportError(
            "Markdown Front Matter 超过 64 KB。",
            code="ARTICLE_MARKDOWN_FRONT_MATTER_INVALID",
        )
    yaml_text = raw.decode("utf-8")
    try:
        for event in yaml.parse(yaml_text, Loader=_SafeFrontMatterLoader):
            if event.__class__.__name__ == "AliasEvent" or getattr(
                event, "anchor", None
            ):
                raise DocumentImportError(
                    "Front Matter 不允许 YAML 锚点或别名。",
                    code="ARTICLE_MARKDOWN_FRONT_MATTER_INVALID",
                )
        loader = _SafeFrontMatterLoader(yaml_text)
        try:
            values = loader.get_single_data()
        finally:
            loader.dispose()
    except DocumentImportError:
        raise
    except Exception as exc:
        raise DocumentImportError(
            "Markdown Front Matter 无效。", code="ARTICLE_MARKDOWN_FRONT_MATTER_INVALID"
        ) from exc
    if (
        not isinstance(values, dict)
        or len(values) > MAX_FRONT_MATTER_KEYS
        or _yaml_depth(values) > MAX_FRONT_MATTER_DEPTH
    ):
        raise DocumentImportError(
            "Markdown Front Matter 结构超过限制。",
            code="ARTICLE_MARKDOWN_FRONT_MATTER_INVALID",
        )
    warnings = []
    result = {}
    for key, value in values.items():
        key = str(key)
        if key in FORBIDDEN_METADATA_KEYS:
            warnings.append(
                DocumentConversionWarning(
                    "ARTICLE_DOCUMENT_FORBIDDEN_METADATA_IGNORED",
                    f"已忽略禁止元数据字段：{key}",
                )
            )
            continue
        if key not in SUPPORTED_FRONT_MATTER_KEYS:
            warnings.append(
                DocumentConversionWarning(
                    "ARTICLE_DOCUMENT_UNKNOWN_METADATA_IGNORED",
                    f"已忽略未知元数据字段：{key}",
                )
            )
            continue
        result[key] = value
    return result, text[match.end() :], warnings


def _preflight_markdown_destinations(text: str) -> None:
    """Reject unsafe Markdown destinations even when the parser renders them as text."""
    image_pattern = re.compile(
        r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))", re.IGNORECASE
    )
    for match in image_pattern.finditer(text):
        src = (match.group(1) or match.group(2) or "").strip()
        compact = re.sub(r"[\x00-\x20\x7f]+", "", src)
        parsed = urlsplit(compact)
        decoded_path = unquote(parsed.path)
        if (
            not src
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or src.startswith("//")
            or decoded_path.startswith("/")
            or re.match(r"^[A-Za-z]:", decoded_path)
            or decoded_path.startswith("\\")
        ):
            raise DocumentImportError(
                "Markdown 图片必须是 ZIP 内安全的相对路径。",
                code="ARTICLE_DOCUMENT_IMAGE_UNSAFE",
            )

    link_pattern = re.compile(
        r"(?<!!)\[[^\]]+\]\(\s*(?:<([^>]+)>|([^\s)]+))",
        re.IGNORECASE,
    )
    for match in link_pattern.finditer(text):
        href = (match.group(1) or match.group(2) or "").strip()
        compact = re.sub(r"[\x00-\x20\x7f]+", "", href)
        scheme = urlsplit(compact).scheme.lower()
        if scheme and scheme not in {"http", "https", "mailto"}:
            raise DocumentImportError(
                "Markdown 链接协议不安全。",
                code="ARTICLE_DOCUMENT_CONVERSION_FAILED",
            )


def _rewrite_markdown_images(
    html: str,
    *,
    source_path: Path,
    package_root: Path,
    direct: bool,
) -> tuple[str, list[DocumentConversionWarning], list[dict[str, int | str]]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    warnings_payload: list[DocumentConversionWarning] = []
    image_records: list[dict[str, int | str]] = []
    package_root_resolved = package_root.resolve()
    for image in soup.find_all("img"):
        src = str(image.get("src") or "").strip()
        compact = re.sub(r"[\x00-\x20\x7f]+", "", src)
        parsed = urlsplit(compact)
        decoded_path = unquote(parsed.path)
        if (
            not src
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or src.startswith("//")
            or decoded_path.startswith("/")
            or re.match(r"^[A-Za-z]:", decoded_path)
            or decoded_path.startswith("\\")
        ):
            raise DocumentImportError(
                "Markdown 图片必须是 ZIP 内安全的相对路径。",
                code="ARTICLE_DOCUMENT_IMAGE_UNSAFE",
            )
        if direct:
            raise DocumentImportError(
                "直接上传 Markdown 不能包含本地图片，请改用 ZIP。",
                code="ARTICLE_MARKDOWN_LOCAL_IMAGE_REQUIRES_ZIP",
            )
        relative_path = decoded_path.replace("\\", "/")
        candidate = (source_path.parent / relative_path).resolve()
        try:
            candidate.relative_to(package_root_resolved)
        except ValueError as exc:
            raise DocumentImportError(
                "Markdown 图片路径不安全。", code="ARTICLE_DOCUMENT_IMAGE_UNSAFE"
            ) from exc
        if not candidate.is_file() or candidate.stat().st_size > MAX_IMAGE_FILE_SIZE:
            raise DocumentImportError(
                "Markdown 图片不存在或超过安全限制。",
                code="ARTICLE_DOCUMENT_IMAGE_UNSAFE",
            )
        raw = candidate.read_bytes()
        suffix, content_type, width, height = _actual_image_type(raw)
        reference = candidate.relative_to(package_root_resolved).as_posix()
        image["src"] = reference
        image_records.append(
            {
                "converted_reference": reference,
                "image_sha256": hashlib.sha256(raw).hexdigest(),
                "content_type": content_type,
                "suffix": suffix,
                "width": width,
                "height": height,
                "pixels": width * height,
            }
        )
    return str(soup), warnings_payload, image_records


def convert_markdown_to_html(
    path: Path,
    *,
    package_root: Path,
    generated_root: Path,
    budget: ImportExtractionBudget,
    direct_upload: bool = False,
    source_bytes_counted: bool = False,
) -> ConvertedArticleDocument:
    del generated_root  # Markdown images remain package-relative until common validation.
    size = path.stat().st_size
    if size > _limit("ARTICLE_IMPORT_MAX_MARKDOWN_SIZE", MAX_MARKDOWN_SIZE):
        raise DocumentImportError(
            "Markdown 文件超过 5 MB。", code="ARTICLE_DOCUMENT_CONVERSION_FAILED"
        )
    if not source_bytes_counted:
        budget.consume(bytes_=size)
    source_bytes = path.read_bytes()
    try:
        text = source_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentImportError(
            "Markdown 必须使用 UTF-8 或 UTF-8-SIG。",
            code="ARTICLE_MARKDOWN_ENCODING_INVALID",
        ) from exc
    if "\x00" in text:
        raise DocumentImportError(
            "Markdown 不允许 NUL 字节。", code="ARTICLE_MARKDOWN_ENCODING_INVALID"
        )
    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    metadata, body, warnings_payload = _parse_front_matter(normalized_text)
    line_count = body.count("\n") + (1 if body else 0)
    character_count = len(body)
    max_lines = _limit("ARTICLE_IMPORT_MAX_MARKDOWN_LINES", MAX_MARKDOWN_LINES)
    max_characters = _limit(
        "ARTICLE_IMPORT_MAX_MARKDOWN_CHARACTERS", MAX_MARKDOWN_CHARACTERS
    )
    if line_count > max_lines or character_count > max_characters:
        raise DocumentImportError(
            "Markdown 行数或字符数超过安全限制。",
            code="ARTICLE_DOCUMENT_CONVERSION_FAILED",
        )
    warning_characters = _limit(
        "ARTICLE_IMPORT_MARKDOWN_CHARACTERS_WARNING",
        MAX_MARKDOWN_CHARACTERS_WARNING,
    )
    if warning_characters > 0 and character_count > warning_characters:
        warnings_payload.append(
            DocumentConversionWarning(
                "ARTICLE_DOCUMENT_FORMAT_DEGRADED",
                f"Markdown 字符数超过 {warning_characters:,}，预览和转换可能较慢。",
            )
        )
    _preflight_markdown_destinations(body)
    md = MarkdownIt(
        "commonmark", {"html": True, "linkify": False, "typographer": False}
    )
    md.enable(["table", "strikethrough"])
    md.use(footnote_plugin).use(tasklists_plugin)
    try:
        html = md.render(body)
    except Exception as exc:
        raise DocumentImportError(
            "Markdown 转换失败。", code="ARTICLE_DOCUMENT_CONVERSION_FAILED"
        ) from exc
    html, image_warnings, image_records = _rewrite_markdown_images(
        html,
        source_path=path,
        package_root=package_root,
        direct=direct_upload,
    )
    warnings_payload.extend(image_warnings)
    for link in re.findall(r"<a\s+[^>]*href=[\"']([^\"']+)", html, flags=re.I):
        if urlsplit(link).scheme.lower() in {"http", "https", "mailto"}:
            warnings_payload.append(
                DocumentConversionWarning(
                    "ARTICLE_DOCUMENT_EXTERNAL_LINK_PRESENT",
                    "正文包含允许的外部普通链接。",
                )
            )
    try:
        source_path = path.resolve().relative_to(package_root.resolve()).as_posix()
    except ValueError as exc:
        raise DocumentImportError(
            "Markdown 文件不在导入隔离目录中。",
            code="ARTICLE_DOCUMENT_FILE_NOT_FOUND",
        ) from exc
    result = ConvertedArticleDocument(
        source_format=MARKDOWN_FORMAT,
        source_path=source_path,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        converter_name="markdown-it-py",
        converter_version="4.0.0",
        html=html,
        metadata=metadata,
        warnings=warnings_payload,
        statistics={
            "source_bytes": size,
            "line_count": line_count,
            "source_characters": character_count,
            "generated_assets": image_records,
            "total_image_pixels": sum(
                int(record["pixels"]) for record in image_records
            ),
        },
    )
    return _finalize(result)


def convert_article_document(
    path: Path,
    *,
    package_root: Path,
    generated_root: Path,
    budget: ImportExtractionBudget,
    direct_upload: bool = False,
    source_bytes_counted: bool = False,
) -> ConvertedArticleDocument:
    format_name = detect_document_format(path, path.suffix)
    if format_name == DOCX_FORMAT:
        return convert_docx_to_html(
            path,
            package_root=package_root,
            generated_root=generated_root,
            budget=budget,
        )
    return convert_markdown_to_html(
        path,
        package_root=package_root,
        generated_root=generated_root,
        budget=budget,
        direct_upload=direct_upload,
        source_bytes_counted=source_bytes_counted,
    )
