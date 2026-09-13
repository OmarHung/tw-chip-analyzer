"""測試用假腳本：印出參數與數行輸出，依 --exit / --sleep 決定結束碼與耗時。"""
from __future__ import annotations

import argparse
import sys
import time


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--exit", type=int, default=0)
    p.add_argument("--sleep", type=float, default=0.0)
    p.add_argument("--label", default="")
    args = p.parse_args()
    print(f"argv={sys.argv[1:]}", flush=True)
    for i in range(3):
        print(f"line {i} {args.label}", flush=True)
    time.sleep(args.sleep)
    sys.exit(args.exit)


if __name__ == "__main__":
    main()
