# 首批场景一：本机已登录Codex CLI

状态：设计，未调用真实Codex或生成图片。Bridge通用链路之后优先实现。

## 1. 目标

Agent在用户电脑上调用已安装、已登录的Codex CLI，将统一最终生成请求交给它执行，收集本次任务的原图，再通过Bridge API回传。Alchemy不知道其CLI命令或事件结构。

官方图像文档描述交互式CLI图像生成及引用图；app-server文档提供本地stdio协议。它们不能自动证明用户当前版本的非交互生图、产物事件、恢复和取消已可用。因此第一步是版本与能力探测，不假设每个Codex版本都有相同字段。

来源（2026-10-20核查）：
- https://learn.chatgpt.com/docs/image-generation
- https://learn.chatgpt.com/docs/app-server

## 2. 推荐接入

优先用本机`codex app-server`的stdio受控子进程，生成并固定该CLI版本对应的协议schema；不暴露远程app-server端口。这个模式仍调用本地Codex，不切换成另一套云端API账户。

若当前版本无法稳定返回图像文件/事件，记录阻断，不靠解析“已生图”文本冒充成功。必要时评估官方CLI执行模式，但不将交互式可用当作exec模式自动可用。

可局部借用MIT第三方codex-native-bridge的协议客户端/收集器，不整体导入其多Agent平台，也不把第三方组件当必须安装的前置。

## 3. 本地执行约束

- 使用用户已有登录环境；凭据保持本地，不提取到Bridge或Alchemy，不改全局模型/provider配置。
- 每attempt独立任务目录，只放本次授权参考素材与输出；不把用户代码项目当工作目录。
- 最终prompt保持原义，参考图按批准协议传入；仅在prompt写路径不能视作引用能力验证。
- 限制允许工具与读写目录，不开启任意shell/网络/项目修改能力来换取自动成功；出现额外审批转needs_attention。
- 绑定thread/turn及产物事件；对本次原文件做真实媒体、路径、hash验证。不能取“目录最新图片”或截屏作为结果。
- 实际模型可验证则记录，否则标unknown；不能用UI名称猜实际图片模型。

## 4. 恢复和取消

start许可签出后进入可能执行状态。子进程退出不证明远端生成没发生；断线无结果时查询同thread/turn或置unknown，不另开turn重试。终止进程树、取消turn与上游已停止分别记录。

已生成原图应先持久化到任务目录再回传；上传失败只重传。CLI升级可能改变事件协议，必须重新通过兼容测试才更新profile revision。

## 5. 适配验收

固定CLI版本/OS/登录类型；文生图、支持时的参考图输入；仅文本回应、产物事件缺失、越界路径、进程崩溃、取消、登录过期、额度不足、上传失败恢复。start次数和原图归属可核对，不能凭最终看见图片推断完整可靠。

真实生成会使用账号额度，测试前单独确认测试数量与费用范围。首版不需要现在交出密码或登录材料。Codex能力验证失败不允许偷偷改走Sub2API。

## 6. 2026-10-26：Codex ImageGen 参数适配器

以下固定依据为 OpenAI Codex CLI `rust-v0.163.0` 的内置 image-generation tool
schema/实现，不把 OpenAI Images API 的字段误当作 Codex CLI 的直接工具参数：

- 原生参数：`prompt`、`transparent_background`（0.163.0 新增，默认 `false`）、
  `referenced_image_paths`（最多 5 个绝对路径）、
  `num_last_images_to_include`（1–5）。两种参考图模式不能同时使用；Alchemy
  适配固定使用显式路径，不依赖易变化的“最近图片”窗口。
- Codex 工具内部固定 `model=gpt-image-2`、`quality=auto`、`size=auto`、单张输出；
  背景由 `transparent_background` 映射为 transparent/opaque。
- 工具没有可直接指定的 quality、size、aspect ratio、width/height、seed、
  output format/compression 或 input_fidelity 参数。GPT Image 2 的输入 fidelity
  默认 high，且官方说明该模型不允许通过 API 改成其它等级。
- 0.157.1 接受显式参考路径，但没有 `transparent_background` 字段；该版本
  内部请求使用 `background=auto`。透明意图在该版本省略透明字段，归到 CLI 的
  `auto` 默认。

Alchemy 适配在保留原始请求副本（供 Bridge 明确证明未受理时的传统 Provider
回退）基础上，将质量、尺寸/比例、格式/压缩、seed 归到上述工具默认；V2 的
count 仍拆成每个 Bridge task 单张。透明意图在 0.163.0 指令为
`transparent_background=true`；0.157.1 则归到 CLI 默认 `auto`。当前 app-server
接口由 Codex 根据执行器的开发指令生成 imagegen 工具调用，Bridge 无法直接注入
函数调用 JSON；透明选项与参考路径是明确指令，非 Bridge 自己构造的原生调用。
执行器把最多五张已哈希校验的本地参考路径作为 `localImage` 输入，并指示 Codex
显式使用 `referenced_image_paths`，不依赖最近图片窗口。
实际探测到的 CLI 版本和透明字段的生效归一结果保存在本地
`codex-completion.json`/执行器结果中；Agent 上传收据只证明原图字节，不把这类参数
执行细节投影为云端能力证明。
超过五张参考图不截断、不提交。参数归一不会改变 prompt、参考图内容/顺序、Bridge
任务身份、返回原图字节/MIME、产品审核或计费；只会影响以后创建的新 Bridge 请求。

权威源码：[Codex v0.163.0 image-generation tool](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/ext/image-generation/src/tool.rs)。
GPT Image 2 fidelity 说明：[OpenAI Image Generation 文档](https://developers.openai.com/api/docs/guides/image-generation)。
