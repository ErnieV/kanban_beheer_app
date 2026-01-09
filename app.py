import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.automap import automap_base
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from azure.storage.blob import BlobServiceClient

# Laad variabelen uit .env bestand
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key')

# --- CONFIGURATIE ---
db_server = os.environ.get('DB_SERVER')
db_name = os.environ.get('DB_NAME')
db_user = os.environ.get('DB_USER')
db_pass = os.environ.get('DB_PASS')

# Azure Blob Storage Config
connect_str = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
container_name = os.environ.get('AZURE_CONTAINER_NAME')

driver = 'ODBC+Driver+18+for+SQL+Server'
# Ensure connection string is correct
app.config['SQLALCHEMY_DATABASE_URI'] = f"mssql+pyodbc://{db_user}:{db_pass}@{db_server}/{db_name}?driver={driver}&TrustServerCertificate=yes"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- MODEL DEFINITIE ---
Base = automap_base()

# 1. Definieer de View Model (Onze nieuwe slimme lees-laag)
# Views have no primary key, so we must define this manually for SQLAlchemy to work
class ViewArtikelCompleet(db.Model):
    __tablename__ = 'vw_Artikel_Compleet'
    lokaal_artikel_id = db.Column(db.Integer, primary_key=True)
    bedrijf_id = db.Column(db.Integer)
    global_id = db.Column(db.Integer)
    Display_Naam = db.Column(db.String)
    Display_Foto = db.Column(db.String)
    Display_Nummer = db.Column(db.String)
    Display_Prijs = db.Column(db.Numeric)
    Display_Verpakking = db.Column(db.String)
    Is_Global_Linked = db.Column(db.Boolean)
    Is_Naam_Gewijzigd = db.Column(db.Boolean)
    Is_Quick_Add = db.Column(db.Boolean)

# Initialize global variables for models
Global_Catalogus = None
Lokaal_Artikel = None
Bedrijf = None
Kast = None
Ruimte = None
Vestiging = None
Voorraad_Positie = None

# 2. Reflecteer de bestaande tabellen (Automap)
with app.app_context():
    try:
        # Reflect database tables
        Base.prepare(db.engine, reflect=True)
        
        # Assign classes to global variables
        # We use .get() to avoid crashing if a table is missing, but check afterwards
        Global_Catalogus = Base.classes.get('Global_Catalogus')
        Lokaal_Artikel = Base.classes.get('Lokaal_Artikel')
        Bedrijf = Base.classes.get('Bedrijf')
        Kast = Base.classes.get('Kast')
        Ruimte = Base.classes.get('Ruimte')
        Vestiging = Base.classes.get('Vestiging')
        Voorraad_Positie = Base.classes.get('Voorraad_Positie')

        # Log loaded tables for debugging
        print("Tables loaded successfully:")
        print(f"- Global_Catalogus: {'OK' if Global_Catalogus else 'MISSING'}")
        print(f"- Lokaal_Artikel: {'OK' if Lokaal_Artikel else 'MISSING'}")
        print(f"- Kast: {'OK' if Kast else 'MISSING'}")

    except Exception as e:
        print(f"CRITICAL ERROR LOADING DATABASE TABLES: {e}")

# --- HULPFUNCTIES (Azure) ---
def upload_to_blob(file):
    if not file or file.filename == '':
        return None
    try:
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        filename = secure_filename(str(uuid.uuid4()) + "_" + file.filename)
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=filename)
        blob_client.upload_blob(file)
        return blob_client.url
    except Exception as e:
        print(f"Azure Upload Error: {e}")
        return None

# --- ROUTES: DASHBOARD & BEHEER ---

@app.route('/')
def dashboard():
    return render_template('base.html')

@app.route('/beheer')
def beheer_dashboard():
    """Centraal portaal voor beheertaken."""
    return render_template('beheer.html')

# --- ROUTES: INFRASTRUCTUUR (Vestiging -> Ruimte -> Kast) ---

