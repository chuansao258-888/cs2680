#!/usr/bin/env python3
"""Rebuild the 3 synthesized verifiers on the official v2 grading chain.

The first synthesis ran pytest directly, which broke ansible instances whose
tests import the installed dist-packages copy instead of /app/lib (the official
run_script.sh sets PYTHONPATH / uses ansible-test). This copies the official
test.sh + run_script.sh + parser.py from run_scripts/<iid>/ and replaces only
the test_patch.patch application with our /tests/files overlay.
"""

import os
import shutil

CACHE = os.environ.get("A3_CACHE", os.path.expanduser("~/.cache/cs2680_a3"))
REPO = os.path.join(CACHE, "SWE-bench_Pro-os")
TARGETS = ("a26c325", "83909bfa", "21b426b")

APPLY_BLOCK = '''# Apply the test patch (adds/updates the gold test files)
echo "Applying test patch..."
git apply --verbose /tests/test_patch.patch || { echo "ERROR: Failed to apply test patch"; exit 1; }'''

OVERLAY_BLOCK = '''# Synthesized instances have no test_patch.patch: overlay the official
# post-PR test files downloaded at the instance's test commit instead.
echo "Overlaying official post-PR test files..."
while IFS= read -r f; do
  [ -f "/tests/files/$f" ] || continue
  mkdir -p "$ROOT/$(dirname "$f")"
  cp -f "/tests/files/$f" "$ROOT/$f"
done < <(cd /tests/files && find . -type f | sed 's|^\\./||')'''


def main() -> int:
    tasks = os.path.join(REPO, "v2", "tasks")
    runs = os.path.join(REPO, "run_scripts")
    rebuilt = 0
    for iid in sorted(os.listdir(tasks)):
        if not any(t in iid for t in TARGETS):
            continue
        tdir = os.path.join(tasks, iid, "tests")
        rdir = os.path.join(runs, iid)
        official = open(os.path.join(rdir, "test.sh") if os.path.exists(os.path.join(rdir, "test.sh"))
                        else _official_test_sh(tasks), encoding="utf-8").read()
        if APPLY_BLOCK not in official:
            raise SystemExit(f"apply block not found in official test.sh for {iid}")
        patched = official.replace(APPLY_BLOCK, OVERLAY_BLOCK)
        # The overlay block needs ROOT; official test.sh cds but never sets it.
        patched = patched.replace(
            'cd /app 2>/dev/null || cd /testbed 2>/dev/null || { echo "ERROR: Neither /app nor /testbed exists"; exit 1; }',
            'cd /app 2>/dev/null || cd /testbed 2>/dev/null || { echo "ERROR: Neither /app nor /testbed exists"; exit 1; }\nROOT="$PWD"\n: > /tmp/no_test_patch.patch',
        )
        # Synthesized instances ship no test patch; point the leftover grep
        # probes at an empty file so they stay silent instead of erroring.
        patched = patched.replace("/tests/test_patch.patch", "/tmp/no_test_patch.patch")
        with open(os.path.join(tdir, "test.sh"), "w", encoding="utf-8", newline="\n") as f:
            f.write(patched)
        os.chmod(os.path.join(tdir, "test.sh"), 0o755)
        for name in ("run_script.sh", "parser.py"):
            shutil.copyfile(os.path.join(rdir, name), os.path.join(tdir, name))
        rebuilt += 1
        print(f"rebuilt {iid[:60]}")
    print(f"rebuilt {rebuilt} verifiers on the official chain")
    return 0


def _official_test_sh(tasks):
    """These dropped instances have no run_scripts/test.sh; borrow the generic
    official v2 test.sh from any instance that ships one (it is identical)."""
    for iid in sorted(os.listdir(tasks)):
        p = os.path.join(tasks, iid, "tests", "test.sh")
        if os.path.exists(p) and APPLY_BLOCK in open(p, encoding="utf-8").read():
            return p
    raise SystemExit("no official v2 test.sh found to borrow")


if __name__ == "__main__":
    raise SystemExit(main())