"""Wellness configuration and transport failure regression tests."""
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from garminconnect import GarminConnectNotFoundError
from scripts import wellness_sync as w


class WellnessErrorTests(unittest.TestCase):
    def test_invalid_athlete_id_is_rejected_before_configuration_write(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            private = Path(tmp)
            path = private / 'intervals.json'
            for previous in (None, b'{"api_key":"synthetic","athlete_id":"i123"}'):
                if previous is not None:
                    path.write_bytes(previous)
                for athlete in ('1i23', 'ii123', 'i', '-1', '\uff11\uff12\uff13', 'i123/../../x'):
                    with self.subTest(athlete=athlete), patch.object(w.getpass, 'getpass', return_value='synthetic'), patch('builtins.input', return_value=athlete):
                        with self.assertRaisesRegex(RuntimeError, 'Invalid'):
                            w.credentials(private, configure=True)
                        self.assertEqual(path.read_bytes() if path.exists() else None, previous)
                with patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'synthetic', 'INTERVALS_ICU_ATHLETE_ID': '1i23'}):
                    with self.assertRaisesRegex(RuntimeError, 'Invalid'):
                        w.credentials(private)

    def test_backup_and_update_failures_never_confirm_wellness(self):
        for failure in ('backup', 'update'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp, patch.object(w, 'credentials', return_value=('synthetic', '0')), patch.object(w, 'request_json') as request:
                api = MagicMock()
                api.get_body_composition.return_value = {}
                api.get_training_readiness.return_value = []
                api.get_stats.return_value = {'totalSteps': 123}
                api.get_sleep_data.return_value = {}
                api.get_hrv_data.return_value = {}
                request.side_effect = [{'id': 'i1'}, {}, RuntimeError('synthetic update failure')]
                state, save = {}, MagicMock()
                if failure == 'backup':
                    with patch.object(Path, 'write_text', side_effect=OSError('synthetic backup failure')):
                        with self.assertRaises(OSError):
                            w.sync_wellness(api, state, date(2000,1,9), date(2000,1,9), True, Path(tmp), save)
                    self.assertEqual([call.args[1] for call in request.call_args_list], ['GET', 'GET'])
                else:
                    with self.assertRaisesRegex(RuntimeError, 'synthetic update failure'):
                        w.sync_wellness(api, state, date(2000,1,9), date(2000,1,9), True, Path(tmp), save)
                    self.assertEqual(len(list(Path(tmp).rglob('*.json'))), 1)
                self.assertEqual(state['wellness']['i1'], {})
                save.assert_not_called()

    def test_credentials_configuration_defaults_overrides_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            private = Path(tmp) / 'private'
            self.assertFalse(w.configured(private))
            with self.assertRaisesRegex(RuntimeError, 'Set INTERVALS'):
                w.credentials(private)
            with patch.object(w.getpass, 'getpass', return_value=' '), patch('builtins.input', return_value=''):
                with self.assertRaisesRegex(RuntimeError, 'empty'):
                    w.credentials(private, configure=True)
            with patch.object(w.getpass, 'getpass', return_value=' synthetic '), patch('builtins.input', return_value=''):
                self.assertEqual(w.credentials(private, configure=True), ('synthetic', '0'))
            self.assertTrue(w.configured(private))
            self.assertEqual(json.loads((private / 'intervals.json').read_text()), {'api_key': 'synthetic', 'athlete_id': '0'})
            with patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'override', 'INTERVALS_ICU_ATHLETE_ID': 'i123'}):
                self.assertEqual(w.credentials(private), ('override', 'i123'))
            with patch.dict(os.environ, {'INTERVALS_ICU_ATHLETE_ID': 'invalid'}):
                with self.assertRaisesRegex(RuntimeError, 'Invalid'):
                    w.credentials(private)
            with patch.object(w.getpass, 'getpass', return_value='synthetic'), patch('builtins.input', return_value='i456'):
                self.assertEqual(w.credentials(private, configure=True), ('synthetic', 'i456'))
            (private / 'intervals.json').unlink()
            with patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'synthetic'}):
                self.assertTrue(w.configured(private))

    def test_http_success_and_failure_without_body_disclosure(self):
        session = MagicMock()
        session.request.return_value.json.return_value = {'id': 'i1'}
        self.assertEqual(w.request_json(session, 'GET', '/athlete/0'), {'id': 'i1'})
        session.request.assert_called_once_with('GET', 'https://intervals.icu/api/v1/athlete/0', timeout=30)
        session.request.return_value.ok = False
        session.request.return_value.status_code = 403
        session.request.return_value.text = 'sensitive'
        with self.assertRaisesRegex(RuntimeError, r'HTTP 403\.') as error:
            w.request_json(session, 'PUT', '/athlete/i1/wellness/2000-01-09', json={'steps': 1})
        self.assertNotIn('sensitive', str(error.exception))

    def test_optional_endpoint_only_suppresses_not_found(self):
        api = MagicMock()
        api.read.side_effect = GarminConnectNotFoundError('not found')
        self.assertIsNone(w.optional_data(api, 'read', 'day'))
        api.read.side_effect = RuntimeError('unavailable')
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            w.optional_data(api, 'read', 'day')

    def test_invalid_weights_and_older_measurement_are_ignored(self):
        records = [{}, {'calendarDate': '2000-01-09', 'weight': 0},
                   {'calendarDate': '2000-01-09', 'weight': 68000, 'timestampGMT': None},
                   {'calendarDate': '2000-01-09', 'weight': 68000, 'timestampGMT': 2},
                   {'calendarDate': '2000-01-09', 'weight': 69000, 'timestampGMT': 1}]
        self.assertEqual(w.daily_weights({'dateWeightList': records}), {'2000-01-09': 68})
        for value in (None, True, float('nan'), float('inf'), -1):
            self.assertFalse(w.present_number(value, 0, 100))

    def test_no_data_preview_and_unconfirmed_write(self):
        day = date(2000,1,9)
        for stats, apply, fail in (({}, True, False), ({'totalSteps': 123}, False, False), ({'totalSteps': 123}, True, True)):
            with self.subTest(stats=stats, apply=apply), tempfile.TemporaryDirectory() as tmp, patch.object(w, 'credentials', return_value=('synthetic', '0')), patch.object(w, 'request_json') as request:
                api = MagicMock()
                api.get_body_composition.return_value = {}
                api.get_training_readiness.return_value = []
                api.get_stats.return_value = stats
                api.get_sleep_data.return_value = {}
                api.get_hrv_data.return_value = {}
                request.side_effect = [{'id': 'i1'}, {}, {}, {}]
                save = MagicMock()
                state = {}
                if fail:
                    with self.assertRaisesRegex(RuntimeError, 'read-back'):
                        w.sync_wellness(api, state, day, day, apply, Path(tmp), save)
                    self.assertEqual(request.call_args_list[2].args[1], 'PUT')
                    self.assertEqual(len(list(Path(tmp).rglob('*.json'))), 1)
                    self.assertEqual(state['wellness']['i1'], {})
                else:
                    w.sync_wellness(api, state, day, day, apply, Path(tmp), save)
                    self.assertFalse(any(c.args[1] == 'PUT' for c in request.call_args_list))
                    self.assertEqual(list(Path(tmp).rglob('*.json')), [])
                save.assert_not_called()
