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
    )
    kast = SimpleNamespace(kast_id=4, bedrijf_id=1, ruimte_id=9)
    article = SimpleNamespace(
        lokaal_artikel_id=7,
        bedrijf_id=1,
        kanban_min=1,
        kanban_refill_quantity=1,
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
            "materiaaltype": "KANBAN",
            "kanban_min_override": "5",
            "kanban_refill_quantity_override": "2",
        },
    )

    assert response.status_code == 302
    assert position.kanban_min_override == 5
    assert position.kanban_refill_quantity_override == 2


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
    assert created[0].trigger_min == 3


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


def test_existing_legacy_position_keeps_its_effective_values_during_expand(
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

    assert {"kanban_min", "kanban_refill_quantity"} <= article_columns
    assert {
        "materiaaltype",
        "kanban_min_override",
        "kanban_refill_quantity_override",
    } <= position_columns


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
