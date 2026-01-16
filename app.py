import os
import uuid
import urllib.parse
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.automap import automap_base
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from azure.storage.blob import BlobServiceClient

# Laad variabelen
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key')

# --- CONFIGURATIE ---
db_server = os.environ.get('DB_SERVER')
db_name = os.environ.get('DB_NAME')
db_user = os.environ.get('DB_USER')
db_pass = os.environ.get('DB_PASS')
connect_str = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
container_name = os.environ.get('AZURE_CONTAINER_NAME')

# Check of variabelen bestaan
if not all([db_server, db_name, db_user, db_pass]):
    print("WAARSCHUWING: Database configuratie ontbreekt!")

# Veilig encoden van user/pass
encoded_user = urllib.parse.quote_plus(db_user) if db_user else ''
encoded_pass = urllib.parse.quote_plus(db_pass) if db_pass else ''

driver = 'ODBC+Driver+18+for+SQL+Server'
app.config['SQLALCHEMY_DATABASE_URI'] = f"mssql+pyodbc://{encoded_user}:{encoded_pass}@{db_server}/{db_name}?driver={driver}&TrustServerCertificate=yes"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- AUTOMAP & MODELS ---
Base = automap_base()
db_operational = False

# We definiëren globale variabelen voor de classes
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
        
        # Haal de classes veilig op
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
        else:
            print("WAARSCHUWING: Niet alle tabellen konden worden geladen.")

    except Exception as e:
        print(f"CRITIQUE DB ERROR: {e}")

# --- CONTEXT PROCESSOR (NIEUW) ---
@app.context_processor
def inject_bedrijf_context():
    """Zorgt dat 'huidig_bedrijf' beschikbaar is in ALLE templates."""
    if db_operational and Bedrijf:
        # Hardcoded op 1 voor deze fase, later dynamisch
        bedrijf = db.session.query(Bedrijf).get(1)
        return dict(huidig_bedrijf=bedrijf)
    return dict(huidig_bedrijf=None)

# --- HELPER FUNCTIES ---

def check_db():
    if not db_operational:
        flash("Geen verbinding met de database.", 'danger')
        return False
    return True

def upload_image_to_azure(file):
    if not file or file.filename == '': return None
    if not file.filename.lower().endswith('.png'): return "ERROR_TYPE"

    try:
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}-{filename}"
        if not connect_str: return "ERROR_CONFIG"

        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=unique_filename)
        blob_client.upload_blob(file)
        return blob_client.url
    except Exception as e:
        print(f"Upload error: {e}")
        return "ERROR_UPLOAD"

# --- ROUTES ---

@app.route('/')
def dashboard():
    return render_template('dashboard.html', db_status=db_operational)

# === ASSISTENT ROUTES ===
@app.route('/assistent/kasten')
def assistent_kasten():
    if not check_db(): return redirect(url_for('dashboard'))
    try:
        kasten = db.session.query(Kast, Ruimte, Vestiging)\
            .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
            .join(Vestiging, Ruimte.vestiging_id == Vestiging.vestiging_id)\
            .order_by(Vestiging.naam, Ruimte.naam, Kast.naam).all()
        return render_template('kast_selectie.html', kasten=kasten)
    except Exception as e:
        return f"Error: {e}", 500

@app.route('/assistent/kast/<int:kast_id>', methods=['GET', 'POST'])
def assistent_kast_inhoud(kast_id):
    if not check_db(): return redirect(url_for('dashboard'))
    huidig_bedrijf_id = 1
    
    gekozen_kast = db.session.query(Kast).get(kast_id)
    
    if request.method == 'POST':
        artikel_id = request.form.get('artikel_id')
        
        # Check dubbelingen
        bestaat = db.session.query(Voorraad_Positie).filter_by(kast_id=kast_id, lokaal_artikel_id=artikel_id).first()
        if bestaat:
            flash('Dit artikel ligt al in deze kast.', 'warning')
        else:
            nieuw = Voorraad_Positie(
                bedrijf_id=huidig_bedrijf_id,
                kast_id=kast_id,
                lokaal_artikel_id=artikel_id,
                strategie='TWO_BIN',
                trigger_min=request.form.get('trigger_min'),
                target_max=request.form.get('target_max')
            )
            # Foto upload
            file = request.files.get('locatie_foto')
            if file:
                url = upload_image_to_azure(file)
                if url and "ERROR" not in url: nieuw.locatie_foto_url = url
            
            db.session.add(nieuw)
            db.session.commit()
            flash('Artikel toegevoegd.', 'success')
        return redirect(url_for('assistent_kast_inhoud', kast_id=kast_id))

    # Haal inhoud op (SLIMME WEERGAVE LOGICA)
    # We joinen alles zodat we in de template de 'inheritance' kunnen tonen
    inhoud = db.session.query(Voorraad_Positie, Lokaal_Artikel, Global_Catalogus)\
        .join(Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id)\
        .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
        .filter(Voorraad_Positie.kast_id == kast_id).all()
        
    alle_artikelen = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=huidig_bedrijf_id).order_by(Lokaal_Artikel.eigen_naam).all()
    
    return render_template('kast_inhoud.html', kast=gekozen_kast, inhoud=inhoud, artikelen=alle_artikelen)

# === BEHEERDER ROUTES ===

@app.route('/beheer/catalogus', methods=['GET', 'POST'])
def beheer_catalogus():
    if not check_db(): return redirect(url_for('dashboard'))
    huidig_bedrijf_id = 1
    
    if request.method == 'POST':
        actie = request.form.get('actie')
        
        if actie == 'nieuw_global':
            # Global item aanmaken
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
            # Koppel global aan lokaal (Assortiment opnemen)
            global_id = request.form.get('global_id')
            bestaat = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=huidig_bedrijf_id, global_id=global_id).first()
            
            if not bestaat:
                global_item = db.session.query(Global_Catalogus).get(global_id)
                nieuw_lokaal = Lokaal_Artikel(
                    bedrijf_id=huidig_bedrijf_id,
                    global_id=global_id,
                    eigen_naam=global_item.generieke_naam, # Default: neem naam over
                    verpakkingseenheid_tekst="Stuk"
                )
                db.session.add(nieuw_lokaal)
                db.session.commit()
                flash('Opgenomen in lokaal assortiment.', 'success')
            else:
                flash('Dit artikel zit al in je assortiment.', 'warning')
                
        return redirect(url_for('beheer_catalogus'))

    # Haal Global items op
    globals = db.session.query(Global_Catalogus).all()
    
    # Haal Lokaal assortiment op om te checken wat we al hebben
    # (Dit is een simpele manier om 'Is_Global_Linked' te checken in de template)
    lokale_ids = [a.global_id for a in db.session.query(Lokaal_Artikel.global_id).filter_by(bedrijf_id=huidig_bedrijf_id).all()]
    
    return render_template('beheer_catalogus.html', globals=globals, lokale_ids=lokale_ids)

@app.route('/beheer/infra')
def beheer_infra():
    if not check_db(): return redirect(url_for('dashboard'))
    # (Vereenvoudigd voor nu)
    return render_template('beheer_infra.html', vestigingen=[], ruimtes=[])

if __name__ == '__main__':
    app.run(debug=True)