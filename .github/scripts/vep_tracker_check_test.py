"""Unit tests for vep_tracker_check.py. Run from the repository root:

    python3 -m unittest discover -s .github/scripts -p '*_test.py'
"""

import unittest
from unittest import mock

import vep_tracker_check as tracker

PR_LINK = "https://github.com/kubevirt/enhancements/pull/482"
VEP_MD_LINK = "https://github.com/kubevirt/enhancements/blob/main/veps/sig-compute/123-x/vep.md"

# A filled-in tracker template. The script must skip the HTML comment
# (a template hint) between the stage label and its value.
FILLED_BODY = f"""**Current Feature Stage**:
<!-- a template hint, ignored by the script -->
Alpha

**Feature Gate**: LiveMigration

**Enhancement link**: {PR_LINK}
"""


def make_issue():
    """An issue that passes all checks. Each test breaks one field."""
    return {
        "title": "VEP 123: a title",
        "body": FILLED_BODY,
        "labels": [{"name": "sig/compute"}],
        "assignees": [{"login": "someone"}],
    }


def run_checks(issue, pr_state="OPEN"):
    """Run all checks. The linked PR is reported to be in pr_state, without calling GitHub."""
    with mock.patch.object(tracker, "pr_state", return_value=pr_state):
        return tracker.findings(issue)


class ChecksTest(unittest.TestCase):
    def assert_no_findings(self, issue, pr_state="OPEN"):
        self.assertEqual(run_checks(issue, pr_state), [])

    def assert_one_finding(self, issue, expected_text, pr_state="OPEN"):
        findings = run_checks(issue, pr_state)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn(expected_text, findings[0])

    def test_filled_issue_passes(self):
        self.assert_no_findings(make_issue())

    def test_missing_assignee(self):
        issue = make_issue()
        issue["assignees"] = []
        self.assert_one_finding(issue, "No one is assigned")

    def test_missing_sig_label(self):
        issue = make_issue()
        issue["labels"] = [{"name": "kind/tracker"}]
        self.assert_one_finding(issue, "No sig label")

    def test_title_without_vep_number(self):
        issue = make_issue()
        issue["title"] = "Add hotplug"
        self.assert_one_finding(issue, "title does not mention the VEP number")

    def test_title_with_hyphenated_vep_number_passes(self):
        issue = make_issue()
        issue["title"] = "VEP-265: ppc64le"
        self.assert_no_findings(issue)

    def test_unknown_stage(self):
        issue = make_issue()
        issue["body"] = FILLED_BODY.replace("Alpha", "Soon")
        self.assert_one_finding(issue, "Current Feature Stage")

    def test_stage_with_release_passes(self):
        issue = make_issue()
        issue["body"] = FILLED_BODY.replace("Alpha", "Alpha, v1.8")
        self.assert_no_findings(issue)

    def test_empty_feature_gate(self):
        issue = make_issue()
        issue["body"] = FILLED_BODY.replace("LiveMigration", "")
        self.assert_one_finding(issue, "feature gate is empty")

    def test_link_to_merged_vep_md_passes(self):
        issue = make_issue()
        issue["body"] = FILLED_BODY.replace(PR_LINK, VEP_MD_LINK)
        self.assert_no_findings(issue)

    def test_link_to_another_repo_is_not_a_vep_pr(self):
        issue = make_issue()
        issue["body"] = FILLED_BODY.replace(PR_LINK, "kubevirt/kubevirt#123")
        self.assert_one_finding(issue, "Please link the VEP PR")

    def test_link_to_merged_pr(self):
        self.assert_one_finding(make_issue(), "merged PR", pr_state="MERGED")

    def test_short_link_to_merged_pr(self):
        issue = make_issue()
        issue["body"] = FILLED_BODY.replace(PR_LINK, "#482")
        self.assert_one_finding(issue, "merged PR", pr_state="MERGED")

    def test_link_to_closed_pr(self):
        self.assert_one_finding(make_issue(), "closed PR", pr_state="CLOSED")

    def test_failed_pr_lookup_is_not_reported(self):
        self.assert_no_findings(make_issue(), pr_state=None)

    def test_body_without_template_is_not_judged(self):
        issue = make_issue()
        issue["body"] = "Just some text."
        self.assert_no_findings(issue)


class CommentTest(unittest.TestCase):
    def test_no_line_starts_with_a_slash(self):
        """Prow runs any comment line starting with "/" as a command, e.g. /close."""
        issue = {"title": "Add hotplug", "body": "**Feature Gate**:\n", "labels": [], "assignees": []}
        text = tracker.comment(run_checks(issue))
        for line in text.splitlines():
            self.assertFalse(line.lstrip().startswith("/"), line)


if __name__ == "__main__":
    unittest.main()
