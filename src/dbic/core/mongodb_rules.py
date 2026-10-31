import logging
from datetime import datetime, timedelta

from pydantic import ValidationError

from dbic.core.models import DiagnosticTask, Evidence, Finding, utc_now
from dbic.core.mongodb_models import CurrentOperations, ReplicaSnapshot


def mongodb_findings(
    task: DiagnosticTask, evidence: list[Evidence], *, now: datetime | None = None
) -> list[Finding]:
    now = now or utc_now()
    findings: list[Finding] = []
    if task.database_type != "mongodb" or task.instance_id != "mongodb-lab-01":
        return findings
    for item in evidence:
        if (
            item.task_id != task.task_id
            or item.instance_id != task.instance_id
            or not item.redacted
            or item.kind != "database_state"
            or item.quality != "observed"
            or item.window is not None
            or item.status not in {"ok", "empty"}
            or not timedelta(seconds=-5) <= now - item.observed_at <= timedelta(seconds=90)
        ):
            continue
        try:
            if item.source == "mongodb.current_ops_summary":
                current = CurrentOperations.model_validate(item.payload)
                if (item.status == "empty") != (len(current.samples) == 0):
                    continue
                waiting = sum(s.waiting_for_lock is True for s in current.samples)
                long = sum(
                    s.running_seconds is not None and s.running_seconds >= 1
                    for s in current.samples
                )
                rule = "activity.sample"
                statement = (
                    f"本次观察 {len(current.samples)} 条活动操作样本，"
                    f"其中 {waiting} 条报告锁等待、{long} 条运行至少 1 秒；"
                    "有限当前采样不代表完整历史，运行时长不单独证明慢查询。"
                )
            elif item.source == "mongodb.replica_health" and item.status == "ok":
                replica = ReplicaSnapshot.model_validate(item.payload)
                member = replica.members[0]
                primary = member.state == "PRIMARY" and member.reachable
                rule = "replica.primary_observed" if primary else "replica.primary_not_observed"
                statement = (
                    f"本节点视角：单成员复制集状态 {replica.local_state}，"
                    f"成员可达标志为 {member.reachable}；"
                    "单成员不提供冗余，不能据此验证高可用或多节点复制延迟。"
                )
            else:
                continue
        except ValidationError:
            logging.getLogger("dbic.rules").warning(
                "invalid MongoDB evidence task=%s evidence=%s", task.task_id, item.evidence_id
            )
            continue
        findings.append(
            Finding(
                statement=statement,
                certainty="observed",
                evidence_ids=[item.evidence_id],
                rule_id="mongodb." + rule,
            )
        )
    return findings
