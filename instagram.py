"""Onglet Instagram du dashboard : données collectées chaque soir (collecte_ig.py) croisées avec Stripe."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
FENETRE = timedelta(hours=48)   # effet d'une publication : ce qui se passe dans les 48 h qui suivent
JOURS_GRAPHE = 45

FICHIERS = {
    "etat": ("instagram_etat.json", {}),
    "abonnes": ("instagram_abonnes.json", []),
    "compte": ("instagram_compte.json", []),
    "nouveaux": ("instagram_nouveaux_abonnes.json", {}),
    "posts": ("instagram_posts.json", []),
    "commentaires": ("instagram_commentaires.json", {}),
    "audience": ("instagram_audience.json", {}),
    "heures": ("instagram_heures.json", {}),
}
LIBELLES_GENRE = {"F": "Femmes", "M": "Hommes", "U": "Non précisé"}


def lire(store) -> dict[str, Any]:
    return {k: store.read_json(nom, vide) for k, (nom, vide) in FICHIERS.items()}


def _n(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def section(ig: dict[str, Any], sessions: list[dict], commandes: list[dict]) -> dict[str, Any]:
    """sessions : sessions de paiement hors consultations internes ; commandes : commandes actives."""
    etat, posts = ig.get("etat") or {}, ig.get("posts") or []
    abonnes = [a for a in ig.get("abonnes") or [] if a.get("abonnes") is not None]
    compte = {c["date"]: c for c in ig.get("compte") or [] if c.get("date")}
    nouveaux = ig.get("nouveaux") or {}
    maintenant = datetime.now(PARIS)

    ouvertures_ts = sorted(int(s["created"]) for s in sessions if s.get("created"))
    cmd_ts = sorted((int(o["created"]), int(o.get("quantite") or 1)) for o in commandes if o.get("created"))

    # Moyenne d'un créneau de 48 h, depuis la première ouverture du lien de paiement
    debut = ouvertures_ts[0] if ouvertures_ts else None
    duree = (maintenant.timestamp() - debut) if debut else 0
    nb_creneaux = max(duree / FENETRE.total_seconds(), 1) if debut else None
    base = {"ouvertures": len(ouvertures_ts) / nb_creneaux, "commandes": len(cmd_ts) / nb_creneaux} if nb_creneaux else None

    lignes = []
    for p in posts:
        t = datetime.fromisoformat(p["date"])
        t0, t1 = t.timestamp(), (t + FENETRE).timestamp()
        complet = maintenant >= t + FENETRE
        avant_vente = debut is None or t1 < debut
        o = sum(1 for x in ouvertures_ts if t0 <= x < t1)
        c = [q for x, q in cmd_ts if t0 <= x < t1]
        reach, inter = p.get("reach"), p.get("total_interactions")
        lignes.append({
            **{k: p.get(k) for k in ("id", "date", "type", "format", "lien", "legende", "legende_complete", "audio",
                                       "reach", "views", "likes", "comments", "shares", "saved", "reposts", "replies",
                                       "follows", "profile_visits", "total_interactions", "ig_reels_avg_watch_time",
                                       "nb_commentaires", "enfants")},
            "engagement": (_n(inter) / _n(reach)) if reach else None,
            "effet": None if avant_vente else {"ouvertures": o, "commandes": len(c), "jeux": sum(c), "complet": complet},
        })

    # Série jour par jour : Instagram (portée, vues, clics) et lien de paiement (ouvertures, commandes)
    par_jour_o: dict[str, int] = {}
    for x in ouvertures_ts:
        k = datetime.fromtimestamp(x, PARIS).date().isoformat()
        par_jour_o[k] = par_jour_o.get(k, 0) + 1
    par_jour_c: dict[str, int] = {}
    for x, q in cmd_ts:
        k = datetime.fromtimestamp(x, PARIS).date().isoformat()
        par_jour_c[k] = par_jour_c.get(k, 0) + q
    fin = maintenant.date()
    jours = []
    for i in range(JOURS_GRAPHE - 1, -1, -1):
        k = (fin - timedelta(days=i)).isoformat()
        c = compte.get(k, {})
        jours.append({"jour": k, "portee": c.get("reach"), "portee_abonnes": c.get("portee_abonnes"),
                      "portee_non_abonnes": c.get("portee_non_abonnes"), "abonnements": c.get("abonnements"),
                      "desabonnements": c.get("desabonnements"), "vues": c.get("views"), "clics_profil": c.get("profile_links_taps"),
                      "interactions": c.get("total_interactions"), "nouveaux_abonnes": nouveaux.get(k),
                      "ouvertures": par_jour_o.get(k, 0), "jeux": par_jour_c.get(k, 0),
                      "posts": [p["id"] for p in posts if p["date"][:10] == k]})

    def somme7(cle: str) -> float | None:
        vals = [compte[k].get(cle) for k in compte if date.fromisoformat(k) > fin - timedelta(days=8)]
        vals = [v for v in vals if v is not None]
        return sum(_n(v) for v in vals) if vals else None

    ab_now = abonnes[-1]["abonnes"] if abonnes else None
    ab_7 = next((a["abonnes"] for a in reversed(abonnes) if date.fromisoformat(a["date"]) <= fin - timedelta(days=7)), None)
    connus = [o for o in commandes if o.get("source") and o["source"] != "Non renseigné"]
    via_insta = [o for o in connus if "insta" in o["source"].lower()]
    expire = etat.get("token_expire_le")
    alerte = None
    if not etat:
        alerte = "La collecte Instagram n'a pas encore tourné."
    elif expire and datetime.fromisoformat(expire) - maintenant < timedelta(days=10):
        alerte = f"Le jeton Instagram expire le {expire[8:10]}/{expire[5:7]} et n'a pas pu être renouvelé."
    elif etat.get("derniere_collecte") and maintenant - datetime.fromisoformat(etat["derniere_collecte"]) > timedelta(hours=36):
        alerte = "La collecte Instagram n'a pas tourné depuis plus d'un jour."
    # Commentaires, du plus récent au plus ancien
    legendes = {p["id"]: p for p in posts}
    commentaires = []
    for mid, liste in (ig.get("commentaires") or {}).items():
        for c in liste or []:
            reponses = (c.get("replies") or {}).get("data") or []
            commentaires.append({"post": mid, "post_legende": (legendes.get(mid) or {}).get("legende", ""),
                                 "post_lien": (legendes.get(mid) or {}).get("lien", ""),
                                 "pseudo": c.get("username") or "", "texte": c.get("text") or "",
                                 "date": c.get("timestamp") or "", "likes": c.get("like_count") or 0,
                                 "masque": bool(c.get("hidden")),
                                 "repondu": any(r.get("username") == etat.get("username") for r in reponses),
                                 "reponses": [{"pseudo": r.get("username") or "", "texte": r.get("text") or ""} for r in reponses]})
    commentaires.sort(key=lambda c: c["date"], reverse=True)

    def barres(bloc: dict | None, cle: str, n: int, libelles: dict | None = None) -> list[dict]:
        vals = (bloc or {}).get(cle) or {}
        return [{"label": (libelles or {}).get(k, k), "n": v} for k, v in list(vals.items())[:n]]
    aud = ig.get("audience") or {}
    ab, en = aud.get("abonnes"), aud.get("engages")
    audience = {
        "abonnes": {"age": barres(ab, "age", 8), "genre": barres(ab, "gender", 3, LIBELLES_GENRE),
                    "villes": barres(ab, "city", 10), "pays": barres(ab, "country", 8), "maj": (ab or {}).get("maj")} if ab else None,
        "engages": {"age": barres(en, "age", 8), "genre": barres(en, "gender", 3, LIBELLES_GENRE),
                    "villes": barres(en, "city", 10), "pays": barres(en, "country", 8)} if en else None,
    }
    return {
        "compte": etat.get("username") or "",
        "profil": etat.get("profil") or {},
        "commentaires": commentaires[:60],
        "commentaires_sans_reponse": sum(1 for c in commentaires if not c["repondu"] and c["pseudo"] != etat.get("username")),
        "audience": audience,
        "heures": (ig.get("heures") or {}).get("moyennes") or {},
        "derniere_collecte": etat.get("derniere_collecte") or "",
        "erreurs": etat.get("erreurs") or [],
        "alerte": alerte,
        "kpis": {
            "abonnes": ab_now,
            "abonnes_7j": (ab_now - ab_7) if ab_now is not None and ab_7 is not None else None,
            "portee_7j": somme7("reach"),
            "interactions_7j": somme7("total_interactions"),
            "clics_profil_7j": somme7("profile_links_taps"),
            "clients_instagram": len(via_insta),
            "clients_source_connue": len(connus),
            "nb_posts": sum(1 for p in posts if p.get("type") != "STORY"),
        },
        "base_48h": base,
        "posts": sorted(lignes, key=lambda l: l["date"], reverse=True),
        "jours": jours,
    }
