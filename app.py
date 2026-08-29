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
from typing import NamedTuple
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
from kanban_domain import (
    KanbanSettingsError,
    KanbanStandard,
    LocatiekaartInhoud,
    LocatiekaartStatus,
    Materiaaltype,
    effective_kanban_settings,
    normalized_position_overrides,
    normalize_material_type,
)

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
database_url = os.environ.get('DATABASE_URL')
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
LOCATION_CARDS_PRINTER_ID = os.environ.get(
    'LOCATION_CARDS_PRINTER_ID',
    'location-cards-a4-01',
)
LOCATION_CARDS_PRINT_ENDPOINT = '/api/v1/print-location-cards'
LOCATION_CARDS_CARD_TYPE = 'LOCATION_A4_DUPLEX'
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
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or (
    f"mssql+pyodbc://{encoded_user}:{encoded_pass}@{db_server}/{db_name}"
    f"?driver={driver}&TrustServerCertificate=yes"
)
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


class LocatiekaartVersie(db.Model):
    __tablename__ = 'Locatiekaart_Versie'

    locatiekaart_versie_id = db.Column(db.Integer, primary_key=True)
    bedrijf_id = db.Column(db.Integer, nullable=False, index=True)
    voorraad_positie_id = db.Column(db.Integer, nullable=False, index=True)
    lokaal_artikel_id = db.Column(db.Integer, nullable=False, index=True)
    inhoud_hash = db.Column(db.String(64), nullable=False)
    artikelnaam = db.Column(db.String(255), nullable=False)
    artikel_foto_url = db.Column(db.String(2048), nullable=True)
    bedrijfslogo_url = db.Column(db.String(2048), nullable=True)
    vestiging_naam = db.Column(db.String(255), nullable=False)
    ruimte_naam = db.Column(db.String(255), nullable=False)
    opslaglocatie_naam = db.Column(db.String(255), nullable=False)
    kamertype_naam = db.Column(db.String(255), nullable=True)
    kamertype_kleur = db.Column(db.String(20), nullable=True)
    materiaaltype = db.Column(db.String(20), nullable=False)
    min_level = db.Column(db.Integer, nullable=True)
    status = db.Column(
        db.String(20),
        nullable=False,
        default=LocatiekaartStatus.PENDING_PRINT.value,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)
    printed_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    superseded_at = db.Column(db.DateTime, nullable=True)


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


class QueueItemSource(NamedTuple):
    card: object
    position: object
    article: object


def ensure_scan_schema():
    inspector = inspect(db.engine)
    db.create_all()

    if inspector.has_table('Print_Queue'):
        existing_columns = {col['name'] for col in inspector.get_columns('Print_Queue')}
        if 'kaart_id' not in existing_columns:
            db.session.execute(text("ALTER TABLE Print_Queue ADD kaart_id NVARCHAR(36) NULL"))
            db.session.commit()

    ensure_kanban_settings_schema()


def ensure_kanban_settings_schema():
    """Add expand-step columns without rewriting or deleting legacy values."""
    inspector = inspect(db.engine)
    column_definitions = {
        'Lokaal_Artikel': {
            'kanban_min': 'INTEGER NULL',
            'kanban_refill_quantity': 'INTEGER NULL',
        },
        'Voorraad_Positie': {
            'materiaaltype': 'NVARCHAR(20) NULL',
            'kanban_min_override': 'INTEGER NULL',
            'kanban_refill_quantity_override': 'INTEGER NULL',
        },
        'Print_Queue': {
            'refill_quantity': 'INTEGER NULL',
        },
    }

    changed = False
    for table_name, columns in column_definitions.items():
        if not inspector.has_table(table_name):
            continue
        existing_columns = {
            column['name'] for column in inspector.get_columns(table_name)
        }
        for column_name, column_definition in columns.items():
            if column_name in existing_columns:
                continue
            db.session.execute(text(
                f"ALTER TABLE [{table_name}] ADD [{column_name}] {column_definition}"
            ))
            changed = True

    if changed:
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


def _article_kanban_standard(article):
    """Read the article standard, defaulting old rows to the new 1/1 default."""
    if article is None:
        return KanbanStandard.from_values()
    return KanbanStandard.from_values(
        getattr(article, 'kanban_min', None),
        getattr(article, 'kanban_refill_quantity', None),
    )


def _position_material_type(position):
    return normalize_material_type(getattr(position, 'materiaaltype', None))


def _normalized_position_overrides_for_standard(position, standard):
    min_override = getattr(position, 'kanban_min_override', None)
    refill_override = getattr(position, 'kanban_refill_quantity_override', None)

    return normalized_position_overrides(
        _position_material_type(position),
        standard,
        min_override,
        refill_override,
    )


def _position_overrides(position, article):
    return _normalized_position_overrides_for_standard(
        position,
        _article_kanban_standard(article),
    )


def effective_position_kanban_settings(position, article):
    """Return effective Kanban settings, or ``None`` for Standard material."""
    standard = _article_kanban_standard(article)
    min_override, refill_override = _position_overrides(position, article)
    return effective_kanban_settings(
        _position_material_type(position),
        standard,
        min_override,
        refill_override,
    )


def position_kanban_override_values(position, article):
    """Return only actual local deviations from the Article standard."""
    if _position_material_type(position) is Materiaaltype.STANDAARD:
        return None, None
    return _position_overrides(position, article)


def kanban_display_values(position, article):
    """Return the user-facing effective values and deviation flags."""
    material_type = _position_material_type(position)
    if material_type is Materiaaltype.STANDAARD:
        return {
            'materiaaltype': material_type.value,
            'min_level': None,
            'refill_quantity': None,
            'min_is_override': False,
            'refill_is_override': False,
        }

    standard = _article_kanban_standard(article)
    effective = effective_position_kanban_settings(position, article)
    min_override, refill_override = position_kanban_override_values(
        position,
        article,
    )
    return {
        'materiaaltype': material_type.value,
        'min_level': effective.min_level,
        'refill_quantity': effective.refill_quantity,
        'min_is_override': min_override is not None,
        'refill_is_override': refill_override is not None,
        'standard_min_level': standard.min_level,
        'standard_refill_quantity': standard.refill_quantity,
    }


def _set_article_kanban_standard(article, standard):
    setattr(article, 'kanban_min', standard.min_level)
    setattr(article, 'kanban_refill_quantity', standard.refill_quantity)


def _set_new_article_defaults(article):
    _set_article_kanban_standard(article, KanbanStandard.from_values())


def _position_form_values(form, standard):
    return normalized_position_overrides(
        Materiaaltype.KANBAN,
        standard,
        form.get('kanban_min_override'),
        form.get('kanban_refill_quantity_override'),
    )


def _set_position_kanban_values(position, article, form):
    material_type = normalize_material_type(form.get('materiaaltype'))
    standard = _article_kanban_standard(article)

    if material_type is Materiaaltype.STANDAARD:
        min_override, refill_override = None, None
    else:
        min_override, refill_override = _position_form_values(form, standard)
        effective_kanban_settings(
            material_type,
            standard,
            min_override,
            refill_override,
        )

    setattr(position, 'materiaaltype', material_type.value)
    setattr(
        position,
        'strategie',
        'STANDARD' if material_type is Materiaaltype.STANDAARD else 'TWO_BIN',
    )
    setattr(position, 'kanban_min_override', min_override)
    setattr(position, 'kanban_refill_quantity_override', refill_override)

app.jinja_env.globals['effective_position_kanban_settings'] = (
    effective_position_kanban_settings
)
app.jinja_env.globals['article_kanban_standard'] = _article_kanban_standard
app.jinja_env.globals['position_material_type'] = _position_material_type
app.jinja_env.globals['position_kanban_override_values'] = (
    position_kanban_override_values
)
app.jinja_env.globals['kanban_display_values'] = kanban_display_values


