# 双方API与联合验收契约 v1.2

这是拟实现的协议，非现有已部署接口。双方先冻结schema和golden fixtures，再各自开发。接口发布为v1；文档版本v1.2不代表协议已经迭代上线。

## 1. 通信方向

Alchemy新增模块主动调用Render Bridge的HTTPS source API。Render Bridge的Agent主动调用自己的node API/任务引擎。Bridge不回调Alchemy、不登录其DB、不直接写其媒体目录。

Alchemy仅发送已授权的冻结请求与素材；Render Bridge仅返回状态、文件与回执。两端通过origin_key/task_id/receipt_id关联，不交换内部数据库主键含义或账户管理权限。

## 2. Source API（Alchemy可调用）

| API | 目的 | 幂等/权限 |
|---|---|---|
| GET /bridge/v1/capabilities | 查询批准profile与限制 | 按源应用/workspace过滤，未知能力禁用 |
| POST /bridge/v1/inputs | 创建输入会话 | source+workspace+input_key；同内容返回同input_id，不同内容409 |
| PUT /bridge/v1/inputs/{id}/content | 流式上传输入 | 同hash重试；完整后才ready |
| GET /bridge/v1/inputs/{id} | 查询ready | 同作用域可读，不全局hash查询 |
| POST /bridge/v1/tasks | 创建异步任务 | origin_key同内容返回原task，不同409 |
| GET /bridge/v1/tasks/by-origin/{key} | 创建回应丢失恢复 | 必须绑定认证作用域，key本身不是权限 |
| GET /bridge/v1/tasks/{id} | 查询状态/receipt | 只读，不触发生成 |
| POST /bridge/v1/tasks/{id}/cancel | 请求取消 | 返回真实当前状态，不保证上游已停 |
| GET /bridge/v1/receipts/{id} | manifest及接收状态 | scope核对，不等于审核通过 |
| GET /bridge/v1/artifacts/{id}/content | 原文件流 | 有权task/receipt关联、下载限额与有效期 |
| POST /bridge/v1/receipts/{id}/ack | 客户端接受/拒绝确认 | client_receipt_id幂等；不同确认冲突 |

本API不包含领取节点、start-grant、续租、GPU中断、Hatchet管理、Alchemy用户余额或数据库操作。

HTTP重试仅对已定义幂等操作进行；429/503尊重Retry-After并有总预算。授权失败不无限重试。超时不能推断未执行。只允许固定TLS服务地址，不跟随跨主机携密重定向。

## 3. 请求合同

字段：schema_version、origin_key、binding_id（不透明）、operation、execution_profile_id/revision、prompt、parameters、按顺序的references(input_ref/role/hash)、output_slot(count=1)、deadline、routing=bridge_only。

source_instance/workspace由已认证源应用可用范围产生或校验；浏览器不可用自填owner让后台代签越权请求。Bridge不持有Alchemy用户会话凭据；Alchemy负责每次访问/取消/导入的原生权限判断。

完整执行语义计算task_digest，规范字节由服务端产生，排除临时URL/凭据与digest本身；拒绝重复JSON键/非法数值/未知执行能力。input_ref须ready、未过期且属于同作用域；任务创建与input_ref引用固定在Bridge自身事务中完成，清理并发必须核对引用。已接受任务的引用在排队、执行、unknown和恢复期间保持有效，不按未绑定输入TTL删除。profile升级不能静默改变已创建任务。

## 4. 返回合同

创建：remote_task_id、origin_key、task_digest、state、poll_after_ms。查询只是状态读取。

状态最少区分：queued、running、awaiting_user、uploading、artifacts_ready、execution_unknown、cancel_requested、canceled、failed、expired。技术引擎状态不直接暴露为原生Alchemy Job状态。Bridge内部dispatch_pending映射queued并可附reason=dispatch_pending，active映射running；API不要求Alchemy理解内部引擎状态。产物ready后保持artifacts_ready，单独delivery_ack={pending,accepted,rejected}表示确认；内部delivery_acknowledged映射该字段，不另造成第四套任务终态。

receipt：receipt_id、task_id、attempt_id、slot_id、profile实际版本、artifact_id、original_sha256、size、MIME、dimensions、accepted_at。accepted_at表示Bridge媒体接收合格，不表示创意/像素审核通过。

ack：client_receipt_id、decision(accepted/rejected)、reason_code、可选不透明native_registration_ref、original_hash、可选normalized_hash。Bridge只保存客户确认，不验证客户业务表，也不能据此声明原生交付成功。

