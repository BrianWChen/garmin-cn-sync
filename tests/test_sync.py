# All fixtures are synthetic and do not describe a real athlete.
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile
from scripts import sync_garmin as s


class BridgeTests(unittest.TestCase):
    def test_name_authority_follows_upload_not_reverse_match(self):
        cn, com = {'activityId': 1}, {'activityId': 42}
        state = {'activities': {'cn:1->com': {'confirmedBy': 'target-list'},
                                 'com:42->cn': {'sha256': 'original'}}}
        self.assertEqual(s.name_source(state, cn, com), 'com')

    def test_names_only_renames_imported_target(self):
        a = dict(activityId=1, activityName='Auto name', startTimeGMT='2000-01-07 06:00:00', duration=1800, distance=10000)
        b = dict(a, activityId=42, activityName='Zwift session')
        class API:
            def __init__(self, activity):
                self.activity, self.renames = activity, []
            def get_activities_by_date(self, *args):
                return [self.activity]
            def set_activity_name(self, activity_id, title):
                self.renames.append((activity_id, title))
        cn, com = API(a), API(b)
        state = {'activities': {'com:42->cn': {'sha256': 'original'}}}
        with tempfile.TemporaryDirectory(dir=s.ROOT) as tmp, patch.object(s, 'PRIVATE', Path(tmp)), patch.object(s, 'STATE', Path(tmp)/'state.json'):
            s.sync_activity_names({'cn': cn, 'com': com}, state, '2000-01-07', '2000-01-09', False)
            self.assertEqual(cn.renames, [])
            s.sync_activity_names({'cn': cn, 'com': com}, state, '2000-01-07', '2000-01-09', True)
            self.assertEqual(cn.renames, [('1', 'Zwift session')])
            self.assertEqual(com.renames, [])

    def test_delayed_search_index_is_polled(self):
        a = dict(activityId=1, startTimeGMT='2000-01-07 06:00:00', duration=2400, distance=15000)
        class API:
            calls = 0
            def get_activities_by_date(self, *args):
                self.calls += 1
                return [] if self.calls < 3 else [dict(a, activityId=42)]
        api = API()
        with patch.object(s.time, 'sleep') as sleep:
            match, _ = s.confirm_activity(api, a, '2000-01-07', '2000-01-09')
        self.assertEqual(match['activityId'], 42)
        self.assertEqual(sleep.call_count, 2)

    def test_pending_upload_is_not_repeated_when_target_is_still_missing(self):
        a = dict(activityId=1, startTimeGMT='2000-01-07 06:00:00', duration=2400, distance=15000)
        class API:
            def __init__(self, activities):
                self.activities = activities
            def get_activities_by_date(self, *args):
                return list(self.activities)
            def upload_activity(self, path):
                raise AssertionError('Must not repeat a pending upload')
        state = {'activities': {'cn:1->com': {'status': 'pending'}}, 'workouts': {}}
        with patch.object(s.time, 'sleep'), self.assertRaisesRegex(RuntimeError, 'still unconfirmed'):
            s.sync_activities({'cn': API([a]), 'com': API([])}, state,
                              '2000-01-07', '2000-01-09', True)

    def test_import_result_accepts_wrapped_flat_and_response_json(self):
        dto = {'successes': [{'internalId': 42}], 'failures': []}
        class Response:
            def json(self):
                return {'detailedImportResult': dto}
        for value in (dto, {'detailedImportResult': dto}, Response()):
            self.assertEqual(s.import_result(value), dto)
        self.assertEqual(s.import_result(None), {})

    def test_unfamiliar_success_response_is_reconciled_without_reupload(self):
        a = dict(activityId=1, startTimeGMT='2000-01-09 01:00:00', duration=1800, distance=10000)
        class API:
            def __init__(self, activities):
                self.activities, self.uploads = activities, 0
            def get_activities_by_date(self, *args):
                return list(self.activities)
            def download_activity(self, *args, **kwargs):
                return b'12345678.FITdata'
            def upload_activity(self, path):
                self.uploads += 1
                self.activities.append(dict(a, activityId=42))
                return {'unexpected': 'format'}
        cn, com = API([a]), API([])
        with tempfile.TemporaryDirectory(dir=s.ROOT) as tmp, patch.object(s, 'PRIVATE', Path(tmp)), patch.object(s, 'STATE', Path(tmp)/'state.json'):
            state = {'activities': {}, 'workouts': {}}
            for _ in range(2):
                s.sync_activities({'cn': cn, 'com': com}, state, '2000-01-09', '2000-01-09', True)
            self.assertEqual((cn.uploads, com.uploads), (0, 1))

    def test_previous_success_without_state_is_repaired(self):
        a = dict(activityId=1, startTimeGMT='2000-01-09 01:00:00', duration=1800, distance=10000)
        class API:
            def get_activities_by_date(self, *args):
                return [a]
            def upload_activity(self, path):
                self.fail('Existing activity must not be uploaded')
        with tempfile.TemporaryDirectory(dir=s.ROOT) as tmp, patch.object(s, 'PRIVATE', Path(tmp)), patch.object(s, 'STATE', Path(tmp)/'state.json'):
            state = {'activities': {}, 'workouts': {}}
            s.sync_activities({'cn': API(), 'com': API()}, state, '2000-01-09', '2000-01-09', True)
            self.assertIn('cn:1->com', state['activities'])

    def test_identity_is_independent_of_regional_ids_and_names(self):
        a = dict(activityId=1, startTimeGMT='2000-01-09 01:00:00', duration=3600, distance=20000)
        b = dict(a, activityId=999, activityName='different')
        self.assertTrue(s.activity_match(a, b))
        self.assertFalse(s.activity_match(a, dict(b, startTimeGMT='2000-01-09 02:00:00')))

    def test_zip_export_never_extracts_paths(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z:
            z.writestr('../../a.fit', b'activity')
        self.assertEqual(s.original_file(data.getvalue()), ('.fit', b'activity'))

    def test_cross_region_ids_removed_but_targets_preserved(self):
        w = dict(workoutId=9, ownerId=10, workoutName='Intervals', sportType={'sportTypeId': 2},
                 workoutSegments=[{'segmentId': 5, 'workoutSteps': [
                     {'stepId': 8, 'targetValueOne': 200, 'endConditionValue': 300}]}])
        p = s.payload(w)
        self.assertNotIn('workoutId', p)
        self.assertNotIn('stepId', p['workoutSegments'][0]['workoutSteps'][0])
        self.assertEqual(p['workoutSegments'][0]['workoutSteps'][0]['targetValueOne'], 200)
        self.assertEqual(s.fingerprint(w), s.fingerprint(dict(w, workoutId=100)))

    def test_bidirectional_import_is_not_echoed_back(self):
        a = dict(activityId=1, startTimeGMT='2000-01-09 01:00:00', duration=3600, distance=20000)
        class API:
            def __init__(self, activities):
                self.activities, self.uploads = activities, 0
            def get_activities_by_date(self, start, end):
                return list(self.activities)
            def download_activity(self, *args, **kw):
                return b'12345678.FITdata'
            def upload_activity(self, path):
                self.uploads += 1
                return {'detailedImportResult': {'successes': [{'internalId': 42}]}}
        cn, com = API([a]), API([])
        with tempfile.TemporaryDirectory(dir=s.ROOT) as tmp, patch.object(s, 'PRIVATE', Path(tmp)), patch.object(s, 'STATE', Path(tmp)/'state.json'):
            state = {'activities': {}, 'workouts': {}}
            s.sync_activities({'cn': cn, 'com': com}, state, '2000-01-01', '2000-01-09', True)
            self.assertEqual((cn.uploads, com.uploads), (0, 1))
            s.sync_activities({'cn': cn, 'com': com}, state, '2000-01-01', '2000-01-09', True)
            self.assertEqual(com.uploads, 1)


if __name__ == '__main__':
    unittest.main()
