import tempfile
import unittest
from pathlib import Path

from meow.execution.run_state import RunStore
from meow.infrastructure.worktree import (
    _ensure_branch_worktree,
    _ensure_existing_branch_worktree,
    _ensure_feature_worktree,
    _reject_reserved_name,
)
from meow.project.plan_files import planned_plan_file, reject_report_name
from meow.project.plan_state import PlanOwnedError, PlanStore
from meow.tasks.model import TaskGraph, TaskSpec
from tests.worktrees.test_worktree import git, make_repo


class NameValidationTests(unittest.TestCase):
    def test_path_like_names_are_rejected(self):
        for name in ("foo/bar", "foo\\bar", "../escape", "a..b", "x.", " x", "a b"):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                _reject_reserved_name(name)

    def test_plain_names_pass(self):
        for name in ("feat", "feat-2", "issue-PROJ-1", "v1.2_x"):
            _reject_reserved_name(name)
        self.assertTrue(True)

    def test_report_like_plan_names_are_rejected(self):
        for name in ("review", "add-code-review", "foo-test", "Foo-Review"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                reject_report_name(name)
        reject_report_name("testing")
        reject_report_name(None)

    def test_planned_plan_file(self):
        self.assertEqual(planned_plan_file(Path("d"), "x"), Path("d/x.md"))
        self.assertEqual(planned_plan_file(Path("d"), None), Path("d/plan.md"))


class ReuseGuardTests(unittest.TestCase):
    def test_branch_worktree_reuse_requires_the_same_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            feature = _ensure_feature_worktree(root, "issue-p-2")
            self.assertEqual(feature.name, "issue-p-2")
            with self.assertRaises(RuntimeError):
                _ensure_branch_worktree(root, "issue-p-2", "issue/P-2")

    def test_branch_worktree_reuse_on_matching_branch_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            first = _ensure_branch_worktree(root, "issue-p-2", "issue/P-2")
            again = _ensure_branch_worktree(root, "issue-p-2", "issue/P-2")
            self.assertEqual(again, first)

    def test_review_worktree_reuse_rejects_a_lossy_name_clash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "feat/x")
            git(root, "branch", "feat-x")
            _ensure_existing_branch_worktree(root, "branch-review-feat-x", "feat/x")
            with self.assertRaises(RuntimeError):
                _ensure_existing_branch_worktree(root, "branch-review-feat-x", "feat-x")


class PlanClaimTests(unittest.TestCase):
    def test_active_owner_blocks_a_second_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "foo.md"
            store = PlanStore(Path(tmp))
            store.claim(plan, "run-a", owner_finished=lambda owner: False)
            with self.assertRaises(PlanOwnedError):
                store.claim(plan, "run-b", owner_finished=lambda owner: False)
            with self.assertRaises(PlanOwnedError):
                store.assert_available(plan, lambda owner: False)
            store.assert_available(plan, lambda owner: True)
            self.assertFalse(plan.with_name("foo.md.state.lock").exists())

    def test_same_run_may_claim_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "foo.md"
            store = PlanStore(Path(tmp))
            store.claim(plan, "run-a", owner_finished=lambda owner: False)
            again = store.claim(plan, "run-a", owner_finished=lambda owner: False)
            self.assertEqual(again.run_id, "run-a")


class RunRecordSidecarTests(unittest.TestCase):
    def test_preplan_artifact_is_not_a_run_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp))
            record = store.create(
                source="prompt",
                request="x",
                repo=Path(tmp),
                worktree=Path(tmp),
                branch="main",
            )
            (store.directory / f"{record.id}.preplan.json").write_text("{}")
            self.assertEqual([path.stem for path in store.record_paths()], [record.id])
            self.assertEqual(store.latest().id, record.id)


class TaskIdTests(unittest.TestCase):
    @staticmethod
    def _graph(*ids):
        return TaskGraph(
            tuple(TaskSpec(task_id, (), (f"src/{task_id}",), ()) for task_id in ids)
        )

    def test_ids_differing_only_by_case_are_duplicates(self):
        with self.assertRaises(ValueError):
            self._graph("Api", "api").validate()

    def test_windows_device_names_are_rejected(self):
        with self.assertRaises(RuntimeError):
            self._graph("con").validate()


if __name__ == "__main__":
    unittest.main()


class ReviewFileNameTests(unittest.TestCase):
    def test_names_are_unique_and_keep_their_flavor(self):
        from meow.agents.reviewer import new_review_filename
        from meow.project.plan_files import (
            _detect_review_flavor,
            _latest_plan_file,
            _latest_review_file,
        )

        names = {new_review_filename("prompt") for _ in range(50)}
        self.assertEqual(len(names), 50)
        for flavor in ("prompt", "gitlab", "github", "branch"):
            name = new_review_filename(flavor)
            self.assertEqual(_detect_review_flavor(Path(name)), flavor)
        self.assertEqual(_detect_review_flavor(Path("review.md")), "prompt")
        self.assertEqual(_detect_review_flavor(Path("foo-review.md")), "plan")
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            (docs / "feat.md").write_text("plan")
            review = docs / new_review_filename("branch")
            review.write_text("r")
            self.assertEqual(_latest_plan_file(docs).name, "feat.md")
            self.assertEqual(_latest_review_file(docs), review)
        with self.assertRaises(ValueError):
            new_review_filename("nope")
