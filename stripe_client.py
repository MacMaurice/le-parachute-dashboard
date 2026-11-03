"""Lecture des données Stripe (lecture seule) avec un petit cache mémoire."""
from __future__ import annotations

import threading
import time
from typing import Any

import httpx

API_BASE = "https://api.stripe.com/v1"


class StripeError(Exception):
    pass


class StripeClient:
    def __init__(self, api_key: str, payment_links: list[str] | None = None,
                 ttl_seconds: int = 60, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=API_BASE, auth=(api_key, ""), timeout=30,
                                  transport=transport)
        self._links_wanted = [p for p in (payment_links or []) if p]
        self._ttl = ttl_seconds
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0
        self._lock = threading.Lock()

    # --- bas niveau -------------------------------------------------------
    def _get(self, path: str, params: list[tuple[str, Any]] | None = None) -> dict:
        try:
            r = self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise StripeError(f"Stripe injoignable : {exc}") from exc
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except ValueError:
                msg = r.text
            raise StripeError(f"Stripe a refusé {path} ({r.status_code}) : {msg}")
        return r.json()

    def _list_all(self, path: str, params: list[tuple[str, Any]] | None = None,
                  max_pages: int = 30) -> list[dict]:
        base = list(params or []) + [("limit", 100)]
        items: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            p = base + ([("starting_after", cursor)] if cursor else [])
            page = self._get(path, p)
            items.extend(page.get("data", []))
            if not page.get("has_more") or not page.get("data"):
                break
            cursor = page["data"][-1]["id"]
        return items

    # --- données utiles au dashboard --------------------------------------
    def _fetch(self) -> dict[str, Any]:
        warnings: list[str] = []

        def optional(label: str, fn, default, silent: bool = False):
            try:
                return fn()
            except StripeError as exc:
                if not silent:
                    warnings.append(f"{label} : {exc}")
                return default

        links = optional("Liens de paiement", lambda: self._list_all("/payment_links"), [])
        if self._links_wanted:
            chosen = [l for l in links if l["id"] in self._links_wanted]
            chosen_ids = list(self._links_wanted)
        else:
            # Par défaut : tous les liens sauf ceux marqués comme tests (metadata.usage = test_…)
            chosen = [l for l in links
                      if not str((l.get("metadata") or {}).get("usage", "")).startswith("test")]
            chosen_ids = [l["id"] for l in chosen]

        sessions: list[dict] = []
        if chosen_ids:
            for link_id in chosen_ids:
                sessions += self._list_all("/checkout/sessions", [
                    ("payment_link", link_id), ("expand[]", "data.line_items")])
        else:
            sessions = self._list_all("/checkout/sessions", [("expand[]", "data.line_items")])

        charges = optional("Paiements (charges)", lambda: self._list_all("/charges"), [])
        balance_tx = optional("Mouvements du solde",
                              lambda: self._list_all("/balance_transactions"), [])
        balance = optional("Solde", lambda: self._get("/balance"), {})
        # Facultatif : sans ce droit, le mode de livraison est déduit du montant du port.
        rates = optional("Tarifs de livraison",
                         lambda: self._list_all("/shipping_rates"), [], silent=True)

        payouts = optional("Versements (Payouts)", lambda: self._list_all("/payouts"), [])
        disputes = optional("Contestations (Disputes)", lambda: self._list_all("/disputes"), [])

        return {
            "fetched_at": time.time(),
            "payment_links": chosen,
            "sessions": sessions,
            "charges": charges,
            "balance_transactions": balance_tx,
            "balance": balance,
            "shipping_rates": rates,
            "payouts": payouts,
            "disputes": disputes,
            "warnings": warnings,
        }

    def snapshot(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            fresh = self._cache and (time.time() - self._cache_at) < self._ttl
            if force or not fresh:
                self._cache = self._fetch()
                self._cache_at = time.time()
            return self._cache
