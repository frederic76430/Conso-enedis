"""Statistiques du tableau Énergie (linky:<PRM> / linky_prod:<PRM>)."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.conso_enedis.const import (
    CONF_PLAGES_HC,
    CONF_PRODUCTION,
    CONF_STATISTIQUES,
)
from custom_components.conso_enedis.statistiques import metadata, par_heure

from .test_integration import PRM, FauxEnedis, entree, installer


@pytest.fixture
async def paris(hass, freezer):
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to("2026-11-10 08:00:00+01:00")
    return freezer


async def lire(hass, sid, debut):
    res = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, debut, None, {sid}, "hour", None, {"state", "sum"}
    )
    return res.get(sid, [])


async def test_par_heure(hass, paris):
    points = [(datetime(2026, 11, 8, 0, 0), 500.0, 30), (datetime(2026, 11, 8, 0, 30), 300.0, 30),
              (datetime(2026, 11, 8, 1, 0), 100.0, 30)]
    heures = par_heure(points, None)
    assert [(h.hour, v) for h, v in heures] == [(0, 800.0), (1, 100.0)]
    assert heures[0][0].utcoffset() == timedelta(hours=1)
    assert par_heure(points, heures[0][0]) == heures[1:]


async def test_premier_import_30_jours(hass, paris):
    faux = FauxEnedis(date(2026, 11, 9))
    await installer(hass, entree({CONF_PRODUCTION: True, CONF_PLAGES_HC: "", CONF_STATISTIQUES: True}), faux)
    await async_wait_recording_done(hass)

    conso = await lire(hass, f"linky:{PRM}", dt_util.parse_datetime("2026-10-01 00:00:00+00:00"))
    # 1000 W en continu = 1000 Wh par heure ; 30 jours ramenés en 5 tranches de 7 jours au plus
    assert conso[0]["state"] == pytest.approx(1000)
    assert dt_util.utc_from_timestamp(conso[0]["start"]) == dt_util.parse_datetime("2026-10-10 22:00:00+00:00")
    assert conso[-1]["sum"] == pytest.approx(len(conso) * 1000)
    prod = await lire(hass, f"linky_prod:{PRM}", dt_util.parse_datetime("2026-10-01 00:00:00+00:00"))
    assert len(prod) == len(conso)


async def test_reprise_apres_ha_linky(hass, paris):
    # ha-linky s'est arrêté le 8 novembre à 23 h avec une somme de 5 000 000 Wh
    fin_ha_linky = dt_util.parse_datetime("2026-11-08 22:00:00+00:00")  # 23:00 heure de Paris
    async_add_external_statistics(
        hass, metadata(PRM, False), [StatisticData(start=fin_ha_linky, state=900.0, sum=5_000_000.0)]
    )
    await async_wait_recording_done(hass)

    faux = FauxEnedis(date(2026, 11, 9))
    await installer(hass, entree({CONF_PRODUCTION: False, CONF_PLAGES_HC: "", CONF_STATISTIQUES: True}), faux)
    await async_wait_recording_done(hass)

    courbes = [a for a in faux.appels if a[0] == "courbe_de_charge_consommation"]
    assert courbes == [("courbe_de_charge_consommation", date(2026, 11, 9), date(2026, 11, 10))]
    stats = await lire(hass, f"linky:{PRM}", fin_ha_linky)
    assert len(stats) == 25  # l'heure d'ha-linky + les 24 heures du 9 novembre
    assert stats[1]["sum"] == pytest.approx(5_001_000)
    assert stats[-1]["sum"] == pytest.approx(5_024_000)


async def test_a_jour_aucun_appel_de_courbe(hass, paris):
    fin = dt_util.parse_datetime("2026-11-09 22:00:00+00:00")  # 9 novembre 23 h : déjà à jour
    async_add_external_statistics(hass, metadata(PRM, False), [StatisticData(start=fin, state=1.0, sum=10.0)])
    await async_wait_recording_done(hass)
    faux = FauxEnedis(date(2026, 11, 9))
    with patch("custom_components.conso_enedis.api.ConsoApiClient.async_get", new_callable=AsyncMock, side_effect=faux.appel):
        await installer(hass, entree({CONF_PRODUCTION: False, CONF_PLAGES_HC: "", CONF_STATISTIQUES: True}), faux)
    assert not [a for a in faux.appels if a[0].startswith("courbe")]
