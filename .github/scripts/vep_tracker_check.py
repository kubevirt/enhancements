"""Best-effort checks on kubevirt/enhancements tracker issues.

A tracker issue (label kind/tracker, template .github/ISSUE_TEMPLATE/vep.md)
should have an assignee, a sig label, a "VEP <number>" title and a filled-in
template. This script reads one issue and builds the comment listing what to
update; a template field it cannot find is not judged, so a false alarm needs
an unusual issue body. The workflow runs it with --post OWNER/REPO NUMBER to
keep one comment per issue in sync. Operational owner: iholder101.

Local dry run (read-only, prints the comment, nothing when all checks pass):

    gh issue view 21 -R kubevirt/enhancements --json title,body,labels,assignees |
        python3 .github/scripts/vep_tracker_check.py
"""

import json
import re
import subprocess
import sys

MARKER = "<!-- vep-tracker-bot -->"
BOT = "github-actions[bot]"
STAGES = ("new", "alpha", "beta", "ga", "deprecated")
SCRIPT_URL = "https://github.com/kubevirt/enhancements/blob/main/.github/scripts/vep_tracker_check.py"


def gh_json(*args):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("gh failed: " + (proc.stderr.strip() or " ".join(args)))
    return json.loads(proc.stdout)


def field(body, label):
    """Value of a "**label**: value" template line: the text after the first
    colon, or the next non-empty line that is not another label. None when
    the label line is missing, "" when the value is empty."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if f"**{label}" not in line:
            continue
        value = line.split(":", 1)[1].strip(" *") if ":" in line else ""
        for later in lines[i + 1:]:
            if value or later.lstrip().startswith("**"):
                break
            if later.strip():
                value = later.strip(" *")
                break
        return value.rstrip(" .")
    return None


def pr_state(number):
    """OPEN, MERGED or CLOSED, or None when the lookup fails."""
    try:
        return gh_json("pr", "view", number, "-R", "kubevirt/enhancements", "--json", "state")["state"]
    except (RuntimeError, KeyError):
        return None


def check_assignee(issue):
    if issue["assignees"]:
        return None
    return "No one is assigned. Please assign the primary contact, for example by commenting `/assign <github-handle>`."


def check_sig(issue):
    if any(label["name"].startswith("sig/") for label in issue["labels"]):
        return None
    return "No sig label is set. Please add the owning SIG, for example by commenting `/sig <name>`."


def check_title(issue):
    if re.search(r"VEP[\s#:-]*\d+", issue["title"] or "", re.IGNORECASE):
        return None
    return "The title does not mention the VEP number, e.g. `VEP <number>: short title`."


def check_stage(body):
    value = field(body, "Current Feature Stage")
    words = value.split() if value else []
    if value is None or (words and words[0].lower().strip(".,;:") in STAGES):
        return None
    return "Please set Current Feature Stage to New, Alpha, Beta, GA or Deprecated."


def check_feature_gate(body):
    value = field(body, "Feature Gate")
    if value is None or value:
        return None
    return "The feature gate is empty. Please add its name, or N/A if the feature has none."


def check_enhancement_link(body):
    value = field(body, "Enhancement link")
    if value is None:
        return None
    if "/veps/" in value:
        return None
    match = re.search(r"github\.com/kubevirt/enhancements/pull/(\d+)|(?<![\w/.])#(\d+)", value)
    if not match:
        return "Please link the VEP PR, or the merged vep.md once the PR is merged."
    state = pr_state(match.group(1) or match.group(2))
    if state == "MERGED":
        return "The enhancement link points to a merged PR; please link the merged vep.md instead."
    if state == "CLOSED":
        return "The enhancement link points to a closed PR; please link the open VEP PR instead."
    return None


def findings(issue):
    body = re.sub(r"<!--.*?-->", "", issue["body"] or "", flags=re.DOTALL)
    checks = [check_assignee(issue), check_sig(issue), check_title(issue),
              check_stage(body), check_feature_gate(body), check_enhancement_link(body)]
    return [finding for finding in checks if finding]


def comment(found):
    bullets = ["- " + finding for finding in found] or ["All checks pass. Thanks for keeping this up to date!"]
    lines = [MARKER, "### VEP tracker check", "", *bullets, "",
             f"<sub>Posted by the [VEP tracker bot]({SCRIPT_URL}).</sub>"]
    return "\n".join(lines) + "\n"


def post(repo, number):
    """Sync the bot comment of one issue with its current findings."""
    issue = gh_json("issue", "view", number, "-R", repo, "--json", "title,body,labels,assignees")
    found = findings(issue)
    text = comment(found)
    pages = gh_json("api", "--paginate", "--slurp", f"repos/{repo}/issues/{number}/comments")
    comments = [c for page in pages for c in page]
    mine = [c for c in comments if MARKER in (c["body"] or "") and c["user"]["login"] == BOT]
    if not mine:
        if found:
            gh_json("api", "-X", "POST", "-f", f"body={text}", f"repos/{repo}/issues/{number}/comments")
    elif mine[0]["body"].replace("\r\n", "\n") != text:
        gh_json("api", "-X", "PATCH", "-f", f"body={text}", f"repos/{repo}/issues/comments/{mine[0]['id']}")


def main(argv):
    if len(argv) == 4 and argv[1] == "--post":
        post(argv[2], argv[3])
    elif len(argv) == 1:
        found = findings(json.load(sys.stdin))
        if found:
            print(comment(found), end="")
    else:
        sys.exit("usage: vep_tracker_check.py [--post OWNER/REPO NUMBER] < issue.json")


if __name__ == "__main__":
    main(sys.argv)
