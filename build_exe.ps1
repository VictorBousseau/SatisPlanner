<#
.SYNOPSIS
    Construit l'executable Windows de SatisPlanner.

.DESCRIPTION
    Deux variantes, et la difference n'est pas technique mais juridique.

    Par defaut, le dossier satisplanner/resources/icons est embarque : c'est la
    variante confortable, pour un usage prive. Les icones appartiennent a Coffee Stain
    Studios et leur redistribution n'est pas autorisee, donc cette variante ne se
    publie pas.

    Avec -NoAssets, l'exe part sans aucune icone du jeu et tombe entierement sur le
    repli generatif de l'application, qui est un mode de fonctionnement nominal et non
    une degradation. C'est cette variante qui se distribue, accompagnee de la
    procedure d'extraction FModel decrite dans le README.

    Dans les deux cas, la base SQLite est embarquee : sans elle l'application n'a plus
    de catalogue et la palette est vide. Le script verifie apres coup que les donnees
    sont bien la, parce que le piege classique de l'empaquetage est un exe qui se
    lance parfaitement et n'affiche plus rien.

.PARAMETER NoAssets
    Produit la variante publiable, sans le dossier d'icones.

.PARAMETER Clean
    Efface build/ et dist/ avant de construire.

.PARAMETER Python
    Interpreteur a utiliser. Par defaut celui de l'environnement virtuel du projet.

.EXAMPLE
    .\build_exe.ps1
    .\build_exe.ps1 -NoAssets -Clean
#>

[CmdletBinding()]
param(
    [switch]$NoAssets,
    [switch]$Clean,
    [string]$Python = ".venv\Scripts\python.exe"
)

# Volontairement pas "Stop" : en PowerShell 5.1, la moindre ligne ecrite sur la sortie
# d'erreur par un executable natif -- et PyInstaller en ecrit beaucoup, y compris quand
# tout va bien -- devient une exception. Les echecs sont donc verifies explicitement,
# code de retour par code de retour.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$AppName      = "SatisPlanner"
$Entry        = "main.py"
$ResourceDir  = "satisplanner\resources"
$Database     = "$ResourceDir\game_1.2.sqlite"
$Catalogue    = "$ResourceDir\en.json"
$IconsDir     = "$ResourceDir\icons"
$AppIcon      = "$ResourceDir\satisplanner.ico"

if ($NoAssets) {
    $Variant = "publiable"
} else {
    $Variant = "complet"
}
$DistPath = "dist\$Variant"
$WorkPath = "build\$Variant"
$Bundle   = "$DistPath\$AppName"
$Internal = "$Bundle\_internal"

function Write-Step($message) {
    Write-Host ""
    Write-Host "== $message" -ForegroundColor Cyan
}

function Fail($message) {
    Write-Host "ECHEC : $message" -ForegroundColor Red
    exit 1
}

# --------------------------------------------------------------- prerequis

Write-Step "Verification des prerequis"

if (-not (Test-Path $Python)) {
    Fail "interpreteur introuvable : $Python. Creez l'environnement avec 'py -3.12 -m venv .venv'."
}

& $Python -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    Fail "PyInstaller absent. Installez les dependances de developpement : $Python -m pip install -e .[dev]"
}

if (-not (Test-Path $Database)) {
    Fail "base de donnees absente : $Database. Regenerez-la (voir README, section Donnees du jeu)."
}

if (-not (Test-Path $AppIcon)) {
    Fail "icone d'application absente : $AppIcon. Regenerez-la avec '$Python tools\make_app_icon.py'."
}

$IconCount = 0
if (Test-Path $IconsDir) {
    $IconCount = (Get-ChildItem $IconsDir -Recurse -File | Measure-Object).Count
}

if ($NoAssets) {
    Write-Host "Variante publiable : les $IconCount icone(s) locales ne seront PAS embarquees."
} elseif ($IconCount -eq 0) {
    Write-Host "Aucune icone locale a embarquer : l'exe utilisera le repli generatif." -ForegroundColor Yellow
} else {
    Write-Host "Variante complete : $IconCount icone(s) embarquee(s), usage prive uniquement." -ForegroundColor Yellow
}

if ($Clean) {
    Write-Step "Nettoyage"
    foreach ($path in @($DistPath, $WorkPath)) {
        if (Test-Path $path) {
            Remove-Item $path -Recurse -Force
            Write-Host "supprime : $path"
        }
    }
}

# ------------------------------------------------------------------ build

Write-Step "Construction ($Variant)"

# --onedir et non --onefile : un --onefile se decompresse dans un dossier temporaire
# a CHAQUE lancement, ce qui ajoute plusieurs secondes au demarrage d'une application
# qui en pese deja plus de cent megaoctets. Le dossier se compresse en zip pour
# l'envoi ; la lenteur, elle, ne se compresse pas.
$Arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--onedir",
    "--windowed",
    "--name", $AppName,
    "--distpath", $DistPath,
    "--workpath", $WorkPath,
    "--specpath", $WorkPath,
    "--icon", (Resolve-Path $AppIcon).Path,
    # Inclusion explicite des donnees. Le format est SOURCE;DESTINATION, et la
    # destination doit etre exactement satisplanner\resources : c'est la que
    # satisplanner.paths.resource_directory() ira les chercher une fois gele.
    # Les sources sont absolues : --specpath deplace le dossier de reference des
    # chemins relatifs, et une source relative serait cherchee sous build\.
    "--add-data", "$((Resolve-Path $Database).Path);satisplanner\resources",
    "--add-data", "$((Resolve-Path $AppIcon).Path);satisplanner\resources",
    "--add-data", "$((Resolve-Path $Catalogue).Path);satisplanner\resources",
    "--exclude-module", "tkinter",
    "--exclude-module", "pytest"
)

if (-not $NoAssets -and $IconCount -gt 0) {
    $Arguments += @("--add-data", "$((Resolve-Path $IconsDir).Path);satisplanner\resources\icons")
}
$Arguments += (Resolve-Path $Entry).Path

& $Python $Arguments
if ($LASTEXITCODE -ne 0) {
    Fail "PyInstaller s'est arrete avec le code $LASTEXITCODE."
}

# ------------------------------------------------------------ verification

Write-Step "Verification du contenu"

$Executable = "$Bundle\$AppName.exe"
if (-not (Test-Path $Executable)) {
    Fail "executable introuvable : $Executable"
}

$EmbeddedDatabase = "$Internal\satisplanner\resources\game_1.2.sqlite"
if (-not (Test-Path $EmbeddedDatabase)) {
    Fail "la base n'a pas ete embarquee ($EmbeddedDatabase). L'application demarrerait avec une palette vide."
}
Write-Host "base embarquee : OK"

$EmbeddedCatalogue = "$Internal\satisplanner\resources\en.json"
if (-not (Test-Path $EmbeddedCatalogue)) {
    Fail "le catalogue anglais n'a pas ete embarque ($EmbeddedCatalogue). L'interface anglaise resterait entierement en francais."
}
Write-Host "catalogue anglais embarque : OK"

$EmbeddedIcons = "$Internal\satisplanner\resources\icons"
if ($NoAssets) {
    if (Test-Path $EmbeddedIcons) {
        Fail "des icones du jeu ont ete embarquees alors que -NoAssets etait demande : cette variante ne doit pas etre publiee."
    }
    Write-Host "aucune icone du jeu embarquee : OK, variante publiable"
} elseif ($IconCount -gt 0) {
    $EmbeddedCount = (Get-ChildItem $EmbeddedIcons -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
    if ($EmbeddedCount -ne $IconCount) {
        Fail "$EmbeddedCount icone(s) embarquee(s) sur $IconCount attendues."
    }
    Write-Host "icones embarquees : $EmbeddedCount / $IconCount"
}

$SizeMb = [math]::Round((Get-ChildItem $Bundle -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB, 1)

Write-Step "Termine"
Write-Host "Executable : $Executable"
Write-Host "Taille du dossier : $SizeMb Mo"
Write-Host ""
Write-Host "Mesure du demarrage (Start-Process -Wait : PowerShell n'attend pas une"
Write-Host "application graphique lancee avec &) :"
Write-Host "  Measure-Command { Start-Process '$Executable' -ArgumentList '--startup-probe' -Wait }"
