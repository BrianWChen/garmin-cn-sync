"""Synthetic regression tests for identity, state and payload safety."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import activity_overlap as overlap
from scripts import sync_garmin as sync


class SafetyTests(unittest.TestCase):
    def test_identity_compares_instants_across_offsets_and_dates(self):
        base = dict(startTimeGMT='2000-01-01T00:00:00Z', duration=3600, distance=20000)
        for stamp in ('2000-01-01 00:00:00', '2000-01-01T08:00:00+08:00',
                      '1999-12-31T19:00:00-05:00'):
            with self.subTest(stamp=stamp):
                other = dict(base, startTimeGMT=stamp)
                self.assertTrue(sync.activity_match(base, other))
                self.assertEqual(overlap.interval(base)[0], overlap.interval(other)[0])
        self.assertFalse(sync.activity_match(base, dict(base, startTimeGMT='2000-01-01T00:00:00+08:00')))

    def test_bad_timestamp_stops_identity_and_skips_overlap(self):
        base = dict(startTimeGMT='2000-01-01T00:00:00Z', duration=3600, distance=20000)
        for stamp in (None, 123, 'invalid', ''):
            with self.subTest(stamp=stamp):
                other = dict(base, startTimeGMT=stamp)
                with self.assertRaisesRegex(RuntimeError, 'missing identity'):
                    sync.activity_match(base, other)
                self.assertIsNone(overlap.interval(other))

    def test_atomic_state_failures_preserve_previous_state(self):
        for failing_method in ('write_text', 'replace'):
            with self.subTest(method=failing_method), tempfile.TemporaryDirectory() as tmp:
                private = Path(tmp)
                state = private / 'state.json'
                state.write_text(json.dumps({'previous': True}), encoding='utf-8')
                before = state.read_bytes()
                target = Path if failing_method == 'write_text' else sync.os
                with patch.object(sync, 'PRIVATE', private), patch.object(sync, 'STATE', state), patch.object(target, failing_method, side_effect=OSError('synthetic disk failure')):
                    with self.assertRaises(OSError):
                        sync.save({'replacement': True})
                self.assertEqual(state.read_bytes(), before)
                self.assertEqual(json.loads(state.read_text()), {'previous': True})

    def test_payload_whitelist_and_independent_nested_objects(self):
        workout = {'workoutName': 'Synthetic', 'unexpected': 'omit', 'ownerId': 9,
                   'workoutSegments': [{'segmentId': 5, 'workoutSteps': [
                       {'stepId': 8, 'targetValueOne': 200, 'endConditionValue': 300}]}]}
        before = copy.deepcopy(workout)
        result = sync.payload(workout)
        self.assertNotIn('unexpected', result)
        self.assertNotIn('ownerId', result)
        self.assertNotIn('segmentId', result['workoutSegments'][0])
        result['workoutSegments'][0]['workoutSteps'][0]['targetValueOne'] = 999
        self.assertEqual(workout, before)
