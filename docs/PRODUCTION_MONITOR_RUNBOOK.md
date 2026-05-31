# MemoX Production Monitor Runbook

Use this runbook when the `Production Monitor` workflow or
`scripts/production_monitor_check.py` reports `warning` or `error`.

## First Response

1. Open the failed GitHub Actions run and read the Step Summary.
2. Download the `production-monitor-report` artifact and keep it with the
   incident notes.
3. Log in to MemoX as an administrator and open System Status.
4. If the service is reachable, export a diagnostics bundle from
   `/api/system/diagnostics/export`.
5. Check whether the alert is new or repeating by comparing recent workflow
   runs and operational events.
6. If authentication fails, confirm the workflow uses
   `MEMOX_PRODUCTION_MONITOR_TOKEN` and the deployment host uses the same value
   as `MEMOX_MONITOR_TOKEN`.

## `public_health` Error

This means `/api/health` did not return `healthy`.

- Check container/process status and reverse proxy routing.
- Inspect recent service logs for startup errors, port conflicts, or failed
  configuration expansion.
- Confirm disk space and permissions for `data/`, `workspace/`, and `backups/`.
- If the service cannot start, keep the current files intact and follow
  `RECOVERY_RUNBOOK.md`.

## `system_health` Or `readiness_checks` Warning/Error

These alerts come from the authenticated system health endpoint.

- Open System Status and inspect the failing readiness row.
- For backup warnings, run `scripts/ops_check.py --create-backup` and verify the
  newest archive.
- For archive mirror warnings, confirm `ops.archive_mirror_dir` exists, is
  writable by the service, and has enough free space.
- For SQLite or index errors, run `scripts/ops_check.py` locally before any
  repair. Use `scripts/index_consistency.py --repair` only after creating a
  verified backup.

## `task_jobs` Warning/Error

`manual_retryable` means jobs are waiting for an operator decision.
`needs_intervention` means at least one background job requires attention before
normal operation can be trusted.

- Review task events for the affected jobs.
- Retry only failures marked retryable, such as timeout or lease loss.
- Do not blindly retry non-retryable exceptions; inspect the failure message and
  source task input first.
- If repeated lease losses appear, check restarts, process crashes, and host
  resource pressure.

## `media_jobs` Warning

This usually means I2V work is accumulating faster than providers or workers can
complete it.

- Check provider status, API quota, and recent I2V error events.
- Inspect persisted queued/running counts in the monitor summary.
- Temporarily reduce new I2V submissions if running jobs remain high.
- Raise workflow thresholds only after confirming the queue depth is expected
  for the current traffic level.

## Operational Events

`ops_error_events` is an error because the monitor found recent operational
error events. `ops_warning_events` is a warning because warnings may still
represent a degraded dependency.

- Open `/api/system/events?status=error&limit=20` or
  `/api/system/events?status=warning&limit=20`.
- Group events by source and operation before changing configuration.
- For repeated provider failures, check keys, model names, quota, network
  reachability, and provider status.
- For lifecycle or backup errors, run the matching operation manually with
  logs visible.

## Tool Audit Alerts

Tool errors usually indicate broken tool backends, bad model tool arguments, or
provider instability. Tool rejections may be normal when the policy blocks risky
requests, but a spike can indicate attack traffic or overly strict policy.

- Open `/api/system/tool-audit?status=error&limit=20` and
  `/api/system/tool-audit?status=rejected&limit=20`.
- Compare the tool name, requesting workflow, and rejection reason.
- For expected public web usage, tune `tool_policy.web` thresholds rather than
  disabling policy checks.
- Keep larger permissions only when bounded by timeout, response-size, and
  domain/host controls.

## Closing The Alert

Before closing the incident:

- Re-run the `Production Monitor` workflow manually.
- Save the passing monitor artifact or link in the incident notes.
- If any secret appeared in logs, chat, or downloaded artifacts, rotate it.
- If thresholds were changed, record why the new value matches expected traffic.
