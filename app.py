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

# --- AUTOMAP ---
Base = automap_base()

# Probeer verbinding te maken en tabellen te laden
with app.app_context():
    try:
        Base.prepare(db.engine, reflect=True)
        
        # Tabellen laden en beschikbaar maken als classes
        Global_Catalogus = Base.classes.Global_Catalogus
        Lokaal_Artikel = Base.classes.Lokaal_Artikel
        Voorraad_Positie = Base.classes.Voorraad_Positie
        Bedrijf = Base.classes.Bedrijf
        Vestiging = Base.classes.Vestiging
        Ruimte = Base.classes.Ruimte
        Kast = Base.classes.Kast
        Leverancier = Base.classes.Leverancier
        
        print("Database succesvol verbonden en tabellen geladen.")
    except Exception as e:
        print(f"CRITIQUE DB ERROR: {e}")
        # Dummy classes voor fallback tijdens startup crashes
        class Dummy: pass
        Global_Catalogus = Lokaal_Artikel = Voorraad_Positie = Bedrijf = Vestiging = Ruimte = Kast = Leverancier = Dummy

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
def home():
    return redirect(url_for('artikelen_lijst'))

# === ARTIKELEN BEHEER ===

@app.route('/artikelen', methods=['GET', 'POST'])
def artikelen_lijst():
    huidig_bedrijf_id = 1 
    
    if request.method == 'POST':
        if 'actie' in request.form and request.form['actie'] == 'update_foto':
            # Foto update bestaand artikel
            artikel_id = request.form.get('artikel_id')
            file = request.files.get('afbeelding')
            
            if file:
                url = upload_image_to_azure(file)
                if url and "ERROR" not in str(url):
                    artikel = db.session.query(Lokaal_Artikel).get(artikel_id)
                    artikel.foto_url = url
                    db.session.commit()
                    flash('Lokale artikel foto bijgewerkt', 'success')
                else:
                    flash('Fout bij uploaden', 'danger')
            return redirect(url_for('artikelen_lijst'))

        else:
            # Nieuw artikel aanmaken
            naam = request.form.get('naam')
            eenheid = request.form.get('eenheid')
            file = request.files.get('afbeelding') 

            image_url = None
            if file:
                result = upload_image_to_azure(file)
                if result and "ERROR" in str(result):
                    flash('Fout bij uploaden (alleen .png).', 'danger')
                    return redirect(url_for('artikelen_lijst'))
                image_url = result

            nieuw_global = Global_Catalogus(
                generieke_naam=naam,
                foto_url=image_url,
                categorie='Algemeen'
            )
            db.session.add(nieuw_global)
            db.session.flush()

            nieuw_artikel = Lokaal_Artikel(
                bedrijf_id=huidig_bedrijf_id,
                global_id=nieuw_global.global_id,
                eigen_naam=naam,
                verpakkingseenheid_tekst=eenheid,
                foto_url=None
            )
            db.session.add(nieuw_artikel)
            db.session.commit()
            
            flash('Artikel aangemaakt', 'success')
            return redirect(url_for('artikelen_lijst'))

    try:
        artikelen = db.session.query(Lokaal_Artikel, Global_Catalogus)\
            .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
            .filter(Lokaal_Artikel.bedrijf_id == huidig_bedrijf_id)\
            .order_by(Lokaal_Artikel.eigen_naam).all()
        return render_template('artikelen.html', artikelen=artikelen)
    except Exception as e:
        return f"Database Error: {e}", 500

# === KASTEN INRICHTEN ===

@app.route('/kast-beheer', methods=['GET'])
def kast_selectie():
    huidig_bedrijf_id = 1
    try:
        kasten = db.session.query(Kast, Ruimte, Vestiging)\
            .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
            .join(Vestiging, Ruimte.vestiging_id == Vestiging.vestiging_id)\
            .filter(Kast.bedrijf_id == huidig_bedrijf_id)\
            .order_by(Vestiging.naam, Ruimte.naam, Kast.naam).all()
        return render_template('kast_selectie.html', kasten=kasten)
    except Exception as e:
        return f"Error: {e}", 500

