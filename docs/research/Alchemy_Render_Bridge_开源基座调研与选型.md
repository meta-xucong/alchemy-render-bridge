# Alchemy Render Bridge：开源基座调研与选型

日期：2026-10-20（北京时间）。范围：公开互联网、官方仓库/许可证/关键源码；未安装、运行、生成媒体、提交任务或部署。此报告不能替代选定版本的安全审计和真实环境测试。

**按用户最新要求：先完成Alchemy与本地Bridge之间的通用链路，图像/视频平台逐个后接。只把有明确开源许可的组件列为可吸收候选。**

> 公开仓库版本说明：开源调查事实沿用原报告；实施责任以分端开发方案v1.1为准，第6节已同步校正。该报告不授权修改Alchemy原有核心流程。

## 1. 直接结论

**有，而且已有不少相近实现。不需要从零重造全部基础设施。**

但本次没有找到一个已经完整覆盖“Alchemy业务身份 + 私有本地节点 + Codex/ComfyUI/人工工具 + 安全回退 + 原审核交付”的单仓库。找到的是三类有用基座：

1. **通用桥控制面：Hatchet优先验证，Temporal备选。** 已有持久任务和本地worker连接机制，最直接减少我们自己写任务调度、心跳和重派的工作。
2. **真正相似的生图worker：AI Horde/reGen。** 证明“云任务→本地领活→上传结果”的模式已有实际实现；主要借鉴它的pop/submit与能力匹配，不把公共算力社区整体搬进Alchemy。
3. **工具接入：codex-native-bridge、Comfy官方API Proxy、部分MIT/Apache组件。** 有实际源码可用，后续按平台裁剪接入，不让它们决定第一阶段桥接架构。

**推荐组合：Alchemy薄业务适配 + Hatchet候选控制面/本地SDK Worker + 自有小型产物/恢复合同。第一阶段只接FakeRenderer；真实平台Adapter后置。**

这里的“候选”很重要：Hatchet要新增自托管服务和PostgreSQL，Windows节点、认证隔离、故障重派与资源占用尚未实测。如果这些代价不合适，就退回单服务HTTPS取任务方案，不叠加多个编排平台。

## 2. 调研方法与证据等级

覆盖四类项目：通用任务引擎、图像/传统渲染worker、ComfyUI服务化、Agent本地生图桥；筛查了20余个项目或组件。重点项目核对README、LICENSE、默认分支/固定commit及相关任务/产物源码；其他项目只作为初筛或排除项。

- **已核实：** 文档或源码明确存在的能力、许可证和网络方向。
- **推论/建议：** 与本产品需求比较后得到的适配判断。
- **未验证：** 用户Windows安装、真实任务、性能、长期稳定、权限配置和供应商账号能力。

不把stars、最近一次push、仓库有tests目录或作者的“production-ready”当验收。默认分支最近commit与其他分支push时间分别记录，不能混用；本次未做穷尽所有issue/全仓漏洞审计。

## 3. 最适合我们借用的项目

### 3.1 Hatchet：通用桥第一候选

