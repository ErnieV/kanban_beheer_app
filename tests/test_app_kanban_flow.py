import datetime
import io
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, text

from kanban_domain import KanbanSettingsError, KanbanStandard


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


def test_article_edit_is_a_user_facing_flask_flow_for_the_new_standard(
    app_module, monkeypatch
):
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        bedrijf_id=1,
        eigen_naam="Verband",
        verpakkingseenheid_tekst="doos",
    )

    class FakeSession:
        def commit(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: article)
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/artikelen-beheer",
        data={
            "_csrf_token": "test-csrf",
            "actie": "bewerk_artikel",
            "artikel_id": "7",
            "naam": "Verband",
            "eenheid": "doos",
            "kanban_min": "3",
            "kanban_refill_quantity": "4",
        },
    )

    assert response.status_code == 302
    assert article.kanban_min == 3
    assert article.kanban_refill_quantity == 4


def test_new_article_is_created_with_the_new_default_standard(app_module, monkeypatch):
    created = []

    class FakeArticle:
        def __init__(self, **values):
            self.__dict__.update(values)
            created.append(self)

    class FakeSession:
        def add(self, item):
            return None

        def commit(self):
            return None

    monkeypatch.setattr(app_module, "Lokaal_Artikel", FakeArticle)
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/artikelen-beheer",
        data={
            "_csrf_token": "test-csrf",
            "actie": "nieuw_lokaal",
            "naam": "Nieuwe pleister",
            "eenheid": "doos",
        },
    )

    assert response.status_code == 302
    assert len(created) == 1
    assert created[0].kanban_min == 1
    assert created[0].kanban_refill_quantity == 1


def test_position_edit_is_a_user_facing_flask_flow_for_independent_overrides(
    app_module, monkeypatch
):
    superseded_position_ids = []
    position = SimpleNamespace(
        voorraad_positie_id=12,
        bedrijf_id=1,
        kast_id=4,
        lokaal_artikel_id=7,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        trigger_min=1,
        target_max=2,
    )
    kast = SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9)
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        bedrijf_id=1,
        kanban_min=1,
        kanban_refill_quantity=1,
    )
    card = SimpleNamespace(status="PRINTED")

    class FakeSession:
        def query(self, *models):
            class CardQuery:
                def filter(self, *args, **kwargs):
                    return self

                def all(self):
                    return [card]

            return CardQuery()

        def commit(self):
            return None

    def scoped_item(model, item_id, bedrijf_id):
        return {4: kast, 7: article, 12: position}.get(item_id)

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", scoped_item)
    monkeypatch.setattr(
        app_module,
        "_supersede_locatiekaart_versions_for_position_ids",
        lambda position_ids: superseded_position_ids.extend(position_ids),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/update-voorraad/12",
        data={
            "_csrf_token": "test-csrf",
            "materiaaltype": "KANBAN",
            "kanban_min_override": "5",
            "kanban_refill_quantity_override": "2",
        },
    )

    assert response.status_code == 302
    assert position.kanban_min_override == 5
    assert position.kanban_refill_quantity_override == 2
    assert superseded_position_ids == [12]


def test_storage_location_rename_supersedes_location_card_versions(
    app_module,
    monkeypatch,
):
    storage_location = SimpleNamespace(
        kast_id=4,
        bedrijf_id=1,
        naam="Kast A",
        type_opslag="GRIJP",
    )
    superseded_position_ids = []

    class FakeSession:
        def commit(self):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module,
        "get_scoped_item",
        lambda *args: storage_location,
    )
    monkeypatch.setattr(
        app_module,
        "_position_ids_for_scope",
        lambda scope, item_id: [12],
    )
    monkeypatch.setattr(
        app_module,
        "_supersede_locatiekaart_versions_for_position_ids",
        lambda position_ids: superseded_position_ids.extend(position_ids),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/beheer/update/kast/4",
        data={
            "_csrf_token": "test-csrf",
            "naam": "Kast B",
            "type_opslag": "GRIJP",
        },
    )

    assert response.status_code == 302
    assert storage_location.naam == "Kast B"
    assert superseded_position_ids == [12]


def test_catalog_photo_change_supersedes_only_fallback_location_cards(
    app_module,
    monkeypatch,
):
    global_item = SimpleNamespace(
        global_id=8,
        generieke_naam="Catalogusverband",
        foto_url="old.png",
    )
    superseded_position_ids = []

    class FakeColumn:
        def __eq__(self, other):
            return True

    class FakeSession:
        def query(self, model):
            return _CatalogQuery(global_item)

        def commit(self):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "upload_image_to_azure", lambda file: "new.png")
    monkeypatch.setattr(
        app_module,
        "Global_Catalogus",
        SimpleNamespace(global_id=FakeColumn()),
    )
    monkeypatch.setattr(
        app_module,
        "_position_ids_for_global_item",
        lambda global_id, fallback_attribute: (
            [12] if fallback_attribute == "foto_url" else []
        ),
    )
    monkeypatch.setattr(
        app_module,
        "_supersede_locatiekaart_versions_for_position_ids",
        lambda position_ids: superseded_position_ids.extend(position_ids),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/beheer/catalogus",
        data={
            "_csrf_token": "test-csrf",
            "actie": "bewerk_global",
            "global_id": "8",
            "naam": "Catalogusverband",
            "ean": "123",
            "categorie": "zorg",
            "afbeelding": (io.BytesIO(b"image"), "photo.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert global_item.foto_url == "new.png"
    assert superseded_position_ids == [12]


def test_storage_location_kanban_print_selection_filters_and_validates_assets(
    app_module,
    monkeypatch,
):
    kast = SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9, naam="Kast A", type_opslag="GRIJP")
    room = SimpleNamespace(ruimte_id=9, naam="Behandelkamer", nummer="1")
    room_type = SimpleNamespace(ruimte_type_id=8, naam="Behandeling", kleur_hex="#123456")
    company = SimpleNamespace(bedrijf_id=1, logo_url="logo.png")
    branch = SimpleNamespace(naam="Vestiging")
    valid_position = SimpleNamespace(
        voorraad_positie_id=12,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        locatie_foto_url=None,
    )
    standard_position = SimpleNamespace(
        voorraad_positie_id=13,
        materiaaltype="STANDAARD",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        locatie_foto_url=None,
    )
    invalid_position = SimpleNamespace(
        voorraad_positie_id=14,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        locatie_foto_url="location.png",
    )
    valid_article = SimpleNamespace(
        lokaal_artikel_id=7,
        eigen_naam="Verband",
        foto_url="article.png",
        verpakkingseenheid_tekst="doos",
        kanban_min=3,
        kanban_refill_quantity=4,
    )
    standard_article = SimpleNamespace(
        lokaal_artikel_id=8,
        eigen_naam="Naaldencontainer",
        foto_url="standard.png",
        verpakkingseenheid_tekst="stuk",
        kanban_min=3,
        kanban_refill_quantity=4,
    )
    invalid_article = SimpleNamespace(
        lokaal_artikel_id=9,
        eigen_naam="Onbekend artikel",
        foto_url=None,
        verpakkingseenheid_tekst="stuk",
        kanban_min=3,
        kanban_refill_quantity=4,
    )
    rows = [
        (valid_position, valid_article, None, kast, room, room_type, company, branch),
        (standard_position, standard_article, None, kast, room, room_type, company, branch),
        (invalid_position, invalid_article, None, kast, room, room_type, company, branch),
    ]

    class FakeField:
        def __eq__(self, other):
            return True

    class Query:
        def join(self, *args, **kwargs):
            return self

        def outerjoin(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return rows

    class FakeSession:
        def query(self, *models):
            return Query()

    for name, fields in {
        "Voorraad_Positie": ["lokaal_artikel_id", "kast_id", "bedrijf_id"],
        "Lokaal_Artikel": ["lokaal_artikel_id", "global_id", "eigen_naam"],
        "Global_Catalogus": ["global_id"],
        "Kast": ["kast_id", "ruimte_id", "bedrijf_id"],
        "Ruimte": ["ruimte_id", "vestiging_id", "ruimte_type_id", "bedrijf_id"],
        "Ruimte_Type": ["ruimte_type_id", "bedrijf_id"],
        "Bedrijf": ["bedrijf_id"],
    }.items():
        monkeypatch.setattr(
            app_module,
            name,
            SimpleNamespace(**{field: FakeField() for field in fields}),
        )
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: kast)
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = app_module.app.test_client().get(
        "/assistent/kast/4/print/kanban"
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Kanban-kaartjes printen" in html
    assert "2 toepasselijke" in html
    assert 'value="12"' in html
    assert re.search(r'value="12"\s+checked', html)
    assert re.search(r'value="14"\s+disabled', html)
    assert "Artikel-foto ontbreekt." in html
    assert "Naaldencontainer" not in html

    response = app_module.app.test_client().get(
        "/assistent/kast/4/print/locatie"
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Locatiekaartjes printen" in html
    assert "3 toepasselijke" in html
    assert "Naaldencontainer" in html
    assert 'value="13"' in html
    assert re.search(r'value="13"\s+checked', html)
    assert re.search(r'value="14"\s+disabled', html)
    assert "Min 3" in html
    assert "Aanv. 4" not in html

    company.logo_url = None
    response = app_module.app.test_client().get(
        "/assistent/kast/4/print/locatie"
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Bedrijfslogo ontbreekt." in html
    assert re.search(r'value="12"\s+disabled', html)


def test_storage_location_print_only_processes_selected_valid_items(
    app_module,
    monkeypatch,
):
    kast = SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9, naam="Kast A")
    company = SimpleNamespace(bedrijf_id=1, logo_url="logo.png")
    branch = SimpleNamespace(naam="Vestiging")
    room = SimpleNamespace(naam="Behandelkamer", nummer=None)
    room_type = SimpleNamespace(naam="Behandeling", kleur_hex="#123456")
    valid_position = SimpleNamespace(
        voorraad_positie_id=12,
        lokaal_artikel_id=7,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        locatie_foto_url=None,
    )
    invalid_position = SimpleNamespace(
        voorraad_positie_id=14,
        lokaal_artikel_id=9,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        locatie_foto_url=None,
    )
    valid_article = SimpleNamespace(
        lokaal_artikel_id=7,
        eigen_naam="Verband",
        foto_url="article.png",
        verpakkingseenheid_tekst="doos",
        kanban_min=3,
        kanban_refill_quantity=4,
    )
    invalid_article = SimpleNamespace(
        lokaal_artikel_id=9,
        eigen_naam="Onbekend artikel",
        foto_url=None,
        verpakkingseenheid_tekst="stuk",
        kanban_min=3,
        kanban_refill_quantity=4,
    )
    rows = [
        (valid_position, valid_article, None, kast, room, room_type, company, branch),
        (invalid_position, invalid_article, None, kast, room, room_type, company, branch),
    ]
    added = []
    created_rows = []

    class FakeQuery:
        def all(self):
            return rows

    class FakeSession:
        def add(self, item):
            added.append(item)

        def commit(self):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: kast)
    monkeypatch.setattr(app_module, "_kast_inventory_query", lambda *args: FakeQuery())
    monkeypatch.setattr(
        app_module,
        "create_queue_item",
        lambda *row: created_rows.append(row) or "queue-item",
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/kast/4/print/kanban",
        data={
            "_csrf_token": "test-csrf",
            "position_ids": ["12", "14"],
        },
    )

    assert response.status_code == 302
    assert [row[0].voorraad_positie_id for row in created_rows] == [12]
    assert added == ["queue-item"]


def test_location_print_selection_creates_only_selected_location_versions(
    app_module,
    monkeypatch,
):
    kast = SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9, naam="Kast A")
    company = SimpleNamespace(bedrijf_id=1, logo_url="logo.png")
    branch = SimpleNamespace(naam="Vestiging")
    room = SimpleNamespace(naam="Behandelkamer", nummer=None)
    room_type = SimpleNamespace(naam="Behandeling", kleur_hex="#123456")
    position = SimpleNamespace(
        voorraad_positie_id=13,
        lokaal_artikel_id=8,
        materiaaltype="STANDAARD",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        locatie_foto_url=None,
    )
    article = SimpleNamespace(
        lokaal_artikel_id=8,
        eigen_naam="Naaldencontainer",
        foto_url="standard.png",
        verpakkingseenheid_tekst="stuk",
        kanban_min=3,
        kanban_refill_quantity=4,
    )
    row = (position, article, None, kast, room, room_type, company, branch)
    created_rows = []
    version = SimpleNamespace(
        status="PENDING_PRINT",
        printed_at=None,
        cancelled_at=None,
        artikelnaam="Naaldencontainer",
        artikel_foto_url="data:image/png;base64,QUJD",
        bedrijfslogo_url="data:image/png;base64,REVG",
        kamertype_naam="Behandeling",
        kamertype_kleur="#123456",
    )

    class FakeQuery:
        def all(self):
            return [row]

    class FakeSession:
        def commit(self):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: kast)
    monkeypatch.setattr(app_module, "_kast_inventory_query", lambda *args: FakeQuery())
    monkeypatch.setattr(
        app_module,
        "create_or_reuse_locatiekaart_version",
        lambda *source_row: created_rows.append(source_row) or (version, True),
    )
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda versions, print_batch_id: (
            True,
            None,
            {
                "printBatchId": print_batch_id,
                "jobId": "job-1",
                "status": "ACCEPTED",
                "cardCount": len(versions),
                "sheetCount": 1,
            },
        ),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/kast/4/print/locatie",
        data={
            "_csrf_token": "test-csrf",
            "position_ids": ["13"],
            "printBatchId": "batch-1",
        },
    )

    assert response.status_code == 302
    assert [source_row[0].voorraad_positie_id for source_row in created_rows] == [13]
    assert version.status == "PRINTED"


