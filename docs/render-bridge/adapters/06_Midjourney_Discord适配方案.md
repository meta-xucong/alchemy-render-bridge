# 首批场景二：Midjourney人工执行 + Discord官方消息API

2026-10-20。用户选择：自己在Discord提交Midjourney任务，获取原图后导入对应任务；优先用Discord API传递任务消息以降低输入错误。

## 1. 两个不同动作

A. 把任务编号、prompt和参数发送到Discord频道：官方Incoming Webhook或Bot消息API可以完成，不需要模拟键鼠。

B. 以个人账号调用Midjourney的/imagine并执行：普通消息API不是其他应用Slash Command调用接口。把`/imagine ...`作为content发送，不等于真正提交该命令。本方案B由用户手动完成。

不使用用户token selfbot、不假造Discord客户端交互、不用键鼠脚本替代同样受限的自动调用。Midjourney如将来提供明确授权接口，再单独设计自动执行Adapter。

依据：
- Discord Webhook：https://docs.discord.com/developers/resources/webhook
- Discord消息：https://docs.discord.com/developers/resources/message
- Discord个人账号自动化边界：https://support.discord.com/hc/en-us/articles/115002192352-Automated-User-Accounts-Self-Bots
- Midjourney自动化/少量明确授权例外：https://docs.midjourney.com/hc/en-us/articles/32013696484109-Community-Guidelines

## 2. 最小实施路线

首选用户管理的专用服务器频道Incoming Webhook，仅投送任务资料；若没有频道管理权限，可后续评估已授权Bot。不能假定Webhook可向任意私聊或任意MJ官方频道发消息。

本地Agent内的Discord任务投送组件保存Webhook秘密，Bridge VPS仅提供任务合同，不接收Discord个人凭据。凭据配置和频道权限在实际安装阶段确认；当前设计不创建Webhook或发送真实内容。实际配置前确认目标频道及可见人群、允许发送的任务内容范围；send-only Webhook不申请消息历史读取、Message Content intent、管理员权限或个人Discord token。

流程：
1. Bridge分配manual profile任务，Agent建立本地任务记录。
2. 通过Discord官方API发送普通任务卡片：task/attempt编号、最终prompt、参数、参考资料说明。
3. 用户在Discord选择Midjourney的/imagine，复制完整prompt及适用参数，并按合同顺序/用途手动附上获准参考图，核对后确认提交；Agent显示awaiting_user_submission/completion。
参考图模式只有对应输入流程通过验收才开放；未确认平台支持的参考用途/数量必须拒绝，不允许退化为纯文生图。
4. 用户完成必要的单图选择/放大，下载原文件，在Agent选择对应task并导入。
5. Agent校验图片、记录用户声明的来源/关联信息并明确人工来源，不假称平台签名证明；通过既有upload/commit回传。
6. Alchemy按统一API取回图片，继续自己的权限/审核/交付。

## 3. 降低操作错误的具体约束

- 不仅发prompt：卡片显示唯一task/attempt、profile、尺寸/参数与引用顺序。源prompt字节/hash保留；工具不支持的参数明确提示，不擅自翻译为另一种效果。
- Discord普通content上限按官方当前限制处理（当前2000字符）；超长prompt不能截断。建议发送短摘要与完整UTF-8文本附件/本地复制入口；发送后的消息不是原始合同权威。
- 禁止意外@everyone等提及，设置allowed_mentions为空；引用图只发用户明确授权给该频道的内容，不把全部项目素材带出。
- API返回消息ID后持久记录；使用`wait=true`请求返回已创建消息。响应丢失可能已发出，不能无界重发；置notification_unknown并让用户核对卡片，不因此新建生成attempt。
- 普通通知与用户提交状态分开。发送消息成功不等于MJ已接单，用户点击“已提交”也仅是人工声明。
- 原图导入必须选择task且核对缩略图/文件属性，不扫描全局下载目录最近文件。四宫格不能默认为四个独立输出；首版每slot一个用户确认的原图。

## 4. 状态与容量

manual profile具备requires_human=true、execution_mode=manual，自动能力不冒充已通过。对外API增加可选action_required及明确code，不能把等待人操作伪装为自动running。

人工待办用独立有界manual-pending计数，不长时间占用GPU/Codex物理槽或Hatchet callback；引擎负责投送唤醒，人工恢复按同attempt查账本，不建立第二个执行调度器。manual等待到期后禁止自动再提交或切Provider，原图迟到按统一取消/提交规则处理。

取消只能关闭Bridge待办/请求用户停止。若用户已在MJ提交，不能声称Bridge已取消上游；用户导入不改变先前已取消任务的交付权限。

## 5. 测试

先Mock Discord API：正常、429/Retry-After、5xx、响应丢失、重复通知、2000字符边界、完整附件hash、mention抑制、错task导入、四宫格/多图、取消后迟到、人工等待不占Codex槽。

真实验收：在用户批准频道投送无敏感测试卡片；用户手动生成并导入一张原图；两端按同receipt确认一次。每次真实MJ生成涉及账号额度，测试数量另行确认。

Discord API传消息与Midjourney自动执行分别判定；不能将前者通过写成“Midjourney全自动API已打通”。
