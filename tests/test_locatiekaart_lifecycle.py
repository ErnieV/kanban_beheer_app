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


def _csrf_client(app_module):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["_csrf_token"] = "test-csrf"
    return client


def _create_pending_locatiekaart_version(app_module, **overrides):
    values = dict(
        bedrijf_id=1,
        voorraad_positie_id=91,
        lokaal_artikel_id=7,
        inhoud_hash="hash-1",
        artikelnaam="Verband",
        artikel_foto_url="data:image/png;base64,QUJD",
        bedrijfslogo_url="data:image/png;base64,REVG",
        vestiging_naam="Vestiging",
        ruimte_naam="1 Behandelkamer",
        opslaglocatie_naam="Kast A",
        kamertype_naam="Behandeling",
        kamertype_kleur="#123456",
        materiaaltype="KANBAN",
        min_level=5,
        status=app_module.LocatiekaartStatus.PENDING_PRINT.value,
    )
    values.update(overrides)
    with app_module.app.app_context():
        version = app_module.LocatiekaartVersie(**values)
        app_module.db.session.add(version)
        app_module.db.session.commit()
        return version.locatiekaart_versie_id


def _locatiekaart_status(app_module, version_id):
    with app_module.app.app_context():
        version = app_module.db.session.query(app_module.LocatiekaartVersie).filter_by(
            locatiekaart_versie_id=version_id
        ).first()
        return version.status if version else None


def _flash_messages(client):
    with client.session_transaction() as session:
        flashes = session.get("_flashes", [])
    return " ".join(message for _, message in flashes)


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


def test_reselecting_an_unchanged_printed_version_returns_it_to_the_queue(
    app_module,
):
    """Ticket #25 / ADR 0003: printing is now deferred to the Printwachtrij.
    A deliberately re-requested, content-unchanged Locatiekaart that was
    already PRINTED must come back as PENDING_PRINT — otherwise the
    aanvraag flash would report success while nothing is actually queued.
    """
    position, article, global_item, storage_location, room, room_type, company, branch = (
        _source_objects()
    )

    with app_module.app.app_context():
        app_module.db.session.query(app_module.LocatiekaartVersie).delete()
        app_module.db.session.commit()

        version, _ = app_module.create_or_reuse_locatiekaart_version(
            position, article, global_item, storage_location, room, room_type,
            company, branch,
        )
        app_module.db.session.flush()
        first_id = version.locatiekaart_versie_id
        app_module.mark_locatiekaart_version_printed(version)
        status_after_print = version.status  # lees vóór commit, niet erna
        app_module.db.session.commit()

        reselected, created = app_module.create_or_reuse_locatiekaart_version(
            position, article, global_item, storage_location, room, room_type,
            company, branch,
        )
        reselected_id = reselected.locatiekaart_versie_id
        reselected_status = reselected.status
        reselected_printed_at = reselected.printed_at
        app_module.db.session.commit()

    assert status_after_print == LocatiekaartStatus.PRINTED.value
    assert reselected_id == first_id  # dezelfde rij, geen nieuwe
    assert created is False
    assert reselected_status == LocatiekaartStatus.PENDING_PRINT.value
    assert reselected_printed_at is None


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


def test_kast_and_ruimte_inventory_row_shape_satisfies_both_print_paths(app_module):
    """Regression test for a production bug: the print-selection routes build one
    row tuple per inventory item from _kast_inventory_query/_ruimte_inventory_query
    and splat it with *row into create_queue_item (Kanban-kaartjes, 7 params) or
    create_or_reuse_locatiekaart_version (Locatiekaartjes, 8 params incl. branch).
    The route-level tests all monkeypatch those two functions away, so a query/
    signature arity drift (the queries never selected Vestiging) was never caught
    until it broke in production. This calls both real functions with the exact
    row shape the fixed queries now produce.
    """
    import inspect

    position, article, global_item, storage_location, room, room_type, company, branch = (
        _source_objects()
    )
    article.verpakkingseenheid_tekst = "doos"
    storage_location.type_opslag = "GRIJP"
    row = (position, article, global_item, storage_location, room, room_type, company, branch)

    # create_queue_item ends by constructing Print_Queue, an automapped model
    # unavailable against the plain sqlite test database (unlike the declared
    # KanbanKaart/LocatiekaartVersie models below) — bind() alone still proves
    # the row's arity matches the function's 7 Kanban-side parameters without
    # needing to run its DB-dependent tail.
    inspect.signature(app_module.create_queue_item).bind(*row[:7])

    with app_module.app.app_context():
        app_module.db.session.query(app_module.LocatiekaartVersie).delete()
        app_module.db.session.commit()

        version, created = app_module.create_or_reuse_locatiekaart_version(*row)
        vestiging_naam = version.vestiging_naam
        app_module.db.session.commit()

    assert created is True
    assert vestiging_naam == "Vestiging"


