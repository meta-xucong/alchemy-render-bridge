# Render Bridge / Alchemy 分端开发文档 v1.2

2026-10-20。按用户最新要求拆分，取代v1.0中可能让人误以为需要重构Alchemy主流程的实施安排。v1.0属于早期历史材料，未纳入本仓库，不作为扩改原系统的授权。

## 一、不可跨越的边界

Render Bridge是独立程序，与Alchemy部署在不同VPS；Alchemy只外加一个可关闭的接入模块。两端通过版本化HTTPS API交互，不共享数据库、文件目录、内部Python对象或任务队列。

- Bridge开发者只修改Bridge仓库，不能改Alchemy业务规则。
- Alchemy开发者只实现新增接入模块，以及必要的显式挂载/配置/可选入口；不得重写原生成、重试、计费、审核、历史或调度逻辑。
- 现有API/Sub2API路径及默认选项不变。没有选Bridge的请求不进入新模块。
- 新模块关闭、Bridge断网、Hatchet停止或未安装，原Alchemy仍必须能启动并正常使用。
- 只有明确选择Bridge的任务显示新模块自身的排队/恢复状态；不能因此把全站Job改成新状态机。
- 若现有系统缺少安全扩展/结果导入接缝，先报告阻断与最小挂载建议，不能擅自修改旧服务实现绕过。

“不影响”是代码隔离和回归验收标准，不是写了模块便天然成立的保证。

## 二、文档分放

1. `render-bridge/01_Render_Bridge程序开发方案.md`：独立服务器、Agent、任务引擎、执行器、私有存储、恢复与测试。
2. `alchemy-integration/02_Alchemy新增接入模块开发方案.md`：新增模块、API客户端、桥接任务记录、可选界面与原生结果导入；原功能保护与回滚。
3. `shared-contract/03_双方API与联合验收契约.md`：双方共同冻结的请求、状态、回执、权限、幂等和错误语义。

4. `shared-contract/04_双VPS与版本独立约束.md`：不同VPS、版本中立与升级边界。
5. `render-bridge/adapters/05_Codex_CLI适配方案.md`：本机登录场景及能力验证。
6. `render-bridge/adapters/06_Midjourney_Discord适配方案.md`：官方消息API+人工提交/导入。

每个文件都有自己负责和明确禁止修改的范围。共享合同不等于共享运行时后端。

## 三、责任速查

| 工作 | Render Bridge | Alchemy新增模块 | 现有Alchemy主业务 |
|---|---|---|---|
| 用户/项目权限 | 校验受信调用者与workspace范围 | 复用现有认证、核实项目归属 | 保持权威、不改规则 |
| Prompt/参考图 | 接收冻结请求，不重新规划 | 取得已授权冻结输入 | 原规划保持原样 |
| Bridge任务 | 创建、排队、执行许可 | 保存请求/远端handle映射 | 不改旧任务状态机 |
| 本地节点/工具 | 配对、Agent、资源槽、Adapter | 仅查询可用profile | 不安装Hatchet SDK |
| 产物 | 私有暂存、hash验证、回执 | 通过API下载并校验，调用既有结果接缝 | 原存储/审核/选择/交付保持权威 |
| 失败恢复 | 同attempt执行/上传对账 | 同handle查询/导入对账 | 不改旧重试或fallback规则 |
| 数据 | Bridge自己的DB/文件 | 模块自己的映射与恢复记录 | 不让Bridge直接访问 |
| 回滚 | 保留未决任务恢复 | 关闭新入口，保留待导入对账 | 原API继续运行 |

## 四、首阶段顺序

先冻结共享API → Bridge用模拟Alchemy客户端开发 → Alchemy模块用Mock Bridge独立开发 → 两端联调FakeRenderer → 原功能回归 → 用户验收。Hatchet可行性只阻塞Bridge的引擎选择，不应阻塞Alchemy模块的Mock开发。

先用FakeRenderer过通用桥技术门禁，再优先接本机Codex CLI与Midjourney人工执行闭环；ComfyUI后置。不自动回退Sub2API、不改模型目录核心、不扩展全站账单。后续真实Adapter只改Bridge端；确有新增业务能力时再单独扩展API与Alchemy模块。

## 五、范围解释

v1.0中“原模块pending恢复”具体落到新增模块自己的integration_job恢复记录与有界查询循环，不改全部原生Job的恢复流程；“Alchemy侧Bridge账本”改称独立Render Bridge服务账本；“registration outbox”改称Bridge端待确认收据记录，不向Alchemy发送第二路回调。此解释优先于旧材料。

如果原生接口只支持同步返回，禁止拉长HTTP等待、挪用原队列或把新状态塞回旧状态机。先做独立Bridge任务视图，只有既有受支持的幂等导入入口能正确绑定时才开放原生结果导入；若没有入口，本阶段联合验收标记阻断，提交最小扩展点方案给用户确认。

## 六、本轮文档复核

独立只读复核确认两个程序和共享合同的责任边界已清晰，未发现需要重拆架构的越界。复核提出的input_key响应丢失、引用固定防清理、内部/API状态映射及disabled/draining区分已补入。此为设计文档结论；未验证真实仓库接缝、未实施或部署。

## 七、v1.2新增决定

Bridge与Alchemy在不同VPS，API是唯一业务交界；Alchemy版本适配全部留在Alchemy侧。项目许可证暂缓，不阻塞实验开发，仍保留第三方许可声明。首批真实场景是Codex CLI和Midjourney。Discord用官方API/Webhook投送普通任务消息，不模拟键鼠；实际/imagine及原图获取由用户完成。新增双VPS约束和两个Adapter文档；旧调研中的平台排序不覆盖此决定。


## 2026-10-21 新增设计：可用性与排队自动回退

见 [07：可用性与排队自动回退设计](shared-contract/07_可用性与排队自动回退设计.md)。
用户新增要求为本地工具不可用或排队过久时暂时使用 Sub2API，恢复后新请求回到本地。
这是尚未实现的新路由阶段，仅为显式开启策略的任务扩展原 bridge_only 边界；
旧默认、原生审核/计费权威及已执行/不明任务禁止重复生成的约束不变。
此前首版代码完成的结论不覆盖这项新增设计，不能把旧心跳或排队状态称为已实现自动切换。