def build_locatiekaart_content(
    position,
    article,
    global_item,
    storage_location,
    room,
    room_type,
    company,
    branch,
):
    """Build the immutable snapshot used to identify one storage location."""
    effective = effective_position_kanban_settings(position, article)
    room_name = room.naam
    if getattr(room, 'nummer', None):
        room_name = f"{room.nummer} {room_name}"

    return LocatiekaartInhoud(
        artikelnaam=(
            getattr(article, 'eigen_naam', None)
            or getattr(global_item, 'generieke_naam', None)
            or ''
        ),
        artikel_foto_url=(
            getattr(article, 'foto_url', None)
            or getattr(global_item, 'foto_url', None)
        ),
        bedrijfslogo_url=getattr(company, 'logo_url', None),
        vestiging_naam=getattr(branch, 'naam', None) or '',
        ruimte_naam=room_name,
        opslaglocatie_naam=getattr(storage_location, 'naam', None) or '',
        kamertype_naam=getattr(room_type, 'naam', None),
        kamertype_kleur=getattr(room_type, 'kleur_hex', None),
        materiaaltype=_position_material_type(position),
        min_level=effective.min_level if effective else None,
        refill_quantity=effective.refill_quantity if effective else None,
    )


def create_or_reuse_locatiekaart_version(
    position,
    article,
    global_item,
    storage_location,
    room,
    room_type,
    company,
    branch,
):
    """Reuse the active version or create a pending version for new content."""
    content = build_locatiekaart_content(
        position,
        article,
        global_item,
        storage_location,
        room,
        room_type,
        company,
        branch,
    )
    active_statuses = [
        LocatiekaartStatus.PENDING_PRINT.value,
        LocatiekaartStatus.PRINTED.value,
    ]
    active_versions = db.session.query(LocatiekaartVersie).filter(
        LocatiekaartVersie.voorraad_positie_id
        == position.voorraad_positie_id,
        LocatiekaartVersie.status.in_(active_statuses),
    ).order_by(
        LocatiekaartVersie.locatiekaart_versie_id.desc(),
    ).all()
    if active_versions and active_versions[0].inhoud_hash == content.fingerprint:
        return active_versions[0], False

    _mark_locatiekaart_versions_superseded(active_versions)
    version = LocatiekaartVersie(
        bedrijf_id=company.bedrijf_id,
        voorraad_positie_id=position.voorraad_positie_id,
        lokaal_artikel_id=position.lokaal_artikel_id,
        inhoud_hash=content.fingerprint,
        artikelnaam=content.artikelnaam,
        artikel_foto_url=content.artikel_foto_url,
        bedrijfslogo_url=content.bedrijfslogo_url,
        vestiging_naam=content.vestiging_naam,
        ruimte_naam=content.ruimte_naam,
        opslaglocatie_naam=content.opslaglocatie_naam,
        kamertype_naam=content.kamertype_naam,
        kamertype_kleur=content.kamertype_kleur,
        materiaaltype=content.materiaaltype.value,
        min_level=content.min_level,
        status=LocatiekaartStatus.PENDING_PRINT.value,
        created_at=utcnow(),
    )
    db.session.add(version)
    return version, True


def mark_locatiekaart_version_printed(version):
    """Mark a version printed only after the local printservice accepts it."""
    if version.status != LocatiekaartStatus.PENDING_PRINT.value:
        return version.status == LocatiekaartStatus.PRINTED.value
    version.status = LocatiekaartStatus.PRINTED.value
    version.printed_at = utcnow()
    version.cancelled_at = None
    return True


def mark_locatiekaart_version_cancelled(version):
    """Cancel a pending version without changing the Kanban card lifecycle."""
    if version.status != LocatiekaartStatus.PENDING_PRINT.value:
        return False
    version.status = LocatiekaartStatus.CANCELLED.value
    version.printed_at = None
    version.cancelled_at = utcnow()
    return True


def _mark_locatiekaart_versions_superseded(versions):
    for version in versions:
        if getattr(version, 'status', None) in {
            LocatiekaartStatus.PENDING_PRINT.value,
            LocatiekaartStatus.PRINTED.value,
        }:
            version.status = LocatiekaartStatus.SUPERSEDED.value
            version.superseded_at = utcnow()


def _supersede_locatiekaart_versions_for_position_ids(position_ids):
    position_ids = {item for item in position_ids if item is not None}
    if not position_ids:
        return
    query_factory = getattr(db.session, 'query', None)
    if not query_factory:
        return
    versions = query_factory(LocatiekaartVersie).filter(
        LocatiekaartVersie.voorraad_positie_id.in_(position_ids),
        LocatiekaartVersie.status.in_([
            LocatiekaartStatus.PENDING_PRINT.value,
            LocatiekaartStatus.PRINTED.value,
        ]),
    ).all()
    _mark_locatiekaart_versions_superseded(
        version for version in versions
        if hasattr(version, 'inhoud_hash') and hasattr(version, 'status')
    )


def _position_ids_for_article(article_id):
    query_factory = getattr(db.session, 'query', None)
    if not query_factory or not Voorraad_Positie:
        return []
    rows = query_factory(Voorraad_Positie).filter(
        Voorraad_Positie.lokaal_artikel_id == article_id,
    ).all()
    return [
        position.voorraad_positie_id
        for position in rows
        if hasattr(position, 'voorraad_positie_id')
    ]


def _position_ids_for_global_item(global_id, fallback_attribute):
    query_factory = getattr(db.session, 'query', None)
    if not query_factory or not Voorraad_Positie or not Lokaal_Artikel:
        return []
    fallback_column = getattr(Lokaal_Artikel, fallback_attribute)
    rows = query_factory(Voorraad_Positie).join(
        Lokaal_Artikel,
        Voorraad_Positie.lokaal_artikel_id
        == Lokaal_Artikel.lokaal_artikel_id,
    ).filter(
        Lokaal_Artikel.global_id == global_id,
        or_(fallback_column.is_(None), fallback_column == ''),
    ).all()
    return [
        position.voorraad_positie_id
        for position in rows
        if hasattr(position, 'voorraad_positie_id')
    ]


def _position_ids_with_changed_article_standard(
    article_id,
    old_standard,
    new_standard,
):
    query_factory = getattr(db.session, 'query', None)
    if not query_factory or not Voorraad_Positie:
        return []
    rows = query_factory(Voorraad_Positie).filter(
        Voorraad_Positie.lokaal_artikel_id == article_id,
    ).all()
    changed_ids = []
    for position in rows:
        if not hasattr(position, 'voorraad_positie_id'):
            continue
        old_effective = _effective_settings_from_standard(
            position,
            old_standard,
        )
        new_effective = _effective_settings_from_standard(
            position,
            new_standard,
        )
        if old_effective != new_effective:
            changed_ids.append(position.voorraad_positie_id)
    return changed_ids


def _supersede_locatiekaart_versions_for_company(company_id):
    query_factory = getattr(db.session, 'query', None)
    if not query_factory:
        return
    versions = query_factory(LocatiekaartVersie).filter(
        LocatiekaartVersie.bedrijf_id == company_id,
        LocatiekaartVersie.status.in_([
            LocatiekaartStatus.PENDING_PRINT.value,
            LocatiekaartStatus.PRINTED.value,
        ]),
    ).all()
    _mark_locatiekaart_versions_superseded(
        version for version in versions
        if hasattr(version, 'inhoud_hash') and hasattr(version, 'status')
    )


def _position_ids_for_scope(scope, item_id):
    query_factory = getattr(db.session, 'query', None)
    if not query_factory or not Voorraad_Positie:
        return []

    query = query_factory(Voorraad_Positie)
    if scope == 'kast':
        query = query.filter(Voorraad_Positie.kast_id == item_id)
    elif scope == 'ruimte' and Kast:
        query = query.join(
            Kast,
            Voorraad_Positie.kast_id == Kast.kast_id,
        ).filter(Kast.ruimte_id == item_id)
    elif scope == 'vestiging' and Kast and Ruimte:
        query = query.join(
            Kast,
            Voorraad_Positie.kast_id == Kast.kast_id,
        ).join(
            Ruimte,
            Kast.ruimte_id == Ruimte.ruimte_id,
        ).filter(Ruimte.vestiging_id == item_id)
    elif scope == 'ruimte_type' and Kast and Ruimte:
        query = query.join(
            Kast,
            Voorraad_Positie.kast_id == Kast.kast_id,
        ).join(
            Ruimte,
            Kast.ruimte_id == Ruimte.ruimte_id,
        ).filter(Ruimte.ruimte_type_id == item_id)
    else:
        return []

    rows = query.all()
    return [
        position.voorraad_positie_id
        for position in rows
        if hasattr(position, 'voorraad_positie_id')
    ]


