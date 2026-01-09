# Bestand: app.py
# Functie: Hoofdbestand dat de applicatie start, configuratie laadt en modules koppelt.

import os
from flask import Flask
from dotenv import load_dotenv

# Importeer onze modules
from extensions import db
from models import init_db_models
import routes_general
import routes_artikelen
import routes_kasten
import routes_beheer

# Laad omgevingsvariabelen
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key')

# --- CONFIGURATIE ---
db_server = os.environ.get('DB_SERVER')
db_name = os.environ.get('DB_NAME')
db_user = os.environ.get('DB_USER')
db_pass = os.environ.get('DB_PASS')

driver = 'ODBC+Driver+18+for+SQL+Server'
app.config['SQLALCHEMY_DATABASE_URI'] = f"mssql+pyodbc://{db_user}:{db_pass}@{db_server}/{db_name}?driver={driver}&TrustServerCertificate=yes"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 1. Initialiseer de database extensie
db.init_app(app)

# 2. Laad de modellen (Reflectie)
init_db_models(app)

# 3. Registreer de Routes (Blueprints)
app.register_blueprint(routes_general.bp)
app.register_blueprint(routes_artikelen.bp)
app.register_blueprint(routes_kasten.bp)
app.register_blueprint(routes_beheer.bp)

if __name__ == '__main__':
    app.run(debug=True)