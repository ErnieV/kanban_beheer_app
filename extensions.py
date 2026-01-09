# Bestand: extensions.py
# Functie: Centrale plek voor Flask-extensies (voorkomt circulaire imports).

from flask_sqlalchemy import SQLAlchemy

# We maken het db object hier aan, maar koppelen het later pas aan de app
db = SQLAlchemy()