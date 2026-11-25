# Sub2API Codex 图片 Provider：设计与接口合同

状态：提案，尚未实现。目标仅为 Sub2API 中一个名为 `gpt-image-2` 的单图生成上游。

## 1. 目标与边界

Sub2API 使用现有 OpenAI Images API Key 上游，将 `/v1/images/generations` 请求发给一个窄适配服务。适配服务把请求映射到 RenderBridge Source API 的图片任务，等待同一任务完成，读取 Bridge 回执和原图，再返回 OpenAI 图片响应。

```text
客户端 → Sub2API /v1/images/generations
       → Codex Image Provider Facade
       → RenderBridge Source API /bridge/v1/*
       → 现有 codex-image Agent / Codex ImageGen
```

Facade 是无队列的协议转换器，不直接启动 Codex，不访问 Bridge 数据库或私有存储，也不导入 RenderBridge Python 对象。RenderBridge 是任务、执行许可、恢复与产物的唯一权威。Alchemy 选路、审核、计费和业务恢复不属于此功能。

公开模型标识 `gpt-image-2` 是 Provider 别名。运行必须固定并核验 `codex-image` binding、profile revision 和 `codex_cli_imagegen_v1` adapter。除非实际执行证据能证明底层模型身份，不得宣称这等同于直接调用 OpenAI Images API 的 `gpt-image-2`。

## 2. 部署与授权边界

- 优先把 Facade 放在 Sub2API 可安全访问、且能通过 HTTPS/受信内网访问 Bridge Source API 的服务侧。若采用 VPS API-pull 拓扑，Bridge 与账本留在 VPS，Windows Node 主动出站连接；不要求向用户电脑开放公网端口。
- 当前本机 Bridge 若只监听 `127.0.0.1`，远端 Sub2API 无法直接访问它。禁止为打通连接而开放 LAN；在具备 TLS 和受控路由前 Provider 保持关闭。
- 该基线 `api.py` 将 Source API 标为 experimental，并明确其示例认证接缝不负责生产凭据管理或 TLS。实现/启用前必须证明目标 Bridge 部署实际提供受支持的 Source API、专用角色鉴权、凭据轮换/撤销和 TLS 或等效受信传输；仅仅部署了这段 API 代码不满足门槛。未满足时 Provider 保持关闭，先补部署边界并单独审计。
- Sub2API 只持有发给 Facade 的独立 Provider API Key。Facade 持有专用 Bridge Source token。Node 和 dispatcher token 不进入 Sub2API 或客户端；Codex 登录文件留在本机 profile。
- Facade 只通过版本化 Bridge HTTP API 通信，不共享数据库、文件目录或 Python 对象。
- Sub2API 用户计费与 Codex 账号实际用量是两件事。首轮设为明确的内部测试组/按图计费策略；无可靠上游 usage 时不填零伪装精确用量。

## 3. Facade 对外合同（v1 最小子集）

### `GET /v1/models`

需 Provider API Key 鉴权。只列出 `gpt-image-2`，且只有启用且配置完整时才对 Sub2API 可见。

### `POST /v1/images/generations`

只接受 JSON。字段约定：

| 字段 | v1 规则 |
| --- | --- |
| `model` | 必须精确为 `gpt-image-2` |
| `prompt` | 必填、非空，最多 16,000 字符；原文传给 Bridge，不改写 |
| `n` | 省略或严格整数 `1`；其他值拒绝 |
| `size` | 省略或 `auto`；Codex 工具没有结构化尺寸参数 |
| `quality` | 省略或 `auto`；其他等级拒绝，不能声称已生效 |
| `response_format` | 省略或 `b64_json`；v1 不生成外部 URL |
| `output_format`、压缩、背景、mask、参考图及其他控制 | v1 拒绝；不能静默丢弃或伪称原生支持 |

未知字段和不支持字段必须在提交 Bridge 任务前返回 OpenAI 风格 4xx 错误。`/v1/images/edits`、批量、流式、多图输出、视频和其他模型均不在 v1 内。

成功响应形状：

```json
{
  "created": 1790000000,
  "data": [{"b64_json": "<original image bytes, base64 encoded>"}]
}
```

`created` 使用同一 Bridge 任务的稳定创建时间；响应不编造 `revised_prompt` 或 usage。Facade 返回 Bridge 保存的原始图像字节，不转换格式、不改扩展名来满足请求。

