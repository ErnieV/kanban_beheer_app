import os
import uuid
import urllib.parse
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.automap import automap_base
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
Kast = None
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
        Kast = getattr(Base.classes, 'Kast', None)
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
    alle_bedrijven = db.session.query(Bedrijf).all()
    return render_template('dashboard.html', bedrijven=alle_bedrijven)

@app.route('/switch-bedrijf/<int:bedrijf_id>')
def switch_bedrijf(bedrijf_id):
    session['bedrijf_id'] = bedrijf_id
    flash('Bedrijf gewijzigd.', 'info')
    return redirect(url_for('dashboard'))

# =========================================================
#  ASSISTENT FLOW (Kamers & Kasten)
# =========================================================

@app.route('/assistent/kamers')
def assistent_kamers():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    
    try:
        ruimtes_query = db.session.query(Ruimte, Vestiging)\
            .join(Vestiging, Ruimte.vestiging_id == Vestiging.vestiging_id)\
            .filter(Vestiging.bedrijf_id == bedrijf_id)\
            .order_by(Vestiging.naam, Ruimte.nummer, Ruimte.naam).all() # Sorteer nu ook op nummer
            
        ruimtes_data = []
        for ruimte, vestiging in ruimtes_query:
            count = db.session.query(Kast).filter_by(ruimte_id=ruimte.ruimte_id).count()
            ruimtes_data.append((ruimte, vestiging, count))
            
        return render_template('assistent_kamer_selectie.html', ruimtes=ruimtes_data)
    except Exception as e:
        print(f"Error in assistent_kamers: {e}")
        flash("Er ging iets mis bij het ophalen van de kamers.", "danger")
        return redirect(url_for('dashboard'))

@app.route('/assistent/kamer/<int:ruimte_id>')
def assistent_kamer_view(ruimte_id):
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    
    ruimte = db.session.query(Ruimte).get(ruimte_id)
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

# FIX: De database ID heet waarschijnlijk voorraad_positie_id, niet positie_id
@app.route('/assistent/update-voorraad/<int:voorraad_positie_id>', methods=['POST'])
def update_voorraad_positie(voorraad_positie_id):
    positie = db.session.query(Voorraad_Positie).get(voorraad_positie_id)
    if positie:
        positie.trigger_min = request.form.get('trigger_min')
        positie.target_max = request.form.get('target_max')
        db.session.commit()
        flash('Voorraadniveaus bijgewerkt.', 'success')
    else:
        flash('Voorraadpositie niet gevonden.', 'danger')
        return redirect(url_for('assistent_kamers'))
        
    kast = db.session.query(Kast).get(positie.kast_id)
    return redirect(url_for('assistent_kamer_view', ruimte_id=kast.ruimte_id))

@app.route('/assistent/kast/<int:kast_id>/toevoegen', methods=['POST'])
def add_to_kast_from_room(kast_id):
    bedrijf_id = get_huidig_bedrijf_id()
    artikel_id = request.form.get('artikel_id')
    bestaat = db.session.query(Voorraad_Positie).filter_by(kast_id=kast_id, lokaal_artikel_id=artikel_id).first()
    if not bestaat:
        nieuw = Voorraad_Positie(
            bedrijf_id=bedrijf_id, kast_id=kast_id, lokaal_artikel_id=artikel_id,
            strategie='TWO_BIN', trigger_min=1, target_max=2
        )
        db.session.add(nieuw)
        db.session.commit()
        flash('Artikel toegevoegd.', 'success')
    else:
        flash('Artikel zit al in de kast.', 'warning')
    kast = db.session.query(Kast).get(kast_id)
    return redirect(url_for('assistent_kamer_view', ruimte_id=kast.ruimte_id))

