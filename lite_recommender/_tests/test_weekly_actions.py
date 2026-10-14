import tempfile
import unittest
from unittest.mock import patch
from lite_recommender.service import Service,week_key
from lite_recommender.discovery import search

class WeeklyActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(self.tmp.name)
        self.profile=self.s.save_topic({'topic_id':'topic','title':'Robot','group':'Test','active':True,'stage':'focus','questions':['Robot movement?'],'keywords':['robot','movement'],'core_concepts':[['robot'],['movement']],'method_terms':[]})
        self.s.save_settings({'recommendation_counts':{'core':1,'related':1}})
        self.pool=[{'doi':f'10.1234/weekly-{i}','title':f'Robot movement {i}','abstract':'Robot movement experiments and analysis.','published':'2025-01-01','source':'fixture'} for i in range(12)]
    def tearDown(self):self.tmp.cleanup()
    def run_action(self,action,rid,**kw):
        with patch('lite_recommender.sources.search',return_value=(self.pool,[])):
            return self.s.run(action=action,run_id=rid,**kw)
    def rate(self,rid,paper):
        return self.s.feedback({'run_id':rid,'topic_id':'topic','paper_id':paper['paper_id'],'profile_version':paper['profile_version'],'rating':5,'reason':'relevant','tags':['robot']})
    def test_new_articles_preview_readonly_complete_ratings_and_reselect(self):
        first=self.run_action('reselect','first');one=self.s.store.run_papers('first');self.assertEqual(first['count'],2)
        preview=self.run_action('preview_next','preview');two=self.s.store.run_papers('preview')
        self.assertFalse({p['paper_id'] for p in one}&{p['paper_id'] for p in two})
        self.assertEqual(preview['mode'],'preview');self.assertNotEqual(first['target_week'],preview['target_week'])
        with self.assertRaisesRegex(ValueError,'不可评分'):self.rate('preview',two[0])
        with self.assertRaisesRegex(ValueError,'2篇未评分'):self.run_action('accelerate','early',base_run_id='first')
        self.rate('first',one[0])
        with self.assertRaisesRegex(ValueError,'1篇未评分'):self.run_action('accelerate','half',base_run_id='first')
        self.rate('first',one[1])
        accelerated=self.run_action('accelerate','accelerated',base_run_id='first');three=self.s.store.run_papers('accelerated')
        self.assertEqual(accelerated['count'],2);self.assertEqual(accelerated['mode'],'iteration')
        self.assertFalse({p['paper_id'] for p in one+two}&{p['paper_id'] for p in three})
        with self.assertRaisesRegex(ValueError,'已完成加速迭代'):self.run_action('accelerate','duplicate',base_run_id='first')
        self.rate('accelerated',three[0]);self.rate('accelerated',three[1])
        policy=self.s.store.get_policy('topic','1')
        replacement=self.run_action('reselect','replacement');four=self.s.store.run_papers('replacement')
        self.assertFalse({p['paper_id'] for p in one+two+three}&{p['paper_id'] for p in four})
        self.assertEqual(policy,self.s.store.get_policy('topic','1'))
        displayed=self.s.overview()['runs']
        self.assertEqual({r['run_id'] for r in displayed},{'replacement','preview'})
        self.assertEqual(self.s.overview()['current_feedback_run_id'],'replacement')
        self.assertEqual(self.s.overview()['stats']['used_this_week'],2)
        self.assertTrue(self.s.run(action='reselect',run_id='replacement')['replayed'])
    def test_invalid_base_and_profile_change(self):
        r=self.run_action('reselect','first');papers=self.s.store.run_papers('first')
        for p in papers:self.rate('first',p)
        self.s.save_topic({'topic_id':'topic','questions':['A different movement question?']})
        with self.assertRaisesRegex(ValueError,'配置已变更'):self.run_action('accelerate','changed',base_run_id='first')
        with self.assertRaisesRegex(ValueError,'操作无效'):self.s.run(action='unknown')
    def test_weekly_run_is_idempotent(self):
        rid=week_key(self.s.now())+'-real'
        first=self.run_action('weekly',rid)
        again=self.run_action('weekly',rid)
        self.assertTrue(again['replayed']);self.assertEqual(first['count'],again['count'])
    def test_search_continues_when_first_wave_is_already_shown(self):
        profile={**self.profile,'_search_diagnostics':{},'_exclude_papers':[self.pool[0],self.pool[1]]}
        def fetch(p,limit,sources):
            old=p.get('_search_variant',0)==0
            found=self.pool[:2] if old else self.pool[2:4]
            p['_search_diagnostics'].update(raw_count=2)
            return found,[]
        with patch('lite_recommender.sources.search',side_effect=fetch) as fetcher:
            found,errors=search(profile,{'core':1,'related':1},{},self.s.now().date(),['crossref'],self.s.workspace/'cache')
        self.assertEqual(fetcher.call_count,2);self.assertEqual(profile['_search_diagnostics']['stop_reason'],'groups_filled')
