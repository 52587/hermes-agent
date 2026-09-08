# Discord 星穹小队配置

这是个人角色配置示例，复用 Hermes Profiles、多 Profile Gateway、Discord 的明确提及门槛和 Kanban。无新 Bot 框架或共享聊天数据库。

`roster.yaml` 保存七位角色的名字、背景摘要、性格、分工和语气示例；`TEAM.md` 保存协作规则。SOUL.md 由这些内容组合。分工和说话方式是为助手用途作出的演绎，不能当作官方剧情。

现有 Bot 对应 default（卡芙卡），另外六个 Profile 分别使用六个独立 Discord Application/Bot Token。各 Profile 自己保存模型配置、凭据、SOUL.md、记忆和 state.db。只复制模型配置和所需模型凭据，不复制已有会话、记忆、自动化任务或其他平台登录。

所有 Profile 的 `gateway.multiplex_profiles` 设为 true，`gateway.multiplex_profile_allowlist` 指定这六个 Profile；仅从 default 启动 Gateway。Discord 配置使用 `require_mention: true`、`thread_require_mention: true`、`bots_require_inline_mention: true`。原生 `DISCORD_ALLOW_BOTS=mentions` 允许明确 @ 的跨 Bot 交接；继承原用户 allowlist。自动化 Thread 的拥有者仍支持用户免 @ 继续该任务。

启用原生 kanban toolset 后，较长任务可按 Profile 指派并通过原任务通知回到 Thread。不要给六个 Profile 复制 default 的定时任务，否则会重复执行。

在 Discord Developer Portal 为新增角色创建应用，获取各自的 Bot Token，启用 Message Content Intent（以及 Hermes 使用的 Server Members Intent），邀请进自己的服务器。权限需要读取频道、发送消息、读历史、创建公开 Thread、在线程中发送消息、附件和嵌入链接。不需要 Administrator。每个 Token 只写入对应 Profile 的 `.env`，不要提交到仓库。

将 `discord-team.json` 放到各 Profile 目录，结构为成员数组：每项包含 `profile`、`name`、`bot_id`、`connected`。尚未接入的成员用 null 和 false；不要把未上线成员显示为已上线。头像放在原生 `assets/avatar.png`。

角色资料参考：[流萤官方介绍](https://www.hoyolab.com/article/29770753)、[昔涟官方角色短片](https://www.hoyolab.com/article/42120092)、[遐蝶官方角色前瞻](https://www.hoyolab.com/article_pre/18014398241027732)、[游戏资料汇总](https://honkai-star-rail.fandom.com/wiki/Character)。头像使用 [StarRailRes 的游戏头像资源](https://github.com/Mar-7th/StarRailRes)；游戏角色及素材权利归原权利人，本示例不重新分发图片。
