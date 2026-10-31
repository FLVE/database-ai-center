from dbic.adapters.agent.compatible import CompatibleAgentRuntime
from dbic.adapters.agent.null import NullAgentRuntime
from dbic.core.models import DiagnosticTask, Evidence, Finding, Report
from dbic.core.ports import AgentRuntimePort
from dbic.settings import Settings


class DatabaseAgentRuntime:
    def __init__(self, mysql: AgentRuntimePort) -> None:
        self.mysql = mysql
        self.null = NullAgentRuntime()

    async def explain(
        self, *, task: DiagnosticTask, evidence: list[Evidence], findings: list[Finding]
    ) -> Report:
        runtime = self.mysql if task.database_type == "mysql" else self.null
        return await runtime.explain(task=task, evidence=evidence, findings=findings)


def agent_runtime(settings: Settings) -> AgentRuntimePort:
    if settings.agent_backend == "openai_compatible":
        return DatabaseAgentRuntime(CompatibleAgentRuntime(settings))
    return NullAgentRuntime()
