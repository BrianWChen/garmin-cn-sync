"""Synthetic calendar fixtures; no real account data."""
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import sync_garmin as s


def workout(wid, name):
    return {'workoutId': wid, 'workoutName': name, 'sportType': {'sportTypeId': 2},
            'workoutSegments': []}


def entry(schedule_id, wid, day='2000-01-09'):
    return {'id': schedule_id, 'workoutId': wid, 'date': day, 'itemType': 'workout'}


class API:
    def __init__(self, workouts, items):
        self.workouts = {str(w['workoutId']): w for w in workouts}
        self.items = list(items)
        self.events = []
        self.fail_schedule = False

    def get_workouts(self, start, limit):
        return list(self.workouts.values())[start:start+limit]

    def get_workout_by_id(self, wid):
        return self.workouts[str(wid)]

    def get_scheduled_workouts(self, year, month):
        return {'calendarItems': list(self.items)}

    def upload_workout(self, payload):
        wid = max(int(x) for x in self.workouts) + 1
        w = dict(payload, workoutId=wid)
        self.workouts[str(wid)] = w
        self.events.append(('upload', wid))
        return w

    def unschedule_workout(self, schedule_id):
        self.events.append(('remove', schedule_id))
        self.items = [x for x in self.items if x['id'] != schedule_id]

    def schedule_workout(self, wid, day):
        if self.fail_schedule:
            raise RuntimeError('simulated schedule failure')
        self.events.append(('schedule', str(wid), day))
        self.items.append(entry(1000 + len(self.events), int(wid), day))


class WorkoutTests(unittest.TestCase):
    def test_cross_month_calendar_deduplicates_same_schedule(self):
        item = entry(100, 10, '2000-01-31')
        api = API([workout(10, 'Session')], [item])
        self.assertEqual(s.calendar(api, date(2000, 1, 24), date(2000, 2, 6)), [item])

    def test_conflicting_calendar_id_stops_before_any_writes(self):
        item = entry(200, 20, '2000-01-31')
        responses = [{'calendarItems': [item]},
                     {'calendarItems': [dict(item, workoutId=21)]}]
        with patch.object(self.src, 'get_scheduled_workouts', side_effect=responses):
            before = dict(self.state['workouts'])
            with self.assertRaisesRegex(RuntimeError, 'Conflicting calendar entries'):
                s.sync_workouts({'com': self.src, 'cn': self.dst}, self.state,
                                date(2000, 1, 24), date(2000, 2, 6), True)
        self.assertEqual(self.src.events, [])
        self.assertEqual(self.dst.events, [])
        self.assertEqual(self.state['workouts'], before)
        s.save.assert_not_called()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=s.ROOT)
        self.addCleanup(self.temp.cleanup)
        self.private_patch = patch.object(s, 'PRIVATE', Path(self.temp.name))
        self.private_patch.start()
        self.addCleanup(self.private_patch.stop)
        self.save_patch = patch.object(s, 'save')
        self.save_patch.start()
        self.addCleanup(self.save_patch.stop)
        self.old = workout(10, 'Old session')
        self.new = workout(20, 'Updated session')
        self.src = API([self.new], [entry(200, 20)])
        self.dst = API([self.old, workout(99, 'Manual CN workout')], [entry(100, 10), entry(199, 99)])
        self.state = {'workouts': {f'1:{s.fingerprint(self.old)}': '10'}}

    def run_sync(self, apply=True):
        s.sync_workouts({'com': self.src, 'cn': self.dst}, self.state,
                        date(2000,1,9), date(2000,1,10), apply)

    def test_changed_source_id_replaces_old_before_scheduling_and_preserves_manual(self):
        self.run_sync()
        self.assertEqual([e[0] for e in self.dst.events], ['upload', 'remove', 'schedule'])
        self.assertIn(('remove', 100), self.dst.events)
        self.assertEqual({x['workoutId'] for x in self.dst.items}, {99, 100})
        before = list(self.dst.events)
        self.run_sync()
        self.assertEqual(self.dst.events, before)

    def test_duplicate_schedules_are_replaced_by_one(self):
        self.dst.items.append(entry(101, 10))
        self.src = API([self.old], [entry(200, 10)])
        self.run_sync()
        self.assertEqual(len([x for x in self.dst.items if x['workoutId'] == 10]), 1)
        self.assertEqual([e[1] for e in self.dst.events if e[0] == 'remove'], [100, 101])

    def test_cancelled_source_removes_managed_only(self):
        self.src.items = []
        self.run_sync()
        self.assertEqual(self.dst.items, [entry(199, 99)])

    def test_failed_backup_preserves_calendar(self):
        self.src.items = []
        before = list(self.dst.items)
        with patch.object(Path, 'write_text', side_effect=OSError('synthetic backup failure')):
            with self.assertRaises(OSError):
                self.run_sync()
        self.assertEqual(self.dst.items, before)
        self.assertEqual(self.dst.events, [])
        s.save.assert_not_called()

    def test_preview_makes_no_changes(self):
        self.run_sync(False)
        self.assertEqual(self.dst.events, [])
        self.assertEqual(len(self.state['workouts']), 1)

    def test_failed_schedule_is_recoverable_without_reupload(self):
        self.dst.fail_schedule = True
        with self.assertRaisesRegex(RuntimeError, 'simulated'):
            self.run_sync()
        self.dst.fail_schedule = False
        self.run_sync()
        self.assertEqual(len([x for x in self.dst.events if x[0] == 'upload']), 1)
        self.assertEqual({x['workoutId'] for x in self.dst.items}, {99, 100})


if __name__ == '__main__':
    unittest.main()
