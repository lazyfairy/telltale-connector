# Telltale Windows receiver setup — turns a Windows PC + RTL-SDR dongle into a shore AIS
# station feeding Telltale, auto-starting at logon. Wraps the (unmodified, GPL-3.0)
# AIS-catcher; we don't rebuild it — we just download it, bake in your key, and set it running.
#
# Run it by pasting ONE line into PowerShell (the /contribute page gives you the line with your
# key already in it):
#
#   $Key="YOUR_KEY"; $Name="LBYC clubhouse"; irm https://telltaleracing.com/setup-windows.ps1 | iex
#
# (If you don't set $Key first it'll just ask you for it.)

function Install-TelltaleFeed {
  param([string]$Key, [string]$Name, [string]$Server)
  # When run as  irm .../setup-windows.ps1 | iex , any $Key/$Name/$Server the user set at the
  # prompt live in the global scope — pick them up from there.
  if (-not $Key)    { $Key    = $global:Key }
  if (-not $Name)   { $Name   = $global:Name }
  if (-not $Server) { $Server = $global:Server }
  if (-not $Server) { $Server = "https://telltaleracing.com" }
  $ErrorActionPreference = "Stop"
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

  Write-Host "`n=== Telltale receiver setup ===" -ForegroundColor Cyan
  if (-not $Key)  { $Key  = Read-Host "Paste your station key (from telltaleracing.com/contribute)" }
  if (-not $Key)  { Write-Host "No key given — stopping." -ForegroundColor Red; return }
  if (-not $Name) { $Name = Read-Host "Name this station (e.g. 'LBYC clubhouse')" }
  if (-not $Name) { $Name = "$env:COMPUTERNAME receiver" }
  $ingest = "$Server/api/ais-ingest?key=$Key"

  # 1) Fail fast on a bad key — otherwise you'd get a receiver that looks healthy but 403s forever.
  Write-Host "-- checking your key --"
  try {
    $resp = Invoke-WebRequest -Uri $ingest -Method Post -Body '{"vessels":[]}' `
              -ContentType 'application/json' -UseBasicParsing -TimeoutSec 20
    if ($resp.StatusCode -eq 200) { Write-Host "   key OK" -ForegroundColor Green }
  } catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 403) { Write-Host "   *** That key was rejected (403). Check it at $Server/contribute ***" -ForegroundColor Red; return }
    Write-Host "   (couldn't reach $Server right now — continuing; it'll retry once online)" -ForegroundColor Yellow
  }

  # 2) Download the latest AIS-catcher Windows build (unmodified, straight from the project).
  $dir = Join-Path $env:LOCALAPPDATA "Telltale\AIS-catcher"
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $zip = Join-Path $env:TEMP "AIS-catcher.x64.zip"
  $ok = $false
  Write-Host "-- downloading AIS-catcher --"
  # Prefer our own mirror — no dependency on GitHub, so it still works on networks that block it.
  # (It's the unmodified GPL-3.0 binary; source + licence at $Server/dl/AIS-catcher-SOURCE.txt.)
  try {
    Invoke-WebRequest -Uri "$Server/dl/AIS-catcher.x64.zip" -OutFile $zip -UseBasicParsing -TimeoutSec 300
    if ((Get-Item $zip).Length -gt 500000) { $ok = $true; Write-Host "   got it from $Server" -ForegroundColor Green }
  } catch { Write-Host "   (our mirror unavailable — trying GitHub)" -ForegroundColor Yellow }
  # Fallback: the official GitHub release (latest plain x64 build; skip 32-bit / SDRplay / Airspy variants).
  if (-not $ok) {
    try {
      $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/jvde-github/AIS-catcher/releases/latest" `
               -Headers @{ "User-Agent" = "telltale-setup" } -TimeoutSec 30
      $zips = @($rel.assets | Where-Object { $_.name -match '\.zip$' })
      $asset = $zips | Where-Object { $_.name -match '(?i)x64' -and $_.name -notmatch '(?i)sdrplay|airspy|x86' } | Select-Object -First 1
      if (-not $asset) { $asset = $zips | Where-Object { $_.name -notmatch '(?i)sdrplay|airspy|x86' } | Select-Object -First 1 }
      if ($asset) {
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing -TimeoutSec 300
        if ($asset.size -and (Get-Item $zip).Length -ne $asset.size) { throw "incomplete download" }
        $ok = $true; Write-Host "   got it from GitHub" -ForegroundColor Green
      }
    } catch { Write-Host "   (GitHub download failed: $($_.Exception.Message))" -ForegroundColor Yellow }
  }
  if (-not $ok) {
    Write-Host "   Couldn't download AIS-catcher automatically. Grab it yourself from" -ForegroundColor Yellow
    Write-Host "   $Server/dl/AIS-catcher.x64.zip  and unzip into  $dir" -ForegroundColor Yellow
  } else {
    Write-Host "   extracting ..."
    Expand-Archive -Path $zip -DestinationPath $dir -Force   # a corrupt zip fails here rather than running silently
    Remove-Item $zip -ErrorAction SilentlyContinue
  }

  $exe = Get-ChildItem -Path $dir -Recurse -Filter "AIS-catcher.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $exe) {
    Write-Host "`nAIS-catcher.exe isn't in $dir yet — once you've unzipped it there, re-run this and it'll finish." -ForegroundColor Yellow
    return
  }

  # 3) A CA bundle so AIS-catcher's bundled OpenSSL can verify our HTTPS cert. Without it EVERY push
  #    fails "certificate verify failed" and nothing reaches Telltale (the dongle looks perfectly fine
  #    while the data silently vanishes). Built from this PC's trusted roots (incl. Let's Encrypt's
  #    ISRG Root X1) — no download needed. OpenSSL reads it via the SSL_CERT_FILE env var below.
  $caPath = Join-Path $dir "cacert.pem"
  try {
    $pem = foreach ($c in Get-ChildItem Cert:\LocalMachine\Root) {
      "-----BEGIN CERTIFICATE-----`r`n" + [Convert]::ToBase64String($c.RawData,'InsertLineBreaks') + "`r`n-----END CERTIFICATE-----"
    }
    [System.IO.File]::WriteAllText($caPath, ($pem -join "`r`n"), (New-Object System.Text.ASCIIEncoding))
    Write-Host "-- CA bundle written (HTTPS push will verify)"
  } catch { Write-Host "   (couldn't build cacert.pem: $($_.Exception.Message))" -ForegroundColor Yellow }

  # 4) A launcher with your key baked in. -X off = do NOT share to the public aiscatcher.org hub
  #    (it's ON by default in current builds) — this is the club's own feed to Telltale.
  $launcher = Join-Path $dir "Start-Telltale-Feed.cmd"
  @"
