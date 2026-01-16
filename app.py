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

# --- AUTOMAP & ERROR HANDLING ---
Base = automap_base()
db_operational = False
db_error_msg = ""

# Probeer verbinding te maken en tabellen te laden
with app.app_context():
    try:
        Base.prepare(db.engine, reflect=True)
        
        # We halen de classes veilig op. Als automap faalt, is de class None.
        # Dit voorkomt de 'NameError' crash later in de code.
        Global_Catalogus = getattr(Base.classes, 'Global_Catalogus', None)
        Lokaal_Artikel = getattr(Base.classes, 'Lokaal_Artikel', None)
        Voorraad_Positie = getattr(Base.classes, 'Voorraad_Positie', None)
        Bedrijf = getattr(Base.classes, 'Bedrijf', None)
        Vestiging = getattr(Base.classes, 'Vestiging', None)
        Ruimte = getattr(Base.classes, 'Ruimte', None)
        Kast = getattr(Base.classes, 'Kast', None)
        Leverancier = getattr(Base.classes, 'Leverancier', None)
        
        if Global_Catalogus: # Simpele check of het gelukt is
            db_operational = True
            print("Database succesvol verbonden en tabellen geladen.")
        else:
            db_error_msg = "Tabellen niet gevonden (Automap faalde). Check tabennamen in DB."
            print(db_error_msg)
            
    except Exception as e:
        db_operational = False
        db_error_msg = str(e)
        print(f"CRITIQUE DB ERROR: {e}")

# --- HELPER FUNCTIES ---

def check_db():
    """Geeft True als DB werkt, anders abort met 500."""
    if not db_operational:
        flash(f"Database fout: {db_error_msg}", 'danger')
        return False
    return True

def upload_image_to_azure(file):
    """Uploadt een bestand naar Azure Blob en geeft de URL terug."""
    if not file or file.filename == '':
        return None
    
    if not file.filename.lower().endswith('.png'):
        return "ERROR_TYPE"

    try:
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}-{filename}"

        if not connect_str:
            return "ERROR_CONFIG"

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
    """De nieuwe startpagina met logische taken."""
    return render_template('dashboard.html', db_status=db_operational)

# === ASSISTENTE ROUTES ===

@app.route('/assistent/kasten')
def assistent_kasten():
    """Stap 1 voor assistente: Kies je werkplek."""
    if not check_db(): return render_template('dashboard.html', db_status=False)
    
    try:
        # Toon kasten gegroepeerd (eenvoudige lijst voor nu)
        kasten = db.session.query(Kast, Ruimte, Vestiging)\
            .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
            .join(Vestiging, Ruimte.vestiging_id == Vestiging.vestiging_id)\
            .order_by(Vestiging.naam, Ruimte.naam, Kast.naam).all()
        return render_template('kast_selectie.html', kasten=kasten)
    except Exception as e:
        return f"Fout bij laden kasten: {e}", 500

@app.route('/assistent/kast/<int:kast_id>', methods=['GET', 'POST'])
def assistent_kast_inhoud(kast_id):
    """De werkplek van de assistente: beheer inhoud van één kast."""
    if not check_db(): return redirect(url_for('dashboard'))
    
    huidig_bedrijf_id = 1
    gekozen_kast = db.session.query(Kast).get(kast_id)
    
    if request.method == 'POST':
        # Artikel toevoegen aan deze kast
        artikel_id = request.form.get('artikel_id')
        min_val = request.form.get('trigger_min')
        max_val = request.form.get('target_max')
        
        # Check of het artikel al op de kar ligt
        bestaat_al = db.session.query(Voorraad_Positie).filter_by(kast_id=kast_id, lokaal_artikel_id=artikel_id).first()
        
        if bestaat_al:
            flash('Dit artikel ligt al op deze kar!', 'warning')
        else:
            # Check of er een specifieke foto is geupload voor deze positie
            locatie_file = request.files.get('locatie_foto')
            locatie_url = None
            if locatie_file:
                res = upload_image_to_azure(locatie_file)
                if res and "ERROR" not in str(res): locatie_url = res

            nieuwe_positie = Voorraad_Positie(
                bedrijf_id=huidig_bedrijf_id,
                kast_id=kast_id,
                lokaal_artikel_id=artikel_id,
                strategie='TWO_BIN',
                trigger_min=min_val,
                target_max=max_val,
                locatie_foto_url=locatie_url
            )
            db.session.add(nieuwe_positie)
            db.session.commit()
            flash('Artikel toegevoegd aan kast.', 'success')
        return redirect(url_for('assistent_kast_inhoud', kast_id=kast_id))

    # Haal de inhoud op
    inhoud = db.session.query(Voorraad_Positie, Lokaal_Artikel, Global_Catalogus)\
        .join(Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id)\
        .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
        .filter(Voorraad_Positie.kast_id == kast_id).all()
    
    # Lijst voor de dropdown (alleen lokale artikelen)
    alle_artikelen = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=huidig_bedrijf_id).order_by(Lokaal_Artikel.eigen_naam).all()
    
    return render_template('kast_inhoud.html', kast=gekozen_kast, inhoud=inhoud, artikelen=alle_artikelen)

