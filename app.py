import os
import uuid
import urllib.parse
import socket
import hmac
import secrets
import json
import datetime
import base64
import mimetypes
import threading
import time
from zoneinfo import ZoneInfo
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, or_, text, inspect
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from azure.storage.blob import BlobServiceClient

# Laad variabelen
load_dotenv()

app = Flask(__name__)
secret_key = os.environ.get('SECRET_KEY')
if not secret_key:
    raise RuntimeError("SECRET_KEY ontbreekt. Stel SECRET_KEY in via environment variabele.")
if len(secret_key) < 32:
    raise RuntimeError("SECRET_KEY moet minimaal 32 tekens lang zijn.")
app.secret_key = secret_key
debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '0' if debug_mode else '1') == '1'

# --- CONFIGURATIE ---
db_server = os.environ.get('DB_SERVER')
db_name = os.environ.get('DB_NAME')
db_user = os.environ.get('DB_USER')
db_pass = os.environ.get('DB_PASS')
connect_str = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
container_name = os.environ.get('AZURE_CONTAINER_NAME')

API_BASE_URL = os.environ.get('KANBAN_API_BASE_URL', 'https://api.uw-zorginstelling.nl/scan')
default_scan_base_url = API_BASE_URL.rstrip('/')
if default_scan_base_url.endswith('/scan'):
    default_scan_base_url = default_scan_base_url[:-5]
PRINT_SERVICE_URL = os.environ.get('PRINT_SERVICE_URL')
PRINT_SERVICE_API_KEY = os.environ.get('PRINT_SERVICE_API_KEY')
PRINT_SERVICE_REQUIRE_API_KEY = os.environ.get('PRINT_SERVICE_REQUIRE_API_KEY', '1') == '1'
PRINT_CONNECT_TIMEOUT = float(os.environ.get('PRINT_CONNECT_TIMEOUT', '3'))
PRINT_REQUEST_TIMEOUT = float(os.environ.get('PRINT_REQUEST_TIMEOUT', '10'))
KANBAN_SCAN_BASE_URL = os.environ.get('KANBAN_SCAN_BASE_URL', default_scan_base_url)
APP_VERSION = os.environ.get('APP_VERSION', 'dev')
APP_BUILD_DATETIME = os.environ.get(
    'APP_BUILD_DATETIME',
    datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
)
APP_TIMEZONE = os.environ.get('APP_TIMEZONE', 'Europe/Amsterdam')
DEFAULT_LAYOUT_REFRESH_SECONDS = 300

if not all([db_server, db_name, db_user, db_pass]):
    print("WAARSCHUWING: Database configuratie ontbreekt!")

encoded_user = urllib.parse.quote_plus(db_user) if db_user else ''
encoded_pass = urllib.parse.quote_plus(db_pass) if db_pass else ''

driver = 'ODBC+Driver+18+for+SQL+Server'
app.config['SQLALCHEMY_DATABASE_URI'] = f"mssql+pyodbc://{encoded_user}:{encoded_pass}@{db_server}/{db_name}?driver={driver}&TrustServerCertificate=yes"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class KanbanKaart(db.Model):
    __tablename__ = 'Kanban_Kaart'

    kaart_id = db.Column(db.String(36), primary_key=True)
    bedrijf_id = db.Column(db.Integer, nullable=False, index=True)
    voorraad_positie_id = db.Column(db.Integer, nullable=False, index=True)
    public_token = db.Column(db.String(128), nullable=False, unique=True, index=True)
    human_code = db.Column(db.String(64), nullable=False, unique=True, index=True)
    product_name = db.Column(db.String(255), nullable=False)
    location_text = db.Column(db.String(255), nullable=False)
    product_sku = db.Column(db.String(64), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='PENDING_PRINT')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)
    printed_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)


class KanbanScanlijstItem(db.Model):
    __tablename__ = 'Kanban_Scanlijst_Item'

    scanlijst_item_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    kaart_id = db.Column(db.String(36), nullable=False, index=True)
    bedrijf_id = db.Column(db.Integer, nullable=False, index=True)
    first_scanned_at = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)
    last_scanned_at = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)
    scan_count = db.Column(db.Integer, nullable=False, default=1)
    reset_at = db.Column(db.DateTime, nullable=True)
    reset_by = db.Column(db.String(255), nullable=True)


def ensure_scan_schema():
    inspector = inspect(db.engine)
    db.create_all()

    if inspector.has_table('Print_Queue'):
        existing_columns = {col['name'] for col in inspector.get_columns('Print_Queue')}
        if 'kaart_id' not in existing_columns:
            db.session.execute(text("ALTER TABLE Print_Queue ADD kaart_id NVARCHAR(36) NULL"))
            db.session.commit()

# --- AUTOMAP & MODELS ---
Base = automap_base()
db_operational = False

Global_Catalogus = None
Lokaal_Artikel = None
Voorraad_Positie = None
Bedrijf = None
Vestiging = None
Ruimte = None
Ruimte_Type = None
Kast = None
Print_Queue = None
Leverancier = None
PREVIEW_LAYOUT_CACHE = None
PREVIEW_LAYOUT_LOCK = threading.Lock()

with app.app_context():
    try:
        ensure_scan_schema()
        Base.prepare(db.engine, reflect=True)
        Global_Catalogus = getattr(Base.classes, 'Global_Catalogus', None)
        Lokaal_Artikel = getattr(Base.classes, 'Lokaal_Artikel', None)
        Voorraad_Positie = getattr(Base.classes, 'Voorraad_Positie', None)
        Bedrijf = getattr(Base.classes, 'Bedrijf', None)
        Vestiging = getattr(Base.classes, 'Vestiging', None)
        Ruimte = getattr(Base.classes, 'Ruimte', None)
        Ruimte_Type = getattr(Base.classes, 'Ruimte_Type', None)
        Kast = getattr(Base.classes, 'Kast', None)
        Print_Queue = getattr(Base.classes, 'Print_Queue', None)
        Leverancier = getattr(Base.classes, 'Leverancier', None)

        if Global_Catalogus and Bedrijf:
            db_operational = True
            print("Database succesvol verbonden.")
    except Exception as e:
        print(f"CRITIQUE DB ERROR: {e}")

# --- CONTEXT PROCESSOR ---

@app.context_processor
def inject_context():
    """Zorgt dat bedrijfsdata beschikbaar is in ALLE templates (voor menu)."""
    if not db_operational or not Bedrijf:
        return dict(
            huidig_bedrijf=None,
            alle_bedrijven=[],
            open_scan_count=0,
            app_version=APP_VERSION,
            app_build_datetime=format_build_datetime(APP_BUILD_DATETIME)
        )
    
    # Huidig bedrijf ophalen
    bedrijf_id = get_huidig_bedrijf_id()
    bedrijf = db.session.query(Bedrijf).filter(Bedrijf.bedrijf_id == bedrijf_id).first() if bedrijf_id else None
    
    # NIEUW: Alle bedrijven ophalen voor de selector in de navbar
    alle_bedrijven = db.session.query(Bedrijf).order_by(Bedrijf.naam).all()
    open_scan_count = 0
    if bedrijf_id:
        try:
            open_scan_count = db.session.query(KanbanScanlijstItem).filter(
                KanbanScanlijstItem.bedrijf_id == bedrijf_id,
                KanbanScanlijstItem.reset_at.is_(None)
            ).count()
        except Exception:
            open_scan_count = 0
    
    return dict(
        huidig_bedrijf=bedrijf,
        alle_bedrijven=alle_bedrijven,
        open_scan_count=open_scan_count,
        app_version=APP_VERSION,
        app_build_datetime=format_build_datetime(APP_BUILD_DATETIME)
    )

