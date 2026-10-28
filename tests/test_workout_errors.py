"""Workout pagination and destructive-operation guard regressions."""
from datetime import date
from unittest.mock import MagicMock, patch
from scripts import sync_garmin as s
from tests import test_workouts as fixtures

API, workout, entry = fixtures.API, fixtures.workout, fixtures.entry
import unittest


class WorkoutErrorTests(unittest.TestCase):
    def test_library_pagination_and_limit(self):
        api = MagicMock()
        page = [workout(i, 'Session') for i in range(100)]
        api.get_workouts.side_effect = [page, [workout(100, 'Last')]]
        self.assertEqual(len(s.library(api)), 101)
        self.assertEqual(api.get_workouts.call_args.args, (100, 100))
        api.get_workouts.side_effect = None
        api.get_workouts.return_value = page
        with self.assertRaisesRegex(RuntimeError, 'pagination limit'):
            s.library(api)

    def test_calendar_filters_dates_types_and_crosses_year(self):
        api = MagicMock()
        api.get_scheduled_workouts.side_effect = [
            {'calendarItems': [entry(1, 10, '2000-12-30'), entry(2, 10, '2000-12-31T12:00:00'), dict(entry(3, 10, '2000-12-31'), itemType='activity')]},
            {'calendarItems': [entry(4, 10, '2001-01-01'), entry(5, 10, '2001-01-02')]}]
        self.assertEqual([x['id'] for x in s.calendar(api, date(2000,12,31), date(2001,1,1))], [2, 4])
        self.assertEqual([c.args for c in api.get_scheduled_workouts.call_args_list], [(2000,12), (2001,1)])

    def test_missing_workout_id_stops_before_writes(self):
        src, dst = API([], [dict(entry(1, 10), workoutId=None)]), API([], [])
        with self.assertRaisesRegex(RuntimeError, 'no workoutId'):
            s.sync_workouts({'com': src, 'cn': dst}, {}, date(2000,1,9), date(2000,1,9), True)
        self.assertEqual(dst.events, [])


class WorkoutReconciliationTests(unittest.TestCase):
    setUp = fixtures.WorkoutTests.setUp
    run_sync = fixtures.WorkoutTests.run_sync
    def test_stale_mapping_is_repaired_and_source_template_resolved_once(self):
        self.state['workouts'][f'20:{s.fingerprint(self.new)}'] = '999'
        self.src.items.append(entry(201, 20, '2000-01-10'))
        self.run_sync()
        self.assertEqual(len([e for e in self.dst.events if e[0] == 'upload']), 1)
        self.assertEqual(self.state['workouts'][f'20:{s.fingerprint(self.new)}'], '100')
        self.assertEqual({x['date'] for x in self.dst.items if x['workoutId'] == 100}, {'2000-01-09', '2000-01-10'})

    def test_protected_or_unidentified_schedule_is_preserved(self):
        self.src.items = []
        for change in ({'protectedWorkoutSchedule': True}, {'id': None}):
            self.dst.items = [dict(entry(100, 10), **change)]
            with self.assertRaisesRegex(RuntimeError, 'protected or unidentified'):
                self.run_sync()
            self.assertEqual(self.dst.events, [])

    def test_missing_readback_stops_reconciliation(self):
        with patch.object(self.dst, 'schedule_workout'):
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                self.run_sync()
        self.assertTrue(list(s.PRIVATE.glob('workout-backups/*.json')))
