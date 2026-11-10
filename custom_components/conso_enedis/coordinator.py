"""Coordinateur : relit les données Enedis quelques fois par jour et les garde en mémoire."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    CONSO_QUOTIDIENNE,
    COURBE_CONSO,
    COURBE_PROD,
    PROD_QUOTIDIENNE,
    PUISSANCE_MAX,
    ApiError,
    AuthError,
    ConsoApiClient,
)
from .const import (
    CONF_PLAGES_HC,
    CONF_PRODUCTION,
    CONF_STATISTIQUES,
    DOMAIN,
    JOURS_PAR_COURBE,
    STORAGE_VERSION,
    TRANCHES_MAX,
)
from .donnees import (
    courbe,
    hp_hc_par_jour,
    journee_complete,
    parse_plages,
    premier_du_mois,
    puissances_max,
    quotidien_kwh,
    tranches,
)
from .statistiques import async_derniere, async_mettre_a_jour, statistic_id

_LOGGER = logging.getLogger(__name__)

type ConsoEnedisConfigEntry = ConfigEntry[ConsoEnedisCoordinator]


class ConsoEnedisCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Données d'un PRM.

    data = {
        "conso": {date: kWh}, "prod": {date: kWh},
        "pmax": {date: (kVA, datetime | None)},
        "hphc": {date: {"hp": kWh, "hc": kWh, "minutes": n}},
        "lu_le": datetime,
    }
    """

    config_entry: ConsoEnedisConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConsoEnedisConfigEntry, client: ConsoApiClient) -> None:
        # Pas d'intervalle fixe : les relectures sont programmées à heures fixes (voir __init__.py)
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.client = client
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._charge = False
        # Statistiques Énergie : dernier jour complet enregistré, par sens (False = conso, True = injection)
        self._stats_jusqua: dict[bool, date | None] = {}
        # Courbes déjà lues pendant cette relecture : {ressource: {jour: [points]}}
        self._courbes: dict[str, dict[date, list]] = {}

    # ---------- Options ----------
    @property
    def production(self) -> bool:
        return bool(self.config_entry.options.get(CONF_PRODUCTION, False))

    @property
    def statistiques(self) -> bool:
        return bool(self.config_entry.options.get(CONF_STATISTIQUES, False))

    def _sens_stats(self) -> list[bool]:
        if not self.statistiques:
            return []
        return [False, True] if self.production else [False]

    @property
    def plages_hc(self) -> list:
        try:
            return parse_plages(self.config_entry.options.get(CONF_PLAGES_HC, ""))
        except ValueError:
            return []

    # ---------- Mémoire sur disque (évite de tout redemander après un redémarrage) ----------
    async def _async_charger(self) -> dict[str, Any]:
        brut = await self._store.async_load() or {}

        def jours(d: dict[str, Any]) -> dict[date, Any]:
            return {date.fromisoformat(k): v for k, v in (d or {}).items()}

        pmax = {
            date.fromisoformat(k): (v[0], datetime.fromisoformat(v[1]) if v[1] else None)
            for k, v in (brut.get("pmax") or {}).items()
        }
        lu_le = brut.get("lu_le")
        return {
            "conso": jours(brut.get("conso")),
            "prod": jours(brut.get("prod")),
            "pmax": pmax,
            "hphc": jours(brut.get("hphc")),
            "plages": brut.get("plages", ""),
            "lu_le": dt_util.parse_datetime(lu_le) if lu_le else None,
        }

    async def _async_sauver(self, data: dict[str, Any]) -> None:
        def jours(d: dict[date, Any]) -> dict[str, Any]:
            return {k.isoformat(): v for k, v in d.items()}

        await self._store.async_save(
            {
                "conso": jours(data["conso"]),
                "prod": jours(data["prod"]),
                "pmax": {
                    k.isoformat(): [v[0], v[1].isoformat() if v[1] else None] for k, v in data["pmax"].items()
                },
                "hphc": jours(data["hphc"]),
                "plages": data.get("plages", ""),
                "lu_le": data["lu_le"].isoformat() if data.get("lu_le") else None,
            }
        )

    # ---------- Relecture ----------
    def _complet(self, data: dict[str, Any], hier: date) -> bool:
        """Vrai si on a déjà tout ce qu'Enedis peut donner jusqu'à hier."""
        if hier not in data["conso"]:
            return False
        if self.production and hier not in data["prod"]:
            return False
        if self.plages_hc and not journee_complete(data["hphc"].get(hier, {})):
            return False
        return all((self._stats_jusqua.get(sens) or date.min) >= hier for sens in self._sens_stats())

    async def _async_update_data(self) -> dict[str, Any]:
        if not self._charge:
            self._charge = True
            data = await self._async_charger()
        else:
            data = dict(self.data or {})

        aujourdhui = dt_util.now().date()
        hier = aujourdhui - timedelta(days=1)
        plages_txt = self.config_entry.options.get(CONF_PLAGES_HC, "")

        # Plages HC modifiées : la répartition déjà calculée n'est plus bonne
        if data.get("plages", "") != plages_txt:
            data["hphc"] = {}
            data["plages"] = plages_txt

        # Où en sont les statistiques Énergie (lu dans la base, sans appel à Enedis)
        for sens in self._sens_stats():
            if sens not in self._stats_jusqua:
                derniere, _somme = await async_derniere(self.hass, statistic_id(self.client.prm, sens))
                self._stats_jusqua[sens] = (
                    None if derniere is None
                    else derniere.date() if derniere.hour == 23 else derniere.date() - timedelta(days=1)
                )

        # Déjà complet (redémarrage, ou relecture de l'après-midi) : aucun appel
        if data.get("lu_le") and self._complet(data, hier):
            return data

        self._courbes = {}
        try:
            await self._async_lire(data, aujourdhui)
            for sens in self._sens_stats():
                jusqua = await async_mettre_a_jour(
                    self.hass,
                    self.client.prm,
                    sens,
                    aujourdhui,
                    lambda d1, d2, s=sens: self._async_courbe(COURBE_PROD if s else COURBE_CONSO, d1, d2),
                    TRANCHES_MAX,
                    JOURS_PAR_COURBE,
                )
                if jusqua is not None:
                    self._stats_jusqua[sens] = jusqua
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ApiError as err:
            if data.get("lu_le"):
                # On garde les dernières valeurs connues, la prochaine relecture réessaiera
                _LOGGER.warning("Relecture Enedis impossible, valeurs précédentes conservées : %s", err)
                return data
            raise UpdateFailed(str(err)) from err

        data["lu_le"] = dt_util.now()
        await self._async_sauver(data)
        return data

    async def _async_lire(self, data: dict[str, Any], aujourdhui: date) -> None:
        # Quotidien : depuis le 1er janvier (ou le 1er du mois dernier s'il est avant)
        debut = min(date(aujourdhui.year, 1, 1), premier_du_mois(aujourdhui, -1))
        conso = quotidien_kwh(await self.client.async_get(CONSO_QUOTIDIENNE, debut, aujourdhui))
        data["conso"] = {**{j: v for j, v in data["conso"].items() if j >= debut}, **conso}

        if self.production:
            prod = quotidien_kwh(await self.client.async_get(PROD_QUOTIDIENNE, debut, aujourdhui))
            data["prod"] = {**{j: v for j, v in data["prod"].items() if j >= debut}, **prod}
        else:
            data["prod"] = {}

        # Puissance maximale : les 7 derniers jours suffisent
        pmax = puissances_max(
            await self.client.async_get(
                PUISSANCE_MAX, aujourdhui - timedelta(days=7), aujourdhui, mesuresPas="P1D", grandeurPhysique="PMA"
            )
        )
        limite = aujourdhui - timedelta(days=40)
        data["pmax"] = {**{j: v for j, v in data["pmax"].items() if j >= limite}, **pmax}

        # Heures pleines / creuses : courbe de charge du mois en cours et du mois dernier,
        # seulement pour les jours manquants ou incomplets
        plages = self.plages_hc
        if not plages:
            data["hphc"] = {}
            return
        debut_hphc = premier_du_mois(aujourdhui, -1)
        data["hphc"] = {j: v for j, v in data["hphc"].items() if j >= debut_hphc}
        a_lire = [
            debut_hphc + timedelta(days=n)
            for n in range((aujourdhui - debut_hphc).days)
            if not journee_complete(data["hphc"].get(debut_hphc + timedelta(days=n), {}))
        ]
        # Les jours les plus récents d'abord (hier est le plus utile)
        for d1, d2 in sorted(tranches(a_lire, JOURS_PAR_COURBE), reverse=True)[:TRANCHES_MAX]:
            points = await self._async_courbe(COURBE_CONSO, d1, d2)
            for jour, valeurs in hp_hc_par_jour(points, plages).items():
                if d1 <= jour < d2:
                    data["hphc"][jour] = {k: round(v, 4) for k, v in valeurs.items()}

    async def _async_courbe(self, ressource: str, d1: date, d2: date) -> list:
        """Courbe de charge de [d1 ; d2[, sans redemander les jours déjà lus pendant cette relecture."""
        cache = self._courbes.setdefault(ressource, {})
        jours = [d1 + timedelta(days=n) for n in range((d2 - d1).days)]
        if not all(j in cache for j in jours):
            for j in jours:
                cache[j] = []
            for point in courbe(await self.client.async_get(ressource, d1, d2)):
                cache.setdefault(point[0].date(), []).append(point)
        return [p for j in jours for p in cache.get(j, [])]
