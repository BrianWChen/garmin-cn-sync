# garmin-cn-sync

English | [简体中文](README.zh-CN.md)

Manually sync activities between Garmin Connect China and Global, copy scheduled workouts from Global to China, and import daily China wellness data into Intervals.icu.

Windows / Python 3.12+. Run after a ride; no scheduled job is required.

## Setup

```powershell
git clone https://github.com/BrianWChen/garmin-cn-sync.git
cd .\garmin-cn-sync
python -m pip install --target .packages -r requirements.txt
```

Double-click `preview.cmd`. Enter your China (CN) and Global (COM) Garmin credentials and any verification code requested. Password input is hidden and passwords are not saved. Browser login is separate; the script does not read Edge cookies.

Review the preview, then double-click `sync.cmd`. For subsequent runs, wait until your bike computer or Zwift activity appears in its original account before syncing.

Tokens, sync state, and activity exports are stored in the Git-ignored `private/` directory. Tokens grant account access: do not upload or share this directory. Expired sessions may require another login.

## Sync scope

| Source | Destination | Content |
| --- | --- | --- |
| Garmin China | Garmin Global | Missing completed activities from the last 30 days |
| Garmin Global | Garmin China | Missing completed activities from the last 30 days, including Zwift |
| Garmin Global | Garmin China | Scheduled structured workouts and dates for the next 14 days, including today |
| Garmin China | Intervals.icu | Daily wellness for the last 7 days, including today; requires an API key |

Activity sync includes all sources in both directions. Workout sync includes sessions already sent to Global by Intervals.icu, but does not copy the entire unscheduled workout library. Existing bike computer, Zwift, and Intervals.icu connections remain unchanged.

After importing workouts, sync the device with the China Garmin Connect app or Garmin Express. This script does not explicitly push workouts to a device. If they do not appear on the device calendar, send them manually from China Connect.

## Commands

Run in PowerShell from the repository directory:

```powershell
python sync_garmin.py                         # Preview without uploading
python sync_garmin.py --apply                 # Activities, names, workouts, and configured wellness
python sync_garmin.py --apply --since 2025-01-01  # Include older activities and wellness
python sync_garmin.py --apply --only activities
python sync_garmin.py --apply --only names     # Correct names of matched activities
python sync_garmin.py --apply --only workouts --workout-days 28
```

Reinstall dependencies with `python -m pip install --target .packages -r requirements.txt`.

## Historical date ranges

`--from` and `--to` select a range **including both boundary dates**. `--since` and `--until` remain equivalent aliases. The end defaults to today. Without a start date, activities look back 30 days and wellness covers 7 days. The start cannot be after the end, and the end cannot be in the future.

```powershell
python sync_garmin.py --from 2025-01-01 --to 2025-01-31 --only activities
python sync_garmin.py --apply --from 2025-01-01 --to 2025-01-31 --only activities
python sync_garmin.py --apply --from 2025-01-01 --to 2025-01-31 --only wellness
python sync_garmin.py --apply --from 2025-01-01 --to 2025-01-31 --only names
```

Launchers accept the same arguments: `sync.cmd --from 2025-01-01 --to 2025-01-31` or `wellness.cmd --from 2025-01-01 --to 2025-01-31`.

Historical dates apply to activities, names, and wellness. Workouts still use the upcoming range controlled by `--workout-days`. Select `--only activities` or `--only wellness` when backfilling history to avoid also processing upcoming workouts. Wellness is read day by day; monthly batches can be useful for long ranges.

## Wellness: Garmin China → Intervals.icu

Set `INTERVALS_ICU_API_KEY`; `/athlete/0` resolves the current account automatically. Optionally set `INTERVALS_ICU_ATHLETE_ID`. Alternatively, run `python sync_garmin.py --configure-intervals` to enter the key locally with hidden input and save it in the ignored `private/intervals.json`.

```powershell
python sync_garmin.py --only wellness                      # Preview the last 7 days
python sync_garmin.py --apply --only wellness              # Sync the last 7 days
python sync_garmin.py --apply --only wellness --since 2025-01-01
python sync_garmin.py --only wellness --since 2025-01-09 --until 2025-01-09
```

