import hashlib
import hmac
import json
import threading
import unittest
from uuid import uuid4
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from werkzeug.serving import make_server

from app import app


COLLECTOR = b'''<root><payrollperiodcollector>
    <date format="ansi">2026-10-09</date>
    <employeeidentifier type="number">EMP-1</employeeidentifier>
    <payrollratioline><amount>12.50</amount><payrollratio type="number">1234</payrollratio>
        <dimension><dimensionname>Cost centre</dimensionname><dimensionitem>Port</dimensionitem></dimension>
    </payrollratioline>
</payrollperiodcollector></root>'''

WORKDAY = b'''<root><workday>
    <date format="ansi" method="replace">2026-10-09</date>
    <employeeidentifier type="number">EMP-2</employeeidentifier>
    <workdayhour><hours>7.5</hours><collectorratio type="number">4567</collectorratio>
        <acceptancestatus>accepted</acceptancestatus><description>Port work</description>
    </workdayhour>
</workday></root>'''


class PayrollRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, app, threaded=True)
        cls.base = "http://127.0.0.1:%s" % cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()

    def call(self, path, method="GET", body=None, org="test-org-a", headers=None, sign=True,
             user_key="test-key", mac_encoding="iso-8859-15"):
        request_headers = {"X-Netvisor-Organisation-ID": org}
        if body is not None and ".nv" in path:
            request_headers["Content-Type"] = "application/xml"
        if headers:
            request_headers.update(headers)
        if sign and "X-Netvisor-Authentication-MAC" in request_headers:
            values = [
                self.base + path, request_headers["X-Netvisor-Authentication-Sender"],
                request_headers["X-Netvisor-Authentication-CustomerId"],
                request_headers["X-Netvisor-Authentication-Timestamp"], "EN", org,
                request_headers["X-Netvisor-Authentication-TransactionId"],
                request_headers["X-Netvisor-Authentication-TimestampUnix"],
                user_key, "gisgro-test-partner-key",
            ]
            request_headers["X-Netvisor-Authentication-MAC"] = hmac.new(
                (user_key + "&gisgro-test-partner-key").encode(mac_encoding),
                "&".join(values).encode(mac_encoding), hashlib.sha256
            ).hexdigest()
        request = Request(self.base + path, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except HTTPError as error:
            with error:
                return error.code, error.read()

    def auth(self, sender="GISGRO", customer="test-user"):
        return {
            "X-Netvisor-Authentication-Sender": sender,
            "X-Netvisor-Authentication-CustomerId": customer,
            "X-Netvisor-Authentication-PartnerId": "gisgro-test-partner",
            "X-Netvisor-Authentication-Timestamp": "2026-10-09 12:00:00.0",
            "X-Netvisor-Authentication-TimestampUnix": "1791547200",
            "X-Netvisor-Authentication-TransactionId": "test-transaction",
            "X-Netvisor-Authentication-MAC": "a" * 64,
            "X-Netvisor-Authentication-MACHashCalculationAlgorithm": "HMACSHA256",
            "X-Netvisor-Authentication-UseHTTPResponseStatusCodes": "1",
            "X-Netvisor-Interface-Language": "EN",
        }

    def configure(self, org, scenario, ratio_numbers=None, user_key=None, sender=None):
        body = {"scenario": scenario}
        if ratio_numbers is not None:
            body["ratioNumbers"] = ratio_numbers
        if user_key is not None:
            body["userKey"] = user_key
        if sender is not None:
            body["sender"] = sender
        return self.call("/__test__/payroll/%s/scenario" % org, "PUT", json.dumps(body).encode(),
                         org=org, headers={"Content-Type": "application/json"})

    def journal(self, org):
        status, body = self.call("/__test__/payroll/%s/requests" % org, org=org)
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_successful_resources_and_isolated_post_journal(self):
        org = "route-success-" + uuid4().hex
        self.assertEqual(self.configure(org, "success", ["1234"])[0], 200)
        status, body = self.call("/payrollratiolist.nv?source=collectorratiolines", org=org,
                                 headers=self.auth())
        self.assertEqual(status, 200)
        self.assertIn(b"<RatioNumber>1234</RatioNumber>", body)
        status, body = self.call("/payrollperiodcollector.nv?useonlyexistingdimensions=1",
                                 "POST", COLLECTOR, org, self.auth())
        self.assertEqual(status, 200)
        self.assertIn(b"InsertedDataIdentifier", body)
        status, body = self.call("/workday.nv?useonlyexistingdimensions=0", "POST", WORKDAY,
                                 org, self.auth())
        self.assertEqual(status, 200)
        self.assertNotIn(b"InsertedDataIdentifier", body)
        history = self.journal(org)
        self.assertEqual(history["count"], 2)
        self.assertEqual(history["requests"][0]["path"], "/payrollperiodcollector.nv")
        self.assertIn("<payrollperiodcollector>", history["requests"][0]["body"])
        self.assertNotIn("test-key", json.dumps(history))
        self.assertEqual(self.journal("another-org-" + uuid4().hex)["count"], 0)

    def test_scenarios_preserve_every_post_and_do_not_cross_contexts(self):
        for scenario, expected_status in [("business_rejection", 400), ("auth_rejection", 401),
                                          ("ambiguous", 200), ("server_failure", 503),
                                          ("timeout", 408)]:
            org = "route-%s-%s" % (scenario, uuid4().hex)
            self.assertEqual(self.configure(org, scenario)[0], 200)
            for _ in range(2):
                status, _ = self.call("/workday.nv?useonlyexistingdimensions=1", "POST", WORKDAY,
                                      org, self.auth())
                self.assertEqual(status, expected_status)
            self.assertEqual(self.journal(org)["count"], 2)

    def test_rejects_invalid_contract_and_keeps_request_visible(self):
        org = "route-invalid-" + uuid4().hex
        status, body = self.call("/payrollperiodcollector.nv?method=add&useonlyexistingdimensions=1",
                                 "POST", COLLECTOR, org, self.auth())
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        collector_with_method = COLLECTOR.replace(
            b'<date format="ansi">', b'<date format="ansi" method="replace">'
        )
        status, body = self.call("/payrollperiodcollector.nv?useonlyexistingdimensions=1",
                                 "POST", collector_with_method, org, self.auth())
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        status, body = self.call("/payrollperiodcollector.nv?method=edit", "POST", COLLECTOR,
                                 org, self.auth())
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        status, body = self.call("/workday.nv", "POST", b"<Root/>", org, self.auth())
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        status, body = self.call("/payrollperiodcollector.nv?useonlyexistingdimensions=1",
                                 "POST", COLLECTOR.replace(b">1234</payrollratio>",
                                                           b">NV-CODE</payrollratio>"), org, self.auth())
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        status, body = self.call("/workday.nv?useonlyexistingdimensions=1", "POST",
                                 WORKDAY.replace(b">4567</collectorratio>",
                                                 b">NV-CODE</collectorratio>"), org, self.auth())
        self.assertEqual(status, 400)
        self.assertIn(b"INVALID_DATA", body)
        status, body = self.call("/workday.nv", "POST", WORKDAY, org)
        self.assertEqual(status, 401)
        self.assertIn(b"AUTHENTICATION_FAILED", body)
        status, body = self.call("/workday.nv?useonlyexistingdimensions=1", "POST", WORKDAY,
                                 org, {**self.auth(), "X-Netvisor-Authentication-MAC": "0" * 64},
                                 sign=False)
        self.assertEqual(status, 401)
        self.assertIn(b"AUTHENTICATION_FAILED", body)
        status, body = self.call("/workday.nv?useonlyexistingdimensions=1&userKey=do-not-log",
                                 "POST", WORKDAY, org, self.auth())
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
        status, body = self.call("/payrollratiolist.nv?source=collectorratiolines", org=org,
                                 headers=headers, user_key=user_key)
        self.assertEqual(status, 200)
        self.assertIn(b"<RatioNumber>1234</RatioNumber>", body)
        status, body = self.call("/payrollratiolist.nv?source=collectorratiolines", org=org,
                                 headers=headers, user_key=user_key, mac_encoding="utf-8")
        self.assertEqual(status, 401)
        self.assertIn(b"AUTHENTICATION_FAILED", body)

    def test_decimal_numeric_codes_remain_valid(self):
        org = "route-decimal-" + uuid4().hex
        status, _ = self.call("/payrollperiodcollector.nv?useonlyexistingdimensions=1", "POST",
                              COLLECTOR.replace(b">1234</payrollratio>",
                                                b">1234.5</payrollratio>"), org, self.auth())
        self.assertEqual(status, 200)
        status, _ = self.call("/workday.nv?useonlyexistingdimensions=1", "POST",
                              WORKDAY.replace(b">4567</collectorratio>",
                                              b">4567.5</collectorratio>"), org, self.auth())
        self.assertEqual(status, 200)

    def test_legacy_invoice_route_still_responds(self):
        status, _ = self.call("/salesinvoice.nv", "POST")
        self.assertEqual(status, 204)

    def test_clearing_one_context_preserves_another(self):
        first = "route-clear-a-" + uuid4().hex
        second = "route-clear-b-" + uuid4().hex
        for org in (first, second):
            status, _ = self.call("/workday.nv?useonlyexistingdimensions=1", "POST", WORKDAY,
                                  org, self.auth())
            self.assertEqual(status, 200)
        self.assertEqual(self.call("/__test__/payroll/%s" % first, "DELETE", org=first)[0], 204)
        self.assertEqual(self.journal(first)["count"], 0)
        self.assertEqual(self.journal(second)["count"], 1)


if __name__ == "__main__":
    unittest.main()
