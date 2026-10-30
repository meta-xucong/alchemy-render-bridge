# Alchemy Render Bridge

An optional API-based bridge between Alchemy applications and local rendering tools.

Remote VPS-source adaptation: [bounded storage and HTTPS runbook](docs/implementation/REMOTE_SOURCE_READINESS.md). This is optional configuration; existing local defaults remain unchanged.
For a VPS Bridge with a local Windows Agent, start from the [`node_pull` runtime and cutover contract](docs/shared-contract/04_API_PULL_NODE_RUNTIME.md) and [`bridge.remote-pull.example.json`](config/bridge.remote-pull.example.json); the Hatchet instructions below describe the separate default dispatch mode.

Current parameter-contract candidate: see the [Codex adapter contract](docs/render-bridge/adapters/05_Codex_CLI适配方案.md).
Native-default image options are explicitly versioned; unmarked historical tasks
retain their original recovery contract. Alchemy's four-product integration lives
in its separate repository. The historical milestone notes below are not current
real-account, Windows, or image-generation acceptance evidence.

可选的本地渲染桥接程序：让 Alchemy 通过 API 提交冻结的图片/视频任务，由本地 Agent 执行并回传结果。先完成通用桥接，再逐个适配真实工具。

## 当前状态

**当前：可配置 Bridge / 通用 Agent、Alchemy V3 Standard/general 单图原生适配及显式自动回退已有代码与模拟联调证据；仍为开发与 review-only 范围，用户 Windows 实机登录、生图及生产部署尚未验收。**

- 文档基线：分端开发方案 v1.2；2026-10-21 新增自动回退契约、实现与同机 Windows 首轮验收安排。当前证据和限制见[自动回退实现验收](docs/implementation/AUTO_FALLBACK_ACCEPTANCE.md)，实机步骤见[Windows 同机指南](docs/implementation/WINDOWS_LOCAL_LOOPBACK.md)。
- 新增：[单节点撤销与有界事件记录](docs/implementation/NODE_OPERATIONS_SCOPE.md)。
- 新增：[节点就绪、待处理与停新任务](docs/implementation/NODE_READINESS_AND_ATTENTION.md)、[完整蓝图缺口审计](docs/implementation/DELIVERY_GAP_AUDIT.md)。
- 新增：[有界图片选项合同](docs/implementation/TYPED_IMAGE_OPTIONS_SCOPE.md)、[本地就绪诊断](docs/implementation/LOCAL_READINESS_DOCTOR.md)、[可配置服务与 Node API](docs/implementation/CONFIGURED_BRIDGE_RUNTIME.md)、[离线执行器](docs/implementation/OFFLINE_EXECUTORS.md)、[云端开发与本地迁移说明](docs/implementation/LOCAL_MIGRATION.md)。
- Codex 流式通知过载修正及回归边界见[事件流健壮性说明](docs/implementation/CODEX_EVENT_STREAM_RESILIENCE.md)。
- 实现范围：[第一阶段范围](docs/implementation/PHASE1_SCOPE.md)、[运行隔离测试](docs/implementation/RUN_CONTRACT_TESTS.md)。
- 引擎与业务接缝：[Hatchet门禁](docs/implementation/HATCHET_POC_GATE.md)、[Alchemy只读预检](docs/implementation/ALCHEMY_READ_ONLY_PREFLIGHT.md)。
- 首阶段：隔离环境、一个受信工作区、一个本地节点、FakeRenderer。
- Hatchet 是待验证的任务引擎候选，不是已确定可上线的依赖。
- [原生Windows与真实Linux引擎的三任务试验](docs/implementation/WSL_NATIVE_ENGINE_VALIDATION.md)已通过，TLS、权限及故障恢复等门禁仍未完成。
- [Source HTTP 开发演示与运行方法](docs/implementation/SOURCE_HTTP_DEMO.md)：补齐来源侧实际 HTTP 调用，提供合成 PNG 和结果报告；仍非生产渲染服务。
- [节点HTTP与任务账本的隔离闭环](docs/implementation/NODE_LEDGER_HTTP_TRIAL.md)已通过；该历史试验的来源侧为领域模拟器，不能用它证明 Alchemy 联调；后续实际 Alchemy 宿主加独立 Bridge HTTP 的合成联调证据见上述实现验收收据。
- Windows 合同测试不代表 Windows Agent 验收；该实验存储不承诺 Windows 断电目录持久性或 ACL 安全隔离。
- 首批真实场景为本机已登录的Codex CLI与Midjourney（Discord API发任务消息、用户手动提交、原图导入）；已有可配置任务路由与适配组件，真实调用默认未启用。ComfyUI后置；没有真实模型、生产或正式Windows Agent验收。

