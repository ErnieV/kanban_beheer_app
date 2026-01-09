import os
from flask import Flask
from dotenv import load_dotenv

# Import extensions and models
# Ensure these files (extensions.py, models.py) exist in the same directory
from extensions import db
from models import init_db_models

# Import blueprints
# Ensure these files (routes_general.py, etc.) exist
from routes_general import bp as general_bp
from routes_artikelen import bp as artikelen_bp
from routes_kasten import bp as kasten_bp
from routes_beheer import bp as beheer_bp

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key')

# --- CONFIGURATION ---
db_server = os.environ.get('DB_SERVER')
db_name = os.environ.get('DB_NAME')
db_user = os.environ.get('DB_USER')
db_pass = os.environ.get('DB_PASS')

driver = 'ODBC+Driver+18+for+SQL+Server'
app.config['SQLALCHEMY_DATABASE_URI'] = f"mssql+pyodbc://{db_user}:{db_pass}@{db_server}/{db_name}?driver={driver}&TrustServerCertificate=yes"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 1. Initialize database extension
db.init_app(app)

# 2. Load models (Reflection)
init_db_models(app)

# 3. Register Blueprints
app.register_blueprint(general_bp)
app.register_blueprint(artikelen_bp)
app.register_blueprint(kasten_bp)
app.register_blueprint(beheer_bp)

if __name__ == '__main__':
    app.run(debug=True)