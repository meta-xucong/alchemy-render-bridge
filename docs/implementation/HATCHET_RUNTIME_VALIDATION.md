# Hatchet Linux 隔离运行验证

2026-10-20。范围：临时 Linux 本机环境，不接生产或真实 Provider。

## 结论

**Linux 引擎/SDK 冒烟测试通过；RB-01 整体未验收，Hatchet 仍是候选。**

05:30:29 UTC 开始的最终留证运行，真实 Hatchet 引擎向单槽 Python Worker
派发3个不同的合成任务，返回结果均与唯一标识精确一致。
这不是 Bridge 账本/FakeRenderer 的集成验收，也没有生成真实图片。

## 固定版本

- Python SDK：1.42.1。
- Embedded 引擎：v0.110.5，二进制 SHA-256：
  `18ddacae0005042bd982328bcb8d370cca6907352a1ab2a3c8406001d31e7dee`。
- PostgreSQL：运行日志确认为18.3；其自动下载包尚未单独锁定哈希。
- 临时引导工具 pg8000 1.31.5；未加入 Bridge 运行依赖。

## 真实验证与安全边界

- REST、指标和健康检查关闭；gRPC、PostgreSQL只监听回环地址。
- 50次约1秒间隔的采样没有出现新增通配监听。
- 使用独立临时 HOME、TMPDIR、数据库；没有使用生产配置或真实工具账号。
- 沙箱不支持默认 Unix socket，改为该临时数据库的回环 TCP，未改全局设置。
- 初始化中断后嵌入式库未创建应用数据库，通过本机连接补齐临时库后启动成功；这是实验适配，不是可发布安装器。
- 完成任务后先让 Worker 退出，再停止引擎，正常结束 Python；退出码0。
- 清理前活跃测试进程为0，未发送强制清理信号；停止后监听恢复初始采样集合。初始采样发生在启动子进程后，不能称为严格的启动前快照。
- 临时数据库及其中测试管理员、签名材料和令牌均已销毁。

## 关闭行为的准确范围

初次“先停引擎”的试验产生重连和 semaphore 警告。修正关闭顺序后这些警告消失。
但 REST 被禁用，服务端暂停调用明确失败；关闭流时仍有中断监听警告。
因此仅确认“已完成任务后的有序停止”，不确认服务端暂停、在途排空或暂停竞争安全。

## 未验收事项

Windows 原生 Worker、SIGQUIT 兼容性、TLS、节点最小权限与撤销、真实网络重投、
崩溃恢复、磁盘不足、持续资源基线及双 VPS 联调均未验收。
采样只覆盖部分进程组，不包括全部 PostgreSQL 进程，不可据此推算 VPS 容量。

## 可追溯证据

本轮隔离验证的脱敏结果与脚本身份：

- 合成任务脚本 SHA-256：`302342266b66ef7d88feb3a55686c3e7e85d85084e9c8395096da2c2948a81ef`。
- 隔离运行器 SHA-256：`f213526610ea778ea38250d0b9b2b414d16b179d5e8a2d3669a13a3ff482d5cf`。
- 结果 JSON SHA-256：`5b9798da11c2660e60fa3ec51db3e761a332cc76d31f14dddd267020da822083`。

以上记录是本次实验的可追溯身份，不表示自动下载链已实现可重现构建。
此仓库不包含临时凭据或原始运行目录。

一手版本依据：
[embedded v0.110.5](https://github.com/hatchet-dev/hatchet-embedded/releases/tag/v0.110.5)、
[固定引擎启动源码](https://github.com/hatchet-dev/hatchet-embedded/blob/3dc26f939dfc5c1072c35dedc65bf51313d665a7/embed.go)、
[固定 SDK Worker 源码](https://github.com/hatchet-dev/hatchet/blob/19a3474b7963139124151e7a3b1357b1971c2eb8/sdks/python/hatchet_sdk/worker/worker.py)。

## 实际引擎投递 + Bridge start 锁存集成试验

05:33:26 UTC 开始，使用 Bridge 提交
`5d2629b8d832816f848f4e2be18e8c820dd35482` 的现有领域代码，只读导入，
不改生产调度器。单个 Worker/单槽，临时 SQLite，claim 租约600秒且最终检查未过期。

- 100个不同的真实 Hatchet run 携带同一 Bridge task/attempt/claim；100个结果全部返回，
  run ID 集合与提交引用完全匹配，投递索引完整为0–99。
- 仅1个结果获准启动；其余99个均拒绝再次启动，start grant 相同。
- 仅获准的回调向独立假启动日志追加并 fsync；所有结果返回后日志精确只有1条。
- 首个回调主动注入 execution_unknown 后返回。Hatchet回调槽已空闲，但后续另一个
  Bridge任务仍收到 physical_slot_busy，没有新grant或第二条启动记录。
- 最后再投递原attempt仍不能启动；合计102个不同的引擎run，有对应开始/完成记录。
- 正常停止退出0，清理前无活跃测试进程，不需强制清理；临时数据库及认证材料销毁。

**限定通过：真实引擎承载的人工重复投递与显式 unknown 注入。**
这不是引擎自身断网重投、真实崩溃恢复或通用“恰好一次”保证；没有调用真实渲染进程，
也未覆盖HTTP/上传/收据链路。原始临时假启动日志随环境销毁，结果保留精确解码条目，
没有另外保存原始日志字节hash。

证据身份：
- 测试脚本：`4cbef6906bad0579a253de9e171862608267ae0286137646a37be46e21796d14`。
- 隔离运行器：`14182dd7b20892d9fa35cbfefdcc8eeef144295000d871f06143e579f8c95ea8`。
- 脱敏结果JSON：`50f2d129c02507d56a0564f670eb3ca0ee384be043f57da993ead056f5938ba5`。
- 最终脱敏账本快照：`95f5d511be40e79c218c7cd50b9195a9e435e8d9a11d7ebb52502f16c0d45b06`。

RB-01及第一阶段整体仍未验收。真实故障重投、租约到期恢复身份、撤销、取消竞争、
TLS、原生Windows和Alchemy/API联调继续保留为独立未完成项。

以上两项限定试验已完成独立证据复核：结果与日志ID、账本、源码身份和清理记录一致。复核未重新执行引擎；没有扩大试验本身的覆盖范围。
