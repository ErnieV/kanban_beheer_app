"""Plan and execute the one-time legacy Min/Max cutover."""

from collections import Counter
from dataclasses import dataclass
import datetime

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    inspect,
    literal,
    select,
    text,
    update,
)

from kanban_domain import KanbanStandard, Materiaaltype


MIGRATION_NAME = "legacy-max-to-kanban-standard-v1"


class MigrationError(RuntimeError):
    """Raised when the database cannot safely run the migration."""


@dataclass(frozen=True)
class LegacyPositionRecord:
    position_id: int
    article_id: int
    article_name: str
    storage_location_name: str
    material_type: str | None
    legacy_min: int | None
    legacy_max: int | None
    min_override: int | None = None
    refill_override: int | None = None


@dataclass(frozen=True)
class LegacyQueueRecord:
    print_id: int
    min_level: int | None
    max_level: int | None
    refill_quantity: int | None = None


@dataclass(frozen=True)
class PositionMigrationUpdate:
    position_id: int
    material_type: str
    strategy: str
    min_override: int | None
    refill_override: int | None


@dataclass(frozen=True)
class QueueMigrationUpdate:
    print_id: int
    refill_quantity: int


@dataclass(frozen=True)
class MigrationIssue:
    article_name: str
    storage_location_name: str
    position_id: int | None
    reason: str

    def as_text(self):
        location = self.storage_location_name or "Onbekende Opslaglocatie"
        position = (
            f"Voorraadpositie {self.position_id}"
            if self.position_id is not None
            else "Printopdracht"
        )
        return f"- Artikel {self.article_name} | {location} | {position}: {self.reason}"


@dataclass(frozen=True)
class MigrationPlan:
    article_standards: tuple[tuple[int, KanbanStandard], ...]
    position_updates: tuple[PositionMigrationUpdate, ...]
    queue_updates: tuple[QueueMigrationUpdate, ...]
    issues: tuple[MigrationIssue, ...]

    @property
    def invalid_report(self):
        if not self.issues:
            return "Geen ongeldige legacywaarden gevonden."
        return "\n".join(issue.as_text() for issue in self.issues)


@dataclass(frozen=True)
class MigrationResult:
    success: bool
    blocked: bool
    already_completed: bool
    plan: MigrationPlan

    @property
    def invalid_report(self):
        return self.plan.invalid_report


def select_article_standard(pairs):
    """Select the deterministic winning (Min, Aanv.) pair for one Artikel."""
    counts = Counter(tuple(pair) for pair in pairs)
    if not counts:
        return None

    winning_pair = min(
        counts,
        key=lambda pair: (
            -counts[pair],
            sum(pair),
            pair[0],
            pair[1],
        ),
    )
    return KanbanStandard(*winning_pair)


def build_migration_plan(position_rows, queue_rows):
    """Validate legacy rows and build all updates without changing storage."""
    issues = []
    candidates = []
    for row in position_rows:
        material_type = _normalized_material_type(row.material_type)
        if material_type is Materiaaltype.STANDAARD:
            continue
        if material_type is None:
            issues.append(_position_issue(row, "Onbekend materiaaltype."))
            continue

        old_min = _integer_or_none(row.legacy_min)
        old_max = _integer_or_none(row.legacy_max)
        if old_min is None and old_max is None:
            if (
                row.material_type
                or row.min_override is not None
                or row.refill_override is not None
            ):
                continue
            issues.append(_position_issue(row, "Oude Min en Max ontbreken."))
            continue
        if old_min is None or old_max is None:
            issues.append(_position_issue(row, "Oude Min of Max ontbreekt."))
            continue
        if old_min < 0:
            issues.append(_position_issue(row, "Min moet minimaal 0 zijn."))
            continue
        if old_max <= old_min:
            issues.append(
                _position_issue(row, "Max moet groter zijn dan Min.")
            )
            continue

        candidates.append((row, old_min, old_max - old_min))

    queue_updates = []
    for row in queue_rows:
        old_min = _integer_or_none(row.min_level)
        old_max = _integer_or_none(row.max_level)
        if old_max is None:
            if row.refill_quantity is not None:
                continue
            issues.append(MigrationIssue(
                article_name=f"Printopdracht {row.print_id}",
                storage_location_name="Printwachtrij",
                position_id=None,
                reason="Oude Max ontbreekt.",
            ))
            continue
        if old_min is None:
            issues.append(MigrationIssue(
                article_name=f"Printopdracht {row.print_id}",
                storage_location_name="Printwachtrij",
                position_id=None,
                reason="Oude Min ontbreekt.",
            ))
            continue
        if old_min < 0:
            issues.append(MigrationIssue(
                article_name=f"Printopdracht {row.print_id}",
                storage_location_name="Printwachtrij",
                position_id=None,
                reason="Min moet minimaal 0 zijn.",
            ))
            continue
        if old_max <= old_min:
            issues.append(MigrationIssue(
                article_name=f"Printopdracht {row.print_id}",
                storage_location_name="Printwachtrij",
                position_id=None,
                reason="Max moet groter zijn dan Min.",
            ))
            continue
        queue_updates.append(QueueMigrationUpdate(
            print_id=row.print_id,
            refill_quantity=old_max - old_min,
        ))

    grouped = {}
    for row, old_min, refill_quantity in candidates:
        grouped.setdefault(row.article_id, []).append(
            (old_min, refill_quantity)
        )

    standards = {
        article_id: select_article_standard(pairs)
        for article_id, pairs in grouped.items()
    }
    position_updates = []
    for row, old_min, refill_quantity in candidates:
        standard = standards[row.article_id]
        is_deviation = (old_min, refill_quantity) != (
            standard.min_level,
            standard.refill_quantity,
        )
        position_updates.append(PositionMigrationUpdate(
            position_id=row.position_id,
            material_type=Materiaaltype.KANBAN.value,
            strategy="TWO_BIN",
            min_override=old_min if is_deviation else None,
            refill_override=refill_quantity if is_deviation else None,
        ))

    return MigrationPlan(
        article_standards=tuple(sorted(standards.items())),
        position_updates=tuple(sorted(
            position_updates,
            key=lambda item: item.position_id,
        )),
        queue_updates=tuple(sorted(
            queue_updates,
            key=lambda item: item.print_id,
        )),
        issues=tuple(issues),
    )


