import datetime
import os
import urllib.parse

import azure.functions as func
from sqlalchemy import create_engine, text

from kanban_domain import Materiaaltype, normalize_material_type


app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
ENGINE = None


def _get_engine():
    global ENGINE
    if ENGINE is not None:
        return ENGINE

    db_server = os.environ.get('DB_SERVER')
    db_name = os.environ.get('DB_NAME')
    db_user = os.environ.get('DB_USER')
    db_pass = os.environ.get('DB_PASS')
    if not all([db_server, db_name, db_user, db_pass]):
        raise RuntimeError("Database configuratie ontbreekt.")

    encoded_user = urllib.parse.quote_plus(db_user)
    encoded_pass = urllib.parse.quote_plus(db_pass)
    driver = 'ODBC+Driver+18+for+SQL+Server'
    connection_string = (
        f"mssql+pyodbc://{encoded_user}:{encoded_pass}@{db_server}/{db_name}"
        f"?driver={driver}&TrustServerCertificate=yes"
    )
    ENGINE = create_engine(connection_string, future=True)
    return ENGINE


def _html_page(title, body, status_code=200):
    html = f"""<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #f5f7fb; color: #111827; margin: 0; }}
    main {{ max-width: 520px; margin: 0 auto; padding: 24px 18px 48px; }}
    .card {{ background: white; border-radius: 16px; padding: 24px; box-shadow: 0 10px 30px rgba(0,0,0,0.08); }}
    h1 {{ margin-top: 0; font-size: 1.6rem; }}
    .muted {{ color: #6b7280; }}
    .badge {{ display: inline-block; padding: 6px 10px; border-radius: 999px; background: #dcfce7; color: #166534; font-weight: 700; }}
    .error {{ background: #fee2e2; color: #991b1b; }}
  </style>
</head>
<body>
  <main>{body}</main>
</body>
</html>"""
    return func.HttpResponse(html, status_code=status_code, mimetype="text/html")


def _lookup_kanban_kaart(conn, public_token):
    return conn.execute(text("""
        SELECT kaart_id, bedrijf_id, human_code, product_name, location_text, status
        FROM Kanban_Kaart
        WHERE public_token = :public_token
    """), {"public_token": public_token}).mappings().first()


def _lookup_locatie_positie(conn, public_token):
    """Ticket #36: tweede lookup-pad, voor een scan die van een
    Locatiekaartje komt in plaats van van een Kanban-kaartje. Alleen
    geprobeerd als _lookup_kanban_kaart niets oplevert.
    """
    return conn.execute(text("""
        SELECT vp.voorraad_positie_id, vp.bedrijf_id, vp.materiaaltype,
               COALESCE(la.eigen_naam, gc.generieke_naam, '') AS product_name,
               r.naam AS ruimte_naam, k.naam AS opslaglocatie_naam
        FROM Voorraad_Positie vp
        JOIN Lokaal_Artikel la ON vp.lokaal_artikel_id = la.lokaal_artikel_id
        LEFT JOIN Global_Catalogus gc ON la.global_id = gc.global_id
        JOIN Kast k ON vp.kast_id = k.kast_id
        JOIN Ruimte r ON k.ruimte_id = r.ruimte_id
        WHERE vp.locatie_scan_token = :public_token
    """), {"public_token": public_token}).mappings().first()


def _record_scan(conn, now, kaart_id=None, voorraad_positie_id=None, bedrijf_id=None):
    """Log één scan tegen óf een Kanban-kaartje óf een Voorraadpositie —
    precies één van beide is gevuld. Geeft (aantal_scans, was_al_gemeld)
    terug.
    """
    if kaart_id is not None:
        existing = conn.execute(text("""
            SELECT TOP 1 scanlijst_item_id, scan_count
            FROM Kanban_Scanlijst_Item
            WHERE kaart_id = :kaart_id AND reset_at IS NULL
            ORDER BY last_scanned_at DESC
        """), {"kaart_id": kaart_id}).mappings().first()
    else:
        existing = conn.execute(text("""
            SELECT TOP 1 scanlijst_item_id, scan_count
            FROM Kanban_Scanlijst_Item
            WHERE voorraad_positie_id = :voorraad_positie_id AND reset_at IS NULL
            ORDER BY last_scanned_at DESC
        """), {"voorraad_positie_id": voorraad_positie_id}).mappings().first()

    if existing:
        conn.execute(text("""
            UPDATE Kanban_Scanlijst_Item
            SET scan_count = scan_count + 1,
                last_scanned_at = :now
            WHERE scanlijst_item_id = :scanlijst_item_id
        """), {"now": now, "scanlijst_item_id": existing["scanlijst_item_id"]})
        return int(existing["scan_count"]) + 1, True

    conn.execute(text("""
        INSERT INTO Kanban_Scanlijst_Item (
            kaart_id, voorraad_positie_id, bedrijf_id,
            first_scanned_at, last_scanned_at, scan_count, reset_at, reset_by
        )
        VALUES (
            :kaart_id, :voorraad_positie_id, :bedrijf_id, :now, :now, 1, NULL, NULL
        )
    """), {
        "kaart_id": kaart_id,
        "voorraad_positie_id": voorraad_positie_id,
        "bedrijf_id": bedrijf_id,
        "now": now,
    })
    return 1, False


