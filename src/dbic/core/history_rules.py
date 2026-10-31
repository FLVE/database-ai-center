from dbic.core.models import Evidence, Finding


def historical_findings(evidence: list[Evidence]) -> list[Finding]:
    findings = []
    for item in evidence:
        if item.status != "ok" or item.window is None:
            continue
        if item.kind == "metric":
            samples = item.payload.get("samples")
            if not isinstance(samples, list) or not samples:
                continue
            statement = (
                f"历史查询窗口 {item.window.start.isoformat()} 至 {item.window.end.isoformat()} "
                f"返回 {len(samples)} 个指标采样点；数值及单位见证据。"
                "采样不代表连续完整观测，也不能单独确定根因。"
            )
        elif item.kind == "log":
            events = item.payload.get("events")
            if not isinstance(events, list) or not events:
                continue
            statement = (
                f"历史查询窗口内检索到 {len(events)} 条匹配事件记录；"
                "这是日志检索结果，不能仅凭时间相关性确认根因。"
            )
        else:
            continue
        findings.append(
            Finding(
                statement=statement,
                certainty="observed",
                evidence_ids=[item.evidence_id],
                rule_id=f"history.{item.kind}.records_observed",
            )
        )
    return findings
