import os
import sys
from flask import Flask
from dotenv import load_dotenv

# --- DEBUGGING IMPORTS ---
print("--- STARTING APP ---", flush=True)
try:
    from extensions import db
    print("✓ extensions.py gevonden", flush=True)
except ImportError as e:
    print(f"!!! FOUT: Kan extensions.py niet laden: {e}", flush=True)

try:
    from models import init_db_models
    print("✓ models.py gevonden", flush=True)
except ImportError as e:
    print(f"!!! FOUT: Kan models.py niet laden: {e}", flush=True)

try:
    import routes_general
    print("✓ routes_general.py gevonden", flush=True)
except ImportError as e:
    print(f"!!! FOUT: Kan routes_general.py niet laden: {e}", flush=True)

try:
    import routes_artikelen
    print("✓ routes_artikelen.py gevonden", flush=True)
except ImportError as e:
    print(f"!!! FOUT: Kan routes_artikelen.py niet laden: {e}", flush=True)

try:
    import routes_kasten
    print("✓ routes_kasten.py gevonden", flush=True)
except ImportError as e:
    print(f"!!! FOUT: Kan routes_kasten.py niet laden: {e}", flush=True)

try:
    import routes_beheer
    print("✓ routes_beheer.py gevonden", flush=True)
except ImportError as e:
    print(f"!!! FOUT: Kan routes_beheer.py niet laden: {e}", flush=True)
# -------------------------

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key')

db_server = os.environ.get('DB_SERVER')
db_name = os.environ.get('DB_NAME')
db_user = os.environ.get('DB_USER')
db_pass = os.environ.get('DB_PASS')

driver = 'ODBC+Driver+18+for+SQL+Server'
app.config['SQLALCHEMY_DATABASE_URI'] = f"mssql+pyodbc://{db_user}:{db_pass}@{db_server}/{db_name}?driver={driver}&TrustServerCertificate=yes"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
init_db_models(app)

# Registreer routes alleen als de import gelukt is
if 'routes_general' in sys.modules: app.register_blueprint(routes_general.bp)
if 'routes_artikelen' in sys.modules: app.register_blueprint(routes_artikelen.bp)
if 'routes_kasten' in sys.modules: app.register_blueprint(routes_kasten.bp)
if 'routes_beheer' in sys.modules: app.register_blueprint(routes_beheer.bp)

if __name__ == '__main__':
    app.run(debug=True)