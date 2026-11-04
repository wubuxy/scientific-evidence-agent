param(
    [string]$Python = 'python',
    [ValidateRange(1024,65535)][int]$Port = 8001,
    [switch]$Restart
)
$ErrorActionPreference = 'Stop'
$taskBackendDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$taskRuntimeDir = Join-Path $taskBackendDir '.research-data'
$taskPidPath = Join-Path $taskRuntimeDir 'server.pid'
$taskLogDir = Join-Path $taskRuntimeDir 'logs'
New-Item -ItemType Directory -Force -Path $taskLogDir | Out-Null

if (Test-Path -LiteralPath $taskPidPath) {
    $taskSavedId = [int](Get-Content -LiteralPath $taskPidPath -Raw).Trim()
    $taskExisting = Get-CimInstance Win32_Process -Filter "ProcessId=$taskSavedId" -ErrorAction SilentlyContinue
    if ($taskExisting) {
        if ($taskExisting.CommandLine -notmatch 'uvicorn\s+research_main:app' -or $taskExisting.CommandLine -notmatch "--port\s+$Port\b") {
            throw 'Saved PID does not identify this research service; refusing to stop it.'
        }
        if (-not $Restart) {
            $taskHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
            if ($taskHealth.scope -ne 'scientific_research') { throw 'Unexpected service on the configured port.' }
            Write-Output "Research backend already running: http://127.0.0.1:$Port/docs (PID $taskSavedId)"
            return
        }
        Stop-Process -Id $taskSavedId
        Wait-Process -Id $taskSavedId -Timeout 10 -ErrorAction SilentlyContinue
    }
}

$taskEnvScript = @'
from pathlib import Path
import secrets, sys
p=Path(sys.argv[1])
if not p.exists():
    p.write_text('JWT_SECRET_KEY='+secrets.token_urlsafe(48)+'\nRESEARCH_DATA_DIR=.research-data\nRESEARCH_RAG_ENABLED=false\nRESEARCH_RAG_COLLECTION=research_sources_v1\n',encoding='utf-8')
'@
& $Python -c $taskEnvScript (Join-Path $taskBackendDir '.env.research')
if ($LASTEXITCODE -ne 0) { throw 'Python environment/configuration preparation failed.' }
$taskProcess = Start-Process -FilePath $Python -ArgumentList @('-m','uvicorn','research_main:app','--app-dir','app','--env-file','.env.research','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $taskBackendDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLogDir 'server-stdout.log') -RedirectStandardError (Join-Path $taskLogDir 'server-stderr.log') -PassThru
$taskProcess.Id | Set-Content -Encoding ASCII -LiteralPath $taskPidPath
for ($taskAttempt=0; $taskAttempt -lt 40; $taskAttempt++) {
    if ($taskProcess.HasExited) { throw "Research backend exited; see $taskLogDir/server-stderr.log" }
    try {
        $taskHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 1
        if ($taskHealth.scope -eq 'scientific_research') {
            Write-Output "Research backend ready: http://127.0.0.1:$Port/docs (PID $($taskProcess.Id))"
            return
        }
    } catch { }
    Start-Sleep -Milliseconds 250
}
throw "Research backend health check timed out; see $taskLogDir/server-stderr.log"
