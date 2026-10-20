# 可配置 Bridge 服务：单工作区开发版

本增量把固定 happy/unknown fixture 之外的动态任务接入现有源 API、持久账本和 Node HTTP。服务端接受合法的调用方冻结请求，不写 Alchemy，也不读取用户本机目录。

## 已实现

- `python -m render_bridge.runtime --config config/bridge.example.json` 启动独立 FastAPI 服务。
- JSON 配置指定持久数据目录、source/workspace/node/executor 身份、监听地址和端口。相对数据目录以当前工作目录为基准。
- source、node、dispatcher 三套不同 bearer 由指定环境变量提供。程序不生成凭据、创建账户或自动执行 OAuth。仓库样例只有环境变量名称，没有秘密。
- 创建任务和 dispatch outbox 同一 SQLite 事务提交。重复 origin 不创建第二任务/outbox；重开数据库保留任务、收据和待交接记录。
- 独立 dispatcher 将 outbox 中 task_id 交给 Hatchet；HTTP 源 API 和 node API 本身都不执行任务。没有 HTTP 拉取执行队列。
- Node token 固定绑定 source/workspace/node/executor_instance。任务请求不能更换执行身份。
- 通用 node 路径提供查询、claim、start、unknown、已绑定参考图下载、输出上传和提交。
- `fake-image` 是当前允许提交的 profile。Codex/Midjourney 适配器的离线开发不等于其真实 profile 已放行。

## 启动步骤与进程边界

1. 安装仓库的 runtime 依赖；使用新的隔离虚拟环境。启动前由操作者按安装流程提供三套凭据环境变量：`BRIDGE_SOURCE_TOKEN`、`BRIDGE_NODE_TOKEN`、`BRIDGE_DISPATCH_TOKEN`。不要将实际值写入样例或提交 Git。
2. 复制 `config/bridge.example.json` 到本机工作目录，确认持久 `data_dir` 和身份；再执行上面的模块命令。
3. 在另一套固定 Hatchet SDK 解释器中启动 `render_bridge.agent.hatchet_sidecar` 的 dispatcher 和 worker 入口。各入口的参数以该模块 `--help` 为准；服务端不安装或导入 SDK。
4. 来源客户端调用 `/bridge/v1/tasks`。dispatcher 仅提交 `task_id` 给唯一任务 `bridge-node-execute-v1`；Agent 通过 Node HTTP 取得冻结请求及执行许可，再回传产物。
5. 来源客户端查询任务、读取收据和下载产物，核验 hash 后按同 receipt 确认。

默认只绑定 `127.0.0.1:8787`。非 loopback 绑定必须显式配置 `tls_certfile` 和 `tls_keyfile`，客户端不能跳过证书验证。提供 TLS 配置能力不代表已部署、已配置证书或已验收双 VPS 通信。

## 内部 HTTP 合同

所有请求使用对应角色的 bearer，JSON 请求须 `Content-Type: application/json`。

- `GET /bridge/node/v1/tasks/{task_id}`：完整任务视图。
- `POST /bridge/node/v1/tasks/{task_id}/claim`，`{"mode":"execute"}` 或 `{"mode":"upload_only"}`：返回原 claim 视图，开发版租期 3600 秒。
- `POST .../start`、`POST .../unknown`，`{"claim_id":"..."}`。
- `GET .../inputs/{input_id}/content`，`X-Bridge-Claim` header：仅当前任务已绑定的输入。
- `POST .../uploads`，`{"claim_id":"...","payload":{"attempt_id":"...","slot_id":"primary","original_sha256":"...","size":123,"mime_type":"image/png"}}`。
- `PUT /bridge/node/v1/uploads/{upload_id}/content`：媒体字节，带 `X-Bridge-Claim` 和正确媒体类型。
- `POST .../commit`，`{"claim_id":"..."}`：返回收据。
- `GET /bridge/dispatch/v1/pending?limit=10`：返回 `{"items":[{"dispatch_key":"...","task_id":"..."}]}`。这是交接 outbox，不签发执行许可。
- `POST /bridge/dispatch/v1/{dispatch_key}/ack`，`{"engine_run_id":"..."}`：保存已提交的引擎 run ID。

如果引擎已接收但 ack 丢失，同 task 可能再次投递；一次执行依赖已有 start 锁存，而不是声称引擎只收到一次。只运行一个 dispatcher；本版没有自动重置引擎失败/丢失任务的 outbox 管理界面。未启动的取消/过期任务不继续交接，无法确认执行停止的任务保留 unknown，不自动再生成。

## 范围与后续迁移

这是可配置的单机开发后端，SQLite 并未替代设计中的 PostgreSQL 生产路线。仍未完成 PostgreSQL 迁移、持续节点管理/配对、完整故障恢复、真实 Codex/Midjourney 生成、Alchemy 接入和双 VPS 部署验收。固定内存 token 映射是最小受信环境配置，不是完整凭据管理服务。

本次验收聚焦动态 fake 任务主流程及必要身份/重放行为。真实工具登录及图片能力留到本地迁移后，不作为继续编写其他模块的前置条件。
