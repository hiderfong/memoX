# MemoX Recovery Runbook

This runbook is for a single-node MemoX deployment that stores runtime state in
`config.yaml`, `.env`, `data/`, `workspace/`, and `backups/`.

Use it during production incidents, restore drills, upgrades, and host
migrations. Prefer rehearsing the full flow on a disposable host before a real
maintenance window.

## Recovery Priorities

1. Preserve evidence before changing state.
2. Stop new writes if data integrity is uncertain.
3. Verify the candidate backup before restoring it.
4. Run a restore drill or preflight before any overwrite.
5. Repair indexes and validate user workflows after the service starts.

## Roles And Inputs

Before starting, record:

- Incident owner and approver.
- Deployment root path.
- Admin login token for API-based recovery.
- Candidate backup archive name.
- Latest mirrored backup path, if `ops.archive_mirror_dir` is enabled.
- Maintenance window start and expected end.

Use these shell variables in the examples:

```bash
export MEMOX_URL="http://localhost:8080"
export MEMOX_TOKEN="<admin-token>"
export BACKUP_NAME="<backup-file>.tar.gz"
```

## Normal Readiness Check

Run this from the deployment root during routine checks, before upgrades, and
before a planned restore:

```bash
uv run --extra dev python scripts/ops_check.py
```

For a deeper pre-change check:

```bash
uv run --extra dev python scripts/ops_check.py --create-backup --restore-drill
uv run --extra dev python scripts/docker_smoke_test.py
```

Expected result: no `error` status. Warnings about a fresh deployment without
backups are acceptable only before real users have stored data.

## Incident Triage

First capture current state:

```bash
curl -fsS "$MEMOX_URL/api/health"
curl -fsS "$MEMOX_URL/api/system/health" -H "Authorization: Bearer $MEMOX_TOKEN"
curl -fsS "$MEMOX_URL/api/system/backups" -H "Authorization: Bearer $MEMOX_TOKEN"
curl -fsS "$MEMOX_URL/api/system/events?limit=50" -H "Authorization: Bearer $MEMOX_TOKEN"
curl -fsS "$MEMOX_URL/api/system/tool-audit?limit=50" -H "Authorization: Bearer $MEMOX_TOKEN"
curl -fsS -OJ "$MEMOX_URL/api/system/diagnostics/export" -H "Authorization: Bearer $MEMOX_TOKEN"
```

If writes may make the issue worse, stop the service:

```bash
docker compose down
```

If the service is down already, capture host state instead:

```bash
uv run --extra dev python scripts/ops_check.py --json > memox-ops-check.json
uv run --extra dev python scripts/backup_restore.py create --json > memox-safety-backup.json
```

`scripts/ops_check.py` uses `ops.auto_backup_interval_hours` and
`ops.max_backups` from the active config as its default backup thresholds, so
the CLI verdict should match the authenticated system health page unless you
override those values with command-line flags.

Keep diagnostic bundles and backup archives out of public issue trackers. They
may contain uploaded documents, vector indexes, API keys, and workspace output.

## Backup Selection

List local archives:

```bash
uv run --extra dev python scripts/backup_restore.py inspect "backups/$BACKUP_NAME"
uv run --extra dev python scripts/backup_restore.py verify "backups/$BACKUP_NAME"
```

When `ops.archive_mirror_dir` is enabled, also confirm the mirrored file exists
under:

```text
<mirror>/backups/<backup-file>.tar.gz
```

Prefer the newest verified backup from before the first known-bad write. Do not
restore an archive that fails checksum verification.

## Restore Drill

Always run at least one non-destructive restore check:

```bash
mkdir -p /tmp/memox-restore-check
uv run --extra dev python scripts/backup_restore.py restore "backups/$BACKUP_NAME" --target /tmp/memox-restore-check
uv run --extra dev python scripts/restore_drill.py
```

If the service is still healthy enough to answer admin API calls, also run the
API preflight:

```bash
curl -fsS -X POST "$MEMOX_URL/api/system/backups/$BACKUP_NAME/restore-preflight" \
  -H "Authorization: Bearer $MEMOX_TOKEN"
```

Read the preflight output carefully. A normal in-place restore usually reports
overwrite conflicts and `safe_without_overwrite=false`; this is why a maintenance
window and explicit acknowledgements are required.

## API Restore Path

Use this path when the current service is reachable and the selected backup is
available under the deployment `backups/` directory.

1. Announce maintenance and stop user traffic at the reverse proxy if possible.
2. Export diagnostics.
3. Run `restore-preflight`.
4. Execute the guarded restore:

