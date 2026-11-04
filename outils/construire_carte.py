"""Construit les fichiers de la carte (à relancer seulement si le plateau ou les sources changent).

Entrées (téléchargées à part, voir LISEZ-MOI) :
  --codes-postaux  codes-postaux.json du paquet npm « codes-postaux » (Etalab : code postal -> commune)
  --communes       communes.json du paquet npm « @etalab/decoupage-administratif » (populations, rattachements)
  --contours       communes-version-simplifiee.geojson de github.com/gregoiredavid/france-geojson (IGN)
  --plateau-json   5 - Numérisation/leparachute-sim/assets/plateau/plateau.json (positions des 25 villes)
  --plateau-png    2 - Design/plateau/plateau.png
Sorties : geo/codes_postaux.json, geo/calage.json, static/img/carte-plateau.webp
Dépendances (outil seulement, pas le dashboard) : numpy, pillow.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import carte  # noqa: E402

# Villes du plateau -> code INSEE de la commune (le pion est calé sur le centre de la commune)
VILLES = {
    "paris": ("Paris", "75056"), "lille": ("Lille", "59350"), "nantes": ("Nantes", "44109"),
    "bordeaux": ("Bordeaux", "33063"), "toulouse": ("Toulouse", "31555"), "lyon": ("Lyon", "69123"),
    "marseille": ("Marseille", "13055"), "nice": ("Nice", "06088"), "rouen": ("Rouen", "76540"),
    "caen": ("Caen", "14118"), "brest": ("Brest", "29019"), "rennes": ("Rennes", "35238"),
    "reims": ("Reims", "51454"), "metz": ("Metz", "57463"), "strasbourg": ("Strasbourg", "67482"),
    "mulhouse": ("Mulhouse", "68224"), "dijon": ("Dijon", "21231"), "orleans": ("Orléans", "45234"),
    "tours": ("Tours", "37261"), "clermont_ferrand": ("Clermont-Ferrand", "63113"),
    "grenoble": ("Grenoble", "38185"), "bayonne": ("Bayonne", "64102"),
    "montpellier": ("Montpellier", "34172"), "perpignan": ("Perpignan", "66136"), "toulon": ("Toulon", "83137"),
}
CADRE_LARGEUR = 494.0  # largeur de France retenue (au-delà : pioche Magouille, pistes de popularité)
BORD_PISTES = 487.5    # sous l'étiquette de Strasbourg, les pistes de popularité commencent ici


def centroide(geom: dict) -> tuple[float, float]:
    """Centre de gravité du plus grand morceau de la commune (lon, lat)."""
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    best, best_a = None, -1.0
    for poly in polys:
        ring = np.asarray(poly[0], dtype=float)
        x, y = ring[:, 0], ring[:, 1]
        cross = x[:-1] * y[1:] - x[1:] * y[:-1]
        a = cross.sum() / 2
        if abs(a) < 1e-12:
            c = (x.mean(), y.mean())
        else:
            c = (((x[:-1] + x[1:]) * cross).sum() / (6 * a), ((y[:-1] + y[1:]) * cross).sum() / (6 * a))
        if abs(a) > best_a:
            best, best_a = c, abs(a)
    return best


class Terre:
    """Masque « terre de France » du plateau, pour ramener à terre les points tombés en mer.

    La côte du plateau est stylisée : une commune littorale peut tomber dans l'eau dessinée,
    ou une commune frontalière dans le pays voisin (gris). On la déplace alors vers le point
    de France le plus proche (au plus 12 points du plateau, soit quelques kilomètres).
    """

    def __init__(self, png: str, largeur_pt: float, villes: dict):
        im = np.asarray(Image.open(png).convert("RGB")).astype(int)
        self.k = im.shape[1] / largeur_pt
        r, g, b = im[..., 0], im[..., 1], im[..., 2]
        mer = b > r + 30
        gris = (abs(r - g) < 12) & (abs(g - b) < 12) & (r > 180)
        self.terre = ~(mer | gris)
        self.terre[:, int(CADRE_LARGEUR * self.k):] = False
        self.terre[int(140 * self.k):, int(BORD_PISTES * self.k):] = False
        yy, xx = np.mgrid[0:im.shape[0], 0:im.shape[1]]
        for v in villes.values():  # les pions des villes comptent comme terre, quelle que soit leur couleur
            cx, cy = v["centre"]
            self.terre |= (xx - cx * self.k) ** 2 + (yy - cy * self.k) ** 2 <= ((v["rayon"] + 2) * self.k) ** 2
        self.deplaces = 0

    def accrocher(self, x: float, y: float, rayon: float = 12) -> tuple[float, float]:
        k, (h, w) = self.k, self.terre.shape
        i, j = int(y * k), int(x * k)
        if 0 <= i < h and 0 <= j < w and self.terre[i, j]:
            return x, y
        rp = int(rayon * k)
        i0, j0 = max(0, i - rp), max(0, j - rp)
        fen = self.terre[i0:min(h, i + rp + 1), j0:min(w, j + rp + 1)]
        ii, jj = np.nonzero(fen)
        if not len(ii):
            return x, y
        d = (ii + i0 - i) ** 2 + (jj + j0 - j) ** 2
        n = d.argmin()
        # un peu à l'intérieur des terres plutôt que sur le trait de côte
        vi, vj = ii[n] + i0 - i, jj[n] + j0 - j
        norme = max((vi * vi + vj * vj) ** 0.5, 1)
        self.deplaces += 1
        return (jj[n] + j0 + vj / norme * 1.5 * k) / k, (ii[n] + i0 + vi / norme * 1.5 * k) / k


def tps(src: np.ndarray, dst: np.ndarray) -> dict:
    n = len(src)
    d2 = ((src[:, None, :] - src[None, :, :]) ** 2).sum(-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.where(d2 > 0, d2 * np.log(d2) / 2, 0.0)
    p = np.hstack([np.ones((n, 1)), src])
    m = np.zeros((n + 3, n + 3))
    m[:n, :n], m[:n, n:], m[n:, :n] = k, p, p.T
    out = {"poids": {}, "affine": {}}
    for i, axe in enumerate("xy"):
        sol = np.linalg.solve(m, np.concatenate([dst[:, i], np.zeros(3)]))
        out["poids"][axe] = [round(float(v), 10) for v in sol[:n]]
        out["affine"][axe] = [round(float(v), 10) for v in sol[n:]]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    for a in ("codes-postaux", "communes", "contours", "plateau-json", "plateau-png"):
        ap.add_argument(f"--{a}", required=True)
    args = ap.parse_args()

    contours = json.loads(Path(args.contours).read_text(encoding="utf-8"))
    centre = {f["properties"]["code"]: centroide(f["geometry"]) for f in contours["features"] if f.get("geometry")}
    infos = {c["code"]: c for c in json.loads(Path(args.communes).read_text(encoding="utf-8"))}

    def coord(code: str):
        if code in centre:
            return centre[code]
        parent = infos.get(code, {}).get("commune") or infos.get(code, {}).get("chefLieu")
        return centre.get(parent) if parent else None

    # 1. Calage plateau
    plateau = json.loads(Path(args.plateau_json).read_text(encoding="utf-8"))
    src, dst, villes = [], [], []
    for cle, (nom, code) in VILLES.items():
        lon, lat = centre[code]
        src.append(carte.lambert93(lon, lat))
        x, y = plateau["villes"][cle]["centre"]
        dst.append((x, y))
        villes.append({"id": cle, "nom": nom, "x": x, "y": y})
    src, dst = np.array(src), np.array(dst)
    cal = tps(src, dst)
    cal.update({"appuis": [[round(float(a), 4), round(float(b), 4)] for a, b in src],
                "villes": villes, "cadre": {"largeur": CADRE_LARGEUR, "hauteur": plateau["hauteur"]},
                "source": plateau.get("source", "")})
    (ROOT / "geo" / "calage.json").write_text(json.dumps(cal, ensure_ascii=False, indent=1), encoding="utf-8")
    carte.calage.cache_clear()


    # 2. Codes postaux (coordonnées directement sur le plateau)
    par_cp: dict[str, dict[str, list]] = defaultdict(dict)
    manquants = 0
    for r in json.loads(Path(args.codes_postaux).read_text(encoding="utf-8")):
        cp, code = r["codePostal"], r["codeCommune"]
        if cp[:3] in carte.OUTRE_MER:
            continue
        c = coord(code)
        if not c:
            manquants += 1
            continue
        nom = r["nomCommune"]
        pop = infos.get(code, {}).get("population") or 0
        par_cp[cp][carte.norm(nom)] = [carte.norm(nom), nom, round(c[1], 4), round(c[0], 4), pop]
        acheminement = carte.norm(r.get("libelleAcheminement"))
        if acheminement and acheminement not in par_cp[cp]:  # ex. « Marseille » pour 13001
            par_cp[cp][acheminement] = [acheminement, nom, round(c[1], 4), round(c[0], 4), -1]
    terre = Terre(args.plateau_png, plateau["largeur"], plateau["villes"])
    cache: dict[tuple, list] = {}

    def sur_plateau(lat: float, lon: float) -> list:
        if (lat, lon) not in cache:
            x, y = terre.accrocher(*carte.vers_plateau(lat, lon))
            cache[(lat, lon)] = [round(x, 2), round(y, 2)]
        return cache[(lat, lon)]

    table, pref = {}, defaultdict(list)
    for cp, d in sorted(par_cp.items()):
        lst = sorted(d.values(), key=lambda v: -v[4])
        table[cp] = [[v[0], v[1], *sur_plateau(v[2], v[3])] for v in lst]
        pref[cp[:2]].append((lst[0][2], lst[0][3]))
    prefixes = {k: sur_plateau(float(np.mean([p[0] for p in v])), float(np.mean([p[1] for p in v])))
                for k, v in pref.items()}
    (ROOT / "geo" / "codes_postaux.json").write_text(
        json.dumps({"cp": table, "prefixes": prefixes}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"codes postaux : {len(table)} (lignes sans coordonnées : {manquants}, "
          f"points ramenés sur la terre ferme du plateau : {terre.deplaces})")

    # 3. Image du plateau recadrée sur la France
    img = Image.open(args.plateau_png).convert("RGB")
    k = img.width / plateau["largeur"]
    img = img.crop((0, 0, round(CADRE_LARGEUR * k), img.height))
    # Le bord gauche des pistes de popularité dépasse sur la carte (sous l'étiquette de Strasbourg) :
    # on le recouvre en prolongeant la dernière colonne de mer ou de pays voisin.
    px = np.asarray(img).copy()
    x0, y0 = round(BORD_PISTES * k), round(140 * k)
    px[y0:, x0:] = px[y0:, x0 - 1:x0]
    img = Image.fromarray(px)
    img = img.resize((1400, round(1400 * img.height / img.width)), Image.LANCZOS)
    img.save(ROOT / "static" / "img" / "carte-plateau.webp", "WEBP", quality=82, method=6)
    print("image :", img.size)


if __name__ == "__main__":
    main()
