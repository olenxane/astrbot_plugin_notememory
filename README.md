# 便签记忆

为 AstrBot 提供用户级长期记忆。支持自动总结、LLM 工具、`/note` 指令和 Pages 管理页面。

## 记忆规则

- **按用户共享**：同一用户的私聊、群聊和不同会话共用便签；不同用户、不同平台相互隔离。
- **短期便签**：默认保留 7 天，更新或召回后重新计时。
- **归档便签**：短期便签到期后进入归档池，可按关键词召回。
- **重要便签**：永久保留，只能显式维护，不会被自动总结修改。

Bot 会在会话首次请求时注入短期和重要便签，并通过工具写入、更新、删除或查询记忆。修改后可使用 `/note refresh`，让该用户的所有会话重新装载。

## 指令

```text
/note help
/note list [all|short|archived|important] [页码]
/note show ID [分段页码]
/note add short|important|archived 内容
/note edit ID 新内容
/note move ID short|important|archived
/note delete ID
/note search 关键词
/note summary
/note refresh
/note status
```

`/note status` 可查看用户标识。多词搜索要求所有关键词匹配。

## 管理页面与配置

在 AstrBot 插件详情页打开“便签记忆管理”：

- **记忆管理**：查看、搜索、创建、编辑、删除、召回、总结、刷新快照和导出 JSON。
- **插件设置**：配置保留时间、上下文预算、自动总结、总结模型、分页、指令长度和管理权限。设置与 AstrBot 插件配置同步，保存后立即生效。

自动总结默认每 40 轮或 24 小时触发，会调用 LLM 并产生相应费用。插件只处理启用后完成的对话，不回溯旧历史；总结失败会保留原文并自动重试。

## 数据

```text
AstrBot/data/plugin_data/astrbot_plugin_notememory/notes.db
```

便签保存在插件目录之外，升级插件不会覆盖数据库。用户级版本不会自动迁移旧版的会话级数据。
