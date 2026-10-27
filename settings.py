from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    short_term_days: int = 7
    auto_summary: bool = True
    summary_rounds: int = 40
    summary_hours: float = 24.0
    summary_provider_id: str = ""
    summary_max_notes: int = 8
    summary_note_chars: int = 200
    summary_batch_chars: int = 24000
    summary_timeout: int = 90
    max_note_chars: int = 500
    injection_max_chars: int = 24000
    page_size: int = 6
    command_max_chars: int = 1800
    admin_only_manage: bool = False
    bot_manage_important: bool = True

    @classmethod
    def from_config(cls, config: Mapping):
        values = {
            key: config.get(key, field.default) for key, field in cls.__dataclass_fields__.items()
        }
        result = cls(**values)
        positive = (
            "short_term_days",
            "summary_max_notes",
            "summary_note_chars",
            "summary_batch_chars",
            "summary_timeout",
            "max_note_chars",
            "injection_max_chars",
            "page_size",
            "command_max_chars",
        )
        for key in positive:
            value = getattr(result, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} 必须是正整数")
        if not isinstance(result.summary_provider_id, str):
            raise ValueError("summary_provider_id 必须是文本")
        for key in ("auto_summary", "admin_only_manage", "bot_manage_important"):
            if not isinstance(getattr(result, key), bool):
                raise ValueError(f"{key} 必须是布尔值")
        if (
            isinstance(result.summary_rounds, bool)
            or not isinstance(result.summary_rounds, int)
            or result.summary_rounds < 0
        ):
            raise ValueError("总结轮数必须为非负整数，0 表示关闭轮数阈值")
        if (
            isinstance(result.summary_hours, bool)
            or not isinstance(result.summary_hours, (int, float))
            or result.summary_hours < 0
        ):
            raise ValueError("总结小时数必须为非负数，0 表示关闭时间阈值")
        if result.page_size > 20:
            raise ValueError("page_size 不能大于 20，以免指令条目显示不完整")
        if result.max_note_chars > result.injection_max_chars - 800:
            raise ValueError("初始化上下文预算至少应比单条便签上限多 800 字")
        if result.command_max_chars < 400:
            raise ValueError("command_max_chars 不能小于 400")
        if result.summary_batch_chars < 1000:
            raise ValueError("summary_batch_chars 不能小于 1000")
        if result.injection_max_chars < 1000:
            raise ValueError("injection_max_chars 不能小于 1000")
        if result.summary_note_chars > result.max_note_chars:
            raise ValueError("summary_note_chars 不能大于 max_note_chars")
        return result
