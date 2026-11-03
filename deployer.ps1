<#
  Déploie le dashboard des ventes sur Cloud Run, protégé par IAP (accès réservé à une liste de comptes Google).

  À lancer depuis ce dossier, APRÈS les étapes console du LISEZ-MOI-CLOUD.md (projet, facturation, client OAuth) :

    powershell -ExecutionPolicy Bypass -File .\deployer.ps1 -Projet le-parachute-dashboard `
      -Emails hugomaurice14@gmail.com,leparachute37@gmail.com,adresse-du-frere@gmail.com

  Le script peut être relancé sans risque : il ne recrée pas ce qui existe déjà. Relancé après une
  modification du code, il redéploie simplement la nouvelle version.
#>
param(
  [Parameter(Mandatory = $true)] [string]   $Projet,
  [Parameter(Mandatory = $true)] [string[]] $Emails,
  [string] $Region  = "europe-west1",
  [string] $Service = "dashboard-ventes",
  [string] $Bucket  = "",
  [string] $OAuthClientId = "",
  [string] $OAuthClientSecret = ""
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
# Avec « powershell -File », a@x,b@y arrive en une seule chaîne : on découpe.
$Emails = @($Emails | ForEach-Object { $_ -split '[,;\s]+' } | Where-Object { $_ })
if (-not $Bucket) { $Bucket = "$Projet-donnees" }
$SA = "$Service@$Projet.iam.gserviceaccount.com"
$SecretName = "stripe-api-key"

function Etape([string]$texte) { Write-Host ""; Write-Host "== $texte" -ForegroundColor Cyan }
function G {
  # Lance gcloud et s'arrête à la première erreur.
  & gcloud @args
  if ($LASTEXITCODE -ne 0) { Write-Host "Échec de : gcloud $($args -join ' ')" -ForegroundColor Red; exit 1 }
}
function Existe { & gcloud @args *> $null; return ($LASTEXITCODE -eq 0) }
function Lire-Env([string]$cle) {
  if (-not (Test-Path .env)) { return "" }
  foreach ($l in Get-Content .env -Encoding UTF8) {
    if ($l -match "^\s*$cle\s*=\s*(.*?)\s*$") { return $Matches[1] }
  }
  return ""
}
function Ecrire-SansBom([string]$chemin, [string]$texte) {
  [IO.File]::WriteAllText((Join-Path $PSScriptRoot $chemin), $texte, (New-Object Text.UTF8Encoding($false)))
}

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
  Write-Host "gcloud est introuvable : installe Google Cloud CLI (voir LISEZ-MOI-CLOUD.md)." -ForegroundColor Red; exit 1
}

Etape "Projet $Projet"
G config set project $Projet
$numero = (& gcloud projects describe $Projet '--format=value(projectNumber)')
if (-not $numero) { Write-Host "Projet introuvable ou inaccessible avec ce compte." -ForegroundColor Red; exit 1 }
$facturation = (& gcloud billing projects describe $Projet '--format=value(billingEnabled)' 2> $null)
if ($facturation -eq "False") {
  Write-Host "La facturation n'est pas activée sur ce projet (obligatoire pour Cloud Run, même gratuit). Voir LISEZ-MOI-CLOUD.md, étape 1." -ForegroundColor Red; exit 1
}

Etape "Activation des API (une seule fois, peut prendre 1 à 2 minutes)"
G services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com `
  secretmanager.googleapis.com iap.googleapis.com storage.googleapis.com cloudresourcemanager.googleapis.com

Etape "Compte de service du dashboard"
if (-not (Existe iam service-accounts describe $SA)) {
  G iam service-accounts create $Service --display-name "Dashboard des ventes"
}
# Le build (--source) utilise le compte Compute par défaut : il lui faut le rôle Cloud Run Builder.
G projects add-iam-policy-binding $Projet "--member=serviceAccount:$numero-compute@developer.gserviceaccount.com" `
  --role=roles/run.builder --condition=None --quiet --format=none

Etape "Bucket des données : gs://$Bucket"
if (-not (Existe storage buckets describe "gs://$Bucket")) {
  G storage buckets create "gs://$Bucket" "--location=$Region" --uniform-bucket-level-access --public-access-prevention
}
$deja = (& gcloud storage ls "gs://$Bucket/data/" 2> $null)
if (-not $deja) {
  Write-Host "Premier déploiement : copie des fichiers data/*.json actuels dans le bucket."
  G storage cp "data/*.json" "gs://$Bucket/data/"
} else {
  Write-Host "Le bucket contient déjà des données : elles sont conservées (data/ local n'est pas recopié)."
}
G storage buckets add-iam-policy-binding "gs://$Bucket" "--member=serviceAccount:$SA" --role=roles/storage.objectAdmin --format=none

Etape "Clé Stripe dans Secret Manager"
if (-not (Existe secrets describe $SecretName)) {
  $cle = Lire-Env "STRIPE_API_KEY"
  if (-not $cle -or $cle -like "*xxx*") { Write-Host "Clé Stripe introuvable dans .env" -ForegroundColor Red; exit 1 }
  Ecrire-SansBom "cle.tmp" $cle
  & gcloud secrets create $SecretName --replication-policy=user-managed "--locations=$Region" --data-file=cle.tmp
  $code = $LASTEXITCODE
  Remove-Item cle.tmp -Force
  if ($code -ne 0) { exit 1 }
}
G secrets add-iam-policy-binding $SecretName "--member=serviceAccount:$SA" --role=roles/secretmanager.secretAccessor --format=none

Etape "Construction et déploiement sur Cloud Run (3 à 5 minutes)"
$liens = Lire-Env "STRIPE_PAYMENT_LINKS"
Ecrire-SansBom "env.tmp.yaml" ("GCS_BUCKET: `"$Bucket`"`nSTRIPE_PAYMENT_LINKS: `"$liens`"`nCACHE_SECONDES: `"60`"`n")
& gcloud run deploy $Service --source . "--region=$Region" --no-allow-unauthenticated --iap `
  "--service-account=$SA" --min-instances=0 --max-instances=1 --memory=512Mi --cpu=1 --concurrency=20 `
  "--set-secrets=STRIPE_API_KEY=${SecretName}:latest" --env-vars-file=env.tmp.yaml --quiet
$code = $LASTEXITCODE
Remove-Item env.tmp.yaml -Force
if ($code -ne 0) { Write-Host "Le déploiement a échoué (voir le message ci-dessus)." -ForegroundColor Red; exit 1 }

Etape "Images Docker : ne garder que les 2 dernières (rester sous les 0,5 Go gratuits)"
Ecrire-SansBom "nettoyage.tmp.json" '[{"name":"garder-2-dernieres","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":2}},{"name":"supprimer-le-reste","action":{"type":"Delete"},"condition":{"tagState":"any"}}]'
& gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy "--location=$Region" --policy=nettoyage.tmp.json --no-dry-run --format=none
Remove-Item nettoyage.tmp.json -Force

Etape "IAP : client OAuth et liste des personnes autorisées"
& gcloud beta services identity create --service=iap.googleapis.com "--project=$Projet" --quiet *> $null
& gcloud run services add-iam-policy-binding $Service "--region=$Region" `
  "--member=serviceAccount:service-$numero@gcp-sa-iap.iam.gserviceaccount.com" --role=roles/run.invoker --format=none
if ($LASTEXITCODE -ne 0) {
  Write-Host "Le compte de service IAP n'existe pas encore. Lance « gcloud components install beta », attends 1 minute puis relance ce script." -ForegroundColor Red; exit 1
}

$reglages = (& gcloud iap settings get "--project=$Projet" --format=json 2> $null) | Out-String
if ($reglages -notmatch "client_?id") {
  if (-not $OAuthClientId)     { $OAuthClientId = Read-Host "ID client OAuth (étape 2 du LISEZ-MOI-CLOUD.md)" }
  if (-not $OAuthClientSecret) { $OAuthClientSecret = Read-Host "Code secret du client OAuth" }
  Ecrire-SansBom "iap_settings.tmp.yaml" ("access_settings:`n  oauth_settings:`n    client_id: $OAuthClientId`n    client_secret: $OAuthClientSecret`n")
  & gcloud iap settings set iap_settings.tmp.yaml "--project=$Projet"
  $code = $LASTEXITCODE
  Remove-Item iap_settings.tmp.yaml -Force
  if ($code -ne 0) { exit 1 }
}
foreach ($e in $Emails) {
  G iap web add-iam-policy-binding "--member=user:$($e.Trim())" --role=roles/iap.httpsResourceAccessor `
    "--region=$Region" --resource-type=cloud-run "--service=$Service" --condition=None --format=none --quiet
  Write-Host "Accès autorisé : $($e.Trim())"
}

$url = (& gcloud run services describe $Service "--region=$Region" '--format=value(status.url)')
Etape "Terminé"
Write-Host "Dashboard en ligne : $url" -ForegroundColor Green
Write-Host "Seuls ces comptes Google peuvent l'ouvrir : $($Emails -join ', ')"
Write-Host "Mets cette adresse en favori (ordinateur et téléphone) et envoie-la à ton frère."
