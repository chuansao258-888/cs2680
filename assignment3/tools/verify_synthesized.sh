#!/bin/bash
# Verify the synthesized V2 verifiers for the three recovered A2 instances.
C=/home/guany/.cache/cs2680_a3/SWE-bench_Pro-os/v2/tasks
for id in \
  instance_ansible__ansible-a26c325bd8f6e2822d9d7e62f77a424c1db4fbf6-v0f01c69f1e2528b935359cfe578530722bca2c59 \
  instance_qutebrowser__qutebrowser-21b426b6a20ec1cc5ecad770730641750699757b-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d \
  instance_ansible__ansible-83909bfa22573777e3db5688773bda59721962ad-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5
do
  T="$C/$id/tests"
  echo "== ${id:10:30}"
  bash -n "$T/test.sh" && echo "   bash -n: OK"
  file "$T/test.sh" | sed 's/.*: //'
  echo "   overlay files: $(find "$T/files" -type f | wc -l), total bytes: $(find "$T/files" -type f -printf '%s\n' | awk '{s+=$1} END {print s}')"
  echo "   f2p=$(grep -c . "$T/f2p.txt") p2p=$(grep -c . "$T/p2p.txt")"
done
echo "== sample downloaded test file =="
head -4 "$C/instance_qutebrowser__qutebrowser-21b426b6a20ec1cc5ecad770730641750699757b-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d/tests/files/tests/unit/config/test_configutils.py"
