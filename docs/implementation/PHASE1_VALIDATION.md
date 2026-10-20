# Stage 1A 合同原型验收记录

2026-10-20 UTC。基线73dd31209f5b7b85b7f6f4363e795ae8f07cf535；实现分支feat/phase1-bridge-contracts。

## 结论

**隔离合同原型范围PASS，可以提交代码审查；整个第一阶段及生产部署尚未放行。** 本次没有修改Alchemy、创建运行授权、调用真实工具、发送Discord消息或部署VPS。

Hatchet运行/Windows、生产授权和真实业务导入不是此PASS的组成部分，见两个preflight报告。此拆分公开表示可交付的独立增量，不将未完成项换名计为通过。

## 实际验证

| 命令/检查 | 结果 |
|---|---|
| `uv lock` / `uv sync --locked --extra test` | 成功；隔离venv、固定依赖 |
| `.venv/bin/python -m unittest discover -s tests -v` | 根执行57通过、0失败、0跳过，6.447秒 |
| 同一锁定venv独立复审重跑 | 57通过、0失败、0跳过，9.433秒 |
| `.venv/bin/python -m compileall -q src tests scripts` | 退出0 |
| `.venv/bin/python scripts/export_contracts.py` | 退出0；独立核对3份schema与当前模型一致 |
| `uv build` | sdist和wheel构建成功；非部署包验收 |
| `pip-audit --disable-pip --no-deps -r requirements-test.txt`（可写cache） | 退出0，未发现已知依赖漏洞；不是源码无漏洞证明 |
| `git diff --check` | 退出0 |

早期运行发现并修复夹具Content-Type/remote_task_id错配、Fake默认尺寸、origin键恢复路径、快照一致性、quota边界恢复、ack schema空值等问题。旧失败不作为最终通过依据，最终重新运行以上检查。

## 独立审计修复

1. origin_key可含斜杠/点导致URL恢复失败：现在只接受明确ASCII安全键，边界HTTP回归通过；binding仍不透明。
2. 多次SELECT拼出不同时间状态：复合读取使用事务快照，并发commit回归通过。
3. 写文件后DB回滚，在存储满额时无法复用同一孤儿文件：验证并复用精确同字节不可变对象；损坏/符号链接仍拒绝。
4. ack导出schema允许null但运行时拒绝：统一为可省略、不可null，生成schema与HTTP测试一致。
5. API同步IO：单操作有界线程准入、忙时503/Retry-After；HTTP取消不会提前释放仍运行的线程容量。

## 覆盖的闭环

模拟Alchemy HTTP创建/上传输入→创建task→内部测试驱动领取/start→Fake原图→上传/commit→HTTP取回manifest与文件→幂等ack。独立start日志验证重复投递100次不会再次执行，非仅检查最终图片数量。

还覆盖跨source/workspace、参数篡改、严格类型、重复JSON键、实际字节/媒体/hash、输入固定、状态持久化、旧claim、unknown占槽、取消/commit竞争、上传与登记重复确认。没有node/source混权HTTP路由。

## 源码身份

计算范围：src/render_bridge/*.py、tests/*.py、scripts/*.py、contracts/v1/*.json、pyproject.toml、uv.lock、requirements-test.txt、.github/workflows/contracts.yml（16文件）。按相对路径排序，依次拼接path + NUL + bytes + NUL，SHA256：

`3b00d231f00a4ba7cfca0dbc90d56b3a4377949dd2b04a34f8b75fd5832b45b2`

独立核对的实现摘要：
- domain.py：d1e9ced8319699a39732aef80d244ff4d1a7031748c1bf6a18be72ac11e63625
- api.py：9acedcb1b5d077838f8669e6e7a1a85b56bceef7965679567637da7e6d35438e
- schemas.py：043cc313cc0d004ce93ea304a5d7580efe9905a3468b52955360def7c77c7c72
- fake.py：4c5144a1992caf16ab74514f65cd664e4c73ccf161ca87fa5dd5a9e6b266d71f

## 首轮未通过的门禁（后续状态见下方更新）

- Hatchet embedded真实启动：需要本地临时授权状态创建许可，未执行；不是没有Docker就否决产品。
- Hatchet SDK 1.42.1 Windows信号处理：存在源码/隔离方法复现的SIGQUIT兼容问题，不能以下载Windows轮子代替运行验收。
- Windows合同CI：待本提交远端实际运行；通过也只证明Python合同测试，不证明Windows Agent或Hatchet工作。
- 正式Agent/任务引擎、PostgreSQL领域存储、真实TLS与凭据管理未实现。
- Alchemy通用远端导入接缝未具备；现有loopback MCP路径不能冒用为跨VPSBridge接口。
- Codex、Midjourney、Discord真实调用、生成质量、付费/额度、原生业务审核/计费均未验证。

PR应保持Draft。现阶段产物仅用于隔离开发与审查，不提供公网服务启动说明。

## 2026-10-20 Windows CI 兼容性修复

首次远端运行 [39374589251](https://github.com/meta-xucong/alchemy-render-bridge/actions/runs/39374589251)
针对提交 `5c63d6c07b4ab2a9789a0a2292dcd6d02b5bb8bb`：Linux 通过；Windows
运行 57 项测试，2 项失败、15 项错误。根因是 Windows CRT 不支持用
`os.open` 打开目录再 `fsync`，不能把云端 Linux 通过解释为跨平台通过。

修复保留文件 `fsync` 和同目录替换；POSIX 仍强制同步目录并传播失败。
Windows 显式不调用不支持的目录同步。对已有孤儿对象的重试也重新执行
POSIX 目录屏障，避免前一次屏障失败后仅凭文件已存在就返回成功。
读取使用二进制标志，结合 `lstat`、`fstat` 和文件身份检查拒绝符号链接及
读取前发生的文件替换，保留 hash 校验。

增加 5 项回归，云端 Linux 完整 62 项通过、零跳过；独立复审同样 62 项通过、零跳过（5.896 秒），限定代码审计 PASS。真实 Windows CI
必须在修复提交推送后重新验证，旧失败运行不可记为通过。

**限制：Windows 仅作为隔离合同原型的功能测试目标。本实现不承诺 Windows
断电后的目录项持久性；ACL 隐私、Windows Agent、Hatchet Worker 和真实设备
崩溃恢复均未验收。未关闭或跳过功能测试，也未改变 Linux 的目录同步要求。**

上文源码指纹只适用于首次提交，不能用于本修复的代码身份；新提交及其远端
CI 为本修复的精确身份依据。

### 修复后的真实双平台 CI

提交 `5d2629b8d832816f848f4e2be18e8c820dd35482` 的运行
[39374964684](https://github.com/meta-xucong/alchemy-render-bridge/actions/runs/39374964684)
已完成且成功：Linux 62 项通过（2.349 秒）；Windows 62 项通过（14.255 秒）。
两平台 compileall 和 diff 检查通过。该结果只覆盖合同测试，不扩大上述存储、
Windows Agent 或 Hatchet 验收范围。
