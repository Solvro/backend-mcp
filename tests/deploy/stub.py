"""Stand-in for docker/git/curl/flock/sleep in the tests of the scripts under deploy/.

Invoked as `stub.py <name> <args...>` by the wrappers deploy_harness.py puts on PATH. Every call
is appended to $STUB_LOG as one JSON line. The first rule in $STUB_RULES whose `cmd` equals the
name and whose `match` regex is found in the space-joined arguments decides stdout and the exit
status (a rule with `times` stops matching after that many uses). No rule: exit 0, no output.
"""

import json
import os
import re
import sys
from pathlib import Path

name, args = sys.argv[1], sys.argv[2:]
joined = " ".join(args)

with Path(os.environ["STUB_LOG"]).open("a") as log:
    record = {"cmd": name, "args": args, "release_tag": os.environ.get("RELEASE_TAG")}
    log.write(json.dumps(record) + "\n")

rules_path = Path(os.environ["STUB_RULES"])
counts_path = rules_path.with_name("rule-counts.json")
rules = json.loads(rules_path.read_text())
counts = json.loads(counts_path.read_text()) if counts_path.exists() else {}

for index, rule in enumerate(rules):
    if rule["cmd"] != name or not re.search(rule.get("match", ""), joined):
        continue
    used = counts.get(str(index), 0)
    if "times" in rule and used >= rule["times"]:
        continue
    counts[str(index)] = used + 1
    counts_path.write_text(json.dumps(counts))
    sys.stdout.write(rule.get("stdout", ""))
    sys.exit(rule.get("exit", 0))

sys.exit(0)
