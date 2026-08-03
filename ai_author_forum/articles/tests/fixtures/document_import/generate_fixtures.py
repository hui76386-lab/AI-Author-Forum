from __future__ import annotations

import csv
import hashlib
import io
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai_author_forum.settings.dev")

import django

django.setup()

from ai_author_forum.articles.tests.test_document_importers import (  # noqa: E402
    build_docx,
    image_bytes,
    image_drawing,
    paragraph,
)

ROOT = Path(__file__).resolve().parent


def build() -> dict[str, str]:
    minimal = build_docx(ROOT / "minimal.docx", body=paragraph("Minimal fixture body"))

    formatted_body = "".join(
        [
            paragraph("Formatted fixture", style="Heading1"),
            paragraph("Strong fixture text", bold=True),
            '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Fixture list item</w:t></w:r></w:p>',
            '<w:p><w:hyperlink r:id="rLink"><w:r><w:t>Fixture link</w:t></w:r></w:hyperlink></w:p>',
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
        ]
    )
    formatted = build_docx(
        ROOT / "formatted.docx",
        body=formatted_body,
        document_rels='<Relationship Id="rLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/fixture" TargetMode="External"/>',
    )

    embedded = build_docx(
        ROOT / "embedded-images.docx",
        body=paragraph("Embedded image fixture") + image_drawing("rImg1"),
        document_rels='<Relationship Id="rImg1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/fixture.png"/>',
        extra_entries={"word/media/fixture.png": image_bytes("PNG")},
    )

    revisions = build_docx(
        ROOT / "revisions.docx",
        body=paragraph("Visible fixture")
        + "<w:p><w:ins><w:r><w:t>Inserted fixture</w:t></w:r></w:ins><w:del><w:r><w:delText>Deleted fixture</w:delText></w:r></w:del></w:p>"
        + "<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>Hidden fixture</w:t></w:r></w:p>",
    )

    unsafe_external = build_docx(
        ROOT / "unsafe-external-image.docx",
        body=paragraph("Unsafe external image fixture"),
        document_rels='<Relationship Id="rUnsafe" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="https://example.invalid/image.png" TargetMode="External"/>',
    )

    unsafe_embedded = build_docx(
        ROOT / "unsafe-embedded-object.docx",
        body=paragraph("Unsafe embedded object fixture"),
        extra_entries={
            "word/embeddings/oleObject1.bin": b"SAFE-PLACEHOLDER-NO-EXECUTABLE-CONTENT"
        },
    )

    valid_md = ROOT / "valid.md"
    valid_md.write_text(
        "# Valid fixture\n\nA paragraph with **emphasis** and a [safe link](https://example.com).\n",
        encoding="utf-8",
    )
    front_matter = ROOT / "front-matter.md"
    front_matter.write_text(
        """---
title: Front Matter Fixture
slug: front-matter-fixture
journal_slug: fixture-journal
article_type: news
authors:
  - Fixture Author
keywords:
  - import
  - fixture
publication_date: 2026-07-29
notes: Generated safe fixture
---
# Front Matter Fixture

Fixture body.
""",
        encoding="utf-8",
    )
    unsafe_html = ROOT / "unsafe-html.md"
    unsafe_html.write_text(
        "# Unsafe HTML fixture\n\n<script>alert('fixture')</script>\n",
        encoding="utf-8",
    )

    package = ROOT / "package-mixed.zip"
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "journal_slug",
            "title",
            "slug",
            "article_type",
            "authors",
            "body_html",
            "html_file",
            "docx_file",
            "markdown_file",
        ]
    )
    writer.writerow(
        [
            "fixture-journal",
            "Fixture DOCX",
            "fixture-docx",
            "news",
            "Fixture Author",
            "",
            "",
            "documents/minimal.docx",
            "",
        ]
    )
    writer.writerow(
        [
            "fixture-journal",
            "Fixture Markdown",
            "fixture-markdown",
            "news",
            "Fixture Author",
            "",
            "",
            "",
            "documents/valid.md",
        ]
    )
    writer.writerow(
        [
            "fixture-journal",
            "Fixture HTML",
            "fixture-html",
            "news",
            "Fixture Author",
            "",
            "documents/body.html",
            "",
            "",
        ]
    )
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("articles.csv", output.getvalue().encode("utf-8-sig"))
        archive.write(minimal, "documents/minimal.docx")
        archive.write(valid_md, "documents/valid.md")
        archive.writestr("documents/body.html", "<h2>Fixture HTML</h2><p>Body.</p>")

    paths = [
        minimal,
        formatted,
        embedded,
        revisions,
        unsafe_external,
        unsafe_embedded,
        valid_md,
        front_matter,
        unsafe_html,
        package,
    ]
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


if __name__ == "__main__":
    for name, digest in build().items():
        print(f"{digest}  {name}")
