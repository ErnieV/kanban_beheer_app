# Bestand: routes_beheer.py
# Functie: Beheerportaal voor infrastructuur (CRUD), catalogus en bedrijfsinstellingen.

from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import get_model
from helpers import upload_to_blob

bp = Blueprint('beheer', __name__, url_prefix='/beheer')

@bp.route('/')
def dashboard():
    return render_template('beheer.html')

# --- INFRASTRUCTUUR ---
@bp.route('/infra')
def infra():
    Vestiging = get_model('Vestiging')
    Ruimte = get_model('Ruimte')
    Kast = get_model('Kast')
    
    if not Vestiging: 
        return redirect(url_for('beheer.dashboard'))
    
    return render_template('beheer_infra.html', 
        vestigingen=db.session.query(Vestiging).all(),
        ruimtes=db.session.query(Ruimte).all(),
        kasten=db.session.query(Kast).all()
    )

# Vestiging acties
@bp.route('/vestiging/nieuw', methods=['POST'])
def nieuwe_vestiging():
    Vestiging = get_model('Vestiging')
    db.session.add(Vestiging(bedrijf_id=1, naam=request.form['naam'], adres=request.form.get('adres')))
    db.session.commit()
    flash('Vestiging toegevoegd.', 'success')
    return redirect(url_for('beheer.infra'))

@bp.route('/vestiging/update/<int:id>', methods=['POST'])
def update_vestiging(id):
    Vestiging = get_model('Vestiging')
    v = db.session.query(Vestiging).get(id)
    v.naam = request.form['naam']
    v.adres = request.form.get('adres')
    db.session.commit()
    return redirect(url_for('beheer.infra'))

@bp.route('/vestiging/verwijder/<int:id>', methods=['POST'])
def verwijder_vestiging(id):
    Vestiging = get_model('Vestiging')
    Ruimte = get_model('Ruimte')
    cnt = db.session.query(Ruimte).filter_by(vestiging_id=id).count()
    if cnt > 0:
        flash(f'Kan niet verwijderen: Nog {cnt} ruimtes gekoppeld.', 'danger')
    else:
        db.session.delete(db.session.query(Vestiging).get(id))
        db.session.commit()
        flash('Vestiging verwijderd.', 'success')
    return redirect(url_for('beheer.infra'))

# Ruimte acties (Soortgelijk patroon)
@bp.route('/ruimte/nieuw', methods=['POST'])
def nieuwe_ruimte():
    Ruimte = get_model('Ruimte')
    db.session.add(Ruimte(vestiging_id=request.form['vestiging_id'], naam=request.form['naam']))
    db.session.commit()
    return redirect(url_for('beheer.infra'))

@bp.route('/ruimte/update/<int:id>', methods=['POST'])
def update_ruimte(id):
    Ruimte = get_model('Ruimte')
    r = db.session.query(Ruimte).get(id)
    r.naam = request.form['naam']
    db.session.commit()
    return redirect(url_for('beheer.infra'))

@bp.route('/ruimte/verwijder/<int:id>', methods=['POST'])
def verwijder_ruimte(id):
    Ruimte = get_model('Ruimte')
    Kast = get_model('Kast')
    cnt = db.session.query(Kast).filter_by(ruimte_id=id).count()
    if cnt > 0:
        flash(f'Nog {cnt} kasten aanwezig.', 'danger')
    else:
        db.session.delete(db.session.query(Ruimte).get(id))
        db.session.commit()
    return redirect(url_for('beheer.infra'))

# Kast acties
@bp.route('/kast/nieuw', methods=['POST'])
def nieuwe_kast():
    Kast = get_model('Kast')
    db.session.add(Kast(ruimte_id=request.form['ruimte_id'], naam=request.form['naam'], type_opslag=request.form.get('type_opslag')))
    db.session.commit()
    return redirect(url_for('beheer.infra'))

@bp.route('/kast/update/<int:id>', methods=['POST'])
def update_kast(id):
    Kast = get_model('Kast')
    k = db.session.query(Kast).get(id)
    k.naam = request.form['naam']
    k.type_opslag = request.form.get('type_opslag')
    db.session.commit()
    return redirect(url_for('beheer.infra'))

@bp.route('/kast/verwijder/<int:id>', methods=['POST'])
def verwijder_kast(id):
    Kast = get_model('Kast')
    Voorraad_Positie = get_model('Voorraad_Positie')
    cnt = db.session.query(Voorraad_Positie).filter_by(kast_id=id).count()
    if cnt > 0:
        flash(f'Kast is niet leeg ({cnt} items).', 'danger')
    else:
        db.session.delete(db.session.query(Kast).get(id))
        db.session.commit()
    return redirect(url_for('beheer.infra'))

# --- CATALOGUS ---
@bp.route('/catalogus')
def catalogus():
    Global_Catalogus = get_model('Global_Catalogus')
    return render_template('beheer_catalogus.html', items=db.session.query(Global_Catalogus).all())

@bp.route('/catalogus/nieuw', methods=['POST'])
def nieuw_global_item():
    Global_Catalogus = get_model('Global_Catalogus')
    foto_url = upload_to_blob(request.files.get('afbeelding'))
    db.session.add(Global_Catalogus(generieke_naam=request.form['generieke_naam'], ean_code=request.form['ean_code'], foto_url=foto_url))
    db.session.commit()
    return redirect(url_for('beheer.catalogus'))

# --- BEDRIJF ---
@bp.route('/bedrijf', methods=['GET', 'POST'])
def bedrijf():
    Bedrijf = get_model('Bedrijf')
    bedrijf = db.session.query(Bedrijf).get(1)
    
    if request.method == 'POST':
        if not bedrijf:
            bedrijf = Bedrijf(naam="Nieuw", is_actief=True)
            db.session.add(bedrijf)
        
        bedrijf.naam = request.form.get('naam')
        bedrijf.kvk_nummer = request.form.get('kvk_nummer')
        logo = request.files.get('logo')
        if logo:
            url = upload_to_blob(logo)
            if url: bedrijf.logo_url = url
            
        db.session.commit()
        flash('Bedrijfsgegevens opgeslagen.', 'success')
        
    return render_template('beheer_bedrijf.html', bedrijf=bedrijf)