def get_huidig_bedrijf_id():
    bedrijf_id = session.get('bedrijf_id')
    if not db_operational or not Bedrijf:
        return bedrijf_id

    if bedrijf_id:
        bedrijf = db.session.query(Bedrijf).filter(Bedrijf.bedrijf_id == bedrijf_id).first()
        if bedrijf:
            return bedrijf_id

    eerste = db.session.query(Bedrijf).order_by(Bedrijf.bedrijf_id).first()
    if eerste:
        session['bedrijf_id'] = eerste.bedrijf_id
        return eerste.bedrijf_id
    return None

def check_db():
    if not db_operational:
        flash("Geen verbinding met de database.", 'danger')
        return False
    return True

def _pk_name(model):
    return next(iter(model.__table__.primary_key.columns)).name

def get_scoped_item(model, item_id, bedrijf_id):
    query = db.session.query(model).filter(getattr(model, _pk_name(model)) == item_id)
    if hasattr(model, 'bedrijf_id'):
        query = query.filter(model.bedrijf_id == bedrijf_id)
    return query.first()

def generate_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token

app.jinja_env.globals['csrf_token'] = generate_csrf_token

@app.before_request
def csrf_protect():
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        expected = session.get('_csrf_token')
        submitted = request.form.get('_csrf_token') or request.headers.get('X-CSRFToken')
        if not expected or not submitted or not hmac.compare_digest(expected, submitted):
            abort(400, description="CSRF token ontbreekt of is ongeldig.")

def upload_image_to_azure(file):
    if not file or file.filename == '': return None
    if not file.filename.lower().endswith(('.png', '.jpg', '.jpeg')): return "ERROR_TYPE"
    try:
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}-{filename}"
        if not connect_str: return "ERROR_CONFIG"
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=unique_filename)
        blob_client.upload_blob(file, overwrite=True)
        return blob_client.url
    except Exception as e:
        print(f"Upload error: {e}")
        return "ERROR_UPLOAD"

# --- HELPERS ---

def utcnow():
    return datetime.datetime.utcnow()


def format_local_dt(value, fmt='%d-%m-%Y %H:%M'):
    if not value:
        return '-'
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(ZoneInfo(APP_TIMEZONE)).strftime(fmt)


