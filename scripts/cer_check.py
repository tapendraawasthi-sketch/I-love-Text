"""
Character Error Rate (CER) helper for golden-file accuracy checks.

Usage:
  python scripts/cer_check.py reference.txt hypothesis.txt
  python scripts/cer_check.py --dir tests/golden
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cer(hypothesis: str, reference: str) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    a, b = hypothesis, reference
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1] / len(b)


def main() -> int:
    parser = argparse.ArgumentParser(description="CER check for TextExtract golden files")
    parser.add_argument("reference", nargs="?", help="Reference .txt path")
    parser.add_argument("hypothesis", nargs="?", help="Hypothesis .txt path")
    parser.add_argument("--dir", help="Directory with pairs: name.gold.txt + name.out.txt")
    parser.add_argument("--max-cer", type=float, default=0.02, help="Fail if CER exceeds this")
    args = parser.parse_args()

    failures = 0

    if args.dir:
        root = Path(args.dir)
        for gold in sorted(root.glob("*.gold.txt")):
            out = gold.with_name(gold.name.replace(".gold.txt", ".out.txt"))
            if not out.exists():
                print(f"MISSING {out.name}")
                failures += 1
                continue
            score = cer(out.read_text(encoding="utf-8"), gold.read_text(encoding="utf-8"))
            status = "PASS" if score <= args.max_cer else "FAIL"
            if status == "FAIL":
                failures += 1
            print(f"{status}  CER={score:.4f}  {gold.stem}")
    else:
        if not args.reference or not args.hypothesis:
            parser.print_help()
            return 2
        score = cer(
            Path(args.hypothesis).read_text(encoding="utf-8"),
            Path(args.reference).read_text(encoding="utf-8"),
        )
        print(f"CER={score:.6f}")
        if score > args.max_cer:
            failures = 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
