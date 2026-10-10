"""Level 2: numbers are computed in Python; the model only reads the bill and writes the words."""
import json, re
from statistics import mean
from llm import ask_json

SYSTEM = """You are a friendly assistant explaining an electricity bill to a customer in plain English.
Rules:
- Use ONLY the bill image, the extracted JSON and the precomputed facts. No outside tariff or rate knowledge.
- Prefer the precomputed facts for any number (averages, percentages, counts); never redo risky arithmetic yourself.
- If the bill does not show something needed, say so plainly instead of guessing.
- Label estimates as estimates and say how you calculated them (e.g. "Estimate: ...").
- If a question is ambiguous, state the meaning you used or answer both readings
  (e.g. history counts with and without the current month).
- Be specific: quote PKR amounts, units and months. Keep each answer to 2-5 sentences.
- When asked how you can tell something (e.g. net-metered vs standard), name the printed evidence (labels, lines, readings).
- Averages, totals, differences, trends and forecasts are YOUR calculations from the bill's figures. Say "calculated from the bill's usage history",
  never that the bill itself shows, prints or estimates them, and never use the words 'precomputed' or 'method printed'. Only quote the bill as the source for values it literally prints.
- For forecast or estimate questions, lead with ONE headline number, then give the method and, if useful, a short range.
- Speak to the customer. Never mention 'precomputed', 'facts', JSON, field or variable names; just state the numbers and where they come from on the bill.
- Write in English only. Never mention names, addresses, CNIC, account, consumer or meter numbers.
Return JSON: {"answers": {"Q1": "...", ...}} with one entry per question id given."""


def _pct(a, b):
    return round(100 * a / b, 2) if a is not None and b else None


def facts(l1: dict, hist: dict) -> dict:
    use = [h for h in hist["usage_history"] if isinstance(h.get("units"), (int, float))]
    units = [h["units"] for h in use]
    cur = l1["units_consumed"]
    f = {"history_months_found": len(use)}
    if units:
        avg = mean(units)
        hi, lo = max(use, key=lambda h: h["units"]), min(use, key=lambda h: h["units"])
        f.update(
            previous_months_avg_units=round(avg, 2),
            avg_including_current_month=round(mean(units + [cur]), 2) if cur is not None else None,
            highest_previous_month=hi, lowest_previous_month=lo,
            last_month=use[-1],
            current_vs_previous_avg_pct=_pct(cur - avg, avg) if cur is not None else None,
            current_vs_last_month_units=cur - use[-1]["units"] if cur is not None else None,
            months_above_previous_avg=[h["month"] for h in use if h["units"] > avg],
            usage_sorted_desc=sorted(use, key=lambda h: -h["units"]),
        )
    if l1["current_bill"]:
        f["tax_share_of_bill_pct"] = _pct(l1["total_taxes"], l1["current_bill"])
        f["charges_share_of_bill_pct"] = _pct(l1["total_charges"], l1["current_bill"])
        if cur:
            f["avg_cost_per_unit_all_in"] = round(l1["current_bill"] / cur, 2)
    if None not in (l1["payable_after_due_date"], l1["payable_within_due_date"]):
        f["late_payment_extra"] = round(l1["payable_after_due_date"] - l1["payable_within_due_date"], 2)
    for key, lines in (("charges_by_type", l1["charges"]), ("taxes_by_type", l1["taxes"])):
        agg = {}
        for r in lines:
            agg[r["type"]] = round(agg.get(r["type"], 0) + r["amount"], 2)
        f[key] = agg
    bh = [h for h in hist["billing_history"] if isinstance(h.get("billed"), (int, float))]
    if bh:
        f["billing_history_avg_billed"] = round(mean(h["billed"] for h in bh), 2)
    f.update(_seasonal_and_trend(l1, use))
    f.update(_payments_and_estimates(l1, use, bh))
    return f


def _month(m) -> tuple[int, int] | None:
    try:
        return int(str(m)[:4]), int(str(m)[5:7])
    except ValueError:
        return None


def _seasonal_and_trend(l1: dict, use: list[dict]) -> dict:
    """Same-month-last-year, summer vs winter, last 3 months and next-month estimates, from history + this month."""
    series = {h["month"]: h["units"] for h in use if _month(h.get("month"))}
    if l1["units_consumed"] is not None and _month(l1["bill_month"]):
        series[l1["bill_month"]] = l1["units_consumed"]
    ordered = sorted(series.items())
    if not ordered:
        return {}
    f = {"units_by_month_incl_current": ordered}
    ym = _month(l1["bill_month"])
    if ym:
        last_year = f"{ym[0] - 1}-{ym[1]:02d}"
        f["same_month_last_year"] = {"month": last_year, "units": series.get(last_year)}
        if series.get(last_year) is not None and l1["units_consumed"] is not None:
            f["same_month_last_year"]["difference_now_minus_then"] = l1["units_consumed"] - series[last_year]
    summer = [(m, u) for m, u in ordered if _month(m)[1] in (6, 7, 8)]
    winter = [(m, u) for m, u in ordered if _month(m)[1] in (12, 1, 2)]
    if summer and winter:
        f["summer_vs_winter"] = {
            "summer_months": summer, "winter_months": winter,
            "summer_avg": round(mean(u for _, u in summer), 2), "winter_avg": round(mean(u for _, u in winter), 2),
            "avg_difference": round(mean(u for _, u in summer) - mean(u for _, u in winter), 2),
            "summer_total": sum(u for _, u in summer), "winter_total": sum(u for _, u in winter),
            "total_difference": sum(u for _, u in summer) - sum(u for _, u in winter),
            "note": "only months present in the bill's history are used"}
    last3 = ordered[-3:]
    u3 = [u for _, u in last3]
    f["last_3_months"] = {"months": last3, "direction": "up" if u3 == sorted(set(u3)) else
                          "down" if u3 == sorted(set(u3), reverse=True) else "mixed"}
    vals = [u for _, u in ordered[-12:]]
    est = {"avg_of_last_12_months_shown": round(mean(vals), 1), "avg_of_last_3_months": round(mean(u3), 1)}
    if len(vals) >= 3:  # least-squares line through the months, extended one step
        n, mx, my = len(vals), (len(vals) - 1) / 2, mean(vals)
        slope = sum((i - mx) * (v - my) for i, v in enumerate(vals)) / sum((i - mx) ** 2 for i in range(n))
        est["linear_trend_next_month"] = round(max(0, my + slope * (n - mx)), 1)
    f["next_month_estimates"] = est
    return f