def format_build_datetime(value):
    if not value:
        return '-'
    if isinstance(value, datetime.datetime):
        return format_local_dt(value, '%d-%m-%Y %H:%M')

    text_value = str(value).strip()
    parse_candidates = [
        ('%Y-%m-%d %H:%M UTC', datetime.timezone.utc),
        ('%Y-%m-%d %H:%M:%S UTC', datetime.timezone.utc),
        ('%Y-%m-%d %H:%M', datetime.timezone.utc),
        ('%Y-%m-%d %H:%M:%S', datetime.timezone.utc),
    ]

    for fmt, tzinfo in parse_candidates:
        try:
            parsed = datetime.datetime.strptime(text_value, fmt).replace(tzinfo=tzinfo)
            return format_local_dt(parsed, '%d-%m-%Y %H:%M')
        except ValueError:
            continue

    try:
        parsed = datetime.datetime.fromisoformat(text_value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return format_local_dt(parsed, '%d-%m-%Y %H:%M')
    except ValueError:
        return text_value


app.jinja_env.filters['localdt'] = format_local_dt


def _generate_human_code():
    return f"KB-{secrets.token_hex(4).upper()}"


def _generate_public_scan_url(public_token):
    base_url = (KANBAN_SCAN_BASE_URL or '').rstrip('/')
    if not base_url:
        return ''
    return f"{base_url}/scan/{public_token}"


def _get_reset_actor():
    return (
        request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME')
        or request.headers.get('X-MS-CLIENT-PRINCIPAL')
        or request.headers.get('X-Forwarded-User')
        or 'webapp-user'
    )


def _create_kanban_card(pos, art, kast, ruimte, bedrijf):
    human_code = _generate_human_code()
    while db.session.query(KanbanKaart).filter(KanbanKaart.human_code == human_code).first():
        human_code = _generate_human_code()

    card = KanbanKaart(
        kaart_id=str(uuid.uuid4()),
        bedrijf_id=bedrijf.bedrijf_id,
        voorraad_positie_id=pos.voorraad_positie_id,
        public_token=secrets.token_urlsafe(32),
        human_code=human_code,
        product_name=art.eigen_naam,
        location_text=f"{kast.naam} ({kast.type_opslag})",
        product_sku=str(art.lokaal_artikel_id),
        status='PENDING_PRINT',
        created_at=utcnow()
    )
    db.session.add(card)
    db.session.flush()
    return card

def create_queue_item(pos, art, global_item, kast, ruimte, r_type, bedrijf):
    header_text = ruimte.naam.upper()
    if ruimte.nummer: header_text = f"{ruimte.nummer} {header_text}"
    card = _create_kanban_card(pos, art, kast, ruimte, bedrijf)

    queue_kwargs = dict(
        bedrijf_id=bedrijf.bedrijf_id,
        status='PENDING',
        printer_id="reception-badgy-01",
        card_type="KANBAN_TWO_BIN",
        header_text=header_text,
        header_color=r_type.kleur_hex if r_type else "#3B82F6",
        product_name=art.eigen_naam,
        product_packaging=art.verpakkingseenheid_tekst or "Stuk",
        product_sku=str(art.lokaal_artikel_id),
        product_image_url=pos.locatie_foto_url or art.foto_url or (global_item.foto_url if global_item else None),
        location_text=f"{kast.naam} ({kast.type_opslag})",
        min_level=pos.trigger_min,
        max_level=pos.target_max,
        qr_code_value=_generate_public_scan_url(card.public_token),
        qr_human_readable=card.human_code,
        company_logo_url=bedrijf.logo_url
    )
    if hasattr(Print_Queue, 'kaart_id'):
        queue_kwargs['kaart_id'] = card.kaart_id

    return Print_Queue(**queue_kwargs)


def _get_queue_card(queue_item):
    kaart_id = getattr(queue_item, 'kaart_id', None)
    if not kaart_id:
        return None
    return db.session.query(KanbanKaart).filter(KanbanKaart.kaart_id == kaart_id).first()


def _mark_card_printed(queue_item):
    card = _get_queue_card(queue_item)
    if not card:
        return
    card.status = 'PRINTED'
    card.printed_at = utcnow()
    card.cancelled_at = None


def _mark_card_cancelled(queue_item):
    card = _get_queue_card(queue_item)
    if not card:
        return
    card.status = 'CANCELLED'
    card.cancelled_at = utcnow()

def _image_to_base64_object(image_source, label):
    if not image_source:
        return None, f"{label} ontbreekt."

    if isinstance(image_source, str) and image_source.startswith("data:image/"):
        return {"base64Data": image_source}, None

    try:
        response = requests.get(image_source, timeout=PRINT_REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        return None, f"{label} kon niet worden opgehaald: {exc}"

    content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
    if not content_type.startswith("image/"):
        guessed_type, _ = mimetypes.guess_type(image_source)
        content_type = guessed_type or "image/png"

    encoded = base64.b64encode(response.content).decode("ascii")
    return {"base64Data": f"data:{content_type};base64,{encoded}"}, None

def _build_print_payload(queue_item):
    product = {
        "name": queue_item.product_name or "",
        "packaging": queue_item.product_packaging or "Stuk",
        "sku": queue_item.product_sku or ""
    }
    product_image, product_image_error = _image_to_base64_object(
        queue_item.product_image_url,
        "Productafbeelding"
    )
    if product_image_error:
        return None, product_image_error
    product["image"] = product_image

    company = {}
    company_logo, company_logo_error = _image_to_base64_object(
        queue_item.company_logo_url,
        "Bedrijfslogo"
    )
    if company_logo_error:
        return None, company_logo_error
    company["logo"] = company_logo

    return {
        "printerId": queue_item.printer_id or "reception-badgy-01",
        "cardType": queue_item.card_type or "KANBAN_TWO_BIN",
        "data": {
            "header": {
                "text": queue_item.header_text or "",
                "color": queue_item.header_color or "#3B82F6",
                "textColor": "#FFFFFF"
            },
            "product": product,
            "company": company,
            "logistics": {
                "location": queue_item.location_text or "",
                "minLevel": int(queue_item.min_level or 0),
                "maxLevel": int(queue_item.max_level or 0)
            },
            "trigger": {
                "qrCodeValue": queue_item.qr_code_value or "",
                "humanReadableCode": queue_item.qr_human_readable or ""
            }
        },
        "options": {
            "orientation": "portrait",
            "doubleSided": False
        }
    }, None

def _print_service_root_url():
    if not PRINT_SERVICE_URL:
        return None
    parsed = urllib.parse.urlparse(PRINT_SERVICE_URL)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/"

def _print_service_api_base_url():
    root_url = _print_service_root_url()
    if not root_url:
        return None
    return root_url.rstrip('/')

def _resolve_print_service_api_url(path):
    base_url = _print_service_api_base_url()
    if not base_url:
        return None
    return urllib.parse.urljoin(f"{base_url}/", path.lstrip('/'))

def _print_service_headers():
    if PRINT_SERVICE_REQUIRE_API_KEY and not PRINT_SERVICE_API_KEY:
        return None, "PRINT_SERVICE_API_KEY ontbreekt terwijl API key verplicht is."

    headers = {}
    if PRINT_SERVICE_API_KEY:
        headers["X-API-Key"] = PRINT_SERVICE_API_KEY
    return headers, None

def _discover_preview_layout_endpoint():
    headers, header_err = _print_service_headers()
    if header_err:
        raise RuntimeError(header_err)

    request_format_url = _resolve_print_service_api_url('/api/v1/request-format')
    if not request_format_url:
        raise RuntimeError("PRINT_SERVICE_URL ontbreekt of is ongeldig.")

    try:
        response = requests.get(
            request_format_url,
            headers=headers,
            timeout=PRINT_REQUEST_TIMEOUT
        )
        response.raise_for_status()
        body = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"request-format endpoint faalde: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("request-format endpoint gaf geen geldige JSON terug.") from exc

    endpoint = body.get('previewLayoutEndpoint') or '/api/v1/layout-config'
    if not isinstance(endpoint, str) or not endpoint.strip():
        endpoint = '/api/v1/layout-config'
    return endpoint

def _fetch_preview_layout_config(endpoint):
    headers, header_err = _print_service_headers()
    if header_err:
        raise RuntimeError(header_err)

    layout_url = _resolve_print_service_api_url(endpoint)
    if not layout_url:
        raise RuntimeError("PRINT_SERVICE_URL ontbreekt of is ongeldig.")

    try:
        response = requests.get(
            layout_url,
            headers=headers,
            timeout=PRINT_REQUEST_TIMEOUT
        )
        response.raise_for_status()
        body = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"layout-config endpoint faalde: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("layout-config endpoint gaf geen geldige JSON terug.") from exc

    refresh_seconds = body.get('suggestedRefreshIntervalSeconds')
    if not isinstance(refresh_seconds, (int, float)) or refresh_seconds <= 0:
        refresh_seconds = DEFAULT_LAYOUT_REFRESH_SECONDS

    fetched_at = int(time.time())
    config = body.get('config') or {}
    raw_element_count = 0
    if isinstance(config.get('elements'), list):
        raw_element_count = len(config.get('elements'))
    elif isinstance(config.get('items'), list):
        raw_element_count = len(config.get('items'))
    elif isinstance(config.get('fields'), list):
        raw_element_count = len(config.get('fields'))

    return {
        "endpoint": endpoint,
        "layoutVersion": str(body.get('layoutVersion') or 'unknown'),
        "template": body.get('template') or '',
        "lastModifiedUtc": body.get('lastModifiedUtc'),
        "config": config,
        "fetchedAt": fetched_at,
        "nextRefreshAt": fetched_at + int(refresh_seconds),
        "suggestedRefreshIntervalSeconds": int(refresh_seconds),
        "debug": {
            "bodyKeys": list(body.keys()) if isinstance(body, dict) else [],
            "configKeys": list(config.keys()) if isinstance(config, dict) else [],
            "rawElementCount": raw_element_count
        }
    }

def _get_preview_layout_cache():
    with PREVIEW_LAYOUT_LOCK:
        if PREVIEW_LAYOUT_CACHE is None:
            return None
        return dict(PREVIEW_LAYOUT_CACHE)

def _set_preview_layout_cache(layout_cache):
    global PREVIEW_LAYOUT_CACHE
    with PREVIEW_LAYOUT_LOCK:
        PREVIEW_LAYOUT_CACHE = dict(layout_cache)

def get_preview_layout(force_refresh=False):
    cached = _get_preview_layout_cache()
    now = int(time.time())

    if cached and not force_refresh and now <= cached.get('nextRefreshAt', 0):
        return cached, False, None

    endpoint = cached.get('endpoint') if cached else None
    try:
        if not endpoint:
            endpoint = _discover_preview_layout_endpoint()
        latest = _fetch_preview_layout_config(endpoint)
        warning = None
        if cached and latest.get('layoutVersion') != cached.get('layoutVersion'):
            warning = (
                f"Preview-layout bijgewerkt van versie {cached.get('layoutVersion')} "
                f"naar {latest.get('layoutVersion')}."
            )
        _set_preview_layout_cache(latest)
        return latest, False, warning
    except RuntimeError as exc:
        if cached:
            return cached, True, f"Preview gebruikt verouderde layoutconfig: {exc}"
        raise RuntimeError(
            f"Geen layoutconfig beschikbaar. Controleer de printservice en probeer opnieuw. ({exc})"
        ) from exc

def test_print_service_connectivity():
    if not PRINT_SERVICE_URL:
        return False, "PRINT_SERVICE_URL ontbreekt."

    parsed = urllib.parse.urlparse(PRINT_SERVICE_URL)
    if not parsed.scheme or not parsed.hostname:
        return False, "PRINT_SERVICE_URL is ongeldig."

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=PRINT_CONNECT_TIMEOUT):
            pass
    except OSError as exc:
        return False, f"Poortcheck mislukt op {parsed.hostname}:{port} ({exc})."

    try:
        root_url = _print_service_root_url()
        headers, header_err = _print_service_headers()
        if header_err:
            return False, header_err
        resp = requests.get(root_url, headers=headers, timeout=PRINT_REQUEST_TIMEOUT)
        if resp.status_code >= 400:
            return False, f"Service bereikbaar, maar health-check gaf HTTP {resp.status_code}."
    except requests.RequestException as exc:
        return False, f"Poort open, maar service health-check faalde ({exc})."

    return True, f"Verbonden met printservice op {parsed.hostname}:{port}."

def send_queue_item_to_print_service(queue_item):
    if not PRINT_SERVICE_URL:
        return False, "PRINT_SERVICE_URL ontbreekt."

    payload, payload_error = _build_print_payload(queue_item)
    if payload_error:
        return False, payload_error
    headers, header_err = _print_service_headers()
    if header_err:
        return False, header_err

    try:
        response = requests.post(
            PRINT_SERVICE_URL,
            json=payload,
            headers=headers,
            timeout=PRINT_REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return True, None
    except requests.RequestException as exc:
        return False, f"Printservice fout: {exc}"

# --- ROUTES ---

@app.route('/')
def dashboard():
    if not check_db(): return render_template('dashboard.html')
    
    # Count voor print wachtrij (alleen voor huidig bedrijf)
    print_queue_count = 0
    open_scan_count = 0
    huidig_id = get_huidig_bedrijf_id()
    if huidig_id:
        try:
            print_queue_count = db.session.query(Print_Queue).filter_by(
                bedrijf_id=huidig_id, 
                status='PENDING'
            ).count()
            open_scan_count = db.session.query(KanbanScanlijstItem).filter(
                KanbanScanlijstItem.bedrijf_id == huidig_id,
                KanbanScanlijstItem.reset_at.is_(None)
            ).count()
        except Exception:
            print_queue_count = 0
            open_scan_count = 0

    return render_template(
        'dashboard.html',
        print_queue_count=print_queue_count,
        open_scan_count=open_scan_count
    )

@app.route('/switch-bedrijf/<int:bedrijf_id>')
def switch_bedrijf(bedrijf_id):
    if not check_db():
        return redirect(url_for('dashboard'))
    bestaat = db.session.query(Bedrijf).filter(Bedrijf.bedrijf_id == bedrijf_id).first()
    if not bestaat:
        flash('Bedrijf niet gevonden.', 'warning')
        return redirect(url_for('dashboard'))
    session['bedrijf_id'] = bedrijf_id
    flash('Bedrijf gewijzigd.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/bedrijf/nieuw', methods=['POST'])
def nieuw_bedrijf():
    if not check_db(): return redirect(url_for('dashboard'))
    
    naam = request.form.get('naam')
    if naam:
        try:
            nieuw = Bedrijf(naam=naam)
            db.session.add(nieuw)
            db.session.commit()
            session['bedrijf_id'] = nieuw.bedrijf_id
            flash(f'Bedrijf "{naam}" aangemaakt en geselecteerd.', 'success')
        except IntegrityError:
            db.session.rollback()
            flash('Een bedrijf met deze naam bestaat al.', 'warning')
        except Exception as e:
            db.session.rollback()
            flash(f'Fout bij aanmaken: {e}', 'danger')
            
    # Redirect naar beheer pagina zodat ze details kunnen invullen
    return redirect(url_for('beheer_bedrijf'))

# ... (REST VAN DE ROUTES ONGEWIJZIGD LATEN STAAN) ...
# Om de output te beperken, laat ik de bestaande routes hieronder even weg uit de display,
# maar in het echte bestand moeten ze behouden blijven.
# Hieronder staan ALLE routes die we eerder hadden, ongewijzigd:

@app.route('/assistent/kamers')
def assistent_kamers():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    try:
        ruimtes_query = db.session.query(Ruimte, Vestiging)\
            .join(Vestiging, Ruimte.vestiging_id == Vestiging.vestiging_id)\
            .filter(Vestiging.bedrijf_id == bedrijf_id)\
            .order_by(Vestiging.naam, Ruimte.nummer, Ruimte.naam).all() 
        ruimtes_data = []
        for ruimte, vestiging in ruimtes_query:
            count = db.session.query(Kast).filter_by(ruimte_id=ruimte.ruimte_id, bedrijf_id=bedrijf_id).count()
            ruimtes_data.append((ruimte, vestiging, count))
        return render_template('assistent_kamer_selectie.html', ruimtes=ruimtes_data)
    except Exception as e:
        print(f"Error: {e}")
        return redirect(url_for('dashboard'))

@app.route('/assistent/kamer/<int:ruimte_id>')
def assistent_kamer_view(ruimte_id):
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    
    ruimte = db.session.query(Ruimte).filter(Ruimte.ruimte_id == ruimte_id, Ruimte.bedrijf_id == bedrijf_id).first()
    if not ruimte:
        flash('Ruimte niet gevonden of geen toegang.', 'warning')
        return redirect(url_for('assistent_kamers'))

    if ruimte.ruimte_type_id:
        rt = db.session.query(Ruimte_Type).filter(
            Ruimte_Type.ruimte_type_id == ruimte.ruimte_type_id,
            Ruimte_Type.bedrijf_id == bedrijf_id
        ).first()
        ruimte.kleur_hex = rt.kleur_hex if rt else None
    else:
        ruimte.kleur_hex = None

    kasten_in_kamer = db.session.query(Kast).filter_by(ruimte_id=ruimte_id, bedrijf_id=bedrijf_id).all()
    kasten_data = {}
    for kast in kasten_in_kamer:
        inhoud = db.session.query(Voorraad_Positie, Lokaal_Artikel, Global_Catalogus)\
            .join(Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id)\
            .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
            .filter(Voorraad_Positie.kast_id == kast.kast_id, Voorraad_Positie.bedrijf_id == bedrijf_id)\
            .all()
        kasten_data[kast] = inhoud
    alle_artikelen = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id).order_by(Lokaal_Artikel.eigen_naam).all()
    return render_template('assistent_kamer_view.html', ruimte=ruimte, kasten_data=kasten_data, alle_artikelen=alle_artikelen)

@app.route('/assistent/update-voorraad/<int:voorraad_positie_id>', methods=['POST'])
def update_voorraad_positie(voorraad_positie_id):
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    positie = get_scoped_item(Voorraad_Positie, voorraad_positie_id, bedrijf_id)
    if not positie:
        flash('Voorraadpositie niet gevonden of geen toegang.', 'warning')
        return redirect(url_for('assistent_kamers'))

    if positie:
        positie.trigger_min = request.form.get('trigger_min')
        positie.target_max = request.form.get('target_max')
        db.session.commit()
        flash('Voorraadniveaus bijgewerkt.', 'success')
    kast = get_scoped_item(Kast, positie.kast_id, bedrijf_id)
    if not kast:
        return redirect(url_for('assistent_kamers'))
    return redirect(url_for('assistent_kamer_view', ruimte_id=kast.ruimte_id))

@app.route('/assistent/kast/<int:kast_id>/toevoegen', methods=['POST'])
def add_to_kast_from_room(kast_id):
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    artikel_id = request.form.get('artikel_id', type=int)
    kast = get_scoped_item(Kast, kast_id, bedrijf_id)
    artikel = get_scoped_item(Lokaal_Artikel, artikel_id, bedrijf_id) if artikel_id else None
    if not kast or not artikel:
        flash('Ongeldige kast of artikelkeuze.', 'warning')
        return redirect(url_for('assistent_kamers'))

    bestaat = db.session.query(Voorraad_Positie).filter_by(
        bedrijf_id=bedrijf_id,
        kast_id=kast_id,
        lokaal_artikel_id=artikel_id
    ).first()
    if not bestaat:
        nieuw = Voorraad_Positie(
            bedrijf_id=bedrijf_id, kast_id=kast_id, lokaal_artikel_id=artikel_id,
            strategie='TWO_BIN', trigger_min=1, target_max=2
        )
        db.session.add(nieuw)
        db.session.flush()
        nieuw.qr_code = f"{API_BASE_URL}/{nieuw.voorraad_positie_id}"
        db.session.commit()
        flash('Artikel toegevoegd.', 'success')
    else:
        flash('Artikel zit al in de kast.', 'warning')
    return redirect(url_for('assistent_kamer_view', ruimte_id=kast.ruimte_id))

@app.route('/assistent/kanban/aanvragen/enkel/<int:voorraad_positie_id>', methods=['POST'])
def kanban_aanvragen_enkel(voorraad_positie_id):
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    try:
        result = db.session.query(Voorraad_Positie, Lokaal_Artikel, Global_Catalogus, Kast, Ruimte, Ruimte_Type, Bedrijf)\
            .join(Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id)\
            .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
            .join(Kast, Voorraad_Positie.kast_id == Kast.kast_id)\
            .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
            .outerjoin(Ruimte_Type, Ruimte.ruimte_type_id == Ruimte_Type.ruimte_type_id)\
            .join(Bedrijf, Voorraad_Positie.bedrijf_id == Bedrijf.bedrijf_id)\
            .filter(
                Voorraad_Positie.voorraad_positie_id == voorraad_positie_id,
                Voorraad_Positie.bedrijf_id == bedrijf_id
            ).first()

        if not result:
            flash("Artikel niet gevonden.", "danger")
            return redirect(request.referrer)

        queue_item = create_queue_item(*result)
        db.session.add(queue_item)
        db.session.commit()
        
        flash("Kanban kaartje aangevraagd!", "success")
    except Exception as e:
        db.session.rollback()
        print(e)
        flash(f"Fout bij aanvragen: {e}", "danger")
        
    return redirect(request.referrer)

@app.route('/assistent/kanban/aanvragen/kast/<int:kast_id>', methods=['POST'])
def kanban_aanvragen_kast(kast_id):
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    try:
        results = db.session.query(Voorraad_Positie, Lokaal_Artikel, Global_Catalogus, Kast, Ruimte, Ruimte_Type, Bedrijf)\
            .join(Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id)\
            .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
            .join(Kast, Voorraad_Positie.kast_id == Kast.kast_id)\
            .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
            .outerjoin(Ruimte_Type, Ruimte.ruimte_type_id == Ruimte_Type.ruimte_type_id)\
            .join(Bedrijf, Voorraad_Positie.bedrijf_id == Bedrijf.bedrijf_id)\
            .filter(Voorraad_Positie.kast_id == kast_id, Voorraad_Positie.bedrijf_id == bedrijf_id).all()

        if not results:
            flash("Deze kast is leeg.", "warning")
            return redirect(request.referrer)

        count = 0
        for row in results:
            queue_item = create_queue_item(*row)
            db.session.add(queue_item)
            count += 1
            
        db.session.commit()
        flash(f"{count} kaartjes aangevraagd voor kast!", "success")
        
    except Exception as e:
        db.session.rollback()
        flash(f"Fout bij batch aanvraag: {e}", "danger")

    return redirect(request.referrer)

@app.route('/assistent/print-queue')
def assistent_print_queue():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    preview_layout_warning = None
    preview_layout_error = None
    
    queue_items = db.session.query(Print_Queue)\
        .filter(Print_Queue.bedrijf_id == bedrijf_id, Print_Queue.status == 'PENDING')\
        .order_by(Print_Queue.aangemaakt_op.desc()).all()

    try:
        _, stale_layout, layout_warning = get_preview_layout()
        if stale_layout:
            preview_layout_warning = layout_warning
        elif layout_warning:
            preview_layout_warning = layout_warning
    except RuntimeError as exc:
        preview_layout_error = str(exc)

    return render_template(
        'assistent_print_queue.html',
        queue_items=queue_items,
        print_service_url=PRINT_SERVICE_URL,
        preview_layout_warning=preview_layout_warning,
        preview_layout_error=preview_layout_error
    )

@app.route('/api/preview-layout')
def api_preview_layout():
    force_refresh = request.args.get('refresh') == '1'
    try:
        layout, stale, warning = get_preview_layout(force_refresh=force_refresh)
        return jsonify({
            "ok": True,
            "stale": stale,
            "warning": warning,
            "layout": layout
        })
    except RuntimeError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc)
        }), 503


