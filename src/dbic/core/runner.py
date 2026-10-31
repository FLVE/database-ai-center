import asyncio
import logging
import time
from uuid import UUID

from dbic.core.engine import transition_task
from dbic.core.evidence import normalize
from dbic.core.history import EVENT_SOURCE, METRIC_SOURCES
from dbic.core.history_rules import historical_findings
from dbic.core.models import DiagnosticStep, Evidence, Report, TaskStatus, ToolResult, utc_now
from dbic.core.planner import plan
from dbic.core.policy import authorize
from dbic.core.ports import (
    AgentRuntimePort,
    AssetRegistryPort,
    DiagnosticToolPort,
    EvidenceSearchPort,
    MetricsPort,
    TaskStorePort,
)
from dbic.core.rules import evaluate

logger = logging.getLogger("dbic.runner")


class TaskRunner:
    def __init__(
        self,
        registry: AssetRegistryPort,
        tools: DiagnosticToolPort,
        store: TaskStorePort,
        agent: AgentRuntimePort,
        *,
        timeout: float = 90,
        max_calls: int = 8,
        metrics: MetricsPort | None = None,
        search: EvidenceSearchPort | None = None,
    ) -> None:
        self.registry, self.tools, self.store, self.agent = registry, tools, store, agent
        self.timeout, self.max_calls = timeout, max_calls
        self._gate = asyncio.Semaphore(1)
        self.metrics, self.search = metrics, search

    async def run(self, task_id: UUID) -> None:
        task = None
        evidence: list[Evidence] = []
        try:
            detail = await self.store.get_task(task_id)
            if detail is None or detail.task.status != TaskStatus.QUEUED:
                return
            task = transition_task(detail.task, TaskStatus.RUNNING)
            if not await self.store.update_task(task, TaskStatus.QUEUED):
                return
            async with asyncio.timeout(self.timeout):
                async with self._gate:
                    instance = await self.registry.get_instance(task.instance_id)
                    if instance is None:
                        raise PermissionError("instance unavailable")
                    playbook = plan(task.problem_type)
                    if len(playbook.required_tools) + (2 if task.window else 0) > self.max_calls:
                        raise PermissionError("tool budget exceeded")
                    for index, tool_id in enumerate(playbook.required_tools):
                        authorize(instance, tool_id)
                        started = utc_now()
                        clock = time.monotonic()
                        try:
                            result = await self.tools.run(
                                tool_id=tool_id, instance_id=instance.id, params={}
                            )
                        except Exception as exc:
                            logger.error(
                                "task=%s tool=%s failure=%s", task_id, tool_id, type(exc).__name__
                            )
                            result = ToolResult(
                                status="error",
                                source=tool_id,
                                observed_at=utc_now(),
                                duration_ms=(time.monotonic() - clock) * 1000,
                                limitations=["diagnostic tool failed"],
                            )
                        if result.source != tool_id:
                            raise ValueError("tool source mismatch")
                        item = normalize(task, result)
                        step = DiagnosticStep(
                            task_id=task_id,
                            step_key=str(index),
                            tool_id=tool_id,
                            status=item.status,
                            elapsed_ms=result.duration_ms,
                            started_at=started,
                            finished_at=utc_now(),
                            error_code=None if item.status in {"ok", "empty"} else item.status,
                        )
                        await self.store.save_step(step, item)
                        evidence.append(item)
                    if task.window:
                        metric_key = (
                            "mysql.connections"
                            if task.problem_type == "lock_wait"
                            else "mysql.slow_queries"
                        )
                        for offset, source in enumerate(
                            [METRIC_SOURCES[metric_key], EVENT_SOURCE],
                            start=len(playbook.required_tools),
                        ):
                            started = utc_now()
                            try:
                                async with asyncio.timeout(6):
                                    if source == EVENT_SOURCE and self.search:
                                        result = await self.search.search_events(
                                            instance_id=instance.id,
                                            start=task.window.start.isoformat(),
                                            end=task.window.end.isoformat(),
                                            categories=["error", "deadlock"]
                                            if task.problem_type == "lock_wait"
                                            else ["slow_query", "error"],
                                        )
                                    elif source != EVENT_SOURCE and self.metrics:
                                        result = await self.metrics.query_range(
                                            instance_id=instance.id,
                                            metric_key=metric_key,
                                            start=task.window.start.isoformat(),
                                            end=task.window.end.isoformat(),
                                        )
                                    else:
                                        result = ToolResult(
                                            status="unavailable",
                                            source=source,
                                            observed_at=utc_now(),
                                            duration_ms=0,
                                            limitations=["historical backend disabled"],
                                        )
                                if result.source != source or (
                                    result.window is not None and result.window != task.window
                                ):
                                    raise ValueError("historical source or window mismatch")
                            except Exception as exc:
                                logger.warning(
                                    "task=%s source=%s history failure=%s",
                                    task_id,
                                    source,
                                    type(exc).__name__,
                                )
                                result = ToolResult(
                                    status="timeout" if isinstance(exc, TimeoutError) else "error",
                                    source=source,
                                    observed_at=utc_now(),
                                    duration_ms=0,
                                    limitations=[
                                        "historical query timed out"
                                        if isinstance(exc, TimeoutError)
                                        else "historical query failed"
                                    ],
                                )
                            result = result.model_copy(
                                update={
                                    "kind": "log" if source == EVENT_SOURCE else "metric",
                                    "window": task.window,
                                }
                            )
                            item = normalize(task, result)
                            step = DiagnosticStep(
                                task_id=task_id,
                                step_key=str(offset),
                                tool_id=source,
                                status=item.status,
                                elapsed_ms=result.duration_ms,
                                started_at=started,
                                finished_at=utc_now(),
                                error_code=None if item.status in {"ok", "empty"} else item.status,
                            )
                            await self.store.save_step(step, item)
                            evidence.append(item)
                    realtime_findings = evaluate(task.problem_type, evidence).findings
                    findings = list(realtime_findings)
                    findings.extend(historical_findings(evidence))
                    report = await self.agent.explain(
                        task=task, evidence=evidence, findings=findings
                    )
                    partial = not realtime_findings or any(
                        e.status not in {"ok", "empty"} or e.limitations for e in evidence
                    )
                    terminal = transition_task(
                        task, TaskStatus.PARTIAL if partial else TaskStatus.COMPLETED
                    )
                    await self.store.finish_task(terminal, report)
        except (Exception, asyncio.CancelledError) as exc:
            logger.error("task=%s investigation failed type=%s", task_id, type(exc).__name__)
            if task is not None:
                code = (
                    "task_timeout"
                    if isinstance(exc, TimeoutError)
                    else "task_interrupted"
                    if isinstance(exc, asyncio.CancelledError)
                    else "task_failed"
                )
                terminal = transition_task(task, TaskStatus.FAILED, error_code=code)
                report = Report(
                    task_id=task_id,
                    status="inconclusive",
                    summary="调查未完成。",
                    evidence_ids=[e.evidence_id for e in evidence],
                    missing_evidence=[code],
                )
                try:
                    await self.store.finish_task(terminal, report)
                except Exception as storage_exc:
                    logger.error(
                        "task=%s failure persistence unavailable type=%s; last state retained",
                        task_id,
                        type(storage_exc).__name__,
                    )
            if isinstance(exc, asyncio.CancelledError):
                raise
