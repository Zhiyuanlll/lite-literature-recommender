"""Exercise the actual local HTTP boundary without external services."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lite_recommender.web import MAX_BODY, create_server


class StubService:
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.feedback_calls = []
        self.run_calls = []
        self.release = threading.Event()
        self.release.set()
        self.ticks = 0

    def overview(self):
        return {"topics": [{"id": "test", "title": "Test"}], "settings": {"budget": 10}, "runs": [], "stats": {}}

    def save_topic(self, payload):
        return payload

    def save_settings(self, payload):
        return payload

    def feedback(self, payload):
        self.feedback_calls.append(payload)
        return payload

    def run(self, **kwargs):
        self.run_calls.append(kwargs)
        self.release.wait(4)
        if kwargs.get("run_id") == "fail":
            raise ValueError("fixture failure")
        return {"run_id": "R1", "count": 1}

    def run_detail(self, run_id):
        if run_id != "R1":
            raise KeyError("missing run")
        return {"run_id": run_id, "papers": [{"title": "<script>inert</script>", "paper_id": "P1"}]}

    def export_run(self, run_id, format="bib"):
        if run_id != "R1":
            raise KeyError("missing run")
        return {"bib": "@article{test, title={Test}}", "json": '{"run_id":"R1"}', "markdown": "# Test report"}[format]

    def backup(self):
        file = self.workspace / "backup.zip"
        file.write_bytes(b"PK\x03\x04backup fixture")
        return file

    def schedule_tick(self):
        self.ticks += 1


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = StubService(self.temp.name)
        self.server = create_server(Path(self.temp.name), port=0, service=self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.service.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)
        self.temp.cleanup()

    def request(self, method, path, payload=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        defaults = {"Host": f"127.0.0.1:{self.server.server_port}"}
        if method == "POST":
            defaults.update({"Content-Type": "application/json", "X-Lite-Token": self.server.token})
        defaults.update(headers or {})
        body = raw if raw is not None else json.dumps(payload or {}) if method == "POST" else None
        conn.request(method, path, body=body, headers=defaults)
        response = conn.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        conn.close()
        return result

    def json_request(self, method, path, payload=None, headers=None):
        status, response_headers, body = self.request(method, path, payload, headers)
        return status, json.loads(body)

    def test_listens_only_on_loopback_and_serves_tokenized_page(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), body)
        self.assertNotIn(b"__LITE_TOKEN__", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_state_and_health(self):
        status, state = self.json_request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["settings"]["budget"], 10)
        self.assertEqual(self.json_request("GET", "/health")[1]["local_only"], True)

    def test_rejects_dns_rebinding_and_wrong_port(self):
        for host in ["attacker.example", "127.0.0.1:1", f"127.0.0.1:{self.server.server_port}@attacker.example"]:
            self.assertEqual(self.request("GET", "/api/state", headers={"Host": host})[0], 403)

    def test_post_requires_token(self):
        status, body = self.json_request("POST", "/api/feedback", {"rating": 5}, {"X-Lite-Token": "wrong"})
        self.assertEqual(status, 403)
        self.assertIn("error", body)
        self.assertEqual(self.service.feedback_calls, [])
        self.assertEqual(self.request("POST", "/api/topic", headers={"X-Lite-Token": "ÿ"})[0], 403)

    def test_post_rejects_foreign_and_null_origin_even_with_token(self):
        for origin in ["https://evil.example", "null", "http://127.0.0.1:1", "http://[broken", f"http://127.0.0.1:{self.server.server_port}/evil"]:
            self.assertEqual(self.request("POST", "/api/feedback", {"rating": 5}, {"Origin": origin})[0], 403)
        self.assertEqual(self.service.feedback_calls, [])

    def test_local_origin_and_feedback_forwarding(self):
        payload = {"topic_id": "test", "paper_id": "P1", "rating": 1, "reason": "scale_mismatch", "note": "中文笔记"}
        status, body = self.json_request("POST", "/api/feedback", payload, {"Origin": f"http://localhost:{self.server.server_port}"})
        self.assertEqual(status, 200)
        self.assertEqual(body, payload)
        self.assertEqual(self.service.feedback_calls, [payload])

    def test_rejects_bad_json_media_type_and_array(self):
        self.assertEqual(self.request("POST", "/api/topic", raw="{")[0], 400)
        self.assertEqual(self.request("POST", "/api/topic", raw="[]")[0], 400)
        self.assertEqual(self.request("POST", "/api/topic", headers={"Content-Type": "text/plain"})[0], 415)

    def test_rejects_large_and_chunked_body(self):
        self.assertEqual(self.request("POST", "/api/topic", raw="{}", headers={"Content-Length": str(MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.request("POST", "/api/topic", raw="{}", headers={"Transfer-Encoding": "chunked"})[0], 400)

    def test_static_allowlist_does_not_read_arbitrary_files(self):
        for path in ["/../web.py", "/static/../web.py", "/%2e%2e/service.py", "/api/file?path=/etc/passwd"]:
            self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.request("GET", "/app.js")[0], 200)
        self.assertEqual(self.request("GET", "/style.css")[0], 200)
        self.assertEqual(self.request("GET", "http://[broken")[0], 400)

    def test_topic_and_settings(self):
        topic = {"id": "test", "keywords": ["mechanics"]}
        self.assertEqual(self.json_request("POST", "/api/topic", topic), (200, topic))
        settings = {"budget": 8, "schedule": {"enabled": False}}
        self.assertEqual(self.json_request("POST", "/api/settings", settings), (200, settings))

    def test_async_run_polls_and_rejects_concurrent_job(self):
        self.service.release.clear()
        status, started = self.json_request("POST", "/api/run", {"topics": ["test"], "demo": True})
        self.assertEqual(status, 202)
        job_path = "/api/job?id=" + started["job_id"]
        self.assertEqual(self.json_request("GET", job_path)[1]["status"], "running")
        self.assertEqual(self.json_request("POST", "/api/run", {"demo": True})[0], 409)
        self.service.release.set()
        for _ in range(50):
            _, job = self.json_request("GET", job_path)
            if job["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["result"]["run_id"], "R1")
        self.assertEqual(self.service.run_calls[0], {"topics": ["test"], "demo": True, "run_id": None, "preview": False})

    def test_pdf_download_job_and_zip_delivery(self):
        report = {"downloaded": 1, "already_exists": 0, "no_link": 2, "failed": 0}
        archive = Path(self.temp.name) / "papers.zip"
        archive.write_bytes(b"PK-test-fixture")
        with patch("lite_recommender.download.download_run", return_value=report) as downloader, patch("lite_recommender.download.pdf_archive", return_value=archive):
            status, job = self.json_request("POST", "/api/pdf-download", {"run_id": "R1"})
            self.assertEqual(status, 202)
            for _ in range(50):
                _, result = self.json_request("GET", "/api/job?id=" + job["job_id"])
                if result["status"] != "running":
                    break
                time.sleep(.01)
            self.assertEqual(result["status"], "done")
            self.assertTrue(result["result"]["pdf_export"])
            downloader.assert_called_once_with(self.service, "R1", select_all=True)
            status, headers, body = self.request("GET", "/api/pdf-archive?id=R1")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/zip")
            self.assertEqual(body, archive.read_bytes())
        self.assertEqual(self.json_request("POST", "/api/pdf-download", {"run_id": 3})[0], 400)

    def test_empty_pdf_job_does_not_build_archive(self):
        report = {"downloaded": 0, "already_exists": 0, "no_link": 1, "failed": 3}
        with patch("lite_recommender.download.download_run", return_value=report), patch("lite_recommender.download.pdf_archive") as archiver:
            _, job = self.json_request("POST", "/api/pdf-download", {"run_id": "R1"})
            for _ in range(50):
                _, result = self.json_request("GET", "/api/job?id=" + job["job_id"])
                if result["status"] != "running":
                    break
                time.sleep(.01)
            self.assertEqual(result["status"], "done")
            self.assertFalse(result["result"]["has_pdfs"])
            archiver.assert_not_called()

    def test_job_failure_and_unknown_job(self):
        self.assertEqual(self.json_request("GET", "/api/job?id=missing")[0], 404)
        _, started = self.json_request("POST", "/api/run", {"run_id": "fail"})
        for _ in range(50):
            _, job = self.json_request("GET", "/api/job?id=" + started["job_id"])
            if job["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "error")
        self.assertEqual(job["error"], "fixture failure")

    def test_run_payload_validation(self):
        for payload in [{"demo": "false"}, {"topics": "test"}, {"topics": [5]}, {"run_id": 42}]:
            self.assertEqual(self.json_request("POST", "/api/run", payload)[0], 400)

    def test_run_detail_export_and_backup(self):
        self.assertEqual(self.json_request("GET", "/api/run?id=R1")[1]["run_id"], "R1")
        self.assertEqual(self.json_request("GET", "/api/run?id=missing")[0], 404)
        status, headers, body = self.request("GET", "/api/export?id=R1&format=bib")
        self.assertEqual(status, 200)
        self.assertIn(b"@article", body)
        self.assertIn('filename="literature-report.bib"', headers["Content-Disposition"])
        self.assertEqual(self.json_request("GET", "/api/export?id=R1&format=html")[0], 400)
        status, headers, body = self.request("POST", "/api/backup")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/zip")
        self.assertTrue(body.startswith(b"PK"))

    def test_scheduler_can_stop_without_waiting_a_minute(self):
        worker = threading.Thread(target=self.server.scheduler, daemon=True)
        worker.start()
        for _ in range(50):
            if self.service.ticks:
                break
            time.sleep(.002)
        self.assertGreater(self.service.ticks, 0)
        self.server.stop_event.set()
        worker.join(.2)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
