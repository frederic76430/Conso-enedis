"""Constantes de l'intégration Conso Enedis."""

from __future__ import annotations

DOMAIN = "conso_enedis"
MANUFACTURER = "Enedis"
MODEL = "Compteur Linky"

API_HOST = "https://conso.boris.sh"
USER_AGENT = "ha-conso-enedis/0.2.1 (+https://github.com/frederic76430/conso-enedis)"

CONF_TOKEN = "token"
CONF_PRM = "prm"
CONF_PRODUCTION = "production"
CONF_PLAGES_HC = "plages_hc"
CONF_STATISTIQUES = "statistiques"

# Heures de relecture (heure locale). Enedis publie les données de la veille dans la
# matinée : la première relecture suffit en général, les suivantes rattrapent les retards.
HEURES_RELECTURE = (7, 10, 14, 19)

# Une courbe de charge se demande par tranches de 7 jours au maximum (limite Enedis).
JOURS_PAR_COURBE = 7
# Au plus 5 tranches par relecture : un mois complet au premier démarrage, puis 1 par jour.
TRANCHES_MAX = 5

STORAGE_VERSION = 1
