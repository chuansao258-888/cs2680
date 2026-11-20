#!/usr/bin/env python3
"""Rebuild V2-style verifiers for A2 instances dropped from the dataset snapshot.

The dropped instances still have their grading spec in assignment2's
task_test.json, and their official post-PR test files live in the public
upstream repos at the commit encoded in the instance id (the same commit
`before_repo_set_cmd` checks out). This script downloads those files and
synthesizes a v2/tasks/<id>/tests/ verifier directory so evaluate_one.sh
can grade them like any other task.
"""

import ast
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
A2_DIR = os.path.join(HERE, "..", "..", "assignment2", "evaluation_scripts")
A3_JSON = os.path.join(HERE, "..", "starter_code", "evaluation_scripts", "agent_task_input.json")
CACHE = os.environ.get("A3_CACHE", os.path.expanduser("~/.cache/cs2680_a3"))
TARGETS = ("a26c325", "83909bfa", "21b426b")


def as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return ast.literal_eval(value)
    return []


def parse_test_checkout(command: str):
    """Return (test_commit, [files]) from the `git checkout <sha> -- <files>` line."""
    for line in command.splitlines():
        line = line.strip()
        if line.startswith("git checkout ") and " -- " in line:
            head, _, tail = line.partition(" -- ")
            return head.split()[2], tail.split()
    return None, []


def main() -> int:
    a2_tasks = json.load(open(os.path.join(A2_DIR, "agent_task_input.json"), encoding="utf-8"))
    a2_tests = json.load(open(os.path.join(A2_DIR, "task_test.json"), encoding="utf-8"))
    a3 = json.load(open(A3_JSON, encoding="utf-8"))

    added = 0
    for iid, entry in a2_tasks.items():
        if not any(t in iid for t in TARGETS) or iid in a3:
            continue
        spec = a2_tests[iid]
        test_commit, files = parse_test_checkout(spec["before_repo_set_cmd"])
        if not test_commit or not files:
            raise SystemExit(f"cannot parse checkout line for {iid}")
        tdir = os.path.join(CACHE, "SWE-bench_Pro-os", "v2", "tasks", iid, "tests")
        os.makedirs(os.path.join(tdir, "files"), exist_ok=True)
        for rel in files:
            url = f"https://raw.githubusercontent.com/{entry['repo']}/{test_commit}/{rel}"
            dest = os.path.join(tdir, "files", rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as out:
                out.write(resp.read())
        f2p = as_list(spec["fail_to_pass"])
        p2p = as_list(spec["pass_to_pass"])
        with open(os.path.join(tdir, "f2p.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(f2p) + ("\n" if f2p else ""))
        with open(os.path.join(tdir, "p2p.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(p2p) + ("\n" if p2p else ""))
        json.dump({
            "instance_id": iid,
            "test_commit": test_commit,
            "overlay_files": files,
            "fail_to_pass": f2p,
            "pass_to_pass": p2p,
            "selected_test_files_to_run": as_list(spec.get("selected_test_files_to_run")),
            "source": "synthesized from assignment2 task_test.json + upstream git history",
        }, open(os.path.join(tdir, "spec.json"), "w", encoding="utf-8"), indent=1)
        # run_all.py's total_tests() reads this official-v2 file to count tests.
        json.dump({
            "fail_to_pass": f2p,
            "pass_to_pass": p2p,
            "selected_test_files_to_run": as_list(spec.get("selected_test_files_to_run")),
        }, open(os.path.join(tdir, "config.json"), "w", encoding="utf-8"), indent=1)

        a3[iid] = {
            "instance_id": iid,
            "repo": entry["repo"],
            "base_commit": entry["base_commit"],
            "docker_image": entry["docker_image"],
            "problem_statement": entry["problem_statement"],
            "requirements": entry.get("requirements", ""),
            "interface": entry.get("interface", ""),
        }
        added += 1
        print(f"synthesized {iid[:56]}  files={len(files)} f2p={len(f2p)} p2p={len(p2p)}")

    json.dump(a3, open(A3_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"added {added}; local task set now {len(a3)}")
    if added:
        # test.sh must come from the official v2 chain (run_script.sh sets the
        # right import path per repo); a bare pytest run misgrades ansible.
        import rebuild_official_verifiers
        rebuild_official_verifiers.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