def _create_kanban_card(pos, art, kast, ruimte, bedrijf, effective=None):
    human_code = _generate_human_code()
    while db.session.query(KanbanKaart).filter(KanbanKaart.human_code == human_code).first():
        human_code = _generate_human_code()

    effective = effective or effective_position_kanban_settings(pos, art)
    if effective is None:
        raise KanbanSettingsError(
            'Standaard materiaal kan geen Kanban-kaart aanvragen.'
        )
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
    effective = effective_position_kanban_settings(pos, art)
    if effective is None:
        raise KanbanSettingsError(
            'Standaard materiaal kan geen Kanban-kaart aanvragen.'
        )
    card = _create_kanban_card(
        pos,
        art,
        kast,
        ruimte,
        bedrijf,
        effective=effective,
    )

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
        min_level=effective.min_level,
        refill_quantity=effective.refill_quantity,
        qr_code_value=_generate_public_scan_url(card.public_token),
        qr_human_readable=card.human_code,
        company_logo_url=bedrijf.logo_url
    )
    if hasattr(Print_Queue, 'kaart_id'):
        queue_kwargs['kaart_id'] = card.kaart_id
    if not hasattr(Print_Queue, 'refill_quantity'):
        queue_kwargs.pop('refill_quantity', None)

    return Print_Queue(**queue_kwargs)


def _effective_settings_from_standard(position, standard):
    """Resolve a position against an explicitly supplied Article standard."""
    min_override, refill_override = _normalized_position_overrides_for_standard(
        position,
        standard,
    )
    return effective_kanban_settings(
        _position_material_type(position),
        standard,
        min_override,
        refill_override,
    )


def _mark_cards_superseded(cards):
    for card in cards:
        if getattr(card, 'status', None) in {'PENDING_PRINT', 'PRINTED'}:
            card.status = 'SUPERSEDED'


def _supersede_cards_for_article(article_id, old_standard, new_standard):
    """Mark existing Kanban cards stale when their effective content changed."""
    query = getattr(db.session, 'query', None)
    if not query or not Voorraad_Positie:
        return

    rows = query(KanbanKaart, Voorraad_Positie).join(
        Voorraad_Positie,
        KanbanKaart.voorraad_positie_id
        == Voorraad_Positie.voorraad_positie_id,
    ).filter(
        Voorraad_Positie.lokaal_artikel_id == article_id,
    ).all()

    cards_to_supersede = []
    for card, position in rows:
        old_effective = _effective_settings_from_standard(
            position,
            old_standard,
        )
        new_effective = _effective_settings_from_standard(
            position,
            new_standard,
        )
        if old_effective != new_effective:
            cards_to_supersede.append(card)
    _mark_cards_superseded(cards_to_supersede)


def _supersede_cards_for_position(position, old_effective, new_effective):
    """Mark the position's existing Kanban cards stale after a local change."""
    if old_effective == new_effective:
        return

    query = getattr(db.session, 'query', None)
    if not query:
        return
    cards = query(KanbanKaart).filter(
        KanbanKaart.voorraad_positie_id == position.voorraad_positie_id,
    ).all()

    _mark_cards_superseded(cards)


def _get_queue_card(queue_item):
    kaart_id = getattr(queue_item, 'kaart_id', None)
    if not kaart_id:
        return None
    return db.session.query(KanbanKaart).filter(KanbanKaart.kaart_id == kaart_id).first()


def _mark_card_printed(queue_item, source_map=None):
    source = source_map.get(getattr(queue_item, 'kaart_id', None)) if source_map is not None else None
    card = source.card if source else _get_queue_card(queue_item)
    if not card or getattr(card, 'status', None) == 'SUPERSEDED':
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


def _queue_item_sources(queue_items):
    """Load all linked queue sources in one query for the queue page."""
    kaart_ids = {
        getattr(queue_item, 'kaart_id', None)
        for queue_item in queue_items
    }
    kaart_ids.discard(None)
    if not kaart_ids:
        return {}

    rows = _queue_item_source_query().filter(
        KanbanKaart.kaart_id.in_(kaart_ids),
    ).all()
    sources = [QueueItemSource(*row) for row in rows]
    return {
        row.card.kaart_id: row
        for row in sources
    }


def _queue_item_source_query():
    return db.session.query(
        KanbanKaart,
        Voorraad_Positie,
        Lokaal_Artikel,
    ).outerjoin(
        Voorraad_Positie,
        KanbanKaart.voorraad_positie_id
        == Voorraad_Positie.voorraad_positie_id,
    ).outerjoin(
        Lokaal_Artikel,
        Voorraad_Positie.lokaal_artikel_id
        == Lokaal_Artikel.lokaal_artikel_id,
    )


def _queue_item_source(queue_item, source_map=None):
    kaart_id = getattr(queue_item, 'kaart_id', None)
    if source_map is not None:
        source = source_map.get(kaart_id)
        return (source.position, source.article) if source else (None, None)

    query = getattr(db.session, 'query', None)
    if kaart_id and query and Voorraad_Positie and Lokaal_Artikel:
        row = _queue_item_source_query().filter(
            KanbanKaart.kaart_id == kaart_id,
        ).first()
        if row:
            source = QueueItemSource(*row)
            if source.position and source.article:
                return source.position, source.article

    return None, None


def _queue_item_effective_settings(queue_item, source_map=None):
    """Resolve current effective settings from the linked card or queue row."""
    position, article = _queue_item_source(queue_item, source_map)
    if position and article:
        return effective_position_kanban_settings(position, article)

    min_level = getattr(queue_item, 'min_level', None)
    refill_quantity = getattr(queue_item, 'refill_quantity', None)

    try:
        return KanbanStandard.from_values(min_level, refill_quantity)
    except KanbanSettingsError:
        return None


def _queue_item_display_values(queue_item, source_map=None):
    position, article = _queue_item_source(queue_item, source_map)
    if position and article:
        return kanban_display_values(position, article)

    effective = _queue_item_effective_settings(queue_item, source_map)
    if not effective:
        return {
            'materiaaltype': Materiaaltype.KANBAN.value,
            'min_level': None,
            'refill_quantity': None,
            'min_is_override': False,
            'refill_is_override': False,
        }
    return {
        'materiaaltype': Materiaaltype.KANBAN.value,
        'min_level': effective.min_level,
        'refill_quantity': effective.refill_quantity,
        'min_is_override': False,
        'refill_is_override': False,
        'standard_min_level': effective.min_level,
        'standard_refill_quantity': effective.refill_quantity,
    }


def _queue_item_is_superseded(queue_item, source_map=None):
    source = source_map.get(getattr(queue_item, 'kaart_id', None)) if source_map is not None else None
    card = source.card if source else _get_queue_card(queue_item)
    return bool(card and getattr(card, 'status', None) == 'SUPERSEDED')


def _build_print_payload(queue_item, source_map=None):
    if _queue_item_is_superseded(queue_item, source_map):
        return None, "Deze Kanban-kaart is verouderd; vraag een nieuwe kaart aan."

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

    effective = _queue_item_effective_settings(queue_item, source_map)
    if effective is None:
        return None, "Standaard materiaal kan niet als Kanban-kaart worden geprint."

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
                "minLevel": effective.min_level,
                "refillQuantity": effective.refill_quantity,
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
        headers, header_err = _print_service_headers()
        if header_err:
            return False, header_err

        health_url = _resolve_print_service_api_url('/health') or _print_service_root_url()
        resp = requests.get(
            health_url,
            headers=headers,
            timeout=PRINT_REQUEST_TIMEOUT,
            allow_redirects=False
        )
        if 300 <= resp.status_code < 400:
            location = resp.headers.get('Location', '(onbekend)')
            return False, (
                f"Service health-check redirect ({resp.status_code}) naar {location}. "
                "Controleer PRINT_SERVICE_URL."
            )
        if resp.status_code >= 400:
            return False, f"Service bereikbaar, maar health-check gaf HTTP {resp.status_code}."
    except requests.RequestException as exc:
        return False, f"Poort open, maar service health-check faalde ({exc})."

    return True, f"Verbonden met printservice op {parsed.hostname}:{port}."

