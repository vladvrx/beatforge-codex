# Recover an interrupted audio upload

Normal audio and artifact transfers have a five-minute deadline. They remove partial files on timeout or disconnect. Expired pending audio reservations are reclaimed when the next upload is reserved. Completed audio is retained for generation and revisions.

A process crash can bypass normal cleanup and leave an audio reservation in `writing`. Use the offline maintenance command for this case. It is intentionally unavailable to assistant tools and HTTP clients.

1. Stop **every gateway process** using the database. A SQLite lock cannot stop a process that already holds an open upload file. Keep the gateway stopped throughout recovery.
2. Back up the database and its adjacent uploads/artifacts directories consistently.
3. Review the dry-run counts:

   ```sh
   python -m beatforge.connector_maintenance --database /var/data/beatforge/jobs.sqlite3
   ```

4. Apply only after verifying the target database and stopped service:

   ```sh
   python -m beatforge.connector_maintenance --database /var/data/beatforge/jobs.sqlite3 --apply --gateway-stopped
   ```

5. Restart the gateway. Retry an unexpired reservation, or reserve a new upload if it expired. Studio automatically renews its expired cached reservation.

The command validates all selected paths before changing files. It removes only partial bytes belonging to reservations in `writing`, then resets those reservations to `pending`. Ready uploads, published artifacts, jobs, presets and feedback are untouched. Repeating recovery is safe after interruption. A path-validation or filesystem error stops recovery; inspect it rather than manually removing unrelated files.

## Reclaim obsolete worker attempts

With the gateway stopped and a consistent backup made, preview artifacts that belong to abandoned worker attempts:

```sh
python -m beatforge.connector_maintenance --database /var/data/beatforge/jobs.sqlite3 --obsolete-artifacts
```

The report includes artifact count and reserved bytes. Add `--apply --gateway-stopped` to remove only that obsolete set. Every artifact referenced by any job result is protected, as is every artifact belonging to the current lease of a running job, even if that lease has expired. Invalid metadata or paths stop cleanup. Run it again after the job queue has resolved expired attempts if those bytes still need reclaiming.

These commands are recovery, not completed-data retention. Old ready uploads and completed maps still require a separate retention policy before sustained hosted use. No live production recovery has been performed; automated tests cover dry-run behavior, preservation of ready audio, published results and current leases, retry, repeat execution and validation failure before removal.
