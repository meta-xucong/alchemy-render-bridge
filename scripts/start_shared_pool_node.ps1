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
    $bootstrapFile = Join-Path $PSScriptRoot 'node-enrollment.json'
    $bootstrap = $null
    $selectedProfiles = @()
    if ($needsEnrollment -and (Test-Path -LiteralPath $bootstrapFile -PathType Leaf)) {
        try {
            $bootstrap = Get-Content -LiteralPath $bootstrapFile -Raw -Encoding utf8 | ConvertFrom-Json
        } catch {
            throw 'The one-click node enrollment package is invalid JSON. Download a fresh package from the Bridge operator console.'
        }
        if ($bootstrap.schema_version -ne 1 -or $bootstrap.accept_shared_data -ne $true -or
            -not $bootstrap.url -or -not ($bootstrap.profiles -is [System.Array]) -or $bootstrap.profiles.Count -lt 1) {
            throw 'The one-click node enrollment package is incomplete. Download a fresh package from the Bridge operator console.'
        }
        $selectedProfiles = @($bootstrap.profiles | ForEach-Object { [string]$_ })
    } elseif ($needsEnrollment) {
        $selectedProfiles = @($Profiles | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if (-not $Url) {
            throw "This machine is not enrolled at '$statePath'. Download node-enrollment.json from the Bridge operator console into '$PSScriptRoot', next to this launcher, then double-click again."
        }
    } else {
        try {
            $nodeState = Get-Content -LiteralPath $stateFile -Raw -Encoding utf8 | ConvertFrom-Json
            $selectedProfiles = @($nodeState.profiles | ForEach-Object { [string]$_ })
        } catch {
            throw 'The saved node registration is invalid. Create a fresh one-use node package and preserve this state directory for recovery.'
        }
    }
    if (-not $selectedProfiles.Count) {
        throw 'No node profiles were selected. Create a node invitation with at least one profile.'
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

    $profileList = $selectedProfiles -join ','
    & $python -m render_bridge.agent.pool_node preflight --state-dir $statePath --config $ConfigPath --profiles $profileList
    if ($LASTEXITCODE -ne 0) { throw 'Local node checks failed; resolve the message above before joining the pool.' }

    if ($needsEnrollment) {
        if ($bootstrap) {
            Write-Host 'Registering this node with its one-use enrollment package.' -ForegroundColor DarkCyan
            & $python -u -m render_bridge.agent.pool_node enroll --state-dir $statePath --bootstrap-file $bootstrapFile
        } else {
            Write-Host 'Starting interactive node enrollment. Review the sharing notice and enter the one-use invitation code.' -ForegroundColor DarkCyan
            & $python -u -m render_bridge.agent.pool_node enroll --url $Url --state-dir $statePath --profiles $profileList
        }
        if ($LASTEXITCODE -ne 0) { throw 'Shared-pool enrollment did not complete.' }
    }

    & $python -m render_bridge.agent.pool_node verify --state-dir $statePath
    if ($LASTEXITCODE -ne 0) { throw 'Node registration was saved, but Bridge login could not be verified. Keep this state directory and retry after checking the connection.' }

    $runningAgent = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.Name -ieq 'python.exe' -or $_.Name -ieq 'pythonw.exe') -and
        $_.CommandLine -and
        $_.CommandLine.Contains('render_bridge.agent.pool_node run') -and
        $_.CommandLine.IndexOf($statePath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    } | Select-Object -First 1
    if ($runningAgent) { throw 'A shared-pool Agent already owns this local state directory.' }

    Write-Host 'Starting the shared-pool Node Agent. Keep this window open; press Ctrl+C to stop.' -ForegroundColor Green
    Write-Host "State: $statePath | Config: $ConfigPath | Poll interval: $Interval seconds"
    Write-Host 'Local status: http://127.0.0.1:8766/' -ForegroundColor DarkCyan
    & $python -u -m render_bridge.agent.pool_node run --state-dir $statePath --config $ConfigPath --interval $Interval
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) { throw "Shared-pool Agent exited with code $exitCode." }
} catch {
    Write-Host "Shared-pool Node Agent error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ($pushedLocation) { Pop-Location }
}