Double-click `wellness.cmd` to sync only wellness. Once configured, `sync.cmd` includes wellness alongside activities, names, and workouts. An explicit `--from` / `--since` applies to both activities and wellness. Each run compares destination values again, skips unchanged fields, and picks up Garmin corrections.

Fields: resting heart rate, overnight sleep duration (excluding naps), sleep score, average sleeping heart rate, average sleeping SpO2, overnight average HRV (RMSSD), and steps.

Sleep belongs to Garmin's wake-up calendar date. Continuous all-day heart rate and SpO2 series are not imported. Neither the seven-day HRV average nor the highest five-minute value is used. Garmin stress is not mapped to the subjective stress field.

China Garmin is the source of truth for these fields: differing Intervals.icu values, including manual entries, are overwritten. Missing source values are omitted, never sent as null. Other fields such as weight, subjective fatigue, and comments are not written. Before each update, the existing record is backed up under `private/wellness-backups/<athlete>/`; changed fields are read back and checked afterward. Today's steps may be incomplete and refresh on the next run. Avoid competing integrations overwriting the same fields.

## Duplicate detection and limitations

- Names follow the original upload direction: China-origin activities use China names; Global-origin activities use Global names. Already synced activities are checked too. For existing pairs without an upload record, a Global name starting with `Zwift` selects Global; otherwise China is used. Destination-only name edits will be overwritten on the next run.
- Activities are matched by UTC start time within 3 seconds, duration within 5 seconds, and distance within 100 meters. Similar but distinct activities can be skipped and need manual review. Originals are not deleted.
- Original FIT, TCX, or GPX exports are uploaded. A pending record is saved before upload. If the response is inconclusive, the destination is queried again after waits of 2, 4, and 8 seconds, plus request time. Unconfirmed uploads stop the run. Subsequent runs check pending entries without uploading again. Only clear a pending entry after verifying that the upload failed and no matching destination activity exists.
- Workout comparison uses the imported name, description, steps, and targets, with recorded cross-region IDs. Calendar entries are deduplicated by destination workout ID and date.
- COM is authoritative for synced calendar entries. If a day changes, replacements are prepared first, then the old managed CN schedules are removed before the current COM sessions are scheduled. Duplicates, cancellations, and rescheduling are reconciled within the sync window. Unrelated CN workouts are preserved. Old workout templates may remain in the library; only calendar schedules are removed. Calendar replacements are backed up under `private/workout-backups/` and read back for verification. Rerunning repairs an interrupted replacement.
- This is not a complete account clone. Body Battery, gear, photos, badges, and training status are not synced. Activity imports do not guarantee every Garmin-derived metric or downstream third-party export.
- This is unofficial software. Garmin interface or authentication changes can break it. Check an actual activity and workout after the first run.
- `Calendar response has no workoutId` means the calendar schema differs from the expected format. The script stops; the mapping must be adjusted before workout sync can be considered successful.
- If a crash leaves `private/sync.lock`, confirm that no other sync process is running before removing it.

## Validation

Uses `garminconnect==0.3.17`. Bidirectional activity sync, name correction, and wellness updates with per-day read-back have been verified on real accounts. Device-side workout delivery still needs checking on the bike computer. All test fixtures are synthetic.

Run the 19 offline tests:

```powershell
python -m unittest test_sync test_wellness test_workouts -v
```

## License

Copyright (c) 2026 BrianWChen. This project's own code is available under the
[MIT License](LICENSE). Dependencies retain their own licenses; see
[Third-party notices](THIRD_PARTY_NOTICES.md) for `python-garminconnect` attribution.
This is an unofficial project, not affiliated with or endorsed by Garmin or
Intervals.icu.

## References

- [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
- [Intervals.icu wellness API fields](https://forum.intervals.icu/t/best-way-to-integrate-apple-watch-apple-health-data-into-intervals-icu/5776/12)
- [Garmin HRV Status](https://www.garmin.com/en-IE/garmin-technology/health-science/hrv-status/)
