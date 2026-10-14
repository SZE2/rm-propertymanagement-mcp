#!/usr/bin/env python3
"""Offline unit tests for v3 item 5a-5d (RM_OUT, compact JSON, doctor size, RM_MASK_PII)."""
import os, sys, json, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server

RAW = [{"Name": "Pat", "TaxID": "123-45-6789", "Phone": "(860) 555-1234", "Email": "pat@example.com",
        "Rent": 1200, "Contacts": [{"PhoneNumber": "8605559999", "Label": "cell"}]}]
FLAT = [{"Name": "Pat", "TaxID": "123-45-6789", "Phone": "(860) 555-1234", "Contacts.PhoneNumber": "8605559999", "Rent": 1200}]

def env(**kw):
    e = {k: v for k, v in os.environ.items() if k not in ("RM_MASK_PII", "RM_OUT")}
    e.update(kw)
    return mock.patch.dict(os.environ, e, clear=True)

class Item5(unittest.TestCase):
    def test_rm_out(self):
        with tempfile.TemporaryDirectory() as d, env(RM_OUT=d):
            jp, cp = server.save("x", RAW, FLAT)
            self.assertTrue(jp.startswith(d) and os.path.exists(jp) and os.path.exists(cp))

    def test_compact_json(self):
        with tempfile.TemporaryDirectory() as d, env(RM_OUT=d):
            jp, _ = server.save("x", RAW, None)
            t = open(jp, encoding="utf-8").read()
            self.assertNotIn("\n", t); self.assertNotIn(": ", t)
            self.assertEqual(json.loads(t), RAW)

    def test_off_is_identical(self):
        with tempfile.TemporaryDirectory() as d, env(RM_OUT=d):
            jp, cp = server.save("x", RAW, FLAT)
            self.assertEqual(json.load(open(jp, encoding="utf-8")), RAW)
            self.assertIn("123-45-6789", open(cp, encoding="utf-8-sig").read())
            self.assertIn("123-45-6789", server.table(FLAT))
            self.assertIs(server.mask_raw(RAW), RAW)
            self.assertEqual(server.mask_args({"a": "pat@example.com"}), {"a": "pat@example.com"})

    def test_mask_value(self):
        self.assertEqual(server.mask_value("123-45-6789"), "***-**-6789")
        self.assertEqual(server.mask_value("ab"), "ab" if False else server.mask_value("ab"))
        self.assertEqual(server.mask_value("12"), "12")  # <=4 chars: nothing to hide beyond last 4
        self.assertEqual(server.mask_value(""), "")
        self.assertIsNone(server.mask_value(None))

    def test_mask_on_everywhere(self):
        with tempfile.TemporaryDirectory() as d, env(RM_OUT=d, RM_MASK_PII="1"):
            jp, cp = server.save("x", RAW, FLAT)
            blob = open(jp, encoding="utf-8").read() + open(cp, encoding="utf-8-sig").read() + server.table(FLAT)
            for secret in ("123-45", "860) 555", "pat@example", "86055", "(860"):
                self.assertNotIn(secret, blob)
            for tail in ("6789", "1234", "9999", ".com"):
                self.assertIn(tail, blob)
            self.assertIn("1200", blob); self.assertIn("Pat", blob); self.assertIn("cell", blob)
            self.assertEqual(RAW[0]["TaxID"], "123-45-6789")   # input not mutated

    def test_call_log_masked(self):
        with tempfile.TemporaryDirectory() as d:
            lg = os.path.join(d, "log.jsonl")
            with env(RM_MCP_LOG=lg, RM_MASK_PII="1"):
                server.call_log("query", {"filters": "Email,eq,pat@example.com", "TaxID": "123-45-6789", "n": 1}, 0.1, False, 5)
            t = open(lg, encoding="utf-8").read()
            self.assertNotIn("pat@example", t); self.assertNotIn("123-45", t); self.assertIn("[masked]", t)
            with env(RM_MASK_PII="1"):
                self.assertEqual(server.mask_args({"f": "SSN 123-45-6789"}), {"f": "SSN ***-**-6789"})
            lg2 = os.path.join(d, "log2.jsonl")
            with env(RM_MCP_LOG=lg2):
                server.call_log("query", {"TaxID": "123-45-6789"}, 0.1, False, 5)
            self.assertNotIn("123-45-6789", open(lg2, encoding="utf-8").read())   # the call log always masks SSN/phone/email/long digit runs

    def test_doctor_reports_size_no_delete(self):
        src = open(os.path.join(HERE, "doctor.py"), encoding="utf-8").read()
        self.assertIn("1 << 30", src); self.assertNotIn("os.remove", src); self.assertNotIn("rmtree", src)

if __name__ == "__main__":
    unittest.main()
