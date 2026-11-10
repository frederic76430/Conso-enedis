"""Tests avec Home Assistant : configuration, capteurs, relectures."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.conso_enedis.api import AuthError, LimiteAtteinte
from custom_components.conso_enedis.const import (
    CONF_PLAGES_HC,
    CONF_PRM,
    CONF_PRODUCTION,
    CONF_TOKEN,
    DOMAIN,
)

from .test_api import jeton

PRM = "02332127285657"
TOKEN = jeton([PRM])
MAINTENANT = "2026-11-10 08:00:00+01:00"


def quotidien(debut: date, fin: date, wh) -> dict:
    pts, j = [], debut
    while j < fin:
        pts.append({"v": str(wh(j)), "d": j.isoformat()})
        j += timedelta(days=1)
    return {"grandeur": [{"points": pts}]}


class FauxEnedis:
    """Réponses simulées ; les données s'arrêtent à « dernier » (inclus)."""

    def __init__(self, dernier: date) -> None:
        self.dernier = dernier
        self.appels: list[tuple[str, date, date]] = []

    async def appel(self, ressource, debut, fin, **extra):
        self.appels.append((ressource, debut, fin))
        fin = min(fin, self.dernier + timedelta(days=1))
        if ressource == "consommation_quotidienne":
            return quotidien(debut, fin, lambda j: 10000)  # 10 kWh par jour
        if ressource == "production_quotidienne":
            return quotidien(debut, fin, lambda j: 2000)  # 2 kWh par jour
        if ressource == "puissance_conso_max_quotidienne":
            return {"grandeur": [{"points": [{"v": "5960", "d": f"{self.dernier.isoformat()} 19:42:11"}]}]}
        if ressource == "courbe_de_charge_consommation":
            pts, t = [], datetime.combine(debut, datetime.min.time())
            while t.date() < fin:
                t += timedelta(minutes=30)
                pts.append({"v": "1000", "d": t.strftime("%Y-%m-%d %H:%M:%S"), "p": "PT30M"})
            return {"pas": "PT30M", "grandeur": [{"points": pts}]}
        return None


def entree(options=None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRM,
        title=f"Linky {PRM}",
        data={CONF_TOKEN: TOKEN, CONF_PRM: PRM},
        options=options or {CONF_PRODUCTION: True, CONF_PLAGES_HC: "22:00-06:00"},
    )


async def installer(hass, entry, faux):
    entry.add_to_hass(hass)
    with patch("custom_components.conso_enedis.api.ConsoApiClient.async_get", new_callable=AsyncMock, side_effect=faux.appel):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def etat(hass, cle):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{PRM}_{cle}")
    return hass.states.get(entity_id) if entity_id else None


@pytest.fixture
async def paris(hass: HomeAssistant, freezer: FrozenDateTimeFactory):
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to(MAINTENANT)
    return freezer


async def test_capteurs(hass, paris):
    faux = FauxEnedis(date(2026, 11, 9))
    entry = entree()
    await installer(hass, entry, faux)
    assert entry.state is ConfigEntryState.LOADED

    assert etat(hass, "consommation_jour").state == "10.0"
    assert etat(hass, "consommation_jour").attributes["date"] == "2026-11-09"
    assert float(etat(hass, "consommation_mois").state) == 90.0  # 9 jours
    assert float(etat(hass, "consommation_mois_dernier").state) == 310.0  # octobre
    assert float(etat(hass, "consommation_annee").state) == pytest.approx(3130.0)  # 313 jours
    assert float(etat(hass, "injection_mois").state) == 18.0
    assert float(etat(hass, "injection_annee").state) == pytest.approx(626.0)
    # 1000 W en continu : 24 kWh par jour, 8 en HC (22h-6h), 16 en HP
    assert float(etat(hass, "heures_creuses_jour").state) == pytest.approx(8.0)
    assert float(etat(hass, "heures_pleines_jour").state) == pytest.approx(16.0)
    assert float(etat(hass, "heures_creuses_mois").state) == pytest.approx(72.0)
    pmax = etat(hass, "puissance_max_jour")
    assert float(pmax.state) == pytest.approx(5.96)
    assert pmax.attributes["unit_of_measurement"] == "kVA"
    assert pmax.attributes["heure"] == "19:42"


async def test_sans_production_ni_hc(hass, paris):
    faux = FauxEnedis(date(2026, 11, 9))
    await installer(hass, entree({CONF_PRODUCTION: False, CONF_PLAGES_HC: ""}), faux)
    assert etat(hass, "injection_jour") is None
    assert etat(hass, "heures_creuses_jour") is None
    ressources = {a[0] for a in faux.appels}
    assert ressources == {"consommation_quotidienne", "puissance_conso_max_quotidienne"}


