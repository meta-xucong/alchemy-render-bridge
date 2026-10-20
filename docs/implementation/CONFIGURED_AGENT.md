# 可配置 Node Agent 与 Hatchet 交接侧车

本版接受动态任务 ID，经 Node HTTP 获取冻结请求；默认只启用 `fake-image`，可通过显式本地配置选择其他固定 profile。它不是固定 fixture，也不调用真实 Codex、Midjourney 或用户电脑。与 [Bridge 服务配置](CONFIGURED_BRIDGE_RUNTIME.md) 配套。

## 进程与配置

- Bridge 服务独立解释器运行 FastAPI，默认 `http://127.0.0.1:8787`。
- Agent 使用 `httpx`、Pillow 和 Python 标准库。凭据仅从已有环境变量读取，不生成授权，不保存在 journal，不作为命令行参数。
- SDK 侧车使用独立解释器，沿用已有固定依赖锁：Linux 官方 `hatchet-sdk==1.42.1`，Windows 已验证候选 `1.42.1+bridgewin1`。不要升级 SDK 或把 FastAPI 依赖混进 SDK 环境；在该环境安装 Bridge 包时使用 `--no-deps`，并确保 `httpx` 和 Pillow 可用。固定候选安装流程见 [实验目录](../../experiments/hatchet-windows-compat/README.md)，既有真实引擎验证见 [验证记录](WSL_NATIVE_ENGINE_VALIDATION.md)。
- 需要操作者预先提供 `HATCHET_CLIENT_TOKEN`、`HATCHET_CLIENT_HOST_PORT`、`HATCHET_CLIENT_SERVER_URL`。默认 `HATCHET_CLIENT_TLS_STRATEGY=tls`；只有回环开发环境可显式设为 `none`。程序不跳过证书验证。
- `BRIDGE_URL` 可覆盖默认地址。远程 Bridge 必须 HTTPS；仅回环允许 HTTP。`--state-dir` 指定本机持久目录，不能放到网络共享目录；各次启动必须复用同一节点目录。

### 正常 Hatchet 入口

由已配置凭据的终端分别启动下列进程。dispatcher 只需 `BRIDGE_DISPATCH_TOKEN`；worker 只需 `BRIDGE_NODE_TOKEN`，两者不是 source token。

```sh
python -m render_bridge.agent.hatchet_sidecar dispatcher
python -m render_bridge.agent.hatchet_sidecar worker --state-dir ./local-agent-state
```

只运行一个 dispatcher。它读取事务 outbox，将 `task_id` 提交给 Hatchet 的 `bridge-node-execute-v1`，拿到 run ID 后确认交接；它不执行图片任务，也不创造第二套执行调度。可以用 `dispatcher --once` 做一次交接检查。未知提交结果不 ack，允许重新投递同一任务 ID；这不是 exactly-once 投递声明。

worker 只有一个 slot。SDK 回调只收到 `task_id`，Agent 再经 HTTP 读取任务。Node token 在服务端固定绑定 source/workspace/node/executor_instance，客户端不能通过请求切换身份。上传待恢复或物理槽忙由 Hatchet 有界重试；耗尽后需操作者处理，不新增本地自动队列。

### 明确的开发手动入口

不启动 Hatchet 时，可以单次交付一个已有任务 ID：

```sh
python -m render_bridge.agent --state-dir ./local-agent-state run-task TASK_ID
```

需要 `BRIDGE_NODE_TOKEN`，可加前置 `--url`。这是开发入口，不是 Hatchet 验收，不会扫描 pending 或自行挑任务。成功提交/重放成功或已建立人工待办退出 0，其他已返回结果退出 2。不同进程不得同时使用同一状态目录执行任务。

## 恢复语义