@app.route('/kast-beheer/<int:kast_id>', methods=['GET', 'POST'])
def kast_inhoud(kast_id):
    huidig_bedrijf_id = 1
    gekozen_kast = db.session.query(Kast).get(kast_id)
    
    if request.method == 'POST':
        artikel_id = request.form.get('artikel_id')
        min_val = request.form.get('trigger_min')
        max_val = request.form.get('target_max')
        locatie_file = request.files.get('locatie_foto')

        locatie_url = None
        if locatie_file:
            result = upload_image_to_azure(locatie_file)
            if result and "ERROR" not in str(result):
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
                locatie_foto_url=locatie_url
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

# === BEHEER PORTAAL (Toegevoegd om functionaliteit te behouden) ===

@app.route('/beheer')
def beheer_index():
    return render_template('beheer.html')

@app.route('/beheer/bedrijf', methods=['GET', 'POST'])
def beheer_bedrijf():
    huidig_bedrijf_id = 1
    bedrijf = db.session.query(Bedrijf).get(huidig_bedrijf_id)
    
    if request.method == 'POST':
        bedrijf.naam = request.form.get('naam')
        bedrijf.kvk_nummer = request.form.get('kvk')
        db.session.commit()
        flash('Bedrijfsgegevens bijgewerkt', 'success')
        return redirect(url_for('beheer_bedrijf'))
        
    return render_template('beheer_bedrijf.html', bedrijf=bedrijf)

@app.route('/beheer/infra', methods=['GET', 'POST'])
def beheer_infra():
    huidig_bedrijf_id = 1
    
    if request.method == 'POST':
        actie = request.form.get('actie')
        
        if actie == 'nieuwe_vestiging':
            naam = request.form.get('naam')
            adres = request.form.get('adres')
            nieuw = Vestiging(bedrijf_id=huidig_bedrijf_id, naam=naam, adres=adres)
            db.session.add(nieuw)
            
        elif actie == 'nieuwe_ruimte':
            vestiging_id = request.form.get('vestiging_id')
            naam = request.form.get('naam')
            type_ruimte = request.form.get('type_ruimte')
            nieuw = Ruimte(bedrijf_id=huidig_bedrijf_id, vestiging_id=vestiging_id, naam=naam, type_ruimte=type_ruimte)
            db.session.add(nieuw)
            
        elif actie == 'nieuwe_kast':
            ruimte_id = request.form.get('ruimte_id')
            naam = request.form.get('naam')
            type_opslag = request.form.get('type_opslag')
            nieuw = Kast(bedrijf_id=huidig_bedrijf_id, ruimte_id=ruimte_id, naam=naam, type_opslag=type_opslag)
            db.session.add(nieuw)
            
        db.session.commit()
        flash('Item toegevoegd!', 'success')
        return redirect(url_for('beheer_infra'))

    # Haal alles op voor weergave
    vestigingen = db.session.query(Vestiging).filter_by(bedrijf_id=huidig_bedrijf_id).all()
    ruimtes = db.session.query(Ruimte).filter_by(bedrijf_id=huidig_bedrijf_id).all()
    # We sturen alles mee naar de template
    return render_template('beheer_infra.html', vestigingen=vestigingen, ruimtes=ruimtes)

@app.route('/beheer/catalogus', methods=['GET', 'POST'])
def beheer_catalogus():
    huidig_bedrijf_id = 1
    
    if request.method == 'POST':
        actie = request.form.get('actie')
        
        if actie == 'nieuwe_leverancier':
            naam = request.form.get('naam')
            email = request.form.get('email')
            nieuw = Leverancier(bedrijf_id=huidig_bedrijf_id, naam=naam, bestel_email=email)
            db.session.add(nieuw)
            db.session.commit()
            flash('Leverancier toegevoegd', 'success')
            
        elif actie == 'nieuw_global_artikel':
            naam = request.form.get('naam')
            ean = request.form.get('ean')
            cat = request.form.get('categorie')
            nieuw = Global_Catalogus(generieke_naam=naam, ean_code=ean, categorie=cat)
            db.session.add(nieuw)
            db.session.commit()
            flash('Item aan globale catalogus toegevoegd', 'success')
            
        return redirect(url_for('beheer_catalogus'))

    globals = db.session.query(Global_Catalogus).all()
    leveranciers = db.session.query(Leverancier).filter_by(bedrijf_id=huidig_bedrijf_id).all()
    return render_template('beheer_catalogus.html', globals=globals, leveranciers=leveranciers)

if __name__ == '__main__':
    app.run(debug=True)