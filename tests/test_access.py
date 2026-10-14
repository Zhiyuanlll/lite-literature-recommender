import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from lite_recommender import access
from lite_recommender.service import Service, week_key
from lite_recommender.download import download_run, pdf_archive

PDF = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n'
PAPER = {"title": "Robot movement model", "doi": "10.1016/test.2025.001", "abstract": "A robot movement model simulation.", "published": "2025-01-01", "source": "fixture"}

class AccessTests(unittest.TestCase):
    def test_library_url_and_secret_status(self):
        for url in ['http://school.edu', 'https://user:pass@school.edu', 'javascript:alert(1)']:
            with self.assertRaises(ValueError): access.library_url(url)
        self.assertEqual(access.library_url('https://library.example.edu'), 'https://library.example.edu')
        with patch.dict(os.environ, {'ELSEVIER_API_KEY': 'secret-value'}):
            self.assertTrue(access.configuration()['elsevier_key_configured'])
            self.assertNotIn('secret-value', json.dumps(access.configuration()))

    def test_metadata_doi_check_and_missing_key_fallback(self):
        p = {**PAPER, 'abstract': ''}
        with patch.dict(os.environ, {}, clear=True):
            papers, warnings = access.enrich([p], True)
            self.assertEqual(papers, [p]); self.assertTrue(warnings)
        with patch.dict(os.environ, {'ELSEVIER_API_KEY': 'fixture-key'}), patch.object(access, '_request', return_value=json.dumps({'full-text-retrieval-response': {'coredata': {'prism:doi': p['doi'], 'dc:description': '<p>Verified abstract</p>'}}}).encode()):
            papers, warnings = access.enrich([p], True)
            self.assertEqual(papers[0]['abstract'], 'Verified abstract'); self.assertFalse(warnings)
        with patch.dict(os.environ, {'ELSEVIER_API_KEY': 'fixture-key'}), patch.object(access, '_request', return_value=json.dumps({'full-text-retrieval-response': {'coredata': {'prism:doi': '10.1016/wrong', 'dc:description': 'Wrong abstract'}}}).encode()):
            papers, warnings = access.enrich([p], True)
            self.assertFalse(papers[0]['abstract']); self.assertTrue(warnings)

    def test_official_request_never_redirects_credentials(self):
        conn = Mock(); conn.getresponse.return_value.status = 302
        with patch.dict(os.environ, {'ELSEVIER_API_KEY': 'fixture-secret'}), patch('lite_recommender.download._public_addresses', return_value=[]), patch('lite_recommender.download._PinnedHTTPSConnection', return_value=conn):
            with self.assertRaises(ValueError) as error: access._request(PAPER['doi'], 'application/json', 'META_ABS')
            self.assertNotIn('fixture-secret', str(error.exception))
            self.assertEqual(conn.request.call_count, 1)
            self.assertNotIn('fixture-secret', conn.request.call_args.args[1])
            self.assertTrue(conn.close.called)

    def test_import_and_confirm_do_not_change_history_or_quota(self):
        with tempfile.TemporaryDirectory() as temp:
            s = Service(temp)
            s.save_topic({'topic_id':'topic', 'title':'Topic', 'group':'Test','questions':['Test question'], 'keywords':['robot','movement'], 'method_terms':['model','simulation'], 'stage':'focus','active':True})
            with patch('lite_recommender.sources.search', return_value=([PAPER], [])):
                run = s.run(run_id='test')
            paper = s.run_detail('test')['papers'][0]
            payload = {'run_id':'test','paper_id':paper['paper_id'],'data':base64.b64encode(PDF).decode()}
            with patch.object(access, '_extract_abstract', return_value=('A'*100, 'candidate')):
                imported = access.import_pdf(s, payload)
            self.assertEqual(s.store.used_budget(week_key(s.now()), 'topic'), 1)
            self.assertEqual(s.store.paper(paper['paper_id'])['abstract'], PAPER['abstract'])
            access.confirm_abstract(s, {'paper_id':imported['paper_id'],'abstract':'B'*100})
            self.assertEqual(s.store.paper(paper['paper_id'])['abstract'], 'B'*100)
            self.assertEqual(s.run_detail('test')['papers'][0]['abstract'], PAPER['abstract'])
            with self.assertRaises(ValueError):access.import_pdf(s, payload)
            with self.assertRaises(ValueError):access.import_pdf(s, {**payload, 'paper_id':'unrelated'})
            report = download_run(s, 'test', select_all=True)
            self.assertEqual(report['already_exists'], 1)
            self.assertTrue(pdf_archive(s, 'test').exists())

    def test_official_pdf_rejects_html_and_falls_back_to_public_link(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'paper.pdf'
            with patch.object(access, '_request', return_value=b'<html>login</html>'):
                with self.assertRaises(ValueError):access.official_pdf(PAPER, path)
            self.assertFalse(path.exists())
            with patch.object(access, '_request', return_value=PDF):
                access.official_pdf(PAPER, path)
            self.assertEqual(path.read_bytes(), PDF)

    def test_external_entry_is_optional_and_validated(self):
        self.assertEqual(access.external_base(''), '')
        self.assertEqual(access.external_base('https://example.org/'), 'https://example.org')
        for url in ['https://example.org/path', 'https://example.org/?password=test', 'http://example.org']:
            with self.assertRaises(ValueError):access.external_base(url)

    def test_real_poppler_extracts_abstract_candidate(self):
        if not access.configuration()['pdf_text_available']:
            self.skipTest('optional pdftotext unavailable')
        lines = ['Test paper title', 'Abstract', 'This is a test abstract describing a robot movement model.', 'It is a fixture for extraction and contains no scientific claims.', 'Keywords: robot', '1 Introduction', 'The body should not be included.']
        stream = ('BT /F1 12 Tf 50 750 Td ' + ' '.join(('0 -20 Td ' if i else '') + '(' + line + ') Tj' for i,line in enumerate(lines)) + ' ET').encode()
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>', b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
        data=b'%PDF-1.4\n';offsets=[0]
        for i,obj in enumerate(objects,1):
            offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
        xref=len(data);data+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fixture.pdf';path.write_bytes(data)
            abstract, warning = access._extract_abstract(path)
            self.assertIn('test abstract', abstract)
            self.assertNotIn('body should', abstract)

    def test_pdf_text_absent_is_optional(self):
        with patch('lite_recommender.access.shutil.which', return_value=None):
            abstract, warning = access._extract_abstract(Path('fixture.pdf'))
            self.assertFalse(abstract); self.assertIn('pdftotext', warning)

    def test_flattened_keyword_heading_ends_abstract_without_body(self):
        paragraph = ('This test abstract describes a robot movement model. '
                     'Its keywords are ordinary words within this sentence. '
                     'The fixture contains no scientific claims.')
        for heading in ('Keywords:', 'Key words:', 'Index terms:', '关键词：'):
            with self.subTest(heading=heading):
                text = ('Test paper title Abstract ' + paragraph + ' ' + heading
                        + ' robot 1 Introduction The body should not be included.')
                def write_flattened_text(args, **kwargs):
                    Path(args[-1]).write_text(text, encoding='utf-8')
                with patch.object(access.shutil, 'which', return_value='pdftotext'), \
                     patch.object(access.subprocess, 'run', side_effect=write_flattened_text):
                    abstract, warning = access._extract_abstract(Path('fixture.pdf'))
                self.assertEqual(abstract, paragraph)
                self.assertNotIn('body should', abstract)
                self.assertIn('keywords are ordinary words', abstract)
                self.assertIn('人工核对', warning)

if __name__ == '__main__':unittest.main()
