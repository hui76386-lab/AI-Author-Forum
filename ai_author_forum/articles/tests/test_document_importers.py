from __future__ import annotations

import hashlib
import io
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings
from PIL import Image as PILImage

from ai_author_forum.articles.document_importers import (
    DocumentImportError,
    ImportExtractionBudget,
    convert_article_document,
    detect_document_format,
)

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Default Extension="jpg" ContentType="image/jpeg"/>
  <Default Extension="jpeg" ContentType="image/jpeg"/>
  <Default Extension="gif" ContentType="image/gif"/>
  <Default Extension="webp" ContentType="image/webp"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  {extra}
</Types>
"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>
"""

STYLES = """<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="Heading 2"/></w:style>
  <w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/></w:style>
  <w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/></w:style>
</w:styles>
"""

NUMBERING = """<?xml version="1.0" encoding="UTF-8"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/></w:lvl></w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>
"""

DOCUMENT_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
 <w:body>{body}<w:sectPr/></w:body>
</w:document>
"""

CORE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties
 xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <dc:title>{title}</dc:title>
 <dc:creator>{creator}</dc:creator>
 <cp:lastModifiedBy>{modified_by}</cp:lastModifiedBy>
 <cp:keywords>{keywords}</cp:keywords>
 <dc:subject>{subject}</dc:subject>
 <dc:description>{description}</dc:description>
 <dcterms:created xsi:type="dcterms:W3CDTF">2026-07-01T00:00:00Z</dcterms:created>
 <dcterms:modified xsi:type="dcterms:W3CDTF">2026-07-02T00:00:00Z</dcterms:modified>
</cp:coreProperties>
"""


def paragraph(
    text: str, *, style: str = "", bold: bool = False, italic: bool = False
) -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    run_props = ""
    if bold or italic:
        run_props = (
            f"<w:rPr>{'<w:b/>' if bold else ''}{'<w:i/>' if italic else ''}</w:rPr>"
        )
    return f"<w:p>{style_xml}<w:r>{run_props}<w:t>{text}</w:t></w:r></w:p>"


def image_bytes(image_format: str, *, size: tuple[int, int] = (32, 24)) -> bytes:
    buffer = io.BytesIO()
    mode = "RGB" if image_format.upper() in {"JPEG", "WEBP"} else "RGBA"
    PILImage.new(mode, size, (30, 80, 120)).save(buffer, format=image_format)
    return buffer.getvalue()


def image_drawing(relationship_id: str) -> str:
    return f"""<w:p><w:r><w:drawing><wp:inline><wp:extent cx="100" cy="100"/>
      <a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
        <pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="image"/><pic:cNvPicPr/></pic:nvPicPr>
        <pic:blipFill><a:blip r:embed="{relationship_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
        <pic:spPr><a:xfrm/><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>
      </a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"""


def build_docx(
    path: Path,
    *,
    body: str | None = None,
    core: str | None = None,
    document_rels: str = "",
    extra_entries: dict[str, bytes] | None = None,
    content_type_extra: str = "",
) -> Path:
    if body is None:
        body = paragraph("Minimal body")
    rels = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{document_rels}</Relationships>"""
    core = core or CORE_TEMPLATE.format(
        title="Core title",
        creator="Core author",
        modified_by="Reviewer",
        keywords="alpha, beta",
        subject="Subject abstract",
        description="Description abstract",
    )
    entries = {
        "[Content_Types].xml": CONTENT_TYPES.format(extra=content_type_extra).encode(),
        "_rels/.rels": ROOT_RELS.encode(),
        "word/document.xml": DOCUMENT_TEMPLATE.format(body=body).encode(),
        "word/_rels/document.xml.rels": rels.encode(),
        "word/styles.xml": STYLES.encode(),
        "word/numbering.xml": NUMBERING.encode(),
        "docProps/core.xml": core.encode(),
    }
    entries.update(extra_entries or {})
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return path


