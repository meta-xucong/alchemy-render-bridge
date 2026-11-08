# WorkBuddy 单用户 local-trial 扩展

`HarnessConfig.execution_mode` 默认 `synthetic`，保持原 synthetic-/fake- 绑定门禁。显式 `workbuddy_local_trial` 才允许 `workbuddy-*` 别名及非 fake- 原生模型标识，并要求 exactly one `allowed_owners`。

仍为专用 Harness ledger/node/executor，不能与 image runner 混用。新任务仅一条 user 文本；多轮消息在写任务之前拒绝。该检查位于已有任务回放之后，不阻断原任务结果查询。单槽、心跳、冻结 binding、claim/start intent、未知执行保守占槽均未改变。

示意（不是实际模型配置）：

```json
{"execution_mode":"workbuddy_local_trial","allowed_owners":[["user-7","key-9"]],"accepting_new":false}
```

需连同原必填 source/workspace/ledger/node/executor/bindings 配置使用，模型必须由本地原生回读确认。这里只验账本门禁和自写执行替身，不证明真实账号/模型/商业使用可用。默认不部署、不收费。