仓库：[hatchet-dev/hatchet](https://github.com/hatchet-dev/hatchet)
许可证：[MIT](https://github.com/hatchet-dev/hatchet/blob/1df922962308302decff6f8a10eb0888fed7afbf/LICENSE)
本次默认分支快照：`1df922962308302decff6f8a10eb0888fed7afbf`，commit时间2026-10-19 20:17:31 UTC；正式PoC仍应选定稳定发布与相应SDK版本。
部署：[官方自托管说明](https://docs.hatchet.run/self-hosting)

**已核实能力：** 自托管任务服务、SDK worker、持久执行记录、任务路由与并发；worker主动建立gRPC长连接。可以注册普通task，不必一开始写复杂durable workflow。服务端仍要运行，PostgreSQL是持久基础；RabbitMQ可选，不等于仅装SDK就能使用。

**我们借什么：** 任务投递、worker连接/心跳、基础调度、可观察运行记录。用本地SDK执行一个受信函数，函数内部再调用未来的RenderExecutor。

**我们仍要写什么：** Alchemy用户/模块/结果槽绑定、设备配对与最小授权、产物暂存/摘要/回执、原业务幂等登记、unknown状态保护，以及本地未上传文件恢复。

**关键风险：** worker失联会发生重派，不能让第二次函数执行直接触发第二次生成；worker必须先读取当前业务attempt并判断是否允许执行。队列/label不是鉴权。自托管TLS、worker凭证实际权限、Windows睡眠恢复和资源开销需做最小PoC，未通过前不进入生产依赖清单。

**判断：** 最符合“先把桥搭好、渲染软件后接”的开源方向。优先组合使用SDK/服务，不先大改上游核心。

### 3.2 AI Horde + reGen：最相似的成品模式

仓库：[AI-Horde](https://github.com/Haidra-Org/AI-Horde) / [horde-worker-reGen](https://github.com/Haidra-Org/horde-worker-reGen)
许可：AGPL v3系列，需按选定文件确认；本次固定服务端`420f56a7…`、worker`3963ae36…`。

**已核实：** 服务端异步接收生成请求，worker主动pop任务，生成后主动PUT产物并submit；服务端有身份核对和原子完成处理。配置可指向自建Horde地址，不一定加入公共网络。[集成协议](https://github.com/Haidra-Org/AI-Horde/blob/420f56a7b61585025259c494cab43065faab50fd/README_integration.md) · [原子完成源码](https://github.com/Haidra-Org/AI-Horde/blob/420f56a7b61585025259c494cab43065faab50fd/horde/classes/base/processing_generation.py#L198-L245)

**最值得吸收：** claim/pop节流、能力匹配、执行分配身份、上传后submit、重复完成处理。

**不整体fork的原因：** 带公共社区/kudos、hordelib推理和自身身份模型；公开接口的request ID可读取结果语义不适合直接照搬私有Alchemy。reGen核心待处理/待上传队列是内存对象，不能因此宣称断电后能恢复同一产物上传。[worker队列源码](https://github.com/Haidra-Org/horde-worker-reGen/blob/3963ae36839ea6765350abd6dfe0f1a78810acec/horde_worker_regen/process_management/process_manager.py#L1320-L1400)

**判断：** 架构参考很强，源码复用须接受对应许可义务；第一阶段不需要搬进其模型系统或公共算力平台。

### 3.3 codex-native-bridge：可借本地执行骨架

仓库：[PatrickStar-sketch/codex-native-bridge](https://github.com/PatrickStar-sketch/codex-native-bridge)
许可：[MIT](https://github.com/PatrickStar-sketch/codex-native-bridge/blob/04b6d9d0d9d95117be4cfd400cedf9b4358dcaa7/LICENSE)
固定版本：`04b6d9d0d9d95117be4cfd400cedf9b4358dcaa7`，默认分支最近提交2026-08-10，package版本0.1.0；Windows明确未测试。

它是真正的第三方开源Bridge，并非OpenAI官方产品。源码确实以stdio启动codex app-server，有后台worker、本地原子JSON任务记录，并从imageGeneration事件收集savedPath/base64原字节；不是只解析一句“图片已生成”。[app-server客户端](https://github.com/PatrickStar-sketch/codex-native-bridge/blob/04b6d9d0d9d95117be4cfd400cedf9b4358dcaa7/src/app-server-client.mjs) · [worker](https://github.com/PatrickStar-sketch/codex-native-bridge/blob/04b6d9d0d9d95117be4cfd400cedf9b4358dcaa7/src/worker.mjs)

**可借：** 子进程协议封装、任务事件关联和原文件收集器。第一阶段最多借本地worker持久记录的组织方式，真实CodexAdapter后置。

**不能照搬：** 参考图主要靠文字要求Codex使用路径，没有强制使用证明；中断后reply创建新task，不是原image attempt恢复；cancel先标取消再杀进程，不能证明上游已停。未见远端主动领取、Alchemy租户绑定与上传登记闭环。不要搬入Room/多Agent全部UI或权限模式。

### 3.4 Comfy官方API Proxy：后续ComfyUI适配优先候选

仓库：[Comfy-Org/comfy-api-proxy](https://github.com/Comfy-Org/comfy-api-proxy)
许可：[MIT](https://github.com/Comfy-Org/comfy-api-proxy/blob/1569dc17ec0dd293d4b9a0723e142204dacedb85/LICENSE)
固定版本：`1569dc17ec0dd293d4b9a0723e142204dacedb85`，默认分支最近commit2026-08-27。官方组织维护的新项目，不等于长期生产稳定已证明。

有本地v2 jobs、轮询、取消、SSE、assets上传下载与可选SQLite索引，默认回环监听。它包装ComfyUI，不要求使用Comfy Cloud；Python SDK也可复用。

**关键源码差距：** 重复Idempotency-Key返回422，不是取回原job；先提交ComfyUI再保存job，存在提交成功但本地记录不足的窗口，key还有限量保留。SQLite不能让ComfyUI丢失的history恢复。[提交路径](https://github.com/Comfy-Org/comfy-api-proxy/blob/1569dc17ec0dd293d4b9a0723e142204dacedb85/src/comfy_api_proxy/app.py#L732-L812) · [恢复说明](https://github.com/Comfy-Org/comfy-api-proxy/blob/1569dc17ec0dd293d4b9a0723e142204dacedb85/docs/batch-workloads.md)

**判断：** 适合后续放在本地Bridge后面，只复用工具API；不能顶替通用桥。若补丁比直接原生API更重，就不用这一层。

### 3.5 ComfyUI-Distributed：恢复逻辑参考，不是首版控制面

仓库：[robertvoy/ComfyUI-Distributed](https://github.com/robertvoy/ComfyUI-Distributed)，固定`fef70b7d9407bc43538a22993c8d28c31b96b559`，默认分支commit为2026-10-14。[Apache-2.0许可](https://github.com/robertvoy/ComfyUI-Distributed/blob/fef70b7d9407bc43538a22993c8d28c31b96b559/LICENSE)。其近期Runpod serverless路径已有jobs.json、提交无ID进入UNKNOWN、取消请求不等于完成、未决任务占容量等细节，可作为恢复测试的参考。

参考的具体代码是[serverless_ledger.py](https://github.com/robertvoy/ComfyUI-Distributed/blob/fef70b7d9407bc43538a22993c8d28c31b96b559/api/orchestration/serverless_ledger.py)与[恢复状态机](https://github.com/robertvoy/ComfyUI-Distributed/blob/fef70b7d9407bc43538a22993c8d28c31b96b559/api/orchestration/serverless_state.py)。但普通多机模式仍由master访问worker，Runpod模式又依赖托管平台；不能当作已满足“家用PC主动连接Alchemy”的完整基座。不要为了复用这部分恢复逻辑同时引入Runpod依赖。

## 4. 其余候选为何不选为桥接首版

| 项目 | 开源/许可情况 | 适合哪里 | 当前不做主基座的原因 |
|---|---|---|---|
| Temporal | MIT服务端/SDK | 可靠长时workflow/activity、出站worker | 需要独立服务及运维；可作Hatchet备选，不叠加 |
| Salad comfyui-api | MIT | workflow输入输出、对象存储等组件 | stateless，上传前删除原文件；不是恢复账本 |
| SwarmUI | MIT | 本地多模型/多GPU前端与后端 | 远端worker要可达，原生产图会改写/处理history，不是反向领活 |
| ComfyDeploy | GPL前后端已公开，插件另有AGPL | 完整Comfy服务化平台 | 平台重，classic节点仍需可达endpoint；非轻桌面桥 |
| RunPod worker-comfyui | AGPL worker | 特定云平台Adapter | worker开源不代表RunPod平台控制面也可fork自托管 |
| Blender Flamenco | GPLv3+ | 传统渲染农场worker生命周期 | 共享存储和Blender/命令型任务假设，WAN媒体回传要大改 |
| Hidden Switch ComfyUI fork | GPL系列 | RabbitMQ消费式分布执行 | 引擎fork/共享目录耦合，队列参数不能直接推断可靠恢复 |
| LocalAI | MIT | 未来可选本地多模态推理后端 | 分布模式需Postgres/NATS及可回连worker，非轻量桥 |
| GPUStack | Apache-2.0 | 集中GPU模型服务 | 控制面需访问worker；集群管理超出首版 |
| Windmill | 开源主体AGPL/部分Apache，EE不同 | 通用内部自动化平台 | 所需HTTP-only Agent Worker是EE，不能当免费开源能力吸收 |
| Kestra | OSS Apache-2.0，EE另算 | 企业工作流 | 所查文档TLS/mTLS/JWT worker鉴权为EE，OSS链路限制不合默认公网连接 |
| Trigger.dev | Apache-2.0 | 容器后台任务 | 自托管无checkpoints，Docker supervisor/runners重，非原生桌面小Agent |
| Bortlesboat/comfyui-bridge | MIT | 本地Comfy调用示例 | LAN push，默认可增强Prompt，不适合原样继承冻结任务 |
| sandyup/comfyui-mcp | MIT | 本地Agent/MCP工具 | 跨网采用隧道，缺通用业务task/receipt；仅初筛 |

**明确排除可复制基座清单：** ethanfel/ComfyUI-Agent-Bridge、ai-dock/comfyui-api-wrapper、Tiffyn/comfyui-distributed，本次未核实明确许可证。可读其公开介绍理解思路，但不能因公开GitHub就当获准复制/分发。Tiffyn所述CLI参数也未在所查官方ComfyUI实现中确认。

官方许可边界参考：[GitHub关于仓库许可证](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)。MIT/Apache需保留声明等条件；GPL/AGPL是真正开源，但复制或组合后的义务须按具体方式确认。本报告不作“闭源商业使用一定无条件安全”的法律承诺。

## 5. 我们到底自己做多少

### 不应重造

- 已有任务引擎能提供的可靠派发、worker连接和基础调度。
- 现成SDK的协议序列化、流式事件和工具HTTP客户端。
- 现成存储/哈希/数据库库；但使用库不等于免除业务事务设计。
- 已有Alchemy审核、用户权限、结果库、历史与计费。

### 必须由本项目掌握

1. Alchemy测试/业务Job如何变成Bridge任务，如何回到原模块。
2. 谁能把任务派给哪台电脑；相应素材授权和设备撤销。
3. Task/Attempt/Slot与引擎run ID、工具job ID的对应关系。
4. 未知执行时不自动重复生成；旧attempt不能覆盖当前结果。
5. 原文件暂存、commit receipt与业务登记幂等。
6. 后续各平台声明能力，不能静默换模型、丢参考图或改Prompt。

这六项是我们产品的领域边界，不是把别人的HTTP端点改个名字即可消除的工作。代码量和工期暂不给无依据百分比；用首个隔离闭环测量后估算。

## 6. 第一阶段方案（已按分端开发方案v1.1校正）

### 推荐组合

Alchemy可选接入模块 → HTTPS API → 独立Render Bridge/Hatchet候选任务引擎 → 本地SDK Worker → FakeRenderer → Bridge私有暂存/commit → Alchemy模块经API取回并调用已有受支持的结果导入入口。

- 本地无公网入站；向自托管受信端点发起TLS连接。
- 媒体不塞task payload，走限权HTTP传输。
- 任务引擎只作唯一调度器；Alchemy模块保留业务绑定，独立Bridge保留执行/提交资格。业务task与持久投递意图以稳定dispatch_key对接引擎，丢响应先查/恢复同一投递；领取后原子校验当前attempt，重复投递不新建生成attempt。outbox只保证交接，不另选节点/重排任务。
- worker被重派时先核对attempt，不以SDK自动retry直接重做外部生成。
- 第一阶段不安装真实模型；FakeRenderer生成固定合法测试文件，可模拟slow/failure/unknown/upload-loss，全部在隔离Job/资产中运行。
- 如果候选引擎不能以可接受资源和权限做到这些，则采用单服务HTTPS pull退路，不同时引入第二个编排平台。

### 三个可验收交付

**A. 接入合同：** 固定一个Alchemy入口、任务Schema、状态机、授权/错误码、原生结果映射。

**B. 端到端桥：** 测试Job在Alchemy创建，本地节点领取并返回fixture，服务端只登记一次，页面正确显示测试状态。

**C. 故障闭环：** 断线/重启、重复投递、取消、过期claim、提交响应丢失、上传响应丢失、登记失败恢复、跨用户拒绝全部有最小测试证据。

A/B/C通过后再接ComfyUI或Codex，顺序由产品需要决定。这才是“先把桥接Alchemy做好”，而不是先做一堆生图工具演示再补平台可靠性。

## 7. 决策建议

- 同意进入下一步：仅确定通用桥合同与Hatchet隔离PoC，不立即fork所有候选。
- 首版优先MIT/Apache组件；AGPL/GPL项目先做结构参考，需要源码复用时再明确许可决策。
- 保留原API Provider，Bridge默认不全局启用。
- 暂不实现完整多节点调度、插件市场、第三方账号自动化和真实视频工作流。
- 公开研究已证明有可复用基础；尚未证明选定组合在用户电脑可运行。下一步目标是用最小证据选定一个底座，而非扩大研究范围。

## 附录：扩展候选的一手入口与排除依据

- [Temporal源码](https://github.com/temporalio/temporal)；[Worker文档](https://docs.temporal.io/workers)。
- [Hatchet worker](https://docs.hatchet.run/v1/workers)、[at-least-once与架构](https://docs.hatchet.run/v1/architecture-and-guarantees)、[心跳与重派FAQ](https://docs.hatchet.run/v1/faq)、[PostgreSQL-only自托管](https://docs.hatchet.run/self-hosting/docker-compose)。
- [Salad wrapper](https://github.com/SaladTechnologies/comfyui-api/blob/0727370b9202bcba7a790466d2d0fa51cc7156bd/README.md)；[上传前删除源码](https://github.com/SaladTechnologies/comfyui-api/blob/0727370b9202bcba7a790466d2d0fa51cc7156bd/src/server.ts#L293-L362)。
- [SwarmUI](https://github.com/mcmonkeyprojects/SwarmUI/tree/b5d28088f20b0703155d9bb57fa41a39bb62c8fe)（本体MIT）；[ComfyDeploy平台](https://github.com/comfy-deploy/comfydeploy)、[API](https://github.com/comfy-deploy/api)、[前端](https://github.com/comfy-deploy/app)（需分别核对license和子模块pin）。
- [RunPod worker](https://github.com/runpod-workers/worker-comfyui/tree/b7574c8fc7659cae73473140ff58aaec4d585046)（worker AGPL不等于平台开源）。
- [Flamenco官方项目](https://projects.blender.org/studio/flamenco)、[官网](https://flamenco.blender.org/)；本轮直接访问部分返回403/402，补查官方检索与staging镜像，证据完整度低于其他固定文件直读，未推荐为首选。
- [Hidden Switch fork](https://github.com/hiddenswitch/pip-and-uv-installable-ComfyUI/tree/b3338bf16cd9f8b848eb337f4c247fbce69d3e2b)。
- [LocalAI分布模式](https://github.com/mudler/LocalAI/blob/master/docs/content/features/distributed-mode.md)；[GPUStack worker网络要求](https://github.com/gpustack/gpustack/blob/main/docs/user-guide/cluster-management.md)。
- [Windmill Agent Worker的EE边界](https://www.windmill.dev/docs/core_concepts/agent_workers)、[混合许可证](https://github.com/windmill-labs/windmill/blob/main/LICENSE)。
- [Kestra当前worker传输与版本边界](https://kestra.io/docs/architecture/server-components)。
- [Trigger.dev自托管范围](https://github.com/triggerdotdev/trigger.dev/blob/main/docs/self-hosting/overview.mdx)、[worker容器部署](https://github.com/triggerdotdev/trigger.dev/blob/main/hosting/docker/worker/docker-compose.yml)。
- [Bortlesboat bridge](https://github.com/Bortlesboat/comfyui-bridge)、[其默认Prompt增强源码](https://github.com/Bortlesboat/comfyui-bridge/blob/main/server/bridge_server.py)；[sandyup MCP](https://github.com/sandyup/comfyui-mcp)。
- 未纳入可复用清单的公开项目：[ethanfel](https://github.com/ethanfel/ComfyUI-Agent-Bridge)、[ai-dock](https://github.com/ai-dock/comfyui-api-wrapper)、[Tiffyn](https://github.com/Tiffyn/comfyui-distributed)。

有明确license的候选也只表示存在授权文本；逐文件、依赖、模型权重和分发组合仍需在实际吸收代码前复核。没有将任何商业EE功能写成可以从开源仓库直接复制的免费能力。
