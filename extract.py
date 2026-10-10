"""Level 1: bill image -> schema JSON, with arithmetic self-checks and one repair pass."""
import json, re
from pathlib import Path
from llm import ask_json, image_data_url

SYSTEM = """You extract data from Pakistani electricity bills (K-Electric=KE, LESCO, IESCO).
The image may be tilted, a photo, or partly Urdu. Grey boxes hide personal data.
Return ONE JSON object with exactly these keys:
provider (KE|LESCO|IESCO), tariff (string exactly as printed), sanctioned_load_kw (number),
bill_month (YYYY-MM), reading_date, issue_date, due_date (YYYY-MM-DD),
previous_reading, current_reading, units_consumed (numbers),
charges: [{type, amount}], total_charges, taxes: [{type, amount}], total_taxes,
current_bill, arrears, payable_within_due_date, payable_after_due_date,
usage_history: [{month: YYYY-MM, units: number}]   (monthly units chart/table, oldest first, EXCLUDING the current month),
billing_history: [{month: YYYY-MM, billed: number, paid: number|null}]  (payment history table, if printed).
Rules:
- Copy values as printed; never recalculate, round or fix. Numbers only (no commas, no 'Rs').
- Credits (CR, minus) are negative. Subsidies/relief are negative.
- Use null for anything not printed, blank, covered by a grey box, or unreadable. Never guess. Use [] for empty lists.
- charges: one entry per line of the charges section, skipping blank or 0 lines; repeated types stay separate entries.
  type in energy|fixed|fpa|quarterly_adjustment|surcharge|meter_rent|subsidy|other.
  energy = cost of units / variable / energy charges; fpa covers FPA and FCA; quarterly_adjustment = QTA/uniform quarterly adjustment;
  surcharge e.g. Additional Surcharge (PHL), F.C surcharge.
  If the bill prints Total/Gross charges, then a Subsidy, then Net charges: list the GROSS as energy and the subsidy as a negative
  subsidy entry (so they add up to the net), and total_charges = the printed net/subtotal. A separate FPA line printed
  below the breakdown is still an fpa charge. total_charges = the printed charges subtotal, else null.
  Careful: a line like "Electricity Charges for current month" (charges + taxes) is current_bill, NOT total_charges.
- taxes: one entry per line of the tax/government section (classify by SECTION, not name).
  type in gst|electricity_duty|income_tax|municipal_tax|other_tax (municipal = e.g. KE MUCT/KMC; other_tax = extra/further tax, TV fee).
  If only a combined tax amount is printed: taxes=[] and total_taxes=that amount. total_taxes = printed subtotal, else null.
- current_bill = this month's bill amount. arrears = previous dues with printed sign (null if blank).
- payable_within_due_date = amount payable by the due date. payable_after_due_date = if several late amounts are shown, the HIGHEST.
- due_date = last date to pay without late surcharge.
- reading_date (meter read) and issue_date (bill issued) are different printed labels, usually a day or more apart; read each from its own label.
- Do NOT output names, addresses, CNIC, account/reference/consumer/meter numbers.
Reply with JSON only."""

HISTORY_SYSTEM = """Read ONLY the consumption/billing history of this electricity bill (a bar chart and/or a MONTH | UNITS | BILL | PAYMENT table).
History tables are often split into TWO side-by-side column groups: read BOTH groups, every row.
On crumpled or photographed bills the FIRST row of a group may be printed on or overlapping the header line (e.g. 'MONTH AUG 25 ... UNITS 721'); include it.
A 12-month table normally has 12 rows: if you find fewer, look again.
Return JSON: {"usage_history": [{"month": "YYYY-MM", "units": number}],
              "billing_history": [{"month": "YYYY-MM", "billed": number, "paid": number|null}]}
Oldest first. Numbers only, no commas. Use null for a blank payment. Do not invent rows. Reply with JSON only."""

L1_KEYS = ["provider", "tariff", "sanctioned_load_kw", "bill_month", "reading_date", "issue_date",
           "due_date", "previous_reading", "current_reading", "units_consumed", "charges",
           "total_charges", "taxes", "total_taxes", "current_bill", "arrears",
           "payable_within_due_date", "payable_after_due_date"]
CHARGE = {"energy", "fixed", "fpa", "quarterly_adjustment", "surcharge", "meter_rent", "subsidy", "other"}
TAX = {"gst", "electricity_duty", "income_tax", "municipal_tax", "other_tax"}
DATE, MONTH = re.compile(r"^\d{4}-\d{2}-\d{2}$"), re.compile(r"^\d{4}-\d{2}$")


