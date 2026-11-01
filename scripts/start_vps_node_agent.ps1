$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'AlchemyRenderBridge\VPS'
$credentialFile = Join-Path $runtimeRoot 'node-token.dpapi'
$agentConfig = Join-Path $runtimeRoot 'agent.json'
$stateDir = Join-Path $runtimeRoot 'agent-state'
$bridgeUrl = 'https://bridge.aiself.vip'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$pushedLocation = $false

function Resolve-Python312 {
    $candidates = @()
    $command = Get-Command python3.12.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    $candidates += (Join-Path $env:USERPROFILE '.local\bin\python3.12.exe')
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $version = & $candidate -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
        if ($LASTEXITCODE -eq 0 -and $version.Trim() -eq '3.12') { return $candidate }
    }
    throw 'Python 3.12 is required. Install Python 3.12, then reopen this launcher.'
}

try {
    Push-Location $repoRoot
    $pushedLocation = $true
    New-Item -ItemType Directory -Force -Path $runtimeRoot, $stateDir | Out-Null

    $runningAgent = Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -ieq 'python.exe' -or $_.Name -ieq 'pythonw.exe') -and
        $_.CommandLine -and
        $_.CommandLine.Contains('render_bridge.agent.hatchet_sidecar') -and
        $_.CommandLine.Contains('AlchemyRenderBridge\VPS\agent-state')
    } | Select-Object -First 1
    if ($runningAgent) { throw 'A VPS Node Agent already owns this local state directory.' }

    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $bootstrapPython = Resolve-Python312
        & $bootstrapPython -m venv (Join-Path $repoRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the isolated Python environment.' }
        & $python -m pip install --disable-pip-version-check --no-input -e '.[runtime]'
        if ($LASTEXITCODE -ne 0) { throw 'Could not install the Bridge runtime dependencies.' }
    }

    if (-not (Test-Path -LiteralPath $agentConfig -PathType Leaf)) {
        Copy-Item -LiteralPath (Join-Path $repoRoot 'config\agent.example.json') -Destination $agentConfig
    }
    if (-not (Test-Path -LiteralPath $credentialFile -PathType Leaf)) {
        throw 'The encrypted VPS Node token is missing. Re-provision this Windows Node identity before starting.'
    }

    Add-Type -AssemblyName System.Security
    $entropy = [Text.Encoding]::UTF8.GetBytes('AlchemyRenderBridge.NodeToken.v1')
    $protected = [IO.File]::ReadAllBytes($credentialFile)
    $plain = [Security.Cryptography.ProtectedData]::Unprotect(
        $protected, $entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    try {
        $record = [Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json
        $nodeToken = [string]$record.node_token
    } finally {
        [Array]::Clear($plain, 0, $plain.Length)
    }
    if (-not $nodeToken) { throw 'The encrypted VPS Node token could not be read.' }

    Add-Type -AssemblyName System.Net.Http
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [Net.Http.HttpClient]::new($handler)
    try {
        $request = [Net.Http.HttpRequestMessage]::new(
            [Net.Http.HttpMethod]::Post, "$bridgeUrl/bridge/node/v1/heartbeat")
        $request.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new(
            'Bearer', $nodeToken)
        $request.Content = [Net.Http.StringContent]::new(
            '{"state":"available","profiles":["fake-image"]}',
            [Text.Encoding]::UTF8, 'application/json')
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "VPS Node authentication failed with HTTP $([int]$response.StatusCode)."
        }
    } finally {
        if ($request) { $request.Dispose() }
        if ($response) { $response.Dispose() }
        $client.Dispose()
        $handler.Dispose()
    }

    Write-Host 'VPS Bridge HTTPS and Node authentication: verified' -ForegroundColor Green
    Write-Host "Endpoint: $bridgeUrl | mode: outbound pull | profile: fake-image"
    Write-Host 'Close this window to stop the local Node Agent.' -ForegroundColor DarkCyan
    $env:BRIDGE_NODE_TOKEN = $nodeToken
    & $python -u -m render_bridge.agent.hatchet_sidecar --url $bridgeUrl pull-worker `
        --state-dir $stateDir --config $agentConfig --interval 2
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) { throw "VPS Node Agent exited with code $exitCode." }
} catch {
    Write-Host "VPS Node Agent error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    Remove-Item Env:BRIDGE_NODE_TOKEN -ErrorAction SilentlyContinue
    $nodeToken = $null
    $record = $null
    if ($pushedLocation) { Pop-Location }
}
