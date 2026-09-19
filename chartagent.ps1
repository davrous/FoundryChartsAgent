param(
    [ValidateSet("setup", "dev", "web", "playground", "test", "rebuild", "start", "up", "help")]
    [string]$Command = "help",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ExtraArgs
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Agent = Join-Path $PSScriptRoot "src/charts_agent"
$Python = Join-Path $Agent ".venv/Scripts/python.exe"
$AgentPort = if ($env:PORT) { $env:PORT } else { "8188" }
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE)" } }
switch ($Command) {
    "setup" {
        if (-not (Test-Path $Python)) { py -3.13 -m venv "$Agent/.venv"; Check-Exit }
        & $Python -m pip install -r requirements.lock; Check-Exit
        npm --prefix web ci; Check-Exit
        npm --prefix web run build; Check-Exit
    }
    "dev" {
        Set-Location $Agent
        $env:PORT = $AgentPort
        if (-not $env:ARTIFACT_BASE_URL) { $env:ARTIFACT_BASE_URL = "http://localhost:$AgentPort/artifacts" }
        & $Python main.py @ExtraArgs; Check-Exit
    }
    "web" {
        $env:PYTHONPATH = $Agent
        & $Python -m gateway.main @ExtraArgs; Check-Exit
    }
    "playground" {
        Invoke-RestMethod "http://localhost:$AgentPort/health" | Out-Null
        $ServiceUrl = if ($env:PLAYGROUND_SERVICE_URL) { $env:PLAYGROUND_SERVICE_URL } else { "http://localhost:56150/_connector" }
        agentsplayground -e "http://localhost:$AgentPort/api/messages" --service-url $ServiceUrl @ExtraArgs
        Check-Exit
    }
    "test" { & $Python -m pytest @ExtraArgs; Check-Exit }
    "rebuild" { docker build --platform linux/amd64 -t foundry-charts-agent @ExtraArgs $Agent; Check-Exit }
    "start" {
        docker run --rm -it -p "127.0.0.1:${AgentPort}:8088" --env-file "$Agent/.env" `
            -e "ARTIFACT_BASE_URL=http://localhost:$AgentPort/artifacts" @ExtraArgs foundry-charts-agent
        Check-Exit
    }
    "up" {
        & "$PSScriptRoot/chartagent.ps1" rebuild; Check-Exit
        & "$PSScriptRoot/chartagent.ps1" start @ExtraArgs; Check-Exit
    }
    default { Write-Host "chartagent.ps1 setup | dev | web | playground | test | rebuild | start | up" }
}
