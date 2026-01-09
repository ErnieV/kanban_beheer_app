# Bestand: routes_general.py
# Functie: Algemene routes zoals het dashboard en context processors.

from flask import Blueprint, render_template
from extensions import db
from models import get_model

# Maak een 'Blueprint' (een groepje routes)
bp = Blueprint('general', __name__)

@bp.route('/')
def dashboard():
    return render_template('base.html')

# Context Processor: Zorgt dat 'huidig_bedrijf' overal beschikbaar is in templates
@bp.app_context_processor
def inject_bedrijf_data():
    Bedrijf = get_model('Bedrijf')
    if not Bedrijf:
        return dict(huidig_bedrijf=None)
    
    # Hardcoded ID 1 voor nu (later uit sessie halen)
    bedrijf_id = 1 
    bedrijf = db.session.query(Bedrijf).get(bedrijf_id)
    return dict(huidig_bedrijf=bedrijf)