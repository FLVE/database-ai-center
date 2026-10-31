from typing import Any

from dbic.core.models import AgentAdvice


def deepseek_schema() -> dict[str, Any]:
    schema = AgentAdvice.model_json_schema()
    definitions = schema.get("$defs", {})

    def convert(value: Any) -> Any:
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return convert(definitions[value["$ref"].removeprefix("#/$defs/")])
        # DeepSeek strict schemas reject array length limits; Pydantic still enforces them locally.
        result = {
            key: convert(item)
            for key, item in value.items()
            if key not in {"$defs", "minItems", "maxItems", "title"}
        }
        if result.get("type") == "string" and "const" in result:
            result["enum"] = [result.pop("const")]
        return result

    result: dict[str, Any] = convert(schema)
    return result
