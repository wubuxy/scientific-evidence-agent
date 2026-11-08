"""The fault report must not turn skipped/missing runs into successful evidence."""
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

spec = importlib.util.spec_from_file_location("eval_agent_faults", Path(__file__).resolve().parents[2]/"scripts/eval_agent_faults.py")
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def fixture_xml():
    root = ET.Element("testsuite")
    for case_id, name, _ in matrix.CASES:
        case = ET.SubElement(root, "testcase", name=name, time="0.1")
        if case_id not in matrix.CONTRACT_CASES:
            props = ET.SubElement(case, "properties")
            ET.SubElement(props, "property", name="runtime_outcome", value=json.dumps(dict(
                status="completed", stop_reason="fixture", unresolved=1, tokens_used=100,
                tokens_estimated=False, semantic_success=None)))
    return root


def test_missing_report_keeps_every_planned_case_in_denominator(tmp_path):
    result = matrix.summarize(tmp_path/"absent.xml", 2)
    assert result["status"] == "failed"
    assert result["counts"] == {"missing": len(matrix.CASES)}
    assert result["semantic_accuracy"] is None


@pytest.mark.parametrize("fault", ["skipped", "duplicate", "no_runtime", "exit_code", "semantic_success"])
def test_fault_report_rejects_incomplete_or_invalid_success(tmp_path, fault):
    root = fixture_xml()
    runtime = next(case for case in root if case.find("properties") is not None)
    if fault == "skipped": ET.SubElement(root[0], "skipped")
    if fault == "duplicate": ET.SubElement(root, "testcase", name=root[0].get("name"))
    if fault == "no_runtime": runtime.remove(runtime.find("properties"))
    if fault == "semantic_success":
        prop = runtime.find("./properties/property")
        value = json.loads(prop.get("value")); value["semantic_success"] = True
        prop.set("value", json.dumps(value))
    path = tmp_path/"result.xml"
    ET.ElementTree(root).write(path, encoding="utf-8")
    result = matrix.summarize(path, 1 if fault == "exit_code" else 0)
    assert result["status"] == "failed"
    assert result["planned"] == len(matrix.CASES)
    assert len(result["cases"]) == len(matrix.CASES)
