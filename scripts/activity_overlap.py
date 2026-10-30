"""Conservative pairing of independent Garmin indoor and Zwift ride records."""
import math
from scripts.sync_utils import parse_gmt


def interval(activity):
    try:
        start = parse_gmt(activity['startTimeGMT'])
        seconds = float(activity.get('elapsedDuration') or activity['duration'])
        timer = float(activity['duration'])
        if not all(math.isfinite(x) and x > 0 for x in (seconds, timer)):
            return None
        return start.timestamp(), seconds, timer
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def source(activity):
    sport = (activity.get('activityType') or {}).get('typeKey', '').lower()
    maker = str(activity.get('manufacturer') or '').upper()
    name = str(activity.get('activityName') or '').lower()
    if maker == 'ZWIFT' and sport in ('virtual_ride', 'indoor_cycling', 'cycling'):
        return 'zwift'
    if not maker and name.startswith('zwift') and sport in ('virtual_ride', 'indoor_cycling', 'cycling'):
        return 'zwift'
    if maker == 'GARMIN' and sport == 'indoor_cycling':
        return 'garmin'
    return None


def overlap(a, b):
    left, right = interval(a), interval(b)
    if left is None or right is None:
        return None
    a_start, a_elapsed, a_timer = left
    b_start, b_elapsed, b_timer = right
    delta = abs(a_start-b_start)
    shared = max(0, min(a_start+a_elapsed, b_start+b_elapsed)-max(a_start,b_start))
    fraction = shared/min(a_elapsed,b_elapsed)
    duration_ratio = min(a_timer,b_timer)/max(a_timer,b_timer)
    if delta <= 600 and fraction >= 0.90 and duration_ratio >= 0.85:
        return delta, fraction, duration_ratio
    return None


def excluded_activities(activities, report=True):
    """Plan both directions before any writes; ambiguous split sessions are held."""
    cn = [a for a in activities['cn'] if source(a) == 'garmin']
    com = [a for a in activities['com'] if source(a) == 'zwift']
    edges = [(a, b, score) for a in cn for b in com if (score := overlap(a,b))]
    counts_cn, counts_com = {}, {}
    for a,b,_ in edges:
        counts_cn[str(a['activityId'])] = counts_cn.get(str(a['activityId']),0)+1
        counts_com[str(b['activityId'])] = counts_com.get(str(b['activityId']),0)+1
    blocked = {'cn': set(), 'com': set()}
    for a,b,(delta,fraction,ratio) in edges:
        aid, bid = str(a['activityId']), str(b['activityId'])
        blocked['cn'].add(aid)
        blocked['com'].add(bid)
        ambiguous = counts_cn[aid] > 1 or counts_com[bid] > 1
        if report:
            print(f'{"REVIEW ambiguous overlap" if ambiguous else "KEEP originals"} '
                  f'cn:{aid} / com:{bid}: start delta {delta:.0f}s, '
                  f'overlap {fraction:.1%}, duration ratio {ratio:.1%}; no upload or rename')
    return blocked
