#!/usr/bin/env python3
"""Synthetic, loopback-only regression tests for API redirects and call logs."""
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rm
import server


class APIHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.received.append((self.path, "POST"))
        self.send_response(302)
        self.send_header("Location", self.server.destination + "/received")
        self.end_headers()

    def do_GET(self):
        self.server.received.append((self.path, self.headers.get("x-rm12api-apitoken")))
        if self.path == "/cross":
            self.send_response(302)
            self.send_header("Location", self.server.destination + "/received")
        elif self.path == "/chain":
            self.send_response(302)
            self.send_header("Location", "/cross")
        elif self.path == "/same":
            self.send_response(302)
            self.send_header("Location", "/received")
        else:
            self.send_response(200)
        self.end_headers()
        self.wfile.write(b"[]")

    def log_message(self, *args):
        pass


class RedirectSecurity(unittest.TestCase):
    def setUp(self):
        self.servers = [http.server.ThreadingHTTPServer(("127.0.0.1", 0), APIHandler) for _ in range(2)]
        for instance in self.servers:
            instance.received = []
            threading.Thread(target=instance.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%s" % self.servers[0].server_port
        self.servers[0].destination = "http://127.0.0.1:%s" % self.servers[1].server_port

    def tearDown(self):
        for instance in self.servers:
            instance.shutdown()
            instance.server_close()

    def request(self, path):
        with mock.patch.object(rm, "BASE", self.base), mock.patch.dict(rm.ENV, {"RM_COMPANY": "synthetic"}), \
             mock.patch.object(rm, "get_token", return_value="synthetic-token"), \
             mock.patch.object(rm, "_throttle"), mock.patch.object(rm, "_touch_token"):
            return rm.get(path)

    def test_cross_origin_never_receives_api_token(self):
        with self.assertRaisesRegex(rm.RMError, "different origin"):
            self.request("/cross")
        self.assertEqual(self.servers[1].received, [])

    def test_redirect_chain_cannot_cross_origin(self):
        with self.assertRaises(rm.RMError):
            self.request("/chain")
        self.assertEqual(self.servers[1].received, [])

    def test_same_origin_redirect_remains_supported(self):
        result = self.request("/same")
        self.assertEqual(result[0], 200)
        self.assertEqual(self.servers[0].received[-1], ("/received", "synthetic-token"))


    def test_binary_blocked_without_token_touch(self):
        with mock.patch.object(rm, "BASE", self.base), mock.patch.dict(rm.ENV, {"RM_COMPANY": "synthetic"}), \
             mock.patch.object(rm, "get_token", return_value="synthetic-token"), \
             mock.patch.object(rm, "_throttle"), mock.patch.object(rm, "_touch_token") as touch:
            with self.assertRaises(rm.RMError):
                rm.get_bytes("/cross")
            touch.assert_not_called()
        self.assertEqual(self.servers[1].received, [])

    def test_handler_rejects_cross_host_downgrade_and_invalid_port(self):
        handler = rm._APIOriginRedirect()
        request = urllib.request.Request("https://api.example.test/a", headers={"x-rm12api-apitoken": "synthetic-token"})
        for target in ("https://other.example.test/a", "http://api.example.test/a", "https://api.example.test:bad/a"):
            with self.subTest(target=target), self.assertRaises(rm.RMError):
                handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, target)


class CallLogSecurity(unittest.TestCase):
    def test_capability_urls_not_written_in_args_or_errors(self):
        url = "https://files.example.test/path/capability?signature=synthetic-secret"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "calls.jsonl")
            with mock.patch.dict(os.environ, {"RM_MCP_LOG": path}):
                server.call_log("download_file", {"download_url": url, "nested": ["link: " + url]},
                                0.1, True, 5, "failure at " + url)
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            self.assertNotIn("synthetic-secret", raw)
            self.assertNotIn("capability", raw)
            record = json.loads(raw)
            self.assertEqual(record["args"]["download_url"], "[masked]")

    def test_url_in_tool_key_and_encoded_string_is_masked(self):
        url = "https://files.example.test/capability?signature=synthetic-secret"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "calls.jsonl")
            with mock.patch.dict(os.environ, {"RM_MCP_LOG": path}):
                server.call_log(url, {url: "value", "other": ["https%3A%2F%2Ffiles.example.test%2Fsynthetic-secret", "https%253A%252F%252Fsynthetic-secret", "https:%2F/synthetic-secret", "%68ttps%3A%2F%2Fsynthetic-secret"]},
                                0.1, True, 5, "unknown argument " + url)
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            self.assertNotIn("synthetic-secret", raw)
            self.assertNotIn("capability", raw)


    def test_tool_call_error_is_logged_without_url(self):
        url = "https://files.example.test/capability?signature=synthetic-secret"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "calls.jsonl")
            msg = {"id": 1, "method": "tools/call", "params": {"name": "download_file", "arguments": {"download_url": url}}}
            with mock.patch.dict(os.environ, {"RM_MCP_LOG": path}), \
                 mock.patch.object(server, "call_tool", side_effect=server.ToolError("failure " + url)), \
                 mock.patch.object(server, "send"):
                server.handle(msg)
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            self.assertNotIn("synthetic-secret", raw)
            self.assertTrue(json.loads(raw)["isError"])

    def test_tool_name_url_not_written_to_stderr(self):
        url = "https://files.example.test/capability?signature=synthetic-secret"
        msg = {"id": 1, "method": "tools/call", "params": {"name": url, "arguments": {}}}
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"RM_MCP_LOG": "off"}), mock.patch.object(server, "send"), \
             mock.patch.object(server.sys, "stderr", stderr):
            server.handle(msg)
        self.assertNotIn("synthetic-secret", stderr.getvalue())

    def test_internal_exception_url_not_written_to_stderr(self):
        url = "https://files.example.test/capability?signature=synthetic-secret"
        msg = {"id": 1, "method": "tools/call", "params": {"name": "download_file", "arguments": {}}}
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"RM_MCP_LOG": "off"}), mock.patch.object(server, "send"), \
             mock.patch.object(server, "call_tool", side_effect=RuntimeError(url)), mock.patch.object(server.sys, "stderr", stderr):
            server.handle(msg)
        self.assertNotIn("synthetic-secret", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
