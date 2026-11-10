"""Capteurs : consommation, injection, puissance max, heures pleines / creuses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfApparentPower, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import ConsoEnedisConfigEntry, ConsoEnedisCoordinator
from .donnees import premier_du_mois, somme

type Valeur = tuple[Any, dict[str, Any]]


def _dernier_jour(jours: dict[date, Any]) -> date | None:
    return max(jours) if jours else None


def _jour(cle: str) -> Callable[[dict[str, Any], date], Valeur]:
    """Dernier jour disponible (en général hier, parfois avant-hier)."""

    def calcul(data: dict[str, Any], _aujourdhui: date) -> Valeur:
        jours = data[cle]
        dernier = _dernier_jour(jours)
        if dernier is None:
            return None, {}
        return round(jours[dernier], 3), {"date": dernier.isoformat()}

    return calcul


def _periode(cle: str, debut: Callable[[date], date], fin: Callable[[date], date]) -> Callable[[dict[str, Any], date], Valeur]:
    def calcul(data: dict[str, Any], aujourdhui: date) -> Valeur:
        d1, d2 = debut(aujourdhui), fin(aujourdhui)
        jours = {j: v for j, v in data[cle].items() if d1 <= j < d2}
        total = somme(jours, d1, d2)
        if total is None:
            # Le 1er du mois (ou de l'année), Enedis n'a encore rien publié : 0 et pas « inconnu »
            return (0.0 if d1 >= aujourdhui - timedelta(days=2) else None), {"jours": 0}
        return total, {
            "du": d1.isoformat(),
            "jusqu_au": max(jours).isoformat(),
            "jours": len(jours),
        }

    return calcul


_MOIS = (premier_du_mois, lambda a: premier_du_mois(a, 1))
_MOIS_DERNIER = (lambda a: premier_du_mois(a, -1), premier_du_mois)
_ANNEE = (lambda a: date(a.year, 1, 1), lambda a: date(a.year + 1, 1, 1))


def _pmax(data: dict[str, Any], _aujourdhui: date) -> Valeur:
    dernier = _dernier_jour(data["pmax"])
    if dernier is None:
        return None, {}
    kva, heure = data["pmax"][dernier]
    attrs: dict[str, Any] = {"date": dernier.isoformat()}
    if heure:
        attrs["heure"] = heure.strftime("%H:%M")
    return round(kva * 1000), attrs  # en VA, affiché en kVA


def _hphc(cle: str, mois: bool) -> Callable[[dict[str, Any], date], Valeur]:
    def calcul(data: dict[str, Any], aujourdhui: date) -> Valeur:
        jours = data["hphc"]
        if not mois:
            dernier = _dernier_jour(jours)
            if dernier is None:
                return None, {}
            return round(jours[dernier][cle], 3), {
                "date": dernier.isoformat(),
                "complet": jours[dernier].get("minutes", 0) >= 23 * 60,
            }
        d1 = premier_du_mois(aujourdhui)
        du_mois = {j: v[cle] for j, v in jours.items() if j >= d1}
        if not du_mois:
            return (0.0 if d1 >= aujourdhui - timedelta(days=2) else None), {"jours": 0}
        return round(sum(du_mois.values()), 3), {"jusqu_au": max(du_mois).isoformat(), "jours": len(du_mois)}

    return calcul


@dataclass(frozen=True, kw_only=True)
class ConsoEnedisDescription(SensorEntityDescription):
    calcul: Callable[[dict[str, Any], date], Valeur]
    condition: Callable[[ConsoEnedisCoordinator], bool] = lambda _c: True


def _energie(key: str, calcul, condition=lambda _c: True) -> ConsoEnedisDescription:
    return ConsoEnedisDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        calcul=calcul,
        condition=condition,
    )


_PROD = lambda c: c.production
_HPHC = lambda c: bool(c.plages_hc)

DESCRIPTIONS: tuple[ConsoEnedisDescription, ...] = (
    _energie("consommation_jour", _jour("conso")),
    _energie("consommation_mois", _periode("conso", *_MOIS)),
    _energie("consommation_mois_dernier", _periode("conso", *_MOIS_DERNIER)),
    _energie("consommation_annee", _periode("conso", *_ANNEE)),
    _energie("injection_jour", _jour("prod"), _PROD),
    _energie("injection_mois", _periode("prod", *_MOIS), _PROD),
    _energie("injection_mois_dernier", _periode("prod", *_MOIS_DERNIER), _PROD),
    _energie("injection_annee", _periode("prod", *_ANNEE), _PROD),
    _energie("heures_pleines_jour", _hphc("hp", False), _HPHC),
    _energie("heures_creuses_jour", _hphc("hc", False), _HPHC),
    _energie("heures_pleines_mois", _hphc("hp", True), _HPHC),
    _energie("heures_creuses_mois", _hphc("hc", True), _HPHC),
    ConsoEnedisDescription(
        key="puissance_max_jour",
        translation_key="puissance_max_jour",
        device_class=SensorDeviceClass.APPARENT_POWER,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        suggested_unit_of_measurement="kVA",
        suggested_display_precision=2,
        calcul=_pmax,
    ),
    ConsoEnedisDescription(
        key="derniere_lecture",
        translation_key="derniere_lecture",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        calcul=lambda d, _a: (d.get("lu_le"), {}),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConsoEnedisConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        ConsoEnedisSensor(coordinator, description)
        for description in DESCRIPTIONS
        if description.condition(coordinator)
    )


class ConsoEnedisSensor(CoordinatorEntity[ConsoEnedisCoordinator], SensorEntity):
    """Capteur rattaché à l'appareil « Linky <PRM> »."""

    _attr_has_entity_name = True
    entity_description: ConsoEnedisDescription

    def __init__(self, coordinator: ConsoEnedisCoordinator, description: ConsoEnedisDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        prm = coordinator.client.prm
        self._attr_unique_id = f"{prm}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, prm)},
            name=f"Linky {prm}",
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=DeviceEntryType.SERVICE,
        )

    def _valeur(self) -> Valeur:
        return self.entity_description.calcul(self.coordinator.data, dt_util.now().date())

    @property
    def native_value(self) -> Any:
        return self._valeur()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._valeur()[1]
