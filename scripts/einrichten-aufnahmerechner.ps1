# CoThinker - Aufnahmerechner autonom einrichten (Windows)
#
# Richtet den STT-Server auf diesem Rechner ein: virtuelle Umgebung, Pakete,
# Zugangsschluessel, Firewall-Regel, Startskript.
#
# Aufruf (in PowerShell, NICHT als Administrator noetig ausser bei -Firewall):
#
#   powershell -ExecutionPolicy Bypass -File einrichten-aufnahmerechner.ps1
#
# Nochmal ausfuehren ist gefahrlos: vorhandene Schritte werden uebersprungen.

param(
  [string]$Repo      = "$env:USERPROFILE\cothinker-stt",
  [string]$Branch    = "eu-souveraen/infomaniak-whisper",
  [string]$ApiKey    = "",              # leer -> wird abgefragt
  [int]   $Port      = 5051,
  [string]$Backend   = "elevenlabs-scribe",  # oder "infomaniak-whisper" (EU)
  [switch]$Firewall,                    # braucht Administratorrechte
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
function Schritt($t) { Write-Host "`n=== $t" -ForegroundColor Cyan }
function Gut($t)     { Write-Host "  OK   $t" -ForegroundColor Green }
function Warn($t)    { Write-Host "  !    $t" -ForegroundColor Yellow }
function Fehler($t)  { Write-Host "  FEHL $t" -ForegroundColor Red }

# --- 1. Voraussetzungen -----------------------------------------------------
Schritt "Voraussetzungen"

$py = (Get-Command python -EA SilentlyContinue).Source
if (-not $py) { Fehler "Python nicht gefunden. https://python.org installieren."; exit 1 }
$pyver = (& python --version 2>&1)
Gut "$pyver"

if (-not (Get-Command git -EA SilentlyContinue)) {
  Fehler "git nicht gefunden. https://git-scm.com installieren."; exit 1
}
Gut "git vorhanden"

# --- 2. Virtuelle Soundkarte ------------------------------------------------
Schritt "Virtuelle Soundkarte"

$cable = Get-CimInstance Win32_SoundDevice |
         Where-Object { $_.Name -like "*VB-Audio*" -or $_.Name -like "*CABLE*" }
if ($cable) {
  Gut ("gefunden: " + ($cable.Name -join ", "))
} else {
  Fehler "VB-CABLE ist nicht installiert."
  Write-Host "     Herunterladen: https://vb-audio.com/Cable/"
  Write-Host "     Entpacken, VBCABLE_Setup_x64.exe ALS ADMINISTRATOR starten,"
  Write-Host "     danach Windows neu starten. Dann dieses Skript erneut aufrufen."
  exit 1
}

# Sind die Endpunkte auch aktiv? state=1 heisst betriebsbereit.
$aktiv = @()
foreach ($art in @("Capture","Render")) {
  $root = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\$art"
  Get-ChildItem $root -EA SilentlyContinue | ForEach-Object {
    $p = Get-ItemProperty (Join-Path $_.PSPath "Properties") -EA SilentlyContinue
    $n = $p."{a45c254e-df1c-4efd-8020-67d146a850e0},2"
    $s = (Get-ItemProperty $_.PSPath -EA SilentlyContinue).DeviceState
    if ($n -like "*CABLE*" -and $s -eq 1) { $aktiv += "$art/$n" }
  }
}
if ($aktiv.Count -ge 2) { Gut ("aktiv: " + ($aktiv -join ", ")) }
else { Warn "CABLE-Endpunkte nicht beide aktiv - evtl. Neustart noetig." }

# --- 3. Repo ----------------------------------------------------------------
Schritt "STT-Server holen"

if (Test-Path (Join-Path $Repo ".git")) {
  Gut "Repo vorhanden: $Repo"
  Push-Location $Repo
  git fetch --quiet 2>$null
  git checkout --quiet $Branch 2>$null
  Pop-Location
} else {
  Warn "Repo fehlt unter $Repo"
  Write-Host "     Das STT-Server-Repo ist privat. Entweder:"
  Write-Host ('       git clone -b ' + $Branch + ' REPO-URL ' + $Repo)
  Write-Host "     oder vom Server kopieren lassen (scp)."
  Write-Host "     Danach dieses Skript erneut aufrufen."
  exit 1
}

# --- 4. Virtuelle Umgebung --------------------------------------------------
Schritt "Python-Umgebung"

$venv   = Join-Path $Repo ".venv"
$vpy    = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) {
  & python -m venv $venv
  Gut "angelegt: $venv"
} else { Gut "vorhanden: $venv" }

