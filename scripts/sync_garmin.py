"""Manual, additive Garmin CN/COM bridge. Default is a read-only preview."""
import argparse
import copy
from datetime import datetime, timedelta, timezone
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.packages'))
from scripts.sync_utils import parse_gmt
PRIVATE = ROOT / 'private'
STATE = PRIVATE / 'state.json'


def save(state):
    PRIVATE.mkdir(exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, STATE)


def login(region):
    from garminconnect import Garmin
    token_dir = PRIVATE / ('tokens-' + region)
    if token_dir.exists():
        api = Garmin(is_cn=region == 'cn', retry_attempts=0)
        try:
            api.login(str(token_dir))
            return api
        except Exception:
            print(f'{region}: saved session unavailable; please log in again.')
    print(f'Login to Garmin {region.upper()} locally (password is hidden).')
    api = Garmin(input('Email: ').strip(), getpass.getpass('Password: '),
                 is_cn=region == 'cn', retry_attempts=0,
                 prompt_mfa=lambda: input('Verification code: ').strip())
    api.login()
    api.client.dump(str(token_dir))
    return api


def activity_match(a, b):
    # Compare UTC timestamps, not regional IDs or names. Ambiguous matches are skipped.
    try:
        return (abs((parse_gmt(a['startTimeGMT']) - parse_gmt(b['startTimeGMT'])).total_seconds()) <= 3
                and abs(float(a['duration']) - float(b['duration'])) <= 5
                and abs(float(a.get('distance') or 0) - float(b.get('distance') or 0)) <= 100)
    except (KeyError, ValueError, TypeError, OverflowError):
        raise RuntimeError('Activity has missing identity fields; stopped to avoid duplicates.')


def original_file(data):
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            files = [n for n in z.namelist() if n.lower().endswith(('.fit', '.tcx', '.gpx'))]
            if len(files) != 1:
                raise RuntimeError('Original export must contain exactly one activity file.')
            return Path(files[0]).suffix.lower(), z.read(files[0])
    if data[8:12] == b'.FIT':
        return '.fit', data
    raise RuntimeError('Unrecognized original activity export.')


def import_result(result):
    """Accept parsed JSON and response objects from different library versions."""
    if not isinstance(result, dict) and callable(getattr(result, 'json', None)):
        try:
            result = result.json()
        except (ValueError, TypeError):
            return {}
    if not isinstance(result, dict):
        return {}
    dto = result.get('detailedImportResult', result)
    return dto if isinstance(dto, dict) else {}


def confirm_activity(api, activity, start, end):
    # Garmin's search index can lag behind a successful import.
    for delay in (0, 2, 4, 8):
        if delay:
            time.sleep(delay)
        refreshed = api.get_activities_by_date(start, end)
        match = next((b for b in refreshed if activity_match(activity, b)), None)
        if match is not None:
            return match, refreshed
    return None, refreshed


