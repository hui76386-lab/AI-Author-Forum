from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from pathlib import Path

from django.core.management.base import BaseCommand

from ai_author_forum.journals.models import (
    ArticleImportRow,
    Journal,
    JournalImportRow,
    StaticArticle,
)
from ai_author_forum.journals.validators import detect_suspicious_text

MODEL_FIELDS = {
    Journal: {
        "text": (
            "name",
            "name_cn",
            "slug",
            "az_group",
            "seo_title",
            "seo_description",
            "homepage_intro",
            "hero_kicker",
            "hero_primary_cta_text",
            "hero_primary_cta_url",
            "hero_image_alt",
            "static_site_path",
            "notes",
        ),
        "json": ("hero_quick_links",),
    },
    StaticArticle: {
        "text": (
            "title",
            "slug",
            "authors",
            "ai_co_authors",
            "abstract",
            "keywords",
            "source_html_path",
            "static_output_path",
            "build_version",
            "notes",
        ),
        "json": (),
    },
}
IMPORT_ROW_MODELS = (JournalImportRow, ArticleImportRow)
REPORT_FIELDS = (
    "model",
    "pk",
    "import_row_no",
    "field",
    "rule",
    "severity",
    "message",
    "raw_value",
    "suggestion",
)


class Command(BaseCommand):
    help = "只读扫描导入相关文本，输出可疑字符和乱码特征；不会修改数据库。"

    def add_arguments(self, parser):
        parser.add_argument("--output", help="CSV 输出路径；不提供时输出到标准输出。")

    def handle(self, *args, **options):
        findings = []
        for model, field_map in MODEL_FIELDS.items():
            fields = (*field_map["text"], *field_map["json"])
            queryset = model.objects.only("pk", *fields)
            for obj in queryset.iterator(chunk_size=200):
                for field_name in field_map["text"]:
                    findings.extend(
                        self._find_issues(
                            model._meta.label,
                            obj.pk,
                            field_name,
                            getattr(obj, field_name),
                        )
                    )
                for field_name in field_map["json"]:
                    value = getattr(obj, field_name)
                    raw_value = getattr(value, "raw_data", value)
                    for nested_path, nested_value in self._iter_text_values(
                        raw_value, field_name
                    ):
                        findings.extend(
                            self._find_issues(
                                model._meta.label,
                                obj.pk,
                                nested_path,
                                nested_value,
                            )
                        )

        for model in IMPORT_ROW_MODELS:
            queryset = model.objects.only("pk", "row_no", "raw_data")
            for obj in queryset.iterator(chunk_size=200):
                for field_name, value in self._iter_text_values(
                    obj.raw_data or {}, "raw_data"
                ):
                    rows = self._find_issues(
                        model._meta.label,
                        obj.pk,
                        field_name,
                        value,
                    )
                    for row in rows:
                        row["import_row_no"] = obj.row_no
                    findings.extend(rows)

        output = options.get("output")
        if output:
            path = Path(output).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8-sig", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=REPORT_FIELDS)
                writer.writeheader()
                writer.writerows(findings)
            self.stdout.write(
                self.style.SUCCESS(
                    f"只读审计完成：{len(findings)} 项，报告已写入 {path}"
                )
            )
            return

        writer = csv.DictWriter(self.stdout, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(findings)
        self.stderr.write(f"只读审计完成：{len(findings)} 项。")

    @classmethod
    def _iter_text_values(cls, value, field_path):
        raw_value = getattr(value, "raw_data", value)
        if isinstance(raw_value, Mapping):
            for key, nested_value in raw_value.items():
                key = str(key)
                if key.startswith("_"):
                    continue
                nested_path = f"{field_path}.{key}" if field_path else key
                yield from cls._iter_text_values(nested_value, nested_path)
            return
        if isinstance(raw_value, Sequence) and not isinstance(raw_value, str):
            for index, nested_value in enumerate(raw_value):
                yield from cls._iter_text_values(
                    nested_value,
                    f"{field_path}[{index}]",
                )
            return
        if isinstance(raw_value, str):
            yield field_path, raw_value

    @staticmethod
    def _find_issues(model_label, pk, field_name, value):
        return [
            Command._row(model_label, pk, issue)
            for issue in detect_suspicious_text(value, field_name=field_name)
        ]

    @staticmethod
    def _row(model_label, pk, issue):
        return {
            "model": model_label,
            "pk": pk,
            "import_row_no": "",
            "field": issue.field_name,
            "rule": issue.rule,
            "severity": issue.severity,
            "message": issue.message,
            "raw_value": issue.as_dict()["raw_value"],
            "suggestion": issue.suggestion,
        }