@app.route(route="scan/{public_token}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def scan_card(req: func.HttpRequest) -> func.HttpResponse:
    public_token = req.route_params.get("public_token")
    if not public_token:
        return _html_page("Ongeldige scan", '<div class="card"><h1>Ongeldige scan</h1><p>De QR-code bevat geen geldig token.</p></div>', 400)

    try:
        engine = _get_engine()
        now = datetime.datetime.utcnow()
        with engine.begin() as conn:
            card = _lookup_kanban_kaart(conn, public_token)

            if card:
                if card["status"] != "PRINTED":
                    return _html_page(
                        "Kaart niet actief",
                        '<div class="card"><h1>Kaart niet actief</h1><p>Dit kaartje is nog niet geprint of is geannuleerd.</p></div>',
                        409
                    )

                count, was_existing = _record_scan(
                    conn, now, kaart_id=card["kaart_id"], bedrijf_id=card["bedrijf_id"],
                )
                message = (
                    "Dit kaartje stond al op de scanlijst en is opnieuw bevestigd."
                    if was_existing else
                    "Dit kaartje is toegevoegd aan de scanlijst."
                )
                body = f"""
                <div class="card">
                  <span class="badge">Scan verwerkt</span>
                  <h1>{card["product_name"]}</h1>
                  <p class="muted">{card["location_text"]}</p>
                  <p><strong>Kaartcode:</strong> {card["human_code"]}</p>
                  <p>{message}</p>
                  <p class="muted">Aantal scans sinds laatste reset: {count}</p>
                </div>
                """
                return _html_page("Scan verwerkt", body, 200)

            # Ticket #36: geen Kanban-kaartje met dit token — probeer het
            # stabiele Locatiekaartje-token op de Voorraadpositie zelf.
            positie = _lookup_locatie_positie(conn, public_token)
            if not positie:
                return _html_page(
                    "Kaart niet gevonden",
                    '<div class="card"><h1>Kaart niet gevonden</h1><p>Deze QR-code is onbekend.</p></div>',
                    404
                )

            count, was_existing = _record_scan(
                conn,
                now,
                voorraad_positie_id=positie["voorraad_positie_id"],
                bedrijf_id=positie["bedrijf_id"],
            )
            is_kanban_materiaal = (
                normalize_material_type(positie["materiaaltype"]) is Materiaaltype.KANBAN
            )
            if is_kanban_materiaal:
                message = (
                    "Dit was al gemeld — nogmaals bevestigd dat dit op is."
                    if was_existing else
                    "Dit is op — vul aan."
                )
            else:
                message = (
                    "Dit was al gemeld — nogmaals bevestigd."
                    if was_existing else
                    "Dit is gemeld — controleer de voorraad."
                )

            body = f"""
            <div class="card">
              <span class="badge">Scan verwerkt</span>
              <h1>{positie["product_name"]}</h1>
              <p class="muted">{positie["ruimte_naam"]} — {positie["opslaglocatie_naam"]}</p>
              <p>{message}</p>
              <p class="muted">Aantal scans sinds laatste reset: {count}</p>
            </div>
            """
            return _html_page("Scan verwerkt", body, 200)
    except Exception as exc:
        return _html_page(
            "Scan mislukt",
            f'<div class="card error"><h1>Scan mislukt</h1><p>Er ging iets mis bij het registreren van deze scan.</p><p class="muted">{exc}</p></div>',
            500
        )
