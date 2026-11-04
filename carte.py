"""Carte des commandes sur le plateau du Parachuté.

Chaque commande est placée d'après son code postal (et sa ville) sur la France du plateau de jeu.
- geo/codes_postaux.json : code postal -> communes, avec leur position déjà calculée sur le plateau.
- geo/calage.json : passage des coordonnées GPS au plateau. La France du plateau est stylisée :
  une transformation lisse (« plaque mince ») la fait coïncider exactement avec les 25 villes du jeu,
  si bien qu'une commande à Lyon tombe pile sur le pion de Lyon. Les deux fichiers sont produits
  par outils/construire_carte.py (les communes tombées dans la mer dessinée sont ramenées à terre).
Rien n'est envoyé à un service extérieur : tout est calculé à partir de ces fichiers.
"""
from __future__ import annotations

import difflib
import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

GEO = Path(__file__).resolve().parent / "geo"
IMAGE = "/static/img/carte-plateau.webp"

OUTRE_MER = {"971": "Guadeloupe", "972": "Martinique", "973": "Guyane", "974": "La Réunion",
             "975": "Saint-Pierre-et-Miquelon", "976": "Mayotte", "977": "Saint-Barthélemy",
             "978": "Saint-Martin", "986": "Wallis-et-Futuna", "987": "Polynésie française",
             "988": "Nouvelle-Calédonie"}
PAYS = {"BE": "Belgique", "CH": "Suisse", "LU": "Luxembourg", "MC": "Monaco", "DE": "Allemagne",
        "ES": "Espagne", "IT": "Italie", "GB": "Royaume-Uni", "NL": "Pays-Bas", "PT": "Portugal",
        "CA": "Canada", "US": "États-Unis", "BR": "Brésil"}


# --- Projection ---------------------------------------------------------------
def lambert93(lon: float, lat: float) -> tuple[float, float]:
    """Coordonnées Lambert-93 (projection officielle de la France métropolitaine), en km."""
    a, e = 6378137.0, 0.0818191910428158
    r = math.radians

    def m(p): return math.cos(p) / math.sqrt(1 - (e * math.sin(p)) ** 2)
    def t(p): return math.tan(math.pi / 4 - p / 2) / ((1 - e * math.sin(p)) / (1 + e * math.sin(p))) ** (e / 2)

    p1, p2, lat0, lon0 = r(44), r(49), r(46.5), r(3)
    n = (math.log(m(p1)) - math.log(m(p2))) / (math.log(t(p1)) - math.log(t(p2)))
    f = m(p1) / (n * t(p1) ** n)
    rho0, rho, th = a * f * t(lat0) ** n, a * f * t(r(lat)) ** n, n * (r(lon) - lon0)
    return (700000 + rho * math.sin(th)) / 1000, (6600000 + rho0 - rho * math.cos(th)) / 1000


def _u(d2: float) -> float:
    return 0.0 if d2 <= 0 else d2 * math.log(d2) / 2  # r² log r, écrit avec r²


@lru_cache(maxsize=1)
def calage() -> dict[str, Any]:
    return json.loads((GEO / "calage.json").read_text(encoding="utf-8"))


def vers_plateau(lat: float, lon: float) -> tuple[float, float]:
    """GPS -> coordonnées du plateau (points PDF, origine en haut à gauche)."""
    c = calage()
    x, y = lambert93(lon, lat)
    out = []
    for axe in ("x", "y"):
        w, (a0, ax, ay) = c["poids"][axe], c["affine"][axe]
        v = a0 + ax * x + ay * y
        for (px, py), wi in zip(c["appuis"], w):
            v += wi * _u((x - px) ** 2 + (y - py) ** 2)
        out.append(v)
    return out[0], out[1]


# --- Codes postaux ------------------------------------------------------------
def norm(s: Any) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = "".join(ch if ch.isalpha() else " " for ch in s)
    mots = [{"st": "saint", "ste": "sainte"}.get(w, w) for w in s.split() if w != "cedex"]
    return " ".join(mots)


@lru_cache(maxsize=1)
def table() -> dict[str, Any]:
    return json.loads((GEO / "codes_postaux.json").read_text(encoding="utf-8"))