def test_locatiekaart_verstuur_enkel_sends_and_flashes_plain_language(
    app_module, monkeypatch
):
    """Ticket #26: the per-row 'versturen' action for a queued Locatiekaartje
    actually calls the A4 print service (_send_locatiekaart_batch, unused
    since #25) and reports the outcome in plain language — no job/batch ID.
    """
    sent_calls = []

    def fake_send(versions, print_batch_id):
        sent_calls.append([v.locatiekaart_versie_id for v in versions])
        return True, None, {
            "printBatchId": print_batch_id,
            "jobId": "job-xyz",
            "status": "ACCEPTED",
            "cardCount": len(versions),
            "sheetCount": 1,
        }

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module, "test_print_service_connectivity", lambda: (True, "ok")
    )
    monkeypatch.setattr(app_module, "send_location_cards_to_print_service", fake_send)

    version_id = _create_pending_locatiekaart_version(app_module)

    client = _csrf_client(app_module)
    response = client.post(
        f"/assistent/print-queue/locatiekaart/verstuur/{version_id}",
        data={"_csrf_token": "test-csrf"},
    )

    assert response.status_code == 302
    assert sent_calls == [[version_id]]
    flash_messages = _flash_messages(client)
    assert (
        "1 Locatiekaartje(s) verstuurd naar de A4-kleurenprinter (1 vel(len))."
        in flash_messages
    )
    assert "job-xyz" not in flash_messages
    assert _locatiekaart_status(app_module, version_id) == (
        app_module.LocatiekaartStatus.PRINTED.value
    )


def test_locatiekaart_verstuur_enkel_blocked_by_failed_connectivity_check(
    app_module, monkeypatch
):
    """Symmetry fix from the ticket #26 spec review: the Locatiekaart-side
    send routes must run the same connectivity pre-flight the Kanban-side
    routes already ran, instead of discovering an unreachable printservice
    only after fetching/encoding every card's images.
    """
    send_calls = []
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module,
        "test_print_service_connectivity",
        lambda: (False, "PRINT_SERVICE_URL ontbreekt."),
    )
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda *a, **k: send_calls.append(True),
    )

    version_id = _create_pending_locatiekaart_version(app_module)

    client = _csrf_client(app_module)
    response = client.post(
        f"/assistent/print-queue/locatiekaart/verstuur/{version_id}",
        data={"_csrf_token": "test-csrf"},
    )

    assert response.status_code == 302
    assert send_calls == []
    assert "PRINT_SERVICE_URL ontbreekt." in _flash_messages(client)
    assert _locatiekaart_status(app_module, version_id) == (
        app_module.LocatiekaartStatus.PENDING_PRINT.value
    )


def test_locatiekaart_verstuur_enkel_not_found_flashes_warning(
    app_module, monkeypatch
):
    send_calls = []
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda *a, **k: send_calls.append(True),
    )

    client = _csrf_client(app_module)
    response = client.post(
        "/assistent/print-queue/locatiekaart/verstuur/999999",
        data={"_csrf_token": "test-csrf"},
    )

    assert response.status_code == 302
    assert send_calls == []
    assert "Locatiekaartje niet gevonden of al verwerkt." in _flash_messages(client)


def test_locatiekaart_verstuur_enkel_reports_failure_without_marking_printed(
    app_module, monkeypatch
):
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module, "test_print_service_connectivity", lambda: (True, "ok")
    )
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda *a, **k: (False, "Printservice fout: timeout", None),
    )

    version_id = _create_pending_locatiekaart_version(app_module)

    client = _csrf_client(app_module)
    response = client.post(
        f"/assistent/print-queue/locatiekaart/verstuur/{version_id}",
        data={"_csrf_token": "test-csrf"},
    )

    assert response.status_code == 302
    assert "A4-printaanvraag mislukt" in _flash_messages(client)
    # Nooit stilzwijgend als verstuurd markeren wanneer het versturen mislukte.
    assert _locatiekaart_status(app_module, version_id) == (
        app_module.LocatiekaartStatus.PENDING_PRINT.value
    )


def test_locatiekaart_verstuur_selectie_sends_only_the_posted_subset(
    app_module, monkeypatch
):
    sent_calls = []

    def fake_send(versions, print_batch_id):
        sent_calls.append([v.locatiekaart_versie_id for v in versions])
        return True, None, {"cardCount": len(versions), "sheetCount": 1}

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module, "test_print_service_connectivity", lambda: (True, "ok")
    )
    monkeypatch.setattr(app_module, "send_location_cards_to_print_service", fake_send)

    id_a = _create_pending_locatiekaart_version(
        app_module, voorraad_positie_id=91, artikelnaam="Verband"
    )
    id_b = _create_pending_locatiekaart_version(
        app_module, voorraad_positie_id=92, artikelnaam="Pleisters"
    )

    client = _csrf_client(app_module)
    response = client.post(
        "/assistent/print-queue/locatiekaart/verstuur-selectie",
        data={"_csrf_token": "test-csrf", "locatiekaart_ids": [str(id_a)]},
    )

    assert response.status_code == 302
    assert sent_calls == [[id_a]]
    assert _locatiekaart_status(app_module, id_a) == (
        app_module.LocatiekaartStatus.PRINTED.value
    )
    assert _locatiekaart_status(app_module, id_b) == (
        app_module.LocatiekaartStatus.PENDING_PRINT.value
    )


def test_locatiekaart_verstuur_selectie_without_any_checked_row_sends_nothing(
    app_module, monkeypatch
):
    send_calls = []
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda *a, **k: send_calls.append(True),
    )

    client = _csrf_client(app_module)
    response = client.post(
        "/assistent/print-queue/locatiekaart/verstuur-selectie",
        data={"_csrf_token": "test-csrf"},
    )

    assert response.status_code == 302
    assert send_calls == []
    assert "Selecteer minimaal één Locatiekaartje" in _flash_messages(client)


def test_locatiekaart_verstuur_alles_sends_every_pending_version(
    app_module, monkeypatch
):
    sent_calls = []

    def fake_send(versions, print_batch_id):
        sent_calls.append(sorted(v.locatiekaart_versie_id for v in versions))
        return True, None, {"cardCount": len(versions), "sheetCount": 1}

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module, "test_print_service_connectivity", lambda: (True, "ok")
    )
    monkeypatch.setattr(app_module, "send_location_cards_to_print_service", fake_send)

    with app_module.app.app_context():
        app_module.db.session.query(app_module.LocatiekaartVersie).delete()
        app_module.db.session.commit()

    id_a = _create_pending_locatiekaart_version(app_module, voorraad_positie_id=91)
    id_b = _create_pending_locatiekaart_version(app_module, voorraad_positie_id=92)

    client = _csrf_client(app_module)
    response = client.post(
        "/assistent/print-queue/locatiekaart/verstuur-alles",
        data={"_csrf_token": "test-csrf"},
    )

    assert response.status_code == 302
    assert sent_calls == [sorted([id_a, id_b])]


def test_locatiekaart_verstuur_alles_on_empty_queue_flashes_info_and_sends_nothing(
    app_module, monkeypatch
):
    send_calls = []
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda *a, **k: send_calls.append(True),
    )

    with app_module.app.app_context():
        app_module.db.session.query(app_module.LocatiekaartVersie).delete()
        app_module.db.session.commit()

    client = _csrf_client(app_module)
    response = client.post(
        "/assistent/print-queue/locatiekaart/verstuur-alles",
        data={"_csrf_token": "test-csrf"},
    )

    assert response.status_code == 302
    assert send_calls == []
    assert "Geen openstaande Locatiekaartjes." in _flash_messages(client)


def test_locatiekaart_annuleren_marks_cancelled_and_flashes(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)

    version_id = _create_pending_locatiekaart_version(app_module)

    client = _csrf_client(app_module)
    response = client.post(
        f"/assistent/print-queue/locatiekaart/annuleren/{version_id}",
        data={"_csrf_token": "test-csrf"},
    )

    assert response.status_code == 302
    assert _locatiekaart_status(app_module, version_id) == (
        app_module.LocatiekaartStatus.CANCELLED.value
    )
    assert "Aanvraag geannuleerd." in _flash_messages(client)


def test_locatiekaart_annuleren_not_found_or_already_processed_flashes_warning(
    app_module, monkeypatch
):
    """Ticket #26 AC: annuleren is never a silent no-op — mirrors the same
    fix applied to the Kanban-side annuleren_print_opdracht.
    """
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)

    client = _csrf_client(app_module)
    response = client.post(
        "/assistent/print-queue/locatiekaart/annuleren/999999",
        data={"_csrf_token": "test-csrf"},
    )

    assert response.status_code == 302
    assert "Aanvraag niet gevonden of al verwerkt." in _flash_messages(client)


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
