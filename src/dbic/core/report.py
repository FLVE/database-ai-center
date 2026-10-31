from dbic.core.models import DiagnosticTask, Evidence, Finding, Report


def rule_report(task: DiagnosticTask, evidence: list[Evidence], findings: list[Finding]) -> Report:
    missing = [f"{e.source}: {e.status}" for e in evidence if e.status not in {"ok", "empty"}]
    limitations = [f"{e.source}: {item}" for e in evidence for item in e.limitations]
    return Report(
        task_id=task.task_id,
        status="observed" if findings else "inconclusive",
        summary="；".join(f.statement for f in findings) or "证据不足，无法形成诊断结论。",
        findings=findings,
        evidence_ids=[e.evidence_id for e in evidence],
        timeline=[
            f"{e.observed_at.isoformat()} {e.source}: {e.status}"
            for e in sorted(evidence, key=lambda item: item.observed_at)
        ],
        missing_evidence=missing + limitations,
        recommendations=["结合采样时间核对业务影响；需要历史判断时补充指标和日志。"],
        risks=["仅供诊断，不包含自动修复或会话终止操作。"],
        tools=[e.source for e in evidence],
        generator_version="0.8.0",
    )
