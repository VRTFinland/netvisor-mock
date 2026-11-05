import json
import unittest
from uuid import uuid4

from lxml import etree

from payroll_test_support import PayrollServerTestCase

COLLECTOR = b"""<root><payrollperiodcollector>
    <date format="ansi">2026-10-09</date>
    <employeeidentifier type="number">EMP-1</employeeidentifier>
    <payrollratioline><amount>12.50</amount><payrollratio type="number">1234</payrollratio>
        <dimension><dimensionname>Cost centre</dimensionname><dimensionitem>Port</dimensionitem></dimension>
    </payrollratioline>
</payrollperiodcollector></root>"""

WORKDAY = b"""<root><workday>
    <date format="ansi" method="replace">2026-10-09</date>
    <employeeidentifier type="number">EMP-2</employeeidentifier>
    <workdayhour><hours>7.5</hours><collectorratio type="number">4567</collectorratio>
        <acceptancestatus>accepted</acceptancestatus><description>Port work</description>
    </workdayhour>
</workday></root>"""


class PayrollRoutesTest(PayrollServerTestCase):
    def test_successful_resources_and_isolated_post_journal(self):
        org = "route-success-" + uuid4().hex
        self.assertEqual(self.configure(org, "success", ["1234"])[0], 200)
        status, body = self.call("/payrollratiolist.nv?source=collectorratiolines", org=org, headers=self.auth())
        self.assertEqual(status, 200)
        self.assertIn(b"<RatioNumber>1234</RatioNumber>", body)
        status, body = self.call(
            "/payrollperiodcollector.nv?useonlyexistingdimensions=1", "POST", COLLECTOR, org, self.auth()
        )
        self.assertEqual(status, 200)
        self.assertIn(b"InsertedDataIdentifier", body)
        status, body = self.call("/workday.nv?useonlyexistingdimensions=0", "POST", WORKDAY, org, self.auth())
        self.assertEqual(status, 200)
        self.assertNotIn(b"InsertedDataIdentifier", body)
        history = self.journal(org)
        self.assertEqual(history["count"], 2)
        self.assertEqual(history["requests"][0]["path"], "/payrollperiodcollector.nv")
        self.assertIn("<payrollperiodcollector>", history["requests"][0]["body"])
        self.assertNotIn("test-key", json.dumps(history))
        self.assertEqual(self.journal("another-org-" + uuid4().hex)["count"], 0)

    def test_scenarios_preserve_every_post_and_do_not_cross_contexts(self):
        for scenario, expected_status in [
            ("business_rejection", 400),
            ("auth_rejection", 401),
            ("ambiguous", 200),
            ("server_failure", 503),
            ("timeout", 408),
        ]:
            org = f"route-{scenario}-{uuid4().hex}"
            self.assertEqual(self.configure(org, scenario)[0], 200)
            for _ in range(2):
                status, _ = self.call("/workday.nv?useonlyexistingdimensions=1", "POST", WORKDAY, org, self.auth())
                self.assertEqual(status, expected_status)
            self.assertEqual(self.journal(org)["count"], 2)

    def test_rejects_invalid_contract_and_keeps_request_visible(self):
        org = "route-invalid-" + uuid4().hex
        status, body = self.call(
            "/payrollperiodcollector.nv?method=add&useonlyexistingdimensions=1", "POST", COLLECTOR, org, self.auth()
        )
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        collector_with_method = COLLECTOR.replace(b'<date format="ansi">', b'<date format="ansi" method="replace">')
        status, body = self.call(
            "/payrollperiodcollector.nv?useonlyexistingdimensions=1", "POST", collector_with_method, org, self.auth()
        )
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        status, body = self.call("/payrollperiodcollector.nv?method=edit", "POST", COLLECTOR, org, self.auth())
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        status, body = self.call("/workday.nv", "POST", b"<Root/>", org, self.auth())
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        status, body = self.call(
            "/payrollperiodcollector.nv?useonlyexistingdimensions=1",
            "POST",
            COLLECTOR.replace(b">1234</payrollratio>", b">NV-CODE</payrollratio>"),
            org,
            self.auth(),
        )
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        status, body = self.call(
            "/workday.nv?useonlyexistingdimensions=1",
            "POST",
            WORKDAY.replace(b">4567</collectorratio>", b">NV-CODE</collectorratio>"),
            org,
            self.auth(),
        )
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        status, body = self.call("/workday.nv", "POST", WORKDAY, org)
        self.assertEqual(status, 401)
        self.assertIn(b"AUTHENTICATION_FAILED", body)
        status, body = self.call(
            "/workday.nv?useonlyexistingdimensions=1",
            "POST",
            WORKDAY,
            org,
            {**self.auth(), "X-Netvisor-Authentication-MAC": "0" * 64},
            sign=False,
        )
        self.assertEqual(status, 401)
        self.assertIn(b"AUTHENTICATION_FAILED", body)
        status, body = self.call(
            "/workday.nv?useonlyexistingdimensions=1&userKey=do-not-log", "POST", WORKDAY, org, self.auth()
        )
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        history = self.journal(org)
        self.assertEqual(history["count"], 9)
        self.assertEqual(history["requests"][-1]["query"]["userKey"], "<redacted>")
        self.assertNotIn("do-not-log", json.dumps(history))

    def test_accented_authentication_uses_iso_8859_15(self):
        org = "route-accented-" + uuid4().hex
        user_key = "salaisuus-€-ä"
        sender = "GISGRÖ"
        customer = "käyttäjä"
        self.assertEqual(self.configure(org, "success", user_key=user_key, sender=sender)[0], 200)
        headers = self.auth(sender=sender, customer=customer)
        status, body = self.call(
            "/payrollratiolist.nv?source=collectorratiolines", org=org, headers=headers, user_key=user_key
        )
        self.assertEqual(status, 200)
        self.assertIn(b"<RatioNumber>1234</RatioNumber>", body)
        status, body = self.call(
            "/payrollratiolist.nv?source=collectorratiolines",
            org=org,
            headers=headers,
            user_key=user_key,
            mac_encoding="utf-8",
        )
        self.assertEqual(status, 401)
        self.assertIn(b"AUTHENTICATION_FAILED", body)

    def test_decimal_numeric_codes_remain_valid(self):
        org = "route-decimal-" + uuid4().hex
        status, _ = self.call(
            "/payrollperiodcollector.nv?useonlyexistingdimensions=1",
            "POST",
            COLLECTOR.replace(b">1234</payrollratio>", b">1234.5</payrollratio>"),
            org,
            self.auth(),
        )
        self.assertEqual(status, 200)
        status, _ = self.call(
            "/workday.nv?useonlyexistingdimensions=1",
            "POST",
            WORKDAY.replace(b">4567</collectorratio>", b">4567.5</collectorratio>"),
            org,
            self.auth(),
        )
        self.assertEqual(status, 200)

    def test_legacy_invoice_route_still_responds(self):
        status, _ = self.call("/salesinvoice.nv", "POST")
        self.assertEqual(status, 204)

    def test_clearing_one_context_preserves_another(self):
        first = "route-clear-a-" + uuid4().hex
        second = "route-clear-b-" + uuid4().hex
        for org in (first, second):
            status, _ = self.call("/workday.nv?useonlyexistingdimensions=1", "POST", WORKDAY, org, self.auth())
            self.assertEqual(status, 200)
        self.assertEqual(self.call(f"/__test__/payroll/{first}", "DELETE", org=first)[0], 204)
        self.assertEqual(self.journal(first)["count"], 0)
        self.assertEqual(self.journal(second)["count"], 1)


