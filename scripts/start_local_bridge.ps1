$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Net.Http
Add-Type -AssemblyName System.Security

$processLifetimeJobSource = @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace AlchemyRenderBridge {
    [StructLayout(LayoutKind.Sequential)]
    public struct BasicLimitInformation {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct IoCounters {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct ExtendedLimitInformation {
        public BasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    public static class ProcessLifetimeJob {
        private const uint JobObjectLimitKillOnJobClose = 0x00002000;
        private const int JobObjectExtendedLimitInformation = 9;
        private const uint ProcessTerminate = 0x0001;
        private const uint ProcessSetQuota = 0x0100;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimitInformation info, uint length);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint access, bool inheritHandle, uint processId);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);

        public static IntPtr AttachCurrentProcess() {
            IntPtr job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not create the launcher process-lifetime job.");
            try {
                ExtendedLimitInformation info = new ExtendedLimitInformation();
                info.BasicLimitInformation.LimitFlags = JobObjectLimitKillOnJobClose;
                uint length = (uint)Marshal.SizeOf(typeof(ExtendedLimitInformation));
                if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, ref info, length))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not enable process cleanup when the launcher closes.");

                IntPtr process = OpenProcess(ProcessSetQuota | ProcessTerminate, false, (uint)System.Diagnostics.Process.GetCurrentProcess().Id);
                if (process == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not open the launcher process for lifecycle binding.");
                try {
                    if (!AssignProcessToJobObject(job, process))
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not bind Bridge processes to the launcher window lifetime.");
                } finally { CloseHandle(process); }
                return job;
            } catch {
                CloseHandle(job);
                throw;
            }
        }
    }
}
'@
if (-not ('AlchemyRenderBridge.ProcessLifetimeJob' -as [type])) {
    Add-Type -TypeDefinition $processLifetimeJobSource -Language CSharp
}

function Get-UserFirstEnvironmentValue([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name, 'User')
    if ([string]::IsNullOrWhiteSpace($value)) { $value = [Environment]::GetEnvironmentVariable($Name, 'Process') }
    return $value
}

function Get-ConfigValue($Config, [string]$Name, $Default) {
    if ($Config.PSObject.Properties.Name -contains $Name -and $null -ne $Config.$Name) { return $Config.$Name }
    return $Default
}

function Invoke-LocalJson([string]$Method, [string]$Url, [string]$Token) {
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(4)
    if ($Token) { $client.DefaultRequestHeaders.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $Token) }
    try {
        $response = $client.SendAsync([System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($Method), $Url)).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) { return $null }
        return ($response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json)
    } catch { return $null }
    finally { $client.Dispose() }
}

function Get-ConnectionLabel($Health) {
    if ($Health -and ($Health.status -eq 'ok' -or $Health.ok -eq $true)) { return '运行中' }
    return '未连接'
}

function Get-NodeLabel([string]$State) {
    switch ($State) {
        'available' { return '在线 / 空闲' }
        'busy' { return '在线 / 执行中' }
        'offline' { return '离线' }
        'attention' { return '需处理' }
        default { return '状态未知' }
    }
}

function Get-ProcessTree([int]$RootId) {
    $all = @(Get-CimInstance Win32_Process)
    $ids = New-Object 'System.Collections.Generic.HashSet[int]'
    [void]$ids.Add($RootId)
    do {
        $changed = $false
        foreach ($p in $all) {
            if ($ids.Contains([int]$p.ParentProcessId) -and $ids.Add([int]$p.ProcessId)) { $changed = $true }
        }
    } while ($changed)
    return @($ids)
}

