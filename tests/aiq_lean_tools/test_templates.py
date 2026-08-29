from aiq_lean_tools.templates import census_template, formalization_manifest_template, grounding_policy_template, semantic_review_template


def test_census_templates_have_separate_evidence_axes():
    data = census_template("Paper", family="source-semantic-alignment")
    assert data["items"] == []
    assert "status_definitions" in data
    assert "verification_definitions" in data
    assert "semantic_alignment_definitions" in data
    assert data["paper"]["citation"] == "Paper"


def test_review_template():
    data = semantic_review_template("Paper", companion_census="dev/paper.json")
    assert data["rows"] == []
    assert data["companion_census"] == "dev/paper.json"
    assert "exact" in data["relation_definitions"]


def test_workspace_templates():
    manifest = formalization_manifest_template("Demo", sources=["Paper A"])
    assert manifest["project"]["name"] == "Demo"
    assert manifest["sources"][0]["title"] == "Paper A"
    grounding = grounding_policy_template()
    assert grounding["forbid"]["admissions"] is False
