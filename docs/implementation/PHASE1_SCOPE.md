# 第一阶段实现范围与环境门禁

基线：73dd31209f5b7b85b7f6f4363e795ae8f07cf535。分支：feat/phase1-bridge-contracts。

## 本轮实施

- 冻结并实现最小source API、任务/输入/产物/收据合同。
- 构建独立的、显式测试模式FastAPI应用工厂与SQLite领域状态测试底座。
- FakeRenderer与模拟Alchemy客户端验证API、幂等、权限、恢复和取消竞争。
- 只读核查Alchemy接缝，记录实际缺口；不修改Alchemy。
- 独立审计与本轮实际测试记录。

## 为什么存在SQLite

SQLite仅为云端隔离合同/事务故障试验提供可持久化测试底座，不是宣布产品改用自建HTTP调度器，也不是替代Hatchet/PostgreSQL选型。第一阶段不启动真实执行调度器，只有测试驱动显式调用领域claim/start方法；不存在公网node API。

任务状态与start许可对引擎独立，可为后续Hatchet接入提供验收基准。数据库适配、引擎交付、实际Windows Agent、持续授权与生产服务仍需对应阶段验证。不能将该测试底座部署为生产服务。

## 环境事实与停止条件

当前云端Python具备FastAPI、Pydantic、httpx、Pillow；未发现Docker命令，尚无Windows执行器或已授权的Hatchet服务。先完成不依赖这些环境的合同与隔离验证。官方SDK另有无Docker的embedded引擎，但会创建本地测试授权状态；尚未获准启动。固定SDK还发现Windows信号处理兼容问题。具体门禁见HATCHET_POC_GATE.md；环境缺失不是Hatchet产品失败，不据此选择另一引擎。

本轮不创建持久凭据、不调用Codex/Midjourney/Discord、不消耗真实Provider额度、不操作用户电脑或VPS、不修改全站模型/账单/审核。

## 放行口径

可交付“第一阶段合同原型及测试证据”，不能写“完整Render Bridge可上线”。Hatchet、Windows、真实Alchemy结果导入或安全授权未验收，必须单独列为未完成。代码通过隔离测试也不自动解除下一阶段门禁。

## 后续隔离验证更新

此前启动权限阻塞已解除，Linux Hatchet 冒烟测试已执行，详见
[实际运行验证](HATCHET_RUNTIME_VALIDATION.md)。这不解除 Windows、TLS、权限与故障门禁。