def send_queue_item_to_print_service(queue_item, source_map=None):
    if not PRINT_SERVICE_URL:
        return False, "PRINT_SERVICE_URL ontbreekt."

    payload, payload_error = _build_print_payload(queue_item, source_map)
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
            timeout=PRINT_REQUEST_TIMEOUT,
            allow_redirects=False
        )
        if 300 <= response.status_code < 400:
            location = response.headers.get('Location', '(onbekend)')
            return False, (
                f"Printservice redirect ({response.status_code}) naar {location}. "
                "Controleer PRINT_SERVICE_URL."
            )

        response.raise_for_status()

        try:
            response_body = response.json()
        except ValueError:
            response_body = None

        if isinstance(response_body, dict):
            if response_body.get('detail'):
                return False, f"Printservice melding: {response_body.get('detail')}"

            status = str(response_body.get('status') or '').upper()
            if status and status not in {'QUEUED', 'ACCEPTED', 'PRINTING', 'COMPLETED'}:
                return False, f"Printservice gaf onverwachte status: {status}."

            job_id = response_body.get('jobId')
            if status or job_id:
                app.logger.info(
                    "Printservice accepted job",
                    extra={
                        "print_job_id": job_id,
                        "print_job_status": status,
                        "printer_id": payload.get('printerId')
                    }
                )

        return True, None
    except requests.RequestException as exc:
        return False, f"Printservice fout: {exc}"


def build_location_cards_payload(versions, print_batch_id=None):
    """Build the stable, printer-independent A4 Locatiekaart contract."""
    versions = list(versions or [])
    if not versions:
        raise ValueError("Een Locatiekaartbatch moet minimaal één kaart bevatten.")

    if print_batch_id is None:
        print_batch_id = str(uuid.uuid4())
    else:
        print_batch_id = str(print_batch_id).strip()
        if not print_batch_id:
            raise ValueError("printBatchId mag niet leeg zijn.")

    cards = []
    for version in versions:
        status = getattr(version, 'status', LocatiekaartStatus.PENDING_PRINT.value)
        if status in {
            LocatiekaartStatus.CANCELLED.value,
            LocatiekaartStatus.SUPERSEDED.value,
        }:
            raise ValueError(
                f"Locatiekaartversie {getattr(version, 'locatiekaart_versie_id', '?')} "
                "is niet meer printbaar."
            )

        article_name = (getattr(version, 'artikelnaam', None) or '').strip()
        if not article_name:
            raise ValueError("Artikelnaam ontbreekt voor een Locatiekaart.")

        product_image, product_image_error = _image_to_base64_object(
            getattr(version, 'artikel_foto_url', None),
            "Artikel-foto",
        )
        if product_image_error:
            raise ValueError(product_image_error)

        company_logo, company_logo_error = _image_to_base64_object(
            getattr(version, 'bedrijfslogo_url', None),
            "Bedrijfslogo",
        )
        if company_logo_error:
            raise ValueError(company_logo_error)

        material_type = normalize_material_type(
            getattr(version, 'materiaaltype', None),
        )
        card = {
            "cardId": str(getattr(version, 'locatiekaart_versie_id', '') or ''),
            "product": {
                "name": article_name,
                "image": product_image,
            },
            "company": {
                "logo": company_logo,
            },
            "materialType": material_type.value,
            "location": {
                "branchName": getattr(version, 'vestiging_naam', None) or '',
                "roomName": getattr(version, 'ruimte_naam', None) or '',
                "storageLocationName": (
                    getattr(version, 'opslaglocatie_naam', None) or ''
                ),
            },
            "roomType": {
                "name": getattr(version, 'kamertype_naam', None),
                "color": getattr(version, 'kamertype_kleur', None),
            },
        }
        if material_type is Materiaaltype.KANBAN and getattr(
            version,
            'min_level',
            None,
        ) is not None:
            card["logistics"] = {
                "minLevel": version.min_level,
            }
        cards.append(card)

    return {
        "printBatchId": print_batch_id,
        "printerId": LOCATION_CARDS_PRINTER_ID,
        "cardType": LOCATION_CARDS_CARD_TYPE,
        "cards": cards,
        "options": {
            "paper": "A4",
            "orientation": "portrait",
            "cardSize": {
                "widthMm": 90,
                "heightMm": 60,
            },
            "cardsPerSheet": 8,
            "color": True,
            "duplex": True,
            "duplexFlip": "long-edge",
        },
    }


def _location_card_response_metadata(response_body, payload):
    if not isinstance(response_body, dict):
        raise ValueError("Printservice gaf geen JSON-object terug.")
    if response_body.get('detail'):
        raise ValueError(f"Printservice melding: {response_body['detail']}")

    expected_batch_id = payload['printBatchId']
    if response_body.get('printBatchId') != expected_batch_id:
        raise ValueError("Printservice gaf een afwijkende printBatchId terug.")

    job_id = response_body.get('jobId')
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("Printservice response mist jobId.")

    status = str(response_body.get('status') or '').upper()
    if status not in {
        'QUEUED',
        'ACCEPTED',
        'PRINTING',
        'COMPLETED',
        'ALREADY_QUEUED',
        'ALREADY_ACCEPTED',
    }:
        raise ValueError(f"Printservice gaf onverwachte status: {status or '?'}.")

    card_count = response_body.get('cardCount')
    expected_card_count = len(payload['cards'])
    if isinstance(card_count, bool) or card_count != expected_card_count:
        raise ValueError("Printservice gaf een afwijkend cardCount terug.")

    sheet_count = response_body.get('sheetCount')
    expected_sheet_count = (expected_card_count + 7) // 8
    if isinstance(sheet_count, bool) or sheet_count != expected_sheet_count:
        raise ValueError("Printservice gaf een afwijkend sheetCount terug.")

    return {
        'printBatchId': expected_batch_id,
        'jobId': job_id,
        'status': status,
        'cardCount': card_count,
        'sheetCount': sheet_count,
    }


