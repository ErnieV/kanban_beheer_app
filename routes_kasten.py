# Bestand: routes_kasten.py
# Functie: Beheer van kasten en voorraadposities (inhoud).

from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import ViewArtikelCompleet, get_model
from helpers import upload_to_blob

bp = Blueprint('kasten', __name__)

@bp.route('/kasten')
def kast_selectie():
    Kast = get_model('Kast')
    Ruimte = get_model('Ruimte')
    Vestiging = get_model('Vestiging')
    
    if not Kast:
        flash("Database fout.", "danger")
        return render_template('base.html')

    kasten = db.session.query(Kast, Ruimte, Vestiging)\
        .join(Ruimte, Kast.ruimte_id == Ruimte.ruimte_id)\
        .join(Vestiging, Ruimte.vestiging_id == Vestiging.vestiging_id)\
        .all()
    return render_template('kast_selectie.html', kasten=kasten)

@bp.route('/kast/<int:kast_id>', methods=['GET', 'POST'])
def kast_inhoud(kast_id):
    Kast = get_model('Kast')
    Voorraad_Positie = get_model('Voorraad_Positie')
    Lokaal_Artikel = get_model('Lokaal_Artikel')
    Global_Catalogus = get_model('Global_Catalogus')
    
    kast = db.session.query(Kast).get_or_404(kast_id)
    bedrijf_id = 1
    
    if request.method == 'POST':
        artikel_id = request.form.get('artikel_id')
        min_val = request.form.get('trigger_min')
        max_val = request.form.get('target_max')
        foto_file = request.files.get('locatie_foto')
        locatie_url = upload_to_blob(foto_file)
        
        nieuw = Voorraad_Positie(
            bedrijf_id=bedrijf_id, kast_id=kast_id, lokaal_artikel_id=artikel_id,
            strategie='TWO_BIN', trigger_min=min_val, target_max=max_val, locatie_foto_url=locatie_url
        )
        db.session.add(nieuw)
        db.session.commit()
        flash('Positie toegevoegd!', 'success')
        return redirect(url_for('kasten.kast_inhoud', kast_id=kast_id))

    inhoud = db.session.query(Voorraad_Positie, Lokaal_Artikel, Global_Catalogus)\
        .join(Lokaal_Artikel, Voorraad_Positie.lokaal_artikel_id == Lokaal_Artikel.lokaal_artikel_id)\
        .outerjoin(Global_Catalogus, Lokaal_Artikel.global_id == Global_Catalogus.global_id)\
        .filter(Voorraad_Positie.kast_id == kast_id).all()
        
    artikelen = ViewArtikelCompleet.query.filter_by(bedrijf_id=bedrijf_id).all()
    
    return render_template('kast_inhoud.html', kast=kast, inhoud=inhoud, artikelen=artikelen)