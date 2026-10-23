# Windows 同机联调：Alchemy → Render Bridge → 用户自己的 Codex CLI

2026-10-21。当前用户验收拓扑是**同一台 Windows 电脑**，不是先部署两台 VPS。
本指南是安装与验收操作说明，不是已替用户安装、登录、运行或真实生图成功的记录。
远程生产仍要求独立发布、TLS 和独立数据；同机不允许共享数据库、私有目录或 Python 内部对象。

## 0. 先区分三项完成条件

1. **代码安装**：两个仓库固定到最终交付 revision，独立 Python 环境可导入，fake/HTTP 测试通过。
2. **运行配置**：操作者提供已有 Bridge 三角色凭据、Hatchet 引擎与凭据；用户自己的 Codex CLI 在本机登录。此项不证明图片能力。
3. **真实图片验收**：明确授权的一张真实图完成冻结请求、CLI 原图、Bridge receipt、Alchemy 原生登记/审核。仅文本回复、版本号、心跳绿灯或 HTTP 200 均不算完成。

不要复制 Codex 登录文件到 Bridge 服务配置、Alchemy 或云端。不要把凭据写进 Git、终端命令历史或报告。
本页命令由操作者自行在本机执行；本轮代码交付不创建账户/token、不修改 Windows 安全设置、不调用付费提供方。

## 1. 下载/更新，但保留现有 Alchemy

以下使用 PowerShell、Git 和已安装 Python 3.12（`py -3.12 --version`）。建议使用独立新目录验证，再决定如何迁移，不能对现有目录执行 reset/clean 或覆盖配置。

首次下载：

```powershell
$HomeDir = Join-Path $env:USERPROFILE 'AlchemyBridgeTrial'
if (Test-Path $HomeDir) { throw '请选择全新的试验目录名，不覆盖已有代码/配置/数据' }
New-Item -ItemType Directory -Force $HomeDir | Out-Null
Set-Location $HomeDir
git clone https://github.com/meta-xucong/alchemy-render-bridge.git render-bridge
git clone https://github.com/meta-xucong/alchemy-media-agent.git alchemy
$Bridge = Join-Path $HomeDir 'render-bridge'
$Alchemy = Join-Path $HomeDir 'alchemy'
```

不要把默认分支当作已含本阶段代码。最终交付收据必须给出**可 fetch 的分支和完整提交 hash**；下面故意保留占位符，未替换前不能执行 checkout。基线 `de768186` / `901a9a37` 只是开发起点，不是完成版，也不保证已发布到远端。

```powershell
$BridgeRef = 'feat/phase1-bridge-contracts'
$BridgeCommit = '<最终交付 Bridge 40位提交>'
$AlchemyRef = 'feature/render-bridge-preview'
$AlchemyCommit = '<最终交付 Alchemy 40位提交>'
if (($BridgeRef + $BridgeCommit + $AlchemyRef + $AlchemyCommit).Contains('<')) {
    throw '先用最终交付收据替换分支和提交；不要猜 main 或 HEAD'
}
if ($BridgeCommit -notmatch '^[0-9a-f]{40}$' -or $AlchemyCommit -notmatch '^[0-9a-f]{40}$') {
    throw '必须使用交付收据的完整40位提交 hash'
}
git -C $Bridge fetch origin $BridgeRef
git -C $Bridge cat-file -e "$BridgeCommit^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'Bridge 提交尚不可下载，停止安装' }
git -C $Bridge switch --detach $BridgeCommit
git -C $Alchemy fetch origin $AlchemyRef
git -C $Alchemy cat-file -e "$AlchemyCommit^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'Alchemy 提交尚不可下载，停止安装' }
git -C $Alchemy switch --detach $AlchemyCommit
git -C $Bridge rev-parse HEAD
git -C $Alchemy rev-parse HEAD
```

已有 Alchemy：先 `git status --short`、`git branch --show-current`、`git rev-parse HEAD`，记录本地修改和运行配置位置。停止相关写入进程后，备份完整现有仓库（包括未跟踪/忽略文件）、实际数据库、媒体、Bridge 模块映射目录及配置到另一私有位置，核对备份可读。不要在运行 SQLite 时只复制单个 DB 文件，不漏 WAL/journal。先在新 clone 测试；现有本地修改由操作者逐项审阅移植，不自动 stash、merge、rebase 或覆盖。不要复制旧 `.venv` 到新版本。

