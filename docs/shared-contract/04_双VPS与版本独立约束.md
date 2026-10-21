# 同机首测、双VPS与Alchemy版本独立约束

2026-10-21更新。2026-10-20双VPS安排保留为后续部署选项；用户最新要求把首轮完整测试放在同一台Windows电脑。覆盖的是物理位置，不是HTTP合同、独立状态或版本中立边界。

## 当前首轮测试：同一台用户Windows电脑

本地Alchemy、Render Bridge服务/任务引擎、Agent和用户自己的Codex CLI分别运行。两服务通过固定loopback HTTP互通，状态、数据库、输入和产物目录独立；工具登录凭据仍留在本地工具，不导入Alchemy/Bridge配置。引擎是独立运行服务，不把pip安装Hatchet SDK等同于引擎已运行；具体启动与更新步骤见本机部署文档。

loopback例外仅适用于该受控开发测试。暴露到局域网/公网或跨主机仍须TLS、身份验证和原安全门禁。两个服务独立升级，不以同机为理由直接读取彼此目录/数据库。

## 后续双VPS：三个位置

1. Alchemy VPS：原业务系统+默认关闭的可选接入模块，各版本的业务适配都在这里。
2. Render Bridge VPS：独立API、任务引擎、任务/产物存储，只负责通用执行合同。
3. 用户电脑：Agent及已登录的Codex CLI、Discord；不把个人登录凭据上传两个VPS。

Alchemy调用Bridge API；Agent主动连接Bridge及已批准的工具服务。Alchemy不直接操作用户桌面、Hatchet或工具内部接口。两个VPS无共享DB、磁盘挂载、Python内部类或双写业务表。

## 统一输入、统一输出

输入不是只有一段字符串。最少包括：最终prompt、执行profile、operation、必要参数、参考图及其顺序/用途、稳定任务键和结果槽。纯文生图可没有参考图；参考图生图不能静默丢图。

Bridge不得重新创作或“优化”prompt；Adapter只能做必要的协议包装/参数映射。保存原prompt/hash与实际工具输入的关联，平台不支持的参数应拒绝或待用户明确修改，不能默默删掉。无法验证工具内部是否改写时应标记边界，不能宣称像素或prompt忠实度已获证明。

输出统一为任务状态、原始产物、hash与实际执行信息。Bridge不理解V1 session、V2 run、V3 candidate、Lab变体或Video OS审批状态；binding仅为不透明值。原业务归属、审核和交付由Alchemy适配层及原有服务掌握。

## 升级独立

Alchemy独立升级只要保持约定API语义，就不要求Bridge同步修改。协议字段/能力变化必须版本化协商；未知operation/profile拒绝，而不是让Bridge猜某Alchemy版本的新语义。

新增一个Alchemy版本：在Alchemy侧写新的薄适配。新增一个执行工具：在Bridge侧写Adapter。只有通用合同增加能力时才协调协议升级，不能由此合并两个系统。

## 架构拒绝清单

- Bridge读取Alchemy数据库或业务文件路径。
- Bridge调用Alchemy内部计费、审核、candidate创建函数。
- Alchemy引入Hatchet SDK、节点执行器、工具登录凭据。
- 用共享Redis/DB表作为两端隐藏通信通道。
- 按`if alchemy_version == v3`在Bridge内实现业务分支。
- Bridge不可达导致原API-only路径启动失败或退化。

## 联合验证

分别运行在两个独立服务地址/隔离数据卷中；Mock原生版本请求映射后得到同类Bridge合同。检测网络只经过批准API，停Bridge后旧Alchemy照常工作。改变Alchemy内部版本/模型目录但不改Bridge合同，Bridge合同回归仍通过。

同机测试需要实际跑通本地Alchemy → Bridge → 本人Codex CLI → 原图回传 → Alchemy审核，不能用fake-only演示冒充。云端自动测试与历史WSL引擎试验不能代替这次实机验收；双VPS生产部署仍未由本文宣称完成。
