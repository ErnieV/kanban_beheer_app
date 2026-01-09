import os
import uuid
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

driver = 'ODBC+Driver+18+for+SQL+Server'
app.config['SQLALCHEMY_DATABASE_URI'] = f"mssql+pyodbc://{db_user}:{db_pass}@{db_server}/{db_name}?driver={driver}&TrustServerCertificate=yes"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- AUTOMAP ---
Base = automap_base()

# We doen dit NIET in een try-except blok. 
# Als de DB niet bereikbaar is, MOET de app crashen zodat we de foutmelding zien in Azure Logs.
with app.app_context():
    Base.prepare(db.engine, reflect=True)
    
    # Tabellen laden
    Global_Catalogus = Base.classes.Global_Catalogus
    Lokaal_Artikel = Base.classes.Lokaal_Artikel
    Voorraad_Positie = Base.classes.Voorraad_Positie
    Bedrijf = Base.classes.Bedrijf
    Vestiging = Base.classes.Vestiging
    Ruimte = Base.classes.Ruimte
    Kast = Base.classes.Kast

# --- HELPER FUNCTIES ---

def upload_image_to_azure(file):
    """Uploadt een bestand naar Azure Blob en geeft de URL terug."""
    if not file or file.filename == '':
        return None
    
    if not file.filename.lower().endswith('.png'):
        return "ERROR_TYPE"

    try:
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}-{filename}"

        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=unique_filename)
        blob_client.upload_blob(file)

        return blob_client.url
    except Exception as e:
        print(f"Upload error: {e}")
        return "ERROR_UPLOAD"

# --- ROUTES ---

@app.route('/')
def home():
    return redirect(url_for('artikelen_lijst'))

@app.route('/artikelen', methods=['GET', 'POST'])
def artikelen_lijst():
    huidig_bedrijf_id = 1 
    
    if request.method == 'POST':
        # Check welk formulier is ingediend
        if 'actie' in request.form and request.form['actie'] == 'update_foto':
            # === Scenario: Bestaand artikel foto updaten (Lokaal Override) ===
            artikel_id = request.form.get('artikel_id')
            file = request.files.get('afbeelding')
            
            if file:
                url = upload_image_to_azure(file)
                if "ERROR" not in str(url):
                    artikel = db.session.query(Lokaal_Artikel).get(artikel_id)
                    artikel.foto_url = url # Sla op in LOKAAL artikel
                    db.session.commit()
                    flash('Lokale artikel foto bijgewerkt', 'success')
                else:
                    flash('Fout bij uploaden', 'danger')
            return redirect(url_for('artikelen_lijst'))

        else:
            # === Scenario: Nieuw Artikel Aanmaken ===
            naam = request.form.get('naam')
            eenheid = request.form.get('eenheid')
            file = request.files.get('afbeelding') 

            # 1. Upload Productfoto (Indien aanwezig) -> Dit wordt de GLOBAL default
            image_url = None
            if file:
                result = upload_image_to_azure(file)
                if "ERROR" in str(result):
                    flash('Fout bij uploaden (alleen .png).', 'danger')
                    return redirect(url_for('artikelen_lijst'))
                image_url = result

            # 2. Maak Global Catalogus item aan
            nieuw_global = Global_Catalogus(
                generieke_naam=naam,
                foto_url=image_url, # Global foto
                categorie='Algemeen'
            )
            db.session.add(nieuw_global)
            db.session.flush()

            # 3. Maak Lokaal Artikel aan
            nieuw_artikel = Lokaal_Artikel(
                bedrijf_id=huidig_bedrijf_id,
                global_id=nieuw_global.global_id,
                eigen_naam=naam,
                verpakkingseenheid_tekst=eenheid,
                foto_url=None # Nog geen lokale override bij aanmaken
            )
            db.session.add(nieuw_artikel)
            db.session.commit()
            
            flash('Artikel aangemaakt (Global + Lokaal)', 'success')
            return redirect(url_for('artikelen_lijst'))

    # Haal artikelen op met global info
    artikelen = db.session.query(Lokaal_Artikel, Global_Catalogus)\
        .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
        .filter(Lokaal_Artikel.bedrijf_id == huidig_bedrijf_id)\
        .order_by(Lokaal_Artikel.eigen_naam).all()
        
    return render_template('artikelen.html', artikelen=artikelen)

@app.route('/kast-beheer', methods=['GET'])
def kast_selectie():
    huidig_bedrijf_id = 1
    kasten = db.session.query(Kast, Ruimte, Vestiging)\
        .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
        .join(Vestiging, Ruimte.vestiging_id == Vestiging.vestiging_id)\
        .filter(Kast.bedrijf_id == huidig_bedrijf_id)\
        .order_by(Vestiging.naam, Ruimte.naam, Kast.naam).all()
    return render_template('kast_selectie.html', kasten=kasten)

@app.route('/kast-beheer/<int:kast_id>', methods=['GET', 'POST'])
def kast_inhoud(kast_id):
    huidig_bedrijf_id = 1
    gekozen_kast = db.session.query(Kast).get(kast_id)
    
    if request.method == 'POST':
        artikel_id = request.form.get('artikel_id')
        min_val = request.form.get('trigger_min')
        max_val = request.form.get('target_max')
        locatie_file = request.files.get('locatie_foto')

        # Upload Locatiefoto (Kamer specifiek)
        locatie_url = None
        if locatie_file:
            result = upload_image_to_azure(locatie_file)
            if "ERROR" not in str(result):
                locatie_url = result

        bestaat_al = db.session.query(Voorraad_Positie).filter_by(kast_id=kast_id, lokaal_artikel_id=artikel_id).first()
        
        if bestaat_al:
            flash('Dit artikel ligt al op deze kar!', 'danger')
        else:
            nieuwe_positie = Voorraad_Positie(
                bedrijf_id=huidig_bedrijf_id,
                kast_id=kast_id,
                lokaal_artikel_id=artikel_id,
                strategie='TWO_BIN',
                trigger_min=min_val,
                target_max=max_val,
                locatie_foto_url=locatie_url # Specifieke foto voor deze positie
            )
            db.session.add(nieuwe_positie)
            db.session.commit()
            flash('Artikel aan kast toegevoegd!', 'success')
        
        return redirect(url_for('kast_inhoud', kast_id=kast_id))

    inhoud = db.session.query(Voorraad_Positie, Lokaal_Artikel, Global_Catalogus)\
        .join(Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id)\
        .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
        .filter(Voorraad_Positie.kast_id == kast_id).all()
        
    alle_artikelen = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=huidig_bedrijf_id).order_by(Lokaal_Artikel.eigen_naam).all()
    return render_template('kast_inhoud.html', kast=gekozen_kast, inhoud=inhoud, artikelen=alle_artikelen)

if __name__ == '__main__':
    app.run(debug=True)