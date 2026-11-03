import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collecte_ig as c
from store import Store

PARIS = c.PARIS


class FauxSecrets:
    def __init__(self, jeton):
        self.versions = [SimpleNamespace(name="s/versions/1", state=SimpleNamespace(name="ENABLED"), data=jeton)]
        self.detruites = []

    def access_secret_version(self, request):
        v = [v for v in self.versions if v.state.name == "ENABLED"][-1]
        return SimpleNamespace(payload=SimpleNamespace(data=v.data.encode()))

    def add_secret_version(self, request):
        v = SimpleNamespace(name=f"s/versions/{len(self.versions) + 1}", state=SimpleNamespace(name="ENABLED"),
                            data=request["payload"]["data"].decode())
        self.versions.append(v)
        return v

    def list_secret_versions(self, request):
        return list(self.versions)

    def destroy_secret_version(self, request):
        for v in self.versions:
            if v.name == request["name"]:
                v.state = SimpleNamespace(name="DESTROYED")
        self.detruites.append(request["name"])


MEDIAS = [
    {"id": "m1", "caption": "Premier\nexemplaire", "media_type": "IMAGE", "media_product_type": "FEED",
     "timestamp": "2026-10-04T16:59:00+0000", "permalink": "https://ig/p/1", "like_count": 328, "comments_count": 2,
     "media_url": "https://cdn.test/m1.jpg"},
    {"id": "m2", "caption": "Merci", "media_type": "CAROUSEL_ALBUM", "media_product_type": "FEED",
     "timestamp": "2026-11-01T17:30:00+0000", "permalink": "https://ig/p/2", "comments_count": 0,
     "media_url": "https://cdn.test/m2.jpg",
     "children": {"data": [{"id": "e1", "media_type": "IMAGE", "media_url": "https://cdn.test/e1.jpg"},
                           {"id": "e2", "media_type": "VIDEO", "thumbnail_url": "https://cdn.test/e2.jpg"}]}},
    {"id": "r1", "caption": "Reel", "media_type": "VIDEO", "media_product_type": "REELS", "media_audio_type": "MUSIC",
     "timestamp": "2026-10-20T17:30:00+0000", "permalink": "https://ig/p/3", "thumbnail_url": "https://cdn.test/r1.jpg"},
]


