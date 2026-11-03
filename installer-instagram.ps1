<#
  Installe la collecte Instagram quotidienne sur Google Cloud (une seule fois).

  Avant : 1) le dashboard est redéployé avec l'onglet Instagram (Claude s'en charge) ;
          2) le token est rangé dans Google Cloud : dans le dossier de l'outil Instagram,
             python parachute_ig.py migrer-cloud

  Puis, depuis ce dossier :
    powershell -ExecutionPolicy Bypass -File .\installer-instagram.ps1

  Ce que ça crée (tout gratuit) :
  - un compte de service « collecte-instagram » qui peut lire/renouveler le token et écrire dans le bucket ;
  - une tâche Cloud Run « collecte-instagram » (même image que le dashboard, lance collecte_ig.py) ;
  - un déclenchement Cloud Scheduler deux fois par jour, à 11 h 07 et 23 h 07 (heure de Paris),
    pour ne pas rater les stories qui disparaissent au bout de 24 h ;
  - puis lance une première collecte tout de suite.
  Relançable sans risque.
#>
param(
  [string] $Projet  = "le-parachute",
  [string] $Region  = "europe-west1",
  [string] $Bucket  = "le-parachute-donnees",
  [string] $Secret  = "instagram-token",
  [string] $Heure   = "7 11,23 * * *"
)
$ErrorActionPreference = "Continue"
$Job = "collecte-instagram"
$SA = "$Job@$Projet.iam.gserviceaccount.com"
$ClaudeSA = "claude-ops@$Projet.iam.gserviceaccount.com"

function Etape([string]$t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function G { & gcloud @args; if ($LASTEXITCODE -ne 0) { Write-Host "Échec de : gcloud $($args -join ' ')" -ForegroundColor Red; exit 1 } }
function Existe { & gcloud @args *> $null; return ($LASTEXITCODE -eq 0) }

G config set project $Projet

Etape "Vérifications"
if (-not (Existe secrets describe $Secret)) {
  Write-Host "Le token Instagram n'est pas encore dans Google Cloud. Dans le dossier de l'outil Instagram, lance d'abord :" -ForegroundColor Red
  Write-Host "  python parachute_ig.py migrer-cloud" -ForegroundColor Red
  exit 1
}
$image = (& gcloud run services describe dashboard-ventes "--region=$Region" '--format=value(spec.template.spec.containers[0].image)')
if (-not $image) { Write-Host "Service dashboard-ventes introuvable." -ForegroundColor Red; exit 1 }
Write-Host "Image utilisée : $image"

Etape "Activation de Cloud Scheduler"
G services enable cloudscheduler.googleapis.com

Etape "Compte de service $Job"
if (-not (Existe iam service-accounts describe $SA)) {
  G iam service-accounts create $Job --display-name "Collecte Instagram du dashboard"
  Start-Sleep -Seconds 10
}
G secrets add-iam-policy-binding $Secret "--member=serviceAccount:$SA" --role=roles/secretmanager.secretAccessor --format=none
G secrets add-iam-policy-binding $Secret "--member=serviceAccount:$SA" --role=roles/secretmanager.secretVersionManager --format=none
G storage buckets add-iam-policy-binding "gs://$Bucket" "--member=serviceAccount:$SA" --role=roles/storage.objectAdmin --format=none
# Claude (claude-ops) pourra mettre à jour l'image de la tâche quand il redéploie le dashboard
G iam service-accounts add-iam-policy-binding $SA "--member=serviceAccount:$ClaudeSA" --role=roles/iam.serviceAccountUser --format=none

Etape "Tâche Cloud Run $Job"
G run jobs deploy $Job "--image=$image" "--region=$Region" "--service-account=$SA" `
  --command=python --args=collecte_ig.py `
  "--set-env-vars=GCS_BUCKET=$Bucket,GCP_PROJECT=$Projet,IG_SECRET=$Secret" `
  --max-retries=1 --task-timeout=900 --memory=512Mi --cpu=1 --quiet
G run jobs add-iam-policy-binding $Job "--region=$Region" "--member=serviceAccount:$SA" --role=roles/run.invoker --format=none

Etape "Déclenchement deux fois par jour ($Heure, heure de Paris)"
$uri = "https://run.googleapis.com/v2/projects/$Projet/locations/$Region/jobs/${Job}:run"
if (Existe scheduler jobs describe "$Job-quotidienne" "--location=$Region") {
  G scheduler jobs update http "$Job-quotidienne" "--location=$Region" "--schedule=$Heure" --time-zone=Europe/Paris `
    "--uri=$uri" --http-method=POST "--oauth-service-account-email=$SA" --format=none
} else {
  G scheduler jobs create http "$Job-quotidienne" "--location=$Region" "--schedule=$Heure" --time-zone=Europe/Paris `
    "--uri=$uri" --http-method=POST "--oauth-service-account-email=$SA" --format=none
}

Etape "Première collecte (1 à 3 minutes)"
& gcloud run jobs execute $Job "--region=$Region" --wait
if ($LASTEXITCODE -ne 0) {
  Write-Host "La première collecte a échoué : copie ce message à Claude, il lira les journaux." -ForegroundColor Red; exit 1
}
Etape "Terminé"
Write-Host "La collecte tourne à 11 h et 23 h. Ouvre l'onglet Instagram du dashboard." -ForegroundColor Green
