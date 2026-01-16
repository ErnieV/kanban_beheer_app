import os
import uuid
import urllib.parse
import json # Alleen nog nodig voor imports, niet meer voor opslag
import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, or_
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from azure.storage.blob import BlobServiceClient

# Laad variabelen
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'super_geheim_sleutel_123')

# --- CONFIGURATIE ---
db_server = os.environ.get('DB_SERVER')
db_name = os.environ.get('DB_NAME')
db_user = os.environ.get('DB_USER')
db_pass = os.environ.get('DB_PASS')
connect_str = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
container_name = os.environ.get('AZURE_CONTAINER_NAME')

# De basis URL voor de QR codes
API_BASE_URL = os.environ.get('KANBAN_API_BASE_URL', 'https://api.uw-zorginstelling.nl/scan')

if not all([db_server, db_name, db_user, db_pass]):
    print("WAARSCHUWING: Database configuratie ontbreekt!")

encoded_user = urllib.parse.quote_plus(db_user) if db_user else ''
encoded_pass = urllib.parse.quote_plus(db_pass) if db_pass else ''

driver = 'ODBC+Driver+18+for+SQL+Server'
app.config['SQLALCHEMY_DATABASE_URI'] = f"mssql+pyodbc://{encoded_user}:{encoded_pass}@{db_server}/{db_name}?driver={driver}&TrustServerCertificate=yes"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

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

with app.app_context():
    try:
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

# --- CONTEXT PROCESSOR & HELPERS ---

@app.context_processor
def inject_context():
    if not db_operational or not Bedrijf:
        return dict(huidig_bedrijf=None)
    bedrijf_id = session.get('bedrijf_id', 1)
    bedrijf = db.session.query(Bedrijf).get(bedrijf_id)
    return dict(huidig_bedrijf=bedrijf)

def get_huidig_bedrijf_id():
    return session.get('bedrijf_id', 1)

def check_db():
    if not db_operational:
        flash("Geen verbinding met de database.", 'danger')
        return False
    return True

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

# --- ROUTES ---

@app.route('/')
def dashboard():
    if not check_db(): return render_template('dashboard.html', bedrijven=[])
    
    # 1. Alle bedrijven voor de selector
    alle_bedrijven = db.session.query(Bedrijf).all()
    
    # 2. Count voor print wachtrij (alleen voor huidig bedrijf)
    print_queue_count = 0
    huidig_id = get_huidig_bedrijf_id()
    if huidig_id:
        try:
            print_queue_count = db.session.query(Print_Queue).filter_by(
                bedrijf_id=huidig_id, 
                status='PENDING'
            ).count()
        except Exception:
            print_queue_count = 0 # Fallback bij db error

    return render_template('dashboard.html', 
                           bedrijven=alle_bedrijven, 
                           print_queue_count=print_queue_count)

@app.route('/switch-bedrijf/<int:bedrijf_id>')
def switch_bedrijf(bedrijf_id):
    session['bedrijf_id'] = bedrijf_id
    flash('Bedrijf gewijzigd.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/bedrijf/nieuw', methods=['POST'])
def nieuw_bedrijf():
    if not check_db(): return redirect(url_for('dashboard'))
    
    naam = request.form.get('naam')
    if naam:
        try:
            # Nieuw bedrijf aanmaken
            nieuw = Bedrijf(naam=naam)
            db.session.add(nieuw)
            db.session.commit()
            
            # Direct inloggen op dit nieuwe bedrijf
            session['bedrijf_id'] = nieuw.bedrijf_id
            flash(f'Bedrijf "{naam}" aangemaakt en geselecteerd. Welkom!', 'success')
        except IntegrityError:
            db.session.rollback()
            flash('Een bedrijf met deze naam bestaat al.', 'warning')
        except Exception as e:
            db.session.rollback()
            flash(f'Fout bij aanmaken: {e}', 'danger')
            
    return redirect(url_for('dashboard'))

# =========================================================
#  ASSISTENT FLOW
# =========================================================

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
            count = db.session.query(Kast).filter_by(ruimte_id=ruimte.ruimte_id).count()
            ruimtes_data.append((ruimte, vestiging, count))
        return render_template('assistent_kamer_selectie.html', ruimtes=ruimtes_data)
    except Exception as e:
        print(f"Error: {e}")
        return redirect(url_for('dashboard'))