def _get_open_scan_rows(bedrijf_id):
    return db.session.query(
        KanbanScanlijstItem,
        KanbanKaart,
        Voorraad_Positie,
        Lokaal_Artikel,
        Global_Catalogus,
        Kast,
        Ruimte,
        Ruimte_Type,
        Bedrijf,
        Vestiging
    ).join(
        KanbanKaart, KanbanScanlijstItem.kaart_id == KanbanKaart.kaart_id
    ).outerjoin(
        Voorraad_Positie, KanbanKaart.voorraad_positie_id == Voorraad_Positie.voorraad_positie_id
    ).outerjoin(
        Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id
    ).outerjoin(
        Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id
    ).outerjoin(
        Kast, Voorraad_Positie.kast_id == Kast.kast_id
    ).outerjoin(
        Ruimte, Kast.ruimte_id == Ruimte.ruimte_id
    ).outerjoin(
        Ruimte_Type, Ruimte.ruimte_type_id == Ruimte_Type.ruimte_type_id
    ).outerjoin(
        Vestiging, Ruimte.vestiging_id == Vestiging.vestiging_id
    ).outerjoin(
        Bedrijf, KanbanKaart.bedrijf_id == Bedrijf.bedrijf_id
    ).filter(
        KanbanScanlijstItem.bedrijf_id == bedrijf_id,
        KanbanScanlijstItem.reset_at.is_(None)
    ).order_by(KanbanScanlijstItem.last_scanned_at.desc()).all()