回滚也先停写并保留新任务数据；恢复匹配代码和整套一致备份，不能仅换旧代码并丢掉新版本未决任务。不要通过删除 Agent journal 解锁 unknown。

## 2. 独立目录、端口与环境

建议布局：

- `$HomeDir\render-bridge`：Bridge 源码，`.venv-bridge`、`.venv-hatchet` 两个解释器。
- `$HomeDir\alchemy`：Alchemy 源码，`.venv-alchemy` 独立解释器。
- `$HomeDir\private\bridge`：Bridge DB/私有产物；`private\agent`：Agent journal/原图。
- `$HomeDir\private\alchemy-media`：Alchemy 原生存储；`private\alchemy-bridge`：Alchemy 桥接映射；二者不得指向 Bridge 目录。
- Bridge HTTP `127.0.0.1:8787`；Alchemy HTTP `127.0.0.1:8017`。Hatchet 引擎使用自己显式配置的独立端口，不能假定已监听。

```powershell
$Private = Join-Path $HomeDir 'private'
New-Item -ItemType Directory -Force $Private | Out-Null
Set-Location $Bridge
py -3.12 -m venv .venv-bridge
$BridgePython = Join-Path $Bridge '.venv-bridge\Scripts\python.exe'
& $BridgePython -m pip install --require-hashes -r requirements-runtime.txt
& $BridgePython -m pip install --no-deps -e .
& $BridgePython -m pip check
Copy-Item config\windows-loopback\bridge.fake.json "$Private\bridge.json"
Copy-Item config\windows-loopback\agent.fake.json "$Private\agent.json"
# JSON 使用绝对目录，避免不同窗口的工作目录改变数据位置；写入 UTF-8 无 BOM。
$c = Get-Content "$Private\bridge.json" -Raw | ConvertFrom-Json
$c.data_dir = Join-Path $Private 'bridge'
[IO.File]::WriteAllText("$Private\bridge.json", ($c | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false)))
& $BridgePython -m render_bridge.doctor --bridge-config "$Private\bridge.json" --agent-config "$Private\agent.json" --json
```

每条安装命令都检查成功后再继续。doctor 默认不联网、不读 token、不启动 CLI、不创建 DB；退出 0 仅指检查通过，不是登录或真实生图验证。不要以 `--probe-codex` 代替真实验收。

## 3. Hatchet：SDK 不是引擎

完整自动派发需要**已经运行的 Hatchet 引擎及其数据库**、兼容 REST/gRPC 地址和操作者已有的有效 token。安装 SDK 不能启动引擎。本仓库尚无面向用户的持久 Windows 引擎一键安装包。
[WSL 原生联调记录](WSL_NATIVE_ENGINE_VALIDATION.md)仅证明历史 CI 的临时 Linux 引擎与 Windows worker 三个合成任务，不是用户电脑验收，也不是可长期复用的服务配置。

### 3.1 本机持久引擎的可执行准备路线

