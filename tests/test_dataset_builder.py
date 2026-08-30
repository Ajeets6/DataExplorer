import json

from scripts.build_sec_enterprise_dataset import build_dataset


def test_offline_enterprise_dataset_contains_safeguard_cases(tmp_path) -> None:
    manifest = build_dataset(tmp_path, offline=True, user_agent=None)
    assert len(manifest["documents"]) == 9
    assert manifest["structured_sources"] == []
    behaviors = {item["expected_behavior"] for item in manifest["documents"]}
    assert {"cross_tenant_deny", "block_injection", "prefer_current"} <= behaviors
    questions = [json.loads(line) for line in (tmp_path / "golden_questions.jsonl").read_text().splitlines()]
    assert len(questions) == 50
    assert any(row["category"] == "acl_deny" for row in questions)
    assert any(row["category"] == "sec_filing" for row in questions)


def test_online_dataset_requires_declared_contact(tmp_path) -> None:
    try:
        build_dataset(tmp_path, offline=False, user_agent="DataExplorer")
    except ValueError as error:
        assert "contact email" in str(error)
    else:
        raise AssertionError("online build must reject an undeclared contact")
