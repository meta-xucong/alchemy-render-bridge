# Hatchet PoC 门禁与本轮证据

日期：2026-10-20。设计基线：v1.2；本轮读取时仓库基线为
`73dd31209f5b7b85b7f6f4363e795ae8f07cf535`。

## 1. 结论

**RB-01 尚未通过；Hatchet 仍是候选，未确定生产引擎。**

- 本轮实际完成 Linux SDK 安装、导入、依赖一致性检查、官方包/sidecar 校验和核对，以及源码边界检查。
- 当前没有 Docker 不代表 Hatchet 不可用。官方提供不依赖 Docker 的 embedded engine，可用于后续隔离 Linux PoC；它不是内存 Mock。
- 未启动 Hatchet engine、PostgreSQL、worker 或真实渲染器。未创建 API token、服务账号或外部服务账户。
- 发现 SDK `1.42.1` 的明确 Windows 信号处理问题；见第 4 节。这不能被通用 wheel 或 Linux 单元测试掩盖。
- 下面的“检查通过”只指列出的安装/静态检查。没有真实 TLS、节点权限、任务重投、Windows 睡眠恢复、资源基线或端到端运行验收。

**不因缺少 Docker 或一项 SDK 缺陷而自动切换薄 HTTPS 生产队列。**
后续应修复或选择可验证版本，再完成候选引擎门禁；最终保留一个调度权威。

## 2. 精确版本与来源

| 项目 | 本轮固定对象 | 证据与局限 |
|---|---|---|
| Python | CPython `3.12.14`，Linux `6.18.55 x86_64` | 实际执行版本；不是 Windows |
| Python SDK | `hatchet-sdk==1.42.1` | 官方 PyPI wheel，MIT，`Requires-Python: >=3.10,<4` |
| SDK 源码 SHA | `19a3474b7963139124151e7a3b1357b1971c2eb8` | `py/1.42.1`；下载 wheel 中的 worker.py 与该源码逐字节一致 |
| SDK wheel SHA-256 | `da9b111aac64fb678f371b92862f48f475631994ba2340ebea28c94de21a61fd` | 下载内容与 PyPI 发布页面一致 |
| Embedded sidecar | `hatchet-embedded v0.110.5` | 官方发布；下载并校验，未执行 |
| Sidecar 源码 SHA | `3dc26f939dfc5c1072c35dedc65bf51313d665a7` | 固定版本 `go.mod` 依赖 Hatchet `v0.110.5` |
| Linux amd64 sidecar SHA-256 | `18ddacae0005042bd982328bcb8d370cca6907352a1ab2a3c8406001d31e7dee` | 59,072,674 字节，与该发布 `checksums.txt` 一致 |
| 对应 engine 源码 SHA | `56c617e0da399bc9d8a10d2ba74b4950f8a950b8` | Hatchet `v0.110.5`，只读检查 |
| 已安装核心依赖 | `grpcio==1.84.0`、`grpcio-tools==1.81.1`、`protobuf==6.33.6`、`pydantic==2.14.1` | `pip check` 通过；完整解析结果保存在隔离 PoC 目录，不作为项目运行依赖 |

建议下一次 **Linux 隔离试跑候选组合** 为 SDK `1.42.1` + embedded
`v0.110.5`，因为两者均有已核验发布件，且 sidecar 自带示例要求
`hatchet-sdk>=1.38.1`。这个下限是上游声明，**不是本项目已验证的兼容矩阵**。
没有真实任务结果前不得把该组合标记为运行兼容，更不得直接用于 Windows。

同日可见独立 engine 发布 `v0.110.8`；不能假设 embedded 也有同版本发布，
也不能把新 engine 与旧 sidecar 当同一已测版本。本轮不使用 `latest`。

一手来源：