@app.route('/assistent/kamer/<int:ruimte_id>')
def assistent_kamer_view(ruimte_id):
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    
    ruimte = db.session.query(Ruimte).outerjoin(Ruimte_Type).filter(Ruimte.ruimte_id == ruimte_id).first()
    if ruimte.ruimte_type_id:
        rt = db.session.query(Ruimte_Type).get(ruimte.ruimte_type_id)
        ruimte.kleur_hex = rt.kleur_hex
    else:
        ruimte.kleur_hex = None

    kasten_in_kamer = db.session.query(Kast).filter_by(ruimte_id=ruimte_id).all()
    kasten_data = {}
    for kast in kasten_in_kamer:
        inhoud = db.session.query(Voorraad_Positie, Lokaal_Artikel, Global_Catalogus)\
            .join(Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id)\
            .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
            .filter(Voorraad_Positie.kast_id == kast.kast_id)\
            .all()
        kasten_data[kast] = inhoud
    alle_artikelen = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id).order_by(Lokaal_Artikel.eigen_naam).all()
    return render_template('assistent_kamer_view.html', ruimte=ruimte, kasten_data=kasten_data, alle_artikelen=alle_artikelen)

@app.route('/assistent/update-voorraad/<int:voorraad_positie_id>', methods=['POST'])
def update_voorraad_positie(voorraad_positie_id):
    positie = db.session.query(Voorraad_Positie).get(voorraad_positie_id)
    if positie:
        positie.trigger_min = request.form.get('trigger_min')
        positie.target_max = request.form.get('target_max')
        db.session.commit()
        flash('Voorraadniveaus bijgewerkt.', 'success')
    kast = db.session.query(Kast).get(positie.kast_id)
    return redirect(url_for('assistent_kamer_view', ruimte_id=kast.ruimte_id))

@app.route('/assistent/kast/<int:kast_id>/toevoegen', methods=['POST'])
def add_to_kast_from_room(kast_id):
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    artikel_id = request.form.get('artikel_id')
    bestaat = db.session.query(Voorraad_Positie).filter_by(kast_id=kast_id, lokaal_artikel_id=artikel_id).first()
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
    kast = db.session.query(Kast).get(kast_id)
    return redirect(url_for('assistent_kamer_view', ruimte_id=kast.ruimte_id))

# --- KANBAN AANVRAGEN ---

def create_queue_item(pos, art, kast, ruimte, r_type, bedrijf):
    header_text = ruimte.naam.upper()
    if ruimte.nummer: header_text = f"{ruimte.nummer} {header_text}"
    
    return Print_Queue(
        bedrijf_id=bedrijf.bedrijf_id,
        status='PENDING',
        printer_id="reception-badgy-01",
        card_type="KANBAN_TWO_BIN",
        header_text=header_text,
        header_color=r_type.kleur_hex if r_type else "#3B82F6",
        product_name=art.eigen_naam,
        product_packaging=art.verpakkingseenheid_tekst or "Stuk",
        product_sku=str(art.lokaal_artikel_id),
        product_image_url=pos.locatie_foto_url or art.foto_url,
        location_text=f"{kast.naam} ({kast.type_opslag})",
        min_level=pos.trigger_min,
        max_level=pos.target_max,
        qr_code_value=pos.qr_code or "NO_QR",
        qr_human_readable=f"POS-{pos.voorraad_positie_id}",
        company_logo_url=bedrijf.logo_url
    )

@app.route('/assistent/kanban/aanvragen/enkel/<int:voorraad_positie_id>', methods=['POST'])
def kanban_aanvragen_enkel(voorraad_positie_id):
    try:
        result = db.session.query(Voorraad_Positie, Lokaal_Artikel, Kast, Ruimte, Ruimte_Type, Bedrijf)\
            .join(Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id)\
            .join(Kast, Voorraad_Positie.kast_id == Kast.kast_id)\
            .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
            .outerjoin(Ruimte_Type, Ruimte.ruimte_type_id == Ruimte_Type.ruimte_type_id)\
            .join(Bedrijf, Voorraad_Positie.bedrijf_id == Bedrijf.bedrijf_id)\
            .filter(Voorraad_Positie.voorraad_positie_id == voorraad_positie_id).first()

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
    try:
        results = db.session.query(Voorraad_Positie, Lokaal_Artikel, Kast, Ruimte, Ruimte_Type, Bedrijf)\
            .join(Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id)\
            .join(Kast, Voorraad_Positie.kast_id == Kast.kast_id)\
            .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
            .outerjoin(Ruimte_Type, Ruimte.ruimte_type_id == Ruimte_Type.ruimte_type_id)\
            .join(Bedrijf, Voorraad_Positie.bedrijf_id == Bedrijf.bedrijf_id)\
            .filter(Voorraad_Positie.kast_id == kast_id).all()

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
    
    queue_items = db.session.query(Print_Queue)\
        .filter(Print_Queue.bedrijf_id == bedrijf_id, Print_Queue.status == 'PENDING')\
        .order_by(Print_Queue.aangemaakt_op.desc()).all()
        
    return render_template('assistent_print_queue.html', queue_items=queue_items)