async def test_pas_de_nouvel_appel_quand_tout_est_la(hass, paris):
    faux = FauxEnedis(date(2026, 11, 9))
    await installer(hass, entree(), faux)
    nb = len(faux.appels)
    # Courbe : le mois dernier et ce mois (au plus 5 tranches de 7 jours)
    assert sum(a[0] == "courbe_de_charge_consommation" for a in faux.appels) == 5

    with patch("custom_components.conso_enedis.api.ConsoApiClient.async_get", new_callable=AsyncMock, side_effect=faux.appel):
        paris.move_to("2026-11-10 10:30:00+01:00")
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert len(faux.appels) == nb


async def test_donnees_en_retard_puis_relecture(hass, paris):
    faux = FauxEnedis(date(2026, 11, 8))  # Enedis n'a pas encore publié hier
    await installer(hass, entree({CONF_PRODUCTION: False, CONF_PLAGES_HC: ""}), faux)
    assert etat(hass, "consommation_jour").attributes["date"] == "2026-11-08"

    faux.dernier = date(2026, 11, 9)
    with patch("custom_components.conso_enedis.api.ConsoApiClient.async_get", new_callable=AsyncMock, side_effect=faux.appel):
        paris.move_to("2026-11-10 10:59:30+01:00")
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        for minute in range(60):
            async_fire_time_changed(hass, dt_util.parse_datetime("2026-11-10 10:00:00+01:00") + timedelta(minutes=minute))
            await hass.async_block_till_done()
    assert etat(hass, "consommation_jour").attributes["date"] == "2026-11-09"


async def test_redemarrage_sans_appel(hass, paris, hass_storage):
    faux = FauxEnedis(date(2026, 11, 9))
    entry = entree()
    await installer(hass, entry, faux)
    assert await hass.config_entries.async_unload(entry.entry_id)
    nb = len(faux.appels)
    with patch("custom_components.conso_enedis.api.ConsoApiClient.async_get", new_callable=AsyncMock, side_effect=faux.appel):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert len(faux.appels) == nb
    assert float(etat(hass, "consommation_mois").state) == 90.0


async def test_limite_garde_les_valeurs(hass, paris):
    faux = FauxEnedis(date(2026, 11, 8))
    await installer(hass, entree({CONF_PRODUCTION: False, CONF_PLAGES_HC: ""}), faux)
    with patch(
        "custom_components.conso_enedis.api.ConsoApiClient.async_get", side_effect=LimiteAtteinte("trop")
    ):
        coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert etat(hass, "consommation_jour").state == "10.0"


async def test_jeton_refuse_lance_la_reauth(hass, paris):
    entry = entree()
    entry.add_to_hass(hass)
    with patch("custom_components.conso_enedis.api.ConsoApiClient.async_get", side_effect=AuthError("non")):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == SOURCE_REAUTH for f in flows)


# ---------- Configuration par l'interface ----------
async def test_ajout_un_compteur(hass, paris):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.conso_enedis.api.ConsoApiClient.async_get", return_value=None), patch(
        "custom_components.conso_enedis.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_TOKEN: TOKEN, CONF_PRM: PRM}
    assert result["result"].unique_id == PRM


async def test_jeton_illisible_ou_refuse(hass, paris):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "pas un jeton"})
    assert result["errors"] == {"base": "invalid_token"}
    with patch("custom_components.conso_enedis.api.ConsoApiClient.async_get", side_effect=AuthError("non")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["errors"] == {"base": "invalid_auth"}


async def test_plusieurs_compteurs(hass, paris):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: jeton([PRM, "11111111111111"])}
    )
    assert result["step_id"] == "prm"
    with patch("custom_components.conso_enedis.api.ConsoApiClient.async_get", return_value=None), patch(
        "custom_components.conso_enedis.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PRM: "11111111111111"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PRM] == "11111111111111"


async def test_options(hass, paris):
    faux = FauxEnedis(date(2026, 11, 9))
    entry = entree({CONF_PRODUCTION: False, CONF_PLAGES_HC: ""})
    await installer(hass, entry, faux)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PRODUCTION: True, CONF_PLAGES_HC: "22h-6h"}
    )
    assert result["errors"] == {CONF_PLAGES_HC: "invalid_plages"}
    with patch("custom_components.conso_enedis.api.ConsoApiClient.async_get", new_callable=AsyncMock, side_effect=faux.appel):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_PRODUCTION: True, CONF_PLAGES_HC: "22:00-06:00"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert etat(hass, "injection_jour").state == "2.0"
    assert float(etat(hass, "heures_creuses_jour").state) == pytest.approx(8.0)
