# 实施与验收计划

状态：待实施。本文件计划验证项目，不代表测试已运行或功能已通过。

## 阶段 0：冻结依赖

1. 固定 RenderBridge base commit 和实施分支；不要从当前运行目录重建或覆盖 Git 历史。
2. 固定目标 Sub2API revision，核对 API Key 自定义 `base_url` 的 `/v1/images/generations` 转发、`gpt-image-2` 准入、model mapping、请求头、重试、账户切换、图片计费与响应体限制。
3. 确认稳定幂等身份由客户端提供且经 Sub2API 原样传到 Facade。若不成立，先另提最小 Sub2API 接缝；不在 Facade 猜测或合并重复 prompt。
4. 确认 Sub2API、Facade、VPS Bridge、Windows Node 的实际网络拓扑和 TLS；本地 `127.0.0.1` 不作为远程 Provider 地址。
5. 确认 Codex profile 的 CLI 版本、`approval_policy=on-request`、图像能力验收和当前 profile revision；配置标志本身不证明真实能力。
6. 针对目标 Bridge 部署核实 Source API 的支持状态、角色鉴权、凭据轮换/撤销及 TLS/受信传输。主线 `api.py` 标注该 API 为 experimental，示例认证不是生产凭据管理或 TLS；未补齐部署级安全边界前不得启用 Provider。

任一项缺少证据时保持 Provider 关闭，继续完成不依赖它的离线开发。

## 阶段 1：隔离适配服务

在本功能目录内实现窄 Facade：Provider bearer 鉴权、`GET /v1/models`、严格 `POST /v1/images/generations` 校验、Bridge Source HTTP 客户端、稳定 `origin_key`、同任务轮询和 OpenAI 风格 JSON/错误响应。只依赖公开 Bridge API，不直接导入 Bridge 模块，不访问存储，也不新增队列/数据库。

单独密钥使用部署 secret store 注入，不能写入 Git、配置样例、文档、日志或命令历史。Source/Node/dispatcher 凭据继续分角色。

## 阶段 2：离线与 HTTP 合同验收

使用 fake Bridge/HTTP server，不触发真实 Codex：

- 鉴权缺失/错误、错误模型、空/超长 prompt、`n != 1`、不支持字段、错误内容类型都在创建任务前拒绝。
- 请求映射保持 prompt 原样；固定 binding/profile/adapter/slot/routing；客户端不能覆盖这些值。
- 第一次请求创建一条 Bridge 任务；相同幂等键与同一请求查询同任务；相同键不同请求返回冲突。
- 两个并发相同请求都先 lookup 后 create 时，最多一条任务被受理；另一个 create 的唯一键冲突/不确定响应只能触发同一 `origin_key` lookup，核对 digest 后复用原任务。查询缺失或身份不一致则返回冲突/unknown，不得换 key 或再创建任务。
- create/lookup 响应丢失、轮询超时、断连、unknown、receipt pending、artifact 缺失时不创建第二任务、不返回伪成功。
- 成功路径核对 artifact 原字节、实际 MIME、大小、OpenAI `data[0].b64_json` 响应、receipt ACK 与重放窗口。
- 错误映射保留 OpenAI 风格结构；不泄露内部路径、token、原始异常、prompt 或 base64。
- Sub2API 侧确认模型准入、图像计数和每图结算一次；未知 usage 不填零。

## 阶段 3：Sub2API 假上游联调

先将 Sub2API 的测试 OpenAI API Key account 指向 Facade mock，通过现有 `/v1/images/generations` handler 验证请求头、base URL 拼接、model mapping、请求体、错误透传、图片计数和调用结束后的失败处理。把目标 Sub2API revision、稳定幂等身份的具体字段/请求头、端到端透传记录、超时重试及账号切换行为保存为可审查证据；交接基线测试不能代替这些验证。确认目标版本在超时/错误时不会把 unknown 的 `gpt-image-2` 请求切换账号重新执行。

不能把普通 `/v1/chat/completions` 测试或 `/v1/images/generations/async` 的 Sub2API 自有任务 API当作此 Provider 接口验收。

## 阶段 4：Fake Bridge 端到端

由 Sub2API → Facade → 隔离测试 Bridge → fake-image 完成单图 HTTP 回路。验证关闭/重启 Facade 后同一幂等请求仍定位原 Bridge 任务；Fake 只证明协议、回放和计费接缝，不证明 Codex 登录或生图。

## 阶段 5：真实 Codex 小流量验收

需在正式执行前单独核实账号、额度/费用和素材去向。仅测试一张无敏感 prompt，保持 `on-request`，不复制 Codex 登录文件，不放开自动批准。记录脱敏的 request/task/attempt/receipt 标识、实际模型选择证据、图像事件、真实 MIME/尺寸、HTTP 响应和 Sub2API 计费记录。Unknown 不重跑；停止证据不足时保留占用状态。

## 放量门槛与回滚

发布前须通过：目标 Sub2API revision 精确联调、稳定幂等身份、单张限制、角色鉴权、账本唯一、真实图片原件可读取、超时后同任务恢复、重复请求不重生、图片费用和响应上限相符、TLS/网络隔离。

回滚只需关闭/删除 Sub2API 对该 Facade 的 `gpt-image-2` Provider 路由并停止 Facade 新请求；保留 Bridge 的已受理任务、日志、receipt 和结果供只读对账。不得删除数据库/任务以制造“干净回滚”，也不影响 Alchemy 原有路由。

## 明确未完成

目前没有 Provider 实现、Sub2API 配置、自动化测试、真实 Codex 请求、费用测量、VPS/Windows 网络联通或生产验收。文档审计通过只表示设计范围和接口方案一致，不表示可用。