class DocumentImporterTests(SimpleTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.generated = self.root / "_converted_assets"

    def convert(self, path: Path, *, direct: bool = False, budget=None):
        return convert_article_document(
            path,
            package_root=self.root,
            generated_root=self.generated,
            budget=budget or ImportExtractionBudget(),
            direct_upload=direct,
        )

    def assert_document_error(self, code: str, callback):
        with self.assertRaises(DocumentImportError) as captured:
            callback()
        self.assertEqual(captured.exception.code, code)
        self.assertNotIn(str(self.root), str(captured.exception))

    def test_minimal_docx_formats_core_metadata_and_allowed_hyperlink(self):
        body = "".join(
            [
                paragraph("Document title", style="Heading1"),
                paragraph("Strong text", bold=True),
                '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>List item</w:t></w:r></w:p>',
                '<w:p><w:hyperlink r:id="rLink"><w:r><w:t>External link</w:t></w:r></w:hyperlink></w:p>',
                "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Cell B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
            ]
        )
        rels = '<Relationship Id="rLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/article" TargetMode="External"/>'
        result = self.convert(
            build_docx(self.root / "minimal.docx", body=body, document_rels=rels)
        )

        self.assertEqual(result.source_format, "docx")
        self.assertIn("<h1>Document title</h1>", result.html)
        self.assertIn("<strong>Strong text</strong>", result.html)
        self.assertIn("<ul>", result.html)
        self.assertIn("<table>", result.html)
        self.assertIn('href="https://example.com/article"', result.html)
        self.assertEqual(result.metadata["title"], "Core title")
        self.assertEqual(result.metadata["authors"], "Core author")
        self.assertEqual(result.metadata["keywords"], "alpha, beta")
        self.assertEqual(result.metadata["abstract"], "Description abstract")
        self.assertNotIn("publication_date", result.metadata)
        self.assertEqual(
            result.statistics["source_metadata"]["last_modified_by"], "Reviewer"
        )
        self.assertGreater(result.statistics["visible_characters"], 0)

    def test_docx_embedded_images_use_actual_type_deterministic_paths_and_no_database(
        self,
    ):
        image_entries = {}
        rels = []
        body = paragraph("Images")
        formats = [("JPEG", "jpg"), ("PNG", "png"), ("WEBP", "webp"), ("GIF", "gif")]
        for index, (image_format, suffix) in enumerate(formats, start=1):
            relationship_id = f"rImg{index}"
            body += image_drawing(relationship_id)
            image_entries[f"word/media/source-{index}.{suffix}"] = image_bytes(
                image_format
            )
            rels.append(
                f'<Relationship Id="{relationship_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/source-{index}.{suffix}"/>'
            )
        result = self.convert(
            build_docx(
                self.root / "images.docx",
                body=body,
                document_rels="".join(rels),
                extra_entries=image_entries,
            )
        )

        self.assertEqual(len(result.generated_assets), 4)
        for index, reference in enumerate(result.generated_assets, start=1):
            self.assertRegex(
                reference,
                rf"^_converted_assets/[0-9a-f]{{64}}/{index:04d}\.(?:jpg|png|webp|gif)$",
            )
            self.assertTrue((self.root / reference).is_file())
        records = result.statistics["generated_assets"]
        self.assertEqual(
            [item["relationship_id"] for item in records],
            ["rImg1", "rImg2", "rImg3", "rImg4"],
        )
        self.assertTrue(all(len(item["image_sha256"]) == 64 for item in records))
        self.assertEqual(result.statistics["image_count"], 4)
        self.assertEqual(result.statistics["total_image_pixels"], 4 * 32 * 24)

    def test_docx_revision_comment_and_hidden_text_produce_warning(self):
        body = (
            paragraph("Visible")
            + "<w:p><w:ins><w:r><w:t>Inserted</w:t></w:r></w:ins><w:del><w:r><w:delText>Deleted</w:delText></w:r></w:del></w:p>"
            + "<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>Hidden</w:t></w:r></w:p>"
            + '<w:commentRangeStart w:id="0"/><w:commentRangeEnd w:id="0"/>'
        )
        result = self.convert(build_docx(self.root / "revisions.docx", body=body))
        self.assertIn(
            "ARTICLE_DOCX_REVISIONS_PRESENT",
            [warning.code for warning in result.warnings],
        )
        self.assertNotIn("Deleted", result.html)

    def test_docx_rejects_unsafe_members_and_content_types(self):
        cases = [
            ({"word/vbaProject.bin": b"placeholder"}, "", "ARTICLE_DOCX_MACRO_UNSAFE"),
            (
                {"word/activeX/activeX1.bin": b"placeholder"},
                "",
                "ARTICLE_DOCX_MACRO_UNSAFE",
            ),
            (
                {"word/embeddings/oleObject1.bin": b"placeholder"},
                "",
                "ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE",
            ),
            (
                {"word/payload.exe": b"placeholder"},
                "",
                "ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE",
            ),
            (
                {},
                '<Override PartName="/word/activeX/activeX1.xml" ContentType="application/vnd.ms-office.activeX+xml"/>',
                "ARTICLE_DOCX_MACRO_UNSAFE",
            ),
            (
                {},
                '<Override PartName="/word/embeddings/oleObject1.bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>',
                "ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE",
            ),
        ]
        for index, (entries, content_types, code) in enumerate(cases):
            with self.subTest(index=index, code=code):
                path = build_docx(
                    self.root / f"unsafe-{index}.docx",
                    extra_entries=entries,
                    content_type_extra=content_types,
                )
                self.assert_document_error(code, lambda path=path: self.convert(path))

    def test_docx_rejects_altchunk_ole_and_unsafe_external_relationships(self):
        unsafe_bodies = [
            paragraph("Body") + '<w:altChunk r:id="rUnsafe"/>',
            paragraph("Body") + "<w:object><w:t>Object</w:t></w:object>",
        ]
        for index, body in enumerate(unsafe_bodies):
            path = build_docx(self.root / f"unsafe-element-{index}.docx", body=body)
            self.assert_document_error(
                "ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE",
                lambda path=path: self.convert(path),
            )

        relation_types = [
            ("image", "https://example.com/image.png"),
            ("attachedTemplate", "https://example.com/template.dotx"),
            ("oleObject", "file:///C:/outside.bin"),
            ("package", "file:///C:/outside.zip"),
        ]
        for index, (relation_type, target) in enumerate(relation_types):
            rels = (
                f'<Relationship Id="rUnsafe" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{relation_type}" '
                f'Target="{target}" TargetMode="External"/>'
            )
            path = build_docx(
                self.root / f"unsafe-rel-{index}.docx", document_rels=rels
            )
            self.assert_document_error(
                "ARTICLE_DOCX_EXTERNAL_RELATIONSHIP_UNSAFE",
                lambda path=path: self.convert(path),
            )

    def test_docx_rejects_invalid_encrypted_traversal_symlink_and_duplicate_paths(self):
        bad = self.root / "bad.docx"
        bad.write_bytes(b"PK not a zip")
        self.assert_document_error(
            "ARTICLE_DOCX_INVALID_PACKAGE", lambda: self.convert(bad)
        )

        encrypted = self.root / "encrypted.docx"
        encrypted.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1placeholder")
        self.assert_document_error(
            "ARTICLE_DOCX_ENCRYPTED",
            lambda: detect_document_format(encrypted, ".docx"),
        )

        traversal = self.root / "traversal.docx"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("[Content_Types].xml", CONTENT_TYPES.format(extra=""))
            archive.writestr(
                "word/document.xml", DOCUMENT_TEMPLATE.format(body=paragraph("Body"))
            )
            archive.writestr("../outside.xml", "<x/>")
        self.assert_document_error(
            "ARTICLE_DOCX_INVALID_PACKAGE", lambda: self.convert(traversal)
        )

        symlink = build_docx(self.root / "symlink.docx")
        with zipfile.ZipFile(symlink, "a") as archive:
            info = zipfile.ZipInfo("word/link.xml")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, b"target")
        self.assert_document_error(
            "ARTICLE_DOCX_INVALID_PACKAGE", lambda: self.convert(symlink)
        )

        duplicate = self.root / "duplicate.docx"
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("[Content_Types].xml", CONTENT_TYPES.format(extra=""))
            archive.writestr(
                "word/document.xml", DOCUMENT_TEMPLATE.format(body=paragraph("Body"))
            )
            archive.writestr(
                "WORD/DOCUMENT.XML", DOCUMENT_TEMPLATE.format(body=paragraph("Other"))
            )
        self.assert_document_error(
            "ARTICLE_DOCX_INVALID_PACKAGE", lambda: self.convert(duplicate)
        )

    def test_docx_xml_dtd_node_depth_member_and_shared_budget_limits(self):
        dtd = b'<?xml version="1.0"?><!DOCTYPE w:document [<!ENTITY x SYSTEM "file:///etc/passwd">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>'
        path = build_docx(
            self.root / "dtd.docx",
            extra_entries={"word/document.xml": dtd},
        )
        self.assert_document_error(
            "ARTICLE_DOCX_INVALID_PACKAGE", lambda: self.convert(path)
        )

        with override_settings(ARTICLE_IMPORT_MAX_XML_NODES=4):
            path = build_docx(self.root / "nodes.docx")
            self.assert_document_error(
                "ARTICLE_DOCX_LIMIT_EXCEEDED", lambda: self.convert(path)
            )
        with override_settings(ARTICLE_IMPORT_MAX_XML_DEPTH=3):
            path = build_docx(self.root / "depth.docx")
            self.assert_document_error(
                "ARTICLE_DOCX_LIMIT_EXCEEDED", lambda: self.convert(path)
            )
        with override_settings(ARTICLE_IMPORT_MAX_DOCX_MEMBERS=3):
            path = build_docx(self.root / "members.docx")
            self.assert_document_error(
                "ARTICLE_DOCX_LIMIT_EXCEEDED", lambda: self.convert(path)
            )

        path = build_docx(self.root / "budget.docx")
        budget = ImportExtractionBudget(max_total_bytes=10, max_nested_members=20_000)
        self.assert_document_error(
            "ARTICLE_DOCX_LIMIT_EXCEEDED", lambda: self.convert(path, budget=budget)
        )
        budget = ImportExtractionBudget(
            max_total_bytes=250 * 1024 * 1024, max_nested_members=2
        )
        self.assert_document_error(
            "ARTICLE_DOCX_LIMIT_EXCEEDED", lambda: self.convert(path, budget=budget)
        )

    def test_docx_extension_and_content_mismatch_and_empty_body(self):
        markdown_named_docx = self.root / "wrong.docx"
        markdown_named_docx.write_text("# Markdown", encoding="utf-8")
        self.assert_document_error(
            "ARTICLE_DOCUMENT_MIME_MISMATCH",
            lambda: detect_document_format(markdown_named_docx, ".docx"),
        )
        empty = build_docx(self.root / "empty.docx", body="")
        self.assert_document_error(
            "ARTICLE_DOCUMENT_BODY_EMPTY", lambda: self.convert(empty)
        )

    def write_markdown(self, name: str, content: str | bytes) -> Path:
        path = self.root / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def test_markdown_utf8_sig_front_matter_all_fields_and_commonmark_plugins(self):
        markdown = """---
title: Front title
slug: front-title
journal_slug: journal-one
article_type: news
authors: [Alice, Bob]
ai_co_authors: AI Assistant
abstract: Abstract
keywords: [one, two]
publication_date: 2026-07-29
cover_image: media/cover.png
primary_category_code: policy
related_category_codes: [research]
notes: Notes
unknown_key: ignored
status: published
---
# Front title

- list item

| A | B |
|---|---|
| 1 | 2 |

Footnote[^1].

[^1]: note

- [x] done

~~~python
print("safe")
~~~
"""
        path = self.write_markdown("front.md", b"\xef\xbb\xbf" + markdown.encode())
        result = self.convert(path)
        self.assertEqual(result.metadata["title"], "Front title")
        self.assertEqual(result.metadata["authors"], ["Alice", "Bob"])
        self.assertEqual(str(result.metadata["publication_date"]), "2026-07-29")
        self.assertNotIn("unknown_key", result.metadata)
        self.assertNotIn("status", result.metadata)
        codes = [warning.code for warning in result.warnings]
        self.assertIn("ARTICLE_DOCUMENT_UNKNOWN_METADATA_IGNORED", codes)
        self.assertIn("ARTICLE_DOCUMENT_FORBIDDEN_METADATA_IGNORED", codes)
        self.assertIn("<table>", result.html)
        self.assertIn("footnote", result.html)
        self.assertIn('type="checkbox"', result.html)
        self.assertIn('<code class="language-python">', result.html)

    def test_markdown_rejects_encoding_nul_and_unsafe_front_matter(self):
        cases = [
            ("latin.md", b"caf\xff", "ARTICLE_MARKDOWN_ENCODING_INVALID"),
            ("nul.md", b"Body\x00text", "ARTICLE_MARKDOWN_ENCODING_INVALID"),
            (
                "duplicate.md",
                b"---\ntitle: one\ntitle: two\n---\nBody",
                "ARTICLE_MARKDOWN_FRONT_MATTER_INVALID",
            ),
            (
                "tag.md",
                b"---\ntitle: !!python/object/apply:os.system [echo]\n---\nBody",
                "ARTICLE_MARKDOWN_FRONT_MATTER_INVALID",
            ),
            (
                "anchor.md",
                b"---\na: &a [x]\nb: *a\n---\nBody",
                "ARTICLE_MARKDOWN_FRONT_MATTER_INVALID",
            ),
            (
                "top.md",
                b"---\n- one\n- two\n---\nBody",
                "ARTICLE_MARKDOWN_FRONT_MATTER_INVALID",
            ),
        ]
        for name, payload, code in cases:
            with self.subTest(name=name):
                path = self.write_markdown(name, payload)
                self.assert_document_error(code, lambda path=path: self.convert(path))

    def test_markdown_front_matter_size_key_and_depth_limits(self):
        too_many = (
            "---\n" + "\n".join(f"key{i}: value" for i in range(51)) + "\n---\nBody"
        )
        deep = (
            "---\na:\n  b:\n    c:\n      d:\n        e:\n          f: value\n---\nBody"
        )
        large = "---\ntitle: " + ("x" * (64 * 1024)) + "\n---\nBody"
        for index, payload in enumerate((too_many, deep, large)):
            path = self.write_markdown(f"front-limit-{index}.md", payload)
            self.assert_document_error(
                "ARTICLE_MARKDOWN_FRONT_MATTER_INVALID",
                lambda path=path: self.convert(path),
            )

    def test_markdown_relative_image_is_validated_and_direct_upload_requires_zip(self):
        media = self.root / "media"
        media.mkdir()
        (media / "figure.png").write_bytes(image_bytes("PNG"))
        path = self.write_markdown("article.md", "Body\n\n![Figure](media/figure.png)")
        result = self.convert(path)
        self.assertIn('src="media/figure.png"', result.html)
        self.assertEqual(result.statistics["image_count"], 1)
        self.assertEqual(result.statistics["total_image_pixels"], 32 * 24)
        self.assertEqual(
            result.statistics["generated_assets"][0]["content_type"], "image/png"
        )

        self.assert_document_error(
            "ARTICLE_MARKDOWN_LOCAL_IMAGE_REQUIRES_ZIP",
            lambda: self.convert(path, direct=True),
        )

    def test_markdown_rejects_remote_data_file_absolute_unc_query_fragment_and_traversal_images(
        self,
    ):
        sources = [
            "https://example.com/a.png",
            "//example.com/a.png",
            "data:image/png;base64,AAAA",
            "file:///C:/a.png",
            "/var/tmp/a.png",
            "C:/a.png",
            r"\\server\share\a.png",
            "media/a.png?x=1",
            "media/a.png#fragment",
            "../outside.png",
            "%2e%2e/outside.png",
        ]
        for index, source in enumerate(sources):
            path = self.write_markdown(
                f"unsafe-image-{index}.md", f"Body\n\n![x]({source})"
            )
            self.assert_document_error(
                "ARTICLE_DOCUMENT_IMAGE_UNSAFE", lambda path=path: self.convert(path)
            )

    def test_markdown_rejects_missing_invalid_and_oversized_images(self):
        missing = self.write_markdown(
            "missing-image.md", "Body\n\n![x](media/missing.png)"
        )
        self.assert_document_error(
            "ARTICLE_DOCUMENT_IMAGE_UNSAFE", lambda: self.convert(missing)
        )

        media = self.root / "media"
        media.mkdir(exist_ok=True)
        (media / "invalid.png").write_text("not image", encoding="utf-8")
        invalid = self.write_markdown(
            "invalid-image.md", "Body\n\n![x](media/invalid.png)"
        )
        self.assert_document_error(
            "ARTICLE_DOCUMENT_IMAGE_UNSAFE", lambda: self.convert(invalid)
        )

        (media / "large.png").write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        large = self.write_markdown("large-image.md", "Body\n\n![x](media/large.png)")
        self.assert_document_error(
            "ARTICLE_DOCUMENT_IMAGE_UNSAFE", lambda: self.convert(large)
        )

    def test_markdown_empty_size_lines_characters_html_visible_and_image_limits(self):
        empty = self.write_markdown("empty.md", "   ")
        self.assert_document_error(
            "ARTICLE_DOCUMENT_BODY_EMPTY", lambda: self.convert(empty)
        )

        with override_settings(ARTICLE_IMPORT_MAX_MARKDOWN_LINES=2):
            path = self.write_markdown("lines.md", "one\ntwo\nthree")
            self.assert_document_error(
                "ARTICLE_DOCUMENT_CONVERSION_FAILED", lambda: self.convert(path)
            )
        with override_settings(ARTICLE_IMPORT_MAX_MARKDOWN_CHARACTERS=5):
            path = self.write_markdown("chars.md", "123456")
            self.assert_document_error(
                "ARTICLE_DOCUMENT_CONVERSION_FAILED", lambda: self.convert(path)
            )
        with override_settings(ARTICLE_IMPORT_MAX_CONVERTED_HTML_SIZE=10):
            path = self.write_markdown("html.md", "A paragraph that expands")
            self.assert_document_error(
                "ARTICLE_DOCUMENT_HTML_TOO_LARGE", lambda: self.convert(path)
            )
        with override_settings(ARTICLE_IMPORT_MAX_VISIBLE_CHARACTERS=5):
            path = self.write_markdown("visible.md", "123456")
            self.assert_document_error(
                "ARTICLE_DOCUMENT_CONVERSION_FAILED", lambda: self.convert(path)
            )

        media = self.root / "many"
        media.mkdir()
        (media / "a.png").write_bytes(image_bytes("PNG"))
        (media / "b.png").write_bytes(image_bytes("PNG"))
        with override_settings(ARTICLE_IMPORT_MAX_IMAGES=1):
            path = self.write_markdown(
                "many-images.md", "Body\n![a](many/a.png)\n![b](many/b.png)"
            )
            self.assert_document_error(
                "ARTICLE_DOCUMENT_IMAGE_UNSAFE", lambda: self.convert(path)
            )
        with override_settings(ARTICLE_IMPORT_MAX_TOTAL_IMAGE_PIXELS=100):
            path = self.write_markdown("pixels.md", "Body\n![a](many/a.png)")
            self.assert_document_error(
                "ARTICLE_DOCUMENT_IMAGE_UNSAFE", lambda: self.convert(path)
            )

    def test_markdown_warning_threshold_external_link_raw_html_and_fenced_code_are_non_executable(
        self,
    ):
        with override_settings(
            ARTICLE_IMPORT_MARKDOWN_CHARACTERS_WARNING=5,
            ARTICLE_IMPORT_VISIBLE_CHARACTERS_WARNING=5,
        ):
            path = self.write_markdown(
                "warning.md",
                "# Heading\n\n[Allowed](mailto:test@example.com)\n\n<script>alert(1)</script>\n\npython\nprint('x')\n",
            )
            # Replace DEL placeholders with backticks without embedding a Markdown fence in this source file.
            path.write_text(
                path.read_text(encoding="utf-8").replace("", "`"), encoding="utf-8"
            )
            result = self.convert(path)
        codes = [warning.code for warning in result.warnings]
        self.assertIn("ARTICLE_DOCUMENT_EXTERNAL_LINK_PRESENT", codes)
        self.assertGreaterEqual(codes.count("ARTICLE_DOCUMENT_FORMAT_DEGRADED"), 1)
        self.assertIn("<script>alert(1)</script>", result.html)
        self.assertIn('<code class="language-python">', result.html)
        self.assertNotIn("subprocess", result.html)

    def test_document_format_detection_rejects_unsupported_and_mismatch(self):
        unsupported = self.write_markdown("article.pdf", "Body")
        self.assert_document_error(
            "ARTICLE_DOCUMENT_FORMAT_UNSUPPORTED",
            lambda: detect_document_format(unsupported, ".pdf"),
        )
        docx = build_docx(self.root / "package.docx")
        markdown = self.root / "package.md"
        markdown.write_bytes(docx.read_bytes())
        self.assert_document_error(
            "ARTICLE_DOCUMENT_MIME_MISMATCH",
            lambda: detect_document_format(markdown, ".md"),
        )


