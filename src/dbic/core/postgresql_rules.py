import logging
from datetime import datetime, timedelta

from pydantic import ValidationError

from dbic.core.models import DiagnosticTask, Evidence, Finding, utc_now
from dbic.core.postgresql_models import BlockingSnapshot, VacuumSnapshot


def postgresql_findings(
    task: DiagnosticTask, evidence: list[Evidence], *, now: datetime | None = None
) -> list[Finding]:
    now = now or utc_now()
    findings: list[Finding] = []
    if task.database_type != "postgresql" or task.instance_id != "postgresql-lab-01":
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
            if item.source == "postgresql.blocking_sessions":
                blocking = BlockingSnapshot.model_validate(item.payload)
                if (item.status == "empty") != (not blocking.sessions):
                    continue
                statement = (
                    f"当前采样观察到 {len(blocking.sessions)} 个锁等待会话；不代表历史阻塞状态。"
                )
                rule = "blocking.observed" if blocking.sessions else "blocking.none_observed"
            elif item.source == "postgresql.vacuum_health":
                vacuum = VacuumSnapshot.model_validate(item.payload)
                if (item.status == "empty") != (not vacuum.tables):
                    continue
                times = [vacuum.stats_reset_at] + [
                    x for t in vacuum.tables for x in (t.last_vacuum_at, t.last_autovacuum_at)
                ]
                if any(t and t > item.observed_at + timedelta(seconds=5) for t in times):
                    continue
                candidates = sum(
                    t.dead_tuples_estimate >= 1000
                    and t.dead_tuples_estimate
                    / max(1, t.live_tuples_estimate + t.dead_tuples_estimate)
                    >= 0.2
                    for t in vacuum.tables
                )
                statement = (
                    f"采样 {len(vacuum.tables)} 个表的统计估算，{candidates} 个满足死元组核查阈值；"
                    "该统计不证明物理膨胀或自动清理故障，不执行维护操作。"
                )
                rule = "vacuum.review_candidates" if candidates else "vacuum.statistics"
            else:
                continue
        except ValidationError:
            logging.getLogger("dbic.rules").warning(
                "invalid PG evidence task=%s evidence=%s", task.task_id, item.evidence_id
            )
            continue
        findings.append(
            Finding(
                statement=statement,
                certainty="observed",
                evidence_ids=[item.evidence_id],
                rule_id="postgresql." + rule,
            )
        )
    return findings
