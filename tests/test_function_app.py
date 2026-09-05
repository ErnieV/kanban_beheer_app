"""Ticket #36: eerste testdekking voor function_app.py's publieke scanfunctie.

Deze Azure Function werkt rechtstreeks met een SQLAlchemy-engine/text()
(geen Flask, geen ORM), dus de bestaande Flask-testclient-conventie past hier
niet. In plaats daarvan wordt _get_engine() vervangen door een gescripte
fake engine/connectie die precies de queryresultaten teruggeeft die één
scenario nodig heeft, in de volgorde waarin scan_card ze uitvoert.
"""
import datetime
from types import SimpleNamespace

import pytest


@pytest.fixture
def function_app_module(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-123456789012345678901234")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    import function_app

    return function_app


class FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class ScriptedConnection:
    """Geeft, in volgorde, de resultaten uit `script` terug voor elke
    execute()-aanroep — één per query, zodat een test simpelweg de
    verwachte queryvolgorde van scan_card kan naspelen zonder de SQL-tekst
    zelf te hoeven parsen.
    """

    def __init__(self, script):
        self._script = list(script)
        self.executed = []

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        row = self._script.pop(0)
        return FakeResult(row)


class FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def begin(self):
        return self

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        return False


def _request(public_token):
    return SimpleNamespace(route_params={"public_token": public_token})


def test_scan_unknown_token_returns_not_found(function_app_module, monkeypatch):
    """Bestaand gedrag, nu voor het eerst getest: een token dat op geen van
    beide plekken (Kanban-kaartje, Locatiekaartje) bestaat blijft de
    bestaande 404-melding geven.
    """
    conn = ScriptedConnection([None, None])
    monkeypatch.setattr(function_app_module, "_get_engine", lambda: FakeEngine(conn))

    response = function_app_module.scan_card(_request("onbekend-token"))

    assert response.status_code == 404
    assert "Kaart niet gevonden" in response.get_body().decode()


def test_scan_kanban_kaart_printed_adds_to_scanlijst(function_app_module, monkeypatch):
    """Bestaand gedrag, nu voor het eerst getest: een geldig, geprint
    Kanban-kaartje wordt op de scanlijst gezet, precies zoals vóór de
    Locatiekaartje-uitbreiding.
    """
    kaart_row = {
        "kaart_id": "kaart-1",
        "bedrijf_id": 1,
        "human_code": "KB-ABCD1234",
        "product_name": "Verband",
        "location_text": "1e lade linksonder (Grijpvoorraad)",
        "status": "PRINTED",
    }
    conn = ScriptedConnection([kaart_row, None, None])
    monkeypatch.setattr(function_app_module, "_get_engine", lambda: FakeEngine(conn))

    response = function_app_module.scan_card(_request("kaart-token"))
    body = response.get_body().decode()

    assert response.status_code == 200
    assert "Verband" in body
    assert "KB-ABCD1234" in body
    assert "toegevoegd aan de scanlijst" in body

    insert_stmt, insert_params = conn.executed[-1]
    assert "INSERT INTO Kanban_Scanlijst_Item" in insert_stmt
    assert insert_params["kaart_id"] == "kaart-1"
    assert insert_params["voorraad_positie_id"] is None


def test_scan_kanban_kaart_not_printed_is_rejected(function_app_module, monkeypatch):
    """Bestaand gedrag, nu voor het eerst getest: een Kanban-kaartje dat nog
    niet (of niet meer) PRINTED is, blijft de bestaande 409-melding geven.
    """
    kaart_row = {
        "kaart_id": "kaart-2",
        "bedrijf_id": 1,
        "human_code": "KB-EFGH5678",
        "product_name": "Verband",
        "location_text": "1e lade linksonder (Grijpvoorraad)",
        "status": "PENDING_PRINT",
    }
    conn = ScriptedConnection([kaart_row])
    monkeypatch.setattr(function_app_module, "_get_engine", lambda: FakeEngine(conn))

    response = function_app_module.scan_card(_request("kaart-token"))

    assert response.status_code == 409
    assert "Kaart niet actief" in response.get_body().decode()


def test_scan_locatiekaartje_token_for_kanban_materiaal_reports_op_vul_aan(
    function_app_module, monkeypatch
):
    """Ticket #36: een Locatiekaartje-token voor Kanban-materiaal meldt
    hetzelfde als een Kanban-kaartje-scan.
    """
    positie_row = {
        "voorraad_positie_id": 91,
        "bedrijf_id": 1,
        "materiaaltype": "KANBAN",
        "product_name": "Verband",
        "ruimte_naam": "113 Behandelkamer",
        "opslaglocatie_naam": "1e lade linksonder",
    }
    conn = ScriptedConnection([None, positie_row, None, None])
    monkeypatch.setattr(function_app_module, "_get_engine", lambda: FakeEngine(conn))

    response = function_app_module.scan_card(_request("locatie-token"))
    body = response.get_body().decode()

    assert response.status_code == 200
    assert "Verband" in body
    assert "113 Behandelkamer" in body
    assert "dit is op" in body.lower()

    insert_stmt, insert_params = conn.executed[-1]
    assert "INSERT INTO Kanban_Scanlijst_Item" in insert_stmt
    assert insert_params["voorraad_positie_id"] == 91
    assert insert_params["kaart_id"] is None


def test_scan_locatiekaartje_token_for_standaard_materiaal_reports_generic_message(
    function_app_module, monkeypatch
):
    """Ticket #36: Standaard materiaal heeft geen Min/Aanvulhoeveelheid, dus
    de bevestiging is generiek in plaats van een concreet aanvulaantal.
    """
    positie_row = {
        "voorraad_positie_id": 92,
        "bedrijf_id": 1,
        "materiaaltype": "STANDAARD",
        "product_name": "Naaldencontainer",
        "ruimte_naam": "113 Behandelkamer",
        "opslaglocatie_naam": "2e lade rechtsonder",
    }
    conn = ScriptedConnection([None, positie_row, None, None])
    monkeypatch.setattr(function_app_module, "_get_engine", lambda: FakeEngine(conn))

    response = function_app_module.scan_card(_request("locatie-token-2"))
    body = response.get_body().decode()

    assert response.status_code == 200
    assert "Naaldencontainer" in body
    assert "dit is gemeld" in body.lower()
    assert "vul aan" not in body.lower()


def test_scan_locatiekaartje_token_second_scan_increments_count_without_resetting(
    function_app_module, monkeypatch
):
    """Ticket #36: een tweede scan van hetzelfde, nog niet gereset
    Locatiekaartje-token telt op in plaats van een nieuwe regel te maken.
    """
    positie_row = {
        "voorraad_positie_id": 91,
        "bedrijf_id": 1,
        "materiaaltype": "KANBAN",
        "product_name": "Verband",
        "ruimte_naam": "113 Behandelkamer",
        "opslaglocatie_naam": "1e lade linksonder",
    }
    existing_scanlijst_row = {"scanlijst_item_id": 5, "scan_count": 1}
    conn = ScriptedConnection([None, positie_row, existing_scanlijst_row, None])
    monkeypatch.setattr(function_app_module, "_get_engine", lambda: FakeEngine(conn))

    response = function_app_module.scan_card(_request("locatie-token"))
    body = response.get_body().decode()

    assert response.status_code == 200
    assert "Aantal scans sinds laatste reset: 2" in body
    assert "al gemeld" in body.lower()
