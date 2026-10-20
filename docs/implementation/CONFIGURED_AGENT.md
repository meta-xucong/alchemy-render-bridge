# 可配置 Node Agent 与 Hatchet 交接侧车

本版接受动态任务 ID，经 Node HTTP 获取冻结请求；仍只启用 `fake-image`。它不是固定 fixture，也不调用真实 Codex、Midjourney 或用户电脑。与 [Bridge 服务配置](CONFIGURED_BRIDGE_RUNTIME.md) 配套。

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

需要 `BRIDGE_NODE_TOKEN`，可加前置 `--url`。这是开发入口，不是 Hatchet 验收，不会扫描 pending 或自行挑任务。成功提交/重放成功退出 0，其他已返回结果退出 2。不同进程不得同时使用同一状态目录执行任务。

## 恢复语义

- OS 文件锁限制同状态目录只有一个执行入口，进程崩溃后由 OS 释放；Windows 使用 `msvcrt`，POSIX 使用 `flock`。
- SQLite journal 保存 task/attempt/claim 和执行阶段，不保存 token，也不是待执行队列。
- 在发 start 请求前持久化 `start_requested`。start 回包不确定、执行异常或已有未知执行均转为 `execution_unknown`，后续投递不会重新生成；未知任务阻止本地其他执行占用物理槽。
- 参考图按冻结顺序经授权 HTTP 下载，校验大小和 SHA-256 后交给执行器。Fake 记录其顺序与 hash，但合成图不声称受参考图视觉条件控制。
- 产物检查后保存在本地并记录 `artifact_ready`，再上传。上传失败保留同一 attempt 和原始字节；claim 过期则申请 `upload_only`，只重传、不重新执行。提交成功但回包丢失时，通过服务端 receipt 恢复。
- 已完成任务再次投递直接返回原 receipt。保留状态和产物目录；本版没有自动清理或 unknown 强制重置命令。不要删除 journal 来“解锁”。

这些是针对关键故障窗口的保守恢复，不代表所有磁盘/断电/网络故障均已验收。当前服务媒体上限 1 MiB，真实工具适配器更大文件的离线能力不改变此上限。

## 验证与未验收项

`python -m unittest discover -s tests -p 'test_agent_*.py' -v` 覆盖动态 fake 完成/重启重放、上传失败和过期 claim 恢复、提交回包丢失、start 回包丢失不再生成、参考图顺序、单实例锁及 outbox 提交/确认。SDK 调用形状按固定官方 wheel 源码核对；sidecar mock 测试不等于新的真实引擎或远程 TLS 验收。

真实 Codex 默认 `needs_attention`，其本地配置/能力验证和 Midjourney 人工导入见 [离线执行器](OFFLINE_EXECUTORS.md)。本 Agent 和服务尚未启用这些 profile，不能把离线 adapter 或 mock 测试称为真实生成成功。用户本地登录、真实生成、生产数据库、双 VPS 部署及远程恢复仍需后续验收。
