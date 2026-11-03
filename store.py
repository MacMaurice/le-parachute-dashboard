"""Données saisies à la main (stock, suivi des commandes, dépenses), stockées en JSON.

Deux emplacements possibles, au choix par variable d'environnement :
- par défaut, le dossier data/ (fichiers lisibles et modifiables à la main, ou par Claude) ;
- si GCS_BUCKET est renseigné, un bucket Google Cloud Storage (version en ligne sur Cloud Run).
  Les fichiers y ont les mêmes noms, sous le préfixe GCS_PREFIX (« data/ » par défaut).
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

FILES = {
    "mouvements": "mouvements_stock.json",
    "suivi": "suivi_commandes.json",
    "depenses": "depenses.json",
}
SETTINGS = "reglages.json"


# --- Emplacements ------------------------------------------------------------
class LocalFiles:
    """Fichiers JSON dans un dossier local."""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def describe(self) -> str:
        return str(self.dir)

    def read_text(self, name: str) -> str | None:
        path = self.dir / name
        return path.read_text(encoding="utf-8") if path.exists() else None

    def write_text(self, name: str, text: str) -> None:
        path = self.dir / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)


class GcsFiles:
    """Fichiers JSON dans un bucket Cloud Storage (chaque écriture remplace l'objet en entier)."""

    def __init__(self, bucket: str, prefix: str = "data/", client=None):
        if client is None:
            from google.cloud import storage  # importé seulement si le bucket est utilisé
            client = storage.Client()
        self.bucket = client.bucket(bucket)
        self.prefix = prefix if not prefix or prefix.endswith("/") else prefix + "/"

    def describe(self) -> str:
        return f"gs://{self.bucket.name}/{self.prefix}"

    def read_text(self, name: str) -> str | None:
        blob = self.bucket.blob(self.prefix + name)
        try:
            return blob.download_as_bytes().decode("utf-8")
        except Exception as exc:  # NotFound : le fichier n'existe pas encore
            if exc.__class__.__name__ == "NotFound":
                return None
            raise

    def write_text(self, name: str, text: str) -> None:
        self.bucket.blob(self.prefix + name).upload_from_string(
            text.encode("utf-8"), content_type="application/json; charset=utf-8")


# --- Store -------------------------------------------------------------------
def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


class Store:
    def __init__(self, backend: LocalFiles | GcsFiles | Path | str):
        self.files = backend if isinstance(backend, (LocalFiles, GcsFiles)) else LocalFiles(Path(backend))
        self._lock = threading.Lock()
        for name in FILES.values():
            if self.files.read_text(name) is None:
                self.files.write_text(name, "[]\n")

    @classmethod
    def from_env(cls, root: Path) -> "Store":
        bucket = os.getenv("GCS_BUCKET", "").strip()
        if bucket:
            return cls(GcsFiles(bucket, os.getenv("GCS_PREFIX", "data/")))
        return cls(LocalFiles(Path(os.getenv("DATA_DIR") or root / "data")))

    def _read_json(self, name: str, empty: Any) -> Any:
        text = (self.files.read_text(name) or "").strip()
        data = json.loads(text) if text else empty
        return data if isinstance(data, type(empty)) else empty

    def read(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_json(FILES[kind], [])

    def _write(self, kind: str, rows: list[dict[str, Any]]) -> None:
        self.files.write_text(FILES[kind], _dump(rows))

    def add(self, kind: str, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            rows = self._read_json(FILES[kind], [])
            row = {"id": uuid.uuid4().hex[:8], **row}
            rows.append(row)
            self._write(kind, rows)
        return row

    def delete(self, kind: str, row_id: str) -> bool:
        with self._lock:
            rows = self._read_json(FILES[kind], [])
            kept = [r for r in rows if r.get("id") != row_id]
            if len(kept) == len(rows):
                return False
            self._write(kind, kept)
        return True

    def read_settings(self) -> dict[str, Any]:
        with self._lock:
            return self._read_json(SETTINGS, {})

    def write_settings(self, fields: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = {**self._read_json(SETTINGS, {}), **fields}
            self.files.write_text(SETTINGS, _dump(data))
        return data

    def upsert_suivi(self, commande: str, fields: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            rows = self._read_json(FILES["suivi"], [])
            for r in rows:
                if r.get("commande") == commande:
                    r.update(fields)
                    break
            else:
                r = {"commande": commande, **fields}
                rows.append(r)
            self._write("suivi", rows)
        return r