- [PyPI SDK 1.42.1](https://pypi.org/project/hatchet-sdk/1.42.1/)
- [官方 SDK 发布](https://github.com/hatchet-dev/hatchet/releases/tag/py%2F1.42.1)
- [Embedded v0.110.5 发布及校验文件](https://github.com/hatchet-dev/hatchet-embedded/releases/tag/v0.110.5)
- [固定 sidecar go.mod](https://github.com/hatchet-dev/hatchet-embedded/blob/3dc26f939dfc5c1072c35dedc65bf51313d665a7/go.mod)
- [固定 Python 示例依赖](https://github.com/hatchet-dev/hatchet-embedded/blob/3dc26f939dfc5c1072c35dedc65bf51313d665a7/examples/python/requirements.txt)
- [独立 engine v0.110.8 发布](https://github.com/hatchet-dev/hatchet/releases/tag/v0.110.8)

未复制上游实现进入本仓库。下载包保留其许可信息；项目自身许可证仍按既有决定暂缓。

## 3. 本轮执行记录

所有 SDK 安装和只读下载都在独立 `hatchet-phase1-poc` 测试目录；
没有向 Bridge 项目添加 Hatchet 运行依赖，没有修改全局 Python 环境。

实际检查：

```text
python --version
  Python 3.12.14
uname -srmo
  Linux 6.18.55 x86_64 GNU/Linux
command -v docker podman psql postgres hatchet
  上述程序均未发现
```

首次检查也未安装 `hatchet-sdk` 或 `grpcio`。随后执行：

```sh
python -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir \
  --index-url https://pypi.org/simple 'hatchet-sdk==1.42.1'
.venv/bin/python -m pip check
.venv/bin/python -m pip freeze
```

结果：安装成功；`pip check` 输出 `No broken requirements found.`；
导入 `Hatchet`、配置模型、`Worker`、`WorkersClient` 成功。
**没有调用 `Hatchet.from_embedded()` 或创建联网客户端。**

另以明确目标检查 Windows amd64 / CPython 3.12 二进制依赖：

```sh
.venv/bin/python -m pip download --disable-pip-version-check --no-cache-dir \
  --only-binary=:all: --platform win_amd64 --python-version 312 \
  --implementation cp --abi cp312 --index-url https://pypi.org/simple \
  --dest windows-wheel-check 'hatchet-sdk==1.42.1'
```

结果：SDK 及依赖 wheel 全部解析、下载成功。这里只证明该目标有可获取的
二进制包，**不证明导入、worker 初始化、Windows 后台进程或故障恢复能运行**。

隔离证据文件包括 `sdk-linux-freeze.txt`、`sdk-static-check.json`、
`windows-wheel-check.log`、`windows-signal-reproduction.json`、官方 release/tree
元数据、下载校验文件和只读源码。没有包含实际凭据或生产数据。

## 4. Windows 的两项不同限制

### 4.1 Embedded engine 不支持 Windows

SDK `hatchet_sdk/embedded.py` 的 `_sidecar_asset_name()` 仅接受 Linux/macOS
及 amd64/arm64；官方 sidecar 发布也只有这四种组合。
以 Windows 平台参数调用该无联网纯函数得到：

```text
hatchet embedded is not supported on win32/AMD64
```

这是 **Windows 本机 embedded engine** 的限制，不等于 Windows worker
不能出站连接 Linux engine。产品仍应是独立 Linux 服务端与 Windows 节点。

### 4.2 SDK 1.42.1 的 worker 初始化存在 Windows 阻断

下载 wheel 中 `hatchet_sdk/worker/worker.py`：

- `Worker.__init__` 无条件调用 `_setup_signal_handlers()`。
- 该方法无条件读取 `signal.SIGQUIT`。
- Python 官方把 `SIGQUIT` 列为 Unix 专属。

本轮在 Linux 上将该方法的 `signal` 依赖替换为只有 Windows 支持信号的
测试对象，直接调用原 SDK 方法，复现：

```text
AttributeError: 'types.SimpleNamespace' object has no attribute 'SIGQUIT'
```

最小复现（在上述隔离 venv 中；不构造 worker，不联网，也不改变真实信号处理器）：

```python
from types import SimpleNamespace
from unittest.mock import Mock, patch
from hatchet_sdk.worker.worker import Worker

obj = SimpleNamespace(
    _config=SimpleNamespace(force_shutdown_on_shutdown_signal=False),
    _handle_force_quit_signal=lambda *_: None,
    _handle_exit_signal=lambda *_: None,
)
windows_signals = SimpleNamespace(SIGTERM=15, SIGINT=2, signal=Mock())
with patch("hatchet_sdk.worker.worker.signal", windows_signals):
    Worker._setup_signal_handlers(obj)  # AttributeError: SIGQUIT
```

这是源码与隔离方法测试确认的兼容性阻断，**不是 Windows 设备上的运行日志**。
应在锁定版本中获得受审查的平台保护修复，或选择已修复版本，再去 Windows
执行完整 worker 初始化/退出测试。不要通过本地 Monkey Patch 宣称 Windows
验收完成；没有修改上游 SDK 或提交上游 issue。

- [SDK 固定 SHA worker 源码](https://github.com/hatchet-dev/hatchet/blob/19a3474b7963139124151e7a3b1357b1971c2eb8/sdks/python/hatchet_sdk/worker/worker.py)
- [Python 官方 signal 文档](https://docs.python.org/3/library/signal.html#signal.SIGQUIT)

## 5. Embedded 不等于“没有凭据、没有持久状态”

官方文档说无需用户预先配置 token；固定 sidecar 源码实际会：

1. 初始化 PostgreSQL，执行迁移，seed 本地管理员与 tenant。
2. 在数据库生成/保存 JWT keyset，并创建有效期 90 天的 tenant API token 记录。
3. 将连接信息写入临时握手 JSON（`0600`）；SDK 读取后删除握手文件。
4. 把 token 放入相关子进程环境；API 开启时还写入
   `~/.hatchet/profiles.yaml` 的 embedded profile。
5. 正常 shutdown 停止 PostgreSQL，并尝试移除 profile；**不删除数据库、token
   记录、管理员或 keyset**。默认数据库还会跨重启保留。

因此本轮停在启动前，避免越过“不得创建账号/token/持久认证状态”的边界。
要继续，需单独允许创建仅供该隔离测试使用的本地临时认证状态，并明确测试后
销毁。该许可不授权任何云账户、生产 token 或外部服务接入。

[固定 embedded 启动与 token 生成](https://github.com/hatchet-dev/hatchet-embedded/blob/3dc26f939dfc5c1072c35dedc65bf51313d665a7/embed.go)；
[profile 生命周期](https://github.com/hatchet-dev/hatchet-embedded/blob/3dc26f939dfc5c1072c35dedc65bf51313d665a7/profileregistration.go)；
[engine token 存储](https://github.com/hatchet-dev/hatchet/blob/56c617e0da399bc9d8a10d2ba74b4950f8a950b8/pkg/auth/token/token.go)。

### 网络与临时运行边界

| 端点/状态 | 固定源码事实 | 若获准试跑的要求 |
|---|---|---|
| Engine gRPC | embedded 显式绑定 `127.0.0.1`，动态端口；TLS 关闭 | 只允许本机测试，不能证明公网 TLS |
| REST API | engine API 用 `:port` 绑定，默认不局限回环 | `start_api=False`，完全不启动；不能做 REST worker-pause 测试 |
| Engine health | embedded override 关闭 | 保持关闭 |
| Prometheus | 默认关闭；开启时默认 `:9090` | 显式 `SERVER_PROMETHEUS_ENABLED=false` |
| SDK healthcheck | 默认关闭 | 显式关闭 |
| PostgreSQL | 动态端口；沿用 PostgreSQL 默认 localhost，另可能有 Unix socket | 检查真实 listener，不仅相信 URL；只用新建临时数据目录 |
| 认证/配置 | 默认持久数据和可发现 profile | 独立临时 HOME、TMPDIR、`postgres_data_dir`；API 关闭避免 profile；结束后停止所有子进程并销毁该测试新建数据 |

还应清空外部 collector 配置、关闭遥测/安全检查联网，固定二进制与 checksum，
限制 worker `slots=1`、运行时间和总资源。进程结束后验证无遗留监听/进程。
不得更改全局网络、安全设置或接入现有数据库。

SDK 的 `workers.pause/aio_pause` 是 REST 更新 `isPaused`；
worker 优雅退出内部也尝试调用该 REST API。方法存在不证明暂停已生效或无竞态。
不应把 API 关闭的最小 smoke test 当作资源/暂停门禁。

- [固定 API 通配监听源码](https://github.com/hatchet-dev/hatchet/blob/56c617e0da399bc9d8a10d2ba74b4950f8a950b8/api/v1/server/run/run.go)
- [Engine 指标与 health 启动](https://github.com/hatchet-dev/hatchet/blob/56c617e0da399bc9d8a10d2ba74b4950f8a950b8/cmd/hatchet-engine/engine/run.go)
- [PostgreSQL 默认回环监听](https://www.postgresql.org/docs/18/runtime-config-connection.html)
- [官方 embedded 文档](https://docs.hatchet.run/v1/embedded)

## 6. 门禁矩阵：当前状态与必需补证

| 门禁 | 当前状态 | 放行所需证据 |
|---|---|---|
| 官方 SDK 安装、导入、依赖 | 已通过本轮 Linux 检查 | 仅适用于第 2 节实际版本 |
| 发布件与许可来源 | 已核对 SDK/sidecar 来源和 hash | 正式部署还需固定所有镜像 digest、依赖锁、许可记录 |
| Linux engine + SDK 真任务 | 未运行，等待临时认证状态授权 | 启动日志、同一 task 的 dispatch/worker/result、关闭清理 |
| Native Windows worker | 存在已复现源码阻断；运行未测 | 修复/新 SDK 后真实 Windows 构造、spawn、退出、重启 |
| 真 TLS | 未测；embedded 强制无 TLS | 固定服务地址；合法证书成功；错误 CA/主机名、过期证书失败；断线后安全重连 |
| 最小节点权限、撤销 | 未测；普通 tenant token 不等于节点级权限 | 节点无法跨任务/节点取数据、触发任意工作流或管理其他 worker；撤销阻止新授权且保留已执行事实 |
| 100 次重投与 unknown | 未接真实 engine | 同 attempt 重投 100 次外部 start 计数为 1；重派/超时/DB 丢失都不清除 start 锁存 |
| 物理槽、暂停、无第二队列 | 只有 SDK API/源码证据 | SDK callback 已退出而物理执行未知时，下一次执行仍被拒绝；暂停竞态、断网及重启均测 |
| Windows 睡眠/进程崩溃/磁盘不足 | 未测 | 真实设备和受控注入日志；unknown 保持 fail-closed；原文件可恢复上传 |
| 资源与容量 | 未测 | 单 worker/单槽，记录 idle/忙时 RSS、CPU、磁盘；有界队列、日志与重试；压力下拒绝可解释 |
| 独立 VPS 与 Alchemy 联调 | 未测 | 两端各自版本、HTTPS API、无共享数据库/存储，原路径回归 |

Hatchet 官方说明为 at-least-once 执行；重投必须由 Bridge 的 start-grant
与持久 attempt 状态约束，不能靠引擎运行成功或 `retries=0` 推导“不会重复生成”。
[官方架构与保证](https://docs.hatchet.run/v1/architecture-and-guarantees)。

## 7. 引擎无关 SQLite / FakeRenderer 实验的范围

本轮可以继续：共享 schema/golden fixtures、模拟 source 调用、单工作区/单节点
的账本事务、start 锁存、重复提交、unknown、上传/receipt/ack 与故障注入测试。
这些属于引擎选择前可保留的合同和安全不变量实验。

必须同时保持：

- 明确标记 experimental/engine-independent；SQLite 不被宣布为生产服务端存储。
- 测试人工注入 dispatch/claim，不实现后台轮询、定时重投、节点择优或生产派单。
- 不给实验状态机同时接上两套调度器；outbox 只表达交接意图。
- FakeRenderer 只生成固定测试文件，不调用真实模型、Codex、Midjourney 或收费服务。
- 本地测试成功不得填写 Hatchet、TLS、Windows 或 Alchemy 联调“PASS”。
- 不修改 Alchemy 仓库、计费、审核、原队列或默认 API；不提交、推送、部署。

这允许开发准备继续，但不会绕过 RB-01。需要真实服务/节点行为的验收继续保持
阻断，直到对应证据齐备。
