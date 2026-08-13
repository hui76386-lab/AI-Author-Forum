"""Canonical discipline groups for the Top 120 journal directory."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class JournalDisciplineDefinition:
    """A document-defined contiguous range in the Top 120 journal catalogue."""

    code: str
    title: str
    first_sort_order: int
    last_sort_order: int


@dataclass(frozen=True)
class JournalDisciplineGroup:
    """A display-ready discipline group for the journal directory."""

    code: str
    title: str
    anchor: str
    journals: tuple


JOURNAL_DISCIPLINE_DEFINITIONS = (
    JournalDisciplineDefinition(
        code="A",
        title="Artificial Intelligence, Computing, Data Science, and Digital Technology",
        first_sort_order=1,
        last_sort_order=10,
    ),
    JournalDisciplineDefinition(
        code="B",
        title="Medicine, Public Health, and Biomedical Sciences (total of 30)",
        first_sort_order=11,
        last_sort_order=40,
    ),
    JournalDisciplineDefinition(
        code="C",
        title="Biology, Genomics, and Life Sciences",
        first_sort_order=41,
        last_sort_order=50,
    ),
    JournalDisciplineDefinition(
        code="D",
        title="Physical Sciences, Chemistry, Physics, and Astronomy",
        first_sort_order=51,
        last_sort_order=60,
    ),
    JournalDisciplineDefinition(
        code="E",
        title="Engineering, Robotics, Manufacturing, and Infrastructure",
        first_sort_order=61,
        last_sort_order=70,
    ),
    JournalDisciplineDefinition(
        code="F",
        title="Energy, Climate, Environment, and Earth Systems",
        first_sort_order=71,
        last_sort_order=80,
    ),
    JournalDisciplineDefinition(
        code="G",
        title="Agriculture, Food, Veterinary Science, and One Health",
        first_sort_order=81,
        last_sort_order=90,
    ),
    JournalDisciplineDefinition(
        code="H",
        title="Mathematics, Statistics, Economics, and Decision Sciences",
        first_sort_order=91,
        last_sort_order=100,
    ),
    JournalDisciplineDefinition(
        code="I",
        title="Psychology, Education, Social Sciences, and Society",
        first_sort_order=101,
        last_sort_order=110,
    ),
    JournalDisciplineDefinition(
        code="J",
        title="Cross-Disciplinary Grand Challenge AI forums",
        first_sort_order=111,
        last_sort_order=120,
    ),
)


def group_journals_by_discipline(
    journals: Iterable[object],
) -> tuple[JournalDisciplineGroup, ...]:
    """Group journals in the exact Top 120 document order.

    ``Journal.az_group`` is the editor-controlled catalogue group. Historical
    Top 120 records may predate that field being populated correctly, so their
    1-120 ``sort_order`` remains a compatibility fallback. Any record that
    matches neither source is retained in a final group so an editor cannot
    accidentally hide an active journal from the directory.
    """

    grouped = {definition.code: [] for definition in JOURNAL_DISCIPLINE_DEFINITIONS}
    definitions_by_code = {
        definition.code: definition for definition in JOURNAL_DISCIPLINE_DEFINITIONS
    }
    uncategorized = []

    for journal in journals:
        group_code = str(getattr(journal, "az_group", "") or "").strip().upper()
        definition = definitions_by_code.get(group_code)
        if definition is None:
            sort_order = getattr(journal, "sort_order", 0)
            definition = next(
                (
                    candidate
                    for candidate in JOURNAL_DISCIPLINE_DEFINITIONS
                    if candidate.first_sort_order
                    <= sort_order
                    <= candidate.last_sort_order
                ),
                None,
            )
        if definition is None:
            uncategorized.append(journal)
        else:
            grouped[definition.code].append(journal)

    result = tuple(
        JournalDisciplineGroup(
            code=definition.code,
            title=definition.title,
            anchor=f"discipline-{definition.code.lower()}",
            journals=tuple(grouped[definition.code]),
        )
        for definition in JOURNAL_DISCIPLINE_DEFINITIONS
        if grouped[definition.code]
    )
    if uncategorized:
        result += (
            JournalDisciplineGroup(
                code="Other",
                title="Other journals",
                anchor="discipline-other",
                journals=tuple(uncategorized),
            ),
        )
    return result