def sync_activities(apis, state, start, end, apply, keep_overlapping_originals=False):
    activities = {r: api.get_activities_by_date(start, end) for r, api in apis.items()}
    from scripts.activity_overlap import excluded_activities
    excluded = excluded_activities(activities) if keep_overlapping_originals else {'cn': set(), 'com': set()}
    for src, dst in [('cn', 'com'), ('com', 'cn')]:
        for a in activities[src]:
            if str(a['activityId']) in excluded[src]:
                continue
            key = f'{src}:{a["activityId"]}->{dst}'
            saved = state['activities'].get(key)
            if saved and saved.get('status') != 'pending':
                continue
            if saved and saved.get('status') == 'pending':
                print(f'CHECK pending {key}')
                if not apply:
                    continue
                match, refreshed = confirm_activity(apis[dst], a, start, end)
                if match is None:
                    raise RuntimeError(f'Previous upload {key} is still unconfirmed. '
                                       'No repeat upload; inspect target account and private/state.json.')
                activities[dst] = refreshed
                state['activities'][key] = {'confirmedBy': 'target-list',
                                            'targetActivityId': match['activityId']}
                save(state)
                continue
            match = next((b for b in activities[dst] if activity_match(a, b)), None)
            if match is not None:
                print(f'SKIP existing {key} {a.get("activityName", "")}')
                if apply:
                    state['activities'][key] = {'confirmedBy': 'target-list',
                                                'targetActivityId': match['activityId']}
                    save(state)
                continue
            print(f'{"COPY" if apply else "WOULD COPY"} {key} {a.get("activityName", "")}')
            if not apply:
                continue
            from garminconnect import Garmin
            suffix, data = original_file(apis[src].download_activity(
                a['activityId'], dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL))
            cache = PRIVATE / 'exports'
            cache.mkdir(exist_ok=True)
            path = cache / f'{src}-{a["activityId"]}{suffix}'
            path.write_bytes(data)
            # Persist before the network write, including crashes and uncertain responses.
            state['activities'][key] = {'status': 'pending',
                                        'sha256': hashlib.sha256(data).hexdigest()}
            save(state)
            result = apis[dst].upload_activity(str(path))
            dto = import_result(result)
            if not dto.get('successes'):
                # A successful write can have an unfamiliar response schema.
                # Confirm against the destination instead of repeating the upload.
                print(f'WAIT confirming import in {dst} (up to 14 seconds plus requests)')
                match, refreshed = confirm_activity(apis[dst], a, start, end)
                if match is None:
                    raise RuntimeError('Upload not confirmed by response or target list. '
                                       'Saved as pending. Rerun later to check; it will not upload again.')
                activities[dst] = refreshed
                print(f'CONFIRMED in {dst}: {match["activityId"]}')
            state['activities'][key] = {'sha256': hashlib.sha256(data).hexdigest()}
            save(state)
            # Prevent this run from copying the imported activity back to its source.
            activities[dst].append(a)


DROP = {'workoutId', 'ownerId', 'author', 'createdDate', 'updatedDate',
        'workoutProvider', 'workoutSourceId', 'shared', 'stepId',
        'segmentId', 'workoutSegmentId'}


def name_source(state, cn, com):
    cn_key = f'cn:{cn["activityId"]}->com'
    com_key = f'com:{com["activityId"]}->cn'
    # An export hash identifies the original upload direction. Mere duplicate
    # matches in the reverse direction must never become the name authority.
    for key, region in ((cn_key, 'cn'), (com_key, 'com')):
        record = state['activities'].get(key, {})
        if record.get('nameSource'):
            return record['nameSource']
        if record.get('sha256'):
            return region
    # For pre-existing pairs without an upload record, choose a stable default.
    return 'com' if str(com.get('activityName', '')).startswith('Zwift') else 'cn'


def sync_activity_names(apis, state, start, end, apply, keep_overlapping_originals=False):
    cn_items = apis['cn'].get_activities_by_date(start, end)
    com_items = apis['com'].get_activities_by_date(start, end)
    from scripts.activity_overlap import excluded_activities
    excluded = excluded_activities({'cn': cn_items, 'com': com_items}) if keep_overlapping_originals else {'cn': set(), 'com': set()}
    for cn in cn_items:
        if str(cn['activityId']) in excluded['cn']:
            continue
        matches = [com for com in com_items if activity_match(cn, com)]
        if len(matches) != 1:
            continue
        com = matches[0]
        if str(com['activityId']) in excluded['com']:
            continue
        region = name_source(state, cn, com)
        src, target, dst = (cn, com, 'com') if region == 'cn' else (com, cn, 'cn')
        name = src.get('activityName')
        if not name or name == target.get('activityName'):
            continue
        print(f'{"RENAME" if apply else "WOULD RENAME"} {dst}:{target["activityId"]}: '
              f'{target.get("activityName", "")} -> {name}')
        if apply:
            apis[dst].set_activity_name(str(target['activityId']), name)
            key = f'{region}:{src["activityId"]}->{dst}'
            record = state['activities'].setdefault(key, {})
            record.update(nameSource=region, targetActivityId=target['activityId'])
            save(state)


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k not in DROP}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def payload(workout):
    # Whitelist the structure needed to create a personal workout across regions.
    return clean({k: copy.deepcopy(workout[k]) for k in (
        'workoutName', 'description', 'sportType', 'workoutSegments',
        'estimatedDurationInSecs', 'estimatedDistanceInMeters') if k in workout})


