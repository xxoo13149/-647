$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$appUrl = "http://127.0.0.1:5000/"
$probeUrl = "${appUrl}api/defaults"

function Test-AppReady {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $probeUrl -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing Python runtime: $python"
}

if (-not (Test-AppReady)) {
    Start-Process -FilePath $python -ArgumentList "web_app.py" -WorkingDirectory $root -WindowStyle Hidden

    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 750
        if (Test-AppReady) {
            $ready = $true
            break
        }
    }

    if (-not $ready) {
        throw "Web app did not become ready at $appUrl"
    }
}

Start-Process $appUrl
