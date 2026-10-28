"""Activity safety checks and reconciliation using synthetic responses."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile
from scripts import sync_garmin as s


def activity(aid=1, **changes):
    return dict(dict(activityId=aid, startTimeGMT='2000-01-09 01:00:00', duration=3600, distance=20000, activityName='Original'), **changes)


class ActivityErrorTests(unittest.TestCase):
    def test_malformed_identity_export_and_import_responses(self):
        for changes in ({'duration': None}, {'startTimeGMT': 'invalid'}):
            with self.assertRaisesRegex(RuntimeError, 'missing identity'):
                s.activity_match(activity(), activity(**changes))
        for names in ([], ['a.fit', 'b.tcx']):
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as z:
                for name in names:
                    z.writestr(name, b'activity')
            with self.assertRaisesRegex(RuntimeError, 'exactly one'):
                s.original_file(data.getvalue())
        with self.assertRaisesRegex(RuntimeError, 'Unrecognized'):
            s.original_file(b'not an export')
        response = MagicMock()
        response.json.side_effect = ValueError('invalid json')
        self.assertEqual(s.import_result(response), {})
        self.assertEqual(s.import_result({'detailedImportResult': []}), {})

    def test_pending_preview_and_confirmed_recovery_do_not_upload(self):
        cn, com = MagicMock(), MagicMock()
        cn.get_activities_by_date.return_value = [activity()]
        com.get_activities_by_date.return_value = []
        state = {'activities': {'cn:1->com': {'status': 'pending'}}}
        with patch.object(s, 'confirm_activity', return_value=(activity(42), [activity(42)])) as confirm, patch.object(s, 'save'):
            s.sync_activities({'cn': cn, 'com': com}, state, 'start', 'end', False)
            confirm.assert_not_called()
            s.sync_activities({'cn': cn, 'com': com}, state, 'start', 'end', True)
            self.assertEqual(state['activities']['cn:1->com']['targetActivityId'], 42)
            cn.upload_activity.assert_not_called()
            com.upload_activity.assert_not_called()

    def test_existing_activity_preview_does_not_write_state(self):
        cn, com = MagicMock(), MagicMock()
        cn.get_activities_by_date.return_value = [activity()]
        com.get_activities_by_date.return_value = [activity(42)]
        state = {'activities': {}}
        with patch.object(s, 'save') as save:
            s.sync_activities({'cn': cn, 'com': com}, state, 'start', 'end', False)
            save.assert_not_called()
        self.assertEqual(state, {'activities': {}})
        com.upload_activity.assert_not_called()

    def test_unconfirmed_new_upload_preserves_pending_state(self):
        cn, com = MagicMock(), MagicMock()
        cn.get_activities_by_date.return_value = [activity()]
        cn.download_activity.return_value = b'12345678.FITdata'
        com.get_activities_by_date.return_value = []
        com.upload_activity.return_value = {}
        with tempfile.TemporaryDirectory() as tmp, patch.object(s, 'PRIVATE', Path(tmp)), patch.object(s, 'STATE', Path(tmp)/'state.json'), patch.object(s, 'confirm_activity', return_value=(None, [])):
            state = {'activities': {}}
            with self.assertRaisesRegex(RuntimeError, 'Upload not confirmed'):
                s.sync_activities({'cn': cn, 'com': com}, state, 'start', 'end', True)
            self.assertEqual(state['activities']['cn:1->com']['status'], 'pending')
            com.upload_activity.assert_called_once()
            self.assertTrue(s.STATE.exists())

    def test_name_authority_and_unmatched_unchanged_names(self):
        self.assertEqual(s.name_source({'activities': {'cn:1->com': {'nameSource': 'com'}}}, activity(), activity(42)), 'com')
        self.assertEqual(s.name_source({'activities': {'cn:1->com': {'sha256': 'hash'}}}, activity(), activity(42)), 'cn')
        for targets in ([], [activity(42), activity(43)], [activity(42)], [activity(42, activityName='')]):
            cn, com = MagicMock(), MagicMock()
            cn.get_activities_by_date.return_value = [activity(activityName='' if targets and targets[0]['activityName'] == '' else 'Original')]
            com.get_activities_by_date.return_value = targets
            s.sync_activity_names({'cn': cn, 'com': com}, {'activities': {}}, 'start', 'end', True)
            cn.set_activity_name.assert_not_called()
            com.set_activity_name.assert_not_called()
        cn, com = MagicMock(), MagicMock()
        cn.get_activities_by_date.return_value = [activity()]
        com.get_activities_by_date.return_value = [activity(42, activityName='Target')]
        with patch.object(s, 'save'):
            state = {'activities': {}}
            s.sync_activity_names({'cn': cn, 'com': com}, state, 'start', 'end', True)
            com.set_activity_name.assert_called_once_with('42', 'Original')
        with patch('scripts.activity_overlap.excluded_activities', return_value={'cn': set(), 'com': {'42'}}):
            com.set_activity_name.reset_mock()
            s.sync_activity_names({'cn': cn, 'com': com}, state, 'start', 'end', True, True)
            com.set_activity_name.assert_not_called()