def migrate_legacy_max(engine):
    """Run the guarded, repeatable database cutover on ``engine``."""
    with engine.begin() as connection:
        marker = _migration_marker_table()
        marker.create(connection, checkfirst=True)
        if _migration_was_completed(connection, marker):
            return MigrationResult(
                success=True,
                blocked=False,
                already_completed=True,
                plan=_empty_plan(),
            )

        position_table = _required_table(connection, "Voorraad_Positie")
        article_table = _required_table(connection, "Lokaal_Artikel")
        _require_columns(
            article_table,
            ("lokaal_artikel_id", "kanban_min", "kanban_refill_quantity"),
        )
        _require_columns(
            position_table,
            (
                "voorraad_positie_id",
                "lokaal_artikel_id",
                "kast_id",
                "materiaaltype",
                "strategie",
                "kanban_min_override",
                "kanban_refill_quantity_override",
            ),
        )

        queue_table = _optional_table(connection, "Print_Queue")
        position_legacy_columns = {
            name for name in ("trigger_min", "target_max")
            if name in position_table.c
        }
        if position_legacy_columns not in (set(), {"trigger_min", "target_max"}):
            raise MigrationError(
                "Voorraad_Positie bevat slechts een deel van de legacykolommen."
            )
        queue_has_legacy_max = (
            queue_table is not None and "max_level" in queue_table.c
        )
        if queue_has_legacy_max:
            _require_columns(
                queue_table,
                ("print_id", "min_level", "refill_quantity", "max_level"),
            )

        if not position_legacy_columns and not queue_has_legacy_max:
            plan = _empty_plan()
            _write_migration_marker(connection, marker)
            return MigrationResult(True, False, False, plan)

        kast_table = _optional_table(connection, "Kast")
        position_rows = _load_position_rows(
            connection,
            position_table,
            article_table,
            kast_table,
        )
        queue_rows = _load_queue_rows(connection, queue_table) if queue_has_legacy_max else []
        plan = build_migration_plan(position_rows, queue_rows)
        if plan.issues:
            return MigrationResult(False, True, False, plan)

        _apply_plan(
            connection,
            article_table,
            position_table,
            queue_table,
            plan,
        )
        if position_legacy_columns:
            _drop_column(connection, "Voorraad_Positie", "trigger_min")
            _drop_column(connection, "Voorraad_Positie", "target_max")
        if queue_has_legacy_max:
            _drop_column(connection, "Print_Queue", "max_level")
        _write_migration_marker(connection, marker)
        return MigrationResult(True, False, False, plan)


def _normalized_material_type(value):
    if value is None or not str(value).strip():
        return Materiaaltype.KANBAN
    normalized = str(value).strip().upper()
    if normalized in {Materiaaltype.KANBAN.value, "TWO_BIN"}:
        return Materiaaltype.KANBAN
    if normalized in {Materiaaltype.STANDAARD.value, "STANDARD"}:
        return Materiaaltype.STANDAARD
    return None


