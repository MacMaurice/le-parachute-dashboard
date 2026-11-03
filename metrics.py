"""Calcul des indicateurs du dashboard à partir de Stripe + des données saisies."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import carte as carte_plateau

PARIS = ZoneInfo("Europe/Paris")

STATUTS = ["En attente du stock", "À préparer", "Expédiée", "Remise en main propre"]
STATUTS_LIVRES = {"Expédiée", "Remise en main propre"}

TYPES_MOUVEMENT = {
    "commande_imprimeur": "Tirage commandé",
    "reception": "Réception de jeux",
    "vente_directe": "Vente hors Stripe",
    "offert": "Jeu offert",
    "casse": "Jeu abîmé ou perdu",
    "ajustement": "Ajustement d'inventaire (±)",
}

CATEGORIES_DEPENSE = ["Fabrication", "Transport du stock", "Expéditions clients",
                      "Marketing", "Frais divers"]

# Si Stripe ne donne pas le nom du tarif de livraison, on le déduit du montant.
LIVRAISON_PAR_MONTANT = {0: "Retrait en main propre", 500: "Point relais Mondial Relay",
                         700: "Colissimo à domicile"}

SEUIL_STOCK_BAS = 50

STATUT_VERSEMENT = {"paid": "Versé", "pending": "En attente", "in_transit": "En cours",
                    "canceled": "Annulé", "failed": "Échoué"}

STATUT_LITIGE = {
    "warning_needs_response": "Demande d'information à traiter",
    "warning_under_review": "Demande d'information en examen",
    "warning_closed": "Demande d'information close",
    "needs_response": "Réponse à envoyer",
    "under_review": "En examen par la banque",
    "won": "Gagnée",
    "lost": "Perdue",
}
LITIGES_A_TRAITER = {"needs_response", "warning_needs_response"}

MOTIF_LITIGE = {
    "fraudulent": "Fraude déclarée", "product_not_received": "Produit non reçu",
    "product_unacceptable": "Produit non conforme", "duplicate": "Paiement en double",
    "credit_not_processed": "Remboursement non reçu", "unrecognized": "Paiement non reconnu",
    "subscription_canceled": "Abonnement annulé", "general": "Autre",
}

STATUTS_PERDUS = {"Remboursée", "Contestation perdue"}

# Doublons possibles : deux commandes payées par la même personne (même e-mail, même carte
# ou même adresse de livraison) à moins de 24 h d'intervalle.
FENETRE_DOUBLON = 24 * 3600


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, PARIS).date().isoformat()


def _fr(iso_day: str) -> str:
    y, m, d = iso_day.split("-")
    return f"{d}/{m}/{y}"


def _eur(x: float) -> str:
    return f"{x:,.2f} €".replace(",", " ").replace(".", ",")


REF_INTERNES = ("interne", "test")


def _session_interne(s: dict, debut: datetime | None, emails: set[str]) -> bool:
    """Ouverture du lien faite par vous : lien de test, e-mail interne, ou avant le début du comptage."""
    ref = str(s.get("client_reference_id") or "").lower()
    if ref.startswith(REF_INTERNES):
        return True
    email = str((s.get("customer_details") or {}).get("email") or "").lower()
    if email and email in emails:
        return True
    return bool(debut and datetime.fromtimestamp(s["created"], PARIS) < debut)


def _custom_field(session: dict, key: str) -> str | None:
    for f in session.get("custom_fields") or []:
        if f.get("key") != key:
            continue
        if f.get("type") == "dropdown":
            val = (f.get("dropdown") or {}).get("value")
            for opt in (f.get("dropdown") or {}).get("options") or []:
                if opt.get("value") == val:
                    return opt.get("label")
            return val
        if f.get("type") == "text":
            return (f.get("text") or {}).get("value")
        if f.get("type") == "numeric":
            return (f.get("numeric") or {}).get("value")
    return None


def _orders(snap: dict, suivi: list[dict], stock_recu: bool) -> list[dict]:
    rates = {r["id"]: r.get("display_name") for r in snap.get("shipping_rates") or []}
    litiges = {d.get("payment_intent"): d for d in snap.get("disputes") or [] if d.get("payment_intent")}
    charges = {c.get("payment_intent"): c for c in snap.get("charges") or [] if c.get("payment_intent")}
    suivi_by_id = {s.get("commande"): s for s in suivi}
    out = []
    for s in snap.get("sessions") or []:
        if s.get("status") != "complete" or s.get("payment_status") != "paid":
            continue
        cd = s.get("customer_details") or {}
        ship = (s.get("collected_information") or {}).get("shipping_details") \
            or s.get("shipping_details") or {}
        addr = ship.get("address") or cd.get("address") or {}
        items = (s.get("line_items") or {}).get("data") or []
        qty = sum(int(li.get("quantity") or 0) for li in items) or 1
        sc = s.get("shipping_cost") or {}
        port = int(sc.get("amount_total") or 0)
        livraison = rates.get(sc.get("shipping_rate")) or LIVRAISON_PAR_MONTANT.get(port, "Autre")
        charge = charges.get(s.get("payment_intent")) or {}
        total = int(s.get("amount_total") or 0)
        rembourse = int(charge.get("amount_refunded") or 0)
        sv = suivi_by_id.get(s["id"]) or {}
        litige = litiges.get(s.get("payment_intent")) or {}
        if litige.get("status") == "lost":
            rembourse = max(rembourse, int(litige.get("amount") or 0))
            statut = "Contestation perdue"
        elif rembourse and rembourse >= total:
            statut = "Remboursée"
        else:
            statut = sv.get("statut") or ("À préparer" if stock_recu else "En attente du stock")
        out.append({
            "id": s["id"],
            "date": _day(s["created"]),
            "created": s["created"],
            "client": sv.get("client") or ship.get("name") or cd.get("name") or "",
            "email": cd.get("email") or "",
            "telephone": cd.get("phone") or ship.get("phone") or "",
            "adresse": ", ".join(x for x in [addr.get("line1"), addr.get("line2")] if x),
            "code_postal": addr.get("postal_code") or "",
            "ville": addr.get("city") or "",
            "pays": addr.get("country") or "",
            "quantite": qty,
            "livraison": livraison,
            "montant": total / 100,
            "port": port / 100,
            "rembourse": rembourse / 100,
            "statut": statut,
            "numero_suivi": sv.get("numero_suivi") or "",
            "date_statut": sv.get("date") or "",
            "note": sv.get("note") or "",
            "source": _custom_field(s, "connu") or "Non renseigné",
            "mot": _custom_field(s, "motauteurs") or "",
            "recu_url": charge.get("receipt_url") or "",
            "litige": STATUT_LITIGE.get(litige.get("status"), "") if litige else "",
            "heure": datetime.fromtimestamp(s["created"], PARIS).strftime("%H:%M"),
            "doublon": None,
            "doublon_verifie": bool(sv.get("doublon_verifie")),
            "_cles": _cles_client(cd, addr, charge),
        })
    _doublons(out)
    for o in out:
        del o["_cles"]
    out.sort(key=lambda o: o["created"], reverse=True)
    return out


def _norm(x: Any) -> str:
    return " ".join(str(x or "").lower().replace("'", " ").replace("’", " ").split())


def _cles_client(cd: dict, addr: dict, charge: dict) -> dict[str, str]:
    """Ce qui permet de reconnaître une même personne d'une commande à l'autre."""
    carte = ((charge.get("payment_method_details") or {}).get("card") or {}).get("fingerprint")
    adresse = _norm(addr.get("line1")) + "|" + _norm(addr.get("postal_code"))
    return {k: v for k, v in {
        "même e-mail": _norm(cd.get("email")),
        "même carte bancaire": carte or "",
        "même adresse de livraison": adresse if addr.get("line1") and addr.get("postal_code") else "",
    }.items() if v}


def _ecart(sec: float) -> str:
    m = round(sec / 60)
    if m < 60:
        return f"{max(m, 1)} min"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d}" if m else f"{h} h"


def _doublons(orders: list[dict]) -> None:
    """Signale chaque commande payée peu après une autre commande de la même personne."""
    actives = sorted((o for o in orders if o["statut"] not in STATUTS_PERDUS), key=lambda o: o["created"])
    for i, o in enumerate(actives):
        for p in reversed(actives[:i]):
            if o["created"] - p["created"] > FENETRE_DOUBLON:
                break
            raisons = [k for k, v in o["_cles"].items() if p["_cles"].get(k) == v]
            if not raisons:
                continue
            o["doublon"] = {
                "avec": p["id"],
                "avec_date": p["date"],
                "avec_heure": p["heure"],
                "avec_montant": p["montant"],
                "ecart": _ecart(o["created"] - p["created"]),
                "raisons": raisons,
                "meme_montant": o["montant"] == p["montant"],
            }
            break


def build(snap: dict, mouvements: list[dict], suivi: list[dict], depenses: list[dict],
          reglages: dict | None = None) -> dict:
    reglages = reglages or {}
    debut = None
    if reglages.get("debut_comptage"):
        try:
            debut = datetime.fromisoformat(reglages["debut_comptage"]).replace(tzinfo=PARIS)
        except ValueError:
            debut = None
    emails_internes = {e.strip().lower() for e in reglages.get("emails_internes") or [] if e.strip()}

    def qte(t: str) -> int:
        return int(sum(_num(m.get("quantite")) for m in mouvements if m.get("type") == t))

    tirage = qte("commande_imprimeur")
    recus = qte("reception")
    direct = qte("vente_directe")
    offerts = qte("offert")
    casse = qte("casse")
    ajust = qte("ajustement")
    ca_direct = sum(_num(m.get("montant")) for m in mouvements if m.get("type") == "vente_directe")

    orders = _orders(snap, suivi, stock_recu=recus > 0)
    actives = [o for o in orders if o["statut"] not in STATUTS_PERDUS]
    en_ligne = sum(o["quantite"] for o in actives)
    livres = sum(o["quantite"] for o in actives if o["statut"] in STATUTS_LIVRES)
    a_preparer = sum(o["quantite"] for o in actives if o["statut"] == "À préparer")
    vendus = en_ligne + direct
    disponibles = tirage - vendus - offerts - casse + ajust
    stock_physique = recus - livres - direct - offerts - casse + ajust

    ca_en_ligne = sum(o["montant"] - o["rembourse"] for o in orders)
    port = sum(o["port"] for o in actives)
    ca = ca_en_ligne + ca_direct

    # Frais Stripe : frais sur chaque paiement + frais facturés à part (Radar…)
    frais = 0
    for t in snap.get("balance_transactions") or []:
        frais += int(t.get("fee") or 0)
        if t.get("type") == "stripe_fee":
            frais += -int(t.get("amount") or 0)
    frais /= 100
    total_depenses = sum(_num(d.get("montant")) for d in depenses)
    depenses_a_payer = sum(_num(d.get("montant")) for d in depenses if d.get("statut") == "À payer")
    cout_fabrication = sum(_num(d.get("montant")) for d in depenses
                           if d.get("categorie") in ("Fabrication", "Transport du stock"))

    bal = snap.get("balance") or {}
    solde_dispo = sum(b.get("amount", 0) for b in bal.get("available") or [] if b.get("currency") == "eur") / 100
    solde_attente = sum(b.get("amount", 0) for b in bal.get("pending") or [] if b.get("currency") == "eur") / 100

    # Versements Stripe vers le compte bancaire
    versements = sorted(({
        "id": p["id"],
        "date": _day(p.get("arrival_date") or p["created"]),
        "montant": int(p.get("amount") or 0) / 100,
        "statut": STATUT_VERSEMENT.get(p.get("status"), p.get("status") or ""),
        "code": p.get("status") or "",
    } for p in snap.get("payouts") or []), key=lambda v: v["date"], reverse=True)
    verse = sum(v["montant"] for v in versements if v["code"] == "paid")
    verse_en_cours = sum(v["montant"] for v in versements if v["code"] in ("pending", "in_transit"))

    # Contestations de paiement (litiges)
    client_par_pi = {}
    for s in snap.get("sessions") or []:
        if s.get("payment_intent"):
            cd = s.get("customer_details") or {}
            client_par_pi[s["payment_intent"]] = cd.get("name") or cd.get("email") or ""
    contestations = []
    for d in snap.get("disputes") or []:
        due = (d.get("evidence_details") or {}).get("due_by")
        contestations.append({
            "id": d["id"],
            "date": _day(d["created"]),
            "client": client_par_pi.get(d.get("payment_intent"), ""),
            "montant": int(d.get("amount") or 0) / 100,
            "motif": MOTIF_LITIGE.get(d.get("reason"), d.get("reason") or ""),
            "statut": STATUT_LITIGE.get(d.get("status"), d.get("status") or ""),
            "a_traiter": d.get("status") in LITIGES_A_TRAITER,
            "echeance": _day(due) if due else "",
        })
    contestations.sort(key=lambda c: c["date"], reverse=True)

    # Ventes jour par jour (date de Paris), du premier jour de vente à aujourd'hui
    par_jour: dict[str, dict[str, int]] = defaultdict(lambda: {"en_ligne": 0, "direct": 0})
    for o in actives:
        par_jour[o["date"]]["en_ligne"] += o["quantite"]
    for m in mouvements:
        if m.get("type") == "vente_directe" and m.get("date"):
            par_jour[str(m["date"])[:10]]["direct"] += int(_num(m.get("quantite")))
    toutes = snap.get("sessions") or []
    sessions = [s for s in toutes if not _session_interne(s, debut, emails_internes)]
    first_days = [_day(s["created"]) for s in sessions] + list(par_jour)
    serie = []
    if first_days:
        d = date.fromisoformat(min(first_days))
        today = datetime.now(PARIS).date()
        cumul = 0
        while d <= today:
            k = d.isoformat()
            v = par_jour.get(k, {"en_ligne": 0, "direct": 0})
            cumul += v["en_ligne"] + v["direct"]
            serie.append({"jour": k, **v, "cumul": cumul})
            d += timedelta(days=1)

    ouvertures = len(sessions)
    payees = sum(1 for s in sessions if s.get("payment_status") == "paid")
    links_for_test = snap.get("payment_links") or []
    funnel = {
        "ouvertures": ouvertures,
        "payees": payees,
        "abandons": ouvertures - payees,
        "taux": (payees / ouvertures) if ouvertures else None,
        "masquees": len(toutes) - len(sessions),
        "debut_comptage": reglages.get("debut_comptage") or "",
        "emails_internes": sorted(emails_internes),
        "lien_test": (links_for_test[0].get("url") + "?client_reference_id=interne")
        if links_for_test and links_for_test[0].get("url") else "",
    }

    livraison = Counter()
    sources = Counter()
    for o in actives:
        livraison[o["livraison"]] += o["quantite"]
        sources[o["source"]] += 1

    # Carte des commandes sur le plateau (un souci de géolocalisation ne doit pas bloquer le reste)
    try:
        carte_cmd = carte_plateau.construire(actives)
    except Exception as exc:  # noqa: BLE001
        carte_cmd = {"erreur": f"Carte indisponible : {exc}"}

    links = snap.get("payment_links") or []
    alertes = []
    if tirage and disponibles <= 0:
        alertes.append({"niveau": "bad", "texte": "Tirage épuisé : pense à désactiver le lien de paiement Stripe."})
    elif tirage and disponibles <= SEUIL_STOCK_BAS:
        alertes.append({"niveau": "warn", "texte": f"Plus que {disponibles} jeux disponibles à la vente."})
    if a_preparer:
        alertes.append({"niveau": "warn", "texte": f"{a_preparer} jeu{'x' if a_preparer > 1 else ''} à préparer pour l'envoi ou la remise."})
    if stock_physique < 0 and recus:
        alertes.append({"niveau": "bad", "texte": "Le stock physique calculé est négatif : vérifie le journal du stock."})
    for l in links:
        if not l.get("active"):
            alertes.append({"niveau": "warn", "texte": "Le lien de paiement est désactivé : plus personne ne peut commander."})
    for c in contestations:
        if c["a_traiter"]:
            quand = f" avant le {_fr(c['echeance'])}" if c["echeance"] else ""
            alertes.insert(0, {"niveau": "bad", "texte":
                f"Contestation de paiement de {_eur(c['montant'])} ({c['client'] or 'client inconnu'}) : "
                f"réponds dans Stripe{quand}, sinon le montant est perdu."})
    # Doublons juste après les contestations à traiter (placées en tête)
    doublons = [o for o in orders if o["doublon"] and not o["doublon_verifie"]]
    pos = sum(1 for c in contestations if c["a_traiter"])
    for o in sorted(doublons, key=lambda o: o["created"], reverse=True):
        d = o["doublon"]
        identique = "deux commandes identiques" if d["meme_montant"] else "deux commandes"
        alertes.insert(pos, {"niveau": "warn", "commande": o["id"], "texte":
            f"Doublon possible : {o['client'] or o['email'] or 'un client'} a passé {identique} "
            f"à {d['ecart']} d'intervalle ({', '.join(d['raisons'])}). Vérifie avec le client, puis "
            f"rembourse la seconde dans Stripe ou indique « Pas un doublon » dans l'onglet Commandes."})
    for f in (v for v in versements if v["code"] == "failed"):
        alertes.append({"niveau": "bad", "texte": f"Le versement du {_fr(f['date'])} a échoué : vérifie ton RIB dans Stripe."})
    for w in snap.get("warnings") or []:
        alertes.append({"niveau": "warn", "texte": w})

    return {
        "maj": datetime.fromtimestamp(snap.get("fetched_at") or 0, PARIS).isoformat(),
        "kpis": {
            "jeux_vendus": vendus,
            "vendus_en_ligne": en_ligne,
            "vendus_direct": direct,
            "commandes": len(actives),
            "clients": len({o["email"] for o in actives if o["email"]}),
            "tirage": tirage,
            "recus": recus,
            "en_route": max(0, tirage - recus),
            "offerts": offerts,
            "casse": casse,
            "ajustement": ajust,
            "disponibles": disponibles,
            "stock_physique": stock_physique,
            "a_livrer": en_ligne - livres,
            "a_preparer": a_preparer,
            "livres": livres,
            "part_tirage": (vendus / tirage) if tirage else None,
            "ca": ca,
            "ca_en_ligne": ca_en_ligne,
            "ca_direct": ca_direct,
            "port": port,
            "panier_moyen": (ca_en_ligne / len(actives)) if actives else None,
            "frais_stripe": frais,
            "depenses": total_depenses,
            "depenses_a_payer": depenses_a_payer,
            "cout_par_jeu": (cout_fabrication / tirage) if tirage and cout_fabrication else None,
            "prix_moyen_jeu": ((ca - port) / vendus) if vendus else None,
            "seuil_rentabilite": (math.ceil(total_depenses / ((ca - port) / vendus))
                                  if vendus and total_depenses and ca > port else None),
            "resultat": ca - frais - total_depenses,
            "solde_disponible": solde_dispo,
            "solde_en_attente": solde_attente,
            "verse": verse,
            "verse_en_cours": verse_en_cours,
            "contestations_a_traiter": sum(1 for c in contestations if c["a_traiter"]),
            "doublons_a_verifier": len(doublons),
        },
        "lien_paiement": [{"id": l["id"], "url": l.get("url"), "actif": l.get("active")} for l in links],
        "serie": serie,
        "funnel": funnel,
        "livraison": [{"label": k, "n": v} for k, v in livraison.most_common()],
        "sources": [{"label": k, "n": v} for k, v in sources.most_common()],
        "carte": carte_cmd,
        "commandes": orders,
        "mouvements": sorted(mouvements, key=lambda m: str(m.get("date") or ""), reverse=True),
        "versements": versements,
        "contestations": contestations,
        "depenses": sorted(depenses, key=lambda d: str(d.get("date") or ""), reverse=True),
        "alertes": alertes,
        "referentiels": {
            "statuts": STATUTS,
            "types_mouvement": TYPES_MOUVEMENT,
            "categories_depense": CATEGORIES_DEPENSE,
        },
    }