def faux_instagram(appels, refus=(), champs_etendus=True):
    def handler(req: httpx.Request):
        p = dict(req.url.params)
        chemin = req.url.path
        if req.url.host == "cdn.test":
            return httpx.Response(200, content=b"JPEG" + chemin.encode(), headers={"content-type": "image/jpeg"})
        appels.append((chemin, p))
        assert p.get("access_token") in ("JETON1", "JETON2")
        if chemin.endswith("refresh_access_token"):
            return httpx.Response(200, json={"access_token": "JETON2", "expires_in": 5184000})
        if chemin.endswith("/me"):
            return httpx.Response(200, json={"user_id": 42, "username": "le_parachute", "name": "Le Parachuté",
                                             "biography": "Jeu de plateau", "followers_count": 160, "follows_count": 12,
                                             "media_count": 3, "profile_picture_url": "https://cdn.test/profil.jpg"})
        if chemin.endswith("/me/media"):
            if "children" in p["fields"] and not champs_etendus:
                return httpx.Response(400, json={"error": {"message": "Tried accessing nonexisting field (children)"}})
            return httpx.Response(200, json={"data": MEDIAS})
        if chemin.endswith("/42/stories"):
            return httpx.Response(200, json={"data": []})
        if chemin.endswith("/m1/comments"):
            return httpx.Response(200, json={"data": [
                {"id": "c1", "text": "Trop hâte !", "username": "julie", "timestamp": "2026-10-04T18:00:00+0000",
                 "like_count": 1, "replies": {"data": [{"id": "c2", "text": "Merci !", "username": "le_parachute"}]}}]})
        if chemin.endswith("/42/insights"):
            ms = p["metric"].split(",")
            if p["metric"] == "follower_count":
                return httpx.Response(200, json={"data": [{"name": "follower_count", "values": [
                    {"value": 3, "end_time": "2026-11-01T07:00:00+0000"}, {"value": 5, "end_time": "2026-11-02T07:00:00+0000"}]}]})
            if p["metric"] == "online_followers":
                return httpx.Response(200, json={"data": [{"name": "online_followers", "values": [
                    {"value": {"10": 40, "11": 60}, "end_time": "2026-11-01T07:00:00+0000"}]}]})
            if "breakdown" in p:
                if p["metric"] == "engaged_audience_demographics":
                    return httpx.Response(400, json={"error": {"message": "not enough engagements"}})
                dims = {"follow_type": [["FOLLOWER"], ["NON_FOLLOWER"]], "age": [["25-34"], ["18-24"]],
                        "gender": [["F"], ["M"]], "city": [["Paris, Île-de-France"]], "country": [["FR"]]}[p["breakdown"]]
                return httpx.Response(200, json={"data": [{"name": p["metric"], "total_value": {"breakdowns": [
                    {"dimension_keys": [p["breakdown"]], "results": [{"dimension_values": d, "value": 10 - i}
                                                                     for i, d in enumerate(dims)]}]}}]})
            if any(m in refus for m in ms):
                return httpx.Response(400, json={"error": {"message": "metric refused JETON1"}})
            return httpx.Response(200, json={"data": [{"name": m, "total_value": {"value": 10}} for m in ms]})
        if chemin.endswith("/insights"):
            ms = p["metric"].split(",")
            if "navigation" in ms or "reels_skip_rate" in ms:
                return httpx.Response(400, json={"error": {"message": "metric unsupported"}})
            return httpx.Response(200, json={"data": [{"name": m, "values": [{"value": 7}]} for m in ms]})
        return httpx.Response(404, json={"error": {"message": "inconnu " + chemin}})
    return httpx.MockTransport(handler)


