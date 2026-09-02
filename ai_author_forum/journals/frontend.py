from django.db.models import Case, IntegerField, Value, When
from django.utils import translation

from ai_author_forum.utils.i18n import ENGLISH_LANGUAGE, normalize_language

from .models import JournalEditorAssignment

ENGLISH_EDITORIAL_ROLE_LABELS = {
    "主编": "Chief Editor",
    "主编辑": "Chief Editor",
    "执行主编": "Executive Editor",
    "常务副编辑": "Executive Editor",
    "副主编": "Associate Editor",
    "副编辑": "Associate Editor",
}


def get_public_editorial_team(journal, *, at=None):
    is_english = normalize_language(translation.get_language()) == ENGLISH_LANGUAGE
    role_order = Case(
        When(role=JournalEditorAssignment.Role.CHIEF_EDITOR, then=Value(0)),
        When(role=JournalEditorAssignment.Role.EXECUTIVE_EDITOR, then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )
    assignments = (
        JournalEditorAssignment.objects.effective(at=at)
        .filter(journal=journal, show_publicly=True)
        .select_related("user")
        .order_by(role_order, "display_order", "public_name", "pk")
    )
    grouped = []
    for role, default_label in JournalEditorAssignment.Role.choices:
        members = [assignment for assignment in assignments if assignment.role == role]
        if members:
            label = members[0].public_role_label or default_label
            grouped.append(
                {
                    "role": role,
                    "label": (
                        ENGLISH_EDITORIAL_ROLE_LABELS.get(label, label)
                        if is_english
                        else label
                    ),
                    "members": members,
                }
            )
    heading = journal.editorial_team_heading
    if is_english and heading == "编辑团队":
        heading = "Editorial team"
    return {
        "heading": heading,
        "groups": grouped,
        "has_members": bool(grouped),
    }