@pytest.mark.parametrize(
    "scope_url, entity, inventory_query_attr, expected_back_url",
    [
        (
            "/assistent/kast/4/print/kanban",
            SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9, naam="Lege kast"),
            "_kast_inventory_query",
            "/assistent/kamer/9?open_kast=4",
        ),
        (
            "/assistent/kamer/9/print/kanban",
            SimpleNamespace(ruimte_id=9, naam="Lege kamer", nummer=None),
            "_ruimte_inventory_query",
            "/assistent/kamer/9",
        ),
    ],
    ids=["opslaglocatie-scope", "ruimte-scope"],
)
def test_empty_print_selection_has_no_side_effect_for_either_scope(
    app_module, monkeypatch, scope_url, entity, inventory_query_attr, expected_back_url,
):
    """Ticket #24: both scopes now share one handler — a genuinely empty
    submission (0 checkboxes posted, nothing to preserve) must re-render
    the same warning and change nothing, for either scope identically.
    This is distinct from test_print_selectie_keeps_submitted_selection_
    instead_of_wiping_it, which covers a *non-empty* but all-invalid
    submission — there, prior checkbox state must survive; here, there
    never was any state to preserve in the first place.
    """
    commits = []

    class FakeQuery:
        def all(self):
            return []

    class FakeSession:
        def commit(self):
            commits.append(True)

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: entity)
    monkeypatch.setattr(app_module, inventory_query_attr, lambda *args: FakeQuery())
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        scope_url, data={"_csrf_token": "test-csrf"},
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Selecteer minimaal één geldig Artikel" in html
    assert 'id="printSelectionButton" disabled' in html
    assert expected_back_url in html
    assert commits == []


def _room_print_rows():
    room = SimpleNamespace(
        ruimte_id=9,
        ruimte_type_id=8,
        vestiging_id=6,
        naam="Behandelkamer",
        nummer="1",
    )
    room_type = SimpleNamespace(naam="Behandeling", kleur_hex="#123456")
    company = SimpleNamespace(bedrijf_id=1, logo_url="logo.png")
    branch = SimpleNamespace(naam="Vestiging")
    kast_a = SimpleNamespace(kast_id=4, naam="Kast A", ruimte_id=9)
    kast_z = SimpleNamespace(kast_id=5, naam="Kast Z", ruimte_id=9)

    def position(position_id, material_type):
        return SimpleNamespace(
            voorraad_positie_id=position_id,
            materiaaltype=material_type,
            kanban_min_override=None,
            kanban_refill_quantity_override=None,
            locatie_foto_url=None,
        )

    def article(article_id, name, photo="article.png"):
        return SimpleNamespace(
            lokaal_artikel_id=article_id,
            eigen_naam=name,
            foto_url=photo,
            verpakkingseenheid_tekst="doos",
            kanban_min=3,
            kanban_refill_quantity=4,
        )

    return room, [
        (
            position(101, "KANBAN"),
            article(1, "Zebra"),
            None,
            kast_z,
            room,
            room_type,
            company,
            branch,
        ),
        (
            position(102, "KANBAN"),
            article(2, "Alpha"),
            None,
            kast_a,
            room,
            room_type,
            company,
            branch,
        ),
        (
            position(103, "STANDAARD"),
            article(3, "Standard"),
            None,
            kast_a,
            room,
            room_type,
            company,
            branch,
        ),
        (
            position(104, "KANBAN"),
            article(4, "NoPhoto", photo=None),
            None,
            kast_a,
            room,
            room_type,
            company,
            branch,
        ),
    ]


def test_room_print_selection_collects_and_orders_all_storage_locations(
    app_module,
    monkeypatch,
):
    room, rows = _room_print_rows()

    class FakeQuery:
        def all(self):
            return rows

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: room)
    monkeypatch.setattr(app_module, "_ruimte_inventory_query", lambda *args: FakeQuery())

    client = _csrf_client(app_module)
    response = client.get("/assistent/kamer/9/print/kanban")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Kanban-kaartjes printen" in html
    assert "3 toepasselijke" in html
    assert "Alpha" in html
    assert "NoPhoto" in html
    assert "Zebra" in html
    assert "Standard" not in html
    assert re.search(r'value="102"\s+checked', html)
    assert re.search(r'value="104"\s+disabled', html)
    assert html.index("Kast A") < html.index("Kast Z")
    assert html.index("Alpha") < html.index("NoPhoto") < html.index("Zebra")

    response = client.get("/assistent/kamer/9/print/locatie")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Locatiekaartjes printen" in html
    assert "4 toepasselijke" in html
    assert "Standard" in html
    assert "Aanv." not in html
    assert re.search(r'value="103"\s+checked', html)
    assert re.search(r'value="104"\s+disabled', html)


def test_room_print_selection_groups_items_by_storage_location(app_module, monkeypatch):
    """Ticket #24 AC: kaartjes zijn gegroepeerd per Opslaglocatie."""
    room, rows = _room_print_rows()

    class FakeQuery:
        def all(self):
            return rows

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: room)
    monkeypatch.setattr(app_module, "_ruimte_inventory_query", lambda *args: FakeQuery())

    response = _csrf_client(app_module).get("/assistent/kamer/9/print/kanban")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Opslaglocatie: Kast A" in html
    assert "Opslaglocatie: Kast Z" in html
    assert html.index("Opslaglocatie: Kast A") < html.index("Alpha")
    assert html.index("Alpha") < html.index("Opslaglocatie: Kast Z")
    assert html.index("Opslaglocatie: Kast Z") < html.index("Zebra")


def test_storage_location_print_selection_has_no_redundant_group_header(
    app_module, monkeypatch,
):
    """A single Opslaglocatie is, by definition, one group — showing its own
    name again as a group header would just repeat the page subtitle.
    """
    kast = SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9, naam="Kast A")
    company = SimpleNamespace(bedrijf_id=1, logo_url="logo.png")
    branch = SimpleNamespace(naam="Vestiging")
    room = SimpleNamespace(naam="Behandelkamer", nummer=None)
    room_type = SimpleNamespace(naam="Behandeling", kleur_hex="#123456")
    position = SimpleNamespace(
        voorraad_positie_id=12,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        locatie_foto_url=None,
    )
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        eigen_naam="Verband",
        foto_url="article.png",
        verpakkingseenheid_tekst="doos",
        kanban_min=3,
        kanban_refill_quantity=4,
    )
    rows = [(position, article, None, kast, room, room_type, company, branch)]

    class FakeQuery:
        def all(self):
            return rows

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: kast)
    monkeypatch.setattr(app_module, "_kast_inventory_query", lambda *args: FakeQuery())

    response = _csrf_client(app_module).get("/assistent/kast/4/print/kanban")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Verband" in html
    assert "Opslaglocatie: Kast A" not in html


def test_print_selectie_offers_select_all_toggle_when_items_are_valid(
    app_module, monkeypatch,
):
    room, rows = _room_print_rows()

    class FakeQuery:
        def all(self):
            return rows

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: room)
    monkeypatch.setattr(app_module, "_ruimte_inventory_query", lambda *args: FakeQuery())

    response = _csrf_client(app_module).get("/assistent/kamer/9/print/kanban")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="toggleAllButton"' in html
    assert "Alles selecteren" in html


