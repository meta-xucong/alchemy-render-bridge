# Source HTTP FakeRenderer 开发演示

本次补齐的交付缺口是：来源侧不再直接调用领域方法创建任务、下载产物和确认回执，而是通过实际 loopback HTTP 使用现有 `/bridge/v1` API。节点仍走独立的 test-only HTTP 协议，Hatchet 是 Windows CI 中唯一任务派发引擎。没有修改 Alchemy。

## 最小演示

在仓库根目录的 Python 3.12+ 隔离环境中执行：

```sh
python -m pip install --require-hashes -r requirements-test.txt
python -m pip install --require-hashes -r experiments/hatchet-wsl1-integration/requirements-source-server.txt
python -m pip install --no-deps -e .
python experiments/hatchet-wsl1-integration/demo_source_http.py --output-dir demo-output
```

输出目录必须尚不存在。成功后得到 `synthetic.png` 和不含凭据的 `result.json`。PNG 是明确的合成测试图，不是 AI 生成结果。临时账本、凭据和监听器在退出时清理；仅保留用户指定的演示结果。

这一快捷入口使用本机模拟节点、真正的 HTTP socket 和现有 Source API，**不启动 Hatchet、WSL 或真实渲染工具**。来源客户端和服务位于同一进程的不同执行线程，但来源客户端不访问账本或产物文件系统。`source_http.SourceClient` 是独立薄客户端，可检查其 create/query/download/ack 调用方式；不是生产 SDK。

## Windows + Hatchet 验收入口

沿用 GitHub Actions 的 `Experimental WSL1 native Windows engine smoke` 工作流，入口仍为 `preflight.py --engine`。CI 安装单独 hash-lock 的 Uvicorn 到 Bridge 解释器，不污染 SDK 解释器。实际路径为：

来源 HTTP 创建与同 origin 重放 → 既有 fixture 建立执行 claim → Hatchet 派发 → 原生 Windows Worker → 节点 HTTP 上传/提交 → 来源 HTTP 查询、下载、SHA-256 校验和重复 ack。

所有新监听器只绑定 `127.0.0.1`。此增量增加一个 Source API 端口，继续检查退出后无新增监听器。CI 的 `wsl1-preflight-result` artifact 同时保存 `result.json` 和 `demo-artifact.png`。仅在成功验收后输出 PNG，内存中的临时 bearer 不写入报告或产物。

## 此次停止标准

1. 实际 HTTP 创建、查询、下载、确认通过。
2. 同 origin 返回同 task，五次既有派发仅一次 FakeRenderer 启动。
3. 关键本地回归及一轮真实 Windows/Hatchet CI 通过。
4. 提供可拿到的合成 PNG、简短结果和上述运行方法。

达到以上标准即可交付这个开发演示，不再增加非阻塞边界测试。已有 unknown 场景仍为主动注入，不是实际崩溃恢复。TLS/远程部署、生产权限与节点配对、真实 Alchemy 接入、参考图、Codex/Midjourney 执行和正式 Windows Agent 均不在本次验收范围。本演示不是生产服务或可用的真实渲染产品。
