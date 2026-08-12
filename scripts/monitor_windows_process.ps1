param(
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [int]$IntervalSeconds = 5
)

$ErrorActionPreference = 'SilentlyContinue'
$parent = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $parent | Out-Null

function Get-CounterValue([string]$Path) {
    try {
        $sample = Get-Counter -Counter $Path -ErrorAction Stop
        if ($sample.CounterSamples.Count -gt 0) {
            return [double]$sample.CounterSamples[0].CookedValue
        }
    } catch {}
    return $null
}

function Get-GpuTelemetry {
    $result = [ordered]@{
        query = 'Windows GPU Engine performance counter'
        utilizationPercent = $null
        available = $false
        limitation = $null
    }
    try {
        $samples = (Get-Counter -Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction Stop).CounterSamples
        if ($samples.Count -gt 0) {
            $result.utilizationPercent = [math]::Round(($samples | Measure-Object -Property CookedValue -Sum).Sum, 2)
            $result.available = $true
        }
    } catch {
        $result.limitation = 'GPU Engine counter unavailable on this Windows installation.'
    }
    return $result
}

while ($true) {
    $process = Get-Process -Id $ProcessId
    if (-not $process) { break }
    $os = Get-CimInstance Win32_OperatingSystem
    $computer = Get-CimInstance Win32_ComputerSystem
    $record = [ordered]@{
        timestamp = [DateTime]::UtcNow.ToString('o')
        processId = $ProcessId
        processName = $process.ProcessName
        processCpuSeconds = $process.CPU
        processWorkingSetBytes = $process.WorkingSet64
        processPrivateBytes = $process.PrivateMemorySize64
        processPagedBytes = $process.PagedMemorySize64
        system = [ordered]@{
            totalPhysicalMemoryBytes = $computer.TotalPhysicalMemory
            availablePhysicalMemoryBytes = [int64]$os.FreePhysicalMemory * 1024
            cpuTotalPercent = Get-CounterValue '\Processor(_Total)\% Processor Time'
            pagesPerSecond = Get-CounterValue '\Memory\Pages/sec'
            diskBytesPerSecond = Get-CounterValue '\PhysicalDisk(_Total)\Disk Bytes/sec'
        }
        gpu = Get-GpuTelemetry
    }
    ($record | ConvertTo-Json -Compress -Depth 8) | Add-Content -LiteralPath $OutputPath -Encoding UTF8
    Start-Sleep -Seconds ([math]::Max(1, $IntervalSeconds))
}