# =========================================================
#  ARTIKEL BEHEER
# =========================================================
# (Ongewijzigd, maar wel nodig voor de context)
@app.route('/artikelen-beheer', methods=['GET', 'POST'])
def artikelen_beheer():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()

    if request.method == 'POST':
        actie = request.form.get('actie')
        
        if actie == 'nieuw_lokaal':
            nieuw = Lokaal_Artikel(
                bedrijf_id=bedrijf_id,
                eigen_naam=request.form.get('naam'),
                verpakkingseenheid_tekst=request.form.get('eenheid'),
            )
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
                nieuw = Lokaal_Artikel(
                    bedrijf_id=bedrijf_id,
                    global_id=global_id,
                    eigen_naam=global_item.generieke_naam,
                    verpakkingseenheid_tekst='Stuk'
                )
                db.session.add(nieuw)
                db.session.commit()
                flash(f'"{global_item.generieke_naam}" toegevoegd aan assortiment.', 'success')
            else:
                flash('Artikel zit al in assortiment.', 'warning')
        
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
                flash('Artikel gegevens bijgewerkt.', 'success')

        return redirect(url_for('artikelen_beheer'))

    raw_results = db.session.query(Lokaal_Artikel, Global_Catalogus)\
        .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
        .filter(Lokaal_Artikel.bedrijf_id == bedrijf_id)\
        .order_by(Lokaal_Artikel.eigen_naam).all()
    
    view_data = []
    for lokaal, globaal in raw_results:
        display_naam = lokaal.eigen_naam 
        display_foto = lokaal.foto_url or (globaal.foto_url if globaal else None)
        is_globaal_gelinkt = globaal is not None
        is_afwijkend = is_globaal_gelinkt and (lokaal.eigen_naam != globaal.generieke_naam)
        
        view_data.append({
            'obj': lokaal, 
            'display_naam': display_naam,
            'display_foto': display_foto,
            'is_globaal': is_globaal_gelinkt,
            'is_afwijkend': is_afwijkend,
            'oorsprong_naam': globaal.generieke_naam if globaal else None
        })

    linked_ids = db.session.query(Lokaal_Artikel.global_id).filter(
        Lokaal_Artikel.bedrijf_id == bedrijf_id, 
        Lokaal_Artikel.global_id.isnot(None)
    )
    beschikbare_globals = db.session.query(Global_Catalogus).filter(Global_Catalogus.global_id.notin_(linked_ids)).all()

    return render_template('artikelen_beheer.html', artikelen=view_data, beschikbare_globals=beschikbare_globals)

@app.route('/artikelen-beheer/vervang', methods=['POST'])
def vervang_artikel():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()
    
    oud_lokaal_id = request.form.get('oud_lokaal_id')
    nieuw_global_id = request.form.get('nieuw_global_id')
    
    oud_artikel = db.session.query(Lokaal_Artikel).get(oud_lokaal_id)
    if not oud_artikel: return redirect(url_for('artikelen_beheer'))

    bestaand_doel = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id, global_id=nieuw_global_id).first()
    
    if bestaand_doel:
        doel_id = bestaand_doel.lokaal_artikel_id
    else:
        global_item = db.session.query(Global_Catalogus).get(nieuw_global_id)
        nieuw = Lokaal_Artikel(
            bedrijf_id=bedrijf_id,
            global_id=nieuw_global_id,
            eigen_naam=global_item.generieke_naam,
            verpakkingseenheid_tekst=oud_artikel.verpakkingseenheid_tekst
        )
        db.session.add(nieuw)
        db.session.flush()
        doel_id = nieuw.lokaal_artikel_id

    posities = db.session.query(Voorraad_Positie).filter_by(lokaal_artikel_id=oud_lokaal_id).all()
    for pos in posities:
        if db.session.query(Voorraad_Positie).filter_by(kast_id=pos.kast_id, lokaal_artikel_id=doel_id).first():
            db.session.delete(pos)
        else:
            pos.lokaal_artikel_id = doel_id
            
    db.session.delete(oud_artikel)
    db.session.commit()
    flash('Artikel succesvol gemerged/vervangen.', 'success')
    return redirect(url_for('artikelen_beheer'))

@app.route('/api/artikel-gebruik/<int:artikel_id>')
def api_artikel_gebruik(artikel_id):
    posities = db.session.query(Voorraad_Positie, Kast, Ruimte)\
        .join(Kast, Voorraad_Positie.kast_id == Kast.kast_id)\
        .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
        .filter(Voorraad_Positie.lokaal_artikel_id == artikel_id).all()
    data = [{'ruimte': r.naam, 'kast': k.naam, 'min': p.trigger_min, 'max': p.target_max} for p, k, r in posities]
    return jsonify(data)

# === BEHEER ROUTES ===

