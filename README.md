# Utility Bill Decoder

Reads a K-Electric, LESCO or IESCO electricity bill image, extracts its details as JSON (Level 1) and answers customer questions about it in plain English (Level 2).

**Repository:** https://github.com/HiraAhmed/womentechquest-2026

**Runtime:** Python 3.13 (3.11+ works) · **Model:** OpenAI `gpt-5-mini` · **Dependencies:** `requirements.txt` (pinned)

## Setup
```
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
```
Put your OpenAI key in `.env` as `API_KEY=sk-...`. `MODEL_PROVIDER` and `MODEL_NAME` are already set.

## Run
```
python run.py --bills <bills folder> --csv <templates folder> --out output
```
Writes `output/level1.csv` and `output/level2.csv` from the `level1.csv` / `level2.csv` templates, keeping their rows and order. Without `--csv`, only Level 1 runs for every image in the folder.

The run ends with a summary: model calls, tokens, cost, time and empty cells. One failing bill never stops the run. Model responses are cached in `.cache/`, so rerunning the same command retries only what failed.

**Live demo UI:** `python app.py` opens http://localhost:8000. Pick or drop a bill, watch it being read, see the extracted fields and usage chart, click **Explain this bill**, and ask questions.

**Tests:**
- `python -m unittest discover -s tests` runs offline tests of the parsing, self-checks and Level 2 calculations (no API calls).
- `python eval.py` scores Level 1 against the guide's published answer for KESC_0008 (currently 18/18 fields).

## How it works
```
bill image ─► gpt-5-mini (vision) ─► normalise ─► self-checks ──fail──► targeted re-read (≤2) ─► Level 1 JSON
                    │                                                                              │
                    └─► history tables (re-read if incomplete)                                      ▼
                                       └──────────► Python calculations ─► gpt-5-mini ─► Level 2 answers ─► number check
```

**Level 1 (`extract.py`)**
1. **Read:** the image (rotation fixed, size capped) goes straight to the vision model with the guide's extraction rules. There is no separate OCR step, because the model reads tilted photos, Urdu labels and tables directly.
2. **Normalise:** code turns the values into clean numbers and allowed types:
   - `38209CR` becomes `-38209`, and `Rs. 1,234` becomes `1234`.
   - Unknown charge or tax types become `other` / `other_tax`, and subsidies are made negative.
   - `KESC` becomes `KE`.
3. **Self-check:** code verifies that the numbers agree with each other:
   - current reading − previous reading = units;
   - the charge and tax lines add up to the printed subtotals;
   - charges + taxes = current bill;
   - the after-due amount is not below the within-due amount;
   - dates are valid, and the reading date differs from the issue date.
4. **Repair:** a failed check is sent back to the model with the specific doubt ("lines sum to X but the printed total is Y") for a re-read, up to twice. The best attempt is kept. Code never "fixes" printed values: if the bill itself is inconsistent, it stays as printed.

**Level 2 (`qa.py`)**
1. **Calculate in Python:** every number a question might need is computed in code, never by the model:
   - tax share, and averages with and without the current month;
   - highest month, same month last year, summer against winter, and the 3-month direction;
   - next-month estimates (12-month average, 3-month average, linear trend);
   - unpaid months, average bill for spreading costs evenly, the late-payment extra, and the bill at last month's units.
2. **Write the answers:** the model gets the bill image, the Level 1 JSON, the history and these figures, and writes all of a bill's answers in one call. Its rules:
   - use only the bill, and say so when something isn't shown;
   - label estimates and give their method, leading with one number;
   - state the interpretation used for ambiguous questions;
   - present our calculations as calculations, never as something printed on the bill;
   - English only, with no personal identifiers.
3. **Number check:** any answer containing a figure found nowhere in the bill data or calculations is flagged in the terminal for review. It never edits answers. Estimates are expected to be flagged.

## AI design decisions
- **The model reads; code calculates and checks.** The vision model is strong at reading messy bills but unreliable at arithmetic, so it never does maths whose result we score. Every total, average, percentage and forecast is computed in Python.
- **Self-checks catch real misreads.** On training bill IESCO_0004, the first extraction recorded the net charges (2,770) as the energy line. The "lines must sum to the subtotal" check flagged it, and the targeted re-read returned the correct gross charge (6,102) with the subsidy as a negative line. On test bill IESCO_0002, a crumpled photo, the history re-read recovered 5 missed months of usage.
- **One model, chosen by test.** `gpt-4.1-mini` was compared on the training bills. It misread units on KESC_0004 (760.44 against 901), took the whole bill as the tax total on LESCO_0005, and read a sanctioned load of 14 on LESCO_0006. `gpt-5-mini` was more accurate, so it is used for everything.
- **Cost:** measured at about **$0.023 and 65 s per bill** for extraction plus all 12 answers, about $0.23 for the 10-bill test set. That is 2–3 model calls per bill: one read, a re-read only when a check fails, and one call for all of a bill's answers.

## Limitations
- Only KESC_0008 has a published answer. The other bills were checked through self-checks and by comparing against the images.
- On meters with several registers (for example KESC_0004), there is no single previous/current reading pair, so those fields are `null` and not guessed.
- History rows printed over a table header on a crumpled photo can be missed (2 of 12 months on IESCO_0002). Answers then say which months they used.
- Model output can vary between runs. The self-checks catch most errors, but not one that is internally consistent.

## Files
| File | Purpose |
|---|---|
| `run.py` | CLI that generates both CSVs |
| `extract.py` | Level 1: prompt, normalisation, self-checks, repair, history re-read |
| `qa.py` | Level 2: calculations, answers, number check |
| `llm.py` | Model call, image preprocessing, cache, cost tracking, allowed-model guard |
| `app.py`, `ui.html` | Live demo UI (standard library only) |
| `eval.py`, `tests/` | Accuracy check against the guide's example, and offline unit tests |
| `make_submission.py` | Builds `submission/` and `submission.zip`, checking results, secrets and size |

## AI usage
- **Models and APIs:** OpenAI `gpt-5-mini` through the Chat Completions API (image input, JSON mode), used for Level 1, Level 2 and the demo UI. `gpt-4.1-mini` is accepted by `MODEL_NAME` and was used only in the one-off comparison above. No submitted result comes from it.
- **OCR and extraction services:** none.
- **Other services:** none.
- **AI coding tools:** Claude Code (Anthropic).
