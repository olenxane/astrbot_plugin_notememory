import json
from dataclasses import asdict
from pathlib import Path

from quart import Response, jsonify, request

from .service import parse_layer
from .settings import Settings


class MemoryWebAPI:
    def __init__(self, plugin):
        self.plugin = plugin
        self.service = plugin.service
        schema_path = Path(__file__).with_name("_conf_schema.json")
        self.schema = json.loads(schema_path.read_text(encoding="utf-8"))

    def register(self, context, prefix):
        routes = [
            ("scopes", self.scopes, ["GET"]),
            ("notes", self.notes, ["GET"]),
            ("status", self.status, ["GET"]),
            ("settings", self.settings, ["GET", "POST"]),
            ("save", self.save, ["POST"]),
            ("delete", self.delete, ["POST"]),
            ("recall", self.recall, ["POST"]),
            ("summary", self.summary, ["POST"]),
            ("refresh", self.refresh, ["POST"]),
            ("export", self.export, ["GET"]),
        ]
        for path, handler, methods in routes:
            context.register_web_api(f"/{prefix}/{path}", handler, methods, f"便签记忆 {path}")

    @staticmethod
    def scope(payload):
        scope = payload.get("scope", "")
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 512:
            raise ValueError("请先选择用户，或填写 /note status 显示的完整用户标识")
        return scope.strip()

    @staticmethod
    async def payload():
        body = await request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("请求体应为 JSON 对象")
        return body

    @staticmethod
    def error(error):
        return jsonify({"status": "error", "message": str(error)}), 400

    async def scopes(self):
        return jsonify(
            {
                "items": await self.service.store.scopes(),
                "max_note_chars": self.plugin.settings.max_note_chars,
            }
        )

    async def notes(self):
        try:
            scope = self.scope(request.args)
            result = await self.service.list_notes(
                scope,
                request.args.get("layer", "all"),
                request.args.get("keyword", ""),
                int(request.args.get("page", "1")),
                int(request.args.get("page_size", "12")),
            )
            return jsonify(result)
        except ValueError as error:
            return self.error(error)

    def settings_payload(self):
        return {
            "values": asdict(self.plugin.settings),
            "schema": self.schema,
            "providers": [
                provider.meta().id for provider in self.plugin.context.get_all_providers()
            ],
        }

    async def settings(self):
        if request.method == "GET":
            return jsonify(self.settings_payload())
        try:
            body = await self.payload()
            values = asdict(self.plugin.settings)
            unknown = set(body) - set(values)
            if unknown:
                raise ValueError(f"未知配置项：{', '.join(sorted(unknown))}")
            values.update(body)
            settings = Settings.from_config(values)
            self.plugin.config.save_config(asdict(settings))
            self.plugin.settings = settings
            self.service.settings = settings
            return jsonify(self.settings_payload())
        except ValueError as error:
            return self.error(error)

    async def status(self):
        try:
            return jsonify(await self.service.status(self.scope(request.args)))
        except ValueError as error:
            return self.error(error)

    async def save(self):
        try:
            body = await self.payload()
            scope = self.scope(body)
            note_id = body.get("id")
            content, tags, layer = (
                body.get("content"),
                body.get("tags", ""),
                parse_layer(body.get("layer", "short")),
            )
            if note_id:
                note = await self.service.update(scope, str(note_id), content, layer, tags)
            else:
                note = await self.service.create(scope, content, layer, tags, "webui", "Dashboard")
            return jsonify({"note": note})
        except ValueError as error:
            return self.error(error)

    async def delete(self):
        try:
            body = await self.payload()
            scope = self.scope(body)
            note_id = body.get("id")
            if not isinstance(note_id, str) or not await self.service.store.delete(scope, note_id):
                raise ValueError("未找到当前用户的这条便签")
            return jsonify({"deleted": True})
        except ValueError as error:
            return self.error(error)

    async def recall(self):
        try:
            body = await self.payload()
            scope = self.scope(body)
            if body.get("id"):
                note = await self.service.get(scope, str(body["id"]))
                if note["layer"] != "archived":
                    raise ValueError("只有归档便签需要召回")
                items = await self.service.store.recall(scope, [note["id"]])
                return jsonify({"items": items, "total": len(items)})
            return jsonify(await self.service.search_archive(scope, body.get("keyword", "")))
        except ValueError as error:
            return self.error(error)

    async def summary(self):
        try:
            body = await self.payload()
            scope = self.scope(body)
            await self.service.store.touch_scope(scope)
            return jsonify(await self.service.summarize(scope))
        except ValueError as error:
            return self.error(error)
        except Exception as error:
            self.plugin.log.warning(f"[便签记忆] 页面总结失败：{error}")
            return jsonify(
                {
                    "status": "error",
                    "message": "总结调用失败，原对话已保留；请查看 AstrBot 日志或状态栏",
                }
            ), 502

    async def refresh(self):
        try:
            body = await self.payload()
            scope = self.scope(body)
            self.plugin.invalidate_snapshot(scope)
            return jsonify({"refreshed": True})
        except ValueError as error:
            return self.error(error)

    async def export(self):
        try:
            scope = self.scope(request.args)
            content = await self.service.store.export_notes(scope)
            return Response(
                content,
                content_type="application/json; charset=utf-8",
                headers={
                    "Content-Disposition": 'attachment; filename="notememory-export.json"',
                },
            )
        except ValueError as error:
            return self.error(error)
