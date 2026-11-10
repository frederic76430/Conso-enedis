"""Client Conso API."""

import base64
import json
from datetime import date

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.conso_enedis.api import (
    ApiError,
    AuthError,
    ConsoApiClient,
    LimiteAtteinte,
    TokenInvalide,
    prms_du_jeton,
)

URL = "https://conso.boris.sh/api/consommation_quotidienne"


def jeton(sub) -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS256'})}.{b64({'sub': sub})}.signature"


def test_prms_du_jeton():
    assert prms_du_jeton(jeton(["11111111111111", "22222222222222"])) == ["11111111111111", "22222222222222"]
    assert prms_du_jeton(" " + jeton("11111111111111") + " ") == ["11111111111111"]
    for faux in ("abc", "a.b.c", jeton([]), jeton(None)):
        with pytest.raises(TokenInvalide):
            prms_du_jeton(faux)


async def test_appel_et_parametres(hass, aioclient_mock):
    aioclient_mock.get(URL, json={"grandeur": [{"points": [{"v": "1000", "d": "2026-11-08"}]}]})
    client = ConsoApiClient(async_get_clientsession(hass), "TOKEN", "12345678901234")
    r = await client.async_get("consommation_quotidienne", date(2026, 11, 1), date(2026, 11, 9))
    assert r["grandeur"][0]["points"][0]["v"] == "1000"
    _methode, url, _data, headers = aioclient_mock.mock_calls[0]
    assert url.query["pointId"] == "12345678901234"
    assert url.query["dateDebut"] == "2026-11-01"
    assert url.query["dateFin"] == "2026-11-09"
    assert headers["Authorization"] == "Bearer TOKEN"
    assert headers["User-Agent"].startswith("ha-conso-enedis/")


@pytest.mark.parametrize(
    ("statut", "erreur"), [(401, AuthError), (403, AuthError), (429, LimiteAtteinte), (500, ApiError)]
)
async def test_erreurs(hass, aioclient_mock, statut, erreur):
    aioclient_mock.get(URL, status=statut, json={"code": "X", "message": "non"})
    client = ConsoApiClient(async_get_clientsession(hass), "T", "1")
    with pytest.raises(erreur):
        await client.async_get("consommation_quotidienne", date(2026, 11, 1), date(2026, 11, 2))


async def test_404_aucune_mesure(hass, aioclient_mock):
    aioclient_mock.get(URL, status=404, json={"code": "ADAM-ERR0123", "message": "Pas de mesure trouvée pour ce point"})
    client = ConsoApiClient(async_get_clientsession(hass), "T", "1")
    assert await client.async_get("consommation_quotidienne", date(2026, 11, 1), date(2026, 11, 2)) is None
