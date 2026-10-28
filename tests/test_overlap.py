"""Synthetic simultaneous recorder and virtual ride fixtures."""
import contextlib
import io
import unittest
from scripts import activity_overlap as o
from scripts import sync_garmin as s


def ride(wid, maker, start='2000-01-09 06:00:00', duration=3600, distance=18000):
    return dict(activityId=wid, manufacturer=maker, startTimeGMT=start,
                duration=duration, elapsedDuration=duration, distance=distance,
                activityName='Recorder' if maker=='GARMIN' else 'Zwift ride',
                activityType={'typeKey': 'indoor_cycling' if maker=='GARMIN' else 'virtual_ride'})


class OverlapTests(unittest.TestCase):
    def setUp(self):
        self.cn = ride(1, 'GARMIN')
        self.com = ride(42, 'ZWIFT', '2000-01-09 06:02:00', 3500, 7000)

    def test_different_distances_can_represent_one_session(self):
        score = o.overlap(self.cn,self.com)
        self.assertIsNotNone(score)
        self.assertEqual(o.excluded_activities({'cn':[self.cn],'com':[self.com]},False),
                         {'cn': {'1'}, 'com': {'42'}})

    def test_reject_partial_sessions_other_days_and_invalid_times(self):
        for changes in ({'duration':1800,'elapsedDuration':1800},
                        {'startTimeGMT':'2000-01-10 06:00:00'},
                        {'startTimeGMT':'invalid'}, {'duration':float('nan')},
                        {'startTimeGMT':'2000-01-09 06:11:00'}):
            self.assertIsNone(o.overlap(self.cn,dict(self.com,**changes)))

    def test_source_detection_is_conservative(self):
        for activity in (dict(self.cn,activityType={'typeKey':'road_biking'}),
                         dict(self.cn,manufacturer='UNKNOWN'),
                         dict(self.com,activityType={'typeKey':'running'}),
                         dict(self.com,manufacturer='OTHER')):
            self.assertIsNone(o.source(activity))
        self.assertEqual(o.source(dict(self.com,manufacturer=None)), 'zwift')

    def test_ambiguous_pairs_are_held_and_reported(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            blocked=o.excluded_activities({'cn':[self.cn],
                                          'com':[self.com,dict(self.com,activityId=43)]})
        self.assertEqual(blocked,{'cn':{'1'},'com':{'42','43'}})
        self.assertIn('REVIEW ambiguous',output.getvalue())

    def test_apply_blocks_both_uploads_and_names_without_sync_state(self):
        class API:
            def __init__(self,activity): self.activity=activity
            def get_activities_by_date(self,*args): return [self.activity]
            def upload_activity(self,*args): raise AssertionError('Must not upload')
            def set_activity_name(self,*args): raise AssertionError('Must not rename')
        apis={'cn':API(self.cn),'com':API(self.com)}
        state={'activities':{},'workouts':{}}
        s.sync_activities(apis,state,'2000-01-09','2000-01-09',True,True)
        s.sync_activity_names(apis,state,'2000-01-09','2000-01-09',True,True)
        self.assertEqual(state['activities'],{})
        with contextlib.redirect_stdout(io.StringIO()) as output:
            s.sync_activities(apis,state,'2000-01-09','2000-01-09',False)
        self.assertIn('WOULD COPY cn:1->com',output.getvalue())
        self.assertIn('WOULD COPY com:42->cn',output.getvalue())

    def test_elapsed_span_includes_pauses_and_timezone_is_normalized(self):
        cn=dict(self.cn,startTimeGMT='2000-01-09T06:00:00Z',elapsedDuration=4000)
        com=dict(self.com,startTimeGMT='2000-01-09T14:02:00+08:00',elapsedDuration=3900)
        self.assertIsNotNone(o.overlap(cn,com))

    def test_name_sync_does_not_overwrite_even_when_exact_match_also_passes(self):
        com=dict(self.com,startTimeGMT=self.cn['startTimeGMT'],
                 duration=self.cn['duration'],elapsedDuration=self.cn['elapsedDuration'],
                 distance=self.cn['distance'])
        self.assertTrue(s.activity_match(self.cn,com))
        class API:
            def __init__(self,activity): self.activity=activity
            def get_activities_by_date(self,*args): return [self.activity]
            def set_activity_name(self,*args): raise AssertionError('Must not rename')
        s.sync_activity_names({'cn':API(self.cn),'com':API(com)}, {'activities':{}},
                              '2000-01-09','2000-01-09',True,True)


if __name__=='__main__':
    unittest.main()