def test_print_selectie_keeps_submitted_selection_instead_of_wiping_it(
    app_module, monkeypatch,
):
    """Ticket #24 AC: 'Als het versturen van een lege selectie wordt
    geprobeerd, blijft een eerder gemaakte selectie staan in plaats van
    gewist te worden.' A stale/invalid submitted id (e.g. an excluded item,
    which a real browser can't check since it's disabled — but a retried or
    crafted request could still send it) must still come back reflected in
    the re-rendered selection, not silently reset to nothing.
    """
    kast = SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9, naam="Kast A")
    company = SimpleNamespace(bedrijf_id=1, logo_url=None)  # geen logo -> ongeldig
    branch = SimpleNamespace(naam="Vestiging")
    room = SimpleNamespace(naam="Behandelkamer", nummer=None)
    room_type = SimpleNamespace(naam="Behandeling", kleur_hex="#123456")
    position = SimpleNamespace(
        voorraad_positie_id=12,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        locatie_foto_url=None,
    )
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        eigen_naam="Verband",
        foto_url="article.png",
        verpakkingseenheid_tekst="doos",
        kanban_min=3,
        kanban_refill_quantity=4,
    )
    rows = [(position, article, None, kast, room, room_type, company, branch)]

    class FakeQuery:
        def all(self):
            return rows

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: kast)
    monkeypatch.setattr(app_module, "_kast_inventory_query", lambda *args: FakeQuery())

    response = _csrf_client(app_module).post(
        "/assistent/kast/4/print/kanban",
        data={"_csrf_token": "test-csrf", "position_ids": ["12"]},
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200  # re-rendered, geen redirect
    assert "Selecteer minimaal één geldig Artikel om te printen." in html
    assert "1 geselecteerd" in html
    assert re.search(r'value="12"\s+checked', html)


def test_room_print_selection_processes_only_selected_valid_items(
    app_module,
    monkeypatch,
):
    room, rows = _room_print_rows()
    created_rows = []
    added = []
    commits = []

    class FakeQuery:
        def all(self):
            return rows

    class FakeSession:
        def add(self, item):
            added.append(item)

        def commit(self):
            commits.append(True)

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: room)
    monkeypatch.setattr(app_module, "_ruimte_inventory_query", lambda *args: FakeQuery())
    monkeypatch.setattr(
        app_module,
        "create_queue_item",
        lambda *row: created_rows.append(row) or "queue-item",
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/kamer/9/print/kanban",
        data={
            "_csrf_token": "test-csrf",
            "position_ids": ["101", "102", "104", "999"],
        },
    )

    assert response.status_code == 302
    assert [row[0].voorraad_positie_id for row in created_rows] == [102, 101]
    assert added == ["queue-item", "queue-item"]
    assert commits == [True]


def test_room_location_print_selection_processes_selected_standard_and_kanban(
    app_module,
    monkeypatch,
):
    room, rows = _room_print_rows()
    created_rows = []
    versions = {
        row[0].voorraad_positie_id: SimpleNamespace(
            status="PENDING_PRINT",
            printed_at=None,
            cancelled_at=None,
            artikelnaam=row[1].eigen_naam,
            artikel_foto_url="data:image/png;base64,QUJD",
            bedrijfslogo_url="data:image/png;base64,REVG",
            kamertype_naam="Behandeling",
            kamertype_kleur="#123456",
        )
        for row in rows
    }
    sent = []

    class FakeQuery:
        def all(self):
            return rows

    class FakeSession:
        def commit(self):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: room)
    monkeypatch.setattr(app_module, "_ruimte_inventory_query", lambda *args: FakeQuery())
    monkeypatch.setattr(
        app_module,
        "create_or_reuse_locatiekaart_version",
        lambda *row: created_rows.append(row) or (
            versions[row[0].voorraad_positie_id],
            True,
        ),
    )
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda selected_versions, print_batch_id: sent.append(
            (selected_versions, print_batch_id)
        ) or (
            True,
            None,
            {
                "printBatchId": print_batch_id,
                "jobId": "job-2",
                "status": "ACCEPTED",
                "cardCount": len(selected_versions),
                "sheetCount": 1,
            }
        ),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/kamer/9/print/locatie",
        data={
            "_csrf_token": "test-csrf",
            "position_ids": ["101", "103"],
        },
    )

    assert response.status_code == 302
    assert [row[0].voorraad_positie_id for row in created_rows] == [103, 101]
    assert [version.status for version in versions.values() if version.printed_at] == [
        "PRINTED",
        "PRINTED",
    ]
    assert len(sent) == 1
    assert sent[0][1]


def test_room_page_exposes_independent_print_actions_with_counts(
    app_module,
    monkeypatch,
):
    room, rows = _room_print_rows()
    room_type = rows[0][5]
    vestiging = SimpleNamespace(vestiging_id=6, naam="Vestiging Noord")
    class HashableNamespace(SimpleNamespace):
        __hash__ = object.__hash__

    kast = HashableNamespace(**rows[0][3].__dict__)
    content_rows = [rows[0][:3], rows[2][:3]]
    article = rows[0][1]

    class FakeField:
        def __eq__(self, other):
            return True

    class Query:
        def __init__(self, result=None, results=None):
            self.result = result
            self.results = results or []

        def filter(self, *args, **kwargs):
            return self

        def filter_by(self, **kwargs):
            return self

        def join(self, *args, **kwargs):
            return self

        def outerjoin(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def first(self):
            return self.result

        def all(self):
            return self.results

    class FakeSession:
        def query(self, *models):
            if len(models) == 1 and models[0] is app_module.Ruimte:
                return Query(result=room)
            if len(models) == 1 and models[0] is app_module.Ruimte_Type:
                return Query(result=room_type)
            if len(models) == 1 and models[0] is app_module.Vestiging:
                return Query(result=vestiging)
            if len(models) == 1 and models[0] is app_module.Kast:
                return Query(results=[kast])
            if len(models) == 1 and models[0] is app_module.Lokaal_Artikel:
                return Query(results=[article])
            return Query(results=content_rows)

    for name, fields in {
        "Voorraad_Positie": ["lokaal_artikel_id", "kast_id", "bedrijf_id"],
        "Lokaal_Artikel": ["lokaal_artikel_id", "global_id", "eigen_naam"],
        "Global_Catalogus": ["global_id"],
        "Kast": ["kast_id", "ruimte_id", "bedrijf_id", "naam"],
        "Ruimte": ["ruimte_id", "ruimte_type_id", "bedrijf_id"],
        "Ruimte_Type": ["ruimte_type_id", "bedrijf_id"],
        "Vestiging": ["vestiging_id", "bedrijf_id"],
    }.items():
        monkeypatch.setattr(
            app_module,
            name,
            SimpleNamespace(**{field: FakeField() for field in fields}),
        )
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = app_module.app.test_client().get("/assistent/kamer/9")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Kanban-kaartjes (1)" in html
    assert "Locatiekaartjes (2)" in html
    assert "/assistent/kamer/9/print/kanban" in html
    assert "/assistent/kamer/9/print/locatie" in html


def _two_item_kast_fixture():
    kast = SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9, naam="Kast A")
    room = SimpleNamespace(naam="Behandelkamer", nummer=None)
    room_type = SimpleNamespace(naam="Behandeling", kleur_hex="#123456")
    company = SimpleNamespace(bedrijf_id=1, logo_url="logo.png")
    branch = SimpleNamespace(naam="Vestiging")

    def position(position_id):
        return SimpleNamespace(
            voorraad_positie_id=position_id,
            lokaal_artikel_id=position_id,
            materiaaltype="STANDAARD",
            kanban_min_override=None,
            kanban_refill_quantity_override=None,
            locatie_foto_url=None,
        )

    def article(position_id, name):
        return SimpleNamespace(
            lokaal_artikel_id=position_id,
            eigen_naam=name,
            foto_url="standard.png",
            verpakkingseenheid_tekst="stuk",
            kanban_min=3,
            kanban_refill_quantity=4,
        )

    rows = [
        (position(13), article(13, "Naaldencontainer"), None, kast, room, room_type, company, branch),
        (position(14), article(14, "Pulseoximeter"), None, kast, room, room_type, company, branch),
    ]
    return kast, rows


def test_storage_location_location_print_marks_versions_printed_after_acceptance(
    app_module,
    monkeypatch,
):
    kast, rows = _two_item_kast_fixture()
    row = rows[0]
    version = SimpleNamespace(
        status="PENDING_PRINT",
        printed_at=None,
        cancelled_at=None,
        artikelnaam="Naaldencontainer",
        artikel_foto_url="data:image/png;base64,QUJD",
        bedrijfslogo_url="data:image/png;base64,REVG",
        kamertype_naam="Behandeling",
        kamertype_kleur="#123456",
    )
    created = []
    sent = []
    commits = []

    class FakeQuery:
        def all(self):
            return [row]

    class FakeSession:
        def commit(self):
            commits.append(True)

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: kast)
    monkeypatch.setattr(app_module, "_kast_inventory_query", lambda *args: FakeQuery())
    monkeypatch.setattr(
        app_module,
        "create_or_reuse_locatiekaart_version",
        lambda *source_row: created.append(source_row) or (version, True),
    )
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda versions, print_batch_id: sent.append((versions, print_batch_id))
        or (
            True,
            None,
            {
                "printBatchId": print_batch_id,
                "jobId": "job-1",
                "status": "ACCEPTED",
                "cardCount": 1,
                "sheetCount": 1,
            }
        ),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/kast/4/print/locatie",
        data={
            "_csrf_token": "test-csrf",
            "position_ids": ["13"],
            "printBatchId": "batch-1",
            "printBatchSelection": "13",
        },
    )

    assert response.status_code == 302
    assert [source_row[0].voorraad_positie_id for source_row in created] == [13]
    assert sent == [([version], "batch-1")]
    assert version.status == "PRINTED"
    assert version.printed_at is not None
    assert len(commits) == 2