def _num(v):
    """Coerce '1,234.5', '38209CR' etc. to a number; None stays None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace(",", "").replace("Rs.", "").replace("Rs", "").strip()
    neg = s.upper().endswith("CR") or s.startswith("-")
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    x = float(m.group())
    x = int(x) if x.is_integer() else x
    return -abs(x) if neg else x


def normalize(d: dict) -> dict:
    out = {k: d.get(k) for k in L1_KEYS}
    for k in L1_KEYS:
        if k not in ("provider", "tariff", "bill_month", "reading_date", "issue_date", "due_date",
                     "charges", "taxes"):
            out[k] = _num(out[k])
    p = str(out["provider"] or "").upper()
    out["provider"] = "KE" if p in ("KE", "KESC", "K-ELECTRIC") else p or None
    for key, allowed, fallback in (("charges", CHARGE, "other"), ("taxes", TAX, "other_tax")):
        rows = []
        for r in out[key] or []:
            amt = _num(r.get("amount"))
            if amt is None:
                continue
            t = r.get("type") if r.get("type") in allowed else fallback
            rows.append({"type": t, "amount": -abs(amt) if t == "subsidy" else amt})
        out[key] = rows
    return out


def problems(d: dict) -> list[str]:
    """Self-checks. Mismatches are hints for a re-read, never silently 'corrected'."""
    p = []
    if d["provider"] not in ("KE", "LESCO", "IESCO"):
        p.append("provider must be KE, LESCO or IESCO")
    for k in ("reading_date", "issue_date", "due_date"):
        if d[k] is not None and not DATE.match(str(d[k])):
            p.append(f"{k} must be YYYY-MM-DD")
    if d["bill_month"] is not None and not MONTH.match(str(d["bill_month"])):
        p.append("bill_month must be YYYY-MM")
    if d["reading_date"] and d["reading_date"] == d["issue_date"]:
        p.append("reading_date equals issue_date; confirm each against its own label (they usually differ)")
    a, b, u = d["previous_reading"], d["current_reading"], d["units_consumed"]
    if None not in (a, b, u) and abs((b - a) - u) > 1:
        p.append(f"current_reading-previous_reading={b - a} but units_consumed={u}; re-read these three")
    for rows, tot, name in ((d["charges"], d["total_charges"], "charges"), (d["taxes"], d["total_taxes"], "taxes")):
        s = sum(r["amount"] for r in rows)
        fpa = sum(r["amount"] for r in rows if r["type"] == "fpa")
        if rows and tot is not None and abs(s - tot) > 2 and abs(s - fpa - tot) > 2:  # FPA may sit outside the printed subtotal
            p.append(f"sum of {name} lines is {s:.2f} but printed total is {tot}; check for missed or misread lines")
    if None not in (d["total_charges"], d["total_taxes"], d["current_bill"]):
        if abs(d["total_charges"] + d["total_taxes"] - d["current_bill"]) > 2:
            p.append("total_charges + total_taxes does not match current_bill; check which printed figures these are")
    w, l = d["payable_within_due_date"], d["payable_after_due_date"]
    if None not in (w, l) and l < w:
        p.append("payable_after_due_date is lower than payable_within_due_date; use the highest late amount")
    return p


def extract_bill(path: Path) -> tuple[dict, dict]:
    """Returns (level1_json, history) where history feeds the Level 2 answers."""
    img = image_data_url(path)
    raw = ask_json(SYSTEM, "Extract this bill.", img)
    d = normalize(raw)
    best, best_raw = d, raw
    for _ in range(2):  # targeted re-reads; keep whichever attempt has the fewest problems
        issues = problems(best)
        if not issues:
            break
        note = ("Your previous answer:\n" + json.dumps(best_raw) + "\nProblems found:\n- " + "\n- ".join(issues) +
                "\nRe-read the image carefully. Fix only genuine misreads; "
                "if the bill itself is inconsistent, keep the printed values.")
        try:
            raw2 = ask_json(SYSTEM, note, img)
        except Exception as e:
            print(f"  repair failed for {path.name}: {e}")
            break
        d2 = normalize(raw2)
        if len(problems(d2)) < len(issues):
            best, best_raw = d2, raw2
    d, raw = best, best_raw
    if len(raw.get("usage_history") or []) < 12:  # history tables often span two columns; re-read just that part
        try:
            h2 = ask_json(HISTORY_SYSTEM, "Read the bill's history.", img)
            for k in ("usage_history", "billing_history"):
                if len(h2.get(k) or []) > len(raw.get(k) or []):
                    raw[k] = h2[k]
        except Exception as e:
            print(f"  history re-read failed for {path.name}: {e}")
    # some charts (e.g. KE's 13-month chart) end with the current month; history must hold previous months only
    usage = [h for h in raw.get("usage_history") or [] if isinstance(h, dict) and h.get("month") != d["bill_month"]]
    hist = {"usage_history": usage, "billing_history": raw.get("billing_history") or []}
    return d, hist
