import datetime
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import enforce_vep_freeze as e

# Both entries deliberately share `milestone: KubeVirt Alpha 0` (as the real
# kubevirt/sig-release schedule does) to prove the freeze entry is matched by its
# `what` text, not by `milestone`.
SCHEDULE_YAML = b"""
- when: 2026/04/30
  milestone: KubeVirt Alpha 0
  who: KubeVirt
  what: Tag v1.9.0-alpha.0
- when: 2026/04/30
  milestone: KubeVirt Alpha 0
  who: KubeVirt
  what: Virtualization Enhancement Proposal (VEP) Freeze
"""


def vep_text(status="implementable", stage="alpha", milestone=None):
    milestone = {"alpha": "v1.9"} if milestone is None else milestone
    milestone_lines = "\n".join(f"  {k}: \"{v}\"" for k, v in milestone.items()) or "  {}"
    return (
        "---\n"
        f"status: {status}\n"
        f"stage: {stage}\n"
        "milestone:\n" + milestone_lines + "\n"
        "---\n\n# Foo\n"
    )


class HasFreezeExceptionTest(unittest.TestCase):
    def test_no_labels(self):
        with mock.patch.dict("os.environ", {"PR_LABELS": "[]"}):
            self.assertFalse(e.has_freeze_exception())

    def test_other_labels(self):
        with mock.patch.dict("os.environ", {"PR_LABELS": '[{"name": "lgtm"}]'}):
            self.assertFalse(e.has_freeze_exception())

    def test_freeze_exception_label_present(self):
        labels = '[{"name": "lgtm"}, {"name": "freeze-exception"}]'
        with mock.patch.dict("os.environ", {"PR_LABELS": labels}):
            self.assertTrue(e.has_freeze_exception())

    def test_missing_env_var_defaults_to_no_labels(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(e.has_freeze_exception())


class FreezeDateTest(unittest.TestCase):
    def test_finds_the_vep_freeze_entry_by_what_not_milestone(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = io.BytesIO(SCHEDULE_YAML)
            deadline = e.freeze_date("v1.9")
        self.assertEqual(deadline, datetime.date(2026, 4, 30))

    def test_returns_none_when_no_freeze_entry(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = io.BytesIO(b"[]")
            self.assertIsNone(e.freeze_date("v1.9"))

    def test_returns_none_rather_than_crashing_on_an_empty_schedule_file(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = io.BytesIO(b"")  # yaml.safe_load -> None
            self.assertIsNone(e.freeze_date("v1.9"))

    def test_returns_none_rather_than_crashing_when_the_freeze_entry_has_no_when(self):
        schedule = b"- what: Virtualization Enhancement Proposal (VEP) Freeze\n"
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = io.BytesIO(schedule)
            self.assertIsNone(e.freeze_date("v1.9"))

    def test_warns_and_returns_none_on_network_failure(self):
        import urllib.error

        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("no route to host")
        ):
            self.assertIsNone(e.freeze_date("v1.9"))


class CheckFileTest(unittest.TestCase):
    """check_file(path, text, today) -- the per-file decision, independent of main()'s
    file I/O and argv handling, and independent of the real wall clock: `today` is
    passed in directly rather than read from datetime.now()."""

    PAST_FREEZE = datetime.date(2026, 9, 1)  # after v1.9's 2026-04-30 freeze
    BEFORE_FREEZE = datetime.date(2026, 1, 1)  # before v1.9's 2026-04-30 freeze

    def _check(self, text, today):
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = io.BytesIO(SCHEDULE_YAML)
            return e.check_file("vep.md", text, today)

    def test_implementable_past_its_freeze_is_blocked(self):
        message = self._check(vep_text(), self.PAST_FREEZE)
        self.assertIsNotNone(message)
        self.assertIn("v1.9", message)

    def test_implementable_before_its_freeze_is_not_blocked(self):
        message = self._check(vep_text(), self.BEFORE_FREEZE)
        self.assertIsNone(message)

    def test_a_vep_starting_at_beta_is_checked_against_milestone_beta_not_alpha(self):
        # Regression test: a VEP that starts directly at Beta has no milestone.alpha
        # at all. The check must follow `stage` to find the right milestone entry,
        # not silently skip enforcement because milestone.alpha is absent.
        text = vep_text(stage="beta", milestone={"beta": "v1.9"})
        message = self._check(text, self.PAST_FREEZE)
        self.assertIsNotNone(message)
        self.assertIn("v1.9", message)

    def test_a_stale_milestone_alpha_is_ignored_once_stage_has_moved_to_beta(self):
        # The VEP progressed to Beta but still carries its old milestone.alpha entry.
        # Enforcement must key off milestone.beta, not the stale milestone.alpha.
        text = vep_text(stage="beta", milestone={"alpha": "v1.0", "beta": "v1.9"})
        message = self._check(text, self.PAST_FREEZE)
        self.assertIsNotNone(message)
        self.assertIn("v1.9", message)

    def test_status_other_than_implementable_is_not_checked(self):
        text = vep_text(status="provisional")
        message = self._check(text, self.PAST_FREEZE)
        self.assertIsNone(message)

    def test_no_milestone_entry_for_the_current_stage_is_not_checked(self):
        text = vep_text(stage="beta", milestone={"alpha": "v1.9"})  # no "beta" entry
        message = self._check(text, self.PAST_FREEZE)
        self.assertIsNone(message)

    def test_a_version_without_a_v_prefix_is_normalized_before_lookup(self):
        message = self._check(vep_text(milestone={"alpha": "1.9"}), self.PAST_FREEZE)
        self.assertIsNotNone(message)
        self.assertIn("v1.9", message)


class MainTest(unittest.TestCase):
    """Thin end-to-end coverage of main()'s own responsibility: wiring check_file()
    to the file list, argv, and process exit code. Per-file rules are covered by
    CheckFileTest above."""

    def _run_main(self, directory, file_texts, pr_labels="[]"):
        list_path = os.path.join(directory, "changed_files.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for i, text in enumerate(file_texts):
                vep_path = os.path.join(directory, f"vep{i}.md")
                with open(vep_path, "w", encoding="utf-8") as vf:
                    vf.write(text)
                f.write(vep_path + "\n")

        with mock.patch.dict("os.environ", {"PR_LABELS": pr_labels}), mock.patch(
            "urllib.request.urlopen"
        ) as urlopen, mock.patch.object(sys, "argv", ["enforce_vep_freeze.py", list_path]):
            urlopen.return_value.__enter__.return_value = io.BytesIO(SCHEDULE_YAML)
            e.main()

    def test_exits_nonzero_when_any_changed_file_is_past_freeze(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as ctx:
                self._run_main(d, [vep_text(status="provisional"), vep_text()])
            self.assertEqual(ctx.exception.code, 1)

    def test_exits_zero_when_no_changed_file_is_past_freeze(self):
        with tempfile.TemporaryDirectory() as d:
            self._run_main(d, [vep_text(status="provisional")])  # should not raise

    def test_freeze_exception_label_bypasses_all_files(self):
        with tempfile.TemporaryDirectory() as d:
            self._run_main(d, [vep_text()], pr_labels='[{"name": "freeze-exception"}]')


if __name__ == "__main__":
    unittest.main()
