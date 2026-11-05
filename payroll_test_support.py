"""Shared HTTP harness for the payroll tests."""

import hashlib
import hmac
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from werkzeug.serving import make_server

from app import app


class PayrollServerTestCase(unittest.TestCase):
    """Runs the mock on a real port and signs requests the way GISGRO's NetvisorAuthUtil does."""

    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, app, threaded=True)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()

    def call(
        self,
        path,
        method="GET",
        body=None,
        org="test-org-a",
        headers=None,
        sign=True,
        user_key="test-key",
        mac_encoding="iso-8859-15",
    ):
        request_headers = {"X-Netvisor-Organisation-ID": org}
        if body is not None and ".nv" in path:
            request_headers["Content-Type"] = "application/xml"
        if headers:
            request_headers.update(headers)
        if sign and "X-Netvisor-Authentication-MAC" in request_headers:
            values = [
                self.base + path,
                request_headers["X-Netvisor-Authentication-Sender"],
                request_headers["X-Netvisor-Authentication-CustomerId"],
                request_headers["X-Netvisor-Authentication-Timestamp"],
                "EN",
                org,
                request_headers["X-Netvisor-Authentication-TransactionId"],
                request_headers["X-Netvisor-Authentication-TimestampUnix"],
                user_key,
                "gisgro-test-partner-key",
            ]
            request_headers["X-Netvisor-Authentication-MAC"] = hmac.new(
                (user_key + "&gisgro-test-partner-key").encode(mac_encoding),
                "&".join(values).encode(mac_encoding),
                hashlib.sha256,
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

    def configure(
        self,
        org,
        scenario,
        ratio_numbers=None,
        user_key=None,
        sender=None,
        rejection_message=None,
        record_type_numbers=None,
    ):
        body = {"scenario": scenario}
        if rejection_message is not None:
            body["rejectionMessage"] = rejection_message
        if ratio_numbers is not None:
            body["ratioNumbers"] = ratio_numbers
        if record_type_numbers is not None:
            body["recordTypeNumbers"] = record_type_numbers
        if user_key is not None:
            body["userKey"] = user_key
        if sender is not None:
            body["sender"] = sender
        return self.put_json(f"/__test__/payroll/{org}/scenario", body, org)

    def put_json(self, path, payload, org):
        return self.call(
            path, "PUT", json.dumps(payload).encode(), org=org, headers={"Content-Type": "application/json"}
        )

    def journal(self, org):
        status, body = self.call(f"/__test__/payroll/{org}/requests", org=org)
        self.assertEqual(status, 200)
        return json.loads(body)
