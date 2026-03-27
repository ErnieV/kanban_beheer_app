import datetime
import os
import urllib.parse

import azure.functions as func
from sqlalchemy import create_engine, text


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


@app.route(route="scan/{public_token}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def scan_card(req: func.HttpRequest) -> func.HttpResponse:
    public_token = req.route_params.get("public_token")
    if not public_token:
        return _html_page("Ongeldige scan", '<div class="card"><h1>Ongeldige scan</h1><p>De QR-code bevat geen geldig token.</p></div>', 400)

    try:
        engine = _get_engine()
        now = datetime.datetime.utcnow()
        with engine.begin() as conn:
            card = conn.execute(text("""
                SELECT kaart_id, bedrijf_id, human_code, product_name, location_text, status
                FROM Kanban_Kaart
                WHERE public_token = :public_token
            """), {"public_token": public_token}).mappings().first()

            if not card:
                return _html_page(
                    "Kaart niet gevonden",
                    '<div class="card"><h1>Kaart niet gevonden</h1><p>Deze QR-code is onbekend.</p></div>',
                    404
                )

            if card["status"] != "PRINTED":
                return _html_page(
                    "Kaart niet actief",
                    '<div class="card"><h1>Kaart niet actief</h1><p>Dit kaartje is nog niet geprint of is geannuleerd.</p></div>',
                    409
                )

            existing = conn.execute(text("""
                SELECT TOP 1 scanlijst_item_id, scan_count
                FROM Kanban_Scanlijst_Item
                WHERE kaart_id = :kaart_id AND reset_at IS NULL
                ORDER BY last_scanned_at DESC
            """), {"kaart_id": card["kaart_id"]}).mappings().first()

            if existing:
                conn.execute(text("""
                    UPDATE Kanban_Scanlijst_Item
                    SET scan_count = scan_count + 1,
                        last_scanned_at = :now
                    WHERE scanlijst_item_id = :scanlijst_item_id
                """), {"now": now, "scanlijst_item_id": existing["scanlijst_item_id"]})
                message = "Dit kaartje stond al op de scanlijst en is opnieuw bevestigd."
                count = int(existing["scan_count"]) + 1
            else:
                conn.execute(text("""
                    INSERT INTO Kanban_Scanlijst_Item (
                        kaart_id, bedrijf_id, first_scanned_at, last_scanned_at, scan_count, reset_at, reset_by
                    )
                    VALUES (
                        :kaart_id, :bedrijf_id, :now, :now, 1, NULL, NULL
                    )
                """), {"kaart_id": card["kaart_id"], "bedrijf_id": card["bedrijf_id"], "now": now})
                message = "Dit kaartje is toegevoegd aan de scanlijst."
                count = 1

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
    except Exception as exc:
        return _html_page(
            "Scan mislukt",
            f'<div class="card error"><h1>Scan mislukt</h1><p>Er ging iets mis bij het registreren van deze scan.</p><p class="muted">{exc}</p></div>',
            500
        )
