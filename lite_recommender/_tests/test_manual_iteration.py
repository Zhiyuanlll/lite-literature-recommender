import tempfile
import unittest
from unittest.mock import patch
from lite_recommender.service import Service, week_key


class ManualIterationTests(unittest.TestCase):
    def test_iteration_reuses_papers_preserves_feedback_and_weekly_history(self):
        with tempfile.TemporaryDirectory() as folder:
            s = Service(folder)
            s.save_topic({'topic_id':'robot','title':'Robot','group':'Test','active':True,
                          'stage':'focus','questions':['Robot movement?'],
                          'keywords':['robot','movement'],'core_concepts':[['robot'],['movement']],
                          'method_terms':[]})
            papers = [{'doi':'10.1234/iteration','title':'Robot movement',
                       'abstract':'A robot movement study.', 'published':'2025-01-01','source':'fixture'}]
            s.save_settings({'budget':1})
            with patch('lite_recommender.sources.search',return_value=(papers,[])):
                weekly = s.run()
                first = s.run(iteration=True,run_id='manual-iteration-first')
                pid = s.run_detail(first['run_id'])['papers'][0]['paper_id']
                s.feedback({'topic_id':'robot','paper_id':pid,'rating':5,'reason':'relevant','tags':['robot']})
                second = s.run(iteration=True,run_id='manual-iteration-second')
                s.run(preview=True,run_id='manual-preview-last')
            self.assertEqual(first['count'],1)
            self.assertEqual(second['count'],1)
            self.assertEqual(s.store.used_budget(week_key(s.now()),'robot'),1)
            self.assertEqual(second['diagnostics']['robot']['seen_count'],0)
            self.assertEqual(s.run_detail(second['run_id'])['papers'][0]['feedback']['rating'],5)
            self.assertEqual(s.store.iteration_count(),2)
            shown = s.overview()['runs']
            self.assertEqual(len(shown),2)
            self.assertEqual(shown[0]['run_id'],'manual-preview-last')
            self.assertEqual(shown[1]['run_id'],weekly['run_id'])
            self.assertEqual(len(s.store.runs()),4)
            self.assertTrue(s.run(run_id=weekly['run_id'])['replayed'])

    def test_invalid_iteration_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Service(folder)
            for params in [{'iteration':'yes'},{'iteration':True,'preview':True},{'iteration':True,'demo':True}]:
                with self.assertRaises(ValueError):s.run(**params)
