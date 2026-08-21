"""Pixie QA 边界使用的 JSON 类型。"""
from pydantic import JsonValue

JsonObject = dict[str, JsonValue]