def test_storage_location_location_print_keeps_pending_on_service_failure(
    app_module,
    monkeypatch,
):
    kast, rows = _two_item_kast_fixture()
    row = rows[0]
    version = SimpleNamespace(
        status="PENDING_PRINT",
        printed_at=None,
        cancelled_at=None,
        artikelnaam="Naaldencontainer",
        artikel_foto_url="data:image/png;base64,QUJD",
        bedrijfslogo_url="data:image/png;base64,REVG",
        kamertype_naam="Behandeling",
        kamertype_kleur="#123456",
    )
    commits = []

    class FakeQuery:
        def all(self):
            return [row]

    class FakeSession:
        def commit(self):
            commits.append(True)

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: kast)
    monkeypatch.setattr(app_module, "_kast_inventory_query", lambda *args: FakeQuery())
    monkeypatch.setattr(
        app_module,
        "create_or_reuse_locatiekaart_version",
        lambda *source_row: (version, True),
    )
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda versions, print_batch_id: (False, "A4-service offline", None),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/kast/4/print/locatie",
        data={
            "_csrf_token": "test-csrf",
            "position_ids": ["13"],
            "printBatchId": "retry-batch",
            "printBatchSelection": "13",
        },
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "A4-service offline" in html
    assert version.status == "PENDING_PRINT"
    assert 'name="printBatchId" value="retry-batch"' in html
    assert 'name="printBatchSelection" value="13"' in html
    assert len(commits) == 1


def test_storage_location_location_print_skips_articles_with_unresolvable_images_but_prints_rest(
    app_module,
    monkeypatch,
):
    kast, rows = _two_item_kast_fixture()
    versions = {
        13: SimpleNamespace(
            status="PENDING_PRINT",
            printed_at=None,
            cancelled_at=None,
            artikelnaam="Naaldencontainer",
            artikel_foto_url="data:image/png;base64,QUJD",
            bedrijfslogo_url="data:image/png;base64,REVG",
            kamertype_naam="Behandeling",
            kamertype_kleur="#123456",
        ),
        14: SimpleNamespace(
            status="PENDING_PRINT",
            printed_at=None,
            cancelled_at=None,
            artikelnaam="Pulseoximeter",
            artikel_foto_url=None,
            bedrijfslogo_url="data:image/png;base64,REVG",
            kamertype_naam="Behandeling",
            kamertype_kleur="#123456",
        ),
    }
    sent = []
    commits = []

    class FakeQuery:
        def all(self):
            return rows

    class FakeSession:
        def commit(self):
            commits.append(True)

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: kast)
    monkeypatch.setattr(app_module, "_kast_inventory_query", lambda *args: FakeQuery())
    monkeypatch.setattr(
        app_module,
        "create_or_reuse_locatiekaart_version",
        lambda *source_row: (versions[source_row[0].voorraad_positie_id], True),
    )
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda selected_versions, print_batch_id: sent.append(
            (selected_versions, print_batch_id)
        ) or (
            True,
            None,
            {
                "printBatchId": print_batch_id,
                "jobId": "job-3",
                "status": "ACCEPTED",
                "cardCount": len(selected_versions),
                "sheetCount": 1,
            },
        ),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    client = _csrf_client(app_module)
    response = client.post(
        "/assistent/kast/4/print/locatie",
        data={
            "_csrf_token": "test-csrf",
            "position_ids": ["13", "14"],
        },
    )

    assert response.status_code == 302
    assert len(sent) == 1
    assert sent[0][0] == [versions[13]]
    assert versions[13].status == "PRINTED"
    assert versions[14].status == "PENDING_PRINT"
    with client.session_transaction() as session:
        flashes = session.get("_flashes", [])
    flash_messages = " ".join(message for _, message in flashes)
    assert "1 kaartje(s) overgeslagen" in flash_messages
    assert "Pulseoximeter" in flash_messages


def test_storage_location_location_print_fails_when_all_selected_cards_are_unprintable(
    app_module,
    monkeypatch,
):
    kast, rows = _two_item_kast_fixture()
    version = SimpleNamespace(
        status="PENDING_PRINT",
        printed_at=None,
        cancelled_at=None,
        artikelnaam="Naaldencontainer",
        artikel_foto_url=None,
        bedrijfslogo_url="data:image/png;base64,REVG",
        kamertype_naam="Behandeling",
        kamertype_kleur="#123456",
    )
    send_calls = []
    commits = []

    class FakeQuery:
        def all(self):
            return rows[:1]

    class FakeSession:
        def commit(self):
            commits.append(True)

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: kast)
    monkeypatch.setattr(app_module, "_kast_inventory_query", lambda *args: FakeQuery())
    monkeypatch.setattr(
        app_module,
        "create_or_reuse_locatiekaart_version",
        lambda *source_row: (version, True),
    )
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda *args, **kwargs: send_calls.append(True),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/kast/4/print/locatie",
        data={
            "_csrf_token": "test-csrf",
            "position_ids": ["13"],
        },
    )

    assert response.status_code == 200
    assert send_calls == []
    assert version.status == "PENDING_PRINT"
    html = response.get_data(as_text=True)
    assert "Geen enkele kaart is printbaar" in html


def test_storage_location_location_print_mints_new_batch_id_when_retry_selection_changes(
    app_module,
    monkeypatch,
):
    kast, rows = _two_item_kast_fixture()
    versions = {
        13: SimpleNamespace(
            status="PENDING_PRINT",
            printed_at=None,
            cancelled_at=None,
            artikelnaam="Naaldencontainer",
            artikel_foto_url="data:image/png;base64,QUJD",
            bedrijfslogo_url="data:image/png;base64,REVG",
            kamertype_naam="Behandeling",
            kamertype_kleur="#123456",
        ),
    }
    captured_batch_ids = []

    class FakeQuery:
        def all(self):
            return rows

    class FakeSession:
        def commit(self):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: kast)
    monkeypatch.setattr(app_module, "_kast_inventory_query", lambda *args: FakeQuery())
    monkeypatch.setattr(
        app_module,
        "create_or_reuse_locatiekaart_version",
        lambda *source_row: (versions[source_row[0].voorraad_positie_id], True),
    )
    monkeypatch.setattr(
        app_module,
        "send_location_cards_to_print_service",
        lambda selected_versions, print_batch_id: captured_batch_ids.append(
            print_batch_id
        ) or (
            True,
            None,
            {
                "printBatchId": print_batch_id,
                "jobId": "job-4",
                "status": "ACCEPTED",
                "cardCount": len(selected_versions),
                "sheetCount": 1,
            },
        ),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/kast/4/print/locatie",
        data={
            "_csrf_token": "test-csrf",
            "position_ids": ["13"],
            "printBatchId": "stale-batch",
            "printBatchSelection": "13,14",
        },
    )

    assert response.status_code == 302
    assert captured_batch_ids == [captured_batch_ids[0]]
    assert captured_batch_ids[0] != "stale-batch"


class _CatalogQuery:
    def __init__(self, global_item):
        self.global_item = global_item

    def filter(self, *conditions):
        return self

    def join(self, *args):
        return self

    def first(self):
        return self.global_item

    def all(self):
        return []


def test_new_position_defaults_to_kanban_material(app_module, monkeypatch):
    created = []

    class FakePosition:
        def __init__(self, **values):
            self.__dict__.update(values)
            self.voorraad_positie_id = 12
            created.append(self)

    kast = SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9)
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        bedrijf_id=1,
        kanban_min=3,
        kanban_refill_quantity=4,
    )

    class EmptyQuery:
        def filter_by(self, **values):
            return self

        def first(self):
            return None

    class FakeSession:
        def query(self, model):
            return EmptyQuery()

        def add(self, item):
            return None

        def flush(self):
            return None

        def commit(self):
            return None

    def scoped_item(model, item_id, bedrijf_id):
        return {4: kast, 7: article}.get(item_id)

    monkeypatch.setattr(app_module, "Voorraad_Positie", FakePosition)
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", scoped_item)
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/kast/4/toevoegen",
        data={
            "_csrf_token": "test-csrf",
            "artikel_id": "7",
        },
    )

    assert response.status_code == 302
    assert len(created) == 1
    assert created[0].materiaaltype == "KANBAN"
    assert created[0].kanban_min_override is None
    assert created[0].kanban_refill_quantity_override is None
    assert getattr(created[0], "trigger_min", None) is None
    assert getattr(created[0], "target_max", None) is None


def test_new_position_uses_the_current_article_standard_after_a_change(
    app_module,
):
    position = SimpleNamespace(
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
    )
    article = SimpleNamespace(kanban_min=3, kanban_refill_quantity=4)

    assert app_module.effective_position_kanban_settings(
        position,
        article,
    ) == KanbanStandard(3, 4)


def test_expand_schema_adds_new_nullable_columns_without_rewriting_legacy_data(
    app_module,
):
    with app_module.app.app_context():
        app_module.db.session.execute(text(
            "CREATE TABLE Lokaal_Artikel (lokaal_artikel_id INTEGER PRIMARY KEY)"
        ))
        app_module.db.session.execute(text(
            "CREATE TABLE Voorraad_Positie (voorraad_positie_id INTEGER PRIMARY KEY, "
            "trigger_min INTEGER, target_max INTEGER)"
        ))
        app_module.db.session.execute(text(
            "CREATE TABLE Print_Queue (print_id INTEGER PRIMARY KEY, "
            "max_level INTEGER)"
        ))
        app_module.db.session.commit()

        app_module.ensure_kanban_settings_schema()

        inspector = inspect(app_module.db.engine)
        article_columns = {
            column["name"]
            for column in inspector.get_columns("Lokaal_Artikel")
        }
        position_columns = {
            column["name"]
            for column in inspector.get_columns("Voorraad_Positie")
        }
        queue_columns = {
            column["name"]
            for column in inspector.get_columns("Print_Queue")
        }

    assert {"kanban_min", "kanban_refill_quantity"} <= article_columns
    assert {
        "materiaaltype",
        "kanban_min_override",
        "kanban_refill_quantity_override",
    } <= position_columns
    assert "refill_quantity" in queue_columns


def test_position_switch_to_standard_clears_all_kanban_values(app_module, monkeypatch):
    position = SimpleNamespace(
        voorraad_positie_id=12,
        bedrijf_id=1,
        kast_id=4,
        lokaal_artikel_id=7,
        materiaaltype="KANBAN",
        kanban_min_override=5,
        kanban_refill_quantity_override=2,
        trigger_min=5,
    )
    kast = SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9)
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        bedrijf_id=1,
        kanban_min=3,
        kanban_refill_quantity=4,
    )

    class FakeSession:
        def commit(self):
            return None

    def scoped_item(model, item_id, bedrijf_id):
        return {4: kast, 7: article, 12: position}.get(item_id)

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", scoped_item)
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/update-voorraad/12",
        data={
            "_csrf_token": "test-csrf",
            "materiaaltype": "STANDAARD",
            "kanban_min_override": "8",
            "kanban_refill_quantity_override": "9",
        },
    )

    assert response.status_code == 302
    assert position.kanban_min_override is None
    assert position.kanban_refill_quantity_override is None
    assert position.strategie == "STANDARD"