RECORD_TYPE_LIST = "/getrecordtypelist.nv"


class RecordTypeListTest(PayrollServerTestCase):
    def record_types(self, org):
        status, body = self.call(RECORD_TYPE_LIST, org=org, headers=self.auth())
        self.assertEqual(status, 200)
        root = etree.fromstring(body)
        self.assertEqual(root.findtext("ResponseStatus/Status"), "OK")
        return root.findall("RecordTypes/RecordType")

    def test_default_list_has_the_spec_record_type_and_one_without_a_number(self):
        org = "record-types-default-" + uuid4().hex
        numbered, numberless = self.record_types(org)
        self.assertEqual(numbered.findtext("RatioNumber"), "4567")
        self.assertEqual(numbered.findtext("NetvisorKey"), "1")
        self.assertEqual([name.get("ISO639-1Code") for name in numbered.findall("Names/Name")], ["fi", "en", "sv"])
        self.assertTrue(numbered.findtext("Names/Name"))
        self.assertEqual(numbered.findtext("CharacterType"), "1")
        self.assertEqual(numbered.findtext("UnitType"), "1")
        self.assertIsNotNone(numberless.find("RatioNumber"))
        self.assertIsNone(numberless.find("RatioNumber").text)
        self.assertEqual(numberless.findtext("NetvisorKey"), "2")

    def test_record_type_numbers_scenario_replaces_only_the_record_type_list(self):
        org = "record-types-scenario-" + uuid4().hex
        status, body = self.configure(org, "success", record_type_numbers=["1010", None, "2020"])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["recordTypeNumbers"], ["1010", None, "2020"])
        self.assertEqual([item.findtext("RatioNumber") for item in self.record_types(org)], ["1010", "", "2020"])
        status, body = self.call("/payrollratiolist.nv?source=collectorratiolines", org=org, headers=self.auth())
        self.assertEqual(status, 200)
        self.assertIn(b"<RatioNumber>1234</RatioNumber>", body)
        self.assertEqual(len(self.record_types("record-types-other-" + uuid4().hex)), 2)
        self.assertEqual(self.configure(org, "success")[0], 200)
        self.assertEqual([item.findtext("RatioNumber") for item in self.record_types(org)], ["4567", ""])

    def test_lists_without_numbered_record_types(self):
        org = "record-types-numberless-" + uuid4().hex
        self.assertEqual(self.configure(org, "success", record_type_numbers=[None])[0], 200)
        (numberless,) = self.record_types(org)
        self.assertIsNone(numberless.find("RatioNumber").text)
        self.assertEqual(self.configure(org, "success", record_type_numbers=[])[0], 200)
        self.assertEqual(self.record_types(org), [])

    def test_rejects_invalid_record_type_numbers(self):
        org = "record-types-invalid-" + uuid4().hex
        for numbers in ("4567", [""], [" "], [4567], ["45\x0067"]):
            status, body = self.configure(org, "success", record_type_numbers=numbers)
            self.assertEqual(status, 400, numbers)
            self.assertIn(b"Invalid recordTypeNumbers", body)
        self.assertEqual(self.configure(org, "success", ratio_numbers=[None])[0], 400)

    def test_authentication_failures_and_auth_rejection_scenario(self):
        org = "record-types-auth-" + uuid4().hex
        status, body = self.call(RECORD_TYPE_LIST, org=org)
        self.assertEqual(status, 401)
        self.assertIn(b"AUTHENTICATION_FAILED", body)
        status, body = self.call(
            RECORD_TYPE_LIST, org=org, headers={**self.auth(), "X-Netvisor-Authentication-MAC": "0" * 64}, sign=False
        )
        self.assertEqual(status, 401)
        self.assertIn(b"AUTHENTICATION_FAILED", body)
        self.assertEqual(self.configure(org, "auth_rejection")[0], 200)
        status, body = self.call(RECORD_TYPE_LIST, org=org, headers=self.auth())
        self.assertEqual(status, 401)
        self.assertIn(b"AUTHENTICATION_FAILED", body)
        self.assertEqual(self.journal(org)["count"], 0)

    def test_rejects_a_query(self):
        org = "record-types-query-" + uuid4().hex
        status, body = self.call(RECORD_TYPE_LIST + "?source=collectorratiolines", org=org, headers=self.auth())
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)


if __name__ == "__main__":
    unittest.main()