def _group_rows_by_location(rows, row_key, extractor):
    grouped = []
    vestiging_lookup = {}

    for row in rows:
        vestiging, ruimte_type, ruimte, kast = extractor(row)
        vestiging_key = vestiging.vestiging_id if vestiging else 'geen-vestiging'
        ruimte_type_key = ruimte_type.ruimte_type_id if ruimte_type else f"geen-type-{vestiging_key}"
        ruimte_key = ruimte.ruimte_id if ruimte else f"geen-ruimte-{vestiging_key}"
        kast_key = kast.kast_id if kast else f"geen-kast-{ruimte_key}"

        vestiging_group = vestiging_lookup.get(vestiging_key)
        if not vestiging_group:
            vestiging_group = {
                "key": vestiging_key,
                "naam": vestiging.naam if vestiging else "Onbekende vestiging",
                "ruimte_types": [],
                "_ruimte_type_lookup": {}
            }
            vestiging_lookup[vestiging_key] = vestiging_group
            grouped.append(vestiging_group)

        ruimte_type_group = vestiging_group["_ruimte_type_lookup"].get(ruimte_type_key)
        if not ruimte_type_group:
            ruimte_type_group = {
                "key": ruimte_type_key,
                "naam": ruimte_type.naam if ruimte_type else "Geen ruimtetype",
                "kleur_hex": (ruimte_type.kleur_hex if ruimte_type and ruimte_type.kleur_hex else "#CBD5E1"),
                "ruimtes": [],
                "_ruimte_lookup": {}
            }
            vestiging_group["_ruimte_type_lookup"][ruimte_type_key] = ruimte_type_group
            vestiging_group["ruimte_types"].append(ruimte_type_group)

        ruimte_group = ruimte_type_group["_ruimte_lookup"].get(ruimte_key)
        if not ruimte_group:
            ruimte_group = {
                "key": ruimte_key,
                "naam": ruimte.naam if ruimte else "Onbekende ruimte",
                "nummer": ruimte.nummer if ruimte else None,
                "kasten": [],
                "_kast_lookup": {}
            }
            ruimte_type_group["_ruimte_lookup"][ruimte_key] = ruimte_group
            ruimte_type_group["ruimtes"].append(ruimte_group)

        kast_group = ruimte_group["_kast_lookup"].get(kast_key)
        if not kast_group:
            kast_group = {
                "key": kast_key,
                "naam": kast.naam if kast else "Onbekende kast",
                "type_opslag": kast.type_opslag if kast else None,
                row_key: []
            }
            ruimte_group["_kast_lookup"][kast_key] = kast_group
            ruimte_group["kasten"].append(kast_group)

        kast_group[row_key].append(row)

    for vestiging_group in grouped:
        vestiging_group.pop("_ruimte_type_lookup", None)
        vestiging_group["ruimte_types"].sort(key=lambda item: item["naam"])
        for ruimte_type_group in vestiging_group["ruimte_types"]:
            ruimte_type_group.pop("_ruimte_lookup", None)
            ruimte_type_group["ruimtes"].sort(key=lambda item: ((item["nummer"] or ""), item["naam"]))
            for ruimte_group in ruimte_type_group["ruimtes"]:
                ruimte_group.pop("_kast_lookup", None)
                ruimte_group["kasten"].sort(key=lambda item: item["naam"])

    grouped.sort(key=lambda item: item["naam"])
    return grouped


