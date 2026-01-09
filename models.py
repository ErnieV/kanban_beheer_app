# Bestand: models.py
# Functie: Definieert datamodellen en regelt de automatische reflectie van tabellen.

from sqlalchemy.ext.automap import automap_base
from extensions import db

Base = automap_base()

# Definieer de View handmatig (omdat views geen Primary Key hebben voor Automap)
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

def init_db_models(app):
    """Reflecteer de database tabellen nadat de app is gestart."""
    with app.app_context():
        try:
            Base.prepare(db.engine, reflect=True)
            print("Database tabellen succesvol geladen.")
        except Exception as e:
            print(f"KRITIEKE FOUT bij laden database modellen: {e}")

def get_model(model_name):
    """Veilige manier om een model op te vragen uit de Base classes."""
    return Base.classes.get(model_name)