"""Offline Chromium fixtures; no university or external website is contacted."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lite_recommender.browser_access import BrowserAccess, EXTERNAL_DOI_BASE
from test_download import PDF


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright not installed')
class ChromiumFixtureTests(unittest.TestCase):
    def test_login_handoff_reuse_pdf_embed_and_external_isolation(self):
        manager = BrowserAccess()
        observed = []
        fixture = {'logged_in': False, 'external': False}

        def start():
            if manager.browser:
                return
            from playwright.sync_api import sync_playwright
            bundled = Path(__file__).resolve().parents[1] / '.browser-binaries'
            if bundled.exists():
                os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(bundled))
            manager.playwright = sync_playwright().start()
            manager.browser = manager.playwright.chromium.launch(headless=True)
            manager.publisher = manager.browser.new_context(accept_downloads=True, service_workers='block')
            manager.external = manager.browser.new_context(accept_downloads=True, service_workers='block')
            # Fixture credentials are deliberately set for the external hostname in
            # publisher context; the external context must not transmit them.
            manager.publisher.add_cookies([{'name': 'fixture_session', 'value': 'test-only', 'url': EXTERNAL_DOI_BASE}])

            def publisher(route):
                if fixture['external']:
                    route.fulfill(status=503, body='Publisher unavailable')
                elif route.request.url.endswith('/fixture.pdf'):
                    route.fulfill(status=200, body=PDF, headers={'content-type': 'application/pdf', 'content-disposition': 'attachment; filename=fixture.pdf'})
                elif fixture['logged_in']:
                    route.fulfill(status=200, content_type='text/html', body='<iframe src="https://doi.org/fixture.pdf"></iframe>')
                elif '/institution' in route.request.url:
                    route.fulfill(status=200, content_type='text/html', body='Fixture institution login')
                else:
                    route.fulfill(status=200, content_type='text/html', body='<a href="https://doi.org/institution">Sign in via your institution</a>')

            def external(route):
                observed.append(route.request.headers.get('cookie', ''))
                if route.request.url.endswith('/fixture.pdf'):
                    route.fulfill(status=200, body=PDF, headers={'content-type': 'application/pdf', 'content-disposition': 'attachment; filename=fixture.pdf'})
                else:
                    route.fulfill(status=200, content_type='text/html', body='<iframe src="/fixture.pdf"></iframe>')
            manager.publisher.route('**/*', publisher)
            manager.external.route('**/*', external)

        waits = []
        def progress(**fields):
            if fields.get('waiting'):
                waits.append(fields.get('stage'))
                if fixture['external']:
                    manager.signal('skip')
                else:
                    fixture['logged_in'] = True
                    manager.signal('continue')

        try:
            with tempfile.TemporaryDirectory() as temp, patch.object(manager, '_start', side_effect=start), patch('lite_recommender.browser_access._public_addresses', return_value=[]):
                path = Path(temp) / 'first.pdf'
                result = manager.download({'doi': '10.1016/first'}, path, progress)
                self.assertEqual(result['source'], 'institution_browser')
                self.assertFalse(result['identity_verified'])
                self.assertEqual(path.read_bytes(), PDF)
                self.assertEqual(waits, ['institution_browser'])
                result = manager.download({'doi': '10.1016/second'}, Path(temp) / 'second.pdf', progress)
                self.assertEqual(result['source'], 'institution_browser')
                self.assertEqual(waits, ['institution_browser'])
                fixture['external'] = True
                result = manager.download({'doi': '10.1016/third'}, Path(temp) / 'third.pdf', progress)
                self.assertEqual(result['source'], 'external_doi_browser')
                self.assertTrue(observed)
                self.assertTrue(all(not cookie for cookie in observed))
                before = len(observed)
                with patch('lite_recommender.browser_access._public_addresses', side_effect=ValueError('fixture private DNS')) as dns:
                    with self.assertRaises(ValueError):
                        manager.download({'doi': '10.1016/private'}, Path(temp) / 'blocked.pdf', progress)
                    self.assertGreaterEqual(dns.call_count, 2)
                    self.assertEqual(len(observed), before)
                    self.assertFalse((Path(temp) / 'blocked.pdf').exists())
        finally:
            manager.close()
        self.assertIsNone(manager.browser)
        self.assertFalse(manager.worker.is_alive())
