# 云端开发与本地迁移

> 当前实机验收：同一台 Windows 电脑运行本地 Alchemy、独立 Bridge 与用户已有 Codex CLI，通过回环版本化 HTTP API 联调；不要求先部署双 VPS。精确 PowerShell 安装、独立目录、SDK/引擎前提、启动顺序及分阶段验收见 [Windows 同机联调](WINDOWS_LOCAL_LOOPBACK.md)。生产远程 TLS 与数据隔离边界不变。

当前交付目标是无需登录真实工具即可开发和验证的 Bridge/Agent 开发版。真实 Codex 登录、生图和 Midjourney 操作在迁移本地后验收，不阻塞云端代码实现。

## 最快验证

Python 3.12+，仓库根目录：

```sh
python -m venv .venv
# 使用该环境的 python（Windows: .venv\Scripts\python；Linux: .venv/bin/python）
python -m pip install --require-hashes -r requirements-runtime.txt
python -m pip install --no-deps -e .
python scripts/run_configured_demo.py --output-dir demo-output
```

输出目录必须不存在。该命令启动独立 Bridge 进程和 Agent 进程，完成实际 HTTP 创建、持久 outbox、显式模拟派发、一次 fake 执行、结果上传/下载、hash 校验及回执确认；再次启动 Agent 投递同任务不会再生成。还会先导出一项人工待办，验证它不阻塞 fake 任务，再由重启后的 Agent 将测试 PNG 显式导入原任务并确认回执（不实际调用 Midjourney）。退出清理临时服务/Agent数据，只保留测试 PNG 和 result.json。

这是 mock dispatcher 联合验收，不是启动真实 Hatchet 的命令，也不调用真实模型。测试图是合成线条图，不是 AI 生成结果。

## 持续运行与迁移目录

- 服务端：复制 `config/bridge.example.json`，确认持久数据目录（默认 `.runtime/bridge`）、身份与端口。按照[服务说明](CONFIGURED_BRIDGE_RUNTIME.md)配置 source/node/dispatcher 三个不同凭据，再启动 `python -m render_bridge.runtime --config ...`。不把凭据存入仓库。
- Agent：独立状态目录包含 journal、引用素材、本次执行原图及上传恢复记录。停止进程后整体备份，不要仅搬程序却丢弃未决 attempt。模块 `python -m render_bridge.agent --help` 提供显式开发投递入口。
- 引擎：Hatchet SDK 使用独立解释器及已固定锁定依赖，不将它的 Pydantic 版本混入 Bridge 解释器。dispatcher/worker 启动方式见 Agent 说明和 `python -m render_bridge.agent.hatchet_sidecar --help`。
- 执行器：`python -m render_bridge.executors --help`。Fake 与人工导出/导入可离线试用；Codex 默认禁用真实执行，不导入或复制用户登录凭据到云端。

## 迁移前只读诊断

```sh
python -m render_bridge.doctor --bridge-config config/bridge.example.json --agent-config config/agent.example.json --json
# 只有显式传入才探测已安装 CLI 的版本/help；使用临时空 HOME/CODEX_HOME：
python -m render_bridge.doctor --bridge-config config/bridge.example.json --agent-config config/agent.example.json --probe-codex --json
```

默认不启动子进程、不创建服务/客户端/数据库、不联网、不读取凭据或修改配置。
退出码 0 仅表示配置检查通过，1 表示待处理，2 表示配置无效。无论退出码如何，
`login_verified` 和 `real_generation_verified` 始终为 false。Agent 的验收标记只是
操作者声明，不是 doctor 测得的能力；两端 profile 不一致会提示，不自动启用。
完整参数与 JSON 合同见[诊断说明](LOCAL_READINESS_DOCTOR.md)。

## 本地验收清单（尚未执行）

1. 安装并登录用户自己的 Codex CLI，固定版本；用无敏感材料确认图像事件、原始产物及工具权限，之后才显式启用适配。
2. 真实生图前确认测试数量/额度；确认从原文件到 Bridge 收据的同一任务归属。只有文本回复不算成功，不自动改走其他上游。
3. Midjourney 由用户实际提交和下载原图；人工导入必须选定任务并确认单张原图。Discord 通知、频道权限和真实发送另行配置，不代表普通消息能调用 `/imagine`。
4. 使用真实 Hatchet 服务、安装目录和网络配置进行启动/上传恢复核验；不关闭证书校验。

## 仍需开发或配置的范围

默认服务和 Agent 只启用 `fake-image`。`codex-image` 与 `midjourney-manual` 的配置路由、执行前检查、人工等待及原图导入接线已经实现，必须在服务和 Agent 配置中分别显式启用。Codex 仅允许精确 `codex-cli 0.157.1` 和 `codex-cli 0.163.0`；静态 wire review 不代表已登录或真实生图验收，每个版本必须分别完成本机真实图片能力验收后，才能将该版本写入 `accepted_cli_version` 并设置能力标记，运行时探测必须与之相同。两版本官方 wire schema 均包含 `untrusted` 审批枚举，但 Bridge 只接受显式的 `codex.approval_policy="on-request"`（空策略仍阻断，旧标记不自动迁移）。当前仅通过模拟子进程测试，不代表真实生图已验收。`midjourney-manual` 必须显式提供目标宽高，回传原图严格核验尺寸；Codex 尺寸仅为冻结请求证据和提示，Bridge 登记其实际解码尺寸，不自动缩放。

SQLite 是单机开发后端，不等于设计中的 PostgreSQL 生产持久层。Alchemy 独立模块的预览、原生登记/审核恢复、显式真实开发 profile 适配、管理员界面和 V3 Standard/general 单图试用入口已在对应仓库实现，不属于本仓库代码；不是普通 Generate、正式选图/导出或计费接入。Bridge 已实现有界的嵌套 image_options：quality 仅接受 auto（未指定档位），output_format 仅校验原图 PNG/JPEG，input_fidelity 必须省略；Codex 0.163.0 没有这些原生结构化控制。Alchemy 独立适配已有模拟合同/HTTP验收，最终修订回归与本机真实生图仍须分别记录，不能只靠登录或把文本指令当成原生控制。单节点持久撤销/恢复、就绪报告、显式本地媒体清理及 Discord 普通任务通知已实现；PostgreSQL迁移、自助配对/多节点管理及双VPS部署仍未完成。不要直接将本开发配置暴露公网，也不要宣称所有产品功能已完成。

验收聚焦正常主流程、重复投递不重复生成和已生成文件不丢失。非阻塞的复杂故障矩阵后置，不因尚未登录真实工具停下其他可实施开发。

当前代码范围、明确后置项和实机验收的统一分类见 [最终开发范围](FINAL_DEVELOPMENT_SCOPE.md)。历史阶段记录不代表最终修订验收。


新增账户健康探测默认关闭，与默认无副作用的 doctor 不同。顶层 `account_health_probe_enabled=true` 只能在本机操作者同意使用已有 CLI 账号查询状态后开启；CLI 可能内部刷新已有登录，Bridge 不复制认证文件、不建立新授权、不付费生图。不开启时 auth/quota 恢复需操作者针对原原因确认；完整字段、来源及迁移顺序见 [Agent 说明](CONFIGURED_AGENT.md#健康报告账户状态与显式工具策略)和 [Windows 同机联调](WINDOWS_LOCAL_LOOPBACK.md)。
