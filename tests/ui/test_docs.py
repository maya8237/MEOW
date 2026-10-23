import http.client
import json
import threading
from http import HTTPStatus
from pathlib import Path

import pytest

from meow.ui import docs as docs_module
from meow.ui.docs import build_docs, docs_root
from meow.ui.server import _Server

REPO = Path(__file__).resolve().parents[2]


def test_guides_are_the_existing_markdown():
    guides = {doc["path"]: doc for doc in build_docs()["guides"]}

    assert {"README.md", "docs/CLI.md"} <= set(guides)
    assert "docs/ARCHITECTURE.md" not in guides
    for path, doc in guides.items():
        assert doc["text"] == (REPO / path).read_text(encoding="utf-8")
        assert doc["title"]


def test_skill_frontmatter_is_stripped():
    skills = {doc["title"]: doc for doc in build_docs()["skills"]}

    run = skills["/meow:run"]
    assert run["path"] == "skills/run/SKILL.md"
    assert run["description"]
    assert not run["text"].startswith("---")


def test_skills_cover_every_skill_directory():
    skills_dir = REPO / "skills"
    expected = {"/meow:" + p.parent.name for p in skills_dir.glob("*/SKILL.md")}

    assert {doc["title"] for doc in build_docs()["skills"]} == expected


def test_skills_md_lists_every_skill():
    text = (REPO / "docs" / "SKILLS.md").read_text(encoding="utf-8")

    for path in (REPO / "skills").glob("*/SKILL.md"):
        assert f"/meow:{path.parent.name}" in text


def test_missing_root_gives_empty_docs(monkeypatch):
    monkeypatch.setattr(docs_module, "docs_root", lambda: None)

    assert build_docs() == {"guides": [], "skills": []}


def test_malformed_skill_is_skipped(tmp_path, monkeypatch):
    good = tmp_path / "skills" / "good"
    bad = tmp_path / "skills" / "bad"
    good.mkdir(parents=True)
    bad.mkdir()
    (good / "SKILL.md").write_text(
        "---\nname: good\ndescription: Fine.\n---\nBody.\n", encoding="utf-8"
    )
    (bad / "SKILL.md").write_text("No frontmatter.\n", encoding="utf-8")
    monkeypatch.setattr(docs_module, "docs_root", lambda: tmp_path)

    result = build_docs()

    assert [doc["title"] for doc in result["skills"]] == ["/meow:good"]
    assert result["guides"] == []


def test_docs_root_is_the_checkout_here():
    assert docs_root() == REPO


def test_api_docs_endpoint(tmp_path):
    server = _Server(0, Path(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/docs")
        response = connection.getresponse()
        assert response.status == HTTPStatus.OK
        assert json.loads(response.read()) == build_docs()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("name", ["README.md", "docs/CLI.md"])
def test_guide_paths_are_forward_slashed(name):
    assert name in {doc["path"] for doc in build_docs()["guides"]}


def test_every_reader_guide_is_served():
    maintainer = {"ARCHITECTURE", "RELEASING", "ROADMAP"}
    expected = {
        f"docs/{p.name}"
        for p in (REPO / "docs").glob("*.md")
        if p.stem not in maintainer
    }

    served = {doc["path"] for doc in build_docs()["guides"]}

    assert expected <= served


def test_wheel_layout_is_served_from_release_data(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CLI.md").write_text("# CLI\n\nstub\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Readme\n", encoding="utf-8")
    skill = tmp_path / "plugin" / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: x\ndescription: An x skill\n---\nBody\n", encoding="utf-8"
    )
    monkeypatch.setattr(docs_module, "RELEASE_DATA", tmp_path)

    result = build_docs()

    assert docs_root() == tmp_path
    assert [g["path"] for g in result["guides"]] == ["README.md", "docs/CLI.md"]
    assert result["guides"][1]["text"] == "# CLI\n\nstub\n"
    assert [s["path"] for s in result["skills"]] == ["skills/x/SKILL.md"]
    assert result["skills"][0]["description"] == "An x skill"
