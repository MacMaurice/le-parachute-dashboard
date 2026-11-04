# Dashboard de ventes — Le Parachuté

App FastAPI (`app.py`) + front statique (`static/`), déployée sur Cloud Run (projet GCP `le-parachute`, service `dashboard-ventes`, europe-west1, accès par IAP).

## Où on en est
- **Ce dossier est l'unique version du dashboard.** Les anciennes copies sont dans `../_archives/` (lecture seule, ne plus y travailler).
- Branches git :
  - `main` = ce qui tourne sur Cloud Run (révision 00004, avec Instagram).
  - `dev` = travail en cours (Instagram, retouches). On travaille ici.
  - `carte` = ancienne branche de la carte électorale, fusionnée dans `dev` le 04/11 (gardée pour l'historique).
- Après un déploiement depuis `dev` : `git checkout main && git merge dev`.

## Règles
- Ne pas modifier `main` directement : travailler sur `dev` (ou une branche dédiée). Git remplace les copies de dossiers et les fichiers `.bak`.
- Déployer seulement quand Hugo le demande (`deployer.ps1`, PowerShell sur son PC).
- Secrets dans `.env` (jamais commité). `data/` = données locales, ignorées par git.
- Textes de l'interface en français, typo Sansation, couleurs `#1B476A` (marine) et `#F5CD2D` (jaune).

## Lancer en local
`python app.py` (lit `.env`), puis http://127.0.0.1:8037.
Tests : `python -m pytest tests`.

## Mode démo (sans Stripe ni Google Cloud)
`DEMO=1 python app.py` (PowerShell : `$env:DEMO="1"; python app.py`). Données fictives générées par `demo.py`, écrites dans un dossier temporaire : rien ne touche aux vraies données. À utiliser pour vérifier une modification (capture Playwright) avant de la montrer ou de la déployer.
