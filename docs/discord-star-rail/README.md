# Discord 星穹小队配置

这是个人角色配置示例，复用 Hermes Profiles、多 Profile Gateway、Discord 的明确提及门槛和 Kanban。无新 Bot 框架或共享聊天数据库。

`roster.yaml` 保存七位角色的名字、背景摘要、性格、分工和语气示例；`TEAM.md` 保存协作规则。SOUL.md 由这些内容组合。分工和说话方式是为助手用途作出的演绎，不能当作官方剧情。

现有 Bot 对应 default（卡芙卡），另外六个 Profile 分别使用六个独立 Discord Application/Bot Token。各 Profile 自己保存模型配置、凭据、SOUL.md、记忆和 state.db。只复制模型配置和所需模型凭据，不复制已有会话、记忆、自动化任务或其他平台登录。

所有 Profile 的 `gateway.multiplex_profiles` 设为 true，`gateway.multiplex_profile_allowlist` 指定这六个 Profile；仅从 default 启动 Gateway。Discord 使用 `require_mention: true`、`thread_require_mention: true`、`auto_thread: true`，继承原用户 allowlist。保持 `DISCORD_ALLOW_BOTS=none`，不靠 Bot 互相回复派单。自动化 Thread 的拥有者仍支持用户免 @ 继续该任务。

启用原生 kanban toolset；default 的 `kanban.dispatch_in_gateway`、`kanban.auto_subscribe_on_create` 和 `kanban.discord_worker_updates` 均为 true。最后一个选项默认关闭，仅对有 Thread 的 Discord 订阅生效：领取、明确以 `[progress] ` 开头的评论、完成与阻塞由执行角色自己的已连接适配器发送；该角色不可用时注明代转。普通评论不公开。实际执行事件决定身份，完成事件仍唤醒原订阅所属的发起者，不能把唤醒上下文切到执行者。沿用现有事件游标、失败重试和 SQLite 存储，无新增映射表。不要给六个 Profile 复制 default 的定时任务。

`TEAM.md` 规定卡芙卡主动派单的条件：简单状态查询可自行处理，深入研究交黑塔，代码与网络排查交银狼。角色决策仍由模型遵循指令；一旦成功创建任务，分派、持久化、公开事件投递和完成唤醒由原生看板负责，不依赖 Bot 聊天接龙。公开进展不唤醒卡芙卡，避免每次更新又开一轮模型调用。

修改 `TEAM.md` 后，同步到七份 `SOUL.md` 的团队段落，保留角色身份、记忆和其他内容。新会话读取新规则；已有会话保持缓存提示词，不覆盖历史。旧 Thread 要立即采用新规则，可在正常的新消息中要求按更新后的团队规则继续，无需清空历史。

验收使用真实任务，不在测试问题里指定执行者：向卡芙卡询问需要排查的故障，检查她在深入调查前创建指向银狼的看板任务；银狼真正执行，其 Bot 在同一个 Thread 展示进展和结果；卡芙卡被内部完成事件唤醒后汇总。另开研究任务验证黑塔路由及不同 Thread 的隔离。分别 @ 七个 Bot 做自我介绍不能证明主动协作。

方案依据：[Hermes 官方 Kanban](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban) 已提供持久任务、角色执行与原会话 `notify+wake`；[官方 Discord 文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord/) 不支持用多 Bot 回复接龙做调度。[OpenClaw 多 Agent](https://docs.openclaw.ai/multi-agent) 也区分角色身份与内部会话通信。此处复用 Hermes 自身实现，未引入另一套 Agent 框架。

在 Discord Developer Portal 为新增角色创建应用，获取各自的 Bot Token，启用 Message Content Intent（以及 Hermes 使用的 Server Members Intent），邀请进自己的服务器。权限需要读取频道、发送消息、读历史、创建公开 Thread、在线程中发送消息、附件和嵌入链接。不需要 Administrator。每个 Token 只写入对应 Profile 的 `.env`，不要提交到仓库。

将 `discord-team.json` 放到各 Profile 目录，结构为成员数组：每项包含 `profile`、`name`、`bot_id`、`connected`。尚未接入的成员用 null 和 false；不要把未上线成员显示为已上线。头像放在原生 `assets/avatar.png`。

角色资料参考：[流萤官方介绍](https://www.hoyolab.com/article/29770753)、[昔涟官方角色短片](https://www.hoyolab.com/article/42120092)、[遐蝶官方角色前瞻](https://www.hoyolab.com/article_pre/18014398241027732)、[游戏资料汇总](https://honkai-star-rail.fandom.com/wiki/Character)。头像使用 [StarRailRes 的游戏头像资源](https://github.com/Mar-7th/StarRailRes)；游戏角色及素材权利归原权利人，本示例不重新分发图片。
