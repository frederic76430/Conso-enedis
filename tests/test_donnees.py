"""Lecture des réponses Enedis et calculs."""

from datetime import date, datetime, time

import pytest

from custom_components.conso_enedis.donnees import (
    courbe,
    en_heures_creuses,
    hp_hc_par_jour,
    journee_complete,
    parse_plages,
    pas_minutes,
    premier_du_mois,
    puissances_max,
    quotidien_kwh,
    somme,
    tranches,
)


def reponse(points, pas=None):
    r = {"idPrm": "1", "grandeur": [{"grandeurMetier": "CONS", "unite": "Wh", "points": points}]}
    if pas:
        r["pas"] = pas
    return r


def test_quotidien_wh_en_kwh_et_valeurs_vides():
    r = reponse([{"v": "8123", "d": "2026-11-08"}, {"v": None, "d": "2026-11-09"}, {"v": "x", "d": "2026-11-07"}])
    assert quotidien_kwh(r) == {date(2026, 11, 8): 8.123}


def test_quotidien_reponse_absente():
    assert quotidien_kwh(None) == {}
    assert quotidien_kwh({}) == {}


def test_puissance_max_avec_heure():
    r = reponse([{"v": "5960", "d": "2026-11-08 19:42:11"}, {"v": "3000", "d": "2026-11-07"}])
    res = puissances_max(r)
    assert res[date(2026, 11, 8)] == (5.96, datetime(2026, 11, 8, 19, 42, 11))
    assert res[date(2026, 11, 7)] == (3.0, None)


@pytest.mark.parametrize(("texte", "attendu"), [("PT30M", 30), ("PT10M", 10), (None, 30), ("P1D", 30)])
def test_pas(texte, attendu):
    assert pas_minutes(texte) == attendu


def test_courbe_horodate_de_fin_d_intervalle():
    r = reponse([{"v": "1000", "d": "2026-11-08 00:30:00", "p": "PT30M"}, {"v": "600", "d": "2026-11-08 01:00:00"}], "PT30M")
    pts = courbe(r)
    assert pts[0] == (datetime(2026, 11, 8, 0, 0), 500.0, 30)
    assert pts[1] == (datetime(2026, 11, 8, 0, 30), 300.0, 30)


def test_courbe_point_de_minuit_appartient_a_la_veille():
    pts = courbe(reponse([{"v": "2000", "d": "2026-11-09 00:00:00"}], "PT30M"))
    assert pts[0][0] == datetime(2026, 11, 8, 23, 30)


def test_plages():
    assert parse_plages("22:00-06:00 ; 12h30-14h30") == [(time(22), time(6)), (time(12, 30), time(14, 30))]
    assert parse_plages("") == []
    assert parse_plages("20:00-24:00") == [(time(20), time(0))]
    for faux in ("22-06", "25:00-06:00", "10:00-10:00", "ab"):
        with pytest.raises(ValueError):
            parse_plages(faux)


def test_heures_creuses_passant_minuit():
    plages = parse_plages("22:00-06:00")
    assert en_heures_creuses(datetime(2026, 1, 1, 23, 30), plages)
    assert en_heures_creuses(datetime(2026, 1, 1, 5, 30), plages)
    assert not en_heures_creuses(datetime(2026, 1, 1, 6, 0), plages)
    assert en_heures_creuses(datetime(2026, 1, 1, 22, 0), plages)


def test_repartition_hp_hc_journee_complete():
    plages = parse_plages("22:00-06:00")
    # 48 demi-heures à 1000 W = 500 Wh chacune : 16 en HC (22h-6h), 32 en HP
    points = [(datetime(2026, 11, 8, h, m), 500.0, 30) for h in range(24) for m in (0, 30)]
    jour = hp_hc_par_jour(points, plages)[date(2026, 11, 8)]
    assert jour["hc"] == pytest.approx(8.0)
    assert jour["hp"] == pytest.approx(16.0)
    assert journee_complete(jour)
    assert not journee_complete({"minutes": 600})


def test_somme_et_mois():
    v = {date(2026, 10, 31): 1.0, date(2026, 11, 1): 2.0, date(2026, 11, 8): 3.5}
    assert somme(v, date(2026, 11, 1), date(2026, 12, 1)) == 5.5
    assert somme(v, date(2026, 9, 1), date(2026, 10, 1)) is None
    assert premier_du_mois(date(2026, 1, 15), -1) == date(2025, 12, 1)
    assert premier_du_mois(date(2026, 12, 15), 1) == date(2027, 1, 1)


def test_tranches_de_7_jours():
    jours = [date(2026, 11, d) for d in (1, 2, 3, 4, 5, 6, 7, 8, 9, 12)]
    assert tranches(jours, 7) == [
        (date(2026, 11, 1), date(2026, 11, 8)),
        (date(2026, 11, 8), date(2026, 11, 10)),
        (date(2026, 11, 12), date(2026, 11, 13)),
    ]
