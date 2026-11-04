"""Mode démo : le dashboard tourne avec des données fictives, sans Stripe ni Google Cloud.

    DEMO=1 python app.py        (PowerShell : $env:DEMO="1"; python app.py)

- Les « commandes Stripe » sont générées ici (mêmes champs que l'API Stripe), toujours les mêmes
  grâce à une graine fixe, et datées par rapport au jour où l'app est lancée.
- Stock, dépenses, suivi et Instagram sont écrits dans un dossier temporaire : on peut tout
  modifier dans l'interface, rien ne touche aux vraies données (data/ ou bucket).
- Aucun nom, e-mail ou chiffre réel : tout est inventé.
"""
from __future__ import annotations

import json
import random
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
PRIX = 2990           # centimes, jeu seul
PORTS = [(0, 0.45), (500, 0.35), (700, 0.20)]  # main propre, Mondial Relay, Colissimo
PRENOMS = ["Camille", "Léa", "Hugo", "Lucas", "Manon", "Chloé", "Louis", "Inès", "Jules", "Emma",
           "Arthur", "Zoé", "Paul", "Sarah", "Nathan", "Alice", "Théo", "Clara", "Raphaël", "Lina"]
NOMS = ["Martin", "Bernard", "Petit", "Durand", "Leroy", "Moreau", "Simon", "Laurent", "Lefèvre",
        "Michel", "Garcia", "Roux", "Fournier", "Girard", "Bonnet", "Dupont", "Lambert", "Fontaine"]
VILLES = [("75011", "Paris"), ("75015", "Paris"), ("75018", "Paris"), ("77100", "Meaux"),
          ("77910", "Varreddes"), ("77260", "Sammeron"), ("51100", "Reims"), ("76600", "Le Havre"),
          ("69003", "Lyon"), ("44000", "Nantes"), ("59000", "Lille"), ("33000", "Bordeaux"),
          ("13001", "Marseille"), ("31000", "Toulouse"), ("67000", "Strasbourg"), ("35000", "Rennes"),
          ("80000", "Amiens"), ("21000", "Dijon")]
SOURCES = [("instagram", "Instagram", 0.45), ("bouche", "Bouche-à-oreille", 0.30),
           ("famille", "Famille ou amis", 0.15), ("autre", "Autre", 0.10)]
MOTS = ["Drôle", "Bravo les frères !", "Hâte d'y jouer", "", "", "Trop bien", "Vive la magouille", ""]
LINK_ID = "plink_demo"


def _choix(rng: random.Random, pondere):
    x, acc = rng.random(), 0.0
    for *val, p in pondere:
        acc += p
        if x <= acc:
            return val if len(val) > 1 else val[0]
    return pondere[-1][:-1] if len(pondere[-1]) > 2 else pondere[-1][0]