def _payments_and_estimates(l1: dict, use: list[dict], bh: list[dict]) -> dict:
    f = {}
    if bh:
        f["payments"] = {
            "months_listed": len(bh),
            "no_payment_recorded": [h["month"] for h in bh if not h.get("paid")],
            "paid_less_than_billed": [h["month"] for h in bh if h.get("paid") and h["paid"] < h["billed"] - 1],
            "avg_billed": round(mean(h["billed"] for h in bh), 2), "total_billed": round(sum(h["billed"] for h in bh), 2),
            "note": "a missing or zero payment may mean unpaid or simply not printed"}
        if l1["current_bill"] is not None:
            f["payments"]["avg_billed_incl_current_bill"] = round(mean([h["billed"] for h in bh] + [l1["current_bill"]]), 2)
    last = use[-1]["units"] if use else None
    cur, bill = l1["units_consumed"], l1["current_bill"]
    if last is not None and cur and bill is not None:
        fixed = sum(r["amount"] for r in l1["charges"] if r["type"] == "fixed")
        f["bill_estimate_at_last_month_units"] = {
            "last_month_units": last,
            "using_average_all_in_rate": round(bill / cur * last),
            "keeping_fixed_charges_constant": round(fixed + (bill - fixed) / cur * last),
            "note": "your own estimates from this bill's average rates (not printed on the bill); real tariff slabs may differ"}
    return f


# typographic characters the model likes, mapped to plain ASCII so CSVs read cleanly in any tool
ASCII = str.maketrans({"\u2011": '-', "\u2010": '-', "\u2013": '-', "\u2014": ' - ', "\u2212": '-', "\u2018": "'", "\u2019": "'",
                       "\u201c": '"', "\u201d": '"', "\u2192": '->', "\u2248": '~', "\u00a0": ' ', "\u2026": '...', "\u00d7": 'x'})


def answer_questions(l1: dict, hist: dict, questions: list[tuple[str, str]], image: str) -> dict[str, str]:
    """questions: [(question_id, text)]. One call per bill keeps answers consistent and cheap."""
    user = ("Extracted bill JSON:\n" + json.dumps(l1) +
            "\n\nBill history read from the image:\n" + json.dumps(hist) +
            "\n\nPrecomputed facts (computed in code, trust these):\n" + json.dumps(facts(l1, hist)) +
            "\n\nQuestions:\n" + "\n".join(f"{qid}: {q}" for qid, q in questions))
    def run(extra: str = "") -> dict[str, str]:
        out = ask_json(SYSTEM, user + extra, image).get("answers", {})
        if isinstance(out, list):  # tolerate a list of answers in question order
            out = {qid: v for (qid, _), v in zip(questions, out)}
        if len(out) == len(questions) and any(qid not in out for qid, _ in questions):
            out = {qid: v for (qid, _), v in zip(questions, out.values())}  # keys renamed: match by order
        return {qid: " ".join(str(out.get(qid) or "").translate(ASCII).split()) for qid, _ in questions}

    answers = run()
    if not all(answers.values()):  # one retry; a different prompt also bypasses the cache of the bad reply
        retry = run("\n\nAnswer EVERY question id listed above, with non-empty answers.")
        answers = {qid: answers[qid] or retry[qid] for qid, _ in questions}
    return answers


def _numbers(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in re.findall(r"\d[\d,]*\.?\d*", text) if m.replace(",", "").replace(".", "", 1).isdigit()]


def unverified(answers: dict[str, str], l1: dict, hist: dict) -> dict[str, list[float]]:
    """Advisory check: numbers in an answer that appear nowhere in the bill data or computed facts.
    Estimates legitimately create new numbers, so this only flags answers for a human look."""
    known = _numbers(json.dumps([l1, hist, facts(l1, hist)]))
    out = {}
    for qid, text in answers.items():
        odd = [x for x in _numbers(text)
               if x > 31 and not 1990 <= x <= 2100  # ignore days and years
               and not any(abs(x - k) <= max(1.0, abs(k) * 0.005) for k in known)]
        if odd:
            out[qid] = odd
    return out
