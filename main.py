import asyncio
import json
import uuid
from contextlib import suppress
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .commands import run_command
from .service import MemoryService, clip, memory_scope
from .settings import Settings
from .storage import NoteStore
from .web_api import MemoryWebAPI

PLUGIN_NAME = "astrbot_plugin_notememory"


class NoteMemoryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.settings = Settings.from_config(config)
        self.log = logger
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.store = NoteStore(data_dir / "notes.db")
        self.service = MemoryService(self.store, self.settings, self.generate_summary)
        self.web = MemoryWebAPI(self)
        self.web.register(context, PLUGIN_NAME)
        self.worker = None
        self.refresh_scopes = set()

    async def initialize(self):
        await self.store.initialize()
        await self.store.archive_expired(self.settings.short_term_days)
        self.worker = asyncio.create_task(
            self.service.background(logger), name="notememory-maintenance"
        )
        logger.info("[便签记忆] 已载入：三层便签、自动总结和管理页面")

    async def terminate(self):
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
        self.service.snapshots.clear()
        self.refresh_scopes.clear()
        # 当前 AstrBot 不自动移除 register_web_api 注册的绑定方法。
        self.context.registered_web_apis[:] = [
            api
            for api in self.context.registered_web_apis
            if getattr(api[1], "__self__", None) is not self.web
        ]

    async def touch_event(self, event):
        scope = memory_scope(event)
        provider = self.context.get_using_provider(event.unified_msg_origin)
        label = event.get_sender_name() or event.get_sender_id()
        await self.store.touch_scope(scope, label, provider.meta().id if provider else "")
        return scope

    def invalidate_snapshot(self, scope):
        self.refresh_scopes.add(scope)
        for key in list(self.service.snapshots):
            if key[0] == scope:
                del self.service.snapshots[key]

    async def generate_summary(self, scope, system_prompt, prompt):
        provider_id = self.settings.summary_provider_id or await self.store.provider_id(scope)
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            system_prompt=system_prompt,
            prompt=prompt,
        )
        if response.role == "err":
            raise ValueError("总结模型返回错误，请检查模型配置和 AstrBot 日志")
        return response.completion_text or ""

    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req: ProviderRequest):
        scope = await self.touch_event(event)
        conversation = req.conversation
        cid = (
            conversation.cid
            if conversation
            else await self.context.conversation_manager.get_curr_conversation_id(
                event.unified_msg_origin
            )
        )
        empty_history = conversation.history in ("", "[]") if conversation else not req.contexts
        snapshot = await self.service.snapshot(
            scope,
            cid or "default",
            reset=empty_history or scope in self.refresh_scopes,
        )
        self.refresh_scopes.discard(scope)
        # 快照在会话内复用，避免每轮拼接变化的便签。
        req.system_prompt = (req.system_prompt or "") + "\n\n" + snapshot
        prompt = req.prompt or event.message_str or ""
        if req.image_urls and not prompt:
            prompt = "[用户发送了图片，未记录图片内容]"
        event.set_extra(
            "notememory_turn",
            {
                "key": uuid.uuid4().hex,
                "user": prompt,
                "speaker": f"{event.get_sender_name()} (ID:{event.get_sender_id()})",
            },
        )

    @filter.on_agent_done()
    async def record_completed_turn(self, event: AstrMessageEvent, run_context, resp: LLMResponse):
        turn = event.get_extra("notememory_turn")
        if not turn or event.get_extra("notememory_recorded") or resp.role == "err":
            return
        event.set_extra("notememory_recorded", True)
        await self.service.record_turn(
            memory_scope(event),
            turn["key"],
            turn["user"],
            resp.completion_text or "",
            turn["speaker"],
        )

    @filter.command("note", alias={"便签"})
    async def note_command(self, event: AstrMessageEvent):
        """管理三层便签。发送 /note help 查看完整用法。"""
        event.stop_event()
        try:
            message = await run_command(self, event)
        except ValueError as error:
            message = str(error)
        except Exception as error:
            logger.error(f"[便签记忆] 指令失败：{error}")
            message = "操作失败，请查看 AstrBot 日志；若总结失败，尚未处理的对话会保留。"
        yield event.plain_result(clip(message, self.settings.command_max_chars))

    def check_bot_manage(self, event, *layers):
        if self.settings.admin_only_manage and not event.is_admin():
            raise ValueError("仅 AstrBot 管理员可触发便签修改")
        if not self.settings.bot_manage_important and "important" in layers:
            raise ValueError("已禁止 Bot 修改重要便签，请用户通过指令或 WebUI 操作")

    @staticmethod
    def tool_json(payload):
        return json.dumps(payload, ensure_ascii=False)

    @filter.llm_tool(name="notememory_write")
    async def write_memory(
        self, event: AstrMessageEvent, content: str, important: bool = False, tags: str = ""
    ) -> str:
        """写入一条简短便签。通常写短期层，重要层仅用于用户明确要求长期保留的信息。

        Args:
            content(string): 单一主题的简洁事实，不要包含无用寒暄和猜测。
            important(boolean): 是否创建永久重要便签，默认 false。
            tags(string): 可选的关键词标签，用空格分隔。
        """
        try:
            layer = "important" if important else "short"
            self.check_bot_manage(event, layer)
            scope = await self.touch_event(event)
            note = await self.service.create(
                scope, content, layer, tags, "tool", event.get_sender_id()
            )
            return self.tool_json({"saved": True, "note": note})
        except ValueError as error:
            return self.tool_json({"error": str(error)})

    @filter.llm_tool(name="notememory_update")
    async def update_memory(
        self,
        event: AstrMessageEvent,
        note_id: str,
        content: str,
        layer: str = "keep",
        tags: str = "keep",
    ) -> str:
        """用新内容替换当前用户的便签，不会新建副本。

        Args:
            note_id(string): 便签 ID，来自初始化快照或归档检索结果。
            content(string): 新的完整便签内容。
            layer(string): keep 保留类型；也可指定 short、important 或 archived。
            tags(string): keep 保留标签，空文本清除标签，其他文本替换标签。
        """
        try:
            scope = memory_scope(event)
            old = await self.service.get(scope, note_id)
            self.check_bot_manage(event, old["layer"], layer)
            note = await self.service.update(
                scope,
                note_id,
                content,
                None if layer == "keep" else layer,
                None if tags == "keep" else tags,
            )
            return self.tool_json(
                {"updated": True, "note": note, "hint": "新内容优先于初始化快照中的旧内容"}
            )
        except ValueError as error:
            return self.tool_json({"error": str(error)})

    @filter.llm_tool(name="notememory_read_archive")
    async def read_archive(self, event: AstrMessageEvent, keywords: str, limit: int = 5) -> str:
        """按关键词查询当前用户的归档便签；所有关键词须匹配，命中项会召回短期层。

        Args:
            keywords(string): 与待回忆事实相关的关键词，多个词用空格分隔，未命中时可换用更短关键词。
            limit(number): 最多返回并召回的条数，1 到 10，默认 5。
        """
        try:
            result = await self.service.search_archive(memory_scope(event), keywords, limit)
            return self.tool_json(
                {
                    "matched": result["total"],
                    "recalled": result["items"],
                    "hint": "返回项已进入短期层并重置计时；其余匹配项仍在归档池，可缩小关键词继续查询",
                }
            )
        except ValueError as error:
            return self.tool_json({"error": str(error)})

    @filter.llm_tool(name="notememory_delete")
    async def delete_memory(self, event: AstrMessageEvent, note_id: str) -> str:
        """删除当前用户的一条便签。仅在用户明确要求遗忘或确认该信息错误时使用。

        Args:
            note_id(string): 要删除的便签 ID。
        """
        try:
            scope = memory_scope(event)
            old = await self.service.get(scope, note_id)
            self.check_bot_manage(event, old["layer"])
            await self.store.delete(scope, note_id)
            return self.tool_json(
                {
                    "deleted": True,
                    "id": note_id,
                    "hint": "不要再引用初始化快照中的此条信息；快照不会重写，用户可用 /note refresh 刷新",
                }
            )
        except ValueError as error:
            return self.tool_json({"error": str(error)})