def send_location_cards_to_print_service(versions, print_batch_id=None):
    """Send one A4 Locatiekaartbatch and return success, error, metadata."""
    try:
        payload = build_location_cards_payload(versions, print_batch_id)
    except (KanbanSettingsError, ValueError) as exc:
        return False, str(exc), None

    if not PRINT_SERVICE_URL:
        return False, "PRINT_SERVICE_URL ontbreekt.", None

    headers, header_err = _print_service_headers()
    if header_err:
        return False, header_err, None

    endpoint_url = _resolve_print_service_api_url(
        LOCATION_CARDS_PRINT_ENDPOINT,
    )
    if not endpoint_url:
        return False, "PRINT_SERVICE_URL ontbreekt of is ongeldig.", None

    try:
        response = requests.post(
            endpoint_url,
            json=payload,
            headers=headers,
            timeout=PRINT_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            location = response.headers.get('Location', '(onbekend)')
            return False, (
                f"Printservice redirect ({response.status_code}) naar {location}. "
                "Controleer PRINT_SERVICE_URL."
            ), None

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            # FastAPI error responses carry {"detail": "..."} — prefer that
            # over the generic HTTPError text so a conflict or validation
            # failure is actually actionable for the operator.
            detail = None
            try:
                error_body = response.json()
                if isinstance(error_body, dict):
                    detail = error_body.get('detail')
            except (ValueError, AttributeError):
                detail = None
            return False, detail or f"Printservice fout: {exc}", None

        response_body = response.json()
        metadata = _location_card_response_metadata(response_body, payload)
        return True, None, metadata
    except requests.RequestException as exc:
        return False, f"Printservice fout: {exc}", None
    except (TypeError, ValueError) as exc:
        return False, str(exc), None

# --- ROUTES ---

@app.route('/')
def dashboard():
    if not check_db():
        return render_template(
            'dashboard.html',
            print_queue_count=0,
            open_scan_count=0
        )
    
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


@app.route('/assistent/kasten')
def kast_selectie():
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    kasten = db.session.query(Kast, Ruimte, Vestiging).join(
        Ruimte,
        Kast.ruimte_id == Ruimte.ruimte_id,
    ).join(
        Vestiging,
        Ruimte.vestiging_id == Vestiging.vestiging_id,
    ).filter(
        Kast.bedrijf_id == bedrijf_id,
    ).order_by(
        Vestiging.naam,
        Ruimte.nummer,
        Ruimte.naam,
        Kast.naam,
    ).all()
    return render_template('kast_selectie.html', kasten=kasten)


def _kast_inventory_query(kast_id, bedrijf_id):
    if not all((
        Voorraad_Positie,
        Lokaal_Artikel,
        Global_Catalogus,
        Kast,
        Ruimte,
        Ruimte_Type,
        Bedrijf,
    )):
        return db.session.query(
            Voorraad_Positie,
            Lokaal_Artikel,
            Global_Catalogus,
        ).join(
            Lokaal_Artikel,
            Voorraad_Positie.lokaal_artikel_id
            == Lokaal_Artikel.lokaal_artikel_id,
        ).outerjoin(
            Global_Catalogus,
            Lokaal_Artikel.global_id == Global_Catalogus.global_id,
        ).filter(
            Voorraad_Positie.kast_id == kast_id,
            Voorraad_Positie.bedrijf_id == bedrijf_id,
        )
    return db.session.query(
        Voorraad_Positie,
        Lokaal_Artikel,
        Global_Catalogus,
        Kast,
        Ruimte,
        Ruimte_Type,
        Bedrijf,
    ).join(
        Lokaal_Artikel,
        Voorraad_Positie.lokaal_artikel_id
        == Lokaal_Artikel.lokaal_artikel_id,
    ).outerjoin(
        Global_Catalogus,
        Lokaal_Artikel.global_id == Global_Catalogus.global_id,
    ).join(
        Kast,
        Voorraad_Positie.kast_id == Kast.kast_id,
    ).join(
        Ruimte,
        Kast.ruimte_id == Ruimte.ruimte_id,
    ).outerjoin(
        Ruimte_Type,
        Ruimte.ruimte_type_id == Ruimte_Type.ruimte_type_id,
    ).join(
        Bedrijf,
        Voorraad_Positie.bedrijf_id == Bedrijf.bedrijf_id,
    ).filter(
        Voorraad_Positie.kast_id == kast_id,
        Voorraad_Positie.bedrijf_id == bedrijf_id,
    )


def _ruimte_inventory_query(ruimte_id, bedrijf_id):
    return db.session.query(
        Voorraad_Positie,
        Lokaal_Artikel,
        Global_Catalogus,
        Kast,
        Ruimte,
        Ruimte_Type,
        Bedrijf,
    ).join(
        Lokaal_Artikel,
        Voorraad_Positie.lokaal_artikel_id
        == Lokaal_Artikel.lokaal_artikel_id,
    ).outerjoin(
        Global_Catalogus,
        Lokaal_Artikel.global_id == Global_Catalogus.global_id,
    ).join(
        Kast,
        Voorraad_Positie.kast_id == Kast.kast_id,
    ).join(
        Ruimte,
        Kast.ruimte_id == Ruimte.ruimte_id,
    ).outerjoin(
        Ruimte_Type,
        Ruimte.ruimte_type_id == Ruimte_Type.ruimte_type_id,
    ).join(
        Bedrijf,
        Voorraad_Positie.bedrijf_id == Bedrijf.bedrijf_id,
    ).filter(
        Kast.ruimte_id == ruimte_id,
        Voorraad_Positie.bedrijf_id == bedrijf_id,
    )


def _storage_location_print_item(row, kaart_type):
    position, article, global_item, kast, room, room_type, company = row
    material_type = _position_material_type(position)
    effective = effective_position_kanban_settings(position, article)
    article_name = (
        getattr(article, 'eigen_naam', None)
        or getattr(global_item, 'generieke_naam', None)
        or ''
    )
    if kaart_type == 'kanban':
        product_photo_url = (
            getattr(article, 'foto_url', None)
            or getattr(global_item, 'foto_url', None)
        )
        applicable = material_type is Materiaaltype.KANBAN
    else:
        product_photo_url = (
            getattr(article, 'foto_url', None)
            or getattr(global_item, 'foto_url', None)
        )
        applicable = True

    reasons = []
    if not product_photo_url:
        reasons.append('Artikel-foto ontbreekt.')
    if not getattr(company, 'logo_url', None):
        reasons.append('Bedrijfslogo ontbreekt.')

    return {
        'position_id': position.voorraad_positie_id,
        'display_name': article_name or 'Naamloos Artikel',
        'storage_location_name': getattr(kast, 'naam', '') or 'Onbekende Opslaglocatie',
        'product_photo_url': product_photo_url,
        'material_type': material_type.value,
        'min_level': effective.min_level if effective else None,
        'refill_quantity': effective.refill_quantity if effective else None,
        'valid': applicable and not reasons,
        'applicable': applicable,
        'reasons': reasons,
    }


def _kast_print_selection_items(rows, kaart_type):
    items = [
        _storage_location_print_item(row, kaart_type)
        for row in rows
    ]
    items = [item for item in items if item['applicable']]
    return sorted(
        items,
        key=lambda item: (
            item['display_name'].casefold(),
            item['position_id'],
        ),
    )


def _ruimte_print_selection_items(rows, kaart_type):
    items = [
        _storage_location_print_item(row, kaart_type)
        for row in rows
    ]
    items = [item for item in items if item['applicable']]
    return sorted(
        items,
        key=lambda item: (
            item['storage_location_name'].casefold(),
            item['display_name'].casefold(),
            item['position_id'],
        ),
    )


def _print_selection_label(kaart_type, singular=False):
    if kaart_type == 'kanban':
        return 'Kanban-kaartje' if singular else 'Kanban-kaartjes'
    return 'Locatiekaartje' if singular else 'Locatiekaartjes'


def _resolve_locatie_print_batch_id(selected_ids):
    """Reuse the submitted printBatchId only when retrying the same selection.

    A changed selection always gets a fresh batch id, so a stale retry can't
    be silently deduped by the print service against different content.
    """
    current_selection = ','.join(str(position_id) for position_id in sorted(selected_ids))
    submitted_batch_id = request.form.get('printBatchId', '').strip()
    submitted_selection = request.form.get('printBatchSelection', '')
    if submitted_batch_id and submitted_selection == current_selection:
        return submitted_batch_id, current_selection
    return str(uuid.uuid4()), current_selection


def _send_locatiekaart_batch(location_versions, print_batch_id):
    """Send a Locatiekaart batch to the A4 print service and mark accepted
    versions PRINTED.

    Cards whose photo or logo can't be resolved are excluded per-article
    instead of failing the whole batch. Returns (sent, error, metadata,
    skipped), where skipped is a list of (artikelnaam, reason) tuples.
    """
    printable_versions = []
    skipped = []
    for version in location_versions:
        label = getattr(version, 'artikelnaam', None) or 'Onbekend Artikel'
        _, product_image_error = _image_to_base64_object(
            getattr(version, 'artikel_foto_url', None),
            "Artikel-foto",
        )
        if product_image_error:
            skipped.append((label, product_image_error))
            continue
        _, company_logo_error = _image_to_base64_object(
            getattr(version, 'bedrijfslogo_url', None),
            "Bedrijfslogo",
        )
        if company_logo_error:
            skipped.append((label, company_logo_error))
            continue
        printable_versions.append(version)

    if not printable_versions:
        reasons = '; '.join(f'{name} ({reason})' for name, reason in skipped)
        return False, f'Geen enkele kaart is printbaar. {reasons}', None, skipped

    sent, error, metadata = send_location_cards_to_print_service(
        printable_versions,
        print_batch_id,
    )
    if not sent:
        return False, error, None, skipped

    for version in printable_versions:
        mark_locatiekaart_version_printed(version)
    db.session.commit()
    return True, None, metadata, skipped


@app.route('/assistent/kast/<int:kast_id>')
def assistent_kast_inhoud(kast_id):
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    kast = get_scoped_item(Kast, kast_id, bedrijf_id)
    if not kast:
        flash('Kast niet gevonden of geen toegang.', 'warning')
        return redirect(url_for('kast_selectie'))

    inhoud = [
        row[:3]
        for row in _kast_inventory_query(kast_id, bedrijf_id).order_by(
            Lokaal_Artikel.eigen_naam,
        ).all()
    ]
    artikelen = db.session.query(Lokaal_Artikel).filter_by(
        bedrijf_id=bedrijf_id,
    ).order_by(
        Lokaal_Artikel.eigen_naam,
    ).all()
    return render_template(
        'kast_inhoud.html',
        kast=kast,
        inhoud=inhoud,
        artikelen=artikelen,
        kanban_count=sum(
            _position_material_type(row[0]) is Materiaaltype.KANBAN
            for row in inhoud
        ),
        locatiekaart_count=len(inhoud),
    )


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
    return render_template(
        'assistent_kamer_view.html',
        ruimte=ruimte,
        kasten_data=kasten_data,
        alle_artikelen=alle_artikelen,
        kanban_count=sum(
            _position_material_type(row[0]) is Materiaaltype.KANBAN
            for inhoud in kasten_data.values()
            for row in inhoud
        ),
        locatiekaart_count=sum(
            len(inhoud)
            for inhoud in kasten_data.values()
        ),
    )

@app.route('/assistent/update-voorraad/<int:voorraad_positie_id>', methods=['POST'])
def update_voorraad_positie(voorraad_positie_id):
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    positie = get_scoped_item(Voorraad_Positie, voorraad_positie_id, bedrijf_id)
    if not positie:
        flash('Voorraadpositie niet gevonden of geen toegang.', 'warning')
        return redirect(url_for('assistent_kamers'))

    kast = get_scoped_item(Kast, positie.kast_id, bedrijf_id)
    if not kast:
        return redirect(url_for('assistent_kamers'))

    try:
        artikel = get_scoped_item(Lokaal_Artikel, positie.lokaal_artikel_id, bedrijf_id)
        old_effective = effective_position_kanban_settings(positie, artikel)
        _set_position_kanban_values(positie, artikel, request.form)
        new_effective = effective_position_kanban_settings(positie, artikel)
        _supersede_cards_for_position(positie, old_effective, new_effective)
        if old_effective != new_effective:
            _supersede_locatiekaart_versions_for_position_ids(
                [positie.voorraad_positie_id]
            )
    except KanbanSettingsError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('assistent_kamer_view', ruimte_id=kast.ruimte_id))

    db.session.commit()
    flash('Voorraadinstellingen bijgewerkt.', 'success')
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
            strategie='TWO_BIN',
            materiaaltype=Materiaaltype.KANBAN.value,
            kanban_min_override=None,
            kanban_refill_quantity_override=None,
        )
        db.session.add(nieuw)
        db.session.flush()
        nieuw.qr_code = f"{API_BASE_URL}/{nieuw.voorraad_positie_id}"
        db.session.commit()
        flash('Artikel toegevoegd.', 'success')
    else:
        flash('Artikel zit al in de kast.', 'warning')
    return redirect(url_for('assistent_kamer_view', ruimte_id=kast.ruimte_id))