def test_premiere_collecte(tmp_path):
    store = Store(tmp_path)
    sec = FauxSecrets("JETON1")
    appels = []
    mtn = datetime(2026, 11, 2, 23, 0, tzinfo=PARIS)
    etat = c.executer(store, c.JetonSecret("p", "instagram-token", client=sec), mtn=mtn,
                      transport=faux_instagram(appels, refus={"profile_links_taps"}))
    assert etat["erreurs"] == [] and etat["username"] == "le_parachute"
    assert etat["profil"] == {"name": "Le Parachuté", "biography": "Jeu de plateau", "photo": True}
    # jeton : jamais renouvelé auparavant -> renouvelé, ancienne version détruite
    assert sec.versions[-1].data == "JETON2" and sec.detruites == ["s/versions/1"]
    assert etat["token_expire_le"].startswith("2027-01-01")
    assert store.read_json("instagram_abonnes.json", [])[-1] == {"date": "2026-11-02", "abonnes": 160, "abonnements": 12, "nb_posts": 3}
    compte = store.read_json("instagram_compte.json", [])
    assert len(compte) == 30 and compte[-1]["date"] == "2026-11-01"
    assert compte[-1]["reach"] == 10 and "profile_links_taps" not in compte[-1]
    assert compte[-1]["abonnements"] == 10 and compte[-1]["desabonnements"] == 9
    assert compte[-1]["portee_abonnes"] == 10 and compte[-1]["portee_non_abonnes"] == 9
    posts = {p["id"]: p for p in store.read_json("instagram_posts.json", [])}
    assert posts["m1"]["legende"] == "Premier exemplaire" and posts["m1"]["legende_complete"] == "Premier\nexemplaire"
    assert posts["m1"]["reach"] == 7 and posts["m1"]["follows"] == 7 and posts["m1"]["date"] == "2026-10-04T18:59+02:00"
    assert posts["m2"]["enfants"] == ["e1", "e2"] and posts["r1"]["audio"] == "MUSIC"
    assert "reels_skip_rate" not in posts["r1"] and posts["r1"]["ig_reels_avg_watch_time"] == 7
    # visuels copiés (vignette des vidéos, chaque image des carrousels, photo de profil)
    for nom in ("m1", "m2", "e1", "e2", "r1", "profil"):
        assert store.files.read_bytes(f"instagram_media/{nom}.jpg").startswith(b"JPEG")
    assert store.files.read_bytes("instagram_media/e2.jpg") == b"JPEG/e2.jpg"
    com = store.read_json("instagram_commentaires.json", {})
    assert list(com) == ["m1"] and com["m1"][0]["replies"]["data"][0]["text"] == "Merci !"
    aud = store.read_json("instagram_audience.json", {})
    assert aud["abonnes"]["age"] == {"25-34": 10, "18-24": 9} and aud["abonnes"]["country"] == {"FR": 10}
    assert "engages" not in aud  # moins de 100 interactions : Meta ne répond pas, sans gravité
    heures = store.read_json("instagram_heures.json", {})["moyennes"]
    assert heures == {"5-18": 40.0, "5-19": 60.0}  # 10 h et 11 h à Los Angeles (heure d'été) = 18 h et 19 h à Paris
    assert store.read_json("instagram_nouveaux_abonnes.json", {}) == {"2026-10-31": 3, "2026-11-01": 5}

    # 2e passage le lendemain : pas de renouvellement, pas de nouveau téléchargement, 3 derniers jours relus
    appels.clear()
    c.executer(store, c.JetonSecret("p", "instagram-token", client=sec), mtn=datetime(2026, 11, 3, 23, 0, tzinfo=PARIS),
               transport=faux_instagram(appels, refus={"profile_links_taps"}))
    assert not any(a[0].endswith("refresh_access_token") for a in appels)
    jours = {a[1]["since"] for a in appels if a[0].endswith("/42/insights") and a[1].get("period") == "day"
             and a[1]["metric"] != "follower_count"}
    assert len(jours) == 3  # les 3 derniers jours (dont le nouveau), les autres sont déjà connus
    assert len(store.read_json("instagram_compte.json", [])) == 31


def test_champs_etendus_refuses(tmp_path):
    store = Store(tmp_path)
    etat = c.executer(store, c.JetonSecret("p", "s", client=FauxSecrets("JETON1")), mtn=datetime(2026, 11, 2, 23, tzinfo=PARIS),
                      transport=faux_instagram([], champs_etendus=False))
    assert etat["erreurs"] == []
    assert len(store.read_json("instagram_posts.json", [])) == 3


def test_jeton_trop_recent_non_bloquant(tmp_path):
    store = Store(tmp_path)
    sec = FauxSecrets("JETON1")
    base = faux_instagram([])

    def handler(req):
        if req.url.path.endswith("refresh_access_token"):
            return httpx.Response(400, json={"error": {"message": "token too young"}})
        return base.handler(req)
    etat = c.executer(store, c.JetonSecret("p", "s", client=sec), mtn=datetime(2026, 11, 2, 23, tzinfo=PARIS),
                      transport=httpx.MockTransport(handler))
    assert any("Jeton non renouvelé" in e for e in etat["erreurs"]) and etat.get("derniere_collecte")
    assert len(sec.versions) == 1


def test_jeton_invalide(tmp_path):
    store = Store(tmp_path)
    t = httpx.MockTransport(lambda r: httpx.Response(400, json={"error": {"message": "Invalid OAuth access token JETON1"}}))
    etat = c.executer(store, c.JetonSecret("p", "s", client=FauxSecrets("JETON1")), mtn=datetime(2026, 11, 2, 23, tzinfo=PARIS), transport=t)
    assert any(e.startswith("Collecte interrompue") for e in etat["erreurs"])
    assert "JETON1" not in json.dumps(etat)
