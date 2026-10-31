VERSION = (
    "SELECT /*+ MAX_EXECUTION_TIME(2000) */ VERSION() AS version, @@performance_schema AS enabled"
)
HEALTH = """SELECT /*+ MAX_EXECUTION_TIME(2000) */ VARIABLE_NAME, VARIABLE_VALUE
FROM performance_schema.global_status
WHERE VARIABLE_NAME IN ('Threads_connected', 'Threads_running', 'Uptime')"""
LOCKS = """SELECT /*+ MAX_EXECUTION_TIME(2000) */
w.REQUESTING_THREAD_ID AS waiter_thread, w.BLOCKING_THREAD_ID AS blocker_thread,
rt.PROCESSLIST_ID AS waiter_session, bt.PROCESSLIST_ID AS blocker_session,
rl.LOCK_TYPE AS waiting_lock_type, rl.LOCK_MODE AS waiting_lock_mode,
bl.LOCK_TYPE AS blocking_lock_type
FROM performance_schema.data_lock_waits w
LEFT JOIN performance_schema.threads rt ON rt.THREAD_ID = w.REQUESTING_THREAD_ID
LEFT JOIN performance_schema.threads bt ON bt.THREAD_ID = w.BLOCKING_THREAD_ID
LEFT JOIN performance_schema.data_locks rl
  ON rl.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID AND rl.ENGINE = w.ENGINE
LEFT JOIN performance_schema.data_locks bl
  ON bl.ENGINE_LOCK_ID = w.BLOCKING_ENGINE_LOCK_ID AND bl.ENGINE = w.ENGINE
LIMIT 101"""
CONSUMERS = """SELECT /*+ MAX_EXECUTION_TIME(2000) */ NAME, ENABLED
FROM performance_schema.setup_consumers
WHERE NAME IN ('global_instrumentation','thread_instrumentation','statements_digest')"""
INSTRUMENTS = """SELECT /*+ MAX_EXECUTION_TIME(2000) */ COUNT(*) AS total,
SUM(ENABLED = 'NO' OR TIMED = 'NO') AS disabled
FROM performance_schema.setup_instruments WHERE NAME LIKE 'statement/sql/%%'"""
DIGESTS = """SELECT /*+ MAX_EXECUTION_TIME(2000) */ DIGEST AS digest,
COUNT_STAR AS calls, SUM_TIMER_WAIT AS total_ps, AVG_TIMER_WAIT AS average_ps,
SUM_ROWS_EXAMINED AS rows_examined
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST IS NOT NULL AND COUNT_STAR > 0 AND SUM_TIMER_WAIT > 0
AND SCHEMA_NAME IS NOT NULL
AND SCHEMA_NAME NOT IN ('performance_schema','mysql','information_schema','sys')
ORDER BY SUM_TIMER_WAIT DESC LIMIT 21"""
