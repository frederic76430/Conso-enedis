"""Intégration Conso Enedis : consommation et production Linky via Conso API."""

from __future__ import annotations

import random
from datetime import datetime

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change

from .api import ConsoApiClient
from .const import CONF_PRM, CONF_TOKEN, HEURES_RELECTURE
from .coordinator import ConsoEnedisConfigEntry, ConsoEnedisCoordinator

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConsoEnedisConfigEntry) -> bool:
    """Initialise l'intégration à partir d'une entrée de configuration."""
    client = ConsoApiClient(async_get_clientsession(hass), entry.data[CONF_TOKEN], entry.data[CONF_PRM])
    coordinator = ConsoEnedisCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Relectures à heures fixes, à une minute tirée au hasard pour ne pas que toutes les
    # installations interrogent la passerelle au même instant
    minute = random.Random(entry.entry_id).randint(0, 59)

    async def _relire(_now: datetime) -> None:
        await coordinator.async_refresh()

    entry.async_on_unload(
        async_track_time_change(hass, _relire, hour=list(HEURES_RELECTURE), minute=minute, second=0)
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_modifiees))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_modifiees(hass: HomeAssistant, entry: ConsoEnedisConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConsoEnedisConfigEntry) -> bool:
    """Décharge l'entrée de configuration."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
