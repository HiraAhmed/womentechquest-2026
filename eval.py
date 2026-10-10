"""Score Level 1 against the guide's published answer for KESC_0008, using the guide's rules
(text and dates exact, numbers within +-1): python eval.py"""
import json
from pathlib import Path
from extract import extract_bill

expected = json.loads(Path("tests/kesc_0008_expected.json").read_text())
got, _ = extract_bill(Path("data/train/bills/KESC_0008.png"))


def same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(x["type"] == y["type"] and same(x["amount"], y["amount"]) for x, y in zip(a, b))
    return a == b


wrong = [k for k in expected if not same(expected[k], got.get(k))]
for k in wrong:
    print(f"  {k}: expected {expected[k]}, got {got.get(k)}")
print(f"KESC_0008: {len(expected) - len(wrong)}/{len(expected)} fields correct")