Bridge 当前 codex adapter 将尺寸/比例、质量、seed、格式和 fidelity 映射到 Codex 工具默认值。图像实际尺寸、格式和执行证据必须据实处理；如果 v1 的 Sub2API 输出格式/大小/超时限制与实际产物不兼容，应在任务执行前拒绝或在验收中阻断启用，不能生成后再偷偷重生或转码。

## 4. Bridge 映射

Facade 对每个合格请求创建以下语义的 Bridge Source 任务：

- `operation`: `image.generate`
- `execution_profile_id`: `codex-image`; `execution_profile_revision`: 从当前 capabilities 读取并固定
- `parameters.codex_adapter`: `codex_cli_imagegen_v1`
- `prompt`: 原始 prompt；`references`: 空数组
- `output_slot`: `{slot_id: "primary", count: 1}`
- `routing`: `bridge_only`
- `binding_id`: 部署配置中固定、非用户可选的 Sub2API Codex image binding
- `origin_key`: 从稳定幂等身份计算的、不含用户秘密的稳定键
- `deadline`: 有界 UTC deadline，与 Sub2API/Facade 超时预算协调

正式编码前应以该 commit 的 capabilities 和 schema 核对必需字段；不得从客户端接受 profile、source、workspace、binding、执行节点或 routing 选择。

Facade 通过 `GET /bridge/v1/tasks/by-origin/{origin_key}` 找回已有任务；已存在时只观察原任务。只在明确未受理时才可提交新任务。网络超时、Sub2API 断连、Bridge unknown 或 Node attention 都不能作为新任务/新图的许可。

并发重复请求可能同时完成 lookup 后再 create。若 create 返回 origin 唯一键冲突、响应丢失或其他不确定结果，Facade 只能用原 `origin_key` 重新查询并核对请求身份/digest，再复用查到的任务；若查不到或核对不一致，返回冲突/unknown 并停止。不得换 key、用新任务补偿或再次调用生成器。具体 Bridge 冲突响应与 lookup 的可见性必须在实现合同测试中按固定 revision 验证。

完成后按 Bridge 已有 task → receipt → artifact content 路径取回结果，核对任务/attempt/slot/receipt 归属、实际媒体类型及既有大小限制。适配层不添加第二套图片账本，不改变 Bridge 现有产物身份或摘要合同。receipt ACK 只按现有 ACK 语义执行；ACK 与向 Sub2API 完整送达的先后顺序、回放窗口必须在实现测试中固定。

## 5. 幂等、超时和失败

- 必须找到 Sub2API 实际传给自定义上游的稳定幂等值，并证明客户端重试时该值保持不变。Facade 使用该值加受信上游范围形成 `origin_key`；相同 key 不同规范化请求返回冲突。
- 必须针对目标 Sub2API revision 记录幂等值所在的请求头/字段、从客户端到 Facade 的传递证据、超时后重试及账号切换行为；交接基线的图片转发测试不等于这些证据。缺任一证据时保持 Provider 关闭。
- 不可用 prompt 哈希替代幂等键：用户可能有意重复生成相同 prompt。
- 若目标 Sub2API 不能稳定传递幂等身份，不能启用可能重试/故障切换的生产路由；先补最小身份接缝并单独审计。
- 请求等待超时后返回可识别的 504/unknown 响应和稳定 request ID，随后同一幂等键只查询同一 Bridge 任务。不能假设 HTTP 超时等于取消。
- 只有 Bridge 明确证明新任务未受理，才允许由调用方重新提交；已开始或状态未知的任务不得自动重生，也不做云端补偿。
- 该模型在 Sub2API 中必须只映射到这一 Provider；Provider 不自行做第二次云端回退。实际 Sub2API 路由/重试/账号切换语义要对照目标部署版本验证。

## 6. 计费、资源与日志

Sub2API 使用自己的现有图片计数/分组定价能力，每个成功结果计一张。价格未配置或无法解释时拒绝公开给普通用户，不能把未知 Codex usage 填为 0。内部操作日志记录 request ID、Bridge task/receipt ID、耗时、HTTP 结果、图片 count 和错误分类；不记录 prompt、图片 base64、授权头或密钥。

Codex profile 仍由现有 Bridge 管理单槽、并发与 on-request 工具权限。API Facade 不增加队列、dispatcher 或独立本地执行槽。
