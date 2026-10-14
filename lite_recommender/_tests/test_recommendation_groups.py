import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lite_recommender.service import Service
from lite_recommender.ranking import score,select_targets
from lite_recommender.homepage import PageMetadata
from lite_recommender.discovery import search

PROFILE={'topic_id':'topic','title':'Robot movement','profile_version':'1','stage':'focus','core_concepts':[['robot'],['movement']], 'keywords':['robot','movement'],'method_terms':[]}
def paper(i,abstract='A robot movement study.'):
    return {'paper_id':str(i),'doi':f'10.1234/group-{i}','title':'Robot movement '+str(i),'abstract':abstract,'published':'2025-01-01','source':'fixture'}

class GroupTests(unittest.TestCase):
    def test_two_groups_include_title_only_and_more_than_one_exploration(self):
        values=[score(PROFILE,paper(i),{},dt.date(2026,10,14)) for i in range(9)]
        values += [score(PROFILE,paper(i,''),{},dt.date(2026,10,14)) for i in range(9,25)]
        chosen=select_targets(values,5,15)
        self.assertEqual(len(chosen),20)
        self.assertEqual(sum(p['recommendation_group']=='core' for p in chosen),5)
        self.assertEqual(sum(p.get('title_only',False) for p in chosen),11)
        bad=score(PROFILE,{**paper(30,''),'title':'Unrelated mining'}, {},dt.date(2026,10,14))
        self.assertNotIn('30',[p['paper_id'] for p in select_targets([bad],5,15)])
    def test_public_homepage_abstract_and_pdf_require_identity(self):
        html='<meta name="citation_doi" content="10.1234/group-1"><meta name="citation_pdf_url" content="/content/pdf/10.1234/group-1.pdf"><section id="Abs1"><h2>Abstract</h2><p>'+('Robot movement is investigated using simulation. '*6)+'</p></section>'
        parser=PageMetadata();parser.feed(html)
        meta=parser.extract(paper(1),'https://publisher.example/article/one')
        self.assertIn('Robot movement',meta['abstract'])
        self.assertNotIn('Abstract',meta['abstract'])
        self.assertTrue(meta['pdf_url'].endswith('.pdf'))
        self.assertEqual(parser.extract(paper(2),'https://publisher.example/article/one'),{})
        other=PageMetadata();other.feed(html.replace('group-1','group-12'))
        self.assertEqual(other.extract(paper(1),'https://publisher.example/article/one'),{})
    def test_adaptive_search_stops_at_group_targets_and_does_not_request_200(self):
        p={**PROFILE,'_search_diagnostics':{}}
        def fetch(profile,limit,sources):
            profile['_search_diagnostics'].update(raw_count=20,source_counts={'fixture':20})
            return [paper(i) for i in range(20)],[]
        with tempfile.TemporaryDirectory() as folder,patch('lite_recommender.sources.search',side_effect=fetch) as called:
            result,errors=search(p,{'core':5,'related':15},{},dt.date(2026,10,14),['crossref'],folder)
        self.assertEqual(len(result),20)
        self.assertEqual(called.call_count,1)
        self.assertNotEqual(called.call_args.kwargs['limit'],200)
        self.assertEqual(len(p['_search_diagnostics']['waves']),1)
    def test_service_uses_separate_group_counts_without_old_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Service(folder);s.save_topic({**PROFILE,'group':'Test','active':True,'questions':['Robot movement?']})
            s.save_settings({'budget':1,'recommendation_counts':{'core':5,'related':15}})
            def discover(profile,*args):
                profile['_search_diagnostics'].update(raw_count=25)
                return [paper(i) for i in range(9)]+[paper(i,'') for i in range(9,25)],[]
            with patch('lite_recommender.discovery.search',side_effect=discover):
                r=s.run(iteration=True,run_id='manual-groups')
            self.assertEqual(r['count'],20)
            self.assertEqual(r['budget'],20)
            self.assertEqual(s.store.used_budget('2026-W42'),0)
            self.assertEqual(len(s.run_detail(r['run_id'])['papers']),20)
            for payload in [{'core':True,'related':15},{'core':0,'related':0},{'core':-1,'related':15}]:
                with self.assertRaises(ValueError):s.save_settings({'recommendation_counts':payload})

    def test_bulk_pdf_prefers_existing_direct_link_then_homepage_endpoint(self):
        from lite_recommender.download import download_run
        with tempfile.TemporaryDirectory() as folder:
            s=Service(folder);s.save_topic({**PROFILE,'group':'Test','active':True,'questions':['Robot movement?']})
            s.save_settings({'recommendation_counts':{'core':1,'related':0}})
            p=s.store.upsert_paper({**paper(1),'pdf_url':'https://publisher.example/one.pdf'})
            r={'run_id':'pdf-order','status':'complete','mode':'iteration','created_at':s.now().isoformat()}
            s.store.save_run(r,'2026-W42',[{**p,'topic_id':'topic','profile_version':'1'}])
            def direct(url,destination):
                destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(b'%PDF-1.4\n%%EOF')
                return {'source_url':url}
            with patch('lite_recommender.download._download_url',side_effect=direct) as get,patch('lite_recommender.homepage.fetch') as home,patch('lite_recommender.access.official_pdf') as official:
                result=download_run(s,'pdf-order',select_all=True)
            self.assertEqual(result['downloaded'],1);home.assert_not_called();official.assert_not_called()
            self.assertEqual(get.call_args.args[0],'https://publisher.example/one.pdf')
            p2=s.store.upsert_paper(paper(2))
            s.store.save_run({**r,'run_id':'pdf-discovery'},'2026-W42',[{**p2,'topic_id':'topic','profile_version':'1'}])
            with patch('lite_recommender.homepage.fetch',return_value={'pdf_url':'https://publisher.example/two.pdf'}) as home,patch('lite_recommender.download._download_url',side_effect=direct) as get:
                result=download_run(s,'pdf-discovery',select_all=True)
            self.assertEqual(result['downloaded'],1);self.assertEqual(get.call_args.args[0],'https://publisher.example/two.pdf')