# === BEHEERDER ROUTES ===

@app.route('/beheer/catalogus', methods=['GET', 'POST'])
def beheer_catalogus():
    """Beheerder: Globale catalogus en leveranciers."""
    if not check_db(): return render_template('dashboard.html', db_status=False)
    
    huidig_bedrijf_id = 1
    
    if request.method == 'POST':
        actie = request.form.get('actie')
        
        if actie == 'nieuw_global':
            # Voeg toe aan globale catalogus
            naam = request.form.get('naam')
            ean = request.form.get('ean')
            cat = request.form.get('categorie')
            # Upload eventueel een plaatje
            file = request.files.get('afbeelding')
            img_url = None
            if file:
                res = upload_image_to_azure(file)
                if res and "ERROR" not in res: img_url = res

            nieuw = Global_Catalogus(generieke_naam=naam, ean_code=ean, categorie=cat, foto_url=img_url)
            db.session.add(nieuw)
            db.session.commit()
            flash(f'Globale catalogus item "{naam}" aangemaakt.', 'success')
        
        elif actie == 'koppel_lokaal':
            # Maak lokaal artikel van een globaal item
            global_id = request.form.get('global_id')
            global_item = db.session.query(Global_Catalogus).get(global_id)
            
            # Check of we hem al hebben
            bestaat = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=huidig_bedrijf_id, global_id=global_id).first()
            if bestaat:
                flash('Dit artikel zit al in je lokale assortiment.', 'warning')
            else:
                nieuw_lokaal = Lokaal_Artikel(
                    bedrijf_id=huidig_bedrijf_id,
                    global_id=global_id,
                    eigen_naam=global_item.generieke_naam, # Neem naam over, kan later gewijzigd
                    verpakkingseenheid_tekst="Stuk" # Default
                )
                db.session.add(nieuw_lokaal)
                db.session.commit()
                flash('Artikel toegevoegd aan lokaal assortiment.', 'success')

        return redirect(url_for('beheer_catalogus'))

    try:
        # Haal alles op. 
        # Left join om te zien of we het artikel lokaal al hebben
        catalogus_items = db.session.query(Global_Catalogus).all()
        # Voor de netheid zouden we hier een left join met Lokaal_Artikel kunnen doen om te tonen "Al in assortiment"
        
        return render_template('beheer_catalogus.html', catalogus=catalogus_items)
    except Exception as e:
        return f"Fout bij laden catalogus: {e}", 500

@app.route('/beheer/infra', methods=['GET', 'POST'])
def beheer_infra():
    """Beheerder: Kamers en Kasten aanmaken."""
    if not check_db(): return render_template('dashboard.html', db_status=False)
    
    huidig_bedrijf_id = 1
    if request.method == 'POST':
        # Simpele implementatie voor demo: alleen Vestiging/Ruimte/Kast aanmaken
        actie = request.form.get('actie')
        if actie == 'nieuwe_ruimte':
            vestiging_id = request.form.get('vestiging_id')
            naam = request.form.get('naam')
            nieuw = Ruimte(bedrijf_id=huidig_bedrijf_id, vestiging_id=vestiging_id, naam=naam, type_ruimte='KAMER')
            db.session.add(nieuw)
            db.session.commit()
            flash('Ruimte aangemaakt', 'success')
        # ... (overige acties analoog)
        return redirect(url_for('beheer_infra'))

    vestigingen = db.session.query(Vestiging).filter_by(bedrijf_id=huidig_bedrijf_id).all()
    ruimtes = db.session.query(Ruimte).filter_by(bedrijf_id=huidig_bedrijf_id).all()
    
    return render_template('beheer_infra.html', vestigingen=vestigingen, ruimtes=ruimtes)

# Oude routes (redirects voor backward compatibility)
@app.route('/artikelen')
def artikelen_redirect(): return redirect(url_for('beheer_catalogus'))

@app.route('/kast-beheer')
def kast_redirect(): return redirect(url_for('assistent_kasten'))

if __name__ == '__main__':
    app.run(debug=True)