def test_standard_material_cannot_create_a_kanban_queue_item(app_module):
    position = SimpleNamespace(
        materiaaltype="STANDAARD",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
    )
    article = SimpleNamespace(kanban_min=3, kanban_refill_quantity=4)
    room = SimpleNamespace(naam="Behandelkamer", nummer=None)

    with pytest.raises(KanbanSettingsError, match="Standaard materiaal"):
        app_module.create_queue_item(
            position,
            article,
            None,
            SimpleNamespace(naam="Kast", type_opslag="GRIJP"),
            room,
            None,
            None,
        )


def test_article_usage_api_returns_effective_values_and_inheritance_state(
    app_module, monkeypatch
):
    inherited = SimpleNamespace(
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        trigger_min=1,
        target_max=2,
    )
    local_min = SimpleNamespace(
        materiaaltype="KANBAN",
        kanban_min_override=5,
        kanban_refill_quantity_override=None,
        trigger_min=5,
        target_max=6,
    )
    standard = SimpleNamespace(
        materiaaltype="STANDAARD",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        trigger_min=None,
        target_max=None,
    )
    kast = SimpleNamespace(naam="Kast A")
    room = SimpleNamespace(naam="Behandelkamer")
    article = SimpleNamespace(kanban_min=3, kanban_refill_quantity=4)

    class UsageQuery:
        def join(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return [
                (inherited, kast, room),
                (local_min, kast, room),
                (standard, kast, room),
            ]

    class FakeSession:
        def query(self, *models):
            return UsageQuery()

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: article)
    monkeypatch.setattr(
        app_module,
        "Voorraad_Positie",
        SimpleNamespace(
            voorraad_positie_id=object(),
            lokaal_artikel_id=object(),
            kast_id=object(),
            bedrijf_id=object(),
        ),
    )
    monkeypatch.setattr(app_module, "Kast", SimpleNamespace(kast_id=object(), ruimte_id=object()))
    monkeypatch.setattr(app_module, "Ruimte", SimpleNamespace(ruimte_id=object()))
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = app_module.app.test_client().get("/api/artikel-gebruik/7")

    assert response.status_code == 200
    data = response.get_json()
    assert data[0]["min"] == 3
    assert data[0]["aanv"] == 4
    assert data[0]["min_erft_standaard"] is True
    assert data[0]["aanv_erft_standaard"] is True
    assert data[0]["erft_standaard"] is True
    assert data[1]["min"] == 5
    assert data[1]["aanv"] == 4
    assert data[1]["min_erft_standaard"] is False
    assert data[1]["aanv_erft_standaard"] is True
    assert data[1]["erft_standaard"] is False
    assert data[2]["materiaaltype"] == "STANDAARD"
    assert data[2]["min"] is None
    assert data[2]["aanv"] is None
    assert all("max" not in item for item in data)


def test_old_storage_location_url_redirects_to_room_with_it_expanded(
    app_module, monkeypatch
):
    """Ticket #14: the standalone Opslaglocatie detail page is gone. An old
    /assistent/kast/<id> link (bookmark, printed QR, etc.) must land on the
    Ruimte with that Opslaglocatie already expanded, not on a dead page —
    and never render kast_inhoud.html content directly.
    """
    kast = SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9, naam="Kast A")

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: kast)

    response = app_module.app.test_client().get("/assistent/kast/4")

    assert response.status_code == 302
    assert response.headers["Location"] == "/assistent/kamer/9?open_kast=4"


def test_old_storage_location_url_falls_back_to_mijn_ruimtes_when_not_found(
    app_module, monkeypatch
):
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: None)

    response = app_module.app.test_client().get("/assistent/kast/999")

    assert response.status_code == 302
    assert response.headers["Location"] == "/assistent/kamers"


def test_old_storage_location_overview_url_redirects_to_mijn_ruimtes(
    app_module, monkeypatch
):
    response = app_module.app.test_client().get("/assistent/kasten")

    assert response.status_code == 302
    assert response.headers["Location"] == "/assistent/kamers"


def test_print_selectie_unknown_kast_redirects_to_mijn_ruimtes(app_module, monkeypatch):
    """Ticket #14: an error inside the print-selection flow must land on
    Mijn Ruimtes now, not on the deleted Opslaglocatie overview.
    """
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: None)

    response = app_module.app.test_client().get("/assistent/kast/4/print/kanban")

    assert response.status_code == 302
    assert response.headers["Location"] == "/assistent/kamers"


def test_print_selectie_unknown_kaart_type_redirects_to_mijn_ruimtes(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)

    response = app_module.app.test_client().get("/assistent/kast/4/print/onbekend")

    assert response.status_code == 302
    assert response.headers["Location"] == "/assistent/kamers"


