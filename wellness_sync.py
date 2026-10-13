"""Daily Garmin CN summaries -> Intervals.icu wellness, with partial updates."""
import getpass
import json
import math
import os
from datetime import datetime, timedelta, timezone


FIELDS = ('restingHR', 'sleepSecs', 'sleepScore', 'avgSleepingHR', 'spO2', 'hrv', 'steps',
          'respiration', 'weight', 'readiness', 'floorsClimbed')


def present_number(value, minimum, maximum):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and minimum <= value <= maximum)


def daily_weights(body):
    """Latest valid measurement per Garmin calendar day; grams -> kilograms."""
    result = {}
    for item in (body or {}).get('dateWeightList') or []:
        day, grams = item.get('calendarDate'), item.get('weight')
        timestamp = item.get('timestampGMT')
        if not day or not present_number(grams, 1000, 1000000):
            continue
        if not present_number(timestamp, 0, 1e16):
            continue
        if day not in result or timestamp > result[day][0]:
            result[day] = (timestamp, grams / 1000)
    return {day: value for day, (_, value) in result.items()}


def morning_readiness(day, entries):
    # The library's convenience helper falls back to arbitrary snapshots.
    # Only an explicit wake-up snapshot is acceptable for this field.
    entries = [entries] if isinstance(entries, dict) else (entries or [])
    candidates = [x for x in entries
                  if x.get('calendarDate') == day
                  and x.get('inputContext') == 'AFTER_WAKEUP_RESET'
                  and present_number(x.get('score'), 0, 100)]
    if not candidates:
        return None
    selected = max(candidates, key=lambda x: (bool(x.get('primaryActivityTracker')),
                                             str(x.get('timestamp') or '')))
    return selected['score']


def optional_data(api, method, *args):
    from garminconnect import GarminConnectNotFoundError
    try:
        return getattr(api, method)(*args)
    except GarminConnectNotFoundError:
        print(f'SKIP {method}: source endpoint has no data')
        return None


def map_wellness(day, stats, sleep, hrv, weight=None, readiness=None):
    dto = (sleep or {}).get('dailySleepDTO') or {}
    summary = (hrv or {}).get('hrvSummary') or {}
    for label, obj in (('stats', stats), ('sleep', dto), ('hrv', summary)):
        if obj.get('calendarDate') and obj['calendarDate'] != day:
            raise RuntimeError(f'Garmin {label} returned a different date for {day}.')
    # Sleep belongs to the wake-up date supplied by Garmin, not the start date.
    values = {
        'restingHR': (stats.get('restingHeartRate'), 1, 250),
        'sleepSecs': (dto.get('sleepTimeSeconds'), 1, 86400),
        'sleepScore': ((dto.get('sleepScores') or {}).get('overall', {}).get('value'), 0, 100),
        'avgSleepingHR': (dto.get('avgHeartRate'), 1, 250),
        'spO2': (dto.get('averageSpO2Value'), 1, 100),
        'hrv': (summary.get('lastNightAvg', (sleep or {}).get('avgOvernightHrv')), 1, 1000),
        'steps': (stats.get('totalSteps'), 0, 200000),
        'respiration': (dto.get('averageRespirationValue'), 1, 100),
        'weight': (weight, 1, 1000),
        'readiness': (readiness, 0, 100),
        'floorsClimbed': (stats.get('floorsAscended'), 0, 10000),
    }
    mapped = {key: value for key, (value, lo, hi) in values.items()
              if present_number(value, lo, hi)}
    if 'floorsClimbed' in mapped:
        mapped['floorsClimbed'] = math.floor(mapped['floorsClimbed'] + 0.5)
    return mapped


def request_json(session, method, path, **kwargs):
    response = session.request(method, 'https://intervals.icu/api/v1' + path,
                               timeout=30, **kwargs)
    if not response.ok:
        # Response bodies / request headers may contain private information.
        raise RuntimeError(f'Intervals.icu {method} failed: HTTP {response.status_code}.')
    return response.json()


def credentials(private, configure=False):
    config_file = private / 'intervals.json'
    if configure:
        key = getpass.getpass('Intervals.icu API Key (hidden): ').strip()
        athlete = input('Athlete ID (Enter = current account): ').strip() or '0'
        if not key:
            raise RuntimeError('API Key is empty.')
        private.mkdir(exist_ok=True)
        config_file.write_text(json.dumps({'api_key': key, 'athlete_id': athlete}), encoding='utf-8')
        print('Saved local configuration to private/intervals.json.')
    config = json.loads(config_file.read_text(encoding='utf-8')) if config_file.exists() else {}
    key = os.getenv('INTERVALS_ICU_API_KEY') or config.get('api_key')
    athlete = os.getenv('INTERVALS_ICU_ATHLETE_ID') or config.get('athlete_id', '0')
    if not key:
        raise RuntimeError('Set INTERVALS_ICU_API_KEY or run --configure-intervals locally.')
    if not str(athlete).replace('i', '', 1).isdigit():
        raise RuntimeError('Invalid Intervals.icu athlete ID.')
    return key, athlete


def configured(private):
    return bool(os.getenv('INTERVALS_ICU_API_KEY') or (private / 'intervals.json').exists())


def sync_wellness(api, state, start, end, apply, private, save):
    import requests
    key, athlete = credentials(private)
    with requests.Session() as session:
        session.auth = ('API_KEY', key)
        profile = request_json(session, 'GET', f'/athlete/{athlete}')
        athlete = profile['id']
        records = state.setdefault('wellness', {}).setdefault(athlete, {})
        weights = daily_weights(optional_data(api, 'get_body_composition',
                                              start.isoformat(), end.isoformat()))
        for offset in range((end - start).days + 1):
            day = (start + timedelta(days=offset)).isoformat()
            stats = api.get_stats(day) or {}
            sleep = api.get_sleep_data(day) or {}
            hrv = api.get_hrv_data(day) or {}
            readiness = morning_readiness(day, optional_data(api, 'get_training_readiness', day))
            source = map_wellness(day, stats, sleep, hrv, weights.get(day), readiness)
            if not source:
                print(f'SKIP wellness {day}: no source data')
                continue
            path = f'/athlete/{athlete}/wellness/{day}'
            existing = request_json(session, 'GET', path)
            changes = {k: v for k, v in source.items() if existing.get(k) != v}
            if not changes:
                print(f'SKIP wellness {day}: already current')
                continue
            print(f'{"UPDATE" if apply else "WOULD UPDATE"} wellness {day}: '
                  + json.dumps(changes, ensure_ascii=False))
            if not apply:
                continue
            backup = private / 'wellness-backups' / athlete
            backup.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            (backup / f'{day}-{stamp}.json').write_text(
                json.dumps({'before': existing, 'changes': changes}, ensure_ascii=False, indent=2),
                encoding='utf-8')
            # Omit missing fields entirely, preserving unrelated wellness values.
            request_json(session, 'PUT', path, json=changes)
            actual = request_json(session, 'GET', path)
            if any(actual.get(k) != v for k, v in changes.items()):
                raise RuntimeError(f'Wellness {day} read-back did not confirm all fields. Rerun to reconcile.')
            records[day] = {'fields': source, 'confirmedAt': stamp}
            save(state)
            print(f'CONFIRMED wellness {day}')
