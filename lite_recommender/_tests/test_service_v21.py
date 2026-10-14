import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lite_recommender.service import Service, week_key
from lite_recommender.access import import_pdf, confirm_abstract

class ServiceV21Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='lite-v21-')
        self.s=Service(self.tmp.name)
        self.s.save_topic({'topic_id':'topic','title':'Topic','group':'Test','active':True,'stage':'focus','questions':['Robot movement?'],'keywords':['robot','movement'],'core_concepts':[['robot'],['movement']],'method_terms':['simulation']})
    def tearDown(self):self.tmp.cleanup()
    def paper(self,i,abstract='A robot movement study.',title='Robot movement'):
        return {'doi':f'10.1234/v21-{i}','title':title,'abstract':abstract,'published':'2025-01-01','source':'fixture'}
    def test_independent_capture_budget_and_partitioned_diagnostics(self):
        fixture=[self.paper(1),self.paper(2,abstract=''),self.paper(3,title='Unrelated crystal',abstract='Corrosion results'),self.paper(4)]
        self.s.save_settings({'budget':1})
        with patch('lite_recommender.sources.search',return_value=(fixture,[])) as fetch:
            result=self.s.run(run_id='one')
        self.assertEqual(fetch.call_args.kwargs['limit'],200)
        self.assertEqual(result['count'],1)
        diag=result['diagnostics']['topic']
        self.assertEqual((diag['core_count'],diag['pending_count'],diag['rejected_count']),(2,1,1))
        self.assertEqual(sum(diag[k+'_count'] for k in ['core','exploration','pending','rejected','seen']),diag['deduped_count'])
        self.assertEqual(len(self.s.candidate_detail('one')['candidates']),4)
    def test_seen_candidates_remain_inspectable_without_consuming_again(self):
        fixture=[self.paper(1),self.paper(2)]
        self.s.save_settings({'budget':3})
        with patch('lite_recommender.sources.search',return_value=(fixture,[])):
            self.assertEqual(self.s.run(run_id='a')['count'],2)
            again=self.s.run(run_id='b')
        self.assertEqual(again['count'],0)
        self.assertEqual(again['diagnostics']['topic']['seen_count'],2)
        self.assertTrue(all(p['seen'] for p in self.s.candidate_detail('b')['candidates']))
        self.assertEqual(self.s.store.used_budget(week_key(self.s.now()),'topic'),2)
    def test_continuation_freezes_profile_and_request_budget(self):
        captured=[]
        def search(p,limit,sources):captured.append((p,limit,sources));return [self.paper(len(captured))],[]
        with patch('lite_recommender.sources.search',side_effect=search):
            first=self.s.run(preview=True,run_id='parent')
            self.s.save_settings({'candidate_limit':30,'sources':['crossref']})
            self.s.save_topic({'topic_id':'topic','keywords':['changed','terms'],'questions':['changed'],'core_concepts':[['changed'],['terms']]})
            following=self.s.run(preview=True,search_page=1,parent_run_id='parent',run_id='next')
        self.assertEqual(captured[1][1],200)
        self.assertEqual(captured[1][0]['keywords'],captured[0][0]['keywords'])
        self.assertEqual(captured[1][0]['_search_page'],1)
        self.assertEqual(captured[1][2],captured[0][2])
        self.assertEqual(following['profiles'],first['profiles'])
        self.assertEqual(self.s.store.used_budget(week_key(self.s.now())),0)
        with self.assertRaises(ValueError):self.s.run(preview=False,search_page=2,parent_run_id='next',run_id='invalid')
    def test_model_path_and_capture_limit_validation(self):
        for payload in [{'candidate_limit':True},{'candidate_limit':29},{'candidate_limit':1001},{'semantic':{'model_dir':'../../outside'}},{'semantic':{'weight':float('nan')}},{'semantic':{'enabled':'yes'}}]:
            with self.assertRaises(ValueError):self.s.save_settings(payload)
    def test_pending_pdf_can_be_imported_confirmed_and_ranked_later(self):
        p=self.paper(1,abstract='')
        with patch('lite_recommender.sources.search',return_value=([p],[])):
            first=self.s.run(preview=True,run_id='missing')
        self.assertEqual(first['count'],0)
        pending=self.s.candidate_detail('missing')['candidates'][0]
        self.assertEqual(pending['classification'],'pending')
        with patch('lite_recommender.access._extract_abstract',return_value=('A'*100,'candidate')):
            imported=import_pdf(self.s,{'run_id':'missing','paper_id':pending['paper_id'],'data':base64.b64encode(b'%PDF-1.4\n%%EOF').decode()})
        confirm_abstract(self.s,{'paper_id':imported['paper_id'],'abstract':'A robot movement study. '*5})
        with patch('lite_recommender.sources.search',return_value=([p],[])):
            next_run=self.s.run(preview=True,run_id='confirmed')
        self.assertEqual(next_run['count'],1)
        self.assertEqual(self.s.candidate_detail('missing')['candidates'][0]['classification'],'pending')
    def test_profile_synonyms_accept_feedback_tags(self):
        self.s.save_topic({'topic_id':'topic','core_concepts':[['robot','robotics'],['movement']]})
        paper=self.s.store.upsert_paper(self.paper(1,title='Robotics movement'))
        self.s.feedback({'topic_id':'topic','paper_id':paper['paper_id'],'rating':5,'reason':'relevant','tags':['robotics']})
        self.assertEqual(self.s.store.latest_feedback('topic',self.s._topic('topic')['profile_version'])[paper['paper_id']]['tags'],['robotics'])

    def test_inferred_concepts_do_not_invalidate_unchanged_legacy_feedback(self):
        p=self.s.save_topic({'topic_id':'legacy','title':'Legacy','group':'Test','questions':['A question'],'keywords':['robot','movement'],'method_terms':[],'stage':'focus','active':False})
        viewed=next(t for t in self.s.overview()['topics'] if t['topic_id']=='legacy')
        updated=self.s.save_topic({**viewed,'active':True})
        self.assertEqual(updated['profile_version'],p['profile_version'])
        self.assertNotIn('core_concepts',updated)
