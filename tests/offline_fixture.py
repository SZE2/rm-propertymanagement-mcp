"""Opt-in, temporary synthetic API structure for unit tests only.

Never falls back to an installation's spec. The compact fixture is hand-authored
from public recipes/tests, not downloaded metadata. Build and load it through the
real spec_index code so routing/validation/search remain under test.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import server


@contextlib.contextmanager
def synthetic_spec():
    si = server.SI
    fixture_path = Path(__file__).with_name("fixtures") / "offline_spec.json"
    with fixture_path.open(encoding="utf-8") as f:
        fixture = json.load(f)
    with tempfile.TemporaryDirectory(prefix="rm_offline_") as td, contextlib.ExitStack() as stack:
        root = Path(td)
        spec = root / "spec"
        for directory in (spec / "methods", spec / "models", spec / "reports"):
            directory.mkdir(parents=True)

        def write(path, value):
            with path.open("w", encoding="utf-8") as f:
                json.dump(value, f)

        write(spec / "controllers.json", list(fixture["resources"]))
        for controller, resource in fixture["resources"].items():
            methods = [{"Name": "Get", "HasGet": True, "HasMultiGet": True,
                        "HasSingleGet": True, "GetReturnModel": resource["model"]}]
            for name, model in resource.get("subs", {}).items():
                methods.append({"Name": name, "HasGet": True, "HasMultiGet": True,
                                "MultiGetParameters": "id:int", "GetReturnModel": model})
            write(spec / "methods" / (controller + ".json"), methods)
        for model, data in fixture["models"].items():
            write(spec / "models" / (model + "Model.json"), data)
        write(spec / "reports.json", [{"ReportID": r["id"], "Name": r["name"]} for r in fixture["reports"]])
        for report in fixture["reports"]:
            params = []
            for key, kind in report["params"].items():
                source = {"DataType": "int" if kind == "properties" else kind}
                if kind == "properties":
                    source.update(EntitySources=[{"ModelSource": "Property"}], AllowsMultipleValues=True)
                params.append({"ReportParameterID": key, "DisplayName": key,
                               "ReportParameterValueSource": source})
            write(spec / "reports" / (report["id"] + ".json"), {"ReportParameters": params})
        write(spec / "enums.json", [])

        # Enter patches as contexts, not .start(): some older tests call stopall().
        # Fresh cache containers prevent matching mtimes/build seconds across fixtures.
        for obj, name, value in (
            (si, "SPEC", str(spec)), (si, "INDEX", str(spec / "index.json")),
            (si, "_IDX", [None, 0]), (si, "_IDF", {}),
            (server, "LOCAL", str(root / "local")),
            (server, "ALIASES", str(root / "local" / "aliases.json")),
            (server, "CHARGE_TYPES_FILE", str(root / "local" / "charge_types.json")),
            (server, "_cf_cache", {"mt": None, "path": None, "val": None}),
            (server, "_LOC_PROPS", {}), (server, "_INDEX_REBUILT", [False]),
            (server.rm, "TOKEN_FILE", str(root / "token.json")),
        ):
            stack.enter_context(mock.patch.object(obj, name, value))
        stack.enter_context(mock.patch.dict(os.environ, {"RM_OUT": str(root / "out"), "RM_MCP_LOG": "0"}))
        stack.enter_context(mock.patch.object(server.rm.urllib.request, "urlopen",
                                             side_effect=AssertionError("Unexpected network access in offline test")))
        si.model_info.cache_clear()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                si.build(verify=False)
            yield spec
        finally:
            si.model_info.cache_clear()


def setUpModule():
    """Import as setUpModule in each spec-dependent test module (also works directly)."""
    context = synthetic_spec()
    context.__enter__()
    unittest.addModuleCleanup(context.__exit__, None, None, None)