def _sessions(rng: random.Random, debut: datetime, fin: datetime) -> tuple[list, list, list]:
    sessions, charges, btx = [], [], []
    span = (fin - debut).total_seconds()
    n_cmd, n_abandons = 44, 70
    for i in range(n_cmd + n_abandons):
        # plus de monde juste après le lancement, puis un rythme plus calme
        t = debut + timedelta(seconds=span * (rng.random() ** 1.8))
        ts = int(t.timestamp())
        sid, pi = f"cs_demo_{i:03d}", f"pi_demo_{i:03d}"
        if i >= n_cmd:
            sessions.append({"id": sid, "created": ts, "status": "expired", "payment_status": "unpaid",
                             "payment_link": LINK_ID, "amount_total": PRIX, "line_items": {"data": []}})
            continue
        prenom, nom = rng.choice(PRENOMS), rng.choice(NOMS)
        cp, ville = rng.choice(VILLES)
        qty = 2 if rng.random() < 0.12 else 1
        port = _choix(rng, PORTS)
        cle, libelle = _choix(rng, [(k, l, p) for k, l, p in SOURCES])
        email = f"{prenom}.{nom}{i}@exemple.fr".lower().replace("é", "e").replace("è", "e").replace("ë", "e").replace("ï", "i")
        adresse = {"line1": f"{rng.randint(1, 120)} rue de la République", "line2": None,
                   "postal_code": cp, "city": ville, "country": "FR"}
        total = PRIX * qty + port
        sessions.append({
            "id": sid, "created": ts, "status": "complete", "payment_status": "paid",
            "payment_link": LINK_ID, "payment_intent": pi, "amount_total": total,
            "customer_details": {"email": email, "name": f"{prenom} {nom}", "phone": "+33600000000",
                                 "address": adresse},
            "collected_information": {"shipping_details": {"name": f"{prenom} {nom}", "address": adresse}},
            "shipping_cost": {"amount_total": port, "shipping_rate": None},
            "line_items": {"data": [{"quantity": qty, "description": "Le Parachuté"}]},
            "custom_fields": [
                {"key": "connu", "type": "dropdown",
                 "dropdown": {"value": cle, "options": [{"value": k, "label": l} for k, l, _ in SOURCES]}},
                {"key": "motauteurs", "type": "text", "text": {"value": rng.choice(MOTS) or None}},
            ],
        })
        fee = round(total * 0.015) + 25
        charges.append({"id": f"ch_demo_{i:03d}", "payment_intent": pi, "amount": total,
                        "amount_refunded": total if i == 7 else 0,
                        "receipt_url": "", "payment_method_details": {"card": {"fingerprint": f"fp{i}"}}})
        btx.append({"id": f"txn_demo_{i:03d}", "type": "charge", "amount": total, "fee": fee})
    # un doublon possible : même e-mail que la commande 3, une heure plus tard
    d = json.loads(json.dumps(sessions[3]))
    d.update(id="cs_demo_dbl", created=sessions[3]["created"] + 3600, payment_intent="pi_demo_dbl")
    sessions.append(d)
    charges.append({"id": "ch_demo_dbl", "payment_intent": "pi_demo_dbl", "amount": d["amount_total"],
                    "amount_refunded": 0, "payment_method_details": {"card": {"fingerprint": "fp3"}}})
    return sessions, charges, btx


def snapshot(maintenant: datetime | None = None) -> dict:
    rng = random.Random(1003)
    fin = maintenant or datetime.now(PARIS)
    debut = fin - timedelta(days=6)
    sessions, charges, btx = _sessions(rng, debut, fin - timedelta(minutes=20))
    encaisse = sum(s["amount_total"] for s in sessions if s["status"] == "complete")
    frais = sum(t["fee"] for t in btx)
    verse = int(encaisse * 0.35)
    return {
        "fetched_at": time.time(),
        "payment_links": [{"id": LINK_ID, "active": True, "url": "https://buy.stripe.com/demo",
                           "metadata": {}}],
        "sessions": sessions,
        "charges": charges,
        "balance_transactions": btx,
        "balance": {"available": [{"amount": int((encaisse - frais - verse) * 0.6), "currency": "eur"}],
                    "pending": [{"amount": int((encaisse - frais - verse) * 0.4), "currency": "eur"}]},
        "shipping_rates": [],
        "payouts": [{"id": "po_demo_1", "created": int((fin - timedelta(days=2)).timestamp()),
                     "arrival_date": int((fin - timedelta(days=1)).timestamp()),
                     "amount": verse, "status": "paid"}],
        "disputes": [],
        "warnings": [],
    }


class FakeStripe:
    def __init__(self):
        self._snap = snapshot()

    def snapshot(self, force: bool = False) -> dict:
        return self._snap


def _jour(d: datetime) -> str:
    return d.date().isoformat()


