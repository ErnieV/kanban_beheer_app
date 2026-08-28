from types import SimpleNamespace

import pytest

from kanban_domain import LocatiekaartStatus


@pytest.fixture
def app_module(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-123456789012345678901234")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    import app

    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    return app


def test_location_card_content_uses_article_photo_and_effective_min(app_module):
    position, article, global_item, storage_location, room, room_type, company, branch = (
        _source_objects()
    )

    content = app_module.build_locatiekaart_content(
        position,
        article,
        global_item,
        storage_location,
        room,
        room_type,
        company,
        branch,
    )

    assert content.artikelnaam == "Verband"
    assert content.artikel_foto_url == "article.png"
    assert content.bedrijfslogo_url == "logo.png"
    assert content.vestiging_naam == "Vestiging"
    assert content.ruimte_naam == "1 Behandelkamer"
    assert content.opslaglocatie_naam == "Kast A"
    assert content.kamertype_naam == "Behandeling"
    assert content.kamertype_kleur == "#123456"
    assert content.materiaaltype.value == "KANBAN"
    assert content.min_level == 5
    assert content.refill_quantity == 2


def test_standard_location_card_content_has_no_kanban_values(app_module):
    position, article, global_item, storage_location, room, room_type, company, branch = (
        _source_objects()
    )
    position.materiaaltype = "STANDAARD"

    content = app_module.build_locatiekaart_content(
        position,
        article,
        global_item,
        storage_location,
        room,
        room_type,
        company,
        branch,
    )

    assert content.materiaaltype.value == "STANDAARD"
    assert content.min_level is None
    assert content.refill_quantity is None


def test_location_card_version_reuses_unchanged_content_and_supersedes_changed_content(
    app_module,
):
    position, article, global_item, storage_location, room, room_type, company, branch = (
        _source_objects()
    )

    with app_module.app.app_context():
        app_module.db.session.query(app_module.LocatiekaartVersie).delete()
        app_module.db.session.commit()

        first, first_created = app_module.create_or_reuse_locatiekaart_version(
            position,
            article,
            global_item,
            storage_location,
            room,
            room_type,
            company,
            branch,
        )
        app_module.db.session.flush()
        repeated, repeated_created = app_module.create_or_reuse_locatiekaart_version(
            position,
            article,
            global_item,
            storage_location,
            room,
            room_type,
            company,
            branch,
        )
        first_id = first.locatiekaart_versie_id
        repeated_id = repeated.locatiekaart_versie_id

        position.kanban_min_override = 6
        changed, changed_created = app_module.create_or_reuse_locatiekaart_version(
            position,
            article,
            global_item,
            storage_location,
            room,
            room_type,
            company,
            branch,
        )
        first_status = first.status
        changed_status = changed.status
        changed_min = changed.min_level
        app_module.db.session.commit()

    assert first_created is True
    assert repeated_created is False
    assert repeated_id == first_id
    assert changed_created is True
    assert first_status == LocatiekaartStatus.SUPERSEDED.value
    assert changed_status == LocatiekaartStatus.PENDING_PRINT.value
    assert changed_min == 6


def test_location_card_status_transitions_match_print_acceptance(app_module):
    pending = SimpleNamespace(
        status=LocatiekaartStatus.PENDING_PRINT.value,
        printed_at=None,
        cancelled_at=None,
    )
    superseded = SimpleNamespace(
        status=LocatiekaartStatus.SUPERSEDED.value,
        printed_at=None,
        cancelled_at=None,
    )
    cancelled = SimpleNamespace(
        status=LocatiekaartStatus.CANCELLED.value,
        printed_at=None,
        cancelled_at=None,
    )

    assert app_module.mark_locatiekaart_version_printed(pending) is True
    assert pending.status == LocatiekaartStatus.PRINTED.value
    assert pending.printed_at is not None
    assert app_module.mark_locatiekaart_version_cancelled(pending) is False
    assert app_module.mark_locatiekaart_version_printed(superseded) is False
    assert app_module.mark_locatiekaart_version_printed(cancelled) is False


def _source_objects():
    position = SimpleNamespace(
        voorraad_positie_id=91,
        lokaal_artikel_id=7,
        materiaaltype="KANBAN",
        kanban_min_override=5,
        kanban_refill_quantity_override=2,
        locatie_foto_url="location.png",
    )
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        eigen_naam="Verband",
        foto_url="article.png",
        kanban_min=3,
        kanban_refill_quantity=4,
    )
    global_item = SimpleNamespace(
        generieke_naam="Catalogusverband",
        foto_url="global.png",
    )
    storage_location = SimpleNamespace(naam="Kast A")
    room = SimpleNamespace(naam="Behandelkamer", nummer="1")
    room_type = SimpleNamespace(naam="Behandeling", kleur_hex="#123456")
    company = SimpleNamespace(bedrijf_id=1, logo_url="logo.png")
    branch = SimpleNamespace(naam="Vestiging")
    return (
        position,
        article,
        global_item,
        storage_location,
        room,
        room_type,
        company,
        branch,
    )
