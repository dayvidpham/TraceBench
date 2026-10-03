#!/bin/bash
# Offline verifier: pytest is baked into the image, no downloads here
# (verifier phase runs with no-network).
set -u

python3 -m pytest /tests/test_outputs.py -v
if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
