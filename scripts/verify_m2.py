"""Verify the deployed M2 API, then exercise isolated databases and HTTP failure fixtures."""

import argparse
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

from verify_m1 import COMPOSE, ROOT, request, require


def verify_realtime(problem, current, detail):
    diagnostic = "mysql.lock_chain" if problem == "lock_wait" else "mysql.slow_query_summary"
    require(
        len(current) == 2 and {item["source"] for item in current} == {"mysql.health", diagnostic},
        f"{problem}: unexpected realtime evidence sources",
    )
    digest_gaps = {
        "digest consumers disabled or missing",
        "statement instrumentation incomplete",
        "no timed application digest samples",
    }
    for item in current:
        source, status = item["source"], item["status"]
        limits = item.get("limitations", [])
        context = f"{problem}: {source} status={status}, limitations={limits}"
        if status == "ok" or (source == "mysql.lock_chain" and status == "empty"):
            continue
        # An idle lab need not contain digest samples; connectivity failures must still fail.
        if (
            source == "mysql.slow_query_summary"
            and status == "unavailable"
            and limits
            and set(limits) <= digest_gaps
        ):
            report = detail["report"]
            require(detail["task"]["status"] == "partial", context + "; expected partial task")
            missing = report["missing_evidence"]
            require(
                f"{source}: unavailable" in missing
                and all(f"{source}: {reason}" in missing for reason in limits),
                context + "; report must explain missing digest evidence",
            )
            require(
                all(item["evidence_id"] not in f["evidence_ids"] for f in report["findings"]),
                context + "; unavailable digest cannot support a finding",
            )
            print(f"INFO: {context}; verified explicit degradation", flush=True)
            continue
        raise RuntimeError(context)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="UTC/offset ISO-8601; default: 30 minutes ago")
    parser.add_argument("--end", help="UTC/offset ISO-8601; default: now")
    parser.add_argument(
        "--require-history",
        action="store_true",
        help="fail unless both configured backends return actual samples/events",
    )
    parser.add_argument(
        "--require-search",
        action="store_true",
        help="require Elasticsearch events; Prometheus may remain disabled",
    )
    parser.add_argument("--skip-suite", action="store_true", help="only check the deployed API")
    args = parser.parse_args()
    require(bool(args.start) == bool(args.end), "provide --start and --end together")
    now = datetime.now(UTC).replace(microsecond=0)
    window = {
        "start": args.start or (now - timedelta(minutes=30)).isoformat(),
        "end": args.end or now.isoformat(),
    }
    os.chdir(ROOT)
    result = subprocess.run(
        COMPOSE
        + [
            "exec",
            "-T",
            "dbic-api",
            "python",
            "-c",
            "import os; print(os.environ['DBIC_API_TOKEN'])",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    token = result.stdout.strip()
    require(request("/healthz") == (200, {"status": "ok"}), "API is not healthy")
    require(request("/readyz") == (200, {"status": "ready"}), "PostgreSQL schema is not ready")
    require(request("/api/v1/capabilities")[0] == 401, "anonymous access accepted")
    require(request("/api/v1/capabilities", "invalid")[0] == 401, "wrong token accepted")
    status, capabilities = request("/api/v1/capabilities", token)
    require(status == 200 and capabilities["milestone"] == "M2", "deploy the M2 image first")
    backends = capabilities["historical_backends"]
    if args.require_history:
        require(
            backends == {"metrics": "prometheus", "search": "elastic"},
            "both historical backends must be configured",
        )
    if args.require_search:
        require(backends["search"] == "elastic", "Elasticsearch backend must be configured")
    for problem, historical in [("lock_wait", False), ("lock_wait", True), ("slow_query", True)]:
        body = {"instance_id": "mysql-lab-01", "problem_type": problem}
        if historical:
            body["window"] = window
        status, created = request("/api/v1/diagnoses", token, body)
        require(
            status == 202, "diagnosis creation failed; check aware window <=24h and not in future"
        )
        path = "/api/v1/diagnoses/" + created["task_id"]
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            status, detail = request(path, token)
            require(status == 200, "task read failed")
            if detail["task"]["status"] not in {"queued", "running"}:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("task did not reach a terminal state")
        require(detail["task"]["status"] in {"completed", "partial"}, "diagnosis failed")
        status, evidence = request(path + "/evidence", token)
        require(status == 200 and len(evidence) == (4 if historical else 2), "evidence missing")
        require(len(detail["steps"]) == len(evidence), "step/evidence count mismatch")
        current = [item for item in evidence if item["kind"] == "database_state"]
        verify_realtime(problem, current, detail)
        require(
            all(item["window"] is None for item in current),
            "realtime samples mislabeled as historical",
        )
        ids = {item["evidence_id"] for item in evidence}
        require(
            all(
                f["evidence_ids"] and set(f["evidence_ids"]) <= ids
                for f in detail["report"]["findings"]
            ),
            "invalid finding references",
        )
        require(all(item["redacted"] for item in evidence), "unredacted evidence")
        for item in evidence:
            if item["kind"] not in {"metric", "log"}:
                continue
            backend = backends["metrics" if item["kind"] == "metric" else "search"]
            require(
                item["window"] == detail["task"]["window"], "historical window was not persisted"
            )
            if backend == "null":
                require(
                    item["status"] == "unavailable" and item["limitations"],
                    "disabled backend must be explicit",
                )
                require(detail["task"]["status"] == "partial", "disabled backend must degrade task")
            elif args.require_history or (args.require_search and item["kind"] == "log"):
                require(
                    item["status"] == "ok"
                    and item["payload"].get("samples" if item["kind"] == "metric" else "events"),
                    "historical evidence missing; check window, mapping and credentials",
                )
            else:
                print(
                    f"INFO: {item['source']} status={item['status']} "
                    f"limitations={item['limitations']}",
                    flush=True,
                )
        print(
            f"PASS: {problem} window={historical}, status={detail['task']['status']}, "
            f"persisted evidence={len(evidence)}",
            flush=True,
        )
    if not args.skip_suite:
        subprocess.run(
            COMPOSE + ["--profile", "verify", "run", "--build", "--rm", "dbic-tests"], check=True
        )
    print(
        "PASS: M2 API verification"
        + (
            " with real historical evidence"
            if args.require_history
            else " with Elasticsearch event evidence (metrics availability not required)"
            if args.require_search
            else " (historical backend availability not required)"
        )
        + ("; isolated suite passed" if not args.skip_suite else "; suite skipped")
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        sys.exit(f"FAIL: M2 verification: {message}")