def _fichiers(maintenant: datetime) -> dict[str, object]:
    rng = random.Random(77)
    j = lambda n: _jour(maintenant - timedelta(days=n))  # noqa: E731
    lancement = maintenant - timedelta(days=6)
    mouvements = [
        {"id": "a1b2c3d4", "date": j(40), "type": "commande_imprimeur", "quantite": 1003,
         "note": "Tirage complet"},
        {"id": "b2c3d4e5", "date": j(33), "type": "reception", "quantite": 3,
         "note": "Premiers exemplaires"},
        {"id": "c3d4e5f6", "date": j(5), "type": "vente_directe", "quantite": 2, "montant": 60,
         "note": "Soirée jeux chez des amis"},
        {"id": "d4e5f6a7", "date": j(3), "type": "offert", "quantite": 1, "note": "Illustrateur"},
    ]
    depenses = [
        {"id": "e5f6a7b8", "date": j(45), "libelle": "Impression des 1003 jeux", "categorie": "Fabrication",
         "montant": 9800, "statut": "Payée"},
        {"id": "f6a7b8c9", "date": j(20), "libelle": "Transport bateau Chine → France",
         "categorie": "Transport du stock", "montant": 1450, "statut": "À payer"},
        {"id": "a7b8c9d0", "date": j(8), "libelle": "Publicité Instagram", "categorie": "Marketing",
         "montant": 60, "statut": "Payée"},
    ]
    reglages = {"debut_comptage": None, "emails_internes": []}
    jours = [maintenant - timedelta(days=n) for n in range(30, -1, -1)]
    abonnes, base = [], 150
    for i, d in enumerate(jours):
        base += rng.randint(0, 3) + (12 if d.date() == lancement.date() else 0)
        abonnes.append({"date": _jour(d), "abonnes": base, "abonnements": 13, "nb_posts": 2 + i // 10})
    compte = [{"date": _jour(d), "reach": rng.randint(80, 600), "views": rng.randint(300, 2000),
               "accounts_engaged": rng.randint(10, 60), "total_interactions": rng.randint(15, 90),
               "likes": rng.randint(10, 70), "comments": rng.randint(0, 6), "shares": rng.randint(0, 8),
               "saves": rng.randint(0, 5), "profile_links_taps": rng.randint(0, 9)} for d in jours]
    nouveaux = {_jour(d): rng.randint(0, 6) for d in jours}
    posts = []
    for k, (n, legende) in enumerate([(25, "Le Parachuté arrive bientôt…"), (14, "Découvrez les personnages"),
                                      (6, "Les précommandes sont ouvertes !"), (2, "Dans les coulisses du jeu")]):
        t = (maintenant - timedelta(days=n)).replace(hour=19, minute=0, second=0, microsecond=0)
        posts.append({"id": f"demo{k}", "date": t.isoformat(), "type": "IMAGE", "format": "Post",
                      "lien": "https://www.instagram.com/", "legende": legende, "legende_complete": legende,
                      "nb_likes": rng.randint(20, 80), "nb_commentaires": rng.randint(0, 5), "enfants": [],
                      "maj": maintenant.isoformat(), "reach": rng.randint(200, 900)})
    audience_bloc = {"age": {"25-34": 120, "18-24": 30, "35-44": 25, "45-54": 10, "55-64": 6, "65+": 2},
                     "gender": {"M": 100, "F": 80, "U": 13},
                     "city": {"Paris, Île-de-France": 60, "Reims, Champagne-Ardenne": 14, "Meaux, Île-de-France": 9,
                              "Lyon, Rhône-Alpes": 6, "Nantes, Pays de la Loire": 4},
                     "country": {"FR": 180, "BE": 6, "CH": 3}, "maj": maintenant.isoformat()}
    heures = {"maj": maintenant.isoformat(),
              "moyennes": {f"{jsem}-{h}": round(20 + 70 * max(0, 1 - abs(h - 19) / 10) + rng.random() * 8, 1)
                           for jsem in range(7) for h in range(24)}}
    etat = {"erreurs": [], "token_renouvele_le": (maintenant - timedelta(days=2)).isoformat(),
            "token_expire_le": (maintenant + timedelta(days=58)).isoformat(),
            "ig_user_id": "demo", "username": "le.parachute.demo",
            "derniere_collecte": maintenant.isoformat(), "dernier_passage": maintenant.isoformat(),
            "profil": {"name": "Le Parachuté (démo)", "biography": "Compte fictif du mode démo",
                       "website": "", "account_type": "BUSINESS", "photo": False}}
    return {
        "mouvements_stock.json": mouvements, "depenses.json": depenses, "suivi_commandes.json": [],
        "reglages.json": reglages, "instagram_etat.json": etat, "instagram_abonnes.json": abonnes,
        "instagram_compte.json": compte, "instagram_nouveaux_abonnes.json": nouveaux,
        "instagram_posts.json": posts, "instagram_commentaires.json": {p["id"]: [] for p in posts},
        "instagram_audience.json": {"abonnes": audience_bloc, "engages": audience_bloc},
        "instagram_heures.json": heures,
    }


def dossier_donnees() -> Path:
    """Crée un dossier temporaire rempli des fichiers fictifs et le renvoie."""
    d = Path(tempfile.mkdtemp(prefix="parachute-demo-"))
    for nom, contenu in _fichiers(datetime.now(PARIS)).items():
        (d / nom).write_text(json.dumps(contenu, ensure_ascii=False, indent=2), encoding="utf-8")
    return d