@app.route('/assistent/print-queue/annuleren/<int:print_id>', methods=['POST'])
def annuleren_print_opdracht(print_id):
    item = db.session.query(Print_Queue).get(print_id)
    if item and item.status == 'PENDING':
        db.session.delete(item)
        db.session.commit()
        flash("Aanvraag geannuleerd.", "info")
    return redirect(url_for('assistent_print_queue'))

# =========================================================
#  ARTIKEL & INFRA BEHEER (Ongewijzigd)
# =========================================================

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
            global_id = request.form.get('global_id')
            global_item = db.session.query(Global_Catalogus).get(global_id)
            bestaat = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id, global_id=global_id).first()
            if not bestaat:
                nieuw = Lokaal_Artikel(bedrijf_id=bedrijf_id, global_id=global_id, eigen_naam=global_item.generieke_naam, verpakkingseenheid_tekst='Stuk')
                db.session.add(nieuw)
                db.session.commit()
                flash('Gekoppeld.', 'success')
        elif actie == 'bewerk_artikel':
            artikel_id = request.form.get('artikel_id')
            artikel = db.session.query(Lokaal_Artikel).get(artikel_id)
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
    oud_lokaal_id = request.form.get('oud_lokaal_id')
    nieuw_global_id = request.form.get('nieuw_global_id')
    
    oud_artikel = db.session.query(Lokaal_Artikel).get(oud_lokaal_id)
    bestaand_doel = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id, global_id=nieuw_global_id).first()
    
    if bestaand_doel: doel_id = bestaand_doel.lokaal_artikel_id
    else:
        g_item = db.session.query(Global_Catalogus).get(nieuw_global_id)
        nieuw = Lokaal_Artikel(bedrijf_id=bedrijf_id, global_id=nieuw_global_id, eigen_naam=g_item.generieke_naam, verpakkingseenheid_tekst=oud_artikel.verpakkingseenheid_tekst)
        db.session.add(nieuw)
        db.session.flush()
        doel_id = nieuw.lokaal_artikel_id

    posities = db.session.query(Voorraad_Positie).filter_by(lokaal_artikel_id=oud_lokaal_id).all()
    for pos in posities:
        if db.session.query(Voorraad_Positie).filter_by(kast_id=pos.kast_id, lokaal_artikel_id=doel_id).first(): db.session.delete(pos)
        else: pos.lokaal_artikel_id = doel_id
    
    if oud_artikel: db.session.delete(oud_artikel)
    db.session.commit()
    flash('Artikel vervangen.', 'success')
    return redirect(url_for('artikelen_beheer'))

@app.route('/api/artikel-gebruik/<int:artikel_id>')
def api_artikel_gebruik(artikel_id):
    posities = db.session.query(Voorraad_Positie, Kast, Ruimte).join(Kast, Voorraad_Positie.kast_id == Kast.kast_id).join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id).filter(Voorraad_Positie.lokaal_artikel_id == artikel_id).all()
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
            global_id = request.form.get('global_id')
            global_item = db.session.query(Global_Catalogus).get(global_id)
            bestaat = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id, global_id=global_id).first()
            if not bestaat:
                nieuw = Lokaal_Artikel(bedrijf_id=bedrijf_id, global_id=global_id, eigen_naam=global_item.generieke_naam, verpakkingseenheid_tekst="Stuk")
                db.session.add(nieuw)
                db.session.commit()
                flash('Opgenomen in lokaal assortiment.', 'success')
        elif actie == 'bewerk_global':
            item = db.session.query(Global_Catalogus).get(request.form.get('global_id'))
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
            global_id = request.form.get('global_id')
            usage_count = db.session.query(Lokaal_Artikel).filter_by(global_id=global_id).count()
            if usage_count > 0:
                flash(f'Kan item NIET verwijderen: in gebruik.', 'danger')
            else:
                item = db.session.query(Global_Catalogus).get(global_id)
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
    bedrijf = db.session.query(Bedrijf).get(bedrijf_id)
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
                vest_id = request.form.get('vestiging_id')
                nieuwe_ruimte = Ruimte(bedrijf_id=bedrijf_id, vestiging_id=vest_id, naam=request.form.get('naam'), nummer=request.form.get('nummer'), ruimte_type_id=request.form.get('ruimte_type_id'), type_ruimte='KAMER')
                db.session.add(nieuwe_ruimte)
                db.session.flush()
                kopieer_id = request.form.get('kopieer_van_ruimte_id')
                if kopieer_id:
                    bron_kasten = db.session.query(Kast).filter_by(ruimte_id=kopieer_id).all()
                    for bron_kast in bron_kasten:
                        nieuwe_kast = Kast(bedrijf_id=bedrijf_id, ruimte_id=nieuwe_ruimte.ruimte_id, naam=bron_kast.naam, type_opslag=bron_kast.type_opslag)
                        db.session.add(nieuwe_kast)
                        db.session.flush()
                        posities = db.session.query(Voorraad_Positie).filter_by(kast_id=bron_kast.kast_id).all()
                        for pos in posities:
                            nieuw_pos = Voorraad_Positie(bedrijf_id=bedrijf_id, kast_id=nieuwe_kast.kast_id, lokaal_artikel_id=pos.lokaal_artikel_id, strategie=pos.strategie, trigger_min=pos.trigger_min, target_max=pos.target_max, locatie_foto_url=pos.locatie_foto_url)
                            db.session.add(nieuw_pos)
                            db.session.flush()
                            nieuw_pos.qr_code = f"{API_BASE_URL}/{nieuw_pos.voorraad_positie_id}"
                db.session.commit()
                return redirect(url_for('beheer_infra', vestiging_id=vest_id))
            elif actie == 'nieuwe_kast':
                ruimte_id = request.form.get('ruimte_id')
                ruimte = db.session.query(Ruimte).get(ruimte_id)
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
    ruimtes = []
    if active_vestiging_id: ruimtes = db.session.query(Ruimte).filter_by(vestiging_id=active_vestiging_id).order_by(Ruimte.nummer, Ruimte.naam).all()
    kasten = []
    if active_ruimte_id: kasten = db.session.query(Kast).filter_by(ruimte_id=active_ruimte_id).all()
    alle_ruimtes = db.session.query(Ruimte).join(Vestiging).filter(Vestiging.bedrijf_id == bedrijf_id).all()
    return render_template('beheer_infra.html', vestigingen=vestigingen, ruimtes=ruimtes, kasten=kasten, alle_ruimtes=alle_ruimtes, ruimte_types=ruimte_types, active_vestiging_id=active_vestiging_id, active_ruimte_id=active_ruimte_id)

