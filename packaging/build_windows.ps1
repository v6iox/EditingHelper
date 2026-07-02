# Build EditSync for Windows (dist\EditSync\EditSync.exe + EditSync-windows.zip).
#
# Usage (from the repository root, in PowerShell):
#   .\packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "==> Installing build dependencies"
python -m pip install --upgrade pip | Out-Null
python -m pip install ".[gui]" pyinstaller | Out-Null

Write-Host "==> Fetching static ffmpeg/ffprobe to bundle"
New-Item -ItemType Directory -Force -Path packaging\bin | Out-Null
if (-not (Test-Path packaging\bin\ffmpeg.exe)) {
    $url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    Invoke-WebRequest -Uri $url -OutFile $env:TEMP\ffmpeg.zip
    Expand-Archive -Path $env:TEMP\ffmpeg.zip -DestinationPath $env:TEMP\ffmpeg -Force
    $bin = Get-ChildItem -Path $env:TEMP\ffmpeg -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    Copy-Item $bin.FullName packaging\bin\ffmpeg.exe
    Copy-Item (Join-Path $bin.DirectoryName "ffprobe.exe") packaging\bin\ffprobe.exe
}

Write-Host "==> Generating app icon (.ico)"
python -m pip install pillow | Out-Null
python -c "from PIL import Image; Image.open('src/editsync/gui/assets/icon.png').save('packaging/icon.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"

Write-Host "==> Building EditSync"
pyinstaller --noconfirm packaging\editsync.spec

Write-Host "==> Zipping"
Compress-Archive -Path dist\EditSync -DestinationPath dist\EditSync-windows.zip -Force
Write-Host "Done: dist\EditSync-windows.zip"