def localiser(code_postal: str, ville: str = "", pays: str = "") -> dict[str, Any] | None:
    """Commune la plus probable pour une adresse. None si rien d'exploitable."""
    pays = (pays or "").upper()
    if pays and pays not in ("FR", "MC"):
        return {"hors": PAYS.get(pays, pays), "commune": ville or ""}
    cp = "".join(ch for ch in str(code_postal or "") if ch.isdigit())
    if len(cp) == 4:
        cp = "0" + cp  # code postal saisi sans le zéro initial (ex. 1000 pour 01000)
    if len(cp) != 5:
        return None
    if cp[:3] in OUTRE_MER:
        return {"hors": OUTRE_MER[cp[:3]], "commune": ville or ""}
    t = table()
    communes = t["cp"].get(cp)
    if communes:
        cible = norm(ville)
        noms = [c[0] for c in communes]
        best = 0
        if cible:
            if cible in noms:
                best = noms.index(cible)
            else:
                proches = difflib.get_close_matches(cible, noms, n=1, cutoff=0.6)
                best = noms.index(proches[0]) if proches else 0
        nom, affiche, x, y = communes[best]
        precision = "commune"
    else:  # code postal inconnu (récent, ou CEDEX) : centre du département
        ref = t["prefixes"].get(cp[:2])
        if not ref:
            return None
        nom_saisi = re.sub(r"\bcedex\b.*", "", str(ville or ""), flags=re.I).strip(" ,-").title()
        affiche, (x, y), precision = nom_saisi or cp, ref, "département"
    if cp.startswith("20"):  # la Corse est cachée sous les pistes de popularité du plateau
        return {"hors": "Corse", "commune": affiche}
    return {"commune": affiche, "cp": cp, "x": x, "y": y, "precision": precision}


# --- Assemblage pour le dashboard ----------------------------------------------
ARRONDISSEMENT = re.compile(r"\s+\d+(er|e)\s+Arrondissement$", re.I)


def fief(x: float, y: float) -> str:
    """Ville du plateau la plus proche du point (sa « circonscription »)."""
    villes = calage()["villes"]
    return min(villes, key=lambda v: (v["x"] - x) ** 2 + (v["y"] - y) ** 2)["nom"]


def construire(commandes: list[dict[str, Any]]) -> dict[str, Any]:
    c = calage()
    points: dict[tuple, dict] = {}
    hors: dict[str, int] = {}
    sans_adresse = 0
    fiefs: dict[str, int] = {}
    for o in commandes:
        n = int(o.get("quantite") or 1)
        loc = localiser(o.get("code_postal"), o.get("ville"), o.get("pays"))
        if not loc:
            sans_adresse += n
            continue
        if "hors" in loc:
            hors[loc["hors"]] = hors.get(loc["hors"], 0) + n
            continue
        x, y = loc["x"], loc["y"]
        if not (0 <= x <= c["cadre"]["largeur"] and 0 <= y <= c["cadre"]["hauteur"]):
            hors["Hors du plateau"] = hors.get("Hors du plateau", 0) + n
            continue
        nom = ARRONDISSEMENT.sub("", loc["commune"])  # « Paris 11e Arrondissement » -> « Paris »
        p = points.setdefault((nom, round(x, 1), round(y, 1)), {
            "x": round(x, 2), "y": round(y, 2), "commune": nom, "cp": [], "jeux": 0, "commandes": 0,
            "fief": fief(x, y), "approx": loc["precision"] != "commune"})
        p["jeux"] += n
        p["commandes"] += 1
        if loc["cp"] not in p["cp"]:
            p["cp"].append(loc["cp"])
        fiefs[p["fief"]] = fiefs.get(p["fief"], 0) + n
    return {
        "image": IMAGE,
        "largeur": c["cadre"]["largeur"],
        "hauteur": c["cadre"]["hauteur"],
        "points": sorted(points.values(), key=lambda p: -p["jeux"]),
        "hors_plateau": [{"label": k, "jeux": v} for k, v in sorted(hors.items(), key=lambda kv: -kv[1])],
        "sans_adresse": sans_adresse,
        "fiefs": [{"ville": k, "jeux": v} for k, v in sorted(fiefs.items(), key=lambda kv: -kv[1])],
        "villes": c["villes"],
    }
