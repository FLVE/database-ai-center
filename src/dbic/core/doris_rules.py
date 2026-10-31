import logging
from datetime import datetime, timedelta

from pydantic import ValidationError

from dbic.core.doris_models import BackendHealth, ProfileSummary
from dbic.core.models import DiagnosticTask, Evidence, Finding, utc_now


def doris_findings(
    task: DiagnosticTask, evidence: list[Evidence], *, now: datetime | None = None
) -> list[Finding]:
    now = now or utc_now()
    findings: list[Finding] = []
    if task.database_type != "doris" or task.instance_id != "doris-lab-01":
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
            if item.source == "doris.fe_be_health" and item.status == "ok":
                health = BackendHealth.model_validate(item.payload)
                rule = "backends.snapshot"
                statement = (
                    "当前 FE 可达，报告 "
                    f"{health.online_backends}/{health.total_backends} 个 BE 在线；"
                    "不代表多 FE 高可用或 tablet、磁盘健康。"
                )
            elif item.source == "doris.query_profile_summary":
                profiles = ProfileSummary.model_validate(item.payload)
                if (item.status == "empty") != (not profiles.samples):
                    continue
                rule = "profiles.sample"
                statement = (
                    f"当前 FE 上诊断账号可见的保留 Profile 样本为 {len(profiles.samples)} 条；"
                    "不能据此判断其他用户的查询或完整历史慢查询。"
                )
            else:
                continue
        except ValidationError:
            logging.getLogger("dbic.rules").warning(
                "invalid Doris evidence task=%s evidence=%s", task.task_id, item.evidence_id
            )
            continue
        findings.append(
            Finding(
                statement=statement,
                certainty="observed",
                evidence_ids=[item.evidence_id],
                rule_id="doris." + rule,
            )
        )
    return findings
