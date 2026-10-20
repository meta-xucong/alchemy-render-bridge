# Render Bridge 独立程序开发方案 v1.1

日期2026-10-20。责任范围仅Render Bridge仓库。Alchemy侧改动见另一份文件。

## 1. 产品及对外边界

一个独立的异步渲染服务，提供创建任务、查询状态、取消、读取产物和确认已接收等API。本地Agent主动出站连接，执行批准的Executor。服务端不需要Alchemy源码、数据库账户、媒体目录或账单接口。

只接受已认证Alchemy接入模块提交的冻结合同；不重新生成创意Prompt，不推断业务owner，不代替审核。对其他客户也可复用协议，但首版只支持一个可信工作区。

## 2. 组件

- `bridge_api`：来源应用与workspace范围校验、任务账本、能力目录、状态/收据API。
- `engine_adapter`：Hatchet普通任务与SDK封装，唯一执行调度器。先PoC，失败后单次选择薄HTTPS拉取替代，不并存。
- `artifact_store`：私有暂存、流式输入输出、ready/commit、保留与清理。
- `node_agent`：Windows单用户后台/CLI、SQLite恢复、单实例锁、资源槽、断线对账。
- `executors`：统一接口，首版FakeRenderer，后续Comfy/Codex/人工导入。
- `contracts`：共享API固定副本或发布schema，不依赖Alchemy内部类型。

推荐FastAPI/PostgreSQL/Python Agent，服务自己的DB角色和私有存储。Hatchet内部DB与Bridge逻辑表分离；即使同实例也不能让Alchemy读取引擎表。节点凭据、源应用凭据与上传权限分离。

## 3. 开源采用方式

Hatchet（MIT）组合部署并用SDK；AI Horde/reGen（AGPL）只参考任务领取/结果确认模式，不复制代码；Comfy官方API Proxy（MIT）与codex-native-bridge（MIT）后接，只复用工具接口/原文件收集。固定版本、许可证、NOTICE和补丁记录；不把多个后端队列拼成多个调度权威。

来源：https://github.com/hatchet-dev/hatchet 、https://github.com/Haidra-Org/AI-Horde 、https://github.com/Haidra-Org/horde-worker-reGen 、https://github.com/Comfy-Org/comfy-api-proxy 、https://github.com/PatrickStar-sketch/codex-native-bridge 。固定调研SHA与局限见此前选型报告；实施前固定兼容发布版本。

## 4. 自己的数据及状态

表：source/workspace授权、nodes、profiles、tasks、slots、attempts、dispatch_outbox、uploads、artifact_receipts、delivery_acknowledgements、审计事件。

Alchemy传来的`binding_id`仅作为不透明关联，Bridge不解析candidate/job内部格式，不以它越权访问原业务。

Task从dispatch_pending/queued/active到artifacts_ready，再到delivery_acknowledged；失败/取消/过期/unknown有明确状态。delivery_acknowledged只说明客户确认接收或拒绝，不说明审核通过。

关键身份：origin_key用于创建幂等；dispatch_key用于引擎交接；attempt_id用于外部一次执行；receipt_id用于产物接收确认。不同来源应用/workspace之间不能撞键复用。

### 单次执行和恢复

- 服务端事务签发start_grant并锁存可能已执行，绑定原始node/executor_instance。
- current_claim另存holder/epoch/scope/expiry；upload_only恢复不能清除start锁存或重新start。
- 引擎重投不能成为新的生成许可。Agent DB丢失、start回应丢失、外部已接单但ID未落盘，无法查明时unknown。
- 失联、取消请求、lease到期均不证明物理执行停止。unknown仍占本地资源槽；引擎callback结束后也不能据此接下一项执行。
- 暂停/容量控制或有界not_started退回需验证SDK实际能力。不得把任务写入本地第二等待队列。

## 5. 文件闭环

