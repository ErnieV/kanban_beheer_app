"""Run the guarded legacy Min/Max migration for the configured database."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, db
from kanban_migration import MigrationError, migrate_legacy_max


def main():
    try:
        with app.app_context():
            result = migrate_legacy_max(db.engine)
    except MigrationError as exc:
        print(f"Migratie niet uitgevoerd: {exc}")
        return 2

    if result.already_completed:
        print("Migratie was al succesvol uitgevoerd; er is niets gewijzigd.")
        return 0
    if result.blocked:
        print("Migratie gestopt: corrigeer eerst deze waarden:")
        print(result.invalid_report)
        return 2

    print(
        "Migratie geslaagd: "
        f"{len(result.plan.article_standards)} Artikel-standaard(s), "
        f"{len(result.plan.position_updates)} Voorraadpositie(s) verwerkt."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
