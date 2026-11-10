"""Statistiques horaires pour le tableau Énergie (mêmes identifiants qu'ha-linky).

Consommation : linky:<PRM>  —  Injection : linky_prod:<PRM>  —  en Wh, une valeur par heure.
La suite reprend exactement là où s'arrête la dernière statistique enregistrée, que
celle-ci vienne d'ha-linky ou de cette intégration : l'historique continue sans trou.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .donnees import tranches

_LOGGER = logging.getLogger(__name__)

# Premier import (aucune statistique existante) : on remonte 30 jours
JOURS_INITIAUX = 30


def statistic_id(prm: str, production: bool) -> str:
    return f"{'linky_prod' if production else 'linky'}:{prm}"


def metadata(prm: str, production: bool) -> StatisticMetaData:
    sid = statistic_id(prm, production)
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"Linky {'production' if production else 'consommation'} {prm}",
        source=sid.split(":")[0],
        statistic_id=sid,
        unit_class="energy",
        unit_of_measurement="Wh",
    )


def par_heure(points: list[tuple[datetime, float, int]], apres: datetime | None) -> list[tuple[datetime, float]]:
    """Regroupe une courbe (début d'intervalle local, Wh) en heures pleines horodatées (fuseau local).

    Seules les heures strictement postérieures à « apres » sont gardées.
    """
    fuseau = dt_util.get_default_time_zone()
    heures: dict[datetime, float] = {}
    for debut, wh, _minutes in points:
        heure = debut.replace(minute=0, second=0, microsecond=0, tzinfo=fuseau)
        heures[heure] = heures.get(heure, 0.0) + wh
    return sorted(
        (h, round(v, 3)) for h, v in heures.items() if apres is None or h > apres
    )


async def async_derniere(hass: HomeAssistant, sid: str) -> tuple[datetime | None, float]:
    """(début de la dernière heure enregistrée, somme cumulée) ou (None, 0)."""
    resultat = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, sid, True, {"sum"}
    )
    lignes = resultat.get(sid) or []
    if not lignes:
        return None, 0.0
    ligne = lignes[0]
    debut = ligne["start"]
    if isinstance(debut, (int, float)):
        debut = dt_util.utc_from_timestamp(debut)
    return dt_util.as_local(debut), float(ligne.get("sum") or 0.0)


async def async_mettre_a_jour(
    hass: HomeAssistant,
    prm: str,
    production: bool,
    aujourdhui: date,
    lire_courbe: Callable[[date, date], Awaitable[list[tuple[datetime, float, int]]]],
    tranches_max: int,
    jours_par_courbe: int,
) -> date | None:
    """Ajoute les heures manquantes jusqu'à hier. Retourne le dernier jour complet enregistré."""
    sid = statistic_id(prm, production)
    derniere, somme = await async_derniere(hass, sid)

    debut = (derniere + timedelta(hours=1)).date() if derniere else aujourdhui - timedelta(days=JOURS_INITIAUX)
    if debut >= aujourdhui:
        return aujourdhui - timedelta(days=1)

    jours = [debut + timedelta(days=n) for n in range((aujourdhui - debut).days)]
    nouvelles: list[StatisticData] = []
    dernier_jour: date | None = None
    for d1, d2 in tranches(jours, jours_par_courbe)[:tranches_max]:
        heures = par_heure(await lire_courbe(d1, d2), derniere)
        if not heures:
            # Enedis n'a encore rien pour cette période : on s'arrête là, sans laisser de trou
            break
        for heure, wh in heures:
            somme += wh
            nouvelles.append(StatisticData(start=heure, state=wh, sum=round(somme, 3)))
        derniere = heures[-1][0]
        dernier_jour = derniere.date() if derniere.hour == 23 else derniere.date() - timedelta(days=1)

    if nouvelles:
        async_add_external_statistics(hass, metadata(prm, production), nouvelles)
        _LOGGER.debug("%s : %d heures ajoutées jusqu'au %s", sid, len(nouvelles), nouvelles[-1]["start"])
    return dernier_jour
