-- One row per run report, every contract column as landing states it. Silver does not re-derive
-- complete or is_replay: landing owns both.
SELECT
    *,
    getvariable('transformation_version') AS transformation_version
FROM landing_walk
ORDER BY area, run_started_at
