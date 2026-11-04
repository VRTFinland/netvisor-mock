"""getemployees.nv and employee.nv?method=add (issue #10), pinned to the Netvisor sandbox on 2026-11-04."""

import json
import unittest
from uuid import uuid4

from lxml import etree

from payroll_test_support import PayrollServerTestCase

SEEDED = [
    {
        "netvisorKey": "4",
        "employeeNumber": "2",
        "firstName": "Matti",
        "lastName": "Meikäläinen",
        "employmentStatus": 1,
        "personalId": "010180-900A",
    },
    {"netvisorKey": "19", "employeeNumber": "90", "firstName": "Ended", "lastName": "Person", "employmentStatus": 0},
]

# Exactly the elements GISGRO's NetvisorPayrollEmployeeCreator sends.
NEW_EMPLOYEE = """<root><employee>
    <employeebaseinformation>
        <employeeidentifier>{identity}</employeeidentifier>
        <firstname>Anna</firstname>
        <lastname>Ammattilainen</lastname>
    </employeebaseinformation>
    <employeepayrollinformation>
        <streetaddress>Satamatie 1</streetaddress>
        <postnumber>48100</postnumber>
        <city>Kotka</city>
        <employeenumber>{number}</employeenumber>
        <payrollrulegroupname>Tuntipalkkalaiset</payrollrulegroupname>
    </employeepayrollinformation>
    <employmentperiods><employmentperiod>
        <startdate format="ansi">2026-11-01</startdate>
        <profession>Ahtaaja</profession>
    </employmentperiod></employmentperiods>
</employee></root>"""


def new_employee(identity="020290-901B", number="100") -> bytes:
    return NEW_EMPLOYEE.format(identity=identity, number=number).encode()


def statuses(body: bytes) -> list[str | None]:
    return [status.text for status in etree.fromstring(body).iterfind("ResponseStatus/Status")]


