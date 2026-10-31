CHECK = """SELECT current_setting('server_version_num')::int AS version,
current_database() AS database, pg_has_role(current_user, 'pg_read_all_stats', 'USAGE') AS allowed,
current_setting('track_counts')::boolean AS tracked"""

BLOCKING = """SELECT pid AS waiter_pid, pg_blocking_pids(pid) AS blocker_pids,
EXTRACT(EPOCH FROM clock_timestamp()-xact_start)::double precision AS transaction_age_seconds
FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend'
AND pid<>pg_backend_pid() AND wait_event_type='Lock' ORDER BY pid LIMIT 51"""

VACUUM = """SELECT relid::bigint AS relation_id, n_live_tup AS live_tuples_estimate,
n_dead_tup AS dead_tuples_estimate, last_vacuum AS last_vacuum_at,
last_autovacuum AS last_autovacuum_at, vacuum_count, autovacuum_count
FROM pg_stat_user_tables ORDER BY n_dead_tup DESC, relid LIMIT 51"""

RESET = "SELECT stats_reset FROM pg_stat_database WHERE datname=current_database()"