@app.route('/beheer/infra')
def beheer_infra():
    if not Vestiging or not Ruimte or not Kast:
        flash("Database tabellen niet geladen.", "danger")
        return redirect(url_for('beheer_dashboard'))
    
    # Haal alles op. 
    # Let op: Bij automap zijn relaties soms lastig direct te benaderen in Jinja 
    # zonder expliciete configuratie. We halen lijsten los op of gebruiken 
    # een simpele join logica in de template (of pre-processen in Python).
    # Hier kiezen we voor het ophalen van de Vestigingen en laten de relaties aan SQLAlchemy over
    # mits automap ze herkend heeft (meestal vestiging.ruimte_collection).
    # Voor zekerheid sturen we losse lijsten mee als fallback of voor selects.
    
    vestigingen = db.session.query(Vestiging).all()
    ruimtes = db.session.query(Ruimte).all()
    kasten = db.session.query(Kast).all()
    
    return render_template('beheer_infra.html', 
                           vestigingen=vestigingen, 
                           ruimtes=ruimtes, 
                           kasten=kasten)

@app.route('/beheer/vestiging/nieuw', methods=['POST'])
def nieuwe_vestiging():
    naam = request.form.get('naam')
    adres = request.form.get('adres')
    bedrijf_id = 1 # Hardcoded voor nu
    
    if naam:
        nieuw = Vestiging(bedrijf_id=bedrijf_id, naam=naam, adres=adres)
        db.session.add(nieuw)
        db.session.commit()
        flash(f"Vestiging '{naam}' aangemaakt.", "success")
    return redirect(url_for('beheer_infra'))

@app.route('/beheer/ruimte/nieuw', methods=['POST'])
def nieuwe_ruimte():
    naam = request.form.get('naam')
    vestiging_id = request.form.get('vestiging_id')
    
    if naam and vestiging_id:
        nieuw = Ruimte(vestiging_id=vestiging_id, naam=naam)
        db.session.add(nieuw)
        db.session.commit()
        flash(f"Ruimte '{naam}' aangemaakt.", "success")
    return redirect(url_for('beheer_infra'))

@app.route('/beheer/kast/nieuw', methods=['POST'])
def nieuwe_kast():
    naam = request.form.get('naam')
    ruimte_id = request.form.get('ruimte_id')
    type_opslag = request.form.get('type_opslag', 'Stellingkast')
    
    if naam and ruimte_id:
        nieuw = Kast(ruimte_id=ruimte_id, naam=naam, type_opslag=type_opslag)
        db.session.add(nieuw)
        db.session.commit()
        flash(f"Kast '{naam}' aangemaakt.", "success")
    return redirect(url_for('beheer_infra'))


# --- ROUTES: GLOBAL CATALOGUS BEHEER ---

@app.route('/beheer/catalogus')
def beheer_catalogus():
    if not Global_Catalogus:
        flash("Tabel Global_Catalogus niet geladen.", "danger")
        return redirect(url_for('beheer_dashboard'))
        
    items = db.session.query(Global_Catalogus).all()
    return render_template('beheer_catalogus.html', items=items)

@app.route('/beheer/catalogus/nieuw', methods=['POST'])
def nieuw_global_item():
    naam = request.form.get('generieke_naam')
    ean = request.form.get('ean_code')
    foto_file = request.files.get('afbeelding')
    
    if naam:
        foto_url = upload_to_blob(foto_file)
        
        nieuw = Global_Catalogus(
            generieke_naam=naam,
            ean_code=ean,
            foto_url=foto_url
        )
        db.session.add(nieuw)
        db.session.commit()
        flash(f"Global item '{naam}' toegevoegd.", "success")
    
    return redirect(url_for('beheer_catalogus'))


# --- ROUTES: ARTIKELEN (Gebruiker) ---

@app.route('/mijn_artikelen')
def mijn_artikelen():
    if not ViewArtikelCompleet:
        flash("Database view niet geladen.", "danger")
        return render_template('base.html')

    bedrijf_id = 1
    artikelen = ViewArtikelCompleet.query.filter_by(bedrijf_id=bedrijf_id).all()
    return render_template('mijn_artikelen.html', artikelen=artikelen)

@app.route('/catalogus')
def catalogus():
    if not Global_Catalogus or not Lokaal_Artikel:
        flash("Database tabellen niet geladen. Controleer logs.", "danger")
        return redirect(url_for('dashboard'))

    bedrijf_id = 1
    try:
        alle_globals = db.session.query(Global_Catalogus).all()
        
        reeds_in_bezit = db.session.query(Lokaal_Artikel.global_id).filter_by(bedrijf_id=bedrijf_id).all()
        reeds_in_bezit_ids = [item.global_id for item in reeds_in_bezit]
        
        return render_template('catalogus.html', globals=alle_globals, reeds_in_bezit=reeds_in_bezit_ids)
    except Exception as e:
        print(f"Error in catalogus route: {e}")
        flash(f"Fout bij ophalen catalogus: {str(e)}", "danger")
        return redirect(url_for('dashboard'))