- OS 文件锁限制同状态目录只有一个执行入口，进程崩溃后由 OS 释放；Windows 使用 `msvcrt`，POSIX 使用 `flock`。
- SQLite journal 保存 task/attempt/claim 和执行阶段，不保存 token，也不是待执行队列。
- 在发 start 请求前持久化 `start_requested`。start 回包不确定、执行异常或已有未知执行均转为 `execution_unknown`，后续投递不会重新生成；未知任务阻止本地其他执行占用物理槽。
- 参考图按冻结顺序经授权 HTTP 下载，校验大小和 SHA-256 后交给执行器。Fake 记录其顺序与 hash，但合成图不声称受参考图视觉条件控制。
- 产物检查后保存在本地并记录 `artifact_ready`，再上传。上传失败保留同一 attempt 和原始字节；claim 过期则申请 `upload_only`，只重传、不重新执行。提交成功但回包丢失时，通过服务端 receipt 恢复。
- 已完成任务再次投递直接返回原 receipt。保留状态和产物目录；本版没有自动清理或 unknown 强制重置命令。不要删除 journal 来“解锁”。

这些是针对关键故障窗口的保守恢复，不代表所有磁盘/断电/网络故障均已验收。输出限制由共享 profile registry 决定：Fake 为 1 MiB / 单边 1024；Codex 与人工 MJ 为 16 MiB / 40 MP。输入参考图仍最多 1 MiB，顺序和 hash 不变。

## 验证与未验收项

`python -m unittest discover -s tests -p 'test_agent_*.py' -v` 覆盖动态 fake 完成/重启重放、上传失败和过期 claim 恢复、提交回包丢失、start 回包丢失不再生成、参考图顺序、单实例锁及 outbox 提交/确认。SDK 调用形状按固定官方 wheel 源码核对；sidecar mock 测试不等于新的真实引擎或远程 TLS 验收。

真实 Codex 默认 `needs_attention`，其本地配置/能力验证和 Midjourney 人工导入见 [离线执行器](OFFLINE_EXECUTORS.md)。Agent 和服务现在都支持显式启用这些 profile，但默认仍只有 Fake；不能把路由和 mock 测试称为真实生成成功。用户本地登录、真实生成、生产数据库、双 VPS 部署及远程恢复仍需后续验收。

## 显式 profile 配置与人工闭环

复制 `config/agent.example.json` 到本地私有配置，tokens 仍只用环境变量。Agent 的 `enabled_profiles` 与 Bridge 的配置需一致；任务不能自行指定 CLI 路径、模型或 acceptance flags。

```sh
python -m render_bridge.agent --state-dir ./local-agent-state --config ./agent.local.json run-task TASK_ID
python -m render_bridge.agent.hatchet_sidecar worker --state-dir ./local-agent-state --config ./agent.local.json
```

- 默认配置不启用 Codex 或人工 MJ。加入 `codex-image` 后，还必须在本地完成真实能力验证并显式配置 `codex.enabled`、`model`、`image_capability_verified`、`tool_policy_verified`。Agent 在 server start 前只做版本/help preflight；不通过返回 needs_attention，不占物理执行槽。真正 thread/turn 生命周期仍受本地配置控制，本轮仅 mock 验证。
- 加入 `midjourney-manual` 后，任务导出 prompt.txt/task.json，服务通过 manual-ready 建立同 attempt 的 awaiting_user。返回 request_sha256 与导出文件路径，Hatchet 回调即结束，人工等待不占自动物理槽。不发送 Discord 或调用 /imagine。人工参考图流程尚未验证，带参考图的人工任务返回 needs_attention。
- 每个真实 profile 请求必须显式给 width/height；原图需与冻结尺寸一致。用户自行在 Midjourney 生成，并明确选定一张原图，不是网格截图。使用导出时返回的 request_sha256：

```sh
python -m render_bridge.agent --state-dir ./local-agent-state --config ./agent.local.json manual-import TASK_ID /path/to/original.png --request-sha256 HASH --confirm-single-original
```

重启必须复用 state-dir 与配置，journal 恢复同 attempt，不能新建执行。导入先检查服务状态和 attempt；取消/过期拒绝，已有 receipt 返回原 receipt。上传失败仅重传原始字节，claim 过期可用 upload_only。并发取消导致服务拒绝 commit 时返回 not_executable，不把本地导入误报为交付成功。人工来源仅为用户声明，并非自动证明来自 Midjourney。