@echo off
title Telltale AIS Feed - $Name
set SSL_CERT_FILE=%~dp0cacert.pem
echo Feeding Telltale as "$Name" - leave this window open. Ctrl+C to stop.
"$($exe.FullName)" -X off -H $ingest INTERVAL 10
"@ | Set-Content -Path $launcher -Encoding ASCII
  Write-Host "-- launcher written: $launcher"

  # Auto-start via a Startup-folder shortcut — needs NO admin rights (a scheduled task would),
  # so a normal club-volunteer login can set it. Runs minimized at every logon.
  try {
    $startup = [Environment]::GetFolderPath('Startup')
    $lnk = Join-Path $startup "Telltale AIS Feed.lnk"
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = $launcher
    $s.WorkingDirectory = $dir
    $s.WindowStyle = 7            # minimized
    $s.Description = "Telltale AIS Feed - $Name"
    $s.Save()
    Write-Host "-- auto-start at logon: enabled (shortcut in your Startup folder)" -ForegroundColor Green
  } catch { Write-Host "   (couldn't set auto-start: $($_.Exception.Message))" -ForegroundColor Yellow }

  # 4) The one manual step we can't safely automate: the USB driver for the dongle.
  Write-Host "`n=== ONE thing left to do (once) ===" -ForegroundColor Cyan
  Write-Host "The RTL-SDR dongle needs the WinUSB driver, set with a small free tool called Zadig:"
  Write-Host "  1. Download Zadig:  https://zadig.akeo.ie/"
  Write-Host "  2. Plug the dongle in, run Zadig, tick Options -> List All Devices"
  Write-Host "  3. Pick 'Bulk-In, Interface (Interface 0)' (or 'RTL2832U'), choose WinUSB, click Replace/Install Driver"
  Write-Host "Then double-click:  $launcher" -ForegroundColor Green
  Write-Host "Your boats appear on $Server/ais within seconds.`n"
  Write-Host "Heads-up: Windows SmartScreen or your antivirus may warn about AIS-catcher.exe — it's an" -ForegroundColor DarkYellow
  Write-Host "unsigned open-source tool, not a virus. Choose 'More info' -> 'Run anyway', or allow it.`n" -ForegroundColor DarkYellow
}

Install-TelltaleFeed
