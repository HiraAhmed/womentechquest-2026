"""Offline tests for the deterministic parts (no API calls): python -m unittest"""
import json, os, unittest
from pathlib import Path

os.environ.setdefault("API_KEY", "test")  # llm.py builds a client at import; no request is made
from extract import _num, normalize, problems
from qa import facts, unverified

GOOD = json.loads((Path(__file__).parent / "kesc_0008_expected.json").read_text())


class Numbers(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(_num("Rs. 19,964"), 19964)
        self.assertEqual(_num("38209CR"), -38209)
        self.assertEqual(_num("-0.53"), -0.53)
        self.assertIsNone(_num("—"))
        self.assertIsNone(_num(None))


class Normalize(unittest.TestCase):
    def test_types_provider_and_subsidy(self):
        d = normalize({"provider": "KESC", "charges": [{"type": "subsidy", "amount": "3,332"}, {"type": "weird", "amount": 5},
                                                       {"type": "fpa", "amount": None}],
                       "taxes": [{"type": "tv_fee", "amount": "35"}]})
        self.assertEqual(d["provider"], "KE")
        self.assertEqual(d["charges"], [{"type": "subsidy", "amount": -3332}, {"type": "other", "amount": 5}])
        self.assertEqual(d["taxes"], [{"type": "other_tax", "amount": 35}])
        self.assertIn("arrears", d)  # every schema field present, even if null


class SelfChecks(unittest.TestCase):
    def test_guide_example_passes(self):
        self.assertEqual(problems(normalize(GOOD)), [])

    def test_catches_misread_units_and_totals(self):
        bad = normalize({**GOOD, "units_consumed": 115, "total_charges": 2000})
        issues = " ".join(problems(bad))
        self.assertIn("units_consumed", issues)
        self.assertIn("sum of charges", issues)

    def test_catches_wrong_late_amount_and_date(self):
        bad = normalize({**GOOD, "payable_after_due_date": 3000, "due_date": "21 Apr 2026"})
        issues = " ".join(problems(bad))
        self.assertIn("payable_after_due_date", issues)
        self.assertIn("due_date", issues)


class Level2Facts(unittest.TestCase):
    HIST = {"usage_history": [{"month": f"2025-{m:02d}", "units": u} for m, u in
                              zip(range(4, 13), [141, 138, 197, 239, 203, 179, 173, 187, 124])] +
                             [{"month": f"2026-{m:02d}", "units": u} for m, u in zip(range(1, 4), [96, 88, 115])],
            "billing_history": [{"month": "2026-03", "billed": 2577.31, "paid": 2577}, {"month": "2026-02", "billed": 2782.51, "paid": 0}]}

    def test_calculations(self):
        f = facts(normalize(GOOD), self.HIST)
        self.assertEqual(f["tax_share_of_bill_pct"], 16.61)
        self.assertEqual(f["same_month_last_year"]["units"], 141)
        self.assertEqual(f["summer_vs_winter"]["summer_total"], 197 + 239 + 203)
        self.assertEqual(f["last_3_months"]["direction"], "up")  # Feb 88 -> Mar 115 -> Apr 151
        self.assertEqual(f["payments"]["no_payment_recorded"], ["2026-02"])

    def test_number_check_flags_invented_figures_only(self):
        flagged = unverified({"Q1": "Taxes are PKR 569.62 of 3,430.24.", "Q2": "You paid 9,999."}, normalize(GOOD), self.HIST)
        self.assertEqual(flagged, {"Q2": [9999.0]})


if __name__ == "__main__":
    unittest.main()