def fingerprint(workout):
    return hashlib.sha256(json.dumps(payload(workout), sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()


def library(api):
    result = []
    for start in range(0, 10000, 100):
        page = api.get_workouts(start, 100)
        result.extend(page)
        if len(page) < 100:
            return result
    raise RuntimeError('Workout pagination limit reached.')


def calendar(api, start, end):
    months = sorted({(d.year, d.month) for d in
                     (start + timedelta(days=i) for i in range((end-start).days+1))})
    result = []
    seen = {}
    for year, month in months:
        for item in api.get_scheduled_workouts(year, month).get('calendarItems', []):
            day = str(item.get('date', ''))[:10]
            if item.get('itemType') == 'workout' and start.isoformat() <= day <= end.isoformat():
                # Adjacent month responses can include the same schedule.
                sid = str(item['id']) if item.get('id') else None
                if sid in seen:
                    if seen[sid] != item:
                        raise RuntimeError('Conflicting calendar entries for the same schedule id.')
                    continue
                if sid is not None:
                    seen[sid] = item
                result.append(item)
    return result


def sync_workouts(apis, state, start, end, apply):
    from collections import Counter
    src, dst = apis['com'], apis['cn']
    source = calendar(src, start, end)
    target = calendar(dst, start, end)
    mappings = state.setdefault('workouts', {})
    managed = {str(tid) for tid in mappings.values()}
    target_library = library(dst)
    target_ids = {str(w['workoutId']) for w in target_library}
    target_hashes = {fingerprint(dst.get_workout_by_id(w['workoutId'])): str(w['workoutId'])
                     for w in target_library}
    desired = {}
    resolved = {}
    # Prepare the replacements before removing calendar entries. Persist mappings
    # immediately so a failed schedule write is recoverable on the next run.
    for item in source:
        if not item.get('workoutId'):
            raise RuntimeError('Calendar response has no workoutId; inspect account schema first.')
        sid, day = str(item['workoutId']), str(item['date'])[:10]
        if sid not in resolved:
            full = src.get_workout_by_id(sid)
            digest = fingerprint(full)
            marker = f'{sid}:{digest}'
            tid = mappings.get(marker)
            if tid and str(tid) not in target_ids:
                tid = None
            tid = str(tid) if tid else target_hashes.get(digest)
            if not tid:
                print(f'{"CREATE" if apply else "WOULD CREATE"} workout {full.get("workoutName")}')
                if apply:
                    created = dst.upload_workout(payload(full))
                    tid = str(created['workoutId'])
                    target_ids.add(tid)
                    target_hashes[digest] = tid
                else:
                    tid = f'new:{marker}'
            if apply and mappings.get(marker) != tid:
                mappings[marker] = tid
                save(state)
            managed.add(tid)
            resolved[sid] = tid
        desired.setdefault(day, set()).add(resolved[sid])

    days = sorted(set(desired) | {str(x['date'])[:10] for x in target
                                 if str(x.get('workoutId')) in managed})
    for day in days:
        current = [x for x in target if str(x['date'])[:10] == day
                   and str(x.get('workoutId')) in managed]
        wanted = desired.get(day, set())
        if Counter(str(x['workoutId']) for x in current) == Counter(wanted):
            continue
        # Calendar item id is the schedule id, not the workout template id.
        if any(not x.get('id') or x.get('protectedWorkoutSchedule') for x in current):
            raise RuntimeError(f'Cannot safely replace protected or unidentified CN schedule on {day}.')
        print(f'{"REPLACE" if apply else "WOULD REPLACE"} CN calendar {day}: '
              f'{len(current)} old -> {len(wanted)} COM workouts')
        if not apply:
            continue
        backup = PRIVATE / 'workout-backups'
        backup.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        (backup / f'{day}-{stamp}.json').write_text(
            json.dumps({'before': current, 'desiredWorkoutIds': sorted(wanted)},
                       ensure_ascii=False, indent=2), encoding='utf-8')
        for item in current:
            dst.unschedule_workout(item['id'])
        for tid in sorted(wanted):
            dst.schedule_workout(tid, day)
        actual = calendar(dst, datetime.strptime(day, '%Y-%m-%d').date(),
                          datetime.strptime(day, '%Y-%m-%d').date())
        actual_ids = Counter(str(x['workoutId']) for x in actual
                             if str(x.get('workoutId')) in managed)
        if actual_ids != Counter(wanted):
            raise RuntimeError(f'CN calendar replacement for {day} was not confirmed. Rerun to reconcile.')
        print(f'CONFIRMED CN calendar {day}')
    print('COM is authoritative for synced calendar entries; unrelated CN workouts are preserved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Perform uploads (default: preview)')
    parser.add_argument('--keep-overlapping-originals', action='store_true',
                        help='Keep overlapping CN Garmin indoor / COM Zwift rides in their original accounts')
    parser.add_argument('--from', '--since', dest='since', type=str,
                        help='Inclusive activity/wellness start date YYYY-MM-DD (--since is an alias)')
    parser.add_argument('--workout-days', type=int, default=14)
    parser.add_argument('--wellness-days', type=int, default=7, help='Default health-data lookback, including today')
    parser.add_argument('--to', '--until', dest='until',
                        help='Inclusive activity/wellness end date YYYY-MM-DD; default: today')
    parser.add_argument('--configure-intervals', action='store_true', help='Save API key locally, without syncing')
    parser.add_argument('--only', choices=['activities', 'names', 'workouts', 'wellness', 'all'], default='all')
    args = parser.parse_args()
    today = datetime.now(timezone(timedelta(hours=8))).date()
    try:
        end = datetime.strptime(args.until, '%Y-%m-%d').date() if args.until else today
        start = datetime.strptime(args.since, '%Y-%m-%d').date() if args.since else end-timedelta(days=30)
    except ValueError:
        parser.error('Dates must be valid YYYY-MM-DD values.')
    if args.configure_intervals:
        from scripts.wellness_sync import credentials
        credentials(PRIVATE, configure=True)
        return
    if (start > end or end > today or not 1 <= args.workout_days <= 90
            or not 1 <= args.wellness_days <= 365):
        parser.error('Require from <= to <= today, workout-days 1-90, wellness-days 1-365.')
    PRIVATE.mkdir(exist_ok=True)
    # Exclusive lock also prevents parallel double-clicks from racing uploads.
    lock = PRIVATE / 'sync.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError('Another run is active. If it crashed, remove private/sync.lock first.')
    try:
        os.close(fd)
        state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'activities': {}, 'workouts': {}}
        apis = {r: login(r) for r in (('cn',) if args.only == 'wellness' else ('cn', 'com'))}
        if args.only in ('activities', 'all'):
            sync_activities(apis, state, start.isoformat(), end.isoformat(), args.apply,
                            args.keep_overlapping_originals)
        if args.only in ('activities', 'names', 'all'):
            sync_activity_names(apis, state, start.isoformat(), end.isoformat(), args.apply,
                                args.keep_overlapping_originals)
        if args.only in ('workouts', 'all'):
            sync_workouts(apis, state, today, today+timedelta(days=args.workout_days-1), args.apply)
        if args.only in ('wellness', 'all'):
            from scripts.wellness_sync import configured, sync_wellness
            if args.only == 'wellness' or configured(PRIVATE):
                wellness_start = start if args.since else end-timedelta(days=args.wellness_days-1)
                sync_wellness(apis['cn'], state, wellness_start, end, args.apply, PRIVATE, save)
            else:
                print('SKIP wellness: configure Intervals.icu API Key first.')
        for r, api in apis.items():
            api.client.dump(str(PRIVATE / ('tokens-' + r)))
        print('Finished.' if args.apply else 'Preview finished. Use --apply to upload.')
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not print transport exception bodies that may include credentials.
        print(f'Stopped: {type(exc).__name__}.', file=sys.stderr)
        if isinstance(exc, (RuntimeError, ImportError)):
            print(str(exc), file=sys.stderr)
        sys.exit(1)
