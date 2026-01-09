# Bestand: routes_artikelen.py
# Functie: Beheer van lokale artikelen en de catalogus.

from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import ViewArtikelCompleet, get_model

bp = Blueprint('artikelen', __name__)

@bp.route('/mijn_artikelen')
def mijn_artikelen():
    bedrijf_id = 1
    artikelen = ViewArtikelCompleet.query.filter_by(bedrijf_id=bedrijf_id).all()
    return render_template('mijn_artikelen.html', artikelen=artikelen)

@bp.route('/catalogus')
def catalogus():
    Global_Catalogus = get_model('Global_Catalogus')
    Lokaal_Artikel = get_model('Lokaal_Artikel')
    
    if not Global_Catalogus or not Lokaal_Artikel:
        flash("Database fout: Tabellen niet geladen.", "danger")
        return redirect(url_for('general.dashboard'))

    bedrijf_id = 1
    try:
        alle_globals = db.session.query(Global_Catalogus).all()
        reeds_in_bezit = db.session.query(Lokaal_Artikel.global_id).filter_by(bedrijf_id=bedrijf_id).all()
        reeds_in_bezit_ids = [item.global_id for item in reeds_in_bezit]
        
        return render_template('catalogus.html', globals=alle_globals, reeds_in_bezit=reeds_in_bezit_ids)
    except Exception as e:
        flash(f"Fout: {e}", "danger")
        return redirect(url_for('general.dashboard'))

@bp.route('/catalogus/quick_add/<int:global_id>', methods=['POST'])
def quick_add(global_id):
    Lokaal_Artikel = get_model('Lokaal_Artikel')
    bedrijf_id = 1
    
    bestaat = db.session.query(Lokaal_Artikel).filter_by(bedrijf_id=bedrijf_id, global_id=global_id).first()
    if not bestaat:
        nieuw = Lokaal_Artikel(
            bedrijf_id=bedrijf_id, global_id=global_id, 
            eigen_naam=None, foto_url=None, lev_artikel_nummer=None, inkoopprijs=None
        )
        db.session.add(nieuw)
        db.session.commit()
        flash('Artikel toegevoegd.', 'success')
    return redirect(url_for('artikelen.mijn_artikelen'))

@bp.route('/artikel/bewerken/<int:lokaal_id>', methods=['GET', 'POST'])
def artikel_bewerken(lokaal_id):
    Lokaal_Artikel = get_model('Lokaal_Artikel')
    artikel = db.session.query(Lokaal_Artikel).get(lokaal_id)
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
        return redirect(url_for('artikelen.mijn_artikelen'))
        
    return render_template('artikel_bewerken.html', artikel=artikel, view=view_data)