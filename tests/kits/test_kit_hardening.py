"""Regressions found reviewing the kits code: lock data, held hooks, scopes."""

import json

import pytest

from meow.project.config import load_config
from meow.project.custom import kit_ops
from meow.project.custom.kit_store import fetch, kit_dir, remove_tree
from meow.project.custom.kits import kits_home, lock_file, parse_kits, read_lock

from .conftest import SKILL

AGENT = 'description = "{d}"\nprompt = "{d}"\n'
HOOK = 'description = "g"\nevent = "PreToolUse"\nmatcher = "Write"\ncommand = "{c}"\n'


def kit(make_kit, hook="echo one"):
    return make_kit({
        "skills/s/SKILL.md": SKILL.format(name="s", body="b"),
        "hooks/g.toml": HOOK.format(c=hook),
    })


def add(project, user_dir, repo, **options):
    return kit_ops.install(
        project, user_dir, str(repo.path), output=lambda _: None, **options
    )


def hooks(project):
    return [h.command for h in load_config(project)["customizations"].hooks]


def _lock(project, user_dir, scope="user"):
    return lock_file(scope, project, user_dir)


def test_lock_entry_with_non_commit_sha_is_dropped(tmp_path):
    """A lock is data: an option-like or path-like sha never reaches git or disk."""
    path = tmp_path / "kits.lock"
    path.write_text(
        json.dumps({
            "kits": {
                "evil": {"url": "x", "sha": "--upload-pack=touch pwned"},
                "up": {"url": "x", "sha": "a" * 40, "hooks_sha": "../../etc"},
                "junk": "not a table",
                "ok": {"url": "x", "sha": "b" * 40, "hooks_sha": "b" * 40},
            }
        })
    )
    assert set(read_lock(path)) == {"ok"}  # dropped entries are fetched afresh


def test_fetch_refuses_a_non_commit_sha(tmp_path):
    with pytest.raises(ValueError, match="commit id"):
        fetch(tmp_path, "https://gitlab.com/a/b.git", "--upload-pack=touch pwned")
    with pytest.raises(ValueError, match="commit id"):
        kit_dir(tmp_path, "https://gitlab.com/a/b.git", "../x")


def test_held_hooks_never_fall_back_to_the_unapproved_commit(
    make_kit, project, user_dir
):
    repo = kit(make_kit)
    add(project, user_dir, repo, name="k")
    repo.write({"hooks/g.toml": HOOK.format(c="evil")})
    kit_ops.update(project, user_dir)
    assert hooks(project) == ["echo one"]  # held
    held = read_lock(_lock(project, user_dir))["k"]["hooks_sha"]
    remove_tree(kit_dir(kits_home(user_dir), str(repo.path), held))
    assert "evil" not in hooks(project)


def test_startup_restores_a_missing_held_hooks_copy(make_kit, project, user_dir):
    repo = kit(make_kit)
    add(project, user_dir, repo, name="k")
    repo.write({"hooks/g.toml": HOOK.format(c="evil")})
    kit_ops.update(project, user_dir)
    held = read_lock(_lock(project, user_dir))["k"]["hooks_sha"]
    remove_tree(kit_dir(kits_home(user_dir), str(repo.path), held))
    kit_ops.auto_update(project, user_dir, now=0)  # not due: only restores copies
    assert hooks(project) == ["echo one"]


def test_same_name_in_two_scopes_keeps_separate_lock_state(make_kit, project, user_dir):
    user_repo, local_repo = kit(make_kit, "user"), kit(make_kit, "local")
    add(project, user_dir, user_repo, name="dup")
    add(project, user_dir, local_repo, name="dup", scope="local")
    lines = kit_ops.update(project, user_dir)
    assert not [line for line in lines if "could not" in line], lines
    user_entry = read_lock(_lock(project, user_dir))["dup"]
    local_entry = read_lock(_lock(project, user_dir, "local"))["dup"]
    for entry, repo in ((user_entry, user_repo), (local_entry, local_repo)):
        assert entry["url"] == str(repo.path)
        assert entry["hooks_sha"] == entry["sha"]  # never the other scope's commit


def test_listing_does_not_report_hold_for_old_lock_without_hooks_sha(
    make_kit, project, user_dir
):
    add(project, user_dir, kit(make_kit), name="k")
    lock = _lock(project, user_dir)
    kits = read_lock(lock)
    del kits["k"]["hooks_sha"]
    lock.write_text(json.dumps({"kits": kits}))
    assert "on hold" not in "\n".join(kit_ops.listing(project, user_dir))


def test_bad_kit_options_raise_value_errors(tmp_path):
    path = tmp_path / "c.toml"
    for entry in [
        {"name": "a", "url": "x/y", "only": 3},
        {"name": "a", "url": "x/y", "only": "skills"},
    ]:
        with pytest.raises(ValueError, match="only"):
            parse_kits([entry], "user", path)


def test_manifest_roles_must_be_a_table(make_kit, project, user_dir):
    repo = make_kit({
        "skills/s/SKILL.md": SKILL.format(name="s", body="b"),
        "meow-kit.toml": "[skills]\nroles = ['reviewer']\n",
    })
    with pytest.raises(ValueError, match="roles"):
        add(project, user_dir, repo)


def test_install_with_bad_options_leaves_config_untouched(make_kit, project, user_dir):
    config = user_dir / "config.toml"
    before = config.read_text() if config.exists() else None
    with pytest.raises(ValueError):
        add(project, user_dir, kit(make_kit), only=("skillz",))
    with pytest.raises(ValueError):
        add(project, user_dir, kit(make_kit), update="sometimes")
    after = config.read_text() if config.exists() else None
    assert after == before


def test_install_with_a_broken_agent_writes_nothing(make_kit, project, user_dir):
    repo = make_kit({"agents/a.toml": "not = [valid"})
    with pytest.raises(ValueError):
        add(project, user_dir, repo, name="k")
    assert kit_ops.collect_specs(project, user_dir) == []
    assert "k" not in read_lock(_lock(project, user_dir))


def test_one_failing_kit_does_not_stop_the_others(
    make_kit, project, user_dir, monkeypatch
):
    add(project, user_dir, kit(make_kit), name="a")
    add(project, user_dir, kit(make_kit), name="b")
    real = kit_ops._check

    def flaky(spec, entry, plan):
        if spec.name == "a":
            raise PermissionError("disk says no")
        return real(spec, entry, plan)

    monkeypatch.setattr(kit_ops, "_check", flaky)
    lines = kit_ops.update(project, user_dir)
    assert any("kit a:" in line and "disk says no" in line for line in lines)


def _other_project(tmp_path):
    path = tmp_path / "other"
    path.mkdir()
    return path


def test_cleanup_keeps_commits_another_project_pins(
    make_kit, project, user_dir, tmp_path
):
    """The cache is shared: one project's update must not delete another's commit."""
    other = _other_project(tmp_path)
    repo = kit(make_kit)
    add(other, user_dir, repo, name="k", scope="local")
    old = read_lock(_lock(other, user_dir, "local"))["k"]["sha"]
    add(project, user_dir, repo, name="k", scope="local")
    repo.write({"skills/s/SKILL.md": SKILL.format(name="s", body="v2")})
    kit_ops.update(project, user_dir)
    assert kit_dir(kits_home(user_dir), str(repo.path), old).is_dir()
    assert "s" in [s.name for s in load_config(other)["customizations"].skills]


def test_cleanup_forgets_deleted_projects(make_kit, project, user_dir, tmp_path):
    other = _other_project(tmp_path)
    repo = kit(make_kit)
    add(other, user_dir, repo, name="k", scope="local")
    old = read_lock(_lock(other, user_dir, "local"))["k"]["sha"]
    remove_tree(other)
    add(project, user_dir, repo, name="k", scope="local")
    repo.write({"skills/s/SKILL.md": SKILL.format(name="s", body="v2")})
    kit_ops.update(project, user_dir)
    assert not kit_dir(kits_home(user_dir), str(repo.path), old).is_dir()


def test_cleanup_deletes_nothing_when_a_project_is_unreadable(
    make_kit, project, user_dir, tmp_path
):
    other = _other_project(tmp_path)
    repo = kit(make_kit)
    add(other, user_dir, repo, name="k", scope="local")
    old = read_lock(_lock(other, user_dir, "local"))["k"]["sha"]
    (other / ".meow" / "config.local.toml").write_text("kits = 3\n")
    add(project, user_dir, repo, name="k", scope="local")
    repo.write({"skills/s/SKILL.md": SKILL.format(name="s", body="v2")})
    kit_ops.update(project, user_dir)
    assert kit_dir(kits_home(user_dir), str(repo.path), old).is_dir()