@app.route('/catalogus/quick_add/<int:global_id>', methods=['POST'])
def quick_add(global_id):
    if not Lokaal_Artikel:
        flash("Database fout.", "danger")
        return redirect(url_for('dashboard'))
        
    bedrijf_id = 1
    bestaat = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id, global_id=global_id).first()
    
    if not bestaat:
        nieuw = Lokaal_Artikel(
            bedrijf_id=bedrijf_id,
            global_id=global_id,
            eigen_naam=None,
            foto_url=None,
            lev_artikel_nummer=None,
            inkoopprijs=None
        )
        db.session.add(nieuw)
        db.session.commit()
        flash('Artikel toegevoegd aan assortiment.', 'success')
    
    return redirect(url_for('mijn_artikelen'))

@app.route('/artikel/bewerken/<int:lokaal_id>', methods=['GET', 'POST'])
def artikel_bewerken(lokaal_id):
    if not Lokaal_Artikel:
        flash("Database fout.", "danger")
        return redirect(url_for('dashboard'))

    # Use db.session.query for automapped classes to be safe
    artikel = db.session.query(Lokaal_Artikel).get(lokaal_id)
    if not artikel:
        flash("Artikel niet gevonden", "danger")
        return redirect(url_for('mijn_artikelen'))
        
    view_data = ViewArtikelCompleet.query.get(lokaal_id)
    
    if request.method == 'POST':
        naam = request.form.get('eigen_naam', '').strip()
        artikel.eigen_naam = naam if naam else None
        
        nr = request.form.get('lev_artikel_nummer', '').strip()
        artikel.lev_artikel_nummer = nr if nr else None
        
        prijs = request.form.get('inkoopprijs', '').strip()
        artikel.inkoopprijs = float(prijs.replace(',', '.')) if prijs else None
        
        db.session.commit()
        flash('Artikel gewijzigd', 'success')
        return redirect(url_for('mijn_artikelen'))
        
    return render_template('artikel_bewerken.html', artikel=artikel, view=view_data)

# --- ROUTES: KASTEN & VOORRAAD ---

@app.route('/kasten')
def kast_selectie():
    if not Kast:
        flash("Database fout: Kasten tabel niet geladen.", "danger")
        return render_template('base.html')

    kasten = db.session.query(Kast, Ruimte, Vestiging)\
        .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
        .join(Vestiging, Ruimte.vestiging_id == Vestiging.vestiging_id)\
        .all()
    return render_template('kast_selectie.html', kasten=kasten)

@app.route('/kast/<int:kast_id>', methods=['GET', 'POST'])
def kast_inhoud(kast_id):
    if not Kast or not Voorraad_Positie:
         flash("Database fout.", "danger")
         return redirect(url_for('kast_selectie'))

    bedrijf_id = 1
    kast = db.session.query(Kast).get_or_404(kast_id)
    
    if request.method == 'POST':
        artikel_id = request.form.get('artikel_id')
        min_val = request.form.get('trigger_min')
        max_val = request.form.get('target_max')
        foto_file = request.files.get('locatie_foto')
        
        locatie_url = upload_to_blob(foto_file)
        
        bestaat = db.session.query(Voorraad_Positie).filter_by(kast_id=kast_id, lokaal_artikel_id=artikel_id).first()
        if not bestaat:
            nieuw = Voorraad_Positie(
                bedrijf_id=bedrijf_id,
                kast_id=kast_id,
                lokaal_artikel_id=artikel_id,
                strategie='TWO_BIN',
                trigger_min=min_val,
                target_max=max_val,
                locatie_foto_url=locatie_url
            )
            db.session.add(nieuw)
            db.session.commit()
            flash('Positie toegevoegd!', 'success')
        else:
            flash('Dit artikel ligt al in deze kast.', 'warning')
            
        return redirect(url_for('kast_inhoud', kast_id=kast_id))

    inhoud = db.session.query(Voorraad_Positie, Lokaal_Artikel, Global_Catalogus)\
        .join(Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id)\
        .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
        .filter(Voorraad_Positie.kast_id == kast_id).all()
        
    alle_artikelen = ViewArtikelCompleet.query.filter_by(bedrijf_id=bedrijf_id).all()
    
    return render_template('kast_inhoud.html', 
                           kast=kast, 
                           inhoud=inhoud, 
                           artikelen=alle_artikelen)

if __name__ == '__main__':
    app.run(debug=True)