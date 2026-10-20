# 原生 Windows → WSL1 Linux Hatchet 实际联调验收

2026-10-20。**本次限定试验通过；完整第一阶段和生产部署尚未放行。**

## 精确代码和运行

- 实现提交：`18d85eee87da5a5d7d8cce9c9ccc521f2ecd7d59`。
- [真实引擎 CI 39381959728](https://github.com/meta-xucong/alchemy-render-bridge/actions/runs/39381959728)，job `118492859821`，成功。
- [合同 CI 39381959737](https://github.com/meta-xucong/alchemy-render-bridge/actions/runs/39381959737)，Linux/Windows 均成功。
- [生命周期 CI 39381959683](https://github.com/meta-xucong/alchemy-render-bridge/actions/runs/39381959683)，Linux/Windows 均成功。
- 环境：GitHub Windows Server 2025 镜像 `20261007.264.1`，明确使用已有 WSL1；没有启用 WSL2、改默认版本、防火墙或虚拟化设置。
- 固定 Ubuntu、Zonky PostgreSQL18.3、embedded0.110.5、SDK1.42.1+bridgewin1 的来源与 hash 见实验目录的 sources.json 和 SDK candidate manifest。

## 实际通过

1. 49项隔离安全单测通过。合同62项与SDK生命周期10项仍分别独立运行，不能混称为同一种端到端测试。
2. 校验后的 Ubuntu 成功导入 WSL1，原生 Windows 与 Linux 的本机通信通过。
3. PostgreSQL18.3 以现有 nobody 账户运行，独立认证和 SQL42 试验通过，随后正常停止。
4. 新建临时数据库和 Hatchet 实例，Windows 原生进程 PID6396 连接真实引擎，完成 `bridge-native-0/1/2` 三个不同合成任务。三个结果均报告 `win32`、同一原生 PID 和预期标识。
5. 使用公开生命周期API正常停止 Windows Worker，监听子进程 PID8712 退出码0，无残余活动子进程；不是将强杀当作正常退出。
6. 引擎退出码0；数据库进程、pidfile、临时目录均清理。外层发行版清理及 always-cleanup 均无错误。

## 网络与凭据边界

引擎运行及Worker运行时，相对启动前的新增监听仅为：

- PostgreSQL：127.0.0.1:55479
- gRPC：127.0.0.1:55480

停止后的新增监听集合为空。REST、指标、SDK健康检查未开启；未建立公网端点或隧道。
临时数据库密码不放入命令参数，测试令牌仅经私有进程管道转交；数据库、令牌和私有日志
未上传。独立日志审查未发现JWT形式令牌输出；这不是一般化的零泄漏证明。

## 独立复核与持久证据

独立复核确认该提交、任务结果、退出码、监听变化和清理记录一致，限定验收 PASS。
[脱敏结构化结果](evidence/20261020-native-windows-engine.json) 从本次 CI job 标准输出解析保存，
注明原运行与提交。它不是原始 artifact ZIP 的逐字节副本，也不声称替代全部运行日志。

## 明确未验收

- 真实远端网络及 TLS、节点最小权限/撤销。
- REST暂停、任务执行中排空、断网/崩溃重投、租约到期/恢复。
- 其他 Windows 版本、睡眠唤醒、磁盘不足和持续资源压力。
- Windows上的完整Bridge HTTP/输入上传/产物/收据链路。
- Codex/Midjourney真实生成、Alchemy原生接入与审核/计费。
- 生产部署和整个RB-01/第一阶段验收。

下一开发增量应优先将已有Bridge领域账本与实际节点链路接合，并补真实故障恢复验证；
TLS及权限模型需独立可审计设计，不从本机成功自动推导远端安全。Alchemy与Bridge仍保持独立部署、仅API通信。
