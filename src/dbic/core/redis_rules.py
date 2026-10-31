import logging
from datetime import datetime, timedelta

from pydantic import ValidationError

from dbic.core.models import DiagnosticTask, Evidence, Finding, utc_now
from dbic.core.redis_models import AuditEvidence, MemorySnapshot, SlowlogSummary


def redis_findings(
    task: DiagnosticTask, evidence: list[Evidence], *, now: datetime | None = None
) -> list[Finding]:
    now = now or utc_now()
    if task.database_type != "redis" or task.instance_id != "redis-lab-01":
        return []
    findings: list[Finding] = []
    pressure: Evidence | None = None
    bigkey: Evidence | None = None

    def add(item: Evidence, rule: str, statement: str) -> None:
        findings.append(
            Finding(
                statement=statement,
                certainty="observed",
                evidence_ids=[item.evidence_id],
                rule_id="redis." + rule,
            )
        )

    for item in evidence:
        maximum_age = 900 if item.source == "redis.bigkey_audit_read" else 90
        if (
            item.task_id != task.task_id
            or item.instance_id != task.instance_id
            or not item.redacted
            or item.status not in {"ok", "empty"}
            or item.kind != "database_state"
            or item.window is not None
            or not timedelta(seconds=-5) <= now - item.observed_at <= timedelta(seconds=maximum_age)
        ):
            continue
        try:
            if item.source == "redis.memory_breakdown" and item.status == "ok":
                memory = MemorySnapshot.model_validate(item.payload)
                if memory.used_memory_bytes is None:
                    continue
                add(
                    item,
                    "memory.snapshot",
                    f"当前 Redis 分配内存 {memory.used_memory_bytes} bytes；"
                    "单次快照不能证明内存增长。",
                )
                if memory.used_memory_rss_bytes is not None:
                    add(
                        item,
                        "memory.rss",
                        f"当前 RSS {memory.used_memory_rss_bytes} bytes；"
                        "与分配内存的差异不单独证明泄漏。",
                    )
                if memory.maxmemory_bytes == 0:
                    add(
                        item,
                        "memory.no_limit",
                        "未配置 maxmemory 限额，无法计算配置限额压力；不代表没有内存风险。",
                    )
                elif memory.maxmemory_bytes and memory.mem_not_counted_for_evict_bytes is not None:
                    ratio = (
                        max(0, memory.used_memory_bytes - memory.mem_not_counted_for_evict_bytes)
                        / memory.maxmemory_bytes
                    )
                    high = ratio >= 0.9
                    add(
                        item,
                        "memory.pressure_high" if high else "memory.pressure_below_threshold",
                        f"本次估算用于限额比较的内存占比 {ratio:.1%}；"
                        "lab 提示阈值为 90%，不等同于主机内存压力或根因。",
                    )
                    if high:
                        pressure = item
            elif item.source == "redis.slowlog_summary":
                summary = SlowlogSummary.model_validate(item.payload)
                if (item.status == "empty") != (summary.sample_count == 0):
                    continue
                if summary.latest_at and summary.latest_at > item.observed_at + timedelta(
                    seconds=5
                ):
                    continue
                add(
                    item,
                    "slowlog.retained_samples",
                    f"本次读取 {summary.sample_count} 条保留慢命令记录；"
                    "不能推出完整历史、客户端延迟或内存异常因果关系。",
                )
            elif item.source == "redis.bigkey_audit_read" and item.status == "ok":
                audit = AuditEvidence.model_validate(item.payload)
                if audit.finished_at != item.observed_at:
                    continue
                add(
                    item,
                    "audit.imported_summary",
                    f"导入审计截至 {audit.finished_at.isoformat()}，覆盖 {audit.coverage}、"
                    f"检查 {audit.scanned_keys} 个 Key，报告 {audit.bigkey_count} 个大 Key；"
                    "不代表当前全库状态。",
                )
                if audit.bigkey_count:
                    bigkey = item
        except ValidationError:
            # Malformed imported/replayed evidence cannot become a diagnostic fact.
            logging.getLogger("dbic.rules").warning(
                "invalid Redis evidence task=%s evidence=%s", task.task_id, item.evidence_id
            )
            continue
    if pressure is not None and bigkey is not None:
        findings.append(
            Finding(
                statement="当前限额压力与近期审计的大 Key 线索同时存在，"
                "可进一步核对关联，尚未确认因果关系。",
                certainty="supported_hypothesis",
                evidence_ids=[pressure.evidence_id, bigkey.evidence_id],
                rule_id="redis.memory.bigkey_hypothesis",
            )
        )
    return findings
