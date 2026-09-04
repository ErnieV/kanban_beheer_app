"""Ticket #15: the one deliberately browser-tested scenario (per /to-spec) —
inline opslaan van Min op de Ruimte-pagina zonder paginanavigatie.

Unlike the rest of this suite, app.py's automap runs against a real
reflected SQLite schema here instead of the shared in-memory ":memory:" DB
the other test files import app.py against — module-level code (the
automap Base.prepare(...) call) only runs once per test process, so a
second `import app` elsewhere in this same process would just reuse the
first import's (empty) schema. To get a real Ruimte/Kast/Voorraad_Positie
to click through, this spins up the Flask app in a *subprocess* against a
freshly seeded, file-based SQLite DB, and drives it with a real browser.
"""

import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

REPO_ROOT = Path(__file__).resolve().parent.parent

SUBPROCESS_ENTRYPOINT = """
import os
os.environ["SECRET_KEY"] = {secret_key!r}
os.environ["DATABASE_URL"] = {database_url!r}
# Zonder dit stuurt de browser de sessiecookie (en dus het CSRF-token) niet
# terug over plain http://127.0.0.1 — app.py zet SESSION_COOKIE_SECURE
# anders standaard aan tenzij FLASK_DEBUG=1 is gezet.
os.environ["SESSION_COOKIE_SECURE"] = "0"
import app as app_module
app_module.app.run(host="127.0.0.1", port={port}, debug=False, use_reloader=False)
"""

SCHEMA_AND_SEED_SQL = """
CREATE TABLE Bedrijf (
    bedrijf_id INTEGER PRIMARY KEY,
    naam TEXT,
    logo_url TEXT
);
CREATE TABLE Vestiging (
    vestiging_id INTEGER PRIMARY KEY,
    bedrijf_id INTEGER,
    naam TEXT
);
CREATE TABLE Ruimte_Type (
    ruimte_type_id INTEGER PRIMARY KEY,
    bedrijf_id INTEGER,
    naam TEXT,
    kleur_hex TEXT
);
CREATE TABLE Ruimte (
    ruimte_id INTEGER PRIMARY KEY,
    bedrijf_id INTEGER,
    vestiging_id INTEGER,
    ruimte_type_id INTEGER,
    naam TEXT,
    nummer TEXT
);
CREATE TABLE Kast (
    kast_id INTEGER PRIMARY KEY,
    bedrijf_id INTEGER,
    ruimte_id INTEGER,
    naam TEXT,
    type_opslag TEXT
);
CREATE TABLE Global_Catalogus (
    global_id INTEGER PRIMARY KEY,
    generieke_naam TEXT,
    foto_url TEXT
);
CREATE TABLE Lokaal_Artikel (
    lokaal_artikel_id INTEGER PRIMARY KEY,
    bedrijf_id INTEGER,
    global_id INTEGER,
    eigen_naam TEXT,
    foto_url TEXT,
    verpakkingseenheid_tekst TEXT,
    kanban_min INTEGER,
    kanban_refill_quantity INTEGER
);
CREATE TABLE Voorraad_Positie (
    voorraad_positie_id INTEGER PRIMARY KEY,
    bedrijf_id INTEGER,
    kast_id INTEGER,
    lokaal_artikel_id INTEGER,
    materiaaltype TEXT,
    strategie TEXT,
    kanban_min_override INTEGER,
    kanban_refill_quantity_override INTEGER,
    locatie_foto_url TEXT,
    qr_code TEXT
);

INSERT INTO Bedrijf (bedrijf_id, naam, logo_url)
    VALUES (1, 'Vivaldi Kliniek', NULL);
INSERT INTO Vestiging (vestiging_id, bedrijf_id, naam)
    VALUES (1, 1, 'Vestiging Noord');
INSERT INTO Ruimte_Type (ruimte_type_id, bedrijf_id, naam, kleur_hex)
    VALUES (1, 1, 'Behandeling', '#123456');
INSERT INTO Ruimte (ruimte_id, bedrijf_id, vestiging_id, ruimte_type_id, naam, nummer)
    VALUES (1, 1, 1, 1, 'Behandelkamer', '3.14');
INSERT INTO Kast (kast_id, bedrijf_id, ruimte_id, naam, type_opslag)
    VALUES (1, 1, 1, 'Verbandkast', 'GRIJP');
INSERT INTO Lokaal_Artikel (
    lokaal_artikel_id, bedrijf_id, global_id, eigen_naam, foto_url,
    verpakkingseenheid_tekst, kanban_min, kanban_refill_quantity
) VALUES (1, 1, NULL, 'Verband', NULL, 'doos', 3, 4);
INSERT INTO Voorraad_Positie (
    voorraad_positie_id, bedrijf_id, kast_id, lokaal_artikel_id,
    materiaaltype, strategie, kanban_min_override,
    kanban_refill_quantity_override, locatie_foto_url, qr_code
) VALUES (1, 1, 1, 1, 'KANBAN', 'TWO_BIN', NULL, NULL, NULL, 'qr-1');
"""


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _seed_database(db_path):
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(SCHEMA_AND_SEED_SQL)
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def live_app_server(tmp_path):
    db_path = tmp_path / "browser_test.db"
    _seed_database(str(db_path))
    port = _free_port()

    script = SUBPROCESS_ENTRYPOINT.format(
        secret_key="test-secret-key-123456789012345678901234",
        database_url=f"sqlite:///{db_path}",
        port=port,
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    ready = False
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except OSError:
            time.sleep(0.2)

    if not ready:
        process.terminate()
        output = process.stdout.read().decode("utf-8", "replace") if process.stdout else ""
        process.wait(timeout=5)
        raise RuntimeError(f"Live test server did not start in time.\n{output}")

    try:
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def test_inline_min_edit_saves_without_navigation_and_persists_after_reload(
    live_app_server, page
):
    page.goto(f"{live_app_server}/assistent/kamer/1")

    row = page.locator('tr[data-position-id="1"]')
    expect(row).to_be_visible()

    min_input = row.locator('[data-field="kanban_min_override"]')
    expect(min_input).to_have_value("3")

    url_before_edit = page.url
    min_input.fill("7")
    min_input.blur()

    status = row.locator('[data-status-for="kanban_min_override"]')
    expect(status).to_have_text("Opgeslagen")

    # Kernvereiste van ticket #15: geen paginanavigatie bij het opslaan.
    assert page.url == url_before_edit

    # De wijziging blijft staan na een verse laad van de pagina.
    page.reload()
    row_after_reload = page.locator('tr[data-position-id="1"]')
    min_input_after_reload = row_after_reload.locator(
        '[data-field="kanban_min_override"]'
    )
    expect(min_input_after_reload).to_have_value("7")
