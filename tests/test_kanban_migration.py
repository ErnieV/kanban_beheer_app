from sqlalchemy import create_engine, inspect, text

from kanban_domain import KanbanStandard
from kanban_migration import (
    LegacyPositionRecord,
    build_migration_plan,
    migrate_legacy_max,
    select_article_standard,
)


def test_article_standard_selection_uses_frequency_then_tie_breakers():
    assert select_article_standard([(2, 3), (2, 3), (1, 5)]) == KanbanStandard(2, 3)
    assert select_article_standard([(2, 2), (1, 4)]) == KanbanStandard(2, 2)
    assert select_article_standard([(2, 3), (1, 4)]) == KanbanStandard(1, 4)


def test_migration_plan_preserves_nonstandard_pairs_as_local_deviations():
    rows = [
        LegacyPositionRecord(1, 10, "Verband", "Kast A", "KANBAN", 1, 3),
        LegacyPositionRecord(2, 10, "Verband", "Kast B", "KANBAN", 1, 3),
        LegacyPositionRecord(3, 10, "Verband", "Kast C", "KANBAN", 2, 5),
        LegacyPositionRecord(4, 20, "Naalden", "Kast D", "STANDAARD", 9, 2),
    ]

    plan = build_migration_plan(rows, [])

    assert plan.issues == ()
    assert dict(plan.article_standards)[10] == KanbanStandard(1, 2)
    assert plan.position_updates[0].min_override is None
    assert plan.position_updates[0].refill_override is None
    assert plan.position_updates[2].min_override == 2
    assert plan.position_updates[2].refill_override == 3
    assert len(plan.position_updates) == 3


def test_invalid_legacy_values_block_before_cutover():
    rows = [
        LegacyPositionRecord(7, 10, "Verband", "Kast F", "KANBAN", 3, 3),
        LegacyPositionRecord(8, 10, "Verband", "Kast G", "KANBAN", None, 4),
    ]

    plan = build_migration_plan(rows, [])

    assert len(plan.issues) == 2
    assert "Max moet groter zijn dan Min" in plan.issues[0].reason
    assert "ontbreekt" in plan.issues[1].reason
    assert "Verband" in plan.invalid_report
    assert "Kast F" in plan.invalid_report


def test_successful_migration_converts_data_drops_legacy_columns_and_is_repeatable():
    engine = _legacy_engine()
    _insert_valid_fixture(engine)

    result = migrate_legacy_max(engine)

    assert result.success is True
    assert result.already_completed is False
    table_names = set(inspect(engine).get_table_names())
    assert "Kanban_Migratie" in table_names
    assert "trigger_min" not in _columns(engine, "Voorraad_Positie")
    assert "target_max" not in _columns(engine, "Voorraad_Positie")
    assert "max_level" not in _columns(engine, "Print_Queue")

    with engine.connect() as connection:
        position = connection.execute(text(
            "SELECT kanban_min_override, kanban_refill_quantity_override "
            "FROM Voorraad_Positie WHERE voorraad_positie_id = 1"
        )).one()
        article = connection.execute(text(
            "SELECT kanban_min, kanban_refill_quantity "
            "FROM Lokaal_Artikel WHERE lokaal_artikel_id = 10"
        )).one()
        queue = connection.execute(text(
            "SELECT min_level, refill_quantity FROM Print_Queue WHERE print_id = 1"
        )).one()

    assert tuple(position) == (None, None)
    assert tuple(article) == (1, 2)
    assert tuple(queue) == (1, 2)

    repeated = migrate_legacy_max(engine)
    assert repeated.success is True
    assert repeated.already_completed is True


def test_invalid_database_migration_keeps_legacy_schema_and_reports_location():
    engine = _legacy_engine()
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO Lokaal_Artikel "
            "(lokaal_artikel_id, eigen_naam, kanban_min, kanban_refill_quantity) "
            "VALUES (10, 'Verband', NULL, NULL)"
        ))
        connection.execute(text(
            "INSERT INTO Kast (kast_id, naam) VALUES (4, 'Kast F')"
        ))
        connection.execute(text(
            "INSERT INTO Voorraad_Positie "
            "(voorraad_positie_id, lokaal_artikel_id, kast_id, materiaaltype, "
            "trigger_min, target_max) VALUES (7, 10, 4, 'KANBAN', 3, 3)"
        ))

    result = migrate_legacy_max(engine)

    assert result.success is False
    assert result.blocked is True
    assert "Verband" in result.invalid_report
    assert "Kast F" in result.invalid_report
    assert "trigger_min" in _columns(engine, "Voorraad_Positie")
    assert "target_max" in _columns(engine, "Voorraad_Positie")
    with engine.connect() as connection:
        article = connection.execute(text(
            "SELECT kanban_min, kanban_refill_quantity "
            "FROM Lokaal_Artikel WHERE lokaal_artikel_id = 10"
        )).one()
    assert tuple(article) == (None, None)


def _legacy_engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE Lokaal_Artikel ("
            "lokaal_artikel_id INTEGER PRIMARY KEY, eigen_naam TEXT, "
            "kanban_min INTEGER, kanban_refill_quantity INTEGER)"
        ))
        connection.execute(text(
            "CREATE TABLE Kast (kast_id INTEGER PRIMARY KEY, naam TEXT)"
        ))
        connection.execute(text(
            "CREATE TABLE Voorraad_Positie ("
            "voorraad_positie_id INTEGER PRIMARY KEY, lokaal_artikel_id INTEGER, "
            "kast_id INTEGER, materiaaltype TEXT, strategie TEXT, "
            "trigger_min INTEGER, target_max INTEGER, "
            "kanban_min_override INTEGER, "
            "kanban_refill_quantity_override INTEGER)"
        ))
        connection.execute(text(
            "CREATE TABLE Print_Queue ("
            "print_id INTEGER PRIMARY KEY, min_level INTEGER, max_level INTEGER, "
            "refill_quantity INTEGER)"
        ))
    return engine


def _insert_valid_fixture(engine):
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO Lokaal_Artikel "
            "(lokaal_artikel_id, eigen_naam, kanban_min, kanban_refill_quantity) "
            "VALUES (10, 'Verband', NULL, NULL)"
        ))
        connection.execute(text(
            "INSERT INTO Kast (kast_id, naam) VALUES "
            "(1, 'Kast A'), (2, 'Kast B'), (3, 'Kast C')"
        ))
        connection.execute(text(
            "INSERT INTO Voorraad_Positie "
            "(voorraad_positie_id, lokaal_artikel_id, kast_id, materiaaltype, "
            "trigger_min, target_max) VALUES "
            "(1, 10, 1, 'KANBAN', 1, 3), "
            "(2, 10, 2, 'KANBAN', 1, 3), "
            "(3, 10, 3, 'KANBAN', 2, 5)"
        ))
        connection.execute(text(
            "INSERT INTO Print_Queue "
            "(print_id, min_level, max_level, refill_quantity) "
            "VALUES (1, 1, 3, NULL)"
        ))


def _columns(engine, table_name):
    return {column["name"] for column in inspect(engine).get_columns(table_name)}