class EmployeesTest(PayrollServerTestCase):
    def setUp(self):
        self.org = "employees-" + uuid4().hex
        status, _ = self.put_json(f"/__test__/payroll/{self.org}/employees", SEEDED, self.org)
        self.assertEqual(status, 200)

    def list_employees(self, query="?includeendedemployments=1"):
        status, body = self.call("/getemployees.nv" + query, org=self.org, headers=self.auth())
        self.assertEqual(status, 200, body)
        self.assertEqual(statuses(body), ["OK"])
        return etree.fromstring(body).findall("employees/employee")

    def add(self, body: bytes, query="?method=add"):
        return self.call("/employee.nv" + query, "POST", body, self.org, self.auth())

    def test_lists_seeded_employees_in_netvisor_shape(self):
        matti = self.list_employees()[0]

        self.assertEqual(
            [(child.tag, child.text) for child in matti],
            [
                ("employeenumber", "2"),
                ("netvisorkey", "4"),
                ("personalid", "010180-900A"),
                ("firstname", "Matti"),
                ("lastname", "Meikäläinen"),
                ("realname", "Matti Meikäläinen"),
                ("employmentstatus", "1"),
                ("payrollservice", "1"),
                ("resourcemanagement", "0"),
                ("lunchbenefit", "0"),
            ],
        )
        self.assertEqual(matti.xpath("string(personalid/@type)"), "finnishidentifier")

    def test_ended_employments_are_listed_only_on_request(self):
        def keys(query):
            return [employee.findtext("netvisorkey") for employee in self.list_employees(query)]

        self.assertEqual(keys(""), ["4"])
        self.assertEqual(keys("?includeendedemployments=0"), ["4"])
        self.assertEqual(keys("?includeendedemployments=1"), ["4", "19"])
        self.assertEqual(self.list_employees()[1].findtext("employmentstatus"), "0")

    def test_list_requires_authentication_and_a_supported_query(self):
        status, body = self.call("/getemployees.nv", org=self.org)
        self.assertEqual(status, 401)
        self.assertIn(b"AUTHENTICATION_FAILED", body)
        status, body = self.call("/getemployees.nv?includeendedemployments=2", org=self.org, headers=self.auth())
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)

    def test_successful_add_answers_ok_without_key_and_is_listed_with_a_new_key(self):
        status, body = self.add(new_employee())

        self.assertEqual(status, 200)
        self.assertEqual(statuses(body), ["OK"])
        self.assertIsNone(etree.fromstring(body).find("Replies"))
        added = [e for e in self.list_employees() if e.findtext("employeenumber") == "100"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].findtext("netvisorkey"), "20")
        self.assertEqual(added[0].findtext("employmentstatus"), "1")
        self.assertEqual(added[0].findtext("lastname"), "Ammattilainen")

    def test_add_without_employee_number_is_accepted(self):
        status, _ = self.add(new_employee(number="").replace(b"<employeenumber></employeenumber>", b""))

        self.assertEqual(status, 200)
        self.assertEqual(len(self.list_employees()), 3)

    def test_existing_identity_code_is_refused_with_a_masked_code(self):
        status, body = self.add(new_employee(identity="010180-900A", number="101"))

        self.assertEqual(status, 400)
        self.assertEqual(
            statuses(body),
            ["FAILED", "INVALID_DATA :: Data form incorrect:. Employee information already exists: 010180-xxxx"],
        )
        self.assertEqual(len(self.list_employees()), 2)

    def test_existing_employee_number_is_refused(self):
        status, body = self.add(new_employee(number="90"))

        self.assertEqual(status, 400)
        self.assertEqual(
            statuses(body), ["FAILED", "INVALID_DATA :: Data form incorrect:. Employee number already in use: 90"]
        )
        self.assertEqual(len(self.list_employees()), 2)

    def test_business_rejection_carries_the_configured_free_text(self):
        message = "Data form incorrect:. Salary model not found: Tuntipalkkalaiset"
        self.assertEqual(self.configure(self.org, "business_rejection", rejection_message=message)[0], 200)

        status, body = self.add(new_employee())

        self.assertEqual(status, 400)
        self.assertEqual(statuses(body), ["FAILED", "INVALID_DATA :: " + message])
        self.assertEqual(len(self.list_employees()), 2)

    def test_failure_scenarios_match_the_other_payroll_resources(self):
        for scenario, expected_status in [("server_failure", 503), ("timeout", 408), ("auth_rejection", 401)]:
            self.assertEqual(self.configure(self.org, scenario)[0], 200)
            status, _ = self.add(new_employee())
            self.assertEqual(status, expected_status, scenario)
        self.assertEqual(len(self.list_employees()), 2)

    def test_invalid_requests_are_rejected_as_invalid_data(self):
        invalid = [
            (new_employee(), "?method=edit"),
            (new_employee(), ""),
            (new_employee().replace(b"<city>Kotka</city>", b""), "?method=add"),
            (new_employee().replace(b"<employeeidentifier>020290-901B</employeeidentifier>", b""), "?method=add"),
            (new_employee().replace(b'format="ansi"', b""), "?method=add"),
            (new_employee().replace(b"<profession>Ahtaaja</profession>", b""), "?method=add"),
            (new_employee(number="A-100"), "?method=add"),
            (b"<root><workday/></root>", "?method=add"),
        ]
        for body, query in invalid:
            status, response = self.add(body, query)
            self.assertEqual(status, 400, (query, body[:80]))
            self.assertIn(b"INVALID_DATA", response)
        self.assertEqual(len(self.list_employees()), 2)

    def test_journal_records_posts_without_identity_codes(self):
        self.add(new_employee())

        journal = self.journal(self.org)
        self.assertEqual(journal["count"], 1)
        self.assertEqual(journal["requests"][0]["path"], "/employee.nv")
        self.assertIn("<employeeidentifier>[redacted]</employeeidentifier>", journal["requests"][0]["body"])
        self.assertNotIn("020290-901B", json.dumps(journal))

    def test_test_listing_omits_identity_codes_and_contexts_stay_isolated(self):
        self.add(new_employee())

        status, body = self.call(f"/__test__/payroll/{self.org}/employees", org=self.org)
        listed = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(listed["count"], 3)
        self.assertNotIn("personalId", json.dumps(listed))
        other = "employees-other-" + uuid4().hex
        status, body = self.call("/getemployees.nv?includeendedemployments=1", org=other, headers=self.auth())
        self.assertEqual(status, 200)
        self.assertEqual(etree.fromstring(body).findall("employees/employee"), [])

    def test_seeding_rejects_invalid_or_duplicate_employees(self):
        for payload in [
            {"netvisorKey": "1"},
            [{"employeeNumber": "1"}],
            [{"netvisorKey": "1", "employmentStatus": 2}],
            [{"netvisorKey": "1", "unknown": "x"}],
            [{"netvisorKey": "1"}, {"netvisorKey": "1"}],
            [{"netvisorKey": "1", "employeeNumber": "5"}, {"netvisorKey": "2", "employeeNumber": "5"}],
            [{"netvisorKey": "1", "employeeNumber": "A-5"}],
            [{"netvisorKey": "1", "firstName": "Bad\u0001name"}],
        ]:
            status, _ = self.put_json(f"/__test__/payroll/{self.org}/employees", payload, self.org)
            self.assertEqual(status, 400, payload)
        self.assertEqual(len(self.list_employees()), 2)


