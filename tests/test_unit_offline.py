"""Synthetic spec regressions: real loading, validation and routing, no API access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server
from offline_fixture import synthetic_spec


class SyntheticSpec(unittest.TestCase):
    def test_offline_runner_blocks_dns_before_any_network_access(self):
        runner = Path(__file__).resolve().parent.parent / "tools" / "test_offline.py"
        with tempfile.TemporaryDirectory() as td:
            probe = Path(td) / "probe.py"
            probe.write_text(
                "import runpy, socket, sys\n"
                "runner = runpy.run_path(sys.argv[1])\n"
                "sys.addaudithook(runner['deny_network'])\n"
                "socket.getaddrinfo('example.invalid', 443)\n", encoding="utf-8")
            result = subprocess.run([sys.executable, "-S", str(probe), str(runner)],
                                    capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Network access is forbidden in the offline suite", result.stderr)

    def test_builds_loadable_source_files_and_search_index(self):
        with synthetic_spec() as spec:
            self.assertEqual(Path(server.SI.INDEX), spec / "index.json")
            index = server.idx()
            self.assertEqual(index["counts"], {"resources": 7, "reports": 7, "enums": 0, "models_on_disk": 8})
            self.assertIn("HistoryNotes", index["resources"])
            self.assertEqual(server.SI.search("SummaryRentRoll", "reports", 1)[0][1], "69")
            self.assertEqual(server.find_resource("Tenants/3/Transactions")["model"], "Transaction")
            for resource in index["resources"].values():
                self.assertIsNotNone(server.SI.model_info(resource["model"]))
                for sub in resource.get("subs", {}).values():
                    self.assertIsNotNone(server.SI.model_info(sub["model"]))

    def test_invalid_names_are_rejected_by_real_model_validation(self):
        with synthetic_spec():
            self.assertEqual(server.validate("Unit", fields="unitid", filters="UnitID,in,3;4")[0:3],
                             ("UnitID", "", "UnitID,in,(3,4)"))
            self.assertIn("not in Unit", server.validate("Unit", fields="UnknownColumn")[4][0])
            self.assertIn("not filterable", server.validate("Unit", filters="UnknownColumn,eq,3")[4][0])
            self.assertIn("Tenants/{id}/HistoryNotes",
                          server.validate("HistoryNote", filters="Tenant.TenantID,eq,3")[4][0])

    def test_fixture_paths_caches_and_output_are_restored(self):
        si = server.SI
        original = (si.SPEC, si.INDEX, si._IDX, si._IDF, server.ALIASES, server._LOC_PROPS, server.rm.TOKEN_FILE)
        original_out = os.environ.get("RM_OUT")
        with synthetic_spec() as spec:
            first_index = server.idx()
            self.assertIsNotNone(si.model_info("Unit"))
            self.assertNotEqual(server.ALIASES, original[4])
            self.assertNotEqual(server.rm.TOKEN_FILE, original[6])
            self.assertNotEqual(os.environ["RM_OUT"], original_out)
        self.assertFalse(spec.exists())
        self.assertEqual((si.SPEC, si.INDEX, si._IDX, si._IDF, server.ALIASES, server._LOC_PROPS, server.rm.TOKEN_FILE), original)
        self.assertIs(si._IDX, original[2])
        self.assertIs(si._IDF, original[3])
        self.assertEqual(os.environ.get("RM_OUT"), original_out)
        with synthetic_spec():
            self.assertIsNot(server.idx(), first_index)
            self.assertIsNotNone(si.model_info("Unit"))

    def test_cleanup_on_exception(self):
        before = server.SI.SPEC
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            with synthetic_spec() as spec:
                raise RuntimeError("synthetic failure")
        self.assertEqual(server.SI.SPEC, before)
        self.assertFalse(spec.exists())

    def test_index_hot_reload_uses_real_file(self):
        with synthetic_spec() as spec:
            before = server.idx()
            path = spec / "index.json"
            mtime = path.stat().st_mtime
            changed = dict(before, built="synthetic-rebuild")
            with path.open("w", encoding="utf-8") as f:
                json.dump(changed, f)
            os.utime(path, (mtime + 2, mtime + 2))
            self.assertEqual(server.idx()["built"], "synthetic-rebuild")
            self.assertIsNot(server.idx(), before)

    def test_missing_index_is_not_replaced_by_test_data_at_runtime(self):
        with synthetic_spec() as spec:
            # Warm the cache first: missing metadata must not reuse cached data.
            server.idx()
            with mock.patch.object(server.SI, "INDEX", str(spec / "absent.json")):
                with self.assertRaisesRegex(server.ToolError, "spec/index.json missing"):
                    server.t_query("HistoryNotes", "Tenant.TenantID,eq,3")

    def test_unexpected_network_is_rejected_even_after_stopall(self):
        with synthetic_spec():
            mock.patch.stopall()
            self.assertIsNotNone(server.idx())
            with self.assertRaisesRegex(AssertionError, "Unexpected network access"):
                server.rm.urllib.request.urlopen("https://example.invalid/")

    def test_history_routing_through_real_query_validation_and_get_stub(self):
        calls = []

        def api(path, params=None, timeout=None):
            calls.append((path, dict(params or {})))
            return 200, {"x-total-results": "1"}, [{"HistoryID": int(path.split("/")[2])}]

        with synthetic_spec(), mock.patch.object(server, "api", api):
            out = server.t_query("HistoryNotes", "Tenant.TenantID,in,(3,4);HistoryCategoryID,eq,9", fields="HistoryID")
            self.assertTrue(out.startswith("Routed:"), out)
            self.assertEqual([path for path, _ in calls], ["/Tenants/3/HistoryNotes", "/Tenants/4/HistoryNotes"])
            for _, params in calls:
                self.assertEqual(params["filters"], "HistoryCategoryID,eq,9")
                self.assertEqual(params["fields"], "HistoryID")
            self.assertEqual(len(list(Path(os.environ["RM_OUT"]).glob("*_HistoryNotes_*.json"))), 2)
            calls.clear()
            with self.assertRaisesRegex(server.ToolError, "at most 25"):
                server.t_query("HistoryNotes", "Tenant.TenantID,in,(%s)" % ",".join(map(str, range(1, 27))))
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