def _group_scan_rows(rows):
    return _group_rows_by_location(
        rows,
        "scan_rows",
        lambda row: (row[9], row[7], row[6], row[5])
    )


def _get_kamerlijst_rows(bedrijf_id, ruimte_id=None):
    query = db.session.query(
        Voorraad_Positie,
        Lokaal_Artikel,
        Global_Catalogus,
        Kast,
        Ruimte,
        Ruimte_Type,
        Vestiging
    ).join(
        Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id
    ).outerjoin(
        Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id
    ).outerjoin(
        Kast, Voorraad_Positie.kast_id == Kast.kast_id
    ).outerjoin(
        Ruimte, Kast.ruimte_id == Ruimte.ruimte_id
    ).outerjoin(
        Ruimte_Type, Ruimte.ruimte_type_id == Ruimte_Type.ruimte_type_id
    ).outerjoin(
        Vestiging, Ruimte.vestiging_id == Vestiging.vestiging_id
    ).filter(
        Voorraad_Positie.bedrijf_id == bedrijf_id
    )

    if ruimte_id is not None:
        query = query.filter(Ruimte.ruimte_id == ruimte_id)

    return query.order_by(
        Vestiging.naam,
        Ruimte_Type.naam,
        Ruimte.nummer,
        Ruimte.naam,
        Kast.naam,
        Lokaal_Artikel.eigen_naam
    ).all()


def _group_kamerlijst_rows(rows):
    return _group_rows_by_location(
        rows,
        "inventory_rows",
        lambda row: (row[6], row[5], row[4], row[3])
    )


@app.route('/assistent/scanlijst')
def assistent_scanlijst():
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    rows = _get_open_scan_rows(bedrijf_id)
    return render_template('assistent_scanlijst.html', rows=rows, grouped_rows=_group_scan_rows(rows))


@app.route('/assistent/scanlijst/print')
def assistent_scanlijst_print():
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    rows = _get_open_scan_rows(bedrijf_id)
    return render_template(
        'assistent_scanlijst_print.html',
        rows=rows,
        grouped_rows=_group_scan_rows(rows),
        generated_at=utcnow()
    )


@app.route('/assistent/scanlijst/reset', methods=['POST'])
def assistent_scanlijst_reset():
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    rows = db.session.query(KanbanScanlijstItem).filter(
        KanbanScanlijstItem.bedrijf_id == bedrijf_id,
        KanbanScanlijstItem.reset_at.is_(None)
    ).all()
    if not rows:
        flash('Geen openstaande scans om te resetten.', 'info')
        return redirect(url_for('assistent_scanlijst'))

    reset_at = utcnow()
    reset_by = _get_reset_actor()
    for row in rows:
        row.reset_at = reset_at
        row.reset_by = reset_by
    db.session.commit()
    flash(f'{len(rows)} scan(s) gereset.', 'success')
    return redirect(url_for('assistent_scanlijst'))


@app.route('/assistent/kamerlijst')
def assistent_kamerlijst():
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    rows = _get_kamerlijst_rows(bedrijf_id)
    return render_template('assistent_kamerlijst.html', grouped_rows=_group_kamerlijst_rows(rows))


@app.route('/assistent/kamerlijst/print/<int:ruimte_id>')
def assistent_kamerlijst_print(ruimte_id):
    if not check_db():
        return redirect(url_for('dashboard'))

    bedrijf_id = get_huidig_bedrijf_id()
    ruimte = db.session.query(Ruimte).filter(
        Ruimte.ruimte_id == ruimte_id,
        Ruimte.bedrijf_id == bedrijf_id
    ).first()
    if not ruimte:
        flash('Ruimte niet gevonden of geen toegang.', 'warning')
        return redirect(url_for('assistent_kamerlijst'))

    rows = _get_kamerlijst_rows(bedrijf_id, ruimte_id=ruimte_id)
    return render_template(
        'assistent_kamerlijst_print.html',
        grouped_rows=_group_kamerlijst_rows(rows),
        selected_room=ruimte,
        generated_at=utcnow()
    )

@app.route('/assistent/print-queue/test-verbinding', methods=['POST'])
def test_print_verbinding():
    if not check_db():
        return redirect(url_for('dashboard'))
    ok, detail = test_print_service_connectivity()
    if ok:
        flash(detail, 'success')
    else:
        flash(detail, 'danger')
    return redirect(url_for('assistent_print_queue'))

@app.route('/assistent/print-queue/verstuur/<int:print_id>', methods=['POST'])
def verstuur_print_opdracht(print_id):
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()

    item = db.session.query(Print_Queue).filter(
        Print_Queue.print_id == print_id,
        Print_Queue.bedrijf_id == bedrijf_id,
        Print_Queue.status == 'PENDING'
    ).first()
    if not item:
        flash("Printopdracht niet gevonden of al verwerkt.", "warning")
        return redirect(url_for('assistent_print_queue'))

    ok, detail = test_print_service_connectivity()
    if not ok:
        flash(detail, 'danger')
        return redirect(url_for('assistent_print_queue'))

    sent, error_msg = send_queue_item_to_print_service(item)
    if sent:
        _mark_card_printed(item)
        db.session.delete(item)
        db.session.commit()
        flash("Kaartje naar lokale printer gestuurd.", "success")
    else:
        flash(error_msg, "danger")
    return redirect(url_for('assistent_print_queue'))

@app.route('/assistent/print-queue/verstuur-alles', methods=['POST'])
def verstuur_alle_print_opdrachten():
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()

    items = db.session.query(Print_Queue).filter(
        Print_Queue.bedrijf_id == bedrijf_id,
        Print_Queue.status == 'PENDING'
    ).order_by(Print_Queue.aangemaakt_op.asc()).all()

    if not items:
        flash("Geen openstaande printopdrachten.", "info")
        return redirect(url_for('assistent_print_queue'))

    ok, detail = test_print_service_connectivity()
    if not ok:
        flash(detail, 'danger')
        return redirect(url_for('assistent_print_queue'))

    success_count = 0
    fail_count = 0
    fail_messages = []

    for item in items:
        sent, error_msg = send_queue_item_to_print_service(item)
        if sent:
            _mark_card_printed(item)
            db.session.delete(item)
            success_count += 1
        else:
            fail_count += 1
            if len(fail_messages) < 3:
                fail_messages.append(f"ID {item.print_id}: {error_msg}")

    db.session.commit()

    if success_count:
        flash(f"{success_count} kaartje(s) verstuurd naar lokale printer.", "success")
    if fail_count:
        extra = " | ".join(fail_messages)
        flash(f"{fail_count} opdracht(en) mislukt. {extra}", "danger")
    return redirect(url_for('assistent_print_queue'))

@app.route('/assistent/print-queue/annuleren/<int:print_id>', methods=['POST'])
def annuleren_print_opdracht(print_id):
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    item = db.session.query(Print_Queue).filter(
        Print_Queue.print_id == print_id,
        Print_Queue.bedrijf_id == bedrijf_id
    ).first()
    if item and item.status == 'PENDING':
        _mark_card_cancelled(item)
        db.session.delete(item)
        db.session.commit()
        flash("Aanvraag geannuleerd.", "info")
    return redirect(url_for('assistent_print_queue'))