```bash
curl -fsS -X POST "$MEMOX_URL/api/system/backups/$BACKUP_NAME/restore" \
  -H "Authorization: Bearer $MEMOX_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"confirm_archive_name\":\"$BACKUP_NAME\",\"acknowledge_overwrite\":true,\"acknowledge_maintenance_mode\":true}"
```

The API restore creates a verified safety backup before writing restored files.
Save the returned `safety_backup.archive` path in the incident notes.

After the restore:

```bash
curl -fsS -X POST "$MEMOX_URL/api/system/indexes/repair" -H "Authorization: Bearer $MEMOX_TOKEN"
docker compose restart memox
curl -fsS "$MEMOX_URL/api/system/health" -H "Authorization: Bearer $MEMOX_TOKEN"
```

## Offline Restore Path

Use this path when the service cannot start or API authentication is unavailable.

From the deployment root:

```bash
docker compose down
uv run --extra dev python scripts/backup_restore.py create --output "backups/pre-restore-safety.tar.gz"
uv run --extra dev python scripts/backup_restore.py verify "backups/$BACKUP_NAME"
uv run --extra dev python scripts/backup_restore.py restore "backups/$BACKUP_NAME" --target . --overwrite
uv run --extra dev python scripts/index_consistency.py --repair
docker compose up -d
```

Then validate:

```bash
curl -fsS "$MEMOX_URL/api/health"
curl -fsS "$MEMOX_URL/api/system/health" -H "Authorization: Bearer $MEMOX_TOKEN"
uv run --extra dev python scripts/ops_check.py
```

If the service still does not start, inspect container logs:

```bash
docker compose logs --tail=200 memox
```

## Search Or Index Recovery

If uploads and chat work but search is stale, missing, or inconsistent, avoid a
full restore until index repair has been tried.

Read-only audit:

```bash
uv run --extra dev python scripts/index_consistency.py
```

Repair:

```bash
docker compose down
uv run --extra dev python scripts/backup_restore.py create
uv run --extra dev python scripts/index_consistency.py --repair
docker compose up -d
curl -fsS -X POST "$MEMOX_URL/api/system/indexes/repair" -H "Authorization: Bearer $MEMOX_TOKEN"
```

## Post-Restore Validation

Do not close the maintenance window until these pass:

- `curl -fsS "$MEMOX_URL/api/health"`
- Authenticated `GET /api/system/health` has no `error` status.
- `uv run --extra dev python scripts/ops_check.py` has no `error` status.
- Admin can list documents and groups.
- A known document is searchable.
- Recent operational events include restore, index repair, and backup
  maintenance records.
- A fresh backup can be created and verified.
- If `ops.archive_mirror_dir` is set, a fresh backup appears under
  `<mirror>/backups/`.

Use:

```bash
curl -fsS "$MEMOX_URL/api/system/events?limit=20" -H "Authorization: Bearer $MEMOX_TOKEN"
curl -fsS "$MEMOX_URL/api/system/tool-audit?status=error&limit=20" -H "Authorization: Bearer $MEMOX_TOKEN"
curl -fsS -X POST "$MEMOX_URL/api/system/maintenance/backup?force=true" -H "Authorization: Bearer $MEMOX_TOKEN"
```

## Rollback From A Bad Restore

If the restore made the deployment worse and a safety backup was created:

```bash
docker compose down
uv run --extra dev python scripts/backup_restore.py verify "<safety-backup-path>"
uv run --extra dev python scripts/backup_restore.py restore "<safety-backup-path>" --target . --overwrite
uv run --extra dev python scripts/index_consistency.py --repair
docker compose up -d
```

Then repeat post-restore validation.

## Communication Notes

For user-facing updates, report:

- Whether data writes are paused.
- Latest verified backup timestamp.
- Whether an external mirror copy exists.
- Restore path selected: API restore, offline restore, or index repair.
- Expected next update time.
- Any confirmed data loss window.

Avoid sharing raw diagnostic bundles or backup archives in chat, email, or issue
trackers unless the channel is approved for secrets and user documents.

## Routine Schedule

Recommended minimum cadence for real users:

- Daily: confirm `/api/system/health` and latest backup age.
- Weekly: run `scripts/ops_check.py --restore-drill`.
- Before every upgrade: run `scripts/docker_smoke_test.py` and create a verified
  backup.
- Monthly: restore the newest mirrored backup on a disposable host.
- After every incident: export diagnostics, preserve the safety backup, and write
  a short timeline.
