import unittest
from unittest import mock

import validate_vep as v

# A complete, self-consistent, valid front matter dict. Individual tests copy this
# with `dict(VALID_FRONT_MATTER, field=...)` and/or `del` a field to exercise one
# rule at a time; it is not tied to any particular status despite the "alpha"/
# "provisional" defaults.
VALID_FRONT_MATTER = {
    "title": "Foo",
    "vep-number": 123,
    "creation-date": "2026-01-01",
    "status": "provisional",
    "authors": ["@a"],
    "owning-sig": "sig-compute",
    "reviewers": ["TBD"],
    "approvers": ["TBD"],
    "feature-gate": "Foo",
    "stage": "alpha",
    "milestone": {"alpha": "v1.10"},
}
PATH = "veps/sig-compute/123-foo/vep.md"
META_PATH = "veps/meta-VEPs/123-foo/vep.md"


def fake_issue_exists(number):
    """A gh_api stand-in: issue `number` exists, nothing else does."""

    def gh_api(path):
        if path == f"issues/{number}":
            return {"number": number}
        return None

    return gh_api


class RequiredFieldsTest(unittest.TestCase):
    def test_invalid_yaml_short_circuits_with_a_single_error(self):
        errors = v.validate_file(PATH, "invalid-yaml", None)
        self.assertEqual(errors, ["front matter block is not valid YAML"])

    def test_every_required_field_missing_is_individually_reported(self):
        errors = v.validate_file(PATH, {"title": "Foo"}, None)
        for field in [
            "vep-number",
            "creation-date",
            "status",
            "authors",
            "owning-sig",
            "reviewers",
            "approvers",
        ]:
            self.assertTrue(
                any(field in e for e in errors), f"expected an error mentioning {field}"
            )

    def test_fully_populated_provisional_front_matter_is_valid(self):
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, VALID_FRONT_MATTER, None)
        self.assertEqual(errors, [])


class StageMilestoneTest(unittest.TestCase):
    """feature-gate/stage/milestone requirements, and the meta-VEP exemption from them."""

    def test_missing_feature_gate_for_a_regular_vep_is_rejected(self):
        fm = dict(VALID_FRONT_MATTER)
        del fm["feature-gate"]
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, fm, None)
        self.assertTrue(any("feature-gate" in e for e in errors))

    def test_meta_vep_is_exempt_from_feature_gate_and_stage(self):
        fm = dict(VALID_FRONT_MATTER)
        del fm["feature-gate"]
        del fm["stage"]
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(META_PATH, fm, None)
        self.assertEqual(errors, [])

    def test_meta_vep_with_an_informational_stage_is_not_required_to_have_a_milestone(self):
        # Regression test: a meta-VEP is exempt from stage/milestone entirely, but a
        # meta-VEP author may still set `stage` informationally (e.g. VEP-282 itself
        # tracks a GA target). That must not trigger the stage<->milestone coupling
        # rule below, which only applies to regular (non-meta) VEPs.
        fm = dict(VALID_FRONT_MATTER, stage="ga")
        del fm["milestone"]
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(META_PATH, fm, None)
        self.assertEqual(errors, [])

    def test_missing_stage_is_only_required_once_status_is_implementable(self):
        fm = dict(VALID_FRONT_MATTER, status="implementable", reviewers=["bob"], approvers=["alice"])
        del fm["stage"]
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, fm, None)
        self.assertTrue(any("stage" in e and "implementable" in e for e in errors))

    def test_stage_without_its_matching_milestone_entry_is_rejected(self):
        # stage is "alpha" (from VALID_FRONT_MATTER), but milestone only has "beta".
        fm = dict(VALID_FRONT_MATTER, milestone={"beta": "v1.11"})
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, fm, None)
        self.assertTrue(any("milestone.alpha" in e for e in errors))

    def test_a_vep_may_start_directly_at_beta_skipping_alpha(self):
        # Rare but accepted exception: the milestone requirement follows whatever
        # `stage` currently is, not a hardcoded "milestone.alpha" — so a VEP that
        # starts at Beta only needs milestone.beta set, never milestone.alpha.
        fm = dict(VALID_FRONT_MATTER, stage="beta", milestone={"beta": "v1.11"})
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, fm, None)
        self.assertEqual(errors, [])

    def test_implementable_with_no_milestone_at_all_is_rejected(self):
        fm = dict(
            VALID_FRONT_MATTER,
            status="implementable",
            reviewers=["bob"],
            approvers=["alice"],
            milestone={},
        )
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, fm, None)
        self.assertTrue(any("milestone.alpha" in e for e in errors))


