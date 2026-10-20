# Alchemy Render Bridge

An optional API-based bridge between Alchemy applications and local rendering tools.

可选的本地渲染桥接程序：让 Alchemy 通过 API 提交冻结的图片/视频任务，由本地 Agent 执行并回传结果。先完成通用桥接，再逐个适配真实工具。

## 当前状态

**第一阶段：隔离API合同原型与任务引擎可行性试验。已有可运行测试代码及真实引擎实验，尚无可部署的渲染服务或正式Windows Agent。**

- 文档基线：分端开发方案 v1.2，2026-10-20。
- 实现范围：[第一阶段范围](docs/implementation/PHASE1_SCOPE.md)、[运行隔离测试](docs/implementation/RUN_CONTRACT_TESTS.md)。
- 引擎与业务接缝：[Hatchet门禁](docs/implementation/HATCHET_POC_GATE.md)、[Alchemy只读预检](docs/implementation/ALCHEMY_READ_ONLY_PREFLIGHT.md)。
- 首阶段：隔离环境、一个受信工作区、一个本地节点、FakeRenderer。
- Hatchet 是待验证的任务引擎候选，不是已确定可上线的依赖。
- [原生Windows与真实Linux引擎的三任务试验](docs/implementation/WSL_NATIVE_ENGINE_VALIDATION.md)已通过，TLS、权限及故障恢复等门禁仍未完成。
- [节点HTTP与任务账本的隔离闭环](docs/implementation/NODE_LEDGER_HTTP_TRIAL.md)已通过；来源侧仍是领域模拟器，不是Alchemy联调。
- Windows 合同测试不代表 Windows Agent 验收；该实验存储不承诺 Windows 断电目录持久性或 ACL 安全隔离。
- 首批真实场景为本机已登录的Codex CLI与Midjourney（Discord API发任务消息、用户手动提交、原图导入）；均尚未实现。ComfyUI后置；没有真实模型、生产或正式Windows Agent验收。

## 架构边界

1. **Render Bridge 独立程序**：自己的任务、节点、执行许可、恢复记录及私有产物存储。
2. **Alchemy 可选接入模块**：默认关闭，使用 API 通信，管理模块自己的任务映射与恢复。
3. **共享 API 合同**：创建、查询、取消、输入上传、产物下载与接收确认。

Alchemy与Render Bridge分别部署在不同VPS，严格独立发布；本地Agent运行在用户电脑。双方不共享数据库、文件目录、内部对象或执行队列。Render Bridge 不直接写 Alchemy 的 output/candidate，不执行其计费和审核。Alchemy 原有功能与默认链路保持不变；缺少安全扩展接缝时先提交最小方案，不擅自改核心。

## 开发文档

- [开发边界与导航](docs/README_开发边界与文档导航.md)
- [Render Bridge 程序开发方案](docs/render-bridge/01_Render_Bridge程序开发方案.md)
- [Alchemy 新增接入模块开发方案](docs/alchemy-integration/02_Alchemy新增接入模块开发方案.md)
- [双方 API 与联合验收契约](docs/shared-contract/03_双方API与联合验收契约.md)
- [开源基座调研与选型](docs/research/Alchemy_Render_Bridge_开源基座调研与选型.md)

最新分端方案优先于调研报告中的早期架构建议。调研结果不能代替固定版本的运行与安全验收。

## 下一步

Bridge不区分Alchemy V1/V2/V3/Lab/Video OS；各版本在Alchemy侧适配成同一最终请求。核实 Alchemy 现有扩展接缝并冻结 API；验证 Hatchet 的 Windows、TLS、权限、重投与资源行为；随后双方分别使用模拟客户端/服务开发，再联调 FakeRenderer。FakeRenderer验证通用桥后，优先Codex CLI与Midjourney人工执行闭环；ComfyUI和视频执行器后置。

本仓库是 Render Bridge 的后续开发基线。Alchemy 接入模块实现应位于对应 Alchemy 项目，本仓库保存其设计合同与联调约束，不复制或修改旧业务系统。

## 许可

当前仓库公开可读，但项目自身的代码分发许可证尚未选定，不能把公开状态等同于已授予开源许可。按实验项目决定，项目自身许可证选择暂缓，不作为隔离开发准备的阻塞。第三方许可证和服务条款仍分别适用。外部项目的许可由各自 LICENSE/NOTICE 决定；当前未复制其实现代码。

## 首批场景与独立部署

- [双VPS与版本独立边界](docs/shared-contract/04_双VPS与版本独立约束.md)
- [Codex CLI适配方案](docs/render-bridge/adapters/05_Codex_CLI适配方案.md)
- [Midjourney与Discord API适配方案](docs/render-bridge/adapters/06_Midjourney_Discord适配方案.md)
