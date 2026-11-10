# Conso Enedis

Intégration Home Assistant qui lit les données de votre compteur **Linky** chez Enedis
(consommation, injection, puissance maximale, heures pleines / creuses) et les expose
en **capteurs**.

Elle passe par la passerelle [Conso API](https://conso.boris.sh), qui relaie l'API
Enedis « Mesure synchrone v2 » (Data Connect) : Enedis ne donne pas d'accès direct
aux particuliers. C'est la même passerelle que l'add-on
[ha-linky](https://github.com/bokub/ha-linky), et le même jeton.

> Les données Enedis arrivent **le lendemain** (J-1), parfois avec un ou deux jours
> de retard. Pour du temps réel, il faut lire le compteur en local (module TIC / ZLinky).

## Installation

1. HACS → ⋮ → **Dépôts personnalisés** → `https://github.com/frederic76430/conso-enedis`, type **Intégration**.
2. Installez **Conso Enedis**, puis redémarrez Home Assistant.
3. Paramètres → Appareils et services → **Ajouter une intégration** → *Conso Enedis*.
4. Collez votre jeton Conso API (obtenu sur [conso.boris.sh](https://conso.boris.sh) après avoir donné votre accord sur votre espace client Enedis).

## Options

- **J'injecte sur le réseau** : ajoute les capteurs d'injection (panneaux solaires).
- **Plages d'heures creuses** : par exemple `22:00-06:00` ou `01:30-07:30; 12:30-14:30`.
  La courbe de charge par demi-heure est alors répartie entre heures pleines et heures creuses.

## Capteurs

| Capteur | Contenu |
|---|---|
| Consommation jour | dernier jour publié par Enedis (attribut `date`) |
| Consommation mois / mois dernier / année | sommes des jours publiés |
| Injection jour / mois / mois dernier / année | si l'option est activée |
| Heures pleines / creuses jour et mois | si des plages sont renseignées |
| Puissance max jour | en kVA, attribut `heure` |
| Dernière lecture | date de la dernière relecture réussie |

## Appels à la passerelle

Relecture à 7 h, 10 h, 14 h et 19 h (minute tirée au hasard), **seulement si des
données manquent** : en général 4 à 5 appels par jour, aucun après un redémarrage
(les données sont gardées sur disque).

## Licence

MIT
