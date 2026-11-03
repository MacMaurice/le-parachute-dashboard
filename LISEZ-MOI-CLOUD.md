# Dashboard des ventes en ligne (Cloud Run + IAP)

Le même dashboard que la version locale, mais à une adresse en ligne que seuls les comptes Google
autorisés peuvent ouvrir (toi, ton frère…). Quelqu'un qui n'est pas sur la liste tombe sur la page
de connexion Google, puis sur un refus : il ne voit jamais rien du dashboard.

- Le serveur tourne sur **Cloud Run** (Google Cloud), en Belgique (`europe-west1`).
- Les données saisies à la main (stock, suivi des commandes, dépenses, réglages) sont dans un
  **bucket Cloud Storage**, au même format JSON que le dossier `data/`.
- La clé Stripe (lecture seule) est dans **Secret Manager** : elle n'est ni dans le code ni dans l'image.
- **IAP** (Identity-Aware Proxy) filtre les visiteurs par compte Google, avant même que le serveur réponde.

## Ce que ça coûte

Tout reste sous les paliers gratuits de Google Cloud pour deux ou trois utilisateurs :

| Service | Gratuit jusqu'à | Usage du dashboard |
|---|---|---|
| Cloud Run | 2 millions de requêtes par mois | quelques centaines |
| IAP | gratuit pour une appli hébergée sur Google Cloud (sans load balancer) | |
| Secret Manager | 6 versions de secret | 1 |
| Artifact Registry (images) | 0,5 Go | 2 images gardées, le script supprime les anciennes |
| Cloud Build | 120 minutes de build par jour | 3 à 5 min par déploiement |
| Cloud Storage | 5 Go aux États-Unis ; en Europe, 0,02 $ par Go et par mois | quelques Ko, soit 0,00 € |

Google exige quand même une carte bancaire sur le compte de facturation. L'alerte de budget de
l'étape 1 prévient par e-mail au moindre centime (elle ne coupe rien, elle alerte).

## Étape 0 : installer gcloud (une seule fois)

Si la commande `gcloud` n'existe pas encore dans PowerShell : installer Google Cloud CLI
(https://cloud.google.com/sdk/docs/install), puis :

```
gcloud auth login
```

Se connecter avec le compte Google qui possédera le projet (conseillé : leparachute37@gmail.com,
pour que tout reste rattaché au Parachuté). Ne pas utiliser de compte Renault.

## Étape 1 : projet et facturation (console, 5 minutes)

Sur https://console.cloud.google.com, connecté avec ce même compte :

1. **Créer un projet** : nom `le-parachute-dashboard`. Noter son **ID** (affiché sous le nom ; il peut
   avoir un suffixe si le nom est déjà pris ailleurs).
2. **Facturation** : associer le projet à un compte de facturation (le créer si besoin, avec la carte).
3. **Facturation > Budgets et alertes > Créer un budget** : montant 1 €, seuils 50 % et 100 %,
   alertes par e-mail.

## Étape 2 : client OAuth pour la connexion Google (console, 5 minutes)

Comme vos comptes sont des Gmail personnels (sans organisation), IAP a besoin de son propre
client OAuth. Dans le projet, menu **Google Auth Platform** (ou API et services > Écran de consentement OAuth) :

1. **Premiers pas** : nom de l'application « Dashboard Le Parachuté », e-mail d'assistance,
   audience **Externe**, e-mail de contact. Accepter et créer.
2. **Audience > Utilisateurs test** : ajouter les adresses qui auront accès (les mêmes que pour le script).
3. **Clients > Créer un client** : type **Application Web**, nom « IAP dashboard ». Créer, puis copier
   l'**ID client** et le **code secret** (le script les demandera).
4. Rouvrir ce client et ajouter dans **URI de redirection autorisés** :
   `https://iap.googleapis.com/v1/oauth/clientIds/ID_CLIENT:handleRedirect`
   en remplaçant `ID_CLIENT` par l'ID client copié. Enregistrer.

## Étape 3 : déployer

Dans PowerShell :

```
cd "C:\Users\Hugo\OneDrive\Documents\Magouilles Politiciennes\Le Parachuté\7 - Commercialisation\dashboard-ventes (cloud run)"
powershell -ExecutionPolicy Bypass -File .\deployer.ps1 -Projet ID_DU_PROJET -Emails hugomaurice14@gmail.com,leparachute37@gmail.com,adresse-du-frere@gmail.com
```

Le script (3 à 10 minutes la première fois) :

1. active les services Google Cloud nécessaires ;
2. crée le bucket `ID_DU_PROJET-donnees` et y copie les fichiers actuels de `data/` (première fois seulement) ;
3. range la clé Stripe du fichier `.env` dans Secret Manager ;
4. construit l'image et la déploie sur Cloud Run, avec une seule instance au maximum
   (aucun conflit d'écriture) et zéro instance quand personne ne s'en sert (gratuit) ;
5. active IAP, y branche le client OAuth et autorise les adresses passées dans `-Emails` ;
6. affiche l'adresse du dashboard.

Les droits IAP peuvent mettre 2 à 5 minutes à s'appliquer : un refus juste après le déploiement est normal.

Le fichier `.env` et le dossier `data/` ne sont jamais envoyés dans l'image (voir `.gcloudignore`).

## Étape 4 : ouvrir le dashboard

Ouvrir l'adresse affichée (`https://dashboard-ventes-….europe-west1.run.app`) et se connecter avec un
compte autorisé. La première ouverture de la journée prend quelques secondes (démarrage du serveur).
Google peut afficher « application non validée » : c'est votre propre application, cliquer sur Continuer.

Mettre l'adresse en favori (et sur l'écran d'accueil du téléphone : Partager > Sur l'écran d'accueil) :
ton frère et toi voyez la même interface et les mêmes données, en temps réel.
Une redirection `/admin` depuis le site du Parachuté reste possible plus tard (une ligne dans `netlify.toml`).

## Ajouter ou retirer une personne

Ajouter l'adresse dans **Audience > Utilisateurs test** (étape 2), puis :

```
gcloud iap web add-iam-policy-binding --member=user:adresse@gmail.com --role=roles/iap.httpsResourceAccessor --region=europe-west1 --resource-type=cloud-run --service=dashboard-ventes --condition=None
```

Pour retirer : même commande avec `remove-iam-policy-binding`. Pour voir la liste :
`gcloud iap web get-iam-policy --region=europe-west1 --resource-type=cloud-run --service=dashboard-ventes`.

## Mettre à jour le dashboard

Après une modification du code, relancer la commande de l'étape 3 : seule l'application est
redéployée, les données du bucket ne bougent pas.

## Les données, maintenant

À partir du déploiement, **les données de référence sont dans le bucket**. Le dossier `data/` local
n'est plus qu'une copie du jour de la migration : ne plus saisir dans la version locale
(`dashboard-ventes`), sinon les deux versions divergent.

- **Récupérer une sauvegarde** : `gcloud storage cp "gs://ID_DU_PROJET-donnees/data/*.json" data/`
- **Faire tourner le dashboard local sur les données en ligne** : une fois
  `gcloud auth application-default login`, puis ajouter `GCS_BUCKET=ID_DU_PROJET-donnees` dans `.env`
  et lancer `python app.py` comme d'habitude (après `pip install -r requirements.txt`).
- Claude peut toujours mettre les fichiers à jour (« on a reçu 1000 jeux le 3 décembre ») via `gcloud storage`.

## Tout arrêter

```
gcloud run services delete dashboard-ventes --region=europe-west1
```

Ou supprimer le projet entier dans la console (les données du bucket partent avec : faire une sauvegarde avant).
