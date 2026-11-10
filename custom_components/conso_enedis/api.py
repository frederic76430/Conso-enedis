"""Client de la passerelle Conso API (relais de l'API Enedis « Mesure synchrone v2 »)."""

from __future__ import annotations

import base64
import json
import logging
from datetime import date
from typing import Any

import aiohttp

from .const import API_HOST, USER_AGENT

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=60)

CONSO_QUOTIDIENNE = "consommation_quotidienne"
PROD_QUOTIDIENNE = "production_quotidienne"
COURBE_CONSO = "courbe_de_charge_consommation"
COURBE_PROD = "courbe_de_charge_production"
PUISSANCE_MAX = "puissance_conso_max_quotidienne"


class ApiError(Exception):
    """Erreur de communication avec la passerelle ou Enedis."""


class AuthError(ApiError):
    """Jeton refusé (expiré, révoqué ou sans droit sur ce PRM)."""


class LimiteAtteinte(ApiError):
    """Trop d'appels : la passerelle demande d'attendre."""


class TokenInvalide(ValueError):
    """Le jeton ne ressemble pas à un jeton Conso API."""


def prms_du_jeton(token: str) -> list[str]:
    """Liste des PRM autorisés, lue dans le jeton (JWT, champ « sub »), sans vérifier sa signature."""
    try:
        charge = token.strip().split(".")[1]
        charge += "=" * (-len(charge) % 4)
        contenu = json.loads(base64.urlsafe_b64decode(charge))
    except (IndexError, ValueError, UnicodeDecodeError) as err:
        raise TokenInvalide("Jeton illisible") from err
    sub = contenu.get("sub") if isinstance(contenu, dict) else None
    if isinstance(sub, str):
        sub = [sub]
    if not isinstance(sub, list) or not sub or not all(isinstance(p, str) for p in sub):
        raise TokenInvalide("Aucun PRM dans le jeton")
    return sub


class ConsoApiClient:
    """Appels à la passerelle pour un PRM."""

    def __init__(self, session: aiohttp.ClientSession, token: str, prm: str) -> None:
        self._session = session
        self._token = token.strip()
        self.prm = prm

    async def async_get(
        self, ressource: str, debut: date, fin: date, **extra: str
    ) -> dict[str, Any] | None:
        """Données de [debut ; fin[ (la date de fin est exclue, comme chez Enedis).

        Retourne None quand Enedis n'a aucune mesure sur la période (réponse 404).
        """
        params = {
            "pointId": self.prm,
            "dateDebut": debut.isoformat(),
            "dateFin": fin.isoformat(),
            **extra,
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        url = f"{API_HOST}/api/{ressource}"
        try:
            async with self._session.get(url, params=params, headers=headers, timeout=TIMEOUT) as resp:
                texte = await resp.text()
                if resp.status == 200:
                    try:
                        return json.loads(texte)
                    except ValueError as err:
                        raise ApiError(f"Réponse illisible de {ressource}") from err
                message = _message(texte)
                if resp.status in (401, 403):
                    raise AuthError(message or f"Accès refusé ({resp.status})")
                if resp.status == 404:
                    _LOGGER.debug("%s : aucune mesure du %s au %s (%s)", ressource, debut, fin, message)
                    return None
                if resp.status == 429:
                    raise LimiteAtteinte(message or "Trop d'appels, réessai plus tard")
                raise ApiError(f"{ressource} : erreur {resp.status} {message}".strip())
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ApiError(f"Connexion impossible à Conso API : {err}") from err


def _message(texte: str) -> str:
    """Message d'erreur renvoyé par Enedis ou la passerelle, s'il y en a un."""
    try:
        contenu = json.loads(texte)
    except ValueError:
        return texte[:200].strip()
    if isinstance(contenu, dict):
        for cle in ("message", "error", "detail"):
            if isinstance(contenu.get(cle), str):
                return contenu[cle]
    return ""