@app.route('/beheer/catalogus', methods=['GET', 'POST'])
def beheer_catalogus():
    if not check_db(): return redirect(url_for('dashboard'))
    bedrijf_id = get_huidig_bedrijf_id()

    if request.method == 'POST':
        actie = request.form.get('actie')
        
        if actie == 'nieuw_global':
            nieuw = Global_Catalogus(
                generieke_naam=request.form.get('naam'),
                ean_code=request.form.get('ean'),
                categorie=request.form.get('categorie')
            )
            file = request.files.get('afbeelding')
            if file:
                url = upload_image_to_azure(file)
                if url and "ERROR" not in url: nieuw.foto_url = url
            db.session.add(nieuw)
            db.session.commit()
            flash('Global item aangemaakt.', 'success')
        
        elif actie == 'koppel_lokaal':
            global_id = request.form.get('global_id')
            bestaat = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id, global_id=global_id).first()
            if not bestaat:
                global_item = db.session.query(Global_Catalogus).get(global_id)
                nieuw_lokaal = Lokaal_Artikel(
                    bedrijf_id=bedrijf_id,
                    global_id=global_id,
                    eigen_naam=global_item.generieke_naam,
                    verpakkingseenheid_tekst="Stuk"
                )
                db.session.add(nieuw_lokaal)
                db.session.commit()
                flash('Opgenomen in lokaal assortiment.', 'success')
        
        elif actie == 'bewerk_global':
            global_id = request.form.get('global_id')
            item = db.session.query(Global_Catalogus).get(global_id)
            if item:
                item.generieke_naam = request.form.get('naam')
                item.ean_code = request.form.get('ean')
                item.categorie = request.form.get('categorie')
                file = request.files.get('afbeelding')
                if file:
                    url = upload_image_to_azure(file)
                    if url and "ERROR" not in url: item.foto_url = url
                db.session.commit()
                flash('Item bijgewerkt.', 'success')

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
    
    if request.method == 'POST':
        actie = request.form.get('actie')
        
        if actie == 'nieuwe_vestiging':
            db.session.add(Vestiging(bedrijf_id=bedrijf_id, naam=request.form.get('naam'), adres=request.form.get('adres')))
        
        elif actie == 'nieuwe_ruimte':
            nieuwe_ruimte = Ruimte(
                bedrijf_id=bedrijf_id, 
                vestiging_id=request.form.get('vestiging_id'), 
                naam=request.form.get('naam'),
                nummer=request.form.get('nummer'), # NIEUW: Nummer opslaan
                type_ruimte='KAMER'
            )
            db.session.add(nieuwe_ruimte)
            db.session.flush()

            kopieer_id = request.form.get('kopieer_van_ruimte_id')
            if kopieer_id:
                try:
                    bron_kasten = db.session.query(Kast).filter_by(ruimte_id=kopieer_id).all()
                    for bron_kast in bron_kasten:
                        nieuwe_kast = Kast(
                            bedrijf_id=bedrijf_id,
                            ruimte_id=nieuwe_ruimte.ruimte_id,
                            naam=bron_kast.naam,
                            type_opslag=bron_kast.type_opslag
                        )
                        db.session.add(nieuwe_kast)
                        db.session.flush()
                        
                        posities = db.session.query(Voorraad_Positie).filter_by(kast_id=bron_kast.kast_id).all()
                        for pos in posities:
                            nieuw_pos = Voorraad_Positie(
                                bedrijf_id=bedrijf_id,
                                kast_id=nieuwe_kast.kast_id,
                                lokaal_artikel_id=pos.lokaal_artikel_id,
                                strategie=pos.strategie,
                                trigger_min=pos.trigger_min,
                                target_max=pos.target_max,
                                locatie_foto_url=pos.locatie_foto_url
                            )
                            db.session.add(nieuw_pos)
                    flash('Ruimte inclusief inrichting gekopieerd!', 'success')
                except Exception as e:
                    flash(f'Ruimte aangemaakt, maar fout bij kopiëren: {e}', 'warning')
            else:
                flash('Nieuwe lege ruimte aangemaakt.', 'success')

        elif actie == 'nieuwe_kast':
            db.session.add(Kast(bedrijf_id=bedrijf_id, ruimte_id=request.form.get('ruimte_id'), naam=request.form.get('naam'), type_opslag=request.form.get('type_opslag')))
        
        db.session.commit()
        return redirect(url_for('beheer_infra'))
    
    vestigingen = db.session.query(Vestiging).filter_by(bedrijf_id=bedrijf_id).all()
    ruimtes = db.session.query(Ruimte).join(Vestiging).filter(Vestiging.bedrijf_id == bedrijf_id).all()
    kasten = db.session.query(Kast).join(Ruimte).join(Vestiging).filter(Vestiging.bedrijf_id == bedrijf_id).all()
    
    return render_template('beheer_infra.html', vestigingen=vestigingen, ruimtes=ruimtes, kasten=kasten)

@app.route('/beheer/verwijder/<type>/<int:id>', methods=['POST'])
def verwijder_item(type, id):
    redirect_url = url_for('beheer_infra')
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
    try:
        if type == 'vestiging':
            item = db.session.query(Vestiging).get(id)
            item.naam = request.form.get('naam')
            item.adres = request.form.get('adres')
        elif type == 'ruimte':
            item = db.session.query(Ruimte).get(id)
            item.naam = request.form.get('naam')
            item.nummer = request.form.get('nummer') # NIEUW: Nummer updaten
        elif type == 'kast':
            item = db.session.query(Kast).get(id)
            item.naam = request.form.get('naam')
            item.type_opslag = request.form.get('type_opslag')
        db.session.commit()
        flash('Item bijgewerkt.', 'success')
    except Exception as e:
        flash(f'Fout: {e}', 'danger')
    return redirect(url_for('beheer_infra'))

if __name__ == '__main__':
    app.run(debug=True)