if (-not $SkipInstall) {
  & $vpy -m pip install --quiet --upgrade pip

  # NUR was der Server bei Infomaniak-Betrieb wirklich braucht.
  # Whisper laeuft REMOTE bei Infomaniak - openai-whisper und torch werden
  # hier NICHT gebraucht (das waeren ~2,5 GB und ein CUDA-Build ohne GPU).
  # Die volle requirements_without_cuda.txt zieht beides mit und scheitert
  # ausserdem an setuptools>=81 (pkg_resources entfernt). Erlebt 2026-09-07.
  $pakete = @("flask","python-dotenv","numpy","requests",
              "sounddevice","samplerate","vosk")
  # Das ElevenLabs-Backend braucht sein eigenes Paket - fehlt es, startet der
  # Server zwar, aber die Erkennung faellt still aus ("No module named
  # 'elevenlabs'" nur im Protokoll). Erlebt 2026-09-07.
  if ($Backend -eq "elevenlabs-scribe") { $pakete += "elevenlabs" }
  Write-Host "  ... installiere $($pakete.Count) Pakete"
  & $vpy -m pip install --quiet @pakete
  if ($LASTEXITCODE -ne 0) {
    Fehler "Paketinstallation fehlgeschlagen (Code $LASTEXITCODE)."
    Write-Host "     Erneut versuchen:  $vpy -m pip install $($pakete -join ' ')"
    exit 1
  }
  Gut "Pakete installiert"

  # Beweis statt Behauptung: laesst sich der Server ueberhaupt laden?
  Push-Location $Repo
  & $vpy -c "import fundusapps.stt_server" 2>&1 | Out-Null
  $ladbar = ($LASTEXITCODE -eq 0)
  Pop-Location
  if ($ladbar) { Gut "STT-Server ist ladbar" }
  else { Fehler "STT-Server laesst sich nicht laden - Installation unvollstaendig."; exit 1 }
}

# --- 5. Zugangsschluessel ---------------------------------------------------
Schritt "Zugangsschluessel (Infomaniak)"

$vorhanden = [Environment]::GetEnvironmentVariable("INFOMANIAK_API_KEY", "User")
if ($vorhanden) {
  Gut "INFOMANIAK_API_KEY ist gesetzt (Laenge $($vorhanden.Length))"
} elseif ($ApiKey) {
  [Environment]::SetEnvironmentVariable("INFOMANIAK_API_KEY", $ApiKey, "User")
  $env:INFOMANIAK_API_KEY = $ApiKey
  Gut "gesetzt (dauerhaft fuer diesen Benutzer)"
} else {
  Warn "Kein Schluessel hinterlegt."
  Write-Host "     Setzen mit:"
  Write-Host '       [Environment]::SetEnvironmentVariable("INFOMANIAK_API_KEY","<schluessel>","User")'
  Write-Host "     Danach PowerShell neu oeffnen."
}

# --- 5b. Konfigurationsdatei ------------------------------------------------
Schritt "Konfiguration (.env)"

$envdatei = Join-Path $Repo ".env"
if (Test-Path $envdatei) {
  Gut ".env vorhanden"
} else {
  $inhalt = @'
# CoThinker - Konfiguration des STT-Servers. KEINE Geheimnisse hier:
# der Infomaniak-Schluessel steht in der Benutzer-Umgebungsvariable.
STT_HOST="0.0.0.0"
STT_PORT=__PORT__
SST_AUDIO_DEVICES="__GERAET__"
STT_AUDIO_DEVICES_SR=44100
STT_ASIO_CHANNELS=
STT_MIC_GATE=
STT_SETTINGS_FILE=
'@
  $inhalt = $inhalt.Replace("__PORT__", "$Port").Replace("__GERAET__", "CABLE Output")
  Set-Content -Path $envdatei -Value $inhalt -Encoding ASCII
  Gut "angelegt: $envdatei"
}

# --- 6. Firewall ------------------------------------------------------------
Schritt "Firewall"

