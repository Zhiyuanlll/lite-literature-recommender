import copy
import datetime as dt
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
import zipfile
from unittest.mock import Mock, patch

from lite_recommender import download

PDF = b"%PDF-1.7\nsynthetic test fixture\n%%EOF\n"


class Response:
    def __init__(self, body=PDF, status=200, headers=None):
        self.status = status
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.body = io.BytesIO(body)
        self.closed = False

    def read(self, count):
        return self.body.read(count)

    def getheader(self, key):
        return self.headers.get(key.lower())

    def close(self):
        self.closed = True


class ServiceFixture:
    def __init__(self, workspace, papers, mode="real"):
        self.workspace = Path(workspace)
        self.state = {"run_id": "test-real", "mode": mode, "papers": copy.deepcopy(papers)}

    def run_detail(self, run_id):
        assert run_id == "test-real"
        return copy.deepcopy(self.state)

    def now(self):
        return dt.datetime(2026, 10, 14, 9, tzinfo=dt.timezone.utc)


def paper(idx=0, rating=4, status="read", **extra):
    return {"paper_id": "p_test_" + str(idx), "topic_id": "general_topic", "title": "Synthetic HTTP fixture " + str(idx),
            "is_demo": False, "pdf_url": "https://papers.example.test/file.pdf",
            "feedback": {"rating": rating, "read_status": status}, **extra}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lite-download-")
        self.root = Path(self.temp.name) / "中文 空格"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def open_response(self, body=PDF, status=200, headers=None):
        return Mock(), Response(body, status, headers)

    def test_explicit_batch_export_includes_unrated_preview_and_manifest(self):
        service = ServiceFixture(self.root, [paper(0, rating=None), paper(1, rating=1), paper(2, pdf_url="")], mode="preview")
        original = copy.deepcopy(service.state)
        with patch.object(download, "_open_response", side_effect=lambda url: self.open_response()):
            report = download.download_run(service, "test-real", select_all=True)
            self.assertEqual(report["downloaded"], 2)
            self.assertEqual(report["no_link"], 1)
            second = download.download_run(service, "test-real", select_all=True)
            self.assertEqual(second["already_exists"], 2)
        archive = download.pdf_archive(service, "test-real")
        with zipfile.ZipFile(archive) as z:
            manifest = json.loads(z.read("download_report.json"))
            self.assertEqual(manifest["pdf_files"], 2)
            self.assertNotIn("report_path", manifest)
            for item in manifest["items"]:
                if "filename" in item:
                    self.assertEqual(z.read(item["filename"]), PDF)
        self.assertEqual(service.state, original)
        with self.assertRaises(ValueError):
            download.pdf_archive(service, "../bad")

    def test_user_threshold_cited_and_demo_filter_and_state_unchanged(self):
        values = [paper(0, rating=5), paper(1, rating=3), paper(2, rating=None, status="cited"),
                  paper(3, rating=5, is_demo=True), paper(4, rating=None, status="read")]
        service = ServiceFixture(self.root, values)
        original = copy.deepcopy(service.state)
        with patch.object(download, "_open_response", side_effect=lambda url: self.open_response()) as opener:
            report = download.download_run(service, "test-real")
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(report["downloaded"], 2)
        self.assertEqual(report["not_selected"], 2)
        self.assertEqual(report["demo_skipped"], 1)
        self.assertEqual(service.state, original)
        stored = json.loads(Path(report["report_path"]).read_text())
        self.assertEqual(stored, report)
        self.assertEqual((self.root / "_shared/pdfs/p_test_0.pdf").read_bytes(), PDF)

    def test_demo_run_never_downloads_even_unmarked_fixture(self):
        service = ServiceFixture(self.root, [paper()], mode="demo")
        with patch.object(download, "_open_response") as opener:
            report = download.download_run(service, "test-real")
        opener.assert_not_called()
        self.assertEqual(report["demo_skipped"], 1)

    def test_existing_pdf_is_skipped_and_duplicate_topics_share_file(self):
        values = [paper(), paper(topic_id="another_topic")]
        service = ServiceFixture(self.root, values)
        with patch.object(download, "_open_response", side_effect=lambda url: self.open_response()) as opener:
            report = download.download_run(service, "test-real")
            second = download.download_run(service, "test-real")
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(report["downloaded"], 1)
        self.assertEqual(report["already_exists"], 1)
        self.assertEqual(second["already_exists"], 2)

    def test_no_link_http_failure_then_next_paper_continues(self):
        service = ServiceFixture(self.root, [paper(0, pdf_url=""), paper(1), paper(2)])
        with patch.object(download, "_open_response", side_effect=[self.open_response(status=403), self.open_response()]):
            report = download.download_run(service, "test-real")
        self.assertEqual((report["no_link"], report["failed"], report["downloaded"]), (1, 1, 1))
        self.assertFalse((self.root / "_shared/pdfs/p_test_1.pdf").exists())

    def test_historical_aliases_save_to_canonical_file_once(self):
        service = ServiceFixture(self.root, [paper(0, canonical_paper_id="p_canonical"),
                                             paper(1, canonical_paper_id="p_canonical")])
        with patch.object(download, "_open_response", side_effect=lambda url: self.open_response()) as opener:
            report = download.download_run(service, "test-real")
        opener.assert_called_once()
        self.assertEqual(report["downloaded"], 1)
        self.assertEqual(report["already_exists"], 1)
        self.assertEqual((self.root / "_shared/pdfs/p_canonical.pdf").read_bytes(), PDF)
        self.assertFalse((self.root / "_shared/pdfs/p_test_0.pdf").exists())
        self.assertEqual(report["items"][0]["snapshot_paper_id"], "p_test_0")

    def test_version_link_fallback_and_signed_query_redaction(self):
        service = ServiceFixture(self.root, [paper(pdf_url="http://unsafe.example/file", versions=[
            {"pdf_url": "https://archive.example.test/file.pdf?signature=private-query"}])])
        with patch.object(download, "_open_response", side_effect=lambda url: self.open_response()) as opener:
            report = download.download_run(service, "test-real")
        opener.assert_called_once_with("https://archive.example.test/file.pdf?signature=private-query")
        self.assertEqual(report["downloaded"], 1)
        self.assertNotIn("private-query", json.dumps(report))
        self.assertEqual(len(report["items"][0]["attempt_errors"]), 1)

    def test_redirect_to_private_host_rejected_before_second_request(self):
        service = ServiceFixture(self.root, [paper()])
        first = self.open_response(status=302, headers={"Location": "https://127.0.0.1/secrets"})
        with patch.object(download, "_open_response", return_value=first) as opener:
            report = download.download_run(service, "test-real")
        opener.assert_called_once()
        self.assertEqual(report["failed"], 1)
        self.assertTrue(first[1].closed)
        first[0].close.assert_called_once()

    def test_valid_public_redirect_closes_responses(self):
        service = ServiceFixture(self.root, [paper()])
        first = self.open_response(status=302, headers={"Location": "https://repository.example.test/download.pdf"})
        second = self.open_response()
        with patch.object(download, "_open_response", side_effect=[first, second]) as opener:
            report = download.download_run(service, "test-real")
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(report["downloaded"], 1)
        self.assertTrue(first[1].closed and second[1].closed)

    def test_html_partial_and_oversize_never_replaces_destination(self):
        destination = self.root / "_shared/pdfs/p_test_0.pdf"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"old invalid file")
        for body, headers in [(b"<html>login</html>", {}), (PDF, {"Content-Length": str(len(PDF) + 10)}),
                              (PDF, {"Content-Length": str(download.MAX_BYTES + 1)})]:
            with self.subTest(headers=headers, body=body):
                service = ServiceFixture(self.root, [paper()])
                with patch.object(download, "_open_response", return_value=self.open_response(body=body, headers=headers)):
                    report = download.download_run(service, "test-real")
                self.assertEqual(report["failed"], 1)
                self.assertEqual(destination.read_bytes(), b"old invalid file")
                self.assertEqual(list(destination.parent.glob(".pdf-*.part")), [])

    def test_stream_limit_and_timeout_remove_partial_files(self):
        service = ServiceFixture(self.root, [paper()])
        with patch.object(download, "MAX_BYTES", 16), patch.object(download, "_open_response", return_value=self.open_response()):
            report = download.download_run(service, "test-real")
        self.assertEqual(report["failed"], 1)
        response = Response()
        response.read = Mock(side_effect=[b"%PDF-1.7\n", TimeoutError("synthetic timeout")])
        with patch.object(download, "_open_response", return_value=(Mock(), response)):
            report = download.download_run(service, "test-real")
        self.assertEqual(report["failed"], 1)
        self.assertFalse((self.root / "_shared/pdfs/p_test_0.pdf").exists())
        self.assertEqual(list((self.root / "_shared/pdfs").glob(".pdf-*.part")), [])

    def test_private_literal_url_credentials_and_invalid_name_rejected(self):
        for url in ("http://public.example/file", "https://localhost/file", "https://127.0.0.1/file",
                    "https://10.0.0.1/file", "https://169.254.169.254/file", "https://[::1]/file",
                    "https://[fc00::1]/file", "https://user:secret@public.example/file"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                download._validate_url(url)
        service = ServiceFixture(self.root, [paper(paper_id="../outside")])
        with patch.object(download, "_open_response") as opener:
            report = download.download_run(service, "test-real")
        opener.assert_not_called()
        self.assertEqual(report["failed"], 1)

    def test_fake_ip_uses_public_resolver_without_connecting_fake_address(self):
        fake = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.92", 443))
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("151.101.0.95", 443))
        with patch.object(socket, "getaddrinfo", return_value=[fake]), patch.object(download, "_doh_addresses", return_value=[public]) as resolver:
            self.assertEqual(download._public_addresses("www.nature.com", 443), [public])
            resolver.assert_called_once_with("www.nature.com", 443)
        with patch.object(socket, "getaddrinfo", return_value=[fake]), patch.object(download, "_doh_addresses", side_effect=OSError("offline")), self.assertRaisesRegex(ValueError, "Fake-IP"):
            download._public_addresses("www.nature.com", 443)

    def test_doh_validates_answers_and_uses_authenticated_public_endpoint(self):
        for value in ["151.101.0.95", "127.0.0.1", "198.18.0.92"]:
            answer = {"Status": 0, "Question": [{"name": "www.nature.com.", "type": 1}], "Answer": [{"type": 1, "data": value}]}
            conn = Mock()
            conn.getresponse.return_value = Response(json.dumps(answer).encode())
            with patch.object(download, "_PinnedHTTPSConnection", return_value=conn) as constructor:
                if value == "151.101.0.95":
                    result = download._doh_addresses("www.nature.com", 443)
                    self.assertEqual(result[0][4], (value, 443))
                else:
                    with self.assertRaises(ValueError):
                        download._doh_addresses("www.nature.com", 443)
                self.assertEqual(constructor.call_args.args[0], "cloudflare-dns.com")
                self.assertEqual(constructor.call_args.args[2][0][4], ("1.1.1.1", 443))

    def test_stale_success_report_does_not_create_an_empty_archive(self):
        service = ServiceFixture(self.root, [paper()])
        with patch.object(download, "_open_response", return_value=self.open_response()):
            download.download_run(service, "test-real", select_all=True)
        (self.root / "_shared/pdfs/p_test_0.pdf").unlink()
        with self.assertRaisesRegex(ValueError, "不生成空ZIP"):
            download.pdf_archive(service, "test-real")
        self.assertFalse((self.root / "_shared/runs/test-real/papers.zip").exists())
        self.assertEqual(list((self.root / "_shared/runs/test-real").glob(".pdf-archive-*")), [])

    def test_empty_batch_has_detailed_report_but_no_zip(self):
        service = ServiceFixture(self.root, [paper(), paper(1, pdf_url="")])
        with patch.object(download, "_open_response", return_value=self.open_response(status=403)):
            download.download_run(service, "test-real", select_all=True)
        summary = download.download_summary(service, "test-real")
        self.assertIn("403", summary["items"][0]["message"])
        self.assertNotIn("path", json.dumps(summary))
        with self.assertRaisesRegex(ValueError, "不生成空ZIP"):
            download.pdf_archive(service, "test-real")
        self.assertFalse((self.root / "_shared/runs/test-real/papers.zip").exists())

    def test_dns_private_or_mixed_address_rejected(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.2", 443))
        for addresses in ([private], [public, private], []):
            with self.subTest(addresses=addresses), patch.object(socket, "getaddrinfo", return_value=addresses), self.assertRaises(ValueError):
                download._public_addresses("fixture.example.test", 443)
        with patch.object(socket, "getaddrinfo", return_value=[public, public]):
            self.assertEqual(download._public_addresses("fixture.example.test", 443), [public])

    def test_connection_uses_pinned_sockaddr_and_original_tls_name(self):
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        connection = download._PinnedHTTPSConnection("fixture.example.test", 443, [address])
        raw = Mock()
        context = Mock()
        connection._context = context
        with patch.object(socket, "socket", return_value=raw), patch.object(socket, "getaddrinfo") as resolver:
            connection.connect()
        resolver.assert_not_called()
        raw.connect.assert_called_once_with(("93.184.216.34", 443))
        raw.settimeout.assert_called_once_with(15)
        context.wrap_socket.assert_called_once_with(raw, server_hostname="fixture.example.test")

    def test_request_has_no_cookies_or_authorization(self):
        conn = Mock()
        with patch.object(download, "_public_addresses", return_value=[]), patch.object(download, "_PinnedHTTPSConnection", return_value=conn):
            download._open_response("https://papers.example.test/a.pdf?query=1")
        args, kwargs = conn.request.call_args
        self.assertEqual(args, ("GET", "/a.pdf?query=1"))
        headers = {key.lower() for key in kwargs["headers"]}
        self.assertNotIn("cookie", headers)
        self.assertNotIn("authorization", headers)

    def test_invalid_threshold_and_run_path_rejected(self):
        service = ServiceFixture(self.root, [paper()])
        for threshold in (0, 6, True, "4"):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                download.download_run(service, "test-real", threshold)
        with self.assertRaises(ValueError):
            download.download_run(service, "../outside")


if __name__ == "__main__":
    unittest.main()