def test_kanban_aanvragen_enkel_falls_back_to_mijn_ruimtes_without_referer(
    app_module, monkeypatch
):
    """Ticket #14: without a Referer header (privacy settings, a direct
    POST), the final redirect must not become redirect(None).
    """
    position = SimpleNamespace(voorraad_positie_id=12, materiaaltype="KANBAN")
    row = (position, "artikel", None, "kast", "ruimte", "ruimte_type", "bedrijf")

    class Query:
        def join(self, *args, **kwargs):
            return self

        def outerjoin(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return row

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "db", SimpleNamespace(
        session=SimpleNamespace(
            query=lambda *models: Query(),
            add=lambda item: None,
            commit=lambda: None,
            rollback=lambda: None,
        )
    ))
    monkeypatch.setattr(app_module, "create_queue_item", lambda *row: "queue-item")

    response = _csrf_client(app_module).post(
        "/assistent/kanban/aanvragen/enkel/12",
        data={"_csrf_token": "test-csrf"},
        headers={},  # geen Referer
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/assistent/kamers"


def test_assistent_kamers_shows_flash_when_loading_fails(app_module, monkeypatch):
    """Ticket #14: a failure while loading Mijn Ruimtes must be visible,
    not a silent fallback to an empty dashboard.
    """
    def raise_query(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module,
        "db",
        SimpleNamespace(session=SimpleNamespace(query=raise_query)),
    )

    response = app_module.app.test_client().get(
        "/assistent/kamers", follow_redirects=True
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Mijn Ruimtes kon niet worden geladen." in html


def test_room_page_opens_requested_kast_via_open_kast_param(app_module, monkeypatch):
    """Ticket #14: ?open_kast=<id> must actually control which
    Opslaglocatie is expanded, not just the first one.
    """
    room = SimpleNamespace(
        ruimte_id=9, bedrijf_id=1, ruimte_type_id=None, vestiging_id=6,
        nummer=None, naam="Behandelkamer", kleur_hex=None,
    )
    vestiging = SimpleNamespace(naam="Vestiging Noord")

    class HashableNamespace(SimpleNamespace):
        __hash__ = object.__hash__

    kast_a = HashableNamespace(kast_id=4, naam="Kast A", ruimte_id=9)
    kast_b = HashableNamespace(kast_id=5, naam="Kast B", ruimte_id=9)

    class Query:
        def __init__(self, first_value=None, all_values=None):
            self.first_value = first_value
            self.all_values = all_values or []

        def filter(self, *args, **kwargs):
            return self

        def filter_by(self, **kwargs):
            return self

        def join(self, *args, **kwargs):
            return self

        def outerjoin(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def first(self):
            return self.first_value

        def all(self):
            return self.all_values

    class FakeSession:
        def query(self, *models):
            model = models[0] if models else None
            if model is app_module.Ruimte:
                return Query(first_value=room)
            if model is app_module.Vestiging:
                return Query(first_value=vestiging)
            if model is app_module.Kast:
                return Query(all_values=[kast_a, kast_b])
            return Query(all_values=[])

    class FakeField:
        def __eq__(self, other):
            return True

    for name, fields in {
        "Ruimte": ["ruimte_id", "bedrijf_id"],
        "Vestiging": ["vestiging_id", "bedrijf_id"],
        "Kast": ["kast_id", "ruimte_id", "bedrijf_id", "naam"],
        "Voorraad_Positie": ["kast_id", "bedrijf_id", "lokaal_artikel_id"],
        "Lokaal_Artikel": ["lokaal_artikel_id", "global_id", "eigen_naam"],
        "Global_Catalogus": ["global_id"],
    }.items():
        monkeypatch.setattr(
            app_module,
            name,
            SimpleNamespace(**{field: FakeField() for field in fields}),
        )
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = app_module.app.test_client().get("/assistent/kamer/9?open_kast=5")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Kast B's accordion is open (no "collapsed"), Kast A's is collapsed.
    heading_b = html.index("Kast B")
    heading_a = html.index("Kast A")
    assert "collapsed" not in html[max(0, heading_b - 400):heading_b]
    assert "collapsed" in html[max(0, heading_a - 400):heading_a]


def test_effective_change_supersedes_existing_kanban_card(
    app_module, monkeypatch
):
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        bedrijf_id=1,
        eigen_naam="Verband",
        verpakkingseenheid_tekst="doos",
        kanban_min=1,
        kanban_refill_quantity=1,
    )
    position = SimpleNamespace(
        lokaal_artikel_id=7,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        trigger_min=1,
        target_max=2,
    )
    card = SimpleNamespace(status="PRINTED")

    class CardQuery:
        def join(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return [(card, position)]

    class FakeSession:
        def query(self, *models):
            return CardQuery()

        def commit(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: article)
    monkeypatch.setattr(
        app_module,
        "Voorraad_Positie",
        SimpleNamespace(
            voorraad_positie_id=object(),
            lokaal_artikel_id=object(),
        ),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/artikelen-beheer",
        data={
            "_csrf_token": "test-csrf",
            "actie": "bewerk_artikel",
            "artikel_id": "7",
            "naam": "Verband",
            "eenheid": "doos",
            "kanban_min": "3",
            "kanban_refill_quantity": "4",
        },
    )

    assert response.status_code == 302
    assert card.status == "SUPERSEDED"


def test_effective_change_keeps_card_for_position_with_local_deviation(
    app_module, monkeypatch
):
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        bedrijf_id=1,
        eigen_naam="Verband",
        verpakkingseenheid_tekst="doos",
        kanban_min=1,
        kanban_refill_quantity=1,
    )
    position = SimpleNamespace(
        lokaal_artikel_id=7,
        materiaaltype="KANBAN",
        kanban_min_override=5,
        kanban_refill_quantity_override=2,
        trigger_min=5,
        target_max=7,
    )
    card = SimpleNamespace(status="PRINTED")

    class CardQuery:
        def join(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return [(card, position)]

    class FakeSession:
        def query(self, *models):
            return CardQuery()

        def commit(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: article)
    monkeypatch.setattr(
        app_module,
        "Voorraad_Positie",
        SimpleNamespace(
            voorraad_positie_id=object(),
            lokaal_artikel_id=object(),
        ),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/artikelen-beheer",
        data={
            "_csrf_token": "test-csrf",
            "actie": "bewerk_artikel",
            "artikel_id": "7",
            "naam": "Verband",
            "eenheid": "doos",
            "kanban_min": "3",
            "kanban_refill_quantity": "4",
        },
    )

    assert response.status_code == 302
    assert card.status == "PRINTED"


def test_print_service_payload_uses_refill_quantity_without_max_level(
    app_module, monkeypatch
):
    captured = {}

    class FakeResponse:
        status_code = 202

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ACCEPTED", "jobId": "job-7"}

    def fake_post(url, json, headers, timeout, allow_redirects):
        captured["payload"] = json
        return FakeResponse()

    card = SimpleNamespace(
        kaart_id="kaart-7",
        voorraad_positie_id=12,
        status="PRINTED",
    )
    position = SimpleNamespace(
        voorraad_positie_id=12,
        lokaal_artikel_id=7,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
    )
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        kanban_min=3,
        kanban_refill_quantity=4,
    )

    class FakeField:
        def __eq__(self, other):
            return True

        def in_(self, values):
            return True

    class CardQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return card

    class SourceQuery:
        def outerjoin(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return card, position, article

    class FakeSession:
        def query(self, *models):
            return CardQuery() if len(models) == 1 else SourceQuery()

    queue_item = SimpleNamespace(
        kaart_id="kaart-7",
        printer_id="reception-badgy-01",
        card_type="KANBAN_TWO_BIN",
        header_text="KAMER",
        header_color="#123456",
        product_name="Verband",
        product_packaging="doos",
        product_sku="7",
        product_image_url="data:image/png;base64,AA==",
        company_logo_url="data:image/png;base64,AA==",
        location_text="Kast A",
        min_level=99,
        refill_quantity=99,
        max_level=7,
        qr_code_value="https://example.test/scan/token",
        qr_human_readable="KB-1234",
    )

    monkeypatch.setattr(app_module, "PRINT_SERVICE_URL", "http://print.test")
    monkeypatch.setattr(app_module, "PRINT_SERVICE_REQUIRE_API_KEY", False)
    monkeypatch.setattr(app_module.requests, "post", fake_post)
    monkeypatch.setattr(
        app_module,
        "KanbanKaart",
        SimpleNamespace(
            kaart_id=FakeField(),
            voorraad_positie_id=FakeField(),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "Voorraad_Positie",
        SimpleNamespace(
            voorraad_positie_id=FakeField(),
            lokaal_artikel_id=FakeField(),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "Lokaal_Artikel",
        SimpleNamespace(lokaal_artikel_id=FakeField()),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    sent, error = app_module.send_queue_item_to_print_service(queue_item)

    assert sent is True
    assert error is None
    logistics = captured["payload"]["data"]["logistics"]
    assert logistics == {"location": "Kast A", "minLevel": 3, "refillQuantity": 4}
    assert "maxLevel" not in str(captured["payload"])


def test_user_facing_print_route_sends_to_fake_print_service(
    app_module, monkeypatch
):
    captured = {}
    queue_item = SimpleNamespace(
        print_id=7,
        bedrijf_id=1,
        status="PENDING",
        kaart_id="kaart-7",
        printer_id="reception-badgy-01",
        card_type="KANBAN_TWO_BIN",
        header_text="KAMER",
        header_color="#123456",
        product_name="Verband",
        product_packaging="doos",
        product_sku="7",
        product_image_url="data:image/png;base64,AA==",
        company_logo_url="data:image/png;base64,AA==",
        location_text="Kast A",
        min_level=99,
        refill_quantity=99,
        qr_code_value="https://example.test/scan/token",
        qr_human_readable="KB-1234",
    )

    class FakeField:
        def __eq__(self, other):
            return True

    class QueueQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return queue_item

    card = SimpleNamespace(
        kaart_id="kaart-7",
        voorraad_positie_id=12,
        status="PENDING_PRINT",
    )
    position = SimpleNamespace(
        voorraad_positie_id=12,
        lokaal_artikel_id=7,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
    )
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        kanban_min=3,
        kanban_refill_quantity=4,
    )

    class SourceQuery:
        def outerjoin(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return card, position, article

    class CardQuery(QueueQuery):
        def first(self):
            return card

    class FakeSession:
        def query(self, *models):
            if models and models[0] is app_module.Print_Queue:
                return QueueQuery()
            if len(models) == 1:
                return CardQuery()
            return SourceQuery()

        def delete(self, item):
            captured["deleted"] = item

        def commit(self):
            captured["committed"] = True

    class FakeResponse:
        status_code = 202

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ACCEPTED", "jobId": "job-7"}

    def fake_post(url, json, headers, timeout, allow_redirects):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module,
        "test_print_service_connectivity",
        lambda: (True, "ok"),
    )
    monkeypatch.setattr(app_module, "PRINT_SERVICE_URL", "http://print.test")
    monkeypatch.setattr(app_module, "PRINT_SERVICE_REQUIRE_API_KEY", False)
    monkeypatch.setattr(app_module.requests, "post", fake_post)
    monkeypatch.setattr(
        app_module,
        "Print_Queue",
        SimpleNamespace(
            print_id=FakeField(),
            bedrijf_id=FakeField(),
            status=FakeField(),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "KanbanKaart",
        SimpleNamespace(
            kaart_id=FakeField(),
            voorraad_positie_id=FakeField(),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "Voorraad_Positie",
        SimpleNamespace(
            voorraad_positie_id=FakeField(),
            lokaal_artikel_id=FakeField(),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "Lokaal_Artikel",
        SimpleNamespace(lokaal_artikel_id=FakeField()),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/assistent/print-queue/verstuur/7",
        data={"_csrf_token": "test-csrf"},
    )

    assert response.status_code == 302
    assert captured["deleted"] is queue_item
    assert captured["committed"] is True
    assert card.status == "PRINTED"
    assert captured["payload"]["data"]["logistics"] == {
        "location": "Kast A",
        "minLevel": 3,
        "refillQuantity": 4,
    }


def test_superseded_queue_item_is_not_sent_to_print_service(
    app_module, monkeypatch
):
    card = SimpleNamespace(status="SUPERSEDED")

    class CardQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return card

    class FakeSession:
        def query(self, *models):
            return CardQuery()

    queue_item = SimpleNamespace(kaart_id="kaart-7")
    post_called = []

    monkeypatch.setattr(app_module, "PRINT_SERVICE_URL", "http://print.test")
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))
    monkeypatch.setattr(
        app_module.requests,
        "post",
        lambda *args, **kwargs: post_called.append(True),
    )

    sent, error = app_module.send_queue_item_to_print_service(queue_item)

    assert sent is False
    assert "verouderd" in error
    assert post_called == []


def test_print_queue_view_uses_effective_min_and_refill_without_max(
    app_module, monkeypatch
):
    queue_item = SimpleNamespace(
        print_id=7,
        bedrijf_id=1,
        status="PENDING",
        printer_id="reception-badgy-01",
        card_type="KANBAN_TWO_BIN",
        header_text="KAMER",
        header_color="#123456",
        product_name="Verband",
        product_packaging="doos",
        product_sku="7",
        product_image_url="data:image/png;base64,AA==",
        company_logo_url="data:image/png;base64,AA==",
        location_text="Kast A",
        min_level=3,
        refill_quantity=4,
        max_level=7,
        qr_code_value="https://example.test/scan/token",
        qr_human_readable="KB-1234",
        aangemaakt_op=datetime.datetime(2026, 8, 28, 12, 0),
    )

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_preview_layout", lambda: ({}, False, None))

    class FakeField:
        def __eq__(self, other):
            return True

        def desc(self):
            return self

    monkeypatch.setattr(
        app_module,
        "Print_Queue",
        SimpleNamespace(
            bedrijf_id=FakeField(),
            status=FakeField(),
            aangemaakt_op=FakeField(),
        ),
    )

    class FakeQueue:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return [queue_item]

    class FakeSession:
        def query(self, *models):
            return FakeQueue()

    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = app_module.app.test_client().get("/assistent/print-queue")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Min 3" in html
    assert "Aanv. 4" in html
    assert "Max" not in html


def test_print_queue_view_precomputes_linked_card_settings(
    app_module, monkeypatch
):
    queue_item = SimpleNamespace(
        print_id=7,
        bedrijf_id=1,
        status="PENDING",
        kaart_id="kaart-7",
        header_text="KAMER",
        header_color="#123456",
        product_name="Verband",
        product_packaging="doos",
        product_sku="7",
        product_image_url=None,
        company_logo_url=None,
        location_text="Kast A",
        min_level=99,
        refill_quantity=99,
        qr_code_value="https://example.test/scan/token",
        qr_human_readable="KB-1234",
        aangemaakt_op=datetime.datetime(2026, 8, 28, 12, 0),
    )
    card = SimpleNamespace(kaart_id="kaart-7", status="PENDING_PRINT")
    position = SimpleNamespace(
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
    )
    article = SimpleNamespace(kanban_min=3, kanban_refill_quantity=4)

    class FakeField:
        def __eq__(self, other):
            return True

        def in_(self, values):
            return True

        def desc(self):
            return self

    class QueueQuery:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return [queue_item]

    class SourceQuery:
        def outerjoin(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return [(card, position, article)]

    class FakeSession:
        def query(self, *models):
            return QueueQuery() if len(models) == 1 else SourceQuery()

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_preview_layout", lambda: ({}, False, None))
    monkeypatch.setattr(
        app_module,
        "Print_Queue",
        SimpleNamespace(
            bedrijf_id=FakeField(),
            status=FakeField(),
            aangemaakt_op=FakeField(),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "KanbanKaart",
        SimpleNamespace(
            kaart_id=FakeField(),
            voorraad_positie_id=FakeField(),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "Voorraad_Positie",
        SimpleNamespace(
            voorraad_positie_id=FakeField(),
            lokaal_artikel_id=FakeField(),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "Lokaal_Artikel",
        SimpleNamespace(lokaal_artikel_id=FakeField()),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = app_module.app.test_client().get("/assistent/print-queue")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Min 3" in html
    assert "Aanv. 4" in html
    assert "Min 99" not in html
    assert "Aanv. 99" not in html


def test_room_page_renders_effective_kanban_values(
    app_module, monkeypatch
):
    room = SimpleNamespace(
        ruimte_id=9,
        bedrijf_id=1,
        ruimte_type_id=8,
        vestiging_id=6,
        nummer=None,
        naam="Behandelkamer",
        kleur_hex=None,
    )
    room_type = SimpleNamespace(kleur_hex="#123456")
    vestiging = SimpleNamespace(vestiging_id=6, naam="Vestiging Noord")
    class FakeKast:
        kast_id = 4
        ruimte_id = 9
        naam = "Kast A"
        type_opslag = "GRIJP"

    storage_location = FakeKast()
    position = SimpleNamespace(
        voorraad_positie_id=12,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
    )
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        eigen_naam="Verband",
        verpakkingseenheid_tekst="doos",
        foto_url=None,
        kanban_min=3,
        kanban_refill_quantity=4,
    )

    class FakeField:
        def __eq__(self, other):
            return True

        def order_by(self, *args, **kwargs):
            return self

    class Query:
        def __init__(self, first_value=None, all_values=None):
            self.first_value = first_value
            self.all_values = all_values or []

        def filter(self, *args, **kwargs):
            return self

        def filter_by(self, **kwargs):
            return self

        def join(self, *args, **kwargs):
            return self

        def outerjoin(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def first(self):
            return self.first_value

        def all(self):
            return self.all_values

    monkeypatch.setattr(
        app_module,
        "Ruimte",
        SimpleNamespace(ruimte_id=FakeField(), bedrijf_id=FakeField()),
    )
    monkeypatch.setattr(
        app_module,
        "Ruimte_Type",
        SimpleNamespace(ruimte_type_id=FakeField(), bedrijf_id=FakeField()),
    )
    monkeypatch.setattr(
        app_module,
        "Vestiging",
        SimpleNamespace(vestiging_id=FakeField(), bedrijf_id=FakeField()),
    )
    monkeypatch.setattr(
        app_module,
        "Kast",
        SimpleNamespace(
            kast_id=FakeField(),
            ruimte_id=FakeField(),
            bedrijf_id=FakeField(),
            naam=FakeField(),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "Voorraad_Positie",
        SimpleNamespace(
            lokaal_artikel_id=FakeField(),
            kast_id=FakeField(),
            bedrijf_id=FakeField(),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "Lokaal_Artikel",
        SimpleNamespace(
            lokaal_artikel_id=FakeField(),
            global_id=FakeField(),
            eigen_naam=FakeField(),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "Global_Catalogus",
        SimpleNamespace(global_id=FakeField()),
    )

    class FakeSession:
        def query(self, *models):
            model = models[0] if models else None
            if model is app_module.Ruimte:
                return Query(first_value=room)
            if model is app_module.Ruimte_Type:
                return Query(first_value=room_type)
            if model is app_module.Vestiging:
                return Query(first_value=vestiging)
            if model is app_module.Kast:
                return Query(all_values=[storage_location])
            if len(models) == 3:
                return Query(all_values=[(position, article, None)])
            if model is app_module.Lokaal_Artikel:
                return Query(all_values=[article])
            return Query()

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = app_module.app.test_client().get("/assistent/kamer/9")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Behandelkamer" in html
    assert 'value="3"' in html
    assert 'value="4"' in html
    assert "Max:" not in html


def test_inventory_and_scan_reports_render_effective_kanban_values(
    app_module,
):
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        eigen_naam="Verband",
        verpakkingseenheid_tekst="doos",
        foto_url=None,
        kanban_min=3,
        kanban_refill_quantity=4,
    )
    position = SimpleNamespace(
        voorraad_positie_id=12,
        materiaaltype="KANBAN",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        locatie_foto_url=None,
    )
    standard_position = SimpleNamespace(
        voorraad_positie_id=13,
        materiaaltype="STANDAARD",
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        locatie_foto_url=None,
    )
    storage_location = SimpleNamespace(naam="Kast A", type_opslag="GRIJP")
    room = SimpleNamespace(naam="Behandelkamer", nummer=None)
    room_type = SimpleNamespace(naam="Behandeling", kleur_hex="#123456")
    branch = SimpleNamespace(naam="Vestiging")
    inventory_rows = [
        (position, article, None, storage_location, room, room_type, branch),
        (standard_position, article, None, storage_location, room, room_type, branch),
    ]
    card = SimpleNamespace(product_name="Verband", product_sku="7", human_code="KB-1234")
    scan_item = SimpleNamespace(last_scanned_at=datetime.datetime(2026, 8, 28, 12, 0), scan_count=1)
    scan_rows = [
        (scan_item, card, position, article, None, storage_location, room, room_type, None, branch),
    ]
    grouped_rows = [{
        "key": 1,
        "naam": "Vestiging",
        "ruimte_types": [{
            "key": 2,
            "naam": "Behandeling",
            "kleur_hex": "#123456",
            "ruimtes": [{
                "key": 9,
                "naam": "Behandelkamer",
                "nummer": None,
                "kasten": [{
                    "naam": "Kast A",
                    "type_opslag": "GRIJP",
                    "inventory_rows": inventory_rows,
                    "scan_rows": scan_rows,
                }],
            }],
        }],
    }]

    with app_module.app.test_request_context("/"):
        inventory_html = app_module.render_template(
            "assistent_kamerlijst.html",
            grouped_rows=grouped_rows,
        )
        inventory_print_html = app_module.render_template(
            "assistent_kamerlijst_print.html",
            grouped_rows=grouped_rows,
            selected_room=room,
            generated_at=datetime.datetime(2026, 8, 28, 12, 0),
        )
        scan_html = app_module.render_template(
            "assistent_scanlijst.html",
            grouped_rows=grouped_rows,
        )
        scan_print_html = app_module.render_template(
            "assistent_scanlijst_print.html",
            grouped_rows=grouped_rows,
            generated_at=datetime.datetime(2026, 8, 28, 12, 0),
        )

    for html in (inventory_html, inventory_print_html, scan_html, scan_print_html):
        assert "Min 3" in html
        assert "Aanv. 4" in html
        assert "Max" not in html
    assert "Standaard" in inventory_html
    assert "Standaard" in inventory_print_html


def test_nieuwe_ruimte_rejects_missing_kamertype(app_module, monkeypatch):
    """Regression test: a Ruimte without a Kamertype can't produce a valid
    Locatiekaart (BadgyAutomation requires roomType.name/color as non-null
    strings), and the DB column is now NOT NULL. Reject at creation time
    instead of letting a bad row reach the database.
    """
    vestiging = SimpleNamespace(vestiging_id=1, bedrijf_id=1, naam="Hoofdvestiging")
    added = []

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module,
        "get_scoped_item",
        lambda model, item_id, bedrijf_id: (
            vestiging if model is app_module.Vestiging else None
        ),
    )
    monkeypatch.setattr(
        app_module,
        "db",
        SimpleNamespace(
            session=SimpleNamespace(
                add=lambda item: added.append(item),
                commit=lambda: None,
            )
        ),
    )

    response = _csrf_client(app_module).post(
        "/beheer/infra",
        data={
            "_csrf_token": "test-csrf",
            "actie": "nieuwe_ruimte",
            "vestiging_id": "1",
            "naam": "Nieuwe Kamer",
        },
    )

    assert response.status_code == 302
    assert added == []


def test_update_ruimte_rejects_missing_kamertype(app_module, monkeypatch):
    ruimte = SimpleNamespace(
        ruimte_id=2,
        bedrijf_id=1,
        naam="Behandelkamer",
        nummer="113",
        ruimte_type_id=5,
    )

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(
        app_module,
        "get_scoped_item",
        lambda model, item_id, bedrijf_id: (
            ruimte if model is app_module.Ruimte else None
        ),
    )
    monkeypatch.setattr(
        app_module,
        "db",
        SimpleNamespace(session=SimpleNamespace(commit=lambda: None)),
    )

    response = _csrf_client(app_module).post(
        "/beheer/update/ruimte/2",
        data={"_csrf_token": "test-csrf", "naam": "Behandelkamer", "nummer": "113"},
    )

    assert response.status_code == 302
    assert ruimte.ruimte_type_id == 5


def test_ruimte_kopieer_preview_returns_counts_for_bron_ruimte(app_module, monkeypatch):
    """Ticket #19: preview the impact of copying a Ruimte's inrichting
    before the admin commits to it, so the copy is never a surprise.
    """
    bron_ruimte = SimpleNamespace(ruimte_id=7, bedrijf_id=1, naam="Behandelkamer")

    class FakeKastQuery:
        def filter_by(self, **kwargs):
            assert kwargs == {"ruimte_id": 7, "bedrijf_id": 1}
            return self

        def count(self):
            return 3

    class FakePositieQuery:
        def join(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def count(self):
            return 11

    # Kast and Voorraad_Positie are both None (unreflected automap classes)
    # in this test environment, so a bare `model is app_module.Kast` check
    # would match either lookup. Patch them to distinct sentinel classes —
    # the route builds join/filter expressions off class attributes like
    # Voorraad_Positie.kast_id == Kast.kast_id, which the fakes below never
    # evaluate for real, but do need to exist to avoid an AttributeError.
    class KastSentinel:
        kast_id = None
        ruimte_id = None

    class VoorraadPositieSentinel:
        kast_id = None
        bedrijf_id = None

    class FakeSession:
        def query(self, model):
            if model is KastSentinel:
                return FakeKastQuery()
            if model is VoorraadPositieSentinel:
                return FakePositieQuery()
            raise AssertionError(f"unexpected query for {model}")

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "Kast", KastSentinel)
    monkeypatch.setattr(app_module, "Voorraad_Positie", VoorraadPositieSentinel)
    monkeypatch.setattr(
        app_module,
        "get_scoped_item",
        lambda model, item_id, bedrijf_id: (
            bron_ruimte if model is app_module.Ruimte else None
        ),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).get("/api/ruimte-kopieer-preview/7")

    assert response.status_code == 200
    assert response.get_json() == {"opslaglocaties": 3, "voorraadposities": 11}


def test_ruimte_kopieer_preview_returns_zero_for_unknown_ruimte(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "get_scoped_item", lambda *args: None)
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=SimpleNamespace()))

    response = _csrf_client(app_module).get("/api/ruimte-kopieer-preview/999")

    assert response.status_code == 200
    assert response.get_json() == {"opslaglocaties": 0, "voorraadposities": 0}


def test_nieuwe_ruimte_does_not_copy_without_explicit_keuze(app_module, monkeypatch):
    """Ticket #19: an empty/missing 'inrichting_keuze' must never trigger a
    copy, even if a kopieer_van_ruimte_id happens to be present in the
    POST body (defence in depth beyond the client-side confirmation).

    Kast/Voorraad_Positie are deliberately left as the module's real (None)
    automap placeholders: if the fix regresses and a copy is attempted
    anyway, instantiating a None model raises TypeError and fails the test.
    """
    vestiging = SimpleNamespace(vestiging_id=1, bedrijf_id=1, naam="Hoofdvestiging")
    ruimte_type = SimpleNamespace(ruimte_type_id=5, bedrijf_id=1, naam="Behandelkamer")
    added = []

    class FakeRuimte:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.ruimte_id = 501

    def get_scoped_item(model, item_id, bedrijf_id):
        if model is app_module.Vestiging:
            return vestiging
        if model is app_module.Ruimte_Type:
            return ruimte_type
        if model is FakeRuimte:
            raise AssertionError(
                "get_scoped_item(Ruimte, ...) must not run without an "
                "explicit inrichting_keuze='kopieer'"
            )
        return None

    def query_that_must_not_run(model):
        raise AssertionError(
            "db.session.query must not look up a bron Ruimte's inrichting "
            "without an explicit inrichting_keuze='kopieer'"
        )

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "Ruimte", FakeRuimte)
    monkeypatch.setattr(app_module, "get_scoped_item", get_scoped_item)
    monkeypatch.setattr(
        app_module,
        "db",
        SimpleNamespace(
            session=SimpleNamespace(
                add=lambda item: added.append(item),
                flush=lambda: None,
                commit=lambda: None,
                query=query_that_must_not_run,
            )
        ),
    )

    response = _csrf_client(app_module).post(
        "/beheer/infra",
        data={
            "_csrf_token": "test-csrf",
            "actie": "nieuwe_ruimte",
            "vestiging_id": "1",
            "naam": "Nieuwe Kamer",
            "ruimte_type_id": "5",
            # inrichting_keuze intentionally omitted, even though a
            # confirmation flag and a source id are both present — the
            # explicit choice is its own, separate requirement.
            "kopieer_van_ruimte_id": "3",
            "kopieer_bevestigd": "1",
        },
    )

    assert response.status_code == 302
    assert len(added) == 1  # only the new Ruimte itself, no Kast/Voorraad_Positie copied


def test_nieuwe_ruimte_does_not_copy_without_bevestiging(app_module, monkeypatch):
    """Ticket #19: 'expliciete bevestiging door de beheerder' is a separate
    acceptance criterion from the 'kopieer'-choice itself. A form posted
    with inrichting_keuze='kopieer' but without the confirmation flag the
    browser only sets after window.confirm() returns true — e.g. a direct
    POST that bypasses the browser dialog entirely — must not copy either.
    """
    vestiging = SimpleNamespace(vestiging_id=1, bedrijf_id=1, naam="Hoofdvestiging")
    ruimte_type = SimpleNamespace(ruimte_type_id=5, bedrijf_id=1, naam="Behandelkamer")
    added = []

    class FakeRuimte:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.ruimte_id = 501

    def get_scoped_item(model, item_id, bedrijf_id):
        if model is app_module.Vestiging:
            return vestiging
        if model is app_module.Ruimte_Type:
            return ruimte_type
        if model is FakeRuimte:
            raise AssertionError(
                "get_scoped_item(Ruimte, ...) must not run without an "
                "explicit kopieer_bevestigd='1'"
            )
        return None

    def query_that_must_not_run(model):
        raise AssertionError(
            "db.session.query must not look up a bron Ruimte's inrichting "
            "without an explicit kopieer_bevestigd='1'"
        )

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "Ruimte", FakeRuimte)
    monkeypatch.setattr(app_module, "get_scoped_item", get_scoped_item)
    monkeypatch.setattr(
        app_module,
        "db",
        SimpleNamespace(
            session=SimpleNamespace(
                add=lambda item: added.append(item),
                flush=lambda: None,
                commit=lambda: None,
                query=query_that_must_not_run,
            )
        ),
    )

    response = _csrf_client(app_module).post(
        "/beheer/infra",
        data={
            "_csrf_token": "test-csrf",
            "actie": "nieuwe_ruimte",
            "vestiging_id": "1",
            "naam": "Nieuwe Kamer",
            "ruimte_type_id": "5",
            "inrichting_keuze": "kopieer",
            "kopieer_van_ruimte_id": "3",
            # kopieer_bevestigd intentionally omitted — as if the form was
            # posted without the confirm() dialog ever having run.
        },
    )

    assert response.status_code == 302
    assert len(added) == 1  # only the new Ruimte itself, no Kast/Voorraad_Positie copied


def test_nieuwe_ruimte_copies_inrichting_when_explicitly_chosen(app_module, monkeypatch):
    vestiging = SimpleNamespace(vestiging_id=1, bedrijf_id=1, naam="Hoofdvestiging")
    ruimte_type = SimpleNamespace(ruimte_type_id=5, bedrijf_id=1, naam="Behandelkamer")
    bron_ruimte = SimpleNamespace(ruimte_id=3, bedrijf_id=1, naam="Bronkamer")
    bron_kast = SimpleNamespace(kast_id=40, ruimte_id=3, bedrijf_id=1, naam="Kast A", type_opslag="GRIJP")
    bron_positie = SimpleNamespace(
        voorraad_positie_id=90,
        kast_id=40,
        bedrijf_id=1,
        lokaal_artikel_id=8,
        materiaaltype="KANBAN",
        locatie_foto_url=None,
    )
    added = []

    class FakeRuimte:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.ruimte_id = 501

    class FakeKast:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.kast_id = 601

    class FakeVoorraadPositie:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.voorraad_positie_id = 701

    class FakeQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter_by(self, **kwargs):
            return self

        def all(self):
            return self._rows

    class FakeSession:
        def query(self, model):
            if model is FakeKast:
                return FakeQuery([bron_kast])
            if model is FakeVoorraadPositie:
                return FakeQuery([bron_positie])
            raise AssertionError(f"unexpected query for {model}")

        def add(self, item):
            added.append(item)

        def flush(self):
            return None

        def commit(self):
            return None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "Ruimte", FakeRuimte)
    monkeypatch.setattr(app_module, "Kast", FakeKast)
    monkeypatch.setattr(app_module, "Voorraad_Positie", FakeVoorraadPositie)
    monkeypatch.setattr(
        app_module,
        "get_scoped_item",
        lambda model, item_id, bedrijf_id: (
            vestiging if model is app_module.Vestiging
            else ruimte_type if model is app_module.Ruimte_Type
            else bron_ruimte if model is FakeRuimte
            else SimpleNamespace(eigen_naam="Artikel") if model is app_module.Lokaal_Artikel
            else None
        ),
    )
    monkeypatch.setattr(
        app_module,
        "position_kanban_override_values",
        lambda position, article: (None, None),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).post(
        "/beheer/infra",
        data={
            "_csrf_token": "test-csrf",
            "actie": "nieuwe_ruimte",
            "vestiging_id": "1",
            "naam": "Nieuwe Kamer",
            "ruimte_type_id": "5",
            "inrichting_keuze": "kopieer",
            "kopieer_van_ruimte_id": "3",
            "kopieer_bevestigd": "1",
        },
    )

    assert response.status_code == 302
    # 1 nieuwe Ruimte + 1 gekopieerde Kast + 1 gekopieerde Voorraad_Positie
    assert len(added) == 3


def test_beheer_infra_renders_explicit_ruimte_inrichting_choice(app_module, monkeypatch):
    """Ticket #19: the naamloos dropdown is gone — a rendered page must
    offer an explicit, labelled choice instead of an implicit copy trigger.
    """
    vestiging = SimpleNamespace(vestiging_id=1, bedrijf_id=1, naam="Hoofdvestiging", adres=None)
    ruimte_type = SimpleNamespace(ruimte_type_id=5, bedrijf_id=1, naam="Behandelkamer", kleur_hex="#123456")
    andere_ruimte = SimpleNamespace(ruimte_id=9, nummer="1", naam="Spreekkamer")

    class FakeQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter_by(self, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def join(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return self._rows

    class FakeSession:
        def query(self, *models):
            model = models[0]
            if model is app_module.Vestiging:
                return FakeQuery([vestiging])
            if model is app_module.Ruimte_Type:
                return FakeQuery([ruimte_type])
            if model is app_module.Ruimte:
                return FakeQuery([andere_ruimte])
            return FakeQuery([])

    class RuimteSentinel:  # Ruimte.nummer/.naam are read for order_by()
        nummer = None
        naam = None

    class VestigingSentinel:  # Vestiging.bedrijf_id is read for the join filter
        bedrijf_id = None

    monkeypatch.setattr(app_module, "check_db", lambda: True)
    monkeypatch.setattr(app_module, "get_huidig_bedrijf_id", lambda: 1)
    monkeypatch.setattr(app_module, "Ruimte", RuimteSentinel)
    monkeypatch.setattr(app_module, "Vestiging", VestigingSentinel)
    monkeypatch.setattr(
        app_module,
        "get_scoped_item",
        lambda model, item_id, bedrijf_id: (
            vestiging if model is app_module.Vestiging else None
        ),
    )
    monkeypatch.setattr(app_module, "db", SimpleNamespace(session=FakeSession()))

    response = _csrf_client(app_module).get("/beheer/infra?vestiging_id=1")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Nieuwe lege Ruimte" in html
    assert "Inrichting kopiëren van" in html
    assert 'name="inrichting_keuze"' in html
    assert "- Lege -" not in html  # het oude, naamloze dropdown-label mag niet terugkomen
    assert 'name="kopieer_bevestigd"' in html
