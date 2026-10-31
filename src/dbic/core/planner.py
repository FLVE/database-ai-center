from dbic.core.models import Model


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
}


def plan(problem_type: str) -> Playbook:
    if problem_type not in PLAYBOOKS:
        raise ValueError("unsupported problem_type")
    return PLAYBOOKS[problem_type].model_copy(deep=True)