## 5. 两端状态对应

| Bridge状态 | Alchemy模块状态/动作 | 原系统行为 |
|---|---|---|
| queued/running | remote_pending，有限轮询 | 不修改其他原生Job |
| awaiting_user | needs_attention，显示action_required.code与说明，不自动重发 | 只影响新增模块 |
| execution_unknown | needs_reconciliation，禁止重生成 | 不调用旧fallback |
| artifacts_ready | downloading→import_pending | 仅走既有受支持的导入接缝 |
| cancel_requested | cancel_pending | 不宣称已停止 |
| failed/expired/canceled | 对应模块终态或需人工核查 | 不改原默认链路 |
| ack accepted | delivery_confirmed | 原审核仍独立决定交付 |
| ack rejected | delivery_rejected | 不复活取消/删除任务 |

导入回应丢失：Alchemy查自己的稳定导入键；如既有接口没有查询/幂等证据，停在import_unknown，不重复导入。Bridge始终保留receipt供对账，不能再生成补偿。

## 6. 必须共同遵守的竞态

- 创建回应丢失：同origin查回，不新键。
- 外部执行不明：Bridge负责不重start；Alchemy负责不另建task、不云端fallback。
- 上传/下载失败：同文件重传，绝不触发新生成。
- commit与cancel：先cancel拒新accept；先accept保留receipt对账；Alchemy检查本地取消/版本后按原规则导入或拒绝。
- ack回应丢失：同client_receipt_id重发，不再导入。
- 原生Job已删除：允许有权模块完成拒绝确认，不能用迟到文件重新创造原Job。
- 撤销/数据权利变化：每次对象读写重新授权；旧receipt不是绕过认证的通行证。

## 7. 分端测试与联合测试

Bridge端用模拟Alchemy调用者测试协议/执行；Alchemy端用Mock Bridge测试UI/恢复/原功能保护。双方使用同一golden fixtures，无需共享实现代码。

联合最小矩阵：
- 固定图片从输入上传、任务创建到节点执行、结果读取、原生受支持导入、ack完成。
- 重复创建/投递/receipt/ack，start一次，原生登记一次。
- 每一HTTP响应丢失、Agent/服务重启、取消与accept竞争、导入前权限变化。
- Bridge停机/无配置/功能关闭，原Alchemy正常。
- 跨workspace/项目/来源身份、错误hash/格式/超限文件拒绝。
- FakeRenderer仅测试目录，无真实计费、无正式导出或记忆污染。

“独立模块预览成功”不等于“原生结果登记通过”；如果旧系统缺接缝，必须在联合报告中明确FAIL/证据不足并等待扩展点批准。

## 8. 版本、发布和责任

Bridge先发布向后兼容API，Alchemy模块使用明确支持版本；不支持则新模块拒绝启用，不影响原系统。各仓库各自SHA、构建、许可证、测试、回滚文档，联合报告同时固定两端SHA及schema版本。

禁止同一PR顺手修改Sub2API、Brain、计费或全局Provider规则。后续平台Adapter增加且API不变时只发布Bridge；确需新operation时先更新合同再单独给Alchemy模块加能力，不改变原默认请求语义。

## 9. 首批执行模式补充（v1.2设计）

profile声明execution_mode=automatic或manual、requires_human。这是通用执行能力，不是Alchemy版本分支。任务状态增加awaiting_user，action_required是对象字段（非第二状态机），含code与说明；code至少区分submit_in_tool、attach_references、import_original、login_required、tool_approval_required。仅在需要时出现，并给出不含凭据的说明。Alchemy模块只按通用状态显示提示，不内置Discord协议或Codex命令。

Codex登录/额外审批发生在执行前时可进入awaiting_user；如果执行已开始且结果不明，保留execution_unknown并附action_required，不能用等待用户清除start锁存或释放未决物理槽。

manual场景由用户在工具提交并导入原图；Bridge只保证自身派单、关联和回传幂等，不能保证用户没有在外部重复点击生成。人工来源标记human_asserted，不能声称平台签名认证。等待人工以单独有界pending计数管理，不占自动执行资源槽；超时不自动转换为重新生成。

Alchemy与Bridge位于不同VPS；当前任务协议兼容时两端可独立升级。任何Alchemy版本都应在自己适配层转换为本协议，Bridge不按V1/V2/V3等标记选择业务实现。协议不兼容时明确拒绝新请求，原Alchemy路径保持不变。
