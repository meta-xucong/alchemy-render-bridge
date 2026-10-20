# Alchemy Render Bridge

An optional API-based bridge between Alchemy applications and local rendering tools.

可选的本地渲染桥接程序：让 Alchemy 通过 API 提交冻结的图片/视频任务，由本地 Agent 执行并回传结果。先完成通用桥接，再逐个适配真实工具。

## 当前状态

**设计与开发准备阶段。仓库目前只有设计文档，没有可安装或可运行的渲染服务。**

- 文档基线：分端开发方案 v1.1，2026-10-20。
- 首阶段：隔离环境、一个受信工作区、一个本地节点、FakeRenderer。
- Hatchet 是待验证的任务引擎候选，不是已确定可上线的依赖。
- ComfyUI、Codex及其他工具适配尚未实现；没有真实模型、生产或Windows运行验收。

## 架构边界

1. **Render Bridge 独立程序**：自己的任务、节点、执行许可、恢复记录及私有产物存储。
2. **Alchemy 可选接入模块**：默认关闭，使用 API 通信，管理模块自己的任务映射与恢复。
3. **共享 API 合同**：创建、查询、取消、输入上传、产物下载与接收确认。

双方不共享数据库、文件目录、内部对象或执行队列。Render Bridge 不直接写 Alchemy 的 output/candidate，不执行其计费和审核。Alchemy 原有功能与默认链路保持不变；缺少安全扩展接缝时先提交最小方案，不擅自改核心。

## 开发文档

- [开发边界与导航](docs/README_开发边界与文档导航.md)
- [Render Bridge 程序开发方案](docs/render-bridge/01_Render_Bridge程序开发方案.md)
- [Alchemy 新增接入模块开发方案](docs/alchemy-integration/02_Alchemy新增接入模块开发方案.md)
- [双方 API 与联合验收契约](docs/shared-contract/03_双方API与联合验收契约.md)
- [开源基座调研与选型](docs/research/Alchemy_Render_Bridge_开源基座调研与选型.md)

最新分端方案优先于调研报告中的早期架构建议。调研结果不能代替固定版本的运行与安全验收。

## 下一步

核实 Alchemy 现有扩展接缝并冻结 API；验证 Hatchet 的 Windows、TLS、权限、重投与资源行为；随后双方分别使用模拟客户端/服务开发，再联调 FakeRenderer。真实平台适配后置。

本仓库是 Render Bridge 的后续开发基线。Alchemy 接入模块实现应位于对应 Alchemy 项目，本仓库保存其设计合同与联调约束，不复制或修改旧业务系统。

## 许可

当前仓库公开可读，但项目自身的代码分发许可证尚未选定，不能把公开状态等同于已授予开源许可。后续实现前应明确项目许可证。外部项目的许可由各自 LICENSE/NOTICE 决定；当前未复制其实现代码。