## 架构边界

1. **Render Bridge 独立程序**：自己的任务、节点、执行许可、恢复记录及私有产物存储。
2. **Alchemy 可选接入模块**：默认关闭，使用 API 通信，管理模块自己的任务映射与恢复。
3. **共享 API 合同**：创建、查询、取消、输入上传、产物下载与接收确认。

Alchemy与Render Bridge是独立服务、严格独立发布；最新首轮验收按用户要求把本地Alchemy、Bridge/Agent和用户已登录Codex CLI部署在同一台Windows电脑，通过loopback HTTP互通、各自独立数据目录。双VPS部署保留为后续选项，不是本轮测试前提。非loopback/远程连接仍要求TLS与原安全门禁。双方不共享数据库、文件目录、内部对象或执行队列。Render Bridge 不直接写 Alchemy 的 output/candidate，不执行其计费和审核。Alchemy 原有功能与默认链路保持不变；缺少安全扩展接缝时先提交最小方案，不擅自改核心。

## 开发文档

- [开发边界与导航](docs/README_开发边界与文档导航.md)
- [Render Bridge 程序开发方案](docs/render-bridge/01_Render_Bridge程序开发方案.md)
- [Alchemy 新增接入模块开发方案](docs/alchemy-integration/02_Alchemy新增接入模块开发方案.md)
- [双方 API 与联合验收契约](docs/shared-contract/03_双方API与联合验收契约.md)
- [开源基座调研与选型](docs/research/Alchemy_Render_Bridge_开源基座调研与选型.md)

最新分端方案优先于调研报告中的早期架构建议。调研结果不能代替固定版本的运行与安全验收。

## 下一步

Bridge不区分Alchemy V1/V2/V3/Lab/Video OS；各版本应在Alchemy侧适配成同一最终请求，但这不表示所有版本的接入均已实现。当前Alchemy独立仓库已实现预览、V3 Standard/general单图原生登记与共享审核接缝、有界图片参数适配及默认关闭的prefer_bridge路由；代码和模拟证据不等于真实工具能力验收，也不开放正式交付/计费。最终交付须先完成已确认缺口的修复与最终版本回归，再按Windows同机指南进行实机分阶段验收，使用真实宿主认证、canonical Brain和审核配置；fake渲染器不保证宿主规划/审核无费用。Codex登录、真实图片/参考图效果与恢复仍留到用户本机验证，不以版本号、健康报告或操作者配置标记代替。Codex原生控制缺口、PostgreSQL生产持久层、双VPS部署、其他Alchemy版本接入仍是限制或后续范围；ComfyUI和视频执行器后置。

本仓库是 Render Bridge 的后续开发基线。Alchemy 接入模块实现应位于对应 Alchemy 项目，本仓库保存其设计合同与联调约束，不复制或修改旧业务系统。

## 许可

当前仓库公开可读，但项目自身的代码分发许可证尚未选定，不能把公开状态等同于已授予开源许可。按实验项目决定，项目自身许可证选择暂缓，不作为隔离开发准备的阻塞。第三方许可证和服务条款仍分别适用。外部项目的许可由各自 LICENSE/NOTICE 决定；当前未复制其实现代码。

## 首批场景与独立部署

- [同机首测、双VPS与版本独立边界](docs/shared-contract/04_双VPS与版本独立约束.md)
- [Codex CLI适配方案](docs/render-bridge/adapters/05_Codex_CLI适配方案.md)
- [Midjourney与Discord API适配方案](docs/render-bridge/adapters/06_Midjourney_Discord适配方案.md)

- [自动回退代码落地与兼容契约](docs/shared-contract/08_自动回退代码落地与兼容契约.md)：冻结健康信号、pre-start撤回、路由身份和V3传统provider接缝；相关代码与模拟联调已实施，具体证据/未测范围见[实现验收](docs/implementation/AUTO_FALLBACK_ACCEPTANCE.md)，设计或模拟通过均不等于实机/生产验收。