class StatusAndSigValidityTest(unittest.TestCase):
    def test_status_outside_the_seven_valid_states_is_rejected(self):
        fm = dict(VALID_FRONT_MATTER, status="in-review")
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, fm, None)
        self.assertTrue(any("status" in e for e in errors))

    def test_owning_sig_outside_compute_network_storage_is_rejected(self):
        fm = dict(VALID_FRONT_MATTER, **{"owning-sig": "sig-scale"})
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, fm, None)
        self.assertTrue(any("owning-sig" in e for e in errors))


class VepNumberTest(unittest.TestCase):
    def test_vep_number_with_no_matching_issue_or_pr_is_rejected(self):
        with mock.patch.object(v, "gh_api", return_value=None):
            errors = v.validate_file(PATH, VALID_FRONT_MATTER, None)
        self.assertTrue(any("vep-number" in e for e in errors))

    def test_vep_number_matching_a_pr_instead_of_an_issue_is_rejected(self):
        # Issues and PRs share the same numbering on GitHub; a `pull_request` key on
        # the response is how the REST API distinguishes them.
        with mock.patch.object(v, "gh_api", return_value={"number": 123, "pull_request": {}}):
            errors = v.validate_file(PATH, VALID_FRONT_MATTER, None)
        self.assertTrue(any("vep-number" in e for e in errors))


class ImplementableGateTest(unittest.TestCase):
    def test_tbd_reviewers_and_approvers_are_rejected_once_implementable(self):
        fm = dict(VALID_FRONT_MATTER, status="implementable")
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, fm, None)
        self.assertTrue(any("reviewers" in e for e in errors))
        self.assertTrue(any("approvers" in e for e in errors))

    def test_named_reviewers_and_approvers_with_a_milestone_is_valid(self):
        fm = dict(
            VALID_FRONT_MATTER,
            status="implementable",
            reviewers=["bob"],
            approvers=["alice"],
        )
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, fm, None)
        self.assertEqual(errors, [])


class ReplacedTest(unittest.TestCase):
    def test_replaced_without_superseded_by_is_rejected(self):
        fm = dict(VALID_FRONT_MATTER, status="replaced")
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, fm, None)
        self.assertTrue(any("superseded-by" in e for e in errors))

    def test_replaced_with_superseded_by_is_valid(self):
        fm = dict(VALID_FRONT_MATTER, status="replaced", **{"superseded-by": "999"})
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, fm, None)
        self.assertEqual(errors, [])