try {
    try { $Host.UI.RawUI.WindowTitle = 'Alchemy Render Bridge Status' } catch { }
    Write-Host 'Starting Alchemy local Bridge and Codex node...' -ForegroundColor Cyan
    try {
        $processLifetimeJob = [AlchemyRenderBridge.ProcessLifetimeJob]::AttachCurrentProcess()
    } catch {
        throw "Could not enable automatic Bridge cleanup when this window closes: $($_.Exception.Message)"
    }

    $configPath = Get-UserFirstEnvironmentValue 'ALCHEMY_RENDER_BRIDGE_PRIMARY_CONFIG'
    $sourceToken = Get-UserFirstEnvironmentValue 'ALCHEMY_RENDER_BRIDGE_PRIMARY_TOKEN'
    $allowHttp = Get-UserFirstEnvironmentValue 'ALCHEMY_RENDER_BRIDGE_ALLOW_HTTP_LOOPBACK'
    if (-not $configPath -or -not (Test-Path -LiteralPath $configPath)) { throw 'Alchemy user-level Bridge config was not found.' }
    if (-not $sourceToken) { throw 'Alchemy user-level Source token was not found.' }
    if ($allowHttp -ne 'true') { throw 'Loopback HTTP is not enabled in Alchemy user-level settings.' }

    $alchemy = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($alchemy.mode -ne 'enabled' -or $alchemy.base_url -ne 'http://127.0.0.1:8798' -or
        $alchemy.workspace -ne 'primary-acceptance-workspace' -or @($alchemy.profiles) -notcontains 'codex-image') {
        throw 'Alchemy user-level settings do not match the verified local Bridge endpoint, workspace, and Codex profile.'
    }

    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $repoParent = Split-Path $repoRoot -Parent
    $repoWorkspaceRoot = Split-Path $repoParent -Parent
    $pythonCandidates = @(
        (Join-Path $repoRoot '.venv-bridge\Scripts\python.exe'),
        (Join-Path $repoWorkspaceRoot '.venv-bridge\Scripts\python.exe')
    )
    $python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $python) { throw 'Bridge Python environment is missing. Restore the Bridge runtime environment and retry.' }
    $codex = Get-Command codex.cmd -ErrorAction SilentlyContinue
    if (-not $codex) { throw 'Codex CLI is not available in the current Windows user PATH.' }
    $cliVersion = (& $codex.Source --version 2>$null | Select-Object -First 1).Trim()
    if ($cliVersion -ne 'codex-cli 0.163.0') { throw "Codex CLI version '$cliVersion' has no accepted local image execution configuration." }
    Write-Host 'Checking Codex CLI login...' -ForegroundColor DarkCyan
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5 wraps successful CLI stderr text in a
        # NativeCommandError when ErrorActionPreference is Stop.
        $ErrorActionPreference = 'Continue'
        $loginOutput = @(& $codex.Source login status 2>&1)
        $loginExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $loginText = $loginOutput -join "`n"
    if ($loginExitCode -ne 0) {
        throw "Codex CLI login status exited with code $loginExitCode. Run `"codex login`" in this Windows account and reopen this launcher."
    }
    if ($loginText -notmatch '(?i)\bLogged in\b') {
        throw 'Codex CLI returned an unrecognized login status. Run "codex login" in this Windows account and reopen this launcher.'
    }
    $codexLoginVerified = $true
    Write-Host 'Codex CLI login: verified' -ForegroundColor Green

    $alchemyBridgeConfig = 'D:\AI\Alchemy Media Agent System\.runtime\render-bridge-local\config\bridge.json'
    if (-not (Test-Path -LiteralPath $alchemyBridgeConfig)) { throw 'The existing local Bridge identity and data configuration were not found.' }
    $existing = Get-Content -LiteralPath $alchemyBridgeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($existing.workspace_id -ne $alchemy.workspace) { throw 'The existing Bridge data belongs to a different Alchemy workspace; refusing to switch it.' }
    if ($existing.host -ne '127.0.0.1' -or [int]$existing.port -ne 8798) { throw 'The existing Bridge config does not own the Alchemy loopback endpoint.' }
    if (-not (Test-Path -LiteralPath $existing.data_dir)) { throw 'The existing Bridge ledger directory is missing; the launcher will not create a replacement ledger.' }

    $origin = 'http://127.0.0.1:8798'
    $caps = Invoke-LocalJson 'Get' "$origin/bridge/v1/capabilities" $sourceToken
    $attachedToExistingRuntime = $false
    if ($caps -and (-not $caps.workspace -or $caps.workspace -eq $alchemy.workspace)) {
        $reportedProfiles = @($caps.node_status.reported_profiles)
        $nodeState = [string]$caps.node_status.state
        $activeTaskCount = [int]$caps.queue.active_tasks
        if (($nodeState -in @('available','busy','attention') -and $reportedProfiles -contains 'codex-image') -or
            $activeTaskCount -gt 0) {
            $attachedToExistingRuntime = $true
            Write-Host 'Existing authenticated Bridge found; preserving its current runtime.' -ForegroundColor Green
            Write-Host "Endpoint: $origin | workspace: $($alchemy.workspace) | node: $nodeState | profile: codex-image"
            Write-Host "Bridge active/queued tasks: $activeTaskCount / $($caps.queue.max_queued_tasks)"
            Write-Host 'This window is monitoring the existing runtime. Closing it will not stop processes owned by another launcher.' -ForegroundColor Yellow
        }
    }

    if (-not $attachedToExistingRuntime) {
    Write-Host 'Preflight passed. Preparing the local Bridge runtime...' -ForegroundColor DarkCyan

    $managedAgentRoot = Join-Path $env:LOCALAPPDATA 'AlchemyRenderBridge'
    $existingProcesses = @(Get-CimInstance Win32_Process)
    $managedLaunchers = @($existingProcesses | Where-Object {
        $_.CommandLine -match 'start-agent\.ps1' -and
        $_.CommandLine.IndexOf($managedAgentRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    if ($managedLaunchers.Count -gt 0) {
        throw 'A managed Codex node process already exists but could not be authenticated through Bridge; it was left untouched.'
    }

    $listener = Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -LocalPort 8798 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        $parent = if ($process) { Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ParentProcessId)" }
        $runtimeLine = @($process.CommandLine, $parent.CommandLine) -join ' '
        $managedRuntimeConfig = Join-Path $env:LOCALAPPDATA 'AlchemyRenderBridge\bridge.json'
        if ($runtimeLine -notmatch 'render_bridge\.runtime' -or
            ($runtimeLine.IndexOf($managedRuntimeConfig, [StringComparison]::OrdinalIgnoreCase) -lt 0 -and
             $runtimeLine.IndexOf($alchemyBridgeConfig, [StringComparison]::OrdinalIgnoreCase) -lt 0)) {
            throw 'Port 8798 is owned by an unrecognized process; it was left untouched.'
        }
        throw 'The configured Bridge already owns port 8798 but could not be authenticated; it was left untouched.'
    }

    $stateRoot = $managedAgentRoot
    New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null
    $credentialFile = Join-Path $stateRoot 'node-token.dpapi'
    $nodeToken = $null
    if (Test-Path -LiteralPath $credentialFile) {
        $entropy = [Text.Encoding]::UTF8.GetBytes('AlchemyRenderBridge.NodeToken.v1')
        $protected = [IO.File]::ReadAllBytes($credentialFile)
        $plain = [Security.Cryptography.ProtectedData]::Unprotect($protected, $entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $stored = [Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json
        $nodeToken = $stored.node_token
        [Array]::Clear($plain, 0, $plain.Length)
    }
    if (-not $nodeToken) {
        $random = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($random) } finally { $rng.Dispose() }
        $nodeToken = [Convert]::ToBase64String($random).TrimEnd('=').Replace('+','-').Replace('/','_')
        [Array]::Clear($random, 0, $random.Length)
    }

    $runtimeConfigPath = Join-Path $stateRoot 'bridge.json'
    $agentConfigPath = Join-Path $stateRoot 'agent.json'
    $liveDataDir = Join-Path (Split-Path $existing.data_dir -Parent) 'bridge-data-live'
    $runtimeConfig = @{
        data_dir = [IO.Path]::GetFullPath($liveDataDir)
        source_id = [string]$existing.source_id
        workspace_id = [string]$existing.workspace_id
        node_id = [string]$existing.node_id
        executor_instance = [string]$existing.executor_instance
        enabled_profiles = @('codex-image')
        accepting_new_tasks = [bool](Get-ConfigValue $existing 'accepting_new_tasks' $true)
        max_queued_tasks = [int](Get-ConfigValue $existing 'max_queued_tasks' 10)
        host = '127.0.0.1'
        port = 8798
        source_token_env = 'BRIDGE_SOURCE_TOKEN'
        node_token_env = 'BRIDGE_NODE_TOKEN'
        dispatch_mode = 'node_pull'
        retention_enabled = [bool](Get-ConfigValue $existing 'retention_enabled' $false)
        retention_seconds = [int](Get-ConfigValue $existing 'retention_seconds' 86400)
        maintenance_interval_seconds = [int](Get-ConfigValue $existing 'maintenance_interval_seconds' 60)
        maintenance_batch_size = [int](Get-ConfigValue $existing 'maintenance_batch_size' 100)
    } | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText($runtimeConfigPath, $runtimeConfig, [Text.UTF8Encoding]::new($false))

    $agentConfig = @{
        enabled_profiles = @('codex-image')
        health_reporting_enabled = $false
        account_health_probe_enabled = $false
        codex = @{
            enabled = $true
            executable = $codex.Source
            model = 'gpt-6.1-sol'
            accepted_cli_version = 'codex-cli 0.163.0'
            approval_policy = 'on-request'
            image_capability_verified = $true
            tool_policy_verified = $true
            timeout = 120
        }
    } | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText($agentConfigPath, $agentConfig, [Text.UTF8Encoding]::new($false))

    if (-not (Test-Path -LiteralPath $credentialFile)) {
        $plain = [Text.Encoding]::UTF8.GetBytes((@{ node_token = $nodeToken } | ConvertTo-Json -Compress))
        $entropy = [Text.Encoding]::UTF8.GetBytes('AlchemyRenderBridge.NodeToken.v1')
        $protected = [Security.Cryptography.ProtectedData]::Protect($plain, $entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        [IO.File]::WriteAllBytes($credentialFile, $protected)
        [Array]::Clear($plain, 0, $plain.Length)
    }

    $env:PYTHONPATH = (Join-Path $repoRoot 'src') + $(if ($env:PYTHONPATH) { ';' + $env:PYTHONPATH } else { '' })
    $env:BRIDGE_SOURCE_TOKEN = $sourceToken
    $env:BRIDGE_NODE_TOKEN = $nodeToken
    $runtimeArgs = @('-m','render_bridge.runtime','--config',$runtimeConfigPath)
    $server = Start-Process -FilePath $python -ArgumentList "-m render_bridge.runtime --config `"$runtimeConfigPath`"" -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $stateRoot 'bridge.stdout.log') -RedirectStandardError (Join-Path $stateRoot 'bridge.stderr.log')
    [IO.File]::WriteAllText((Join-Path $stateRoot 'server-process.json'), (@{pid=$server.Id; config=$runtimeConfigPath} | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))

    Write-Host 'Bridge started. Waiting for authenticated Alchemy connection...' -ForegroundColor DarkCyan
    $ready = $false
    $deadline = (Get-Date).AddSeconds(25)
    do {
        Start-Sleep -Milliseconds 500
        $caps = Invoke-LocalJson 'Get' "$origin/bridge/v1/capabilities" $sourceToken
        if ($caps) { break }
    } while ((Get-Date) -lt $deadline)
    if (-not $caps) { throw 'Bridge did not accept the Alchemy user-level Source credential.' }
    if ($caps.workspace -and $caps.workspace -ne $alchemy.workspace) { throw 'Bridge reported an unexpected workspace.' }

    $agentStateRoot = Join-Path $stateRoot 'agent-state-live'
    New-Item -ItemType Directory -Force -Path $agentStateRoot | Out-Null
    $workerPath = Join-Path $stateRoot 'start-agent.ps1'
    $workerScript = "`$ErrorActionPreference = 'Stop'`r`n`$env:PYTHONPATH = '$((Join-Path $repoRoot 'src').Replace("'","''"))'`r`n& '$($python.Replace("'","''"))' -m render_bridge.agent.hatchet_sidecar --url $origin pull-worker --state-dir '$($agentStateRoot.Replace("'","''"))' --config '$($agentConfigPath.Replace("'","''"))'"
    [IO.File]::WriteAllText($workerPath, $workerScript, [Text.UTF8Encoding]::new($false))
    $worker = Start-Process -FilePath 'powershell.exe' -ArgumentList "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$workerPath`"" -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $stateRoot 'agent.stdout.log') -RedirectStandardError (Join-Path $stateRoot 'agent.stderr.log')
    [IO.File]::WriteAllText((Join-Path $stateRoot 'agent-process.json'), (@{pid=$worker.Id} | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))

    Write-Host 'Codex node started. Waiting for its first online heartbeat...' -ForegroundColor DarkCyan
    $ready = $false
    $deadline = (Get-Date).AddSeconds(35)
    do {
        Start-Sleep -Milliseconds 500
        $caps = Invoke-LocalJson 'Get' "$origin/bridge/v1/capabilities" $sourceToken
        if ($caps -and $caps.node_status.state -in @('available','busy','attention') -and
            @($caps.node_status.reported_profiles) -contains 'codex-image') { $ready = $true; break }
    } while ((Get-Date) -lt $deadline)
    if (-not $ready) { throw 'Bridge API authenticated, but the Codex node did not report its configured profile.' }

    Write-Host 'Alchemy local Bridge connection is ready.' -ForegroundColor Green
    Write-Host "Endpoint: $origin | workspace: $($alchemy.workspace) | node: $($caps.node_status.state) | profile: codex-image"
    Write-Host 'Codex CLI login: verified; task execution will use this local CLI installation.' -ForegroundColor Green
    Write-Host 'No task was created and no image generation was started.'
    Write-Host 'Bridge and the outbound Codex node are bound to this window.'
    Write-Host 'Status refreshes every 5 seconds. Closing this window stops the Bridge and Codex node.'
    }

    while ($true) {
        $v1Health = Invoke-LocalJson 'Get' 'http://127.0.0.1:8017/healthz' $null
        $v2Health = Invoke-LocalJson 'Get' 'http://127.0.0.1:8020/api/v2/health' $null
        $caps = Invoke-LocalJson 'Get' "$origin/bridge/v1/capabilities" $sourceToken

        Write-Host ''
        Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Alchemy local status" -ForegroundColor Cyan
        Write-Host "V1 / V3 / Lab (127.0.0.1:8017): $(Get-ConnectionLabel $v1Health) | V2 (127.0.0.1:8020): $(Get-ConnectionLabel $v2Health)"
        if ($caps) {
            $nodeState = [string]$caps.node_status.state
            $profiles = @($caps.node_status.reported_profiles)
            $codexAvailable = $profiles -contains 'codex-image'
            $queueActive = [int]$caps.queue.active_tasks
            $queueLimit = [int]$caps.queue.max_queued_tasks
            Write-Host "Alchemy -> Bridge: authenticated | Codex Node: $(Get-NodeLabel $nodeState)" -ForegroundColor Green
            if ($codexLoginVerified) {
                Write-Host 'Codex CLI login: verified locally' -ForegroundColor Green
            }
            if ($codexAvailable) {
                Write-Host 'Codex profile: reported'
            } else {
                Write-Host 'Codex profile: not reported' -ForegroundColor Yellow
            }
            if (-not $codexAvailable) {
                Write-Host 'Codex execution line: unknown / 1 (Codex profile not reported)' -ForegroundColor Red
            } else {
                switch ($nodeState) {
                    'available' { Write-Host 'Codex execution line: 0 / 1 (idle)' -ForegroundColor Green }
                    'busy' { Write-Host 'Codex execution line: 1 / 1 (busy)' -ForegroundColor Yellow }
                    default { Write-Host 'Codex execution line: unknown / 1 (node unavailable)' -ForegroundColor Red }
                }
            }
            Write-Host "Bridge active/queued tasks: $queueActive / $queueLimit | admission: $($caps.admission.mode)"
            if (-not $caps.node_status.real_generation_verified) {
                Write-Host 'Real image generation: not verified'
            }
        } else {
            Write-Host 'Alchemy -> Bridge: disconnected / authentication failed' -ForegroundColor Red
            Write-Host 'Codex execution line: unknown / 1' -ForegroundColor Red
            Write-Host 'Bridge active/queued tasks: unavailable'
        }
        Write-Host ('-' * 88)
        Start-Sleep -Seconds 5
    }
} catch {
    Write-Host "Bridge launcher error: $($_.Exception.Message)" -ForegroundColor Red
    [void](Read-Host 'Press Enter to close this status window')
    exit 1
} finally {
    $sourceToken = $null
    $nodeToken = $null
    $protectedPayload = $null
    $securePayload = $null
}
