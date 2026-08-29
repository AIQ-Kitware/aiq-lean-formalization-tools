from aiq_lean_tools.roadmap import compare_roadmap


def test_compare_roadmap_reports_delivery_missing_and_ambiguity(tmp_path):
    roadmap = tmp_path / "roadmap"
    project = tmp_path / "project"
    (roadmap / "TopicA").mkdir(parents=True)
    (project / "Core").mkdir(parents=True)
    (project / "Bridge").mkdir(parents=True)
    (roadmap / "TopicA" / "Suggested.lean").write_text(
        "theorem wanted : True := by sorry\n"
        "def missing : Nat := 0\n"
    )
    (project / "Core" / "Main.lean").write_text(
        "theorem wanted : True := by trivial\n"
    )
    (project / "Bridge" / "Other.lean").write_text(
        "theorem wanted : True := by trivial\n"
    )
    report = compare_roadmap(
        roadmap,
        project,
        preferred_prefixes=["Core"],
    )
    assert report.total == 2
    assert report.delivered == 1
    topic = report.topics[0]
    assert topic.mapping["wanted"] == "Core/Main.lean"
    assert topic.missing == ("missing",)
    assert len(topic.ambiguous["wanted"]) == 2
    assert "do not establish statement equivalence" in report.to_json()["semantic_warning"]


def test_compare_roadmap_library_filter(tmp_path):
    roadmap = tmp_path / "roadmap"
    project = tmp_path / "project"
    (roadmap / "Topic").mkdir(parents=True)
    (project / "Keep").mkdir(parents=True)
    (project / "Ignore").mkdir(parents=True)
    (roadmap / "Topic" / "Suggested.lean").write_text("theorem wanted : True := by sorry\n")
    (project / "Ignore" / "A.lean").write_text("theorem wanted : True := by trivial\n")
    report = compare_roadmap(roadmap, project, libraries=["Keep"])
    assert report.delivered == 0
