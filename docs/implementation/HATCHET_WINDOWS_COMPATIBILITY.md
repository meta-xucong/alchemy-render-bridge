# Hatchet 原生 Windows Worker 兼容性门禁

2026-10-20，只读源码核查。**未验收，不影响合同测试的Windows通过结论。**

官方 SDK 1.42.1 至少有两处不同的平台阻断：

1. Worker构造无条件引用Unix专属SIGQUIT；handle_kill=False不能关闭注册。
2. Action listener无条件调用loop.add_signal_handler；Windows的asyncio事件循环不支持此方法，切换SelectorEventLoop也不能解决。

截至本次核查，PyPI最新仍为1.42.1，官方main
`392e7c472221cdab235cca674640e98f904e1ea0` 也保留上述代码。
这不是Hatchet架构不可行的证据，但禁止把Linux或合同CI结果冒充Windows Worker可用。

## 最小待审实现方案

- 对固定SDK源码提供可审计的平台保护补丁，构建有版本和hash的wheel，不做运行时monkeypatch。
- 仅当SIGQUIT存在时注册它，保留SIGINT/SIGTERM处理。
- listener的Windows主线程分支使用signal.signal注册同用途处理器；POSIX保持现状。
- 保持原Event/STOP_LOOP控制协议；若允许旧引擎fallback，同步处理deprecated listener，或明确拒绝旧路径。
- 保留MIT版权/许可和补丁来源，每次SDK升级重新验证。

以上为候选设计，**未实施，未做Windows生命周期测试**，不能据此认定修复必然充分。

## 验收层次

1. GitHub Windows CI：原版失败及补丁版构造成功对照；真实spawn、Queue/Event、停止事件、超时join和清理。
2. mock transport只能证明平台生命周期，不能证明真实引擎任务链。
3. Windows的Ctrl+C与程序退出分开验证，不照搬Linux SIGTERM试验。
4. 最终必须让原生Windows Worker连接真实Linux引擎，验证任务、断线、在途退出和重连。
   这需要额外的受控跨环境测试设施；本次只允许回环的embedded试验不能代替。

## 一手依据

- [SDK 1.42.1](https://pypi.org/project/hatchet-sdk/1.42.1/)
- [固定Worker源码](https://github.com/hatchet-dev/hatchet/blob/19a3474b7963139124151e7a3b1357b1971c2eb8/sdks/python/hatchet_sdk/worker/worker.py#L77-L145)
- [固定listener源码](https://github.com/hatchet-dev/hatchet/blob/19a3474b7963139124151e7a3b1357b1971c2eb8/sdks/python/hatchet_sdk/worker/action_listener_process.py#L132-L136)
- [Python Windows asyncio限制](https://docs.python.org/3.12/library/asyncio-platforms.html#windows)
- [Windows os.kill行为](https://docs.python.org/3.12/library/os.html#os.kill)
