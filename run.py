"""Usage: python run.py --bills test/bills --csv test/csv --out output"""
import argparse, csv, json, sys, time
from pathlib import Path
from extract import extract_bill
from llm import USAGE, cost_usd, image_data_url
from qa import answer_questions, unverified


def find_bill(folder: Path, bill_id: str) -> Path | None:
    return next((p for p in folder.glob(bill_id + ".*") if p.suffix.lower() in (".png", ".jpg", ".jpeg")), None)


def read_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_rows(path: Path, fields: list[str], rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bills", required=True, type=Path, help="folder of bill images")
    ap.add_argument("--csv", type=Path, help="folder with level1.csv and level2.csv templates")
    ap.add_argument("--out", type=Path, default=Path("output"))
    a = ap.parse_args()
    start = time.time()

    if a.csv:
        l1_rows, l2_rows = read_rows(a.csv / "level1.csv"), read_rows(a.csv / "level2.csv")
    else:  # no templates: process every image and skip Level 2 questions
        ids = sorted(p.stem for p in a.bills.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        l1_rows, l2_rows = [{"bill_id": i, "json": ""} for i in ids], []

    results = {}
    for row in l1_rows:
        bid = row["bill_id"]
        path = find_bill(a.bills, bid)
        print(f"[L1] {bid}", flush=True)
        try:
            if path is None:
                raise FileNotFoundError(f"no image for {bid}")
            results[bid] = extract_bill(path)
            row["json"] = json.dumps(results[bid][0], separators=(",", ":"), ensure_ascii=False)
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            row["json"] = ""  # surfaced in the final report instead of crashing the run
    write_rows(a.out / "level1.csv", ["bill_id", "json"], l1_rows)

    for bid in dict.fromkeys(r["bill_id"] for r in l2_rows):
        qs = [(r["question_id"], r["question"]) for r in l2_rows if r["bill_id"] == bid]
        print(f"[L2] {bid} ({len(qs)} questions)", flush=True)
        answers = {}
        try:
            l1, hist = results[bid]
            answers = answer_questions(l1, hist, qs, image_data_url(find_bill(a.bills, bid)))
            for qid, nums in unverified(answers, l1, hist).items():
                print(f"  check {bid} {qid}: numbers not found in bill data (estimate or error?): {nums}")
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
        for r in l2_rows:
            if r["bill_id"] == bid:
                r["answer"] = answers.get(r["question_id"], "")
    if l2_rows:
        write_rows(a.out / "level2.csv", ["bill_id", "question_id", "question", "answer"], l2_rows)

    empty = sum(not r["json"] for r in l1_rows) + sum(not r["answer"] for r in l2_rows)
    print(f"Model calls: {USAGE['calls']} new, {USAGE['cached']} cached | tokens in/out: {USAGE['input']}/{USAGE['output']}"
          f" | cost ~${cost_usd():.3f} | {time.time() - start:.0f}s")
    print(f"Done. Wrote {a.out}/. Empty cells: {empty}" + ("  <-- rerun to retry failures (successes are cached)" if empty else ""))


if __name__ == "__main__":
    main()
