from dbic.adapters.agent.compatible import CompatibleAgentRuntime
from dbic.adapters.agent.null import NullAgentRuntime
from dbic.core.ports import AgentRuntimePort
from dbic.settings import Settings


def agent_runtime(settings: Settings) -> AgentRuntimePort:
    if settings.agent_backend == "openai_compatible":
        return CompatibleAgentRuntime(settings)
    return NullAgentRuntime()
