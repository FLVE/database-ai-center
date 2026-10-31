from typing import Any

from dbic.core.mongodb_models import CurrentOperations, OperationSample, ReplicaSnapshot

STATES = {
    0: "STARTUP",
    1: "PRIMARY",
    2: "SECONDARY",
    3: "RECOVERING",
    5: "STARTUP2",
    6: "UNKNOWN",
    7: "ARBITER",
    8: "DOWN",
    9: "ROLLBACK",
    10: "REMOVED",
}
OPERATIONS = {"query", "getmore", "insert", "update", "remove", "command"}


def current_operations(rows: list[dict[str, Any]]) -> CurrentOperations:
    if len(rows) > 51:
        raise ValueError("operation sample exceeded limit")
    samples = []
    for row in rows[:50]:
        # The server projection is repeated here as a defense against response/schema drift.
        op = row.get("op")
        samples.append(
            OperationSample.model_validate(
                {
                    "operation": op if isinstance(op, str) and op in OPERATIONS else "OTHER",
                    "running_seconds": row.get("secs_running"),
                    "waiting_for_lock": row.get("waitingForLock"),
                }
            )
        )
    return CurrentOperations(samples=samples, truncated=len(rows) > 50)


def replica_snapshot(raw: dict[str, Any]) -> ReplicaSnapshot:
    members = raw.get("members")
    if raw.get("set") != "dbic-rs" or not isinstance(members, list) or len(members) != 1:
        raise LookupError("unsupported replica topology")
    member = members[0]
    state, local, health = member.get("state"), raw.get("myState"), member.get("health")
    if (
        type(state) is not int
        or state not in STATES
        or type(local) is not int
        or local not in STATES
        or type(health) not in (int, float)
        or health not in (0, 1)
        or member.get("self") is not True
    ):
        raise ValueError("invalid replica state")
    return ReplicaSnapshot.model_validate(
        {
            "local_state": STATES[local],
            "members": [
                {
                    "member_id": member.get("_id"),
                    "state": STATES[state],
                    "reachable": health == 1,
                    "is_self": True,
                }
            ],
        }
    )
