import json

from .service import (
    LAYER_NAMES,
    clip,
    format_note,
    format_note_page,
    memory_scope,
    parse_layer,
    summary_message,
)

HELP = """便签记忆 /note（别名 /便签）
/note list [all|short|archived|important] [页码]
/note show ID [分段页码]
/note add short|important|archived 内容
/note edit ID 新内容
/note move ID short|important|archived
/note delete ID
/note search 关键词（召回归档项到短期层）
/note summary（手动总结待处理对话）
/note refresh（下一轮重新装载便签快照）
/note status（用户标识、条数和总结状态）
列表仅预览，show 按字数分段。多词检索须全部匹配。
重要便签永不自动归档，自动总结只新增短期便签。"""


async def run_command(plugin, event):
    raw = event.message_str.strip().split(None, 1)
    args = raw[1] if len(raw) > 1 else "help"
    parts = args.split(None, 1)
    action = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    service = plugin.service
    scope = memory_scope(event)
    await plugin.touch_event(event)
    if action in ("help", "帮助"):
        return HELP
    if action in ("add", "edit", "delete", "move", "summary", "refresh"):
        if plugin.settings.admin_only_manage and not event.is_admin():
            return "配置已限制为仅 AstrBot 管理员可管理便签和触发总结。"
    if action == "list":
        params = rest.split()
        layer, page = "all", 1
        if params:
            if params[0].isdigit():
                page = int(params[0])
            else:
                layer = parse_layer(params[0], allow_all=True)
                if len(params) > 1:
                    page = int(params[1])
        result = await service.list_notes(scope, layer, page=page)
        return format_note_page(result, plugin.settings.command_max_chars)
    if action == "show":
        params = rest.split()
        if not params:
            raise ValueError("用法：/note show ID [分段页码]")
        note = await service.get(scope, params[0])
        page = int(params[1]) if len(params) > 1 else 1
        size = plugin.settings.command_max_chars - 250
        text = format_note(note, plugin.settings.max_note_chars)
        pages = max(1, (len(text) + size - 1) // size)
        if not 1 <= page <= pages:
            raise ValueError(f"分段页码应为 1 到 {pages}")
        return (
            f"详情 {page}/{pages}\n"
            + text[(page - 1) * size : page * size]
            + (f"\n/note show {note['id']} {page + 1}" if page < pages else "")
        )
    if action == "add":
        params = rest.split(None, 1)
        if len(params) != 2:
            raise ValueError("用法：/note add short|important|archived 内容")
        note = await service.create(
            scope,
            params[1],
            parse_layer(params[0]),
            source="command",
            created_by=event.get_sender_id(),
        )
        return f"已创建 {LAYER_NAMES[note['layer']]}便签 [{note['id']}]。"
    if action == "edit":
        params = rest.split(None, 1)
        if len(params) != 2:
            raise ValueError("用法：/note edit ID 新内容")
        note = await service.update(scope, params[0], params[1])
        return f"已替换便签 [{note['id']}] 的内容。"
    if action == "move":
        params = rest.split()
        if len(params) != 2:
            raise ValueError("用法：/note move ID short|important|archived")
        old = await service.get(scope, params[0])
        note = await service.update(scope, old["id"], old["content"], parse_layer(params[1]))
        return f"便签 [{note['id']}] 已设为{LAYER_NAMES[note['layer']]}。"
    if action == "delete":
        if not rest or " " in rest:
            raise ValueError("用法：/note delete ID")
        if not await service.store.delete(scope, rest):
            raise ValueError("未找到当前用户的这条便签")
        return f"已删除便签 [{rest}]。已有会话快照不自动重写，可用 /note refresh 重新装载。"
    if action == "search":
        result = await service.search_archive(scope, rest)
        text = json.dumps(
            {
                "matched": result["total"],
                "recalled": [{"id": n["id"], "content": n["content"]} for n in result["items"]],
            },
            ensure_ascii=False,
            indent=2,
        )
        return clip(
            "匹配项已召回为短期便签并重置归档计时。\n" + text, plugin.settings.command_max_chars
        )
    if action == "summary":
        return summary_message(await service.summarize(scope))
    if action == "refresh":
        plugin.invalidate_snapshot(scope)
        return "已标记重新装载；下一次模型请求将注入当前短期和重要便签。"
    if action == "status":
        status = await service.status(scope)
        counts = status["counts"]
        text = (
            f"用户：{scope}\n短期 {counts['short']}，归档 {counts['archived']}，重要 {counts['important']}\n"
            f"短期保留：{status['short_term_days']} 天；待总结 {status['pending_rounds']} 轮\n"
            f"自动总结：{'开' if status['auto_summary'] else '关'}，"
            f"轮数阈值 {status['summary_rounds']}，小时阈值 {status['summary_hours']}（0 为禁用）"
        )
        if status["last_error"]:
            text += f"\n最近总结错误：{status['last_error']}"
        return clip(text, plugin.settings.command_max_chars)
    raise ValueError("未知操作，发送 /note help 查看用法")