@app.route('/assistent/kast/<int:kast_id>/print/<kaart_type>', methods=['GET', 'POST'])
def kast_print_selectie(kast_id, kaart_type):
    if not check_db():
        return redirect(url_for('dashboard'))
    if kaart_type not in {'kanban', 'locatie'}:
        flash('Onbekend kaarttype.', 'warning')
        return redirect(url_for('kast_selectie'))
    kaart_type_label = (
        'Kanban-kaartjes'
        if kaart_type == 'kanban'
        else 'Locatiekaartjes'
    )

    bedrijf_id = get_huidig_bedrijf_id()
    kast = get_scoped_item(Kast, kast_id, bedrijf_id)
    if not kast:
        flash('Opslaglocatie niet gevonden of geen toegang.', 'warning')
        return redirect(url_for('kast_selectie'))

    rows = _kast_inventory_query(kast_id, bedrijf_id).all()
    selection_items = _kast_print_selection_items(rows, kaart_type)
    rows_by_position_id = {
        row[0].voorraad_positie_id: row
        for row in rows
    }
    applicable_items = [item for item in selection_items if item['applicable']]
    valid_items = [item for item in applicable_items if item['valid']]
    selected_ids = None
    print_batch_id = ''
    print_batch_selection = ''

    if request.method == 'POST':
        selected_ids = {
            int(value)
            for value in request.form.getlist('position_ids')
            if value.isdigit()
        }
        selected_items = [
            item for item in valid_items
            if item['position_id'] in selected_ids
        ]
        if kaart_type == 'locatie':
            print_batch_id, print_batch_selection = _resolve_locatie_print_batch_id(
                selected_ids,
            )
        if not selected_items:
            flash('Selecteer minimaal één geldig Artikel om te printen.', 'warning')
            return render_template(
                'kast_print_selectie.html',
                kast=kast,
                kaart_type=kaart_type,
                kaart_type_label=kaart_type_label,
                selection_items=selection_items,
                applicable_count=len(applicable_items),
                valid_count=len(valid_items),
                selected_ids=set(),
                print_batch_id=print_batch_id,
                print_batch_selection=print_batch_selection,
            )

        try:
            if kaart_type == 'kanban':
                for item in selected_items:
                    row = rows_by_position_id[item['position_id']]
                    db.session.add(create_queue_item(*row))
                db.session.commit()
                flash(
                    f'{len(selected_items)} Kanban-kaartje'
                    f'{"s" if len(selected_items) != 1 else ""} aangevraagd.',
                    'success',
                )
                return redirect(url_for('assistent_kast_inhoud', kast_id=kast_id))

            location_versions = []
            for item in selected_items:
                row = rows_by_position_id[item['position_id']]
                version, _ = create_or_reuse_locatiekaart_version(*row)
                location_versions.append(version)

            # Persist pending versions before calling the external service so a
            # failed request can be retried without losing the snapshot.
            db.session.commit()
            sent, error, metadata, skipped = _send_locatiekaart_batch(
                location_versions,
                print_batch_id,
            )
            if not sent:
                flash(f'A4-printaanvraag mislukt: {error}', 'danger')
                return render_template(
                    'kast_print_selectie.html',
                    kast=kast,
                    kaart_type=kaart_type,
                    kaart_type_label=kaart_type_label,
                    selection_items=selection_items,
                    applicable_count=len(applicable_items),
                    valid_count=len(valid_items),
                    selected_ids=selected_ids,
                    print_batch_id=print_batch_id,
                    print_batch_selection=print_batch_selection,
                )

            if skipped:
                flash(
                    f'{len(skipped)} kaartje(s) overgeslagen: '
                    + '; '.join(f'{name} ({reason})' for name, reason in skipped),
                    'warning',
                )
            flash(
                f'A4-printbatch {metadata["printBatchId"]} geaccepteerd: '
                f'{metadata["cardCount"]} kaartje(s), '
                f'{metadata["sheetCount"]} vel(len), job {metadata["jobId"]}.',
                'success',
            )
            return redirect(url_for('assistent_kast_inhoud', kast_id=kast_id))
        except Exception as exc:
            db.session.rollback()
            flash(f'Fout bij printaanvraag: {exc}', 'danger')

    if selected_ids is None:
        selected_ids = {
            item['position_id']
            for item in valid_items
        }
    return render_template(
        'kast_print_selectie.html',
        kast=kast,
        kaart_type=kaart_type,
        kaart_type_label=kaart_type_label,
        selection_items=selection_items,
        applicable_count=len(applicable_items),
        valid_count=len(valid_items),
        selected_ids=selected_ids,
        print_batch_id=print_batch_id,
        print_batch_selection=print_batch_selection,
    )


