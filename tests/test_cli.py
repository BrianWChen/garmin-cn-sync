"""CLI orchestration and authentication with synthetic accounts only."""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import sync_garmin as s
from scripts import wellness_sync as w


class CliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.private = Path(temp.name) / 'private'
        for name, value in [('PRIVATE', self.private), ('STATE', self.private / 'state.json')]:
            p = patch.object(s, name, value)
            p.start()
            self.addCleanup(p.stop)

    def invoke(self, *args):
        with patch.object(sys, 'argv', ['sync_garmin', *args]):
            s.main()

    def test_paths_keep_existing_data_and_packages_at_repository_root(self):
        self.assertEqual(s.ROOT, Path(__file__).resolve().parents[1])
        self.assertIn(str(s.ROOT / '.packages'), sys.path)

    def test_login_reuses_tokens_and_falls_back_after_expiry(self):
        self.private.mkdir()
        (self.private / 'tokens-cn').mkdir()
        with patch('garminconnect.Garmin') as factory:
            api = factory.return_value
            self.assertIs(s.login('cn'), api)
            api.login.assert_called_once_with(str(self.private / 'tokens-cn'))
            factory.assert_called_once_with(is_cn=True, retry_attempts=0)
        with patch('garminconnect.Garmin') as factory, patch('builtins.input', side_effect=[' cn@example.test ', '123456']), patch.object(s.getpass, 'getpass', return_value='synthetic'):
            old, fresh = MagicMock(), MagicMock()
            old.login.side_effect = RuntimeError('expired')
            factory.side_effect = [old, fresh]
            self.assertIs(s.login('cn'), fresh)
            self.assertEqual(factory.call_args.kwargs['prompt_mfa'](), '123456')
            fresh.login.assert_called_once_with()
            fresh.client.dump.assert_called_once_with(str(self.private / 'tokens-cn'))
        with patch('garminconnect.Garmin') as factory, patch('builtins.input', return_value='com@example.test'), patch.object(s.getpass, 'getpass', return_value='synthetic'):
            self.assertIs(s.login('com'), factory.return_value)
            self.assertFalse(factory.call_args.kwargs['is_cn'])

    def test_main_routes_modes_dates_and_cleans_lock(self):
        today = datetime.now(timezone(timedelta(hours=8))).date()
        for mode in ('activities', 'names', 'workouts', 'wellness', 'all'):
            for apply in (False, True):
                with self.subTest(mode=mode, apply=apply), patch.object(s, 'login') as login, patch.object(s, 'sync_activities') as activities, patch.object(s, 'sync_activity_names') as names, patch.object(s, 'sync_workouts') as workouts, patch.object(w, 'sync_wellness') as wellness, patch.object(w, 'configured', return_value=True):
                    args = ['--only', mode, '--keep-overlapping-originals']
                    if apply:
                        args += ['--apply', '--from', '2000-01-01', '--to', '2000-01-09']
                        self.private.mkdir(exist_ok=True)
                        s.STATE.write_text(json.dumps({'activities': {}, 'workouts': {}}))
                    self.invoke(*args)
                    self.assertEqual([x.args[0] for x in login.call_args_list], ['cn'] if mode == 'wellness' else ['cn', 'com'])
                    self.assertEqual(activities.called, mode in ('activities', 'all'))
                    self.assertEqual(names.called, mode in ('activities', 'names', 'all'))
                    self.assertEqual(workouts.called, mode in ('workouts', 'all'))
                    self.assertEqual(wellness.called, mode in ('wellness', 'all'))
                    if activities.called:
                        self.assertEqual(activities.call_args.args[2:6], ('2000-01-01', '2000-01-09', True, True) if apply else ((today-timedelta(days=30)).isoformat(), today.isoformat(), False, True))
                    if wellness.called:
                        self.assertEqual(wellness.call_args.args[2:5], (datetime(2000,1,1).date(), datetime(2000,1,9).date(), True) if apply else (today-timedelta(days=6), today, False))
                    self.assertFalse((self.private / 'sync.lock').exists())

    def test_unconfigured_wellness_skipped_and_failure_unlocks(self):
        with patch.object(s, 'login'), patch.object(s, 'sync_activities'), patch.object(s, 'sync_activity_names'), patch.object(s, 'sync_workouts'), patch.object(w, 'configured', return_value=False), patch.object(w, 'sync_wellness') as wellness:
            self.invoke()
            wellness.assert_not_called()
        with patch.object(s, 'login', side_effect=RuntimeError('login failed')):
            with self.assertRaisesRegex(RuntimeError, 'login failed'):
                self.invoke()
            self.assertFalse((self.private / 'sync.lock').exists())

    def test_validation_and_existing_lock(self):
        for args in (['--from', 'invalid'], ['--from', '2000-01-10', '--to', '2000-01-09'], ['--to', '2999-01-01'], ['--workout-days', '0'], ['--workout-days', '91'], ['--wellness-days', '0'], ['--wellness-days', '366']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                self.invoke(*args)
            self.assertEqual(error.exception.code, 2)
        self.private.mkdir()
        lock = self.private / 'sync.lock'
        lock.write_text('existing')
        with self.assertRaisesRegex(RuntimeError, 'Another run'):
            self.invoke()
        self.assertEqual(lock.read_text(), 'existing')

    def test_configure_does_not_login(self):
        with patch.object(w, 'credentials') as credentials, patch.object(s, 'login') as login:
            self.invoke('--configure-intervals')
            credentials.assert_called_once_with(self.private, configure=True)
            login.assert_not_called()

    def test_executable_errors_hide_transport_details(self):
        for error, visible in ((RuntimeError('actionable'), True), (ImportError('missing dependency'), True), (ValueError('sensitive transport body'), False)):
            output = io.StringIO()
            with patch.object(sys, 'argv', ['sync_garmin.py', '--configure-intervals']), patch.object(w, 'credentials', side_effect=error), contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as result:
                runpy.run_path(str(s.ROOT / 'scripts' / 'sync_garmin.py'), run_name='__main__')
            self.assertEqual(result.exception.code, 1)
            self.assertIn(type(error).__name__, output.getvalue())
            self.assertEqual(str(error) in output.getvalue(), visible)
        with patch.object(sys, 'argv', ['sync_garmin.py', '--configure-intervals']), patch.object(w, 'credentials') as credentials:
            runpy.run_path(str(s.ROOT / 'scripts' / 'sync_garmin.py'), run_name='__main__')
            credentials.assert_called_once()
