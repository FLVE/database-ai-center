import hashlib
import json

from dbic.core.models import DiagnosticTask, Evidence, ToolResult


def normalize(task: DiagnosticTask, result: ToolResult) -> Evidence:
    payload = json.dumps(result.payload, ensure_ascii=False, sort_keys=True)
    if len(payload.encode()) > 65536:
        result = ToolResult(
            status="error",
            source=result.source,
            observed_at=result.observed_at,
            duration_ms=result.duration_ms,
            limitations=["result_size_limit"],
            kind=result.kind,
            window=result.window,
        )
        payload = "{}"
    return Evidence(
        task_id=task.task_id,
        instance_id=task.instance_id,
        kind=result.kind,
        window=result.window,
        source=result.source,
        observed_at=result.observed_at,
        quality="observed",
        status=result.status,
        payload=result.payload,
        limitations=result.limitations,
        redacted=True,
        tool_version="1",
        parameters_summary="predefined read-only query; no raw query input",
        payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
    )
