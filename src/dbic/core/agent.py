from dbic.core.models import DiagnosticTask, Evidence, Instance, Report
from dbic.core.policy import authorize


def validate_agent_report(
    candidate: Report,
    baseline: Report,
    task: DiagnosticTask,
    evidence: list[Evidence],
    instance: Instance,
    max_calls: int,
) -> Report:
    candidate = Report.model_validate(candidate.model_dump())
    excluded = {"generated_at", "agent_status", "agent_reason", "agent_suggestions"}
    if candidate.model_dump(exclude=excluded) != baseline.model_dump(exclude=excluded):
        raise ValueError("agent cannot change the rule report")
    known = {
        item.evidence_id
        for item in evidence
        if item.task_id == task.task_id and item.instance_id == task.instance_id and item.redacted
    }
    if len(evidence) + len(candidate.agent_suggestions) > max_calls:
        raise OverflowError("agent suggestion budget exceeded")
    seen = set()
    for suggestion in candidate.agent_suggestions:
        authorize(instance, suggestion.tool_id)
        if (
            suggestion.instance_id != task.instance_id
            or not set(suggestion.evidence_ids) <= known
            or len(set(suggestion.evidence_ids)) != len(suggestion.evidence_ids)
            or suggestion.tool_id in seen
        ):
            raise ValueError("invalid agent target or evidence references")
        seen.add(suggestion.tool_id)
    return candidate