输入由API上传到Bridge输入会话，客户端提供稳定reference和真实hash；上传完成返回input_ref；POST回应丢失时以同input_key与内容摘要重发，返回同input_id。素材不通过Hatchet payload，也不让节点任意抓取Alchemy内网地址。任务引用已完成、授权范围匹配的input_ref，引用顺序/角色写入digest；创建任务事务同时固定引用，保证queued/unknown期间不会被未绑定会话TTL清理。

首版采用上传输入，避免为了云端回拉额外暴露Alchemy媒体接口。可复用相同workspace内的短期输入会话，但禁止全局hash当公开读取凭证。

输出：Agent获得upload_id→流式上传→服务端hash/媒体校验→不可变ready对象→事务内写receipt和待确认记录。Alchemy通过受权API读取manifest及字节，Bridge不推送到Alchemy，也不写原业务output。

客户端确认包含accepted或rejected及稳定client_receipt_id。同receipt重试幂等；重复不同确认冲突。源应用保留原始hash和导入后hash的责任不由Bridge猜测。

## 6. 对Alchemy公开的API

严格使用共享合同文档中的source API。节点claim/start/heartbeat/upload等属于Bridge内部node API，Alchemy接入模块不得调用。Alchemy不持有Hatchet token，不查询Hatchet数据库。

能力目录返回真实profile_id/revision、operation、输入输出上限、已验证/未验证标记、节点可用性；不是Alchemy模型目录的权威替代品。未验证profile不开放正式使用。

## 7. 安全与资源

验证源应用身份及其允许的workspace；请求体的source标签不能覆盖认证身份。所有task/input/receipt读取都检查该范围。节点权限仅其绑定任务。默认一台节点、一并发、一可信workspace。

入队限额包含dispatch_pending，初始建议全局10；上传字节/像素/临时空间独立限额。原生使用不受影响是Alchemy侧验收；Bridge端也须限制自己流量，避免打满共享主机。

禁止远程shell、任意workflow/model下载、任意本地路径、任意SSRF地址。任务日志不存秘密或完整素材。节点撤销立即阻止新授权；已执行事实保持对账。

## 8. 开发工单

RB-01 固定SDK/服务器版本与许可证、Windows/TLS/权限/资源PoC。
RB-02 source API schema+Mock客户端+身份/幂等fixture。
RB-03 Bridge账本、outbox、容量、start-grant及取消并发。
RB-04 输入会话/输出暂存/manifest/receipt/确认API。
RB-05 Agent SQLite、锁、恢复、物理槽、磁盘保护。
RB-06 FakeRenderer独立start计数及精确故障注入。
RB-07 联调API合同与最终审计包。

依次完成，不修改Alchemy仓库。所有接口用模拟Alchemy客户端可独立验收。

## 9. 本端验收

- 100次重复引擎投递已启动任务，start计数1；unknown不能自动再start。
- task创建回应丢失/输入上传回应丢失/receipt回应丢失，原ID可查回。
- 同键不同payload、不同workspace/input/slot交换均拒绝。
- cancel/commit并发线性化，已有receipt仍可按权限对账。
- Agent双实例、进程崩溃、睡眠、断网、DB丢失、磁盘不足均可解释。
- Windows Agent、隔离Linux服务端、真实TLS，通过且留下精确版本日志。
- Hatchet权限或暂停能力不满足时不放行；PoC通过不代替真实工具验收。

## 10. 本端运维与回滚

独立部署，默认与既有业务服务隔离部署；是否共址以实测预算决定。停止新任务不停止必要查询/上传/确认。保留unknown与未确认产物，不能按年龄直接删除唯一副本。清理不涉及Alchemy数据。未绑定任务的输入会话有短期TTL；已绑定活动/unknown任务的输入不得被该TTL清理，需引用检查和有界回收。

版本升级保持source API兼容，旧任务继续同attempt恢复。回滚不把running改queued；保留恢复端点直到未决任务清楚。真实Adapter、付费调用、生产部署另行验收授权。
