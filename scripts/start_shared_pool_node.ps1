[CmdletBinding()]
param(
    [string]$StateDir,
    [string]$ConfigPath,
    [string]$Url,
    [string[]]$Profiles = @('fake-image'),
    [ValidateRange(0.2, 60)]
    [double]$Interval = 2
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'AlchemyRenderBridge\SharedPool'
if (-not $StateDir) { $StateDir = Join-Path $runtimeRoot 'agent-state' }
if (-not $ConfigPath) { $ConfigPath = Join-Path $runtimeRoot 'agent.json' }
$python = Join-Path $runtimeRoot 'venv\Scripts\python.exe'
$pushedLocation = $false

function Resolve-Python312 {
    $candidates = @()
    $command = Get-Command python3.12.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notmatch '\\Microsoft\\WindowsApps\\python3\.12\.exe$') {
        $candidates += $command.Source
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notmatch '\\Microsoft\\WindowsApps\\python\.exe$') {
        $candidates += $command.Source
    }
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    $candidates += (Join-Path $env:USERPROFILE '.local\bin\python3.12.exe')
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $version = & $candidate -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
        if ($LASTEXITCODE -eq 0 -and $version.Trim() -eq '3.12') { return $candidate }
    }
    throw 'Python 3.12 is required. Install Python 3.12, then reopen this launcher.'
}

function Test-SharedPoolRuntime {
    & $python -c 'import importlib.util, pathlib, sys; spec=importlib.util.find_spec("render_bridge"); root=(pathlib.Path.cwd()/"src").resolve(); package=pathlib.Path(spec.origin).resolve() if spec and spec.origin else None; required=("fastapi", "httpx", "uvicorn", "PIL", "pydantic", "anyio"); sys.exit(0 if package and package.is_relative_to(root) and all(importlib.util.find_spec(name) for name in required) else 1)'
    return $LASTEXITCODE -eq 0
}

try {
    Push-Location $repoRoot
    $pushedLocation = $true
    $statePath = [IO.Path]::GetFullPath($StateDir)
    $ConfigPath = [IO.Path]::GetFullPath($ConfigPath)
    $stateFile = Join-Path $statePath 'shared-node.json'
    $needsEnrollment = -not (Test-Path -LiteralPath $stateFile -PathType Leaf)
    if ($needsEnrollment -and -not $Url) {
        throw "This machine is not enrolled at '$statePath'. Start once with -Url '<Bridge URL>' -Profiles fake-image, using the command shown in the Bridge console."
    }

    $runtimeRoot = [IO.Path]::GetFullPath($runtimeRoot)
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        $configParent = Split-Path -Parent $ConfigPath
        if ($configParent) { New-Item -ItemType Directory -Force -Path $configParent | Out-Null }
        Copy-Item -LiteralPath (Join-Path $repoRoot 'config\agent.example.json') -Destination $ConfigPath
        Write-Host "Created local Agent configuration: $ConfigPath" -ForegroundColor DarkCyan
    }

    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $bootstrapPython = Resolve-Python312
        & $bootstrapPython -m venv (Join-Path $runtimeRoot 'venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the isolated Python environment.' }
    }

    $pythonVersion = & $python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
    if ($LASTEXITCODE -ne 0 -or $pythonVersion.Trim() -ne '3.12') {
        throw 'The local shared-pool environment must use Python 3.12. Repair or recreate that environment, then run this launcher again.'
    }
    & $python -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("pip") else 1)'
    if ($LASTEXITCODE -ne 0) {
        & $python -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw 'Could not restore pip in the local Python environment.' }
    }
    if (-not (Test-SharedPoolRuntime)) {
        & $python -m pip install --disable-pip-version-check --no-input -e '.[runtime]'
        if ($LASTEXITCODE -ne 0) { throw 'Could not install the Bridge runtime dependencies.' }
        if (-not (Test-SharedPoolRuntime)) { throw 'The local Python environment is still missing Bridge runtime dependencies.' }
    }

    if ($needsEnrollment) {
        $profileList = ($Profiles | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ','
        if (-not $profileList) { throw 'Choose at least one profile for the node invitation.' }
        Write-Host 'Starting one-time shared-pool enrollment. Review the sharing notice and enter the invite code when prompted.' -ForegroundColor DarkCyan
        & $python -u -m render_bridge.agent.pool_node enroll --url $Url --state-dir $statePath --profiles $profileList
        if ($LASTEXITCODE -ne 0) { throw 'Shared-pool enrollment did not complete.' }
    }

    $runningAgent = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.Name -ieq 'python.exe' -or $_.Name -ieq 'pythonw.exe') -and
        $_.CommandLine -and
        $_.CommandLine.Contains('render_bridge.agent.pool_node run') -and
        $_.CommandLine.IndexOf($statePath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    } | Select-Object -First 1
    if ($runningAgent) { throw 'A shared-pool Agent already owns this local state directory.' }

    Write-Host 'Starting the shared-pool Node Agent. Keep this window open; press Ctrl+C to stop.' -ForegroundColor Green
    Write-Host "State: $statePath | Config: $ConfigPath | Poll interval: $Interval seconds"
    & $python -u -m render_bridge.agent.pool_node run --state-dir $statePath --config $ConfigPath --interval $Interval
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) { throw "Shared-pool Agent exited with code $exitCode." }
} catch {
    Write-Host "Shared-pool Node Agent error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ($pushedLocation) { Pop-Location }
}