class DocumentImportFixtureTests(SimpleTestCase):
    fixture_source = Path(__file__).parent / "fixtures" / "document_import"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name) / "document_import"
        shutil.copytree(self.fixture_source, self.root)
        self.generated = self.root / "_converted_assets"

    def convert(self, name: str):
        return convert_article_document(
            self.root / name,
            package_root=self.root,
            generated_root=self.generated,
            budget=ImportExtractionBudget(),
        )

    def test_delivered_fixture_set_exists_and_matches_documented_sha256(self):
        readme = (self.fixture_source / "README.md").read_text(encoding="utf-8")
        expected_names = {
            "minimal.docx",
            "formatted.docx",
            "embedded-images.docx",
            "revisions.docx",
            "unsafe-external-image.docx",
            "unsafe-embedded-object.docx",
            "valid.md",
            "front-matter.md",
            "unsafe-html.md",
            "package-mixed.zip",
        }
        documented = dict(
            re.findall(
                r"\| \x60([^\x60]+)\x60 \|[^\n]+\| \x60([0-9a-f]{64})\x60 \|",
                readme,
            )
        )
        self.assertEqual(set(documented), expected_names)
        for name, expected_digest in documented.items():
            with self.subTest(name=name):
                fixture = self.fixture_source / name
                self.assertTrue(fixture.is_file())
                self.assertEqual(
                    hashlib.sha256(fixture.read_bytes()).hexdigest(),
                    expected_digest,
                )

    def test_safe_docx_and_markdown_fixtures_convert_with_expected_evidence(self):
        minimal = self.convert("minimal.docx")
        self.assertEqual(minimal.source_format, "docx")
        self.assertIn("Minimal fixture body", minimal.html)

        formatted = self.convert("formatted.docx")
        self.assertIn("<h1>", formatted.html)
        self.assertIn("<strong>", formatted.html)
        self.assertIn("<table>", formatted.html)

        embedded = self.convert("embedded-images.docx")
        self.assertEqual(embedded.statistics["image_count"], 1)
        self.assertEqual(len(embedded.generated_assets), 1)
        self.assertTrue((self.root / embedded.generated_assets[0]).is_file())

        revisions = self.convert("revisions.docx")
        warning_codes = {warning.code for warning in revisions.warnings}
        self.assertIn("ARTICLE_DOCX_REVISIONS_PRESENT", warning_codes)
        self.assertIn("ARTICLE_DOCUMENT_UNSUPPORTED_ELEMENT", warning_codes)

        markdown = self.convert("valid.md")
        self.assertEqual(markdown.source_format, "markdown")
        self.assertIn("<strong>emphasis</strong>", markdown.html)

        front_matter = self.convert("front-matter.md")
        self.assertEqual(front_matter.metadata["title"], "Front Matter Fixture")

    def test_unsafe_docx_fixtures_are_rejected_by_expected_structural_checks(self):
        cases = {
            "unsafe-external-image.docx": "ARTICLE_DOCX_EXTERNAL_RELATIONSHIP_UNSAFE",
            "unsafe-embedded-object.docx": "ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE",
        }
        for name, code in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(DocumentImportError) as captured:
                    self.convert(name)
                self.assertEqual(captured.exception.code, code)
                self.assertNotIn(str(self.root), str(captured.exception))