@app.route('/assistent/kamer/<int:ruimte_id>/print/<kaart_type>', methods=['GET', 'POST'])
def ruimte_print_selectie(ruimte_id, kaart_type):
    if not check_db():
        return redirect(url_for('dashboard'))
    if kaart_type not in {'kanban', 'locatie'}:
        flash('Onbekend kaarttype.', 'warning')
        return redirect(url_for('assistent_kamers'))

    bedrijf_id = get_huidig_bedrijf_id()
    ruimte = get_scoped_item(Ruimte, ruimte_id, bedrijf_id)
    if not ruimte:
        flash('Ruimte niet gevonden of geen toegang.', 'warning')
        return redirect(url_for('assistent_kamers'))

    rows = _ruimte_inventory_query(ruimte_id, bedrijf_id).all()
    selection_items = _ruimte_print_selection_items(rows, kaart_type)
    rows_by_position_id = {
        row[0].voorraad_positie_id: row
        for row in rows
    }
    applicable_items = [item for item in selection_items if item['applicable']]
    valid_items = [item for item in applicable_items if item['valid']]
    selected_ids = None
    print_batch_id = ''
    print_batch_selection = ''

    if request.method == 'POST':
        selected_ids = {
            int(value)
            for value in request.form.getlist('position_ids')
            if value.isdigit()
        }
        selected_items = [
            item for item in valid_items
            if item['position_id'] in selected_ids
        ]
        if kaart_type == 'locatie':
            print_batch_id, print_batch_selection = _resolve_locatie_print_batch_id(
                selected_ids,
            )
        if not selected_items:
            flash('Selecteer minimaal één geldig Artikel om te printen.', 'warning')
            return render_template(
                'kamer_print_selectie.html',
                ruimte=ruimte,
                kaart_type=kaart_type,
                kaart_type_label=_print_selection_label(kaart_type),
                selection_items=selection_items,
                applicable_count=len(applicable_items),
                valid_count=len(valid_items),
                selected_ids=set(),
                print_batch_id=print_batch_id,
                print_batch_selection=print_batch_selection,
            )

        try:
            if kaart_type == 'kanban':
                for item in selected_items:
                    row = rows_by_position_id[item['position_id']]
                    db.session.add(create_queue_item(*row))
                db.session.commit()
                kaartje_label = _print_selection_label(kaart_type, singular=True)
                kaartje_meervoud = 's' if len(selected_items) != 1 else ''
                flash(
                    f'{len(selected_items)} {kaartje_label}{kaartje_meervoud} aangevraagd.',
                    'success',
                )
                return redirect(url_for('assistent_kamer_view', ruimte_id=ruimte_id))

            location_versions = []
            for item in selected_items:
                row = rows_by_position_id[item['position_id']]
                version, _ = create_or_reuse_locatiekaart_version(*row)
                location_versions.append(version)

            # Persist pending versions before calling the external service so a
            # failed request can be retried without losing the snapshot.
            db.session.commit()
            sent, error, metadata, skipped = _send_locatiekaart_batch(
                location_versions,
                print_batch_id,
            )
            if not sent:
                flash(f'A4-printaanvraag mislukt: {error}', 'danger')
                return render_template(
                    'kamer_print_selectie.html',
                    ruimte=ruimte,
                    kaart_type=kaart_type,
                    kaart_type_label=_print_selection_label(kaart_type),
                    selection_items=selection_items,
                    applicable_count=len(applicable_items),
                    valid_count=len(valid_items),
                    selected_ids=selected_ids,
                    print_batch_id=print_batch_id,
                    print_batch_selection=print_batch_selection,
                )

            if skipped:
                flash(
                    f'{len(skipped)} kaartje(s) overgeslagen: '
                    + '; '.join(f'{name} ({reason})' for name, reason in skipped),
                    'warning',
                )
            flash(
                f'A4-printbatch {metadata["printBatchId"]} geaccepteerd: '
                f'{metadata["cardCount"]} kaartje(s), '
                f'{metadata["sheetCount"]} vel(len), job {metadata["jobId"]}.',
                'success',
            )
            return redirect(url_for('assistent_kamer_view', ruimte_id=ruimte_id))
        except Exception as exc:
            db.session.rollback()
            flash(f'Fout bij printaanvraag: {exc}', 'danger')

    if selected_ids is None:
        selected_ids = {
            item['position_id']
            for item in valid_items
        }
    return render_template(
        'kamer_print_selectie.html',
        ruimte=ruimte,
        kaart_type=kaart_type,
        kaart_type_label=_print_selection_label(kaart_type),
        selection_items=selection_items,
        applicable_count=len(applicable_items),
        valid_count=len(valid_items),
        selected_ids=selected_ids,
        print_batch_id=print_batch_id,
        print_batch_selection=print_batch_selection,
    )


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
            return redirect(request.referrer or url_for('assistent_kamers'))

        if _position_material_type(result[0]) is Materiaaltype.STANDAARD:
            flash(
                "Standaard materiaal heeft geen Kanban-kaart.",
                "info",
            )
            return redirect(request.referrer or url_for('assistent_kamers'))

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
    return redirect(
        url_for('kast_print_selectie', kast_id=kast_id, kaart_type='kanban')
    )

