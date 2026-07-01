import argparse
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--message", required=True)
parser.add_argument("--sleep", type=int, default=5)
args = parser.parse_args()

print(f"[DEMO] start message={args.message}")
for i in range(args.sleep):
    print(f"[DEMO] running {i + 1}/{args.sleep}")
    sys.stdout.flush()
    time.sleep(1)

print(f"[DEMO] done message={args.message}")
