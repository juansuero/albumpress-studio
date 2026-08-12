param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Clip
)

$ErrorActionPreference = 'Stop'
$rootPath = [IO.Path]::GetFullPath($Root)
$clipPath = [IO.Path]::GetFullPath($Clip)
$repoPath = Split-Path -Parent $PSScriptRoot
$pythonPath = (Resolve-Path (Join-Path $repoPath '.venv/Scripts/python.exe')).Path
$modelDir = Join-Path $rootPath 'model-cache'
$outputDir = Join-Path $rootPath 'output'
$telemetryDir = Join-Path $rootPath 'telemetry'
$resultPath = Join-Path $rootPath 'worker-result.json'
$workerStdout = Join-Path $rootPath 'worker.stdout.log'
$workerStderr = Join-Path $rootPath 'worker.stderr.log'
$telemetryPath = Join-Path $telemetryDir 'system-process.jsonl'
$runMetadataPath = Join-Path $rootPath 'run-metadata.json'
$monitorScript = Join-Path $PSScriptRoot 'monitor_windows_process.ps1'

New-Item -ItemType Directory -Force -Path $modelDir,$outputDir,$telemetryDir | Out-Null
$startedAt = [DateTime]::UtcNow
$arguments = @(
    (Join-Path $PSScriptRoot 'benchmark_uvr_mdx_cpu.py'),
    '--clip', $clipPath,
    '--model-dir', $modelDir,
    '--output-dir', $outputDir,
    '--result', $resultPath
)

$worker = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $repoPath -RedirectStandardOutput $workerStdout -RedirectStandardError $workerStderr -PassThru
$monitor = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$monitorScript,'-ProcessId',$worker.Id,'-OutputPath',$telemetryPath,'-IntervalSeconds','5') -WindowStyle Hidden -PassThru
$timedOut = $false
try {
    Wait-Process -Id $worker.Id -Timeout 900
} catch {
    $timedOut = $true
}

if (Get-Process -Id $worker.Id -ErrorAction SilentlyContinue) {
    Stop-Process -Id $worker.Id -Force
}
try { Wait-Process -Id $monitor.Id -Timeout 30 } catch {}

$finishedAt = [DateTime]::UtcNow
$workerExitCode = $null
if (Test-Path -LiteralPath $resultPath) {
    try { $workerExitCode = (Get-Content -Raw -LiteralPath $resultPath | ConvertFrom-Json).status } catch {}
}
$metadata = [ordered]@{
    benchmark = 'UVR-MDX-NET-Inst_HQ_5.onnx'
    backendRequested = @('CPUExecutionProvider')
    inputClip = $clipPath
    outputRoot = $rootPath
    startedAt = $startedAt.ToString('o')
    finishedAt = $finishedAt.ToString('o')
    elapsedSeconds = [math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
    hardLimitSeconds = 900
    timedOut = $timedOut
    workerStatus = $workerExitCode
    dockerOrWslChanged = $false
    outputsQuarantined = $true
    productImportAllowed = $false
    selectionAllowed = $false
    exportAllowed = $false
    telemetry = $telemetryPath
    workerResult = $resultPath
}
$metadata | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $runMetadataPath -Encoding UTF8
$metadata | ConvertTo-Json -Depth 8