class IdentityCodeJournalTest(PayrollServerTestCase):
    """No identity code reaches GET /__test__/payroll/<org>/requests, however the body is written."""

    def setUp(self):
        self.org = "journal-" + uuid4().hex

    def journaled(self, path: str, body: bytes) -> str:
        self.call(path, "POST", body, self.org, self.auth())
        return self.journal(self.org)["requests"][-1]["body"]

    def test_employee_identity_code_in_cdata_or_with_odd_spacing_is_redacted(self):
        for body in (
            new_employee(identity="<![CDATA[020290-901B]]>"),
            new_employee().replace(b"</employeeidentifier>", b"</employeeidentifier >"),
        ):
            self.assertNotIn("020290-901B", self.journaled("/employee.nv?method=add", body))

    def test_unparsable_body_with_an_employee_identifier_is_withheld(self):
        body = self.journaled("/employee.nv?method=add", b"<root><employee><employeeidentifier>020290-901B")

        self.assertNotIn("020290-901B", body)
        self.assertIn("withheld", body)

    def test_workday_personal_identification_number_is_redacted_but_other_types_are_kept(self):
        workday = b"""<root><workday><date format="ansi" method="replace">2026-10-09</date>
            <employeeidentifier type="{type}">{value}</employeeidentifier>
            <workdayhour><hours>7.5</hours><collectorratio type="number">4567</collectorratio>
            <acceptancestatus>accepted</acceptancestatus><description>Port work</description></workdayhour>
        </workday></root>"""

        pin = self.journaled(
            "/workday.nv?useonlyexistingdimensions=1",
            workday.replace(b"{type}", b"personalidentificationnumber").replace(b"{value}", b"010180-900A"),
        )
        number_body = workday.replace(b"{type}", b"number").replace(b"{value}", b"EMP-2")
        number = self.journaled("/workday.nv?useonlyexistingdimensions=1", number_body)

        self.assertNotIn("010180-900A", pin)
        self.assertIn("[redacted]", pin)
        self.assertEqual(number, number_body.decode())


class EmployeeKeyTest(PayrollServerTestCase):
    def test_non_ascii_digit_keys_do_not_break_the_next_key(self):
        org = "keys-" + uuid4().hex
        self.put_json(f"/__test__/payroll/{org}/employees", [{"netvisorKey": "²"}, {"netvisorKey": "7"}], org)

        status, _ = self.call("/employee.nv?method=add", "POST", new_employee(), org, self.auth())

        self.assertEqual(status, 200)
        _, body = self.call("/getemployees.nv", org=org, headers=self.auth())
        keys = [e.findtext("netvisorkey") for e in etree.fromstring(body).findall("employees/employee")]
        self.assertEqual(keys, ["²", "7", "8"])


if __name__ == "__main__":
    unittest.main()
