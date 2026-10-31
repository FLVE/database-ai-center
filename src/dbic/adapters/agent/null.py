from dbic.core.models import DiagnosticTask, Evidence, Finding, Report
from dbic.core.report import rule_report


class NullAgentRuntime:
    async def explain(
        self, *, task: DiagnosticTask, evidence: list[Evidence], findings: list[Finding]
    ) -> Report:
        return rule_report(task, evidence, findings)
