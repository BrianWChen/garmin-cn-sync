# All fixtures are synthetic and do not describe a real athlete.
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import sync_garmin
from scripts import wellness_sync as w


class WellnessTests(unittest.TestCase):
    def test_mapping_uses_overnight_hrv_and_sleep_spo2(self):
        day = '2000-01-09'
        stats = {'calendarDate': day, 'restingHeartRate': 64, 'totalSteps': 0, 'averageSpo2': 96}
        sleep = {'dailySleepDTO': {'calendarDate': day, 'sleepTimeSeconds': 27000,
                 'napTimeSeconds': 900, 'avgHeartRate': 62, 'averageSpO2Value': 97,
                 'sleepScores': {'overall': {'value': 88}}}}
        hrv = {'hrvSummary': {'calendarDate': day, 'lastNightAvg': 46, 'weeklyAvg': 42}}
        self.assertEqual(w.map_wellness(day, stats, sleep, hrv),
                         dict(restingHR=64, steps=0, sleepSecs=27000, avgSleepingHR=62,
                              spO2=97, sleepScore=88, hrv=46))

    def test_missing_invalid_and_wrong_date(self):
        self.assertEqual(w.map_wellness('2000-01-09', {'restingHeartRate': 0},
                         {'dailySleepDTO': {'averageSpO2Value': -1}}, {}), {})
        with self.assertRaisesRegex(RuntimeError, 'different date'):
            w.map_wellness('2000-01-09', {'calendarDate': '2000-01-08'}, {}, {})

    def test_partial_update_backup_and_idempotent_rerun(self):
        record = {'id': '2000-01-09', 'weight': 70, 'comments': 'manual note',
                  'sleepScore': 80, 'restingHR': 55}
        writes = []
        def request(session, method, path, **kwargs):
            if '/wellness/' not in path:
                return {'id': 'i1'}
            if method == 'PUT':
                writes.append(kwargs['json'])
                record.update(kwargs['json'])
            return dict(record)
        class Garmin:
            def get_body_composition(self, *args):
                return {}
            def get_training_readiness(self, day):
                return []
            def get_stats(self, day):
                return {'calendarDate': day, 'restingHeartRate': 64}
            def get_sleep_data(self, day):
                return {}
            def get_hrv_data(self, day):
                return {}
        with tempfile.TemporaryDirectory(dir=sync_garmin.ROOT) as tmp, patch.object(w, 'credentials', return_value=('test', '0')), patch.object(w, 'request_json', side_effect=request):
            state = {}
            for _ in range(2):
                w.sync_wellness(Garmin(), state, date(2000,1,9), date(2000,1,9),
                                True, Path(tmp), lambda state: None)
            self.assertEqual(writes, [{'restingHR': 64}])
            self.assertEqual(record['weight'], 70)
            self.assertEqual(record['comments'], 'manual note')
            self.assertEqual(record['sleepScore'], 80)
            self.assertEqual(len(list(Path(tmp).rglob('*.json'))), 1)

    def test_new_fields_use_sleep_respiration_and_round_floors(self):
        result = w.map_wellness('2000-01-09', {'floorsAscended': 4.6,
                              'avgWakingRespirationValue': 22},
                              {'dailySleepDTO': {'averageRespirationValue': 15}}, {},
                              weight=68.5, readiness=0)
        self.assertEqual(result, dict(respiration=15, floorsClimbed=5, weight=68.5, readiness=0))

    def test_weights_use_latest_measurement_per_day_without_forward_fill(self):
        values = w.daily_weights({'dateWeightList': [
            {'calendarDate': '2000-01-09', 'weight': 68000, 'timestampGMT': 1000},
            {'calendarDate': '2000-01-09', 'weight': 68500, 'timestampGMT': 2000},
            {'calendarDate': '2000-01-08', 'weight': 69000, 'timestampGMT': 500}],
            'totalAverage': {'weight': 68000}})
        self.assertEqual(values['2000-01-09'], 68.5)
        self.assertNotIn('2000-01-10', values)

    def test_readiness_never_falls_back_to_after_exercise(self):
        day = '2000-01-09'
        items = [{'calendarDate': day, 'inputContext': 'AFTER_ACTIVITY', 'score': 99},
                 {'calendarDate': day, 'inputContext': 'AFTER_WAKEUP_RESET', 'score': 60}]
        self.assertEqual(w.morning_readiness(day, items), 60)
        self.assertIsNone(w.morning_readiness(day, items[:1]))
        self.assertIsNone(w.morning_readiness('2000-01-10', items))
        self.assertIsNone(w.morning_readiness(day, {'calendarDate': day, 'score': 99}))


if __name__ == '__main__':
    unittest.main()
