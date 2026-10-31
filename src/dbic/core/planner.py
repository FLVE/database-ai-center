from dbic.core.models import Model
from dbic.core.policy import DATABASE_TOOLS


class UnsupportedDiagnosis(ValueError):
    pass


class Playbook(Model):
    id: str
    version: int = 1
    required_tools: list[str]


PLAYBOOKS = {
    "lock_wait": Playbook(
        id="mysql.lock_wait", required_tools=["mysql.health", "mysql.lock_chain"]
    ),
    "slow_query": Playbook(
        id="mysql.slow_query",
        required_tools=[
            "mysql.health",
            "mysql.slow_query_summary",
        ],
    ),
    "memory_pressure": Playbook(
        id="redis.memory_pressure",
        required_tools=[
            "redis.memory_breakdown",
            "redis.slowlog_summary",
            "redis.bigkey_audit_read",
        ],
    ),
}


def plan(problem_type: str, *, database_type: str) -> Playbook:
    allowed = DATABASE_TOOLS.get(database_type, frozenset())
    if problem_type.startswith("tool:") and problem_type[5:] in allowed:
        return Playbook(id=problem_type, required_tools=[problem_type[5:]])
    if problem_type not in PLAYBOOKS or not set(PLAYBOOKS[problem_type].required_tools) <= allowed:
        raise UnsupportedDiagnosis("unsupported database/problem combination")
    return PLAYBOOKS[problem_type].model_copy(deep=True)
