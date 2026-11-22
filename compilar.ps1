# Compila ConfiguradorMeshtasticCanarias.exe (ventana de Windows, un solo fichero) y el ZIP de distribucion.
# Requisitos: Windows 10/11 x64 con Python 3.12 (python.org) e Internet para descargar dependencias.
# Uso:  powershell -ExecutionPolicy Bypass -File compilar.ps1
# (Sin tildes a proposito: PowerShell 5.1 lee los .ps1 sin BOM como ANSI.)
$ErrorActionPreference = 'Stop'
$Nombre = 'ConfiguradorMeshtasticCanarias'
$MeshtasticVersion = '2.7.11'
$PyInstallerVersion = '6.22.3'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dist = Join-Path $Root $Nombre
# Se compila en una ruta corta para no chocar con el limite de 260 caracteres de Windows
$B = Join-Path $env:TEMP 'cm-build'

function Find-Python {
    $c = @()
    $c += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
    $c += Get-ChildItem "$env:ProgramFiles\Python312\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { try { $x = & $py.Source -3.12 -c "import sys;print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0) { $c += $x.Trim() } } catch {} }
    foreach ($e in $c) { if ($e -and (Test-Path $e)) { return $e } }
    throw "No encuentro Python 3.12. Instalalo desde python.org (o: winget install Python.Python.3.12)."
}

$Python = Find-Python
Write-Host "Python: $Python"
New-Item -ItemType Directory -Force $B | Out-Null
$Venv = Join-Path $B 'venv'
if (-not (Test-Path "$Venv\Scripts\python.exe")) { & $Python -m venv $Venv }
$VPy = "$Venv\Scripts\python.exe"
& $VPy -m pip install --disable-pip-version-check -q "meshtastic==$MeshtasticVersion" "pyinstaller==$PyInstallerVersion"
if ($LASTEXITCODE -ne 0) { throw "Fallo instalando dependencias." }

Copy-Item "$Root\fuente\configurar_nodo.py", "$Root\fuente\interfaz.py" $B -Force
if (Test-Path "$B\uf2") { Remove-Item -Recurse -Force "$B\uf2" }
Copy-Item "$Root\fuente\uf2" "$B\uf2" -Recurse

Push-Location $B
try {
    & $VPy -m PyInstaller --noconfirm --clean --onefile --windowed --name $Nombre `
        --hidden-import interfaz --collect-submodules meshtastic --copy-metadata meshtastic `
        --add-data "$B\uf2;uf2" --distpath "$B\dist" --workpath "$B\build" --specpath $B configurar_nodo.py
    if ($LASTEXITCODE -ne 0) { throw "Fallo PyInstaller." }
} finally { Pop-Location }

Copy-Item "$B\dist\$Nombre.exe" $Dist -Force

# ZIP solo con lo necesario (nunca backups): carpeta <Nombre>\ con exe, LEEME y plantilla
$Stage = Join-Path $B "zip\$Nombre"
if (Test-Path (Join-Path $B 'zip')) { Remove-Item -Recurse -Force (Join-Path $B 'zip') }
New-Item -ItemType Directory -Force $Stage | Out-Null
Copy-Item "$Dist\$Nombre.exe", "$Dist\LEEME.txt", "$Dist\plantilla.yaml" $Stage
$Zip = Join-Path $Root "$Nombre.zip"
if (Test-Path $Zip) { Remove-Item $Zip }
Compress-Archive -Path $Stage -DestinationPath $Zip
Write-Host ""
Write-Host "Listo:" -ForegroundColor Green
Write-Host "  $Dist\$Nombre.exe"
Write-Host "  $Zip"