$regel = Get-NetFirewallRule -DisplayName "CoThinker STT" -EA SilentlyContinue
if ($regel) {
  Gut "Regel existiert"
} elseif ($Firewall) {
  try {
    New-NetFirewallRule -DisplayName "CoThinker STT" -Direction Inbound `
      -Action Allow -Protocol TCP -LocalPort $Port -Profile Private | Out-Null
    Gut "Regel angelegt (TCP $Port, privates Netz)"
  } catch {
    Fehler "Regel fehlgeschlagen - Administratorrechte noetig."
  }
} else {
  Warn "Keine Regel. Mit -Firewall in einer Administrator-PowerShell anlegen:"
  Write-Host "     New-NetFirewallRule -DisplayName 'CoThinker STT' -Direction Inbound ``"
  Write-Host "       -Action Allow -Protocol TCP -LocalPort $Port -Profile Private"
}

# --- 7. Startskript ---------------------------------------------------------
Schritt "Startskript"

$start = Join-Path $Repo "cothinker-stt-starten.ps1"

# Nicht-expandierendes Here-String (@' ... '@): PowerShell fasst nichts an.
# Die drei Platzhalter werden danach ersetzt - das ist robuster als Backticks.
$vorlage = @'
# Startet den STT-Server fuer CoThinker.
# Erzeugt von einrichten-aufnahmerechner.ps1 - nicht von Hand aendern.
param(
  [string]$Geraet  = "CABLE Output",
  [int]   $Port    = __PORT__,
  [string]$Sprache = "de",
  [string]$Backend = "__BACKEND__",
  [switch]$AudioMitschnitt   # schreibt WAVs nach logs/audio zur Kontrolle
)
$ErrorActionPreference = "Stop"
Set-Location "__REPO__"
if (-not $env:INFOMANIAK_API_KEY) {
  $env:INFOMANIAK_API_KEY = [Environment]::GetEnvironmentVariable("INFOMANIAK_API_KEY","User")
}
if (-not $env:INFOMANIAK_API_KEY) {
  Write-Host "FEHLER: INFOMANIAK_API_KEY fehlt." -ForegroundColor Red
  exit 1
}
Write-Host "STT-Server auf Port $Port, Geraet '$Geraet'" -ForegroundColor Cyan
# ACHTUNG Reihenfolge: --audio_devices hat nargs="+" und wuerde den
# Backend-Namen als weiteren Geraetenamen verschlucken (args.py Zeile 65 ff.).
# Deshalb kommt das Aufnahmegeraet aus der .env (SST_AUDIO_DEVICES), nicht
# von der Kommandozeile. Erlebt 2026-09-07.
#
# BACKEND-WAHL ist eine DATENSCHUTZ-Entscheidung, kein technischer Schalter:
#   elevenlabs-scribe   -> ElevenLabs, Vereinigte Staaten
#   infomaniak-whisper  -> Infomaniak, Genf (Schweiz/EU)
# Umschalten per -Backend beim Einrichten oder $Backend hier.
$argumente = @(
  "-m", "fundusapps.stt_server",
  "--host", "0.0.0.0",
  "--port", "$Port",
  "$Backend",
  "--language", "$Sprache"
)
if ($AudioMitschnitt) {
  # --audio_logging gehoert VOR den Backend-Namen (Hauptparser, nicht Subparser).
  $argumente = $argumente[0..3] + @("--audio_logging") + $argumente[4..($argumente.Count-1)]
  Write-Host "  Audio-Mitschnitt AN -> logs/audio" -ForegroundColor DarkGray
}
if ($Backend -eq "elevenlabs-scribe") {
  Write-Host "  ACHTUNG: Spracherkennung laeuft ueber ElevenLabs (USA)." -ForegroundColor Yellow
} else {
  Write-Host "  Spracherkennung: $Backend" -ForegroundColor DarkGray
}
& "__PYTHON__" @argumente
'@

$vorlage = $vorlage.Replace("__PORT__",   "$Port")
$vorlage = $vorlage.Replace("__REPO__",   $Repo)
$vorlage = $vorlage.Replace("__PYTHON__", $vpy)
$vorlage = $vorlage.Replace("__BACKEND__", $Backend)
Set-Content -Path $start -Value $vorlage -Encoding UTF8
Gut "geschrieben: $start"

# --- 8. Zusammenfassung -----------------------------------------------------
Schritt "Fertig"

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -like "100.*" } |
       Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "  Starten:  powershell -ExecutionPolicy Bypass -File `"$start`""
if ($ip) { Write-Host "  Erreichbar unter:  http://${ip}:$Port" }
Write-Host ""
Write-Host "  In der Konferenz-App als Wiedergabe 'CABLE Input' waehlen -"
Write-Host "  dann hoert der Server mit, was die anderen sagen."
Write-Host ""