class TransitionTest(unittest.TestCase):
    """Status transitions: only what veps/meta-VEPs/282.../vep.md's state diagram draws
    an arrow for is allowed; everything else -- including every transition out of
    deferred/rejected/withdrawn/replaced, which the diagram gives no way out of -- is
    rejected. See ALLOWED_TRANSITIONS in validate_vep.py.
    """

    def _front_matter_for(self, status):
        """A self-consistent front matter dict that would pass every OTHER rule at
        the given status, so a transition test only ever fails on the transition
        check itself."""
        fm = dict(VALID_FRONT_MATTER, status=status)
        if status in ("implementable", "implemented"):
            fm.update(reviewers=["bob"], approvers=["alice"])
        if status == "replaced":
            fm["superseded-by"] = "999"
        return fm

    def _transition_errors(self, old_status, new_status):
        base = self._front_matter_for(old_status)
        head = self._front_matter_for(new_status)
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            errors = v.validate_file(PATH, head, base)
        return [e for e in errors if "transition" in e]

    # -- Named anchor cases, matching the specific examples called out in the VEP --

    def test_provisional_to_implementable_is_the_normal_progression(self):
        self.assertEqual(self._transition_errors("provisional", "implementable"), [])

    def test_implementable_to_implemented_is_the_normal_progression(self):
        self.assertEqual(self._transition_errors("implementable", "implemented"), [])

    def test_implemented_back_to_implementable_is_explicitly_forbidden(self):
        self.assertTrue(self._transition_errors("implemented", "implementable"))

    def test_provisional_directly_to_implemented_skips_a_state_and_is_rejected(self):
        self.assertTrue(self._transition_errors("provisional", "implemented"))

    def test_a_terminal_status_like_rejected_cannot_transition_anywhere(self):
        self.assertTrue(self._transition_errors("rejected", "provisional"))

    # -- Exhaustive matrix over every (old, new) pair, cross-checked against the
    # module's own ALLOWED_TRANSITIONS table, so a change that silently loosens or
    # tightens the table is caught even if no named test above happens to cover it. --

    def test_every_status_pair_matches_the_allowed_transitions_table(self):
        for old_status in v.VALID_STATUSES:
            for new_status in v.VALID_STATUSES:
                if old_status == new_status:
                    continue  # not a transition
                with self.subTest(old=old_status, new=new_status):
                    errors = self._transition_errors(old_status, new_status)
                    if new_status in v.ALLOWED_TRANSITIONS[old_status]:
                        self.assertEqual(errors, [])
                    else:
                        self.assertTrue(errors)


GOOD_TEXT = (
    "---\n"
    "title: Foo\n"
    "vep-number: 123\n"
    'creation-date: "2026-01-01"\n'
    "status: provisional\n"
    "authors: ['@a']\n"
    "owning-sig: sig-compute\n"
    "reviewers: [TBD]\n"
    "approvers: [TBD]\n"
    "feature-gate: Foo\n"
    "stage: alpha\n"
    "milestone: {alpha: v1.10}\n"
    "---\n\n# Foo\n"
)
LEGACY_TEXT = "# Foo\n\nSome pre-existing VEP body with no front matter.\n"


class EvaluateFileTest(unittest.TestCase):
    """evaluate_file()'s migration-friendly branching, ahead of validate_file()'s
    detailed rules (covered above)."""

    def test_new_file_with_front_matter_is_ok(self):
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            status, messages = v.evaluate_file(PATH, GOOD_TEXT, None)
        self.assertEqual((status, messages), ("ok", []))

    def test_new_file_without_front_matter_is_an_error(self):
        status, messages = v.evaluate_file(PATH, LEGACY_TEXT, None)
        self.assertEqual(status, "error")
        self.assertTrue(any("front matter" in m for m in messages))

    def test_pre_existing_file_still_without_front_matter_is_a_warning_not_a_block(self):
        # base and head both lack front matter: not yet migrated (WI-7), don't block.
        status, messages = v.evaluate_file(PATH, LEGACY_TEXT, LEGACY_TEXT)
        self.assertEqual(status, "warn")
        self.assertTrue(any("not yet migrated" in m for m in messages))

    def test_removing_previously_present_front_matter_is_an_error_not_a_warning(self):
        # base had valid front matter, head removed it: a regression, not a migration gap.
        status, messages = v.evaluate_file(PATH, LEGACY_TEXT, GOOD_TEXT)
        self.assertEqual(status, "error")
        self.assertTrue(any("removed" in m for m in messages))

    def test_front_matter_kept_valid_across_the_pr_is_ok(self):
        with mock.patch.object(v, "gh_api", new=fake_issue_exists(123)):
            status, messages = v.evaluate_file(PATH, GOOD_TEXT, GOOD_TEXT)
        self.assertEqual((status, messages), ("ok", []))

    def test_evaluate_file_delegates_field_validation_to_validate_file(self):
        # Any validate_file() rule would do here; a missing field is just a simple one
        # to trigger. The rule-specific behavior itself is covered by the test classes
        # above -- this only confirms evaluate_file() surfaces validate_file()'s errors.
        head_text = "---\ntitle: Foo\n---\n\n# Foo\n"  # missing most required fields
        status, messages = v.evaluate_file(PATH, head_text, None)
        self.assertEqual(status, "error")
        self.assertTrue(any("vep-number" in m for m in messages))


if __name__ == "__main__":
    unittest.main()
