# Sub2API Codex Image Provider

状态：**设计文档；未实现、未部署、未联调。**

本功能包规划一个只面向 Sub2API 的 OpenAI Images 兼容入口，公开模型别名为 `gpt-image-2`，实际执行复用 RenderBridge 的 `codex-image` profile 和 Codex ImageGen adapter。它不新增模型执行器、任务队列或第二份执行账本。

## 文档

- [设计与接口合同](DESIGN.zh-CN.md)
- [实施与验收计划](IMPLEMENTATION_PLAN.zh-CN.md)
- [独立审计记录](AUDIT.zh-CN.md)

## 已核实的基线

- RenderBridge GitHub `main` 在本次核对时为 `11820032b8a0d7354cffc6547efaa04b80a7a382`。该提交已包含 PR #33；没有需要从当前工作目录推送的 Git 分支。工作目录是运行/部署目录而非 Git checkout，`private/primary-acceptance-20261024/sidecar-source-mirror` 也是比远端 `main` 旧的源码快照，不能覆盖主线。
- RenderBridge 主线现有 `/bridge/v1/*` Source API 与持久任务/回执流程；没有 OpenAI `/v1/images/generations` provider endpoint。
- 主线已有 `codex_cli_imagegen_v1` 参数适配。它不提供原生 `size`、`quality`、`seed`、`output_format` 或 `input_fidelity` 控制；这些值映射到 Codex ImageGen 默认值，生成原图仍按 Bridge 现有合同验收。
- Native Harness PR #16 仍是草稿，内容是独立的 Harness 合同；它不是本功能的实施分支，本计划不合并或改写它。
- Sub2API 的现有图片上游能力核对基线为交接中的提交 `b25ff6169885c66308ed3dffd93da9c3cb3fcf79`。实施前仍要核实目标 Sub2API 部署确实包含兼容 OpenAI API Key 图片转发、`gpt-image-2` 模型准入和稳定请求幂等语义。

本地部署快照与 GitHub `main` 不一致时，以经审阅的 GitHub commit 为源；不得把部署数据目录或旧镜像当作仓库工作树上传。