def _integer_or_none(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if str(result) == str(value).strip() else None


def _position_issue(row, reason):
    return MigrationIssue(
        article_name=row.article_name or f"Artikel {row.article_id}",
        storage_location_name=row.storage_location_name or "",
        position_id=row.position_id,
        reason=reason,
    )


def _empty_plan():
    return MigrationPlan((), (), (), ())


def _migration_marker_table():
    metadata = MetaData()
    return Table(
        "Kanban_Migratie",
        metadata,
        Column("migratie_naam", String(100), primary_key=True),
        Column("voltooid_op", DateTime, nullable=False),
    )


def _migration_was_completed(connection, marker):
    return connection.execute(select(marker.c.migratie_naam).where(
        marker.c.migratie_naam == MIGRATION_NAME,
    )).first() is not None


def _write_migration_marker(connection, marker):
    connection.execute(marker.insert().values(
        migratie_naam=MIGRATION_NAME,
        voltooid_op=datetime.datetime.utcnow(),
    ))


def _required_table(connection, name):
    table = _optional_table(connection, name)
    if table is None:
        raise MigrationError(f"Verplichte tabel {name} ontbreekt.")
    return table


def _optional_table(connection, name):
    if not inspect(connection).has_table(name):
        return None
    return Table(name, MetaData(), autoload_with=connection)


def _require_columns(table, columns):
    missing = [column for column in columns if column not in table.c]
    if missing:
        raise MigrationError(
            f"Tabel {table.name} mist kolommen: {', '.join(missing)}."
        )


def _load_position_rows(connection, position_table, article_table, kast_table):
    storage_name = (
        kast_table.c.naam.label("storage_location_name")
        if kast_table is not None and "naam" in kast_table.c
        else literal(None).label("storage_location_name")
    )
    from_clause = position_table.join(
        article_table,
        position_table.c.lokaal_artikel_id
        == article_table.c.lokaal_artikel_id,
    )
    if kast_table is not None and "kast_id" in kast_table.c:
        from_clause = from_clause.outerjoin(
            kast_table,
            position_table.c.kast_id == kast_table.c.kast_id,
        )
    columns = [
        position_table.c.voorraad_positie_id.label("position_id"),
        position_table.c.lokaal_artikel_id.label("article_id"),
        article_table.c.eigen_naam.label("article_name")
        if "eigen_naam" in article_table.c
        else literal(None).label("article_name"),
        storage_name,
        position_table.c.materiaaltype.label("material_type"),
        position_table.c.trigger_min.label("legacy_min")
        if "trigger_min" in position_table.c
        else literal(None).label("legacy_min"),
        position_table.c.target_max.label("legacy_max")
        if "target_max" in position_table.c
        else literal(None).label("legacy_max"),
        position_table.c.kanban_min_override.label("min_override"),
        position_table.c.kanban_refill_quantity_override.label("refill_override"),
    ]
    rows = connection.execute(select(*columns).select_from(from_clause)).mappings()
    return [LegacyPositionRecord(**row) for row in rows]


def _load_queue_rows(connection, queue_table):
    if queue_table is None:
        return []
    rows = connection.execute(select(
        queue_table.c.print_id,
        queue_table.c.min_level,
        queue_table.c.max_level,
        queue_table.c.refill_quantity,
    )).mappings()
    return [LegacyQueueRecord(**row) for row in rows]


def _apply_plan(connection, article_table, position_table, queue_table, plan):
    for article_id, standard in plan.article_standards:
        connection.execute(update(article_table).where(
            article_table.c.lokaal_artikel_id == article_id,
        ).values(
            kanban_min=standard.min_level,
            kanban_refill_quantity=standard.refill_quantity,
        ))

    for item in plan.position_updates:
        connection.execute(update(position_table).where(
            position_table.c.voorraad_positie_id == item.position_id,
        ).values(
            materiaaltype=item.material_type,
            strategie=item.strategy,
            kanban_min_override=item.min_override,
            kanban_refill_quantity_override=item.refill_override,
        ))

    if queue_table is not None:
        for item in plan.queue_updates:
            connection.execute(update(queue_table).where(
                queue_table.c.print_id == item.print_id,
            ).values(refill_quantity=item.refill_quantity))


def _drop_column(connection, table_name, column_name):
    connection.execute(text(
        f"ALTER TABLE [{table_name}] DROP COLUMN [{column_name}]"
    ))
