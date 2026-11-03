"""Dashboard de commercialisation du Parachuté — serveur FastAPI local."""
from __future__ import annotations

import csv
import io
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import instagram
import metrics
from store import Store
from stripe_client import StripeClient, StripeError

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

STATIC = ROOT / "static"
store = Store.from_env(ROOT)  # dossier data/ en local, bucket GCS si GCS_BUCKET est renseigné
_stripe: StripeClient | None = None


def stripe() -> StripeClient:
    global _stripe
    if _stripe is None:
        key = os.getenv("STRIPE_API_KEY", "").strip()
        if not key or "xxx" in key:
            raise HTTPException(500, "Clé Stripe absente : renseigne STRIPE_API_KEY (fichier .env en local, secret stripe-api-key en ligne)")
        links = [x.strip() for x in os.getenv("STRIPE_PAYMENT_LINKS", "").split(",")]
        _stripe = StripeClient(key, links, ttl_seconds=int(os.getenv("CACHE_SECONDES", "60")))
    return _stripe


app = FastAPI(title="Le Parachuté — commercialisation", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(StripeError)
def _stripe_error(_, exc: StripeError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


def _dashboard(refresh: bool = False) -> dict:
    snap = stripe().snapshot(force=refresh)
    return metrics.build(snap, store.read("mouvements"), store.read("suivi"), store.read("depenses"),
                         store.read_settings(), instagram.lire(store))


@app.get("/api/dashboard")
def dashboard(refresh: bool = False):
    return _dashboard(refresh)


# --- Visuels Instagram (copiés dans le bucket par la collecte) -----------------
@app.get("/api/instagram/media/{media_id}", include_in_schema=False)
def instagram_media(media_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", media_id):
        raise HTTPException(404, "Visuel introuvable")
    data = store.files.read_bytes(f"instagram_media/{media_id}.jpg")
    if data is None:
        raise HTTPException(404, "Visuel introuvable")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# --- Réglages (filtre des consultations internes du lien de paiement) ---------
class Reglages(BaseModel):
    debut_comptage: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$")
    emails_internes: list[str] = []


@app.get("/api/reglages")
def get_reglages():
    return store.read_settings()


@app.put("/api/reglages")
def put_reglages(r: Reglages):
    data = r.model_dump()
    data["emails_internes"] = [e.strip().lower() for e in data["emails_internes"] if e.strip()]
    return store.write_settings(data)


# --- Journal du stock -------------------------------------------------------
class Mouvement(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    type: Literal[tuple(metrics.TYPES_MOUVEMENT)]  # type: ignore[valid-type]
    quantite: int
    montant: Optional[float] = None
    note: str = ""


@app.post("/api/mouvements")
def add_mouvement(m: Mouvement):
    if m.type != "ajustement" and m.quantite <= 0:
        raise HTTPException(422, "La quantité doit être positive (utilise « Ajustement » pour corriger à la baisse).")
    row = m.model_dump()
    if m.type != "vente_directe":
        row["montant"] = None
    return store.add("mouvements", row)


@app.delete("/api/mouvements/{row_id}")
def del_mouvement(row_id: str):
    if not store.delete("mouvements", row_id):
        raise HTTPException(404, "Mouvement introuvable")
    return {"ok": True}


# --- Dépenses ---------------------------------------------------------------
class Depense(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    categorie: str
    montant: float = Field(gt=0)
    statut: Literal["Payée", "À payer"] = "Payée"
    note: str = ""


@app.post("/api/depenses")
def add_depense(d: Depense):
    return store.add("depenses", d.model_dump())


@app.delete("/api/depenses/{row_id}")
def del_depense(row_id: str):
    if not store.delete("depenses", row_id):
        raise HTTPException(404, "Dépense introuvable")
    return {"ok": True}


# --- Suivi des commandes ----------------------------------------------------
class Suivi(BaseModel):
    statut: Literal[tuple(metrics.STATUTS)]  # type: ignore[valid-type]
    numero_suivi: str = ""
    note: str = ""
    client: str = ""
    doublon_verifie: Optional[bool] = None  # True : « Pas un doublon » confirmé à la main


@app.put("/api/commandes/{commande_id}/suivi")
def set_suivi(commande_id: str, s: Suivi):
    fields = s.model_dump()
    if not fields["client"]:
        fields.pop("client")
    if fields["doublon_verifie"] is None:
        fields.pop("doublon_verifie")
    previous = next((r for r in store.read("suivi") if r.get("commande") == commande_id), {})
    if previous.get("statut") != s.statut:
        fields["date"] = datetime.now(metrics.PARIS).date().isoformat()
    return store.upsert_suivi(commande_id, fields)


@app.get("/api/commandes.csv")
def export_commandes():
    data = _dashboard()
    cols = [("date", "Date"), ("client", "Client"), ("email", "E-mail"), ("telephone", "Téléphone"),
            ("adresse", "Adresse"), ("code_postal", "Code postal"), ("ville", "Ville"),
            ("livraison", "Livraison"), ("quantite", "Quantité"), ("montant", "Montant (€)"),
            ("statut", "Statut"), ("numero_suivi", "N° de suivi"), ("source", "Connu par"),
            ("mot", "Mot pour les auteurs")]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([c[1] for c in cols] + ["Doublon possible"])
    for o in data["commandes"]:
        d = o.get("doublon")
        flag = (f"À vérifier : {', '.join(d['raisons'])}, {d['ecart']} après la commande de {d['avec_heure']}"
                if d and not o.get("doublon_verifie") else "")
        w.writerow([str(o[c[0]]).replace(".", ",") if c[0] == "montant" else o[c[0]] for c in cols] + [flag])
    name = f"commandes-parachute-{datetime.now(metrics.PARIS):%Y-%m-%d}.csv"
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


if __name__ == "__main__":
    import uvicorn

    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8037"))
    if not os.getenv("STRIPE_API_KEY", "").strip() or "xxx" in os.getenv("STRIPE_API_KEY", ""):
        print("Attention : aucune clé Stripe dans .env (copie .env.example en .env et renseigne STRIPE_API_KEY).")
    print(f"Dashboard des ventes : http://{host}:{port} (données : {store.files.describe()})")
    uvicorn.run(app, host=host, port=port)
