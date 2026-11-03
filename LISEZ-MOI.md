# Dashboard des ventes du Parachuté

Petite application locale (FastAPI + une page HTML) qui suit la commercialisation du jeu :
ventes Stripe, commandes et clients, stock du tirage, dépenses et résultat.

> Version en ligne (Cloud Run, accès réservé à une liste de comptes Google) : voir `LISEZ-MOI-CLOUD.md`.

## Lancer le dashboard

Toutes les commandes se tapent dans ce dossier. Dans PowerShell, commencer par :

```
cd "C:\Users\Hugo\OneDrive\Documents\Magouilles Politiciennes\Le Parachuté\7 - Commercialisation\dashboard-ventes"
```

Première fois (Python 3.10 ou plus récent) :

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Ouvrir `.env` et y coller la clé Stripe (voir plus bas). Ensuite, à chaque fois :

```
.venv\Scripts\activate
python app.py
```

Le dashboard est sur http://127.0.0.1:8037. `Ctrl+C` dans le terminal pour l'arrêter.

## Créer la clé Stripe en lecture seule

Stripe > Développeurs > Clés API > **Créer une clé restreinte**, nom « Dashboard ventes », puis mettre
en **Lecture** uniquement :

- Balance (solde et mouvements du solde)
- Charges
- Checkout Sessions
- Payment Links
- Shipping Rates (facultatif : sans lui, le mode de livraison est déduit du montant du port)
- Payouts (virements de Stripe vers le compte bancaire)
- Disputes (contestations de paiement par un client)

Tout le reste sur « Aucun ». La clé commence par `rk_live_`. Elle ne permet ni de rembourser,
ni de modifier quoi que ce soit. Si un droit manque, le dashboard l'affiche dans un bandeau jaune.

## Ce qui vient de Stripe, ce qui est saisi à la main

| Donnée | Source |
|---|---|
| Commandes, clients, adresses, mode de livraison, « Comment avez-vous connu le jeu ? », mot pour les auteurs | Stripe (lien de paiement) |
| Remboursements, frais Stripe, solde, virements vers le compte bancaire, contestations de paiement | Stripe |
| Statut de livraison et numéro de suivi de chaque commande | Saisi dans l'onglet Commandes → `data/suivi_commandes.json` |
| Tirage, réceptions, ventes hors Stripe (salon, espèces), jeux offerts ou abîmés | Onglet Stock → `data/mouvements_stock.json` |
| Dépenses (fabrication, envois, pub…) | Onglet Finances → `data/depenses.json` |

Les fichiers du dossier `data/` sont du JSON lisible : Claude peut aussi les mettre à jour
quand vous lui donnez une info (« on a reçu 1000 jeux le 3 décembre », « 5 jeux vendus au salon »…).

## Ne pas compter vos propres consultations du lien de paiement

Dans la carte « Lien de paiement » (Vue d'ensemble), le bloc « Exclure vos consultations » propose trois filtres, enregistrés dans `data/reglages.json` :

- **Le lien de test** : la même page de paiement avec `?client_reference_id=interne` à la fin. Toute ouverture par ce lien est ignorée. C'est celui à utiliser pour vos tests.
- **Ne compter qu'à partir du** : les ouvertures avant cette date et heure sont ignorées (par exemple l'heure du post Instagram de lancement).
- **E-mails internes** : une session payée ou abandonnée avec l'un de ces e-mails est ignorée.

Ces filtres ne touchent que le taux de conversion : une vraie commande payée reste toujours dans les commandes.

## Les calculs

- **Disponibles à la vente** = tirage − vendus en ligne − vendus en direct − offerts − abîmés (± ajustements)
- **Stock physique** = reçus − commandes en ligne livrées − vendus en direct − offerts − abîmés (± ajustements)
- **À livrer** = jeux vendus en ligne pas encore « Expédiée » ou « Remise en main propre ».
  Tant qu'aucune réception n'est saisie, les commandes sont « En attente du stock » ; ensuite « À préparer ».
- **Chiffre d'affaires** = paiements Stripe (port compris, remboursements déduits) + ventes hors Stripe.
- **Résultat** = chiffre d'affaires − frais Stripe − dépenses saisies (payées ou à payer).
- **Coût de fabrication par jeu** = dépenses « Fabrication » + « Transport du stock » ÷ tirage.
- **Seuil de rentabilité** = dépenses totales ÷ prix moyen d'un jeu vendu (hors port, avant frais Stripe), arrondi au jeu supérieur.
- Seules les ventes du lien de paiement principal sont comptées (`STRIPE_PAYMENT_LINKS` dans `.env`) :
  le lien de test à 1 € est ignoré.

## Fichiers

```
app.py              serveur FastAPI (API + page), à lancer avec python app.py
stripe_client.py    lecture de l'API Stripe, cache 60 s
metrics.py          calcul des indicateurs
store.py            lecture/écriture des fichiers data/*.json (ou du bucket GCS en ligne)
static/             page HTML, CSS, JS, logo et police Sansation
data/               données saisies à la main
.env                clé Stripe (à ne jamais partager ni mettre sur GitHub)
```
L'API est documentée sur http://127.0.0.1:8037/api/docs. Le serveur n'écoute que sur l'ordinateur
lui-même : il n'est pas accessible depuis le réseau.

## Doublons possibles

Le dashboard signale toute commande payée moins de 24 h après une autre commande de la même personne
(même e-mail, même carte bancaire ou même adresse de livraison) : bandeau jaune en haut de chaque onglet,
filtre « Doublons possibles » et badge « Doublon ? » dans l'onglet Commandes. En ouvrant la commande :

- si c'est un doublon, la rembourser dans Stripe : elle passe en « Remboursée » et l'alerte disparaît ;
- si le client voulait bien deux commandes, cliquer « Pas un doublon, garder les deux » (réversible).
