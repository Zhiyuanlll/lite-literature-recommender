import tempfile
import base64
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lite_recommender.browser_access import BrowserAccess, EXTERNAL_DOI_BASE
from lite_recommender.download import download_run
from lite_recommender.service import Service
from test_download import ServiceFixture, paper, PDF


class BrowserAccessTests(unittest.TestCase):
    def test_publisher_first_then_external_and_reuse(self):
        browser = BrowserAccess()
        browser.publisher, browser.external = object(), object()
        try:
            with patch.object(browser, '_start'), patch.object(browser, '_visit', side_effect=[None, {'source': 'external_doi_browser'}, {'source': 'institution_browser'}]) as visit:
                result = browser.download({'doi': '10.1016/example'}, Path('/tmp/unused.pdf'))
                self.assertEqual(result['source'], 'external_doi_browser')
                self.assertIs(visit.call_args_list[0].args[0], browser.publisher)
                self.assertIs(visit.call_args_list[1].args[0], browser.external)
                self.assertTrue(visit.call_args_list[1].args[1].startswith(EXTERNAL_DOI_BASE))
                browser.download({'doi': '10.1016/second'}, Path('/tmp/unused.pdf'))
                self.assertIs(visit.call_args_list[2].args[0], browser.publisher)
            # Mock placeholders are not actual browser contexts.
            browser.publisher = browser.external = None
        finally:
            browser.close()
        self.assertTrue(browser.stopping.is_set())
        self.assertFalse(browser.worker.is_alive())
        with self.assertRaises(ValueError):
            browser.download({'doi': '10.1016/example'}, Path('/tmp/unused.pdf'))

    def test_missing_doi_does_not_open_browser(self):
        browser = BrowserAccess()
        try:
            with self.assertRaises(ValueError):
                browser.download({'doi': ''}, Path('/tmp/unused.pdf'))
            self.assertIsNone(browser.worker)
            with self.assertRaises(ValueError): browser.signal('unexpected')
        finally:
            browser.close()

    def test_batch_falls_back_without_pdf_link_and_skips_existing(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ServiceFixture(temp, [paper(doi='10.1016/example', pdf_url='')])
            def save(paper, path, progress):
                path.write_bytes(PDF)
                return {'bytes': len(PDF), 'source': 'institution_browser'}
            browser = Mock(); browser.download.side_effect = save
            report = download_run(service, 'test-real', select_all=True, browser=browser)
            self.assertEqual(report['downloaded'], 1)
            self.assertEqual(report['items'][0]['source'], 'institution_browser')
            self.assertNotIn('message', report['items'][0])
            report = download_run(service, 'test-real', select_all=True, browser=browser)
            self.assertEqual(report['already_exists'], 1)
            browser.download.assert_called_once()

    def test_batch_preserves_per_paper_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ServiceFixture(temp, [paper(0, doi='10.1016/example', pdf_url=''), paper(1, doi='10.1016/second', pdf_url='')])
            browser = Mock(); browser.download.side_effect = ValueError('机构与外部入口均失败')
            report = download_run(service, 'test-real', select_all=True, browser=browser)
            self.assertEqual(report['failed'], 2)
            self.assertEqual(browser.download.call_count, 2)
            self.assertEqual(report['items'][0]['message'], '机构与外部入口均失败')

    def test_obsolete_settings_are_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            service = Service(temp)
            service.save_settings({'publisher_access': {'enabled': True, 'library_url': 'https://old.example.edu', 'external_doi_base': 'https://old.example.com'}})
            self.assertEqual(service.settings['publisher_access'], {'enabled': True})
            self.assertEqual(Service(temp).settings['publisher_access'], {'enabled': True})

    def test_pdf_stream_is_bounded_and_invalid_files_do_not_replace(self):
        browser = BrowserAccess()
        try:
            with tempfile.TemporaryDirectory() as temp:
                destination = Path(temp) / 'paper.pdf'
                session = Mock()
                session.send.return_value = {'data': base64.b64encode(PDF).decode(), 'base64Encoded': True, 'eof': True}
                self.assertEqual(browser._save_stream(session, 'fixture', destination, str(len(PDF))), len(PDF))
                self.assertEqual(destination.read_bytes(), PDF)
                self.assertLessEqual(session.send.call_args.args[1]['size'], 65536)
                session.send.return_value = {'data': base64.b64encode(b'X'*17).decode(), 'base64Encoded': True, 'eof': False}
                with patch('lite_recommender.browser_access.MAX_BYTES', 16), self.assertRaises(ValueError):
                    browser._save_stream(session, 'fixture', destination)
                self.assertEqual(destination.read_bytes(), PDF)
                self.assertEqual(list(Path(temp).glob('.browser-pdf-*')), [])
                session.send.return_value = {'data': '<html>login</html>', 'eof': True}
                with self.assertRaises(ValueError): browser._save_stream(session, 'fixture', destination)
                self.assertEqual(destination.read_bytes(), PDF)
        finally:
            browser.close()
