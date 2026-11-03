"""Collecte Instagram du Parachuté, lancée à 11 h et 23 h sur Google Cloud (Cloud Run Job).

    python collecte_ig.py            # renouvelle le jeton si besoin, puis collecte
    python collecte_ig.py --jeton    # renouvellement du jeton seulement

- Le jeton Instagram est dans Secret Manager (secret IG_SECRET, « instagram-token » par défaut).
  C'est la seule copie de référence : l'outil Instagram du PC le lit au même endroit et ne le
  renouvelle plus lui-même. Ici, on le prolonge de 60 jours toutes les semaines et on détruit
  les anciennes versions (une seule version active = gratuit).
- Les données vont dans le même stockage que le dashboard (bucket GCS_BUCKET ou dossier data/) :
  instagram_etat.json (compte, profil, jeton), instagram_abonnes.json (relevé quotidien),
  instagram_compte.json (métriques jour par jour), instagram_nouveaux_abonnes.json,
  instagram_posts.json (publications et stories), instagram_commentaires.json,
  instagram_audience.json (âge, genre, villes, pays), instagram_heures.json (abonnés en ligne),
  et les visuels dans instagram_media/<id>.jpg (les liens de Meta expirent, on garde une copie).
Tout ce que l'API autorise est collecté. Elle ne donne jamais l'identité de ceux qui aiment,
partagent, enregistrent ou republient (seulement des nombres), ni le titre des musiques.
API : Instagram API avec connexion Instagram (graph.instagram.com), comme l'outil du PC.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from store import Store

PARIS = ZoneInfo("Europe/Paris")
META = ZoneInfo("America/Los_Angeles")  # Meta découpe les journées à l'heure du Pacifique
HOTE = "https://graph.instagram.com"
VERSION = os.getenv("IG_API_VERSION", "v26.0")
RENOUVELER_APRES = timedelta(days=int(os.getenv("IG_RENOUVELER_APRES_JOURS", "7")))
NB_POSTS = int(os.getenv("IG_NB_POSTS", "50"))
JOURS_HISTORIQUE = 30

MAX_VISUELS = int(os.getenv("IG_MAX_VISUELS", "80"))   # téléchargements par passage

METRIQUES_COMPTE = ["reach", "views", "accounts_engaged", "total_interactions", "likes",
                    "comments", "shares", "saves", "replies", "reposts", "profile_links_taps"]
# Métriques du jour ventilées : (métrique, ventilation, {valeur de la ventilation: clé enregistrée})
DETAILS_COMPTE = [
    ("follows_and_unfollows", "follow_type", {"FOLLOWER": "abonnements", "NON_FOLLOWER": "desabonnements"}),
    ("reach", "follow_type", {"FOLLOWER": "portee_abonnes", "NON_FOLLOWER": "portee_non_abonnes"}),
]
METRIQUES_MEDIA = {
    "FEED": ["reach", "views", "likes", "comments", "shares", "saved", "reposts", "follows",
             "profile_visits", "profile_activity", "total_interactions"],
    "REELS": ["reach", "views", "likes", "comments", "shares", "saved", "reposts", "total_interactions",
              "ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "reels_skip_rate"],
    "STORY": ["reach", "views", "replies", "shares", "reposts", "follows", "profile_visits",
              "profile_activity", "navigation", "total_interactions"],
}
CHAMPS_MEDIA = "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count"
CHAMPS_MEDIA_PLUS = (CHAMPS_MEDIA + ",media_url,thumbnail_url,shortcode,media_audio_type,is_shared_to_feed,"
                     "alt_text,children{id,media_type,media_url,thumbnail_url}")
CHAMPS_PROFIL = "user_id,username,name,account_type,biography,website,profile_picture_url,media_count,followers_count,follows_count"
CHAMPS_COMMENTAIRES = "id,text,username,timestamp,like_count,hidden,replies{id,text,username,timestamp,like_count}"
VENTILATIONS_AUDIENCE = ["age", "gender", "city", "country"]

log = logging.getLogger("collecte_ig")


class ErreurAPI(Exception):
    pass


# --- Jeton dans Secret Manager -----------------------------------------------
class JetonSecret:
    def __init__(self, projet: str, secret: str, client=None):
        if client is None:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
        self.c = client
        self.nom = f"projects/{projet}/secrets/{secret}"

    def lire(self) -> str:
        rep = self.c.access_secret_version(request={"name": f"{self.nom}/versions/latest"})
        return rep.payload.data.decode("utf-8").strip()

    def remplacer(self, jeton: str) -> None:
        """Ajoute la nouvelle version puis détruit les autres (rien ne traîne, rien n'est facturé)."""
        nouvelle = self.c.add_secret_version(request={"parent": self.nom, "payload": {"data": jeton.encode("utf-8")}})
        for v in self.c.list_secret_versions(request={"parent": self.nom}):
            etat = getattr(v.state, "name", str(v.state))
            if v.name != nouvelle.name and etat in ("ENABLED", "DISABLED"):
                try:
                    self.c.destroy_secret_version(request={"name": v.name})
                except Exception as exc:  # pas bloquant : la nouvelle version est déjà la « latest »
                    log.warning("Ancienne version %s non détruite : %s", v.name.rsplit("/", 1)[-1], exc)


# --- Client Instagram --------------------------------------------------------
class Instagram:
    def __init__(self, jeton: str, transport: httpx.BaseTransport | None = None, pause=time.sleep):
        self.jeton = jeton
        self.http = httpx.Client(timeout=60, transport=transport)
        self.pause = pause

    def get(self, chemin: str, params: dict | None = None) -> dict:
        url = chemin if chemin.startswith("http") else f"{HOTE}/{VERSION}/{chemin.lstrip('/')}"
        p = dict(params or {})
        if "access_token=" not in url:
            p["access_token"] = self.jeton
        for essai in range(3):
            try:
                r = self.http.get(url, params=p)
            except httpx.HTTPError as exc:
                if essai == 2:
                    raise ErreurAPI(f"Réseau : {type(exc).__name__}") from None
                self.pause(5 * (essai + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504) and essai < 2:
                self.pause(10 * (essai + 1))
                continue
            break
        try:
            corps = r.json()
        except ValueError:
            raise ErreurAPI(f"Réponse illisible (HTTP {r.status_code})") from None
        if isinstance(corps, dict) and "error" in corps:
            raise ErreurAPI(str(corps["error"].get("message", "Erreur inconnue")).replace(self.jeton, "***"))
        return corps

    def pages(self, chemin: str, params: dict, maximum: int) -> list[dict]:
        rep = self.get(chemin, params)
        out = list(rep.get("data", []))
        while len(out) < maximum and rep.get("paging", {}).get("next"):
            rep = self.get(rep["paging"]["next"])
            out.extend(rep.get("data", []))
        return out[:maximum]

    def moi(self) -> dict:
        try:
            return self.get("me", {"fields": CHAMPS_PROFIL})
        except ErreurAPI:
            return self.get("me", {"fields": "user_id,username,account_type,media_count,followers_count,follows_count"})

    def medias(self, maximum: int) -> list[dict]:
        p = {"limit": min(maximum, 50)}
        try:
            return self.pages("me/media", {**p, "fields": CHAMPS_MEDIA_PLUS}, maximum)
        except ErreurAPI as exc:
            log.info("Champs étendus refusés (%s), repli sur les champs de base.", exc)
            return self.pages("me/media", {**p, "fields": CHAMPS_MEDIA}, maximum)

    def commentaires(self, media_id: str) -> list[dict]:
        try:
            return self.pages(f"{media_id}/comments", {"fields": CHAMPS_COMMENTAIRES, "limit": 50}, 500)
        except ErreurAPI:
            return self.pages(f"{media_id}/comments", {"fields": "id,text,username,timestamp", "limit": 50}, 500)

    def total(self, uid: str, params: dict) -> list[dict]:
        return self.get(f"{uid}/insights", {"metric_type": "total_value", **params}).get("data", [])

    def rafraichir(self) -> dict:
        return self.get(f"{HOTE}/refresh_access_token", {"grant_type": "ig_refresh_token"})

    def insights_media(self, media_id: str, metriques: list[str]) -> dict:
        rep = self.get(f"{media_id}/insights", {"metric": ",".join(metriques)})
        res = {}
        for d in rep.get("data", []):
            if d.get("values"):
                res[d["name"]] = d["values"][0].get("value")
            elif d.get("total_value"):
                res[d["name"]] = d["total_value"].get("value")
        return res

    def insights_jour_detail(self, uid: str, metrique: str, ventilation: str, jour: date) -> dict:
        debut = int(datetime.combine(jour, dtime(0), PARIS).timestamp())
        fin = int(datetime.combine(jour + timedelta(days=1), dtime(0), PARIS).timestamp())
        data = self.total(uid, {"metric": metrique, "period": "day", "since": debut, "until": fin, "breakdown": ventilation})
        return _ventilation(data)

    def insights_jour(self, uid: str, metriques: list[str], jour: date) -> dict:
        debut = int(datetime.combine(jour, dtime(0), PARIS).timestamp())
        fin = int(datetime.combine(jour + timedelta(days=1), dtime(0), PARIS).timestamp())
        rep = self.get(f"{uid}/insights", {"metric": ",".join(metriques), "period": "day",
                                           "since": debut, "until": fin, "metric_type": "total_value"})
        return {d["name"]: (d.get("total_value") or {}).get("value") for d in rep.get("data", [])}


# --- Collecte -----------------------------------------------------------------
def _ventilation(data: list[dict]) -> dict:
    """{valeur de la ventilation: nombre} à partir d'une réponse total_value avec breakdown."""
    out = {}
    for d in data:
        for b in (d.get("total_value") or {}).get("breakdowns") or []:
            for r in b.get("results") or []:
                cle = " / ".join(str(v) for v in r.get("dimension_values") or [])
                out[cle] = out.get(cle, 0) + (r.get("value") or 0)
    return out


def _telecharger(ig: Instagram, store: Store, nom: str, url: str | None, forcer: bool = False) -> bool:
    if not url or (not forcer and store.files.exists(nom)):
        return False
    try:
        r = ig.http.get(url, follow_redirects=True)
        if r.status_code != 200 or not r.content:
            return False
        store.files.write_bytes(nom, r.content, r.headers.get("content-type", "image/jpeg"))
        return True
    except httpx.HTTPError as exc:
        log.info("Visuel %s non téléchargé : %s", nom, type(exc).__name__)
        return False


def collecter_audience(ig: Instagram, store: Store, uid: str, aujourdhui: date) -> None:
    audience = store.read_json("instagram_audience.json", {})
    for cle, metrique, periodes in (("abonnes", "follower_demographics", (None, "this_month")),
                                    ("engages", "engaged_audience_demographics", ("this_month", "this_week"))):
        bloc = {}
        for v in VENTILATIONS_AUDIENCE:
            for periode in periodes:
                p = {"metric": metrique, "period": "lifetime", "breakdown": v}
                if periode:
                    p["timeframe"] = periode
                try:
                    res = _ventilation(ig.total(uid, p))
                except ErreurAPI as exc:
                    log.info("%s/%s indisponible (%s) : %s", metrique, v, periode, exc)
                    continue
                if res:
                    bloc[v] = dict(sorted(res.items(), key=lambda kv: -kv[1]))
                    break
        if bloc:
            audience[cle] = {**bloc, "maj": aujourdhui.isoformat()}
    store.write_json("instagram_audience.json", audience)


def collecter_heures(ig: Instagram, store: Store, uid: str, mtn: datetime) -> None:
    """Moyenne des abonnés en ligne par jour de la semaine et par heure (heure de Paris), 30 derniers jours."""
    try:
        rep = ig.get(f"{uid}/insights", {"metric": "online_followers", "period": "lifetime",
                                         "since": int((mtn - timedelta(days=29)).timestamp()),
                                         "until": int(mtn.timestamp())})
    except ErreurAPI as exc:
        log.info("Abonnés en ligne indisponibles : %s", exc)
        return
    cumul: dict[str, list[float]] = {}
    for d in rep.get("data", []):
        for v in d.get("values", []):
            val = v.get("value")
            if not isinstance(val, dict) or not val:
                continue
            fin = datetime.strptime(v["end_time"], "%Y-%m-%dT%H:%M:%S%z").astimezone(META)
            jour = (fin - timedelta(seconds=1)).date()
            for h, n in val.items():
                loc = datetime.combine(jour, dtime(int(h)), META).astimezone(PARIS)
                cumul.setdefault(f"{loc.weekday()}-{loc.hour}", []).append(float(n))
    if cumul:
        store.write_json("instagram_heures.json", {"maj": mtn.date().isoformat(),
                                                   "moyennes": {k: round(sum(v) / len(v), 1) for k, v in sorted(cumul.items())}})


def _par_metrique(appel, metriques: list[str], refusees: set) -> dict:
    """Essaie toutes les métriques d'un coup ; si l'API en refuse une, on les reprend une par une."""
    voulues = [m for m in metriques if m not in refusees]
    if not voulues:
        return {}
    try:
        return appel(voulues)
    except ErreurAPI:
        res = {}
        for m in voulues:
            try:
                res.update(appel([m]))
            except ErreurAPI:
                refusees.add(m)
        return res


def renouveler_si_besoin(ig: Instagram, coffre: JetonSecret, etat: dict, mtn: datetime, forcer: bool = False) -> None:
    dernier = etat.get("token_renouvele_le")
    age = mtn - datetime.fromisoformat(dernier) if dernier else None
    if not forcer and age is not None and age < RENOUVELER_APRES:
        return
    try:
        rep = ig.rafraichir()
    except ErreurAPI as exc:
        # Meta refuse de prolonger un jeton de moins de 24 h : sans gravité, on réessaiera demain.
        etat.setdefault("erreurs", []).append(f"Jeton non renouvelé : {exc}")
        log.warning("Jeton non renouvelé : %s", exc)
        return
    coffre.remplacer(rep["access_token"])
    ig.jeton = rep["access_token"]
    etat["token_renouvele_le"] = mtn.isoformat(timespec="seconds")
    etat["token_expire_le"] = (mtn + timedelta(seconds=int(rep.get("expires_in", 5_184_000)))).isoformat(timespec="seconds")
    log.info("Jeton renouvelé jusqu'au %s.", etat["token_expire_le"][:10])


def collecter(ig: Instagram, store: Store, etat: dict, mtn: datetime) -> None:
    aujourdhui = mtn.date()
    moi = ig.moi()
    uid = str(moi.get("user_id") or etat.get("ig_user_id"))
    etat.update(ig_user_id=uid, username=moi.get("username", ""),
                profil={k: moi.get(k) for k in ("name", "biography", "website", "account_type") if moi.get(k)})
    if _telecharger(ig, store, "instagram_media/profil.jpg", moi.get("profile_picture_url"), forcer=True):
        etat["profil"]["photo"] = True

    # 1. Abonnés : un relevé par jour
    abonnes = [l for l in store.read_json("instagram_abonnes.json", []) if l.get("date") != aujourdhui.isoformat()]
    abonnes.append({"date": aujourdhui.isoformat(), "abonnes": moi.get("followers_count"),
                    "abonnements": moi.get("follows_count"), "nb_posts": moi.get("media_count")})
    store.write_json("instagram_abonnes.json", sorted(abonnes, key=lambda l: l["date"]))

    # 2. Nouveaux abonnés par jour (30 derniers jours, fournis par Meta)
    nouveaux = store.read_json("instagram_nouveaux_abonnes.json", {})
    try:
        rep = ig.get(f"{uid}/insights", {"metric": "follower_count", "period": "day",
                                         "since": int((mtn - timedelta(days=JOURS_HISTORIQUE - 1)).timestamp()),
                                         "until": int(mtn.timestamp())})
        for d in rep.get("data", []):
            for v in d.get("values", []):
                fin = datetime.strptime(v["end_time"], "%Y-%m-%dT%H:%M:%S%z").astimezone(META)
                nouveaux[(fin - timedelta(seconds=1)).date().isoformat()] = v.get("value")
        store.write_json("instagram_nouveaux_abonnes.json", dict(sorted(nouveaux.items())))
    except ErreurAPI as exc:
        log.info("Nouveaux abonnés indisponibles : %s", exc)

    # 3. Métriques du compte jour par jour : on comble les 30 derniers jours, on relit les 3 derniers
    compte = {l["date"]: l for l in store.read_json("instagram_compte.json", [])}
    refusees: set = set()
    for i in range(1, JOURS_HISTORIQUE + 1):
        jour = aujourdhui - timedelta(days=i)
        if jour.isoformat() in compte and i > 3:
            continue
        valeurs = _par_metrique(lambda ms, j=jour: ig.insights_jour(uid, ms, j), METRIQUES_COMPTE, refusees)
        for metrique, ventilation, cles in DETAILS_COMPTE:
            if (metrique, ventilation) in refusees:
                continue
            try:
                detail = ig.insights_jour_detail(uid, metrique, ventilation, jour)
                valeurs.update({cle: detail.get(dim, 0) for dim, cle in cles.items()})
            except ErreurAPI as exc:
                refusees.add((metrique, ventilation))
                log.info("%s par %s indisponible : %s", metrique, ventilation, exc)
        if valeurs:
            compte[jour.isoformat()] = {**compte.get(jour.isoformat(), {}), "date": jour.isoformat(), **valeurs}
    store.write_json("instagram_compte.json", sorted(compte.values(), key=lambda l: l["date"]))

    # 4. Publications et stories du moment
    medias = ig.medias(NB_POSTS)
    try:
        medias += ig.get(f"{uid}/stories", {"fields": CHAMPS_MEDIA + ",media_url,thumbnail_url"}).get("data", [])
    except ErreurAPI as exc:
        log.info("Stories non lues : %s", exc)
    posts = {p["id"]: p for p in store.read_json("instagram_posts.json", [])}
    refus_media: dict[str, set] = {k: set() for k in METRIQUES_MEDIA}
    for m in medias:
        produit = m.get("media_product_type") or "FEED"
        cle = produit if produit in METRIQUES_MEDIA else "FEED"
        valeurs = _par_metrique(lambda ms, i=m["id"]: ig.insights_media(i, ms), METRIQUES_MEDIA[cle], refus_media[cle])
        t = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z").astimezone(PARIS)
        enfants = (m.get("children") or {}).get("data") or []
        ligne = {"id": m["id"], "date": t.isoformat(timespec="minutes"), "type": produit,
                 "format": m.get("media_type", ""), "lien": m.get("permalink", ""),
                 "legende": " ".join((m.get("caption") or "").split())[:160],
                 "legende_complete": m.get("caption") or "", "audio": m.get("media_audio_type"),
                 "texte_alt": m.get("alt_text"), "nb_likes": m.get("like_count"), "nb_commentaires": m.get("comments_count"),
                 "enfants": [e["id"] for e in enfants] or None, "maj": aujourdhui.isoformat()}
        ligne.update({k: valeurs.get(k) for k in METRIQUES_MEDIA[cle]})
        if ligne.get("likes") is None and m.get("like_count") is not None:
            ligne["likes"] = m["like_count"]
        if ligne.get("comments") is None and m.get("comments_count") is not None:
            ligne["comments"] = m["comments_count"]
        posts[m["id"]] = {**posts.get(m["id"], {}), **{k: v for k, v in ligne.items() if v is not None}}
    store.write_json("instagram_posts.json", sorted(posts.values(), key=lambda p: p["date"], reverse=True))

    # 5. Visuels (copie, car les liens de Meta expirent) : vignette de chaque post et de chaque image des carrousels
    telecharges = 0
    for m in medias:
        if telecharges >= MAX_VISUELS:
            break
        apercu = m.get("thumbnail_url") or m.get("media_url")
        telecharges += _telecharger(ig, store, f"instagram_media/{m['id']}.jpg", apercu)
        for e in (m.get("children") or {}).get("data") or []:
            telecharges += _telecharger(ig, store, f"instagram_media/{e['id']}.jpg", e.get("thumbnail_url") or e.get("media_url"))
    if telecharges:
        log.info("%d visuel(s) copié(s).", telecharges)

    # 6. Commentaires (texte, pseudo, date, réponses) des publications qui en ont
    commentaires = store.read_json("instagram_commentaires.json", {})
    for m in medias:
        if (m.get("media_product_type") or "FEED") == "STORY" or not m.get("comments_count"):
            continue
        try:
            commentaires[m["id"]] = ig.commentaires(m["id"])
        except ErreurAPI as exc:
            log.info("Commentaires de %s non lus : %s", m["id"], exc)
    store.write_json("instagram_commentaires.json", commentaires)

    # 7. Audience (âge, genre, villes, pays) et heures où les abonnés sont en ligne
    collecter_audience(ig, store, uid, aujourdhui)
    collecter_heures(ig, store, uid, mtn)


def executer(store: Store, coffre: JetonSecret, mtn: datetime | None = None, transport=None,
             jeton_seulement: bool = False) -> dict:
    mtn = mtn or datetime.now(PARIS)
    etat = store.read_json("instagram_etat.json", {})
    etat["erreurs"] = []
    ig = Instagram(coffre.lire(), transport=transport)
    try:
        renouveler_si_besoin(ig, coffre, etat, mtn)
        if not jeton_seulement:
            collecter(ig, store, etat, mtn)
            etat["derniere_collecte"] = mtn.isoformat(timespec="seconds")
    except ErreurAPI as exc:
        etat["erreurs"].append(f"Collecte interrompue : {exc}")
        log.error("Collecte interrompue : %s", exc)
    etat["dernier_passage"] = mtn.isoformat(timespec="seconds")
    store.write_json("instagram_etat.json", etat)
    return etat


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx journalise les URL, jeton compris
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    projet = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT") or "le-parachute"
    store = Store.from_env(Path(__file__).resolve().parent)
    etat = executer(store, JetonSecret(projet, os.getenv("IG_SECRET", "instagram-token")),
                    jeton_seulement="--jeton" in sys.argv)
    print(json.dumps({k: etat.get(k) for k in ("username", "derniere_collecte", "token_expire_le", "erreurs")},
                     ensure_ascii=False))
    return 1 if any(e.startswith("Collecte interrompue") for e in etat["erreurs"]) else 0


if __name__ == "__main__":
    sys.exit(main())