@app.route('/assistent/print-queue')
def assistent_print_queue():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    preview_layout_warning = None
    preview_layout_error = None
    
    queue_items = db.session.query(Print_Queue)\
        .filter(Print_Queue.bedrijf_id == bedrijf_id, Print_Queue.status == 'PENDING')\
        .order_by(Print_Queue.aangemaakt_op.desc()).all()
    queue_sources = _queue_item_sources(queue_items)
    queue_rows = [
        {
            'item': item,
            'display_values': _queue_item_display_values(item, queue_sources),
            'is_superseded': _queue_item_is_superseded(item, queue_sources),
        }
        for item in queue_items
    ]

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
        queue_rows=queue_rows,
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
        KanbanScanlijstItem.reset_at.is_(None),
        KanbanKaart.status != 'SUPERSEDED',
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

    queue_sources = _queue_item_sources(items)
    success_count = 0
    fail_count = 0
    fail_messages = []

    for item in items:
        sent, error_msg = send_queue_item_to_print_service(item, queue_sources)
        if sent:
            _mark_card_printed(item, queue_sources)
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
            _set_new_article_defaults(nieuw)
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
                _set_new_article_defaults(nieuw)
                db.session.add(nieuw)
                db.session.commit()
                flash('Gekoppeld.', 'success')
        elif actie == 'bewerk_artikel':
            artikel_id = request.form.get('artikel_id', type=int)
            artikel = get_scoped_item(Lokaal_Artikel, artikel_id, bedrijf_id)
            if artikel:
                try:
                    old_standard = _article_kanban_standard(artikel)
                    standard = KanbanStandard.from_values(
                        request.form.get('kanban_min'),
                        request.form.get('kanban_refill_quantity'),
                    )
                except KanbanSettingsError as exc:
                    flash(str(exc), 'danger')
                    return redirect(url_for('artikelen_beheer'))

                old_name = getattr(artikel, 'eigen_naam', None)
                old_photo = getattr(artikel, 'foto_url', None)
                position_ids = _position_ids_for_article(artikel_id)
                standard_change_ids = _position_ids_with_changed_article_standard(
                    artikel_id,
                    old_standard,
                    standard,
                )
                new_name = request.form.get('naam')
                new_photo = old_photo
                file = request.files.get('afbeelding')
                if file:
                    url = upload_image_to_azure(file)
                    if url and "ERROR" not in url:
                        new_photo = url

                artikel.eigen_naam = new_name
                artikel.verpakkingseenheid_tekst = request.form.get('eenheid')
                _supersede_cards_for_article(
                    artikel_id,
                    old_standard,
                    standard,
                )
                _set_article_kanban_standard(artikel, standard)
                artikel.foto_url = new_photo
                if old_name != new_name or old_photo != new_photo:
                    location_card_position_ids = position_ids
                else:
                    location_card_position_ids = standard_change_ids
                _supersede_locatiekaart_versions_for_position_ids(
                    location_card_position_ids
                )
                db.session.commit()
                flash('Artikel bijgewerkt.', 'success')
        return redirect(url_for('artikelen_beheer'))

    raw_results = db.session.query(Lokaal_Artikel, Global_Catalogus).outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id).filter(Lokaal_Artikel.bedrijf_id == bedrijf_id).order_by(Lokaal_Artikel.eigen_naam).all()
    view_data = [{
        'obj': l,
        'display_naam': l.eigen_naam,
        'display_foto': l.foto_url or (g.foto_url if g else None),
        'is_globaal': g is not None,
        'is_afwijkend': g and l.eigen_naam != g.generieke_naam,
        'oorsprong_naam': g.generieke_naam if g else None,
        'kanban_standard': _article_kanban_standard(l),
    } for l, g in raw_results]
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
        _set_new_article_defaults(nieuw)
        db.session.add(nieuw)
        db.session.flush()
        doel_id = nieuw.lokaal_artikel_id

    posities = db.session.query(Voorraad_Positie).filter_by(bedrijf_id=bedrijf_id, lokaal_artikel_id=oud_lokaal_id).all()
    _supersede_locatiekaart_versions_for_position_ids(
        [
            position.voorraad_positie_id
            for position in posities
            if hasattr(position, 'voorraad_positie_id')
        ]
    )
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
    usage = []
    standard = _article_kanban_standard(artikel)
    for position, storage_location, room in posities:
        display = kanban_display_values(position, artikel)
        min_inherits_standard = (
            display['materiaaltype'] == Materiaaltype.KANBAN.value
            and not display['min_is_override']
        )
        refill_inherits_standard = (
            display['materiaaltype'] == Materiaaltype.KANBAN.value
            and not display['refill_is_override']
        )
        usage.append({
            'ruimte': room.naam,
            'opslaglocatie': storage_location.naam,
            'materiaaltype': display['materiaaltype'],
            'min': display['min_level'],
            'aanv': display['refill_quantity'],
            'min_afwijkend': display['min_is_override'],
            'aanv_afwijkend': display['refill_is_override'],
            'standaard_min': standard.min_level,
            'standaard_aanv': standard.refill_quantity,
            'min_erft_standaard': min_inherits_standard,
            'aanv_erft_standaard': refill_inherits_standard,
            'erft_standaard': min_inherits_standard and refill_inherits_standard,
        })
    return jsonify(usage)

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
                _set_new_article_defaults(nieuw)
                db.session.add(nieuw)
                db.session.commit()
                flash('Opgenomen in lokaal assortiment.', 'success')
        elif actie == 'bewerk_global':
            global_id = request.form.get('global_id', type=int)
            item = db.session.query(Global_Catalogus).filter(Global_Catalogus.global_id == global_id).first()
            if item:
                old_name = item.generieke_naam
                old_photo = item.foto_url
                new_photo = old_photo
                item.generieke_naam = request.form.get('naam')
                item.ean_code = request.form.get('ean')
                item.categorie = request.form.get('categorie')
                file = request.files.get('afbeelding')
                if file:
                    url = upload_image_to_azure(file)
                    if url and "ERROR" not in url:
                        new_photo = url
                item.foto_url = new_photo
                location_card_position_ids = set()
                if old_name != item.generieke_naam:
                    location_card_position_ids.update(
                        _position_ids_for_global_item(global_id, 'eigen_naam')
                    )
                if old_photo != new_photo:
                    location_card_position_ids.update(
                        _position_ids_for_global_item(global_id, 'foto_url')
                    )
                _supersede_locatiekaart_versions_for_position_ids(
                    location_card_position_ids
                )
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
        old_logo = getattr(bedrijf, 'logo_url', None)
        bedrijf.naam = request.form.get('naam')
        file = request.files.get('logo')
        if file:
            url = upload_image_to_azure(file)
            if url and "ERROR" not in url: bedrijf.logo_url = url
        if old_logo != getattr(bedrijf, 'logo_url', None):
            _supersede_locatiekaart_versions_for_company(bedrijf_id)
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
                            artikel = get_scoped_item(
                                Lokaal_Artikel,
                                pos.lokaal_artikel_id,
                                bedrijf_id,
                            )
                            override_min, override_refill = (
                                position_kanban_override_values(pos, artikel)
                            )
                            nieuw_pos = Voorraad_Positie(
                                bedrijf_id=bedrijf_id,
                                kast_id=nieuwe_kast.kast_id,
                                lokaal_artikel_id=pos.lokaal_artikel_id,
                                strategie=(
                                    'TWO_BIN'
                                    if _position_material_type(pos)
                                    is Materiaaltype.KANBAN
                                    else 'STANDARD'
                                ),
                                materiaaltype=_position_material_type(pos).value,
                                kanban_min_override=override_min,
                                kanban_refill_quantity_override=override_refill,
                                locatie_foto_url=pos.locatie_foto_url,
                            )
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
    if not check_db():
        return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()

    try:
        if type == 'vestiging':
            item = get_scoped_item(Vestiging, id, bedrijf_id)
            if not item:
                flash('Vestiging niet gevonden of geen toegang.', 'warning')
                return redirect(request.referrer or url_for('beheer_infra'))
            changed = (
                getattr(item, 'naam', None) != request.form.get('naam')
            )
            item.naam = request.form.get('naam')
            item.adres = request.form.get('adres')
            if changed:
                _supersede_locatiekaart_versions_for_position_ids(
                    _position_ids_for_scope('vestiging', id)
                )
        elif type == 'ruimte':
            item = get_scoped_item(Ruimte, id, bedrijf_id)
            if not item:
                flash('Ruimte niet gevonden of geen toegang.', 'warning')
                return redirect(request.referrer or url_for('beheer_infra'))
            changed = (
                getattr(item, 'naam', None) != request.form.get('naam')
                or getattr(item, 'nummer', None) != request.form.get('nummer')
                or str(getattr(item, 'ruimte_type_id', None) or '')
                != str(request.form.get('ruimte_type_id') or '')
            )
            item.naam = request.form.get('naam')
            item.nummer = request.form.get('nummer')
            item.ruimte_type_id = request.form.get('ruimte_type_id', type=int)
            if changed:
                _supersede_locatiekaart_versions_for_position_ids(
                    _position_ids_for_scope('ruimte', id)
                )
        elif type == 'kast':
            item = get_scoped_item(Kast, id, bedrijf_id)
            if not item:
                flash('Opslaglocatie niet gevonden of geen toegang.', 'warning')
                return redirect(request.referrer or url_for('beheer_infra'))
            changed = (
                getattr(item, 'naam', None) != request.form.get('naam')
                or getattr(item, 'type_opslag', None)
                != request.form.get('type_opslag')
            )
            item.naam = request.form.get('naam')
            item.type_opslag = request.form.get('type_opslag')
            if changed:
                _supersede_locatiekaart_versions_for_position_ids(
                    _position_ids_for_scope('kast', id)
                )
        elif type == 'ruimte_type':
            item = get_scoped_item(Ruimte_Type, id, bedrijf_id)
            if not item:
                flash('Kamertype niet gevonden of geen toegang.', 'warning')
                return redirect(request.referrer or url_for('beheer_infra'))
            changed = (
                getattr(item, 'naam', None) != request.form.get('naam')
                or getattr(item, 'kleur_hex', None) != request.form.get('kleur')
            )
            item.naam = request.form.get('naam')
            item.kleur_hex = request.form.get('kleur')
            if changed:
                _supersede_locatiekaart_versions_for_position_ids(
                    _position_ids_for_scope('ruimte_type', id)
                )
        else:
            flash('Onbekend itemtype.', 'warning')
            return redirect(request.referrer or url_for('beheer_infra'))

        db.session.commit()
        flash('Bijgewerkt.', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Kan niet bijwerken: {exc}', 'danger')
    return redirect(request.referrer or url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=debug_mode)
