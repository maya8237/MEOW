import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_marketplace_catalog_exposes_every_meow_skill():
    catalog = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())

    plugin = next(item for item in catalog["plugins"] if item["name"] == "meow")
    assert plugin["source"] == "./"
    plugin_root = (ROOT / plugin["source"]).resolve()
    assert plugin_root == ROOT.resolve()
    skill_directories = sorted(
        path.name
        for path in (plugin_root / "skills").iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )

    assert skill_directories == [
        "customize",
        "lint",
        "migration",
        "onboard",
        "plan",
        "review",
        "run",
    ]