@app.route('/artikelen-beheer', methods=['GET', 'POST'])
def artikelen_beheer():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()

    if request.method == 'POST':
        actie = request.form.get('actie')
        if actie == 'nieuw_lokaal':
            nieuw = Lokaal_Artikel(bedrijf_id=bedrijf_id, eigen_naam=request.form.get('naam'), verpakkingseenheid_tekst=request.form.get('eenheid'))
            file = request.files.get('afbeelding')
            if file:
                url = upload_image_to_azure(file)
                if url and "ERROR" not in url: nieuw.foto_url = url
            db.session.add(nieuw)
            db.session.commit()
            flash('Lokaal artikel aangemaakt.', 'success')
        elif actie == 'koppel_global':
            global_id = request.form.get('global_id', type=int)
            global_item = db.session.query(Global_Catalogus).filter(Global_Catalogus.global_id == global_id).first()
            bestaat = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id, global_id=global_id).first()
            if global_item and not bestaat:
                nieuw = Lokaal_Artikel(bedrijf_id=bedrijf_id, global_id=global_id, eigen_naam=global_item.generieke_naam, verpakkingseenheid_tekst='Stuk')
                db.session.add(nieuw)
                db.session.commit()
                flash('Gekoppeld.', 'success')
        elif actie == 'bewerk_artikel':
            artikel_id = request.form.get('artikel_id', type=int)
            artikel = get_scoped_item(Lokaal_Artikel, artikel_id, bedrijf_id)
            if artikel:
                artikel.eigen_naam = request.form.get('naam')
                artikel.verpakkingseenheid_tekst = request.form.get('eenheid')
                file = request.files.get('afbeelding')
                if file:
                    url = upload_image_to_azure(file)
                    if url and "ERROR" not in url: artikel.foto_url = url
                db.session.commit()
                flash('Artikel bijgewerkt.', 'success')
        return redirect(url_for('artikelen_beheer'))

    raw_results = db.session.query(Lokaal_Artikel, Global_Catalogus).outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id).filter(Lokaal_Artikel.bedrijf_id == bedrijf_id).order_by(Lokaal_Artikel.eigen_naam).all()
    view_data = [{'obj': l, 'display_naam': l.eigen_naam, 'display_foto': l.foto_url or (g.foto_url if g else None), 'is_globaal': g is not None, 'is_afwijkend': g and l.eigen_naam != g.generieke_naam, 'oorsprong_naam': g.generieke_naam if g else None} for l, g in raw_results]
    linked_ids = db.session.query(Lokaal_Artikel.global_id).filter(Lokaal_Artikel.bedrijf_id == bedrijf_id, Lokaal_Artikel.global_id.isnot(None))
    beschikbare_globals = db.session.query(Global_Catalogus).filter(Global_Catalogus.global_id.notin_(linked_ids)).all()
    return render_template('artikelen_beheer.html', artikelen=view_data, beschikbare_globals=beschikbare_globals)

@app.route('/artikelen-beheer/vervang', methods=['POST'])
def vervang_artikel():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    oud_lokaal_id = request.form.get('oud_lokaal_id', type=int)
    nieuw_global_id = request.form.get('nieuw_global_id', type=int)
    
    oud_artikel = get_scoped_item(Lokaal_Artikel, oud_lokaal_id, bedrijf_id)
    if not oud_artikel:
        flash('Bronartikel niet gevonden of geen toegang.', 'warning')
        return redirect(url_for('artikelen_beheer'))

    bestaand_doel = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id, global_id=nieuw_global_id).first()
    
    if bestaand_doel: doel_id = bestaand_doel.lokaal_artikel_id
    else:
        g_item = db.session.query(Global_Catalogus).filter(Global_Catalogus.global_id == nieuw_global_id).first()
        if not g_item:
            flash('Doelartikel uit catalogus niet gevonden.', 'warning')
            return redirect(url_for('artikelen_beheer'))
        nieuw = Lokaal_Artikel(bedrijf_id=bedrijf_id, global_id=nieuw_global_id, eigen_naam=g_item.generieke_naam, verpakkingseenheid_tekst=oud_artikel.verpakkingseenheid_tekst)
        db.session.add(nieuw)
        db.session.flush()
        doel_id = nieuw.lokaal_artikel_id

    posities = db.session.query(Voorraad_Positie).filter_by(bedrijf_id=bedrijf_id, lokaal_artikel_id=oud_lokaal_id).all()
    for pos in posities:
        if db.session.query(Voorraad_Positie).filter_by(bedrijf_id=bedrijf_id, kast_id=pos.kast_id, lokaal_artikel_id=doel_id).first(): db.session.delete(pos)
        else: pos.lokaal_artikel_id = doel_id
    
    if oud_artikel: db.session.delete(oud_artikel)
    db.session.commit()
    flash('Artikel vervangen.', 'success')
    return redirect(url_for('artikelen_beheer'))

@app.route('/api/artikel-gebruik/<int:artikel_id>')
def api_artikel_gebruik(artikel_id):
    if not check_db():
        return jsonify([])
    bedrijf_id = get_huidig_bedrijf_id()
    artikel = get_scoped_item(Lokaal_Artikel, artikel_id, bedrijf_id)
    if not artikel:
        return jsonify([])

    posities = db.session.query(Voorraad_Positie, Kast, Ruimte)\
        .join(Kast, Voorraad_Positie.kast_id == Kast.kast_id)\
        .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
        .filter(
            Voorraad_Positie.lokaal_artikel_id == artikel_id,
            Voorraad_Positie.bedrijf_id == bedrijf_id
        ).all()
    return jsonify([{'ruimte': r.naam, 'kast': k.naam, 'min': p.trigger_min, 'max': p.target_max} for p, k, r in posities])

@app.route('/beheer/catalogus', methods=['GET', 'POST'])
def beheer_catalogus():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    if request.method == 'POST':
        actie = request.form.get('actie')
        if actie == 'nieuw_global':
            nieuw = Global_Catalogus(generieke_naam=request.form.get('naam'), ean_code=request.form.get('ean'), categorie=request.form.get('categorie'))
            file = request.files.get('afbeelding')
            if file:
                url = upload_image_to_azure(file)
                if url and "ERROR" not in url: nieuw.foto_url = url
            db.session.add(nieuw)
            db.session.commit()
            flash('Global item gemaakt.', 'success')
        elif actie == 'koppel_lokaal':
            global_id = request.form.get('global_id', type=int)
            global_item = db.session.query(Global_Catalogus).filter(Global_Catalogus.global_id == global_id).first()
            bestaat = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id, global_id=global_id).first()
            if global_item and not bestaat:
                nieuw = Lokaal_Artikel(bedrijf_id=bedrijf_id, global_id=global_id, eigen_naam=global_item.generieke_naam, verpakkingseenheid_tekst="Stuk")
                db.session.add(nieuw)
                db.session.commit()
                flash('Opgenomen in lokaal assortiment.', 'success')
        elif actie == 'bewerk_global':
            global_id = request.form.get('global_id', type=int)
            item = db.session.query(Global_Catalogus).filter(Global_Catalogus.global_id == global_id).first()
            if item:
                item.generieke_naam = request.form.get('naam')
                item.ean_code = request.form.get('ean')
                item.categorie = request.form.get('categorie')
                file = request.files.get('afbeelding')
                if file:
                    url = upload_image_to_azure(file)
                    if url and "ERROR" not in url: item.foto_url = url
                db.session.commit()
                flash('Global item bijgewerkt', 'success')
        elif actie == 'verwijder_global':
            global_id = request.form.get('global_id', type=int)
            usage_count = db.session.query(Lokaal_Artikel).filter_by(global_id=global_id).count()
            if usage_count > 0:
                flash(f'Kan item NIET verwijderen: in gebruik.', 'danger')
            else:
                item = db.session.query(Global_Catalogus).filter(Global_Catalogus.global_id == global_id).first()
                if item:
                    db.session.delete(item)
                    db.session.commit()
                    flash('Item verwijderd.', 'success')
        return redirect(url_for('beheer_catalogus'))

    globals = db.session.query(Global_Catalogus).all()
    lokale_ids = [a.global_id for a in db.session.query(Lokaal_Artikel.global_id).filter_by(bedrijf_id=bedrijf_id).all()]
    return render_template('beheer_catalogus.html', globals=globals, lokale_ids=lokale_ids)

