"""Configuration par l'interface : jeton Conso API, PRM, options."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import (
    CONSO_QUOTIDIENNE,
    ApiError,
    AuthError,
    ConsoApiClient,
    TokenInvalide,
    prms_du_jeton,
)
from .const import CONF_PLAGES_HC, CONF_PRM, CONF_PRODUCTION, CONF_TOKEN, DOMAIN
from .donnees import parse_plages

_LOGGER = logging.getLogger(__name__)

_JETON = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, multiline=False))


class ConsoEnedisConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ajout d'un compteur."""

    VERSION = 1

    def __init__(self) -> None:
        self._token = ""
        self._prms: list[str] = []

    async def _async_tester(self, token: str, prm: str) -> str | None:
        """Appel d'essai ; retourne la clé d'erreur ou None si tout va bien."""
        client = ConsoApiClient(async_get_clientsession(self.hass), token, prm)
        aujourdhui = dt_util.now().date()
        try:
            await client.async_get(CONSO_QUOTIDIENNE, aujourdhui - timedelta(days=7), aujourdhui)
        except AuthError:
            return "invalid_auth"
        except ApiError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Erreur inattendue pendant l'essai de connexion")
            return "unknown"
        return None

    async def _async_creer(self, prm: str) -> ConfigFlowResult:
        await self.async_set_unique_id(prm)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Linky {prm}",
            data={CONF_TOKEN: self._token, CONF_PRM: prm},
            options={CONF_PRODUCTION: False, CONF_PLAGES_HC: ""},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            try:
                prms = prms_du_jeton(token)
            except TokenInvalide:
                errors["base"] = "invalid_token"
            else:
                self._token, self._prms = token, prms
                if len(prms) > 1:
                    return await self.async_step_prm()
                if erreur := await self._async_tester(token, prms[0]):
                    errors["base"] = erreur
                else:
                    return await self._async_creer(prms[0])

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _JETON}),
            errors=errors,
        )

    async def async_step_prm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Le jeton donne accès à plusieurs compteurs : on en choisit un."""
        errors: dict[str, str] = {}
        if user_input is not None:
            prm = user_input[CONF_PRM]
            if erreur := await self._async_tester(self._token, prm):
                errors["base"] = erreur
            else:
                return await self._async_creer(prm)
        return self.async_show_form(
            step_id="prm",
            data_schema=vol.Schema(
                {vol.Required(CONF_PRM): SelectSelector(SelectSelectorConfig(options=self._prms))}
            ),
            errors=errors,
        )

    # ---------- Jeton refusé : on en demande un nouveau ----------
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        prm = entry.data[CONF_PRM]
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            try:
                prms = prms_du_jeton(token)
            except TokenInvalide:
                errors["base"] = "invalid_token"
            else:
                if prm not in prms:
                    errors["base"] = "wrong_prm"
                elif erreur := await self._async_tester(token, prm):
                    errors["base"] = erreur
                else:
                    return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _JETON}),
            description_placeholders={"prm": prm},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ConsoEnedisOptionsFlow()


class ConsoEnedisOptionsFlow(OptionsFlow):
    """Production et heures creuses."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            plages = (user_input.get(CONF_PLAGES_HC) or "").strip()
            try:
                parse_plages(plages)
            except ValueError:
                errors[CONF_PLAGES_HC] = "invalid_plages"
            else:
                return self.async_create_entry(
                    data={CONF_PRODUCTION: user_input[CONF_PRODUCTION], CONF_PLAGES_HC: plages}
                )
        options = {**self.config_entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(CONF_PRODUCTION, default=options.get(CONF_PRODUCTION, False)): BooleanSelector(),
                vol.Optional(
                    CONF_PLAGES_HC, description={"suggested_value": options.get(CONF_PLAGES_HC, "")}
                ): TextSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