@app.route('/beheer/verwijder/<type>/<int:id>', methods=['POST'])
def verwijder_item(type, id):
    redirect_url = url_for('beheer_infra')
    vestiging_id = request.args.get('vestiging_id')
    ruimte_id = request.args.get('ruimte_id')
    if vestiging_id: redirect_url = url_for('beheer_infra', vestiging_id=vestiging_id, ruimte_id=ruimte_id)

    try:
        item = None
        if type == 'artikel':
            item = db.session.query(Lokaal_Artikel).get(id)
            redirect_url = url_for('artikelen_beheer')
        elif type == 'voorraad':
            item = db.session.query(Voorraad_Positie).get(id)
            if item: redirect_url = url_for('assistent_kamer_view', ruimte_id=db.session.query(Kast).get(item.kast_id).ruimte_id)
        elif type == 'vestiging': item = db.session.query(Vestiging).get(id)
        elif type == 'ruimte': item = db.session.query(Ruimte).get(id)
        elif type == 'kast': item = db.session.query(Kast).get(id)
        elif type == 'ruimte_type': item = db.session.query(Ruimte_Type).get(id)
        
        if item:
            db.session.delete(item)
            db.session.commit()
            flash(f'{type.capitalize()} verwijderd.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Fout: {e}', 'danger')
    return redirect(redirect_url)

@app.route('/beheer/update/<type>/<int:id>', methods=['POST'])
def update_item(type, id):
    redirect_url = url_for('beheer_infra')
    if 'vestiging_id' in request.form: redirect_url = url_for('beheer_infra', vestiging_id=request.form.get('vestiging_id'))

    try:
        if type == 'vestiging':
            item = db.session.query(Vestiging).get(id)
            item.naam = request.form.get('naam')
            item.adres = request.form.get('adres')
        elif type == 'ruimte':
            item = db.session.query(Ruimte).get(id)
            item.naam = request.form.get('naam')
            item.nummer = request.form.get('nummer')
            item.ruimte_type_id = request.form.get('ruimte_type_id')
        elif type == 'kast':
            item = db.session.query(Kast).get(id)
            item.naam = request.form.get('naam')
            item.type_opslag = request.form.get('type_opslag')
        db.session.commit()
        flash('Item bijgewerkt.', 'success')
    except IntegrityError as e:
        db.session.rollback()
        if "CHK_Kast_Type" in str(e):
            flash("Fout: Ongeldig type opslag. Kies 'Grijpvoorraad' of 'Bulkvoorraad'.", 'danger')
        else:
            flash(f"Database fout: {e.orig}", 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Fout: {e}', 'danger')
    return redirect(redirect_url)

if __name__ == '__main__':
    app.run(debug=True)