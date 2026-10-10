"""Build submission/ and submission.zip in the structure the guide requires: python make_submission.py
Put an optional demo video in demo/ (demo.mp4, .mov or .webm) before running."""
import csv, json, re, shutil, sys, zipfile
from pathlib import Path

OUT = Path("submission")
SOURCE = ["run.py", "extract.py", "qa.py", "llm.py", "app.py", "ui.html", "eval.py", "make_submission.py",
          "README.md", ".env.example", "requirements.txt", "tests/test_logic.py", "tests/kesc_0008_expected.json",
          "data/train/bills/KESC_0008.png"]  # the one bill eval.py needs
RESULTS = ["output/level1.csv", "output/level2.csv"]
MAX_MB = 15


def fail(msg):
    sys.exit(f"NOT BUILT: {msg}")


def check_results():
    l1 = list(csv.DictReader(open(RESULTS[0], encoding="utf-8")))
    l2 = list(csv.DictReader(open(RESULTS[1], encoding="utf-8")))
    if not l1 or any(not r["json"] for r in l1):
        fail("level1.csv has empty json cells")
    if not l2 or any(not r["answer"] for r in l2):
        fail("level2.csv has empty answer cells")
    for r in l1:
        json.loads(r["json"])  # must be valid one-line JSON
    print(f"results ok: {len(l1)} Level 1 rows, {len(l2)} Level 2 rows")


def build():
    for f in SOURCE + RESULTS:
        if not Path(f).is_file():
            fail(f"missing {f}")
    check_results()
    if OUT.exists():
        shutil.rmtree(OUT)
    for f in RESULTS:
        dst = OUT / "output" / Path(f).name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
    for f in SOURCE:
        dst = OUT / "source" / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
    for video in Path("demo").glob("demo.*") if Path("demo").is_dir() else []:
        if video.suffix.lower() in (".mp4", ".mov", ".webm"):
            (OUT / "demo").mkdir(exist_ok=True)
            shutil.copy2(video, OUT / "demo" / video.name)

    # secrets guard: no .env, no OpenAI-style keys anywhere in the package
    for p in OUT.rglob("*"):
        if p.name == ".env":
            fail(f"{p} must not be submitted")
        if p.is_file() and p.suffix in (".py", ".md", ".txt", ".csv", ".json", ".html", ".example"):
            if re.search(r"sk-[A-Za-z0-9_-]{20,}", p.read_text(encoding="utf-8", errors="ignore")):
                fail(f"{p} contains what looks like an API key")

    zip_path = Path("submission.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(OUT))
    mb = zip_path.stat().st_size / 1e6
    if mb > MAX_MB:
        fail(f"submission.zip is {mb:.1f} MB (limit {MAX_MB} MB)")
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    print(f"built {zip_path} ({mb:.2f} MB, {len(names)} files)" + ("" if (OUT / "demo").exists() else " - no demo video included"))
    for n in names:
        print("  " + n)


if __name__ == "__main__":
    build()
