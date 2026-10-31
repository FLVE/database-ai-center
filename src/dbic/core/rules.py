from datetime import timedelta
from typing import Literal

from dbic.core.models import Evidence, Finding, Model, utc_now


class RuleResult(Model):
    status: Literal["passed", "failed", "inconclusive"]
    findings: list[Finding]


def evaluate(problem_type: str, evidence: list[Evidence]) -> RuleResult:
    if problem_type not in {"lock_wait", "slow_query"}:
        return RuleResult(status="inconclusive", findings=[])
    source = "mysql.lock_chain" if problem_type == "lock_wait" else "mysql.slow_query_summary"
    item = next((e for e in evidence if e.source == source), None)
    if item is None or item.status not in {"ok", "empty"}:
        return RuleResult(status="inconclusive", findings=[])
    age = utc_now() - item.observed_at
    if age > timedelta(seconds=90) or age < timedelta(seconds=-5):
        return RuleResult(status="inconclusive", findings=[])
    if problem_type == "lock_wait":
        edges = item.payload.get("edges")
        if not isinstance(edges, list):
            return RuleResult(status="inconclusive", findings=[])
        if any(
            not isinstance(edge, dict) or not {"waiter_session", "blocker_session"} <= edge.keys()
            for edge in edges
        ):
            return RuleResult(status="inconclusive", findings=[])
        if not edges and item.limitations:
            return RuleResult(status="inconclusive", findings=[])
        statement = (
            f"本次采样观察到 {len(edges)} 条 InnoDB 行锁等待边。"
            if edges
            else "本次采样未观察到 InnoDB 行锁等待；不能据此判断过去没有锁等待或死锁。"
        )
        rule_id = "mysql.lock_wait.blockers_observed" if edges else "mysql.lock_wait.none_observed"
    else:
        digests = item.payload.get("digests")
        if not isinstance(digests, list) or not digests:
            return RuleResult(status="inconclusive", findings=[])
        for digest in digests:
            if not isinstance(digest, dict):
                return RuleResult(status="inconclusive", findings=[])
            calls, digest_id = digest.get("calls"), digest.get("digest")
            if (
                not isinstance(digest_id, str)
                or not digest_id
                or not isinstance(calls, int)
                or isinstance(calls, bool)
                or calls <= 0
            ):
                return RuleResult(status="inconclusive", findings=[])
        statement = (
            f"读取到 {len(digests)} 条按累计耗时排序的语句摘要，"
            "它们不等同于慢日志，无法据此确认指定时间窗内慢 SQL 增长或根因。"
        )
        rule_id = "mysql.slow_query.cumulative_summary"
    return RuleResult(
        status="failed" if problem_type == "lock_wait" and edges else "passed",
        findings=[
            Finding(
                statement=statement,
                certainty="observed",
                evidence_ids=[item.evidence_id],
                rule_id=rule_id,
            )
        ],
    )
