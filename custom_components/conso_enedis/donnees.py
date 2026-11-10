"""Lecture des réponses Enedis et calculs (sans dépendance à Home Assistant)."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any

_PAS = re.compile(r"^PT(\d+)M$")
_PLAGE = re.compile(r"^\s*(\d{1,2})[:h](\d{2})\s*-\s*(\d{1,2})[:h](\d{2})\s*$")


def _points(reponse: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not reponse:
        return []
    points: list[dict[str, Any]] = []
    for grandeur in reponse.get("grandeur") or []:
        points.extend(grandeur.get("points") or [])
    return points


def _date(texte: str) -> date | None:
    try:
        return date.fromisoformat(str(texte)[:10])
    except ValueError:
        return None


def _horodate(texte: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texte).replace("T", " ")[:19])
    except ValueError:
        return None


def quotidien_kwh(reponse: dict[str, Any] | None) -> dict[date, float]:
    """{jour: kWh} depuis une réponse de consommation ou production quotidienne (en Wh)."""
    resultat: dict[date, float] = {}
    for point in _points(reponse):
        jour, valeur = _date(point.get("d", "")), point.get("v")
        if jour is None or valeur in (None, ""):
            continue
        try:
            resultat[jour] = float(valeur) / 1000
        except (TypeError, ValueError):
            continue
    return resultat


def puissances_max(reponse: dict[str, Any] | None) -> dict[date, tuple[float, datetime | None]]:
    """{jour: (kVA, heure du maximum)} depuis la puissance max quotidienne (en VA)."""
    resultat: dict[date, tuple[float, datetime | None]] = {}
    for point in _points(reponse):
        moment = _horodate(point.get("d", ""))
        jour = moment.date() if moment else _date(point.get("d", ""))
        valeur = point.get("v")
        if jour is None or valeur in (None, ""):
            continue
        try:
            kva = float(valeur) / 1000
        except (TypeError, ValueError):
            continue
        # Sans heure (date seule), on ne garde pas un faux minuit
        heure = moment if moment and len(str(point.get("d", ""))) > 10 else None
        resultat[jour] = (kva, heure)
    return resultat


def pas_minutes(texte: str | None, defaut: int = 30) -> int:
    """« PT30M » → 30."""
    if texte and (m := _PAS.match(str(texte))):
        return int(m.group(1)) or defaut
    return defaut


def courbe(reponse: dict[str, Any] | None) -> list[tuple[datetime, float, int]]:
    """[(début de l'intervalle, Wh, durée en minutes)] depuis une courbe de charge.

    L'horodate Enedis marque la FIN de l'intervalle : 00:30 = consommation de 00:00 à 00:30.
    La valeur est une puissance moyenne en W sur l'intervalle.
    """
    pas_defaut = pas_minutes((reponse or {}).get("pas"))
    resultat: list[tuple[datetime, float, int]] = []
    for point in _points(reponse):
        fin = _horodate(point.get("d", ""))
        valeur = point.get("v")
        if fin is None or valeur in (None, ""):
            continue
        try:
            watts = float(valeur)
        except (TypeError, ValueError):
            continue
        minutes = pas_minutes(point.get("p"), pas_defaut)
        resultat.append((fin - timedelta(minutes=minutes), watts * minutes / 60, minutes))
    return resultat


def parse_plages(texte: str | None) -> list[tuple[time, time]]:
    """« 22:00-06:00 ; 12:30-14:30 » → [(22:00, 06:00), (12:30, 14:30)].

    Lève ValueError si une plage est mal écrite.
    """
    plages: list[tuple[time, time]] = []
    for morceau in re.split(r"[;,]", texte or ""):
        if not morceau.strip():
            continue
        m = _PLAGE.match(morceau)
        if not m:
            raise ValueError(f"Plage illisible : {morceau.strip()}")
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59 or (h2 == 24 and m2):
            raise ValueError(f"Heure impossible : {morceau.strip()}")
        debut, fin = time(h1, m1), time(0, 0) if h2 == 24 else time(h2, m2)
        if debut == fin:
            raise ValueError(f"Plage vide : {morceau.strip()}")
        plages.append((debut, fin))
    return plages


def en_heures_creuses(moment: datetime, plages: list[tuple[time, time]]) -> bool:
    """Vrai si l'instant tombe dans une plage creuse (les plages peuvent passer minuit)."""
    t = moment.time()
    for debut, fin in plages:
        if debut < fin:
            if debut <= t < fin:
                return True
        elif t >= debut or t < fin:
            return True
    return False


def hp_hc_par_jour(
    points: list[tuple[datetime, float, int]], plages: list[tuple[time, time]]
) -> dict[date, dict[str, float]]:
    """{jour: {"hp": kWh, "hc": kWh, "minutes": couverture}} à partir d'une courbe de charge."""
    resultat: dict[date, dict[str, float]] = {}
    for debut, wh, minutes in points:
        jour = resultat.setdefault(debut.date(), {"hp": 0.0, "hc": 0.0, "minutes": 0})
        jour["hc" if en_heures_creuses(debut, plages) else "hp"] += wh / 1000
        jour["minutes"] += minutes
    return resultat


def journee_complete(jour: dict[str, float]) -> bool:
    """Une journée de courbe est complète à 23 h (changement d'heure) ou plus."""
    return jour.get("minutes", 0) >= 23 * 60


def somme(valeurs: dict[date, float], debut: date, fin: date) -> float | None:
    """Somme des jours de [debut ; fin[, None s'il n'y a aucune donnée."""
    jours = [v for j, v in valeurs.items() if debut <= j < fin]
    return round(sum(jours), 3) if jours else None


def premier_du_mois(jour: date, decalage: int = 0) -> date:
    """1er du mois de « jour », décalé de « decalage » mois."""
    mois = jour.year * 12 + jour.month - 1 + decalage
    return date(mois // 12, mois % 12 + 1, 1)


def tranches(jours: list[date], taille: int) -> list[tuple[date, date]]:
    """Regroupe des jours en périodes [début ; fin[ de « taille » jours consécutifs au plus."""
    resultat: list[tuple[date, date]] = []
    for jour in sorted(set(jours)):
        if resultat and jour == resultat[-1][1] and (jour - resultat[-1][0]).days < taille:
            resultat[-1] = (resultat[-1][0], jour + timedelta(days=1))
        else:
            resultat.append((jour, jour + timedelta(days=1)))
    return resultat