优先用用户已安装的 Docker Desktop（Linux containers / WSL2 后端），由 Docker 运行独立 Hatchet Lite 和 PostgreSQL；Bridge/Agent/Alchemy 仍是原生 Windows 进程。没有 Docker 时，由用户先按 [Docker 官方 Windows 安装页](https://docs.docker.com/desktop/setup/install/windows-install/)核对支持的 Windows 版本、硬件与条款并安装启动。WSL2/虚拟化/系统权限改变须由用户明确处理；本交付不替用户启用。不支持的 Windows Server/硬件不能用 CI WSL1 记录当作替代安装保证。

本配方依据 [Hatchet Lite 官方部署说明](https://docs.hatchet.run/self-hosting/hatchet-lite) 的 PostgreSQL 模式改为回环发布、独立卷、必填私有密码和固定镜像。引擎选定 [v0.110.5 官方 release](https://github.com/hatchet-dev/hatchet/releases/tag/v0.110.5)，x86-64 镜像 tag 由该版[发布 workflow](https://github.com/hatchet-dev/hatchet/blob/v0.110.5/.github/workflows/release.yaml)核对为 `v0.110.5-amd64`；不是 `latest`，也不是无鉴权 `-dev` 镜像。ARM Windows 不套用此 x86 配方，需单独兼容性验证。PostgreSQL `15.6` 仅沿用官方本地示例，不是生产安全推荐。

PowerShell，已启动 Docker Desktop 后：

```powershell
docker version
docker compose version
# 应为 linux；若命令失败/服务未启动，先解决 Docker，不继续拉取运行。
docker info --format '{{.OSType}}'
$EngineConfig = Join-Path $Private 'hatchet.compose.json'
Copy-Item "$Bridge\config\windows-loopback\hatchet.compose.json" $EngineConfig
# 操作者安全提供 HATCHET_POSTGRES_PASSWORD；要求长随机字母数字，以避免 URI 转义歧义。
# 这里不生成、不打印密码；缺失会明确停止。不要覆盖已有卷所用的密码。
if ($env:HATCHET_POSTGRES_PASSWORD -notmatch '^[A-Za-z0-9]{24,}$') {
    throw '通过自己的秘密管理流程提供至少24位字母数字数据库密码'
}
# --quiet 验证配置而不把已插值的密码打印到终端。
docker compose -f $EngineConfig config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose 配置未通过' }
docker compose -f $EngineConfig pull
if ($LASTEXITCODE -ne 0) { throw '固定镜像不可拉取；不要改用 latest 绕过' }
# 仅记录镜像身份，不打印完整 container inspect（其中会有凭据）。
docker image inspect ghcr.io/hatchet-dev/hatchet/hatchet-lite:v0.110.5-amd64 --format '{{json .RepoDigests}}'
docker image inspect postgres:15.6 --format '{{json .RepoDigests}}'
docker compose -f $EngineConfig up -d
docker compose -f $EngineConfig ps
Test-NetConnection 127.0.0.1 -Port 8888
Test-NetConnection 127.0.0.1 -Port 7077
```

首次 pull 记录实际 digest 后，把本机私有 Compose 的 image 改为对应 `repository@sha256:...` 并保留记录，后续复现使用同一 digest。tag 固定不等于已经验证 registry digest；此云端文档检查未拉镜像/启动 Docker，最终以本机 pull 与 fake 任务验收为准。

打开 `http://localhost:8888`，按官方 Lite 初次登录说明操作；初始化用户/租户、修改初始密码以及生成 API token 都由操作者在该本地实例完成，需要的授权不由代码安装暗示。默认登录信息见官方说明，不将其作为生产凭据。不要使用 `--disable-auth` 或把仪表板开放到 LAN。为本试验租户准备 worker/dispatcher 所用 token，安全注入各自环境；不要把 token 贴进文档/聊天。地址配置为：

```powershell
$env:HATCHET_CLIENT_SERVER_URL = 'http://localhost:8888'
$env:HATCHET_CLIENT_HOST_PORT = 'localhost:7077'
$env:HATCHET_CLIENT_TLS_STRATEGY = 'none'
# HATCHET_CLIENT_TOKEN 由操作者安全注入此窗口，绝不在命令文本中填写实际值。
```

PostgreSQL 不发布宿主端口；Hatchet 两个宿主端口均只绑定 `127.0.0.1`。容器内 `0.0.0.0` 仅供映射，不代表 Windows 对外监听。命名卷只供此引擎使用，不与 Bridge/Alchemy 共享。停止使用 `docker compose -f $EngineConfig stop`，再次启动使用相同配置的 `up -d`；**不要 `down -v` 或 Docker prune**。升级前停止相关写入并备份引擎数据/配置卷，保持 token、租户和任务记录一致。

缺少引擎时仍可完成第 2 节和第 6 节手动 fake 链路，但必须报告“自动调度尚缺引擎”，不能将模拟派发冒充 Hatchet 完成。已有可信且兼容的本机 Linux 引擎可复用，不要求重新安装 Docker；仍需验证回环地址与持久化配置。

### 3.2 Windows SDK 侧车


在 Bridge 根目录安装**独立 SDK 环境**，不能安装主 Bridge distribution 或其 requirements-runtime 到此环境：

```powershell
Set-Location $Bridge
py -3.12 -m venv .venv-hatchet
$SdkPython = Join-Path $Bridge '.venv-hatchet\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Push-Location experiments\hatchet-windows-compat
& $SdkPython -m pip install --require-hashes -r requirements-upstream.txt -r requirements-sidecar.txt
& $SdkPython probe_upstream.py
& $SdkPython -m pip download --no-deps --dest .cache hatchet-sdk==1.42.1
& $SdkPython build_candidate.py .cache\hatchet_sdk-1.42.1-py3-none-any.whl
& $SdkPython verify_candidate.py
& $SdkPython -m pip install --no-deps --force-reinstall dist\hatchet_sdk-1.42.1+bridgewin1-py3-none-any.whl
& $SdkPython -m pip check
& $SdkPython -m unittest discover -s tests -p test_sidecar_runtime.py -v
Pop-Location
$env:PYTHONPATH = Join-Path $Bridge 'src'
& $SdkPython -m render_bridge.agent.hatchet_sidecar --help
```

Windows 固定候选 `1.42.1+bridgewin1`；Linux 使用官方 `1.42.1`。构建/校验失败即停止，不略过 hash 或改用任意最新版。完整配方与第三方许可见[实验说明](../../experiments/hatchet-windows-compat/README.md#configured-bridge-sidecar)。

## 4. 凭据分配与启动顺序

操作者通过自己的安全流程将**已有**凭据放入对应进程环境，不在文档中提供实际值：

- Bridge 服务：`BRIDGE_SOURCE_TOKEN`、`BRIDGE_NODE_TOKEN`、`BRIDGE_DISPATCH_TOKEN`，三值必须不同。
- Alchemy：`ALCHEMY_RENDER_BRIDGE_TOKEN` 必须等于上述 source token；不需要 node/dispatcher/Hatchet token。
- dispatcher：只分配 `BRIDGE_DISPATCH_TOKEN` 和 Hatchet 配置。
- worker/手动 Agent：只分配 `BRIDGE_NODE_TOKEN`；自动 worker 还需 Hatchet 配置。
- Hatchet 配置：`HATCHET_CLIENT_TOKEN`、`HATCHET_CLIENT_HOST_PORT`、`HATCHET_CLIENT_SERVER_URL`。不能从示例猜端口/token。

不使用 `setx` 永久保存秘密，也不让所有程序继承全部凭据。新 PowerShell 窗口需重新设置自己的路径变量、环境和角色凭据。不要把完整环境打印到报告。

启动顺序：已有 Hatchet 引擎及其数据库 → Bridge → worker → 唯一 dispatcher → Alchemy。
三个 Bridge 进程分别在 Bridge 根目录终端运行，使用上文对应解释器和绝对路径：

```powershell
# 终端 A：Bridge
& $BridgePython -m render_bridge.runtime --config "$Private\bridge.json"
# 终端 B：worker；先设置源码 PYTHONPATH、UTF-8、node 及 Hatchet 环境
$env:BRIDGE_URL = 'http://127.0.0.1:8787'
& $SdkPython -m render_bridge.agent.hatchet_sidecar worker --state-dir "$Private\agent" --config "$Private\agent.json"
# 终端 C：唯一 dispatcher；设置 dispatcher 及 Hatchet 环境
$env:BRIDGE_URL = 'http://127.0.0.1:8787'
& $SdkPython -m render_bridge.agent.hatchet_sidecar dispatcher
```

TLS 默认不变。只有 REST 与 gRPC **都确实是回环**的本地引擎才设置 `HATCHET_CLIENT_TLS_STRATEGY=none`；远程保持 `tls`，不能关闭证书校验。Bridge 也只允许回环 HTTP；同机不等于可以绑定 `0.0.0.0` 或开放 LAN。

## 5. Alchemy 本机 fake 配置与启动

### 5.1 先核对宿主前提，不能把 fake 渲染器当作完整 mock 宿主

以下 fresh-clone 配置只准备隔离服务，并不建立本地账户、登录会话、项目归属或 Brain/审核能力。`fake-image` 只替代渲染器，不替代 Alchemy 登录、canonical Brain 规划和共享审核；原生入口仍要求 `remote_brain_canonical`，不能把测试 fixture 的签核注入用户服务。

先只读核对 `D:\AI\Alchemy Media Agent System` 是否确为用户现有目录、分支、完整 revision、修改及实际启动方式；不覆盖该目录、不 merge、不 reset/clean。本文的新 clone 是独立功能分支联调，不是该主目录的 mainline 验收或正式升级。所有私有配置与数据先按第1节备份，再由操作者逐项选择可复用设置，不盲拷整个 `.env`，不打印或上传秘密。

进入管理员卡片及原生试用前，须核实已有合法管理员/用户、本人项目、有效登录会话及已授权的 canonical Brain/共享审核运行配置。实际宿主认证使用 `VEYRA_AUTH_ENABLED`、`VEYRA_SUB2API_BASE_URL`、`VEYRA_INTERNAL_TOKEN`，会话校验使用 `VEYRA_SESSION_SECRET` 或已有内部 token；应核对现有正确配置，不新造 token、伪签 JWT 或关闭认证绕过。默认 `VEYRA_AUTH_ENABLED=false` 时管理员 API 返回401，打开页面不等于已登录。核对 loopback 地址对应的现有登录/cookie 策略，不擅自降低安全设置。

还须核对祖先 `.env` 和 `MEDIA_AGENT_RUNTIME_ENV_FILE`、`ALCHEMY_MEDIA_AGENT_ENV_FILE`、`ALCHEMY_MEDIA_RUNTIME_ENV_FILE` 的实际来源，确保隔离终端没有意外加载 live 配置。即使选择 fake 图，规划/审核仍可能调用模型并产生费用；须先明确本次允许的服务、素材及费用范围。缺少这些前提时只完成配置检查及第6节独立 configured demo/预览层验收，报告原生联调被宿主前提阻断，不声称完整链路通过。预览页面同样不能代替其适用的合法身份要求。

### 5.2 隔离服务的配置示例

新 clone 安装，不覆盖现有 Alchemy 环境：

```powershell
Set-Location $Alchemy
py -3.12 -m venv .venv-alchemy
$AlchemyPython = Join-Path $Alchemy '.venv-alchemy\Scripts\python.exe'
& $AlchemyPython -m pip install -r src_skeleton\requirements.txt
& $AlchemyPython -m pip check
$env:PYTHONPATH = "$Alchemy;$Alchemy\src_skeleton"
$env:MEDIA_STORAGE_ROOT = Join-Path $Private 'alchemy-media'
$env:MEDIA_AGENT_MODE = 'mock'
$env:MOCK_IMAGE_PROVIDER_ENABLED = 'true'
$env:ALCHEMY_RENDER_BRIDGE_MODE = 'disabled'
$env:ALCHEMY_RENDER_BRIDGE_URL = 'http://127.0.0.1:8787'
$env:ALCHEMY_RENDER_BRIDGE_ALLOW_HTTP_LOOPBACK = 'true'
$env:ALCHEMY_RENDER_BRIDGE_WORKSPACE = 'local-workspace'
$env:ALCHEMY_RENDER_BRIDGE_STORAGE = Join-Path $Private 'alchemy-bridge'
$env:ALCHEMY_RENDER_BRIDGE_PROFILES = 'fake-image'
$env:ALCHEMY_RENDER_BRIDGE_NATIVE_PROFILES = 'fake-image'
$env:ALCHEMY_RENDER_BRIDGE_NATIVE_ENABLED = 'true'
$env:ALCHEMY_RENDER_BRIDGE_LOCAL_PREVIEW = 'true'
# 由操作者安全注入 ALCHEMY_RENDER_BRIDGE_TOKEN，值为 Bridge 的 source token。
& $AlchemyPython -m uvicorn app.main:app --host 127.0.0.1 --port 8017
```

此配置启动时 Bridge 模块关闭，管理员在 `http://127.0.0.1:8017/admin/billing` 的 Bridge 卡片显式开启，再点“检查连接”。沿用已有合法管理员身份，不在此流程创建或绕过认证。若本地原生用户/项目尚未配置，这是另一项运行前提，不冒充联调成功。

使用独立新存储，不复制 live `.env`，不要将 mock 标记理解为进程级网络隔离。Alchemy 会读取运行配置；测试终端不应继承真实上游密钥/运行 env 指针。Bridge fake 不调用付费模型，但宿主规划/审核另有路径：只使用已核验的 mock 模式和合成数据；如发现 live 路由立即停下，不用普通 Generate 作诊断。

## 6. 按层诊断，不用真实图探测连接

```powershell
# 只查监听；端口占用应查明进程，不自动杀进程/改防火墙。
Get-NetTCPConnection -State Listen -LocalPort 8787,8017 -ErrorAction SilentlyContinue
Test-NetConnection 127.0.0.1 -Port 8787
Test-NetConnection 127.0.0.1 -Port 8017
# 使用已有 source 凭据，只读版本化 API；不要输出 header/token。
$headers = @{ Authorization = 'Bearer ' + $env:BRIDGE_SOURCE_TOKEN }
Invoke-RestMethod -Uri 'http://127.0.0.1:8787/bridge/v1/capabilities' -Headers $headers
# 回到 Bridge 解释器做配置检查，按需单独进行无登录 CLI 版本/help 探测。
& $BridgePython -m render_bridge.doctor --bridge-config "$Private\bridge.json" --agent-config "$Private\agent.json" --json
```

- 连接失败：检查绑定地址、启动日志和进程；不要暴露公网作为修复。
- 401/403：检查 source token 对应关系/workspace；不能用 node token 顶替。
- HTTP 通过但任务排队：检查引擎、worker、唯一 dispatcher、outbox；连接检查不派发任务。
- 没有引擎：可在 Alchemy 显式提交一项 fake 试用后，取得该任务的真实 `task_id`，停止自动 worker，再手动交付一次：

```powershell
& $BridgePython -m render_bridge.agent --url http://127.0.0.1:8787 --state-dir "$Private\agent" --config "$Private\agent.json" run-task '<已创建的 task_id>'
```

手动命令不扫描队列。它验证 API/产物/收据，不验收自动引擎。也可单独运行 `scripts/run_configured_demo.py --output-dir <全新输出目录>`，但那是独立模拟器，不是用户正在运行的 Alchemy 服务。

若已有 Codex attempt 仍为 `execution_unknown`，且本地 rollout 明确记录同一 turn 的单张图片已完成、随后该 turn 中止，可在复核 task/attempt/thread/turn 绑定后使用 `reconcile-codex-interrupted <task_id> <rollout.jsonl>`。该命令只验证原图并沿用原 attempt 上传；不调用 Codex、不创建 turn，也不允许从目录中另选图片。最终收据仅用于 review-only。证据不匹配时任务继续保持 `execution_unknown` 并占用原执行槽。

Alchemy 的 V3 Standard/general 当前项目试用入口只提交单图；先确认 fake 产物、相同 receipt、原生审核状态及重复查询不重生。保留两端 revision、配置摘要（无秘密）、任务 ID/receipt/hash、执行方式与结果，不把 fake 图称作 AI 图。

## 7. 从 fake 转真实 Codex，最后才测试自动回退

安装本页默认不启用 Codex/Midjourney，不启用云端自动回退。已有 Codex CLI 由用户在本机维护，不在此重复安装、替换登录或迁移 credentials。

1. 保持 fake 通过，确认自己的 CLI 版本/协议和图像工具策略；doctor 的可选探测只使用空 HOME/CODEX_HOME，不验证真实登录。
2. 由用户明确授权有界真实测试，选择实际支持的 model 和图片尺寸。先在独立工具能力验收中确认实际图片事件、原文件与工具权限，而不是填写 true 来绕过检查。
3. 仅精确 `codex-cli 0.157.1` 与 `codex-cli 0.163.0` 通过此适配器的静态 wire review；这不证明登录或真实生图。每个版本必须单独完成有界本机真实图片验收后，才可为该版本设置能力标记。通过后在私有 Agent JSON 中启用 `codex-image`、`codex.enabled=true`，设置 `codex.accepted_cli_version` 为已验收的精确版本，填写经过验收的 model，并据实设置 `image_capability_verified` / `tool_policy_verified`；运行时探测必须匹配该版本，换版本需重新验收。两版本官方 wire schema 均包含 `untrusted` 审批枚举，但 Bridge 只接受操作者明确选择的 `codex.approval_policy="on-request"`；默认空字符串继续阻断，即使旧配置的两个验收标记已经为 true，也不能静默迁移或自动解释为同意。该字段不是绕过工具审批；需要用户处理的工具请求仍须按工具策略处理。Bridge enabled_profiles、Alchemy module/native allowlists 同时显式加入 `codex-image`；保留同一节点 identity/state-dir。不要将这些字段默认置 true。
4. 重启需要重载配置的进程并检查实际 allowlist，提交一张明确授权的真实试用。Alchemy 若已在管理页保存设置，保存的 `admin-settings.json` 优先于环境中的 module/native allowlist；应在管理页高级设置显式加入工具并保存，仅修改环境变量不会覆盖已有设置。严格核对冻结尺寸、PNG/JPEG 原图、SHA-256、receipt 和 Alchemy review 状态。质量只支持 auto，input_fidelity 必须省略；不把 prompt 文本当作 Codex 原生参数。
5. 可选健康报告：私有 Agent JSON 顶层 `health_reporting_enabled` 默认 false；明确开启后附带在现有心跳循环，不新增调度器。CLI 缺失/磁盘等普通故障可用对应检查恢复；版本/help 成功不能清除 auth/quota 故障，也不能证明图像能力。
   - 可选账户状态探测：顶层 `account_health_probe_enabled` 默认 false。仅 `accepted_cli_version="codex-cli 0.163.0"` 支持该探测；0.157.1 不会调用账户 RPC，状态保持 unknown。只在本机操作者明确同意“通过自己的 CLI 查询已登录账户状态，CLI 可能内部刷新现有登录”后才设置 true，同时开启健康报告。这不是空 HOME 的 doctor 探测：它使用本机已有 CLI 身份，不复制认证文件，不新建登录/授权，不运行生图。
   - 该探测调用官方 `account/rateLimits/read`；`ordinaryUsageAllowed` 必须明确为 true 才提供 auth/quota 恢复证据，false 表示额度不可用，缺失/null/错误不能判恢复。配置仅让后续心跳可做有界状态请求，不是该账户已验证的收据。
   - 不愿开启账户探测或接口不提供可靠证据：保持 false；操作者确认**原** auth/quota 原因解除后才手动递增顶层 `health_revalidation_generation`（默认 0）一次。不能定时/每次启动递增，也不能把 starter 配置写成非零。该确认只产生短时恢复证据，不能用于绕过真实工具验收。
   - Alchemy 仍要求两个新报告与稳定恢复窗口；报告/操作者声明都不等于生产验证。详见 [Agent 健康与账户探测](CONFIGURED_AGENT.md#健康报告账户状态与显式工具策略)。
6. 最后单独启用“本地繁忙或不可用时自动使用云端”并测试已批准的费用/素材范围。先看当次交付的 [07 设计](../shared-contract/07_可用性与排队自动回退设计.md) 与 [08 实施合同](../shared-contract/08_自动回退代码落地与兼容契约.md)。模块开启≠自动回退开启；暂停/关闭/撤销不能被回退绕过。

自动回退验收必须记录：未开始才可退队；已 start、人工交付或 unknown 不重复生图；云端恢复旧任务不重选路；本地恢复只影响未来新请求。没有真实 Sub2API 授权时仅做 mock 验收，不提供实际付费调用脚本。正常 Generate、正式选图/导出及真实计费是否纳入本次交付，以 Alchemy 最终范围为准，不能由本部署页推断。

## 8. 当前证据与限制

本页的 Bridge/Agent JSON 默认 fake、无秘密。2026-10-21 在 Linux Python 3.12 的现有 Bridge 隔离环境使用实际 RuntimeConfig/AgentConfig 解析通过；doctor 返回 ready/退出 0，同时 login_verified=false、real_generation_verified=false。Compose JSON 解析及回环/不发布 DB 端口/无 latest/必填密码的静态检查通过；此环境无 Docker/PowerShell，未做 Compose CLI 校验、镜像拉取或 PowerShell 实际运行。PowerShell 命令未在用户 Windows 电脑执行。Linux 解析通过不能代替 Windows 启动、Hatchet 持续引擎、用户登录、真实 Codex 图片或 Alchemy 全流程验收。先完成这些本机门禁，不把双 VPS、PostgreSQL 生产迁移或公网 TLS 当作本轮同机测试的前置任务。
