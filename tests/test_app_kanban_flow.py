import datetime
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
    assert position.trigger_min == 1
    assert position.target_max == 2


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


def test_existing_legacy_position_inherits_current_article_standard_after_cutover(
    app_module,
):
    position = SimpleNamespace(
        materiaaltype=None,
        kanban_min_override=None,
        kanban_refill_quantity_override=None,
        trigger_min=2,
        target_max=5,
    )
    article = SimpleNamespace(kanban_min=1, kanban_refill_quantity=1)

    assert app_module.effective_position_kanban_settings(
        position,
        article,
    ) == KanbanStandard(2, 3)


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
    assert position.trigger_min is None
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
    assert data[0]["erft_standaard"] is True
    assert data[1]["min"] == 5
    assert data[1]["aanv"] == 4
    assert data[1]["erft_standaard"] is False
    assert data[2]["materiaaltype"] == "STANDAARD"
    assert data[2]["min"] is None
    assert data[2]["aanv"] is None
    assert all("max" not in item for item in data)


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

    queue_item = SimpleNamespace(
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
    )

    monkeypatch.setattr(app_module, "PRINT_SERVICE_URL", "http://print.test")
    monkeypatch.setattr(app_module, "PRINT_SERVICE_REQUIRE_API_KEY", False)
    monkeypatch.setattr(app_module.requests, "post", fake_post)

    sent, error = app_module.send_queue_item_to_print_service(queue_item)

    assert sent is True
    assert error is None
    logistics = captured["payload"]["data"]["logistics"]
    assert logistics == {"location": "Kast A", "minLevel": 3, "refillQuantity": 4}
    assert "maxLevel" not in str(captured["payload"])


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