@app.route('/beheer/bedrijf', methods=['GET', 'POST'])
def beheer_bedrijf():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    bedrijf = db.session.query(Bedrijf).filter(Bedrijf.bedrijf_id == bedrijf_id).first()
    if not bedrijf:
        flash('Bedrijf niet gevonden.', 'warning')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        bedrijf.naam = request.form.get('naam')
        file = request.files.get('logo')
        if file:
            url = upload_image_to_azure(file)
            if url and "ERROR" not in url: bedrijf.logo_url = url
        db.session.commit()
        return redirect(url_for('beheer_bedrijf'))
    return render_template('beheer_bedrijf.html', bedrijf=bedrijf)

@app.route('/beheer/infra', methods=['GET', 'POST'])
def beheer_infra():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    active_vestiging_id = request.args.get('vestiging_id', type=int)
    active_ruimte_id = request.args.get('ruimte_id', type=int)

    if request.method == 'POST':
        actie = request.form.get('actie')
        try:
            if actie == 'nieuwe_vestiging':
                db.session.add(Vestiging(bedrijf_id=bedrijf_id, naam=request.form.get('naam'), adres=request.form.get('adres')))
                db.session.commit()
            elif actie == 'nieuw_ruimte_type':
                db.session.add(Ruimte_Type(bedrijf_id=bedrijf_id, naam=request.form.get('naam'), kleur_hex=request.form.get('kleur')))
                db.session.commit()
            elif actie == 'nieuwe_ruimte':
                vest_id = request.form.get('vestiging_id', type=int)
                vestiging = get_scoped_item(Vestiging, vest_id, bedrijf_id)
                if not vestiging:
                    flash('Vestiging niet gevonden of geen toegang.', 'warning')
                    return redirect(url_for('beheer_infra'))
                nieuwe_ruimte = Ruimte(bedrijf_id=bedrijf_id, vestiging_id=vest_id, naam=request.form.get('naam'), nummer=request.form.get('nummer'), ruimte_type_id=request.form.get('ruimte_type_id'), type_ruimte='KAMER')
                db.session.add(nieuwe_ruimte)
                db.session.flush()
                kopieer_id = request.form.get('kopieer_van_ruimte_id', type=int)
                if kopieer_id:
                    bron_ruimte = get_scoped_item(Ruimte, kopieer_id, bedrijf_id)
                    if bron_ruimte:
                        bron_kasten = db.session.query(Kast).filter_by(ruimte_id=kopieer_id, bedrijf_id=bedrijf_id).all()
                    else:
                        bron_kasten = []
                    for bron_kast in bron_kasten:
                        nieuwe_kast = Kast(bedrijf_id=bedrijf_id, ruimte_id=nieuwe_ruimte.ruimte_id, naam=bron_kast.naam, type_opslag=bron_kast.type_opslag)
                        db.session.add(nieuwe_kast)
                        db.session.flush()
                        posities = db.session.query(Voorraad_Positie).filter_by(kast_id=bron_kast.kast_id, bedrijf_id=bedrijf_id).all()
                        for pos in posities:
                            nieuw_pos = Voorraad_Positie(bedrijf_id=bedrijf_id, kast_id=nieuwe_kast.kast_id, lokaal_artikel_id=pos.lokaal_artikel_id, strategie=pos.strategie, trigger_min=pos.trigger_min, target_max=pos.target_max, locatie_foto_url=pos.locatie_foto_url)
                            db.session.add(nieuw_pos)
                            db.session.flush()
                            nieuw_pos.qr_code = f"{API_BASE_URL}/{nieuw_pos.voorraad_positie_id}"
                db.session.commit()
                return redirect(url_for('beheer_infra', vestiging_id=vest_id))
            elif actie == 'nieuwe_kast':
                ruimte_id = request.form.get('ruimte_id', type=int)
                ruimte = get_scoped_item(Ruimte, ruimte_id, bedrijf_id)
                if not ruimte:
                    flash('Ruimte niet gevonden of geen toegang.', 'warning')
                    return redirect(url_for('beheer_infra'))
                db.session.add(Kast(bedrijf_id=bedrijf_id, ruimte_id=ruimte_id, naam=request.form.get('naam'), type_opslag=request.form.get('type_opslag')))
                db.session.commit()
                return redirect(url_for('beheer_infra', vestiging_id=ruimte.vestiging_id, ruimte_id=ruimte_id))
        except IntegrityError as e:
            db.session.rollback()
            if "CHK_Kast_Type" in str(e): flash("Fout: Ongeldig type opslag.", 'danger')
            else: flash(f"Database fout: {e}", 'danger')
        return redirect(url_for('beheer_infra', vestiging_id=active_vestiging_id, ruimte_id=active_ruimte_id))

    vestigingen = db.session.query(Vestiging).filter_by(bedrijf_id=bedrijf_id).all()
    ruimte_types = db.session.query(Ruimte_Type).filter_by(bedrijf_id=bedrijf_id).all()
    if active_vestiging_id:
        active_vestiging = get_scoped_item(Vestiging, active_vestiging_id, bedrijf_id)
        if not active_vestiging:
            active_vestiging_id = None
    ruimtes = []
    if active_vestiging_id:
        ruimtes = db.session.query(Ruimte).filter_by(vestiging_id=active_vestiging_id, bedrijf_id=bedrijf_id).order_by(Ruimte.nummer, Ruimte.naam).all()
    if active_ruimte_id:
        active_ruimte = get_scoped_item(Ruimte, active_ruimte_id, bedrijf_id)
        if not active_ruimte:
            active_ruimte_id = None
    kasten = []
    if active_ruimte_id:
        kasten = db.session.query(Kast).filter_by(ruimte_id=active_ruimte_id, bedrijf_id=bedrijf_id).all()
    alle_ruimtes = db.session.query(Ruimte).join(Vestiging).filter(Vestiging.bedrijf_id == bedrijf_id).all()
    return render_template('beheer_infra.html', vestigingen=vestigingen, ruimtes=ruimtes, kasten=kasten, alle_ruimtes=alle_ruimtes, ruimte_types=ruimte_types, active_vestiging_id=active_vestiging_id, active_ruimte_id=active_ruimte_id)

@app.route('/beheer/verwijder/<type>/<int:id>', methods=['POST'])
def verwijder_item(type, id):
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    try:
        item = None
        if type == 'artikel':
            item = get_scoped_item(Lokaal_Artikel, id, bedrijf_id)
        elif type == 'voorraad':
            item = get_scoped_item(Voorraad_Positie, id, bedrijf_id)
        elif type == 'vestiging':
            item = get_scoped_item(Vestiging, id, bedrijf_id)
        elif type == 'ruimte':
            item = get_scoped_item(Ruimte, id, bedrijf_id)
        elif type == 'kast':
            item = get_scoped_item(Kast, id, bedrijf_id)
        elif type == 'ruimte_type':
            item = get_scoped_item(Ruimte_Type, id, bedrijf_id)
        else:
            flash('Onbekend itemtype.', 'warning')
            return redirect(request.referrer or url_for('dashboard'))
        
        if item:
            db.session.delete(item)
            db.session.commit()
            flash('Verwijderd.', 'success')
        else:
            flash('Item niet gevonden of geen toegang.', 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Kan niet verwijderen: {e}', 'danger')
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/beheer/update/<type>/<int:id>', methods=['POST'])
def update_item(type, id):
    # Generieke update functie
    return redirect(request.referrer or url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=debug_mode)
