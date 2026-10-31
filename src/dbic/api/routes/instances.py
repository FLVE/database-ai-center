from fastapi import APIRouter, Request

from dbic.core.models import DatabaseType, Model
from dbic.core.planner import PLAYBOOKS
from dbic.core.policy import DATABASE_TOOLS, authorize_instance

router = APIRouter(prefix="/api/v1/instances")


class InstanceSummary(Model):
    id: str
    database_type: DatabaseType
    environment: str
    version: str
    enabled: bool
    problem_types: list[str]
    supports_history: bool


@router.get("", response_model=list[InstanceSummary])
async def instances(request: Request) -> list[InstanceSummary]:
    """Configured lab summaries; enabled is authorization, not a connectivity probe."""
    result = []
    for kind in DATABASE_TOOLS:
        instance = await request.app.state.diagnostics.registry.get_instance(f"{kind}-lab-01")
        if instance is None:
            continue
        try:
            authorize_instance(instance)
            enabled = True
        except PermissionError:
            enabled = False
        problems = [
            name
            for name, playbook in PLAYBOOKS.items()
            if set(playbook.required_tools) <= DATABASE_TOOLS[kind]
        ]
        result.append(
            InstanceSummary(
                id=instance.id,
                database_type=instance.type,
                environment=instance.environment,
                version=instance.version,
                enabled=enabled,
                problem_types=problems if enabled else [],
                supports_history=enabled and instance.type == "mysql",
            )
        )
    return result
