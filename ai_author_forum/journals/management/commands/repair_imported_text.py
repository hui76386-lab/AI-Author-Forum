from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ai_author_forum.journals.models import (
    ArticleImportRow,
    Journal,
    JournalImportRow,
    StaticArticle,
)
from ai_author_forum.site_settings.access_control import is_super_admin
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event

ALLOWED_FIELDS = {
    "journals.Journal": {
        "name",
        "name_cn",
        "seo_title",
        "seo_description",
        "homepage_intro",
        "notes",
    },
    "journals.StaticArticle": {
        "title",
        "abstract",
        "authors",
        "keywords",
        "body",
        "notes",
    },
    "journals.JournalImportRow": {"raw_data"},
    "journals.ArticleImportRow": {"raw_data"},
}
MODELS = {
    "journals.Journal": Journal,
    "journals.StaticArticle": StaticArticle,
    "journals.JournalImportRow": JournalImportRow,
    "journals.ArticleImportRow": ArticleImportRow,
}


class Command(BaseCommand):
    help = "按可信人工映射修复导入文本。默认 dry-run，只有 --apply 才写库并生成备份与审计。"

    def add_arguments(self, parser):
        parser.add_argument("--mapping", required=True, help="可信 JSON 映射文件。")
        parser.add_argument(
            "--apply", action="store_true", help="实际写库；默认只预览。"
        )
        parser.add_argument(
            "--backup", help="备份 JSON 路径；apply 时默认在映射文件旁生成。"
        )
        parser.add_argument(
            "--operator-id", type=int, help="执行 apply 的超级管理员用户 ID。"
        )

    def handle(self, *args, **options):
        mapping_path = Path(options["mapping"]).expanduser().resolve()
        try:
            payload = json.loads(mapping_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"无法读取映射文件：{exc}") from exc
        entries = payload.get("entries") if isinstance(payload, dict) else payload
        if not isinstance(entries, list) or not entries:
            raise CommandError("映射文件必须包含非空 entries 数组。")
        operator = self._operator(options.get("operator_id"), apply=options["apply"])
        plan = [self._resolve(item) for item in entries]
        for item in plan:
            self.stdout.write(
                f"{item['model']}#{item['pk']} {item['field']}: {item['expected']!r} -> {item['replacement']!r}"
            )
        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    f"dry-run 完成：计划修复 {len(plan)} 项，数据库未修改。"
                )
            )
            return
        backup_path = (
            Path(
                options.get("backup")
                or mapping_path.with_name(
                    f"{mapping_path.stem}-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
                )
            )
            .expanduser()
            .resolve()
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(
            json.dumps({"entries": plan}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with transaction.atomic():
            for item in plan:
                self._apply(item)
            record_audit_event(
                actor=operator,
                action=AuditAction.IMPORT,
                status=AuditStatus.SUCCESS,
                target_type="ImportedTextRepair",
                target_label=mapping_path.name,
                message="按可信映射修复导入文本",
                metadata={
                    "operation": "repair_imported_text",
                    "mapping": str(mapping_path),
                    "backup": str(backup_path),
                    "count": len(plan),
                    "rows": plan,
                },
            )
        self.stdout.write(
            self.style.SUCCESS(f"已修复 {len(plan)} 项；备份：{backup_path}")
        )

    def _operator(self, user_id, *, apply):
        if not apply:
            return None
        if not user_id:
            raise CommandError("--apply 必须提供 --operator-id。")
        try:
            user = get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist as exc:
            raise CommandError("operator 不存在。") from exc
        if not is_super_admin(user):
            raise CommandError("只有超级管理员可执行 apply。")
        return user

    def _resolve(self, item):
        if not isinstance(item, dict):
            raise CommandError("每条映射必须是对象。")
        model_label = item.get("model")
        field = item.get("field")
        pk = item.get("pk")
        model = MODELS.get(model_label)
        root_field = str(field or "").split(".", 1)[0]
        if model is None or root_field not in ALLOWED_FIELDS.get(model_label, set()):
            raise CommandError(f"不允许修复字段：{model_label}.{field}")
        try:
            obj = model.objects.get(pk=pk)
        except model.DoesNotExist as exc:
            raise CommandError(f"对象不存在：{model_label}#{pk}") from exc
        current = getattr(obj, root_field)
        if "." in str(field):
            _, key = str(field).split(".", 1)
            if root_field != "raw_data":
                raise CommandError(f"不支持嵌套字段：{field}")
            current = (current or {}).get(key)
        expected = item.get("expected")
        if current != expected:
            raise CommandError(f"原值不匹配，拒绝修复：{model_label}#{pk}.{field}")
        replacement = item.get("replacement")
        if not isinstance(replacement, str):
            raise CommandError("replacement 必须是人工确认后的字符串。")
        return {
            "model": model_label,
            "pk": pk,
            "field": field,
            "expected": expected,
            "replacement": replacement,
        }

    def _apply(self, item):
        model = MODELS[item["model"]]
        obj = model.objects.select_for_update().get(pk=item["pk"])
        root_field = item["field"].split(".", 1)[0]
        if "." in item["field"]:
            _, key = item["field"].split(".", 1)
            data = dict(obj.raw_data or {})
            if data.get(key) != item["expected"]:
                raise CommandError("事务执行时原值发生变化，已回滚。")
            data[key] = item["replacement"]
            obj.raw_data = data
        else:
            if getattr(obj, root_field) != item["expected"]:
                raise CommandError("事务执行时原值发生变化，已回滚。")
            setattr(obj, root_field, item["replacement"])
        obj.save(
            update_fields=(
                (root_field, "updated_at")
                if hasattr(obj, "updated_at")
                else (root_field,)
            )
        )
