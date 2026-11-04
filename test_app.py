"""Tests for the invoicing endpoints in app.py.

Every test runs against empty in-memory data and never touches the developer's data.json.
"""

import base64
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lxml import etree

from app import BASE_URL, app, netvisorData

CUSTOMER_PORT = b"""<root><customer><customerbaseinformation>
    <internalidentifier>C-1</internalidentifier>
    <externalidentifier>1234567-8</externalidentifier>
    <name>Port Oy</name>
    <nameextension>Harbour</nameextension>
    <streetaddress>Satamatie 1</streetaddress>
    <city>Kotka</city>
    <postnumber>48100</postnumber>
    <country type="ISO-3166">FI</country>
    <customergroupname>Ports</customergroupname>
</customerbaseinformation></customer></root>"""

CUSTOMER_SHIP = b"""<root><customer><customerbaseinformation>
    <internalidentifier>C-2</internalidentifier>
    <externalidentifier>7654321-0</externalidentifier>
    <name>Ship Ab</name>
</customerbaseinformation></customer></root>"""

# Shaped like GISGRO's generated sales invoice: dates, seller and customer carry attributes.
SALESINVOICE = b"""<root><salesinvoice>
    <salesinvoicedate format="ansi">2026-11-05</salesinvoicedate>
    <salesinvoicevaluedate format="ansi">2026-11-05</salesinvoicevaluedate>
    <salesinvoiceduedate format="ansi">2026-11-19</salesinvoiceduedate>
    <salesinvoicedeliverydate format="ansi">2026-11-03</salesinvoicedeliverydate>
    <salesinvoicereferencenumber>1232</salesinvoicereferencenumber>
    <salesinvoiceamount>124.50</salesinvoiceamount>
    <selleridentifier type="name">Seller</selleridentifier>
    <salesinvoicestatus type="netvisor">open</salesinvoicestatus>
    <invoicingcustomeridentifier type="netvisor">1</invoicingcustomeridentifier>
    <salesinvoiceyourreference>PO-42</salesinvoiceyourreference>
</salesinvoice></root>"""


def parse(body: bytes) -> etree._Element:
    return etree.fromstring(body)


def statuses(body: bytes) -> list[str | None]:
    return [s.text for s in parse(body).iterfind("ResponseStatus/Status")]


def inserted_identifier(body: bytes) -> str | None:
    return parse(body).findtext("Replies/InsertedDataIdentifier")


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.write_data = MagicMock()
        state = {
            "customers": {},
            "salesinvoices": {},
            "businessIdCustomerMap": {},
            "customer_count": 0,
            "salesinvoice_count": 0,
            "write_data": self.write_data,
        }
        for name, value in state.items():
            patcher = patch.object(netvisorData, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        print_patcher = patch("builtins.print")
        print_patcher.start()
        self.addCleanup(print_patcher.stop)

    def add_customer(self, xml: bytes = CUSTOMER_PORT) -> str | None:
        response = self.client.post("/customer.nv?method=add", data=xml)
        self.assertEqual(response.status_code, 200)
        return inserted_identifier(response.data)

    def add_salesinvoice(self, xml: bytes = SALESINVOICE) -> str | None:
        response = self.client.post("/salesinvoice.nv?method=add", data=xml)
        self.assertEqual(response.status_code, 200)
        return inserted_identifier(response.data)


class RootAndResetTest(AppTestCase):
    def test_root_greets(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"Hello!")

    def test_reset_clears_data_and_rewrites_data_json(self):
        self.add_customer()
        self.add_salesinvoice()

        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            response = self.client.post("/reset")
            stored = json.loads(Path("data.json").read_text())

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            stored,
            {
                "customers": {},
                "salesinvoices": {},
                "customersCount": 0,
                "salesinvoicesCount": 0,
                "businessIdCustomerMap": {},
            },
        )
        self.assertEqual(netvisorData.customers, {})
        self.assertEqual(netvisorData.salesinvoices, {})
        self.assertEqual(self.add_customer(), "1")


class CustomerTest(AppTestCase):
    def test_add_returns_sequential_identifiers_and_persists(self):
        self.assertEqual(self.add_customer(CUSTOMER_PORT), "1")
        self.assertEqual(self.add_customer(CUSTOMER_SHIP), "2")

        self.assertEqual(netvisorData.customers["1"]["customerbaseinformation"]["name"], "Port Oy")
        self.assertEqual(netvisorData.businessIdCustomerMap, {"1234567-8": "1", "7654321-0": "2"})
        self.assertEqual(self.write_data.call_count, 2)

    def test_add_response_is_ok_with_inserted_identifier(self):
        response = self.client.post("/customer.nv?method=add", data=CUSTOMER_PORT)

        self.assertEqual(statuses(response.data), ["OK"])
        self.assertEqual(inserted_identifier(response.data), "1")
        self.assertIsNotNone(parse(response.data).findtext("ResponseStatus/TimeStamp"))

    def test_edit_replaces_customer_with_given_id(self):
        self.add_customer(CUSTOMER_PORT)

        response = self.client.post("/customer.nv?method=edit&id=1", data=CUSTOMER_SHIP)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(inserted_identifier(response.data), "1")
        self.assertEqual(netvisorData.customers["1"]["customerbaseinformation"]["name"], "Ship Ab")
        self.assertEqual(netvisorData.customer_count, 1)
        self.assertEqual(self.write_data.call_count, 2)


class CustomerListTest(AppTestCase):
    def customers(self, query: str = "") -> list[etree._Element]:
        response = self.client.get("/customerlist.nv" + query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(statuses(response.data), ["OK"])
        return parse(response.data).findall("Customerlist/Customer")

    def test_empty_list(self):
        response = self.client.get("/customerlist.nv")

        self.assertIsNotNone(parse(response.data).find("Customerlist"))
        self.assertEqual(self.customers(), [])

    def test_lists_all_customers_without_keyword(self):
        self.add_customer(CUSTOMER_PORT)
        self.add_customer(CUSTOMER_SHIP)

        customers = self.customers()

        self.assertEqual([c.findtext("Netvisorkey") for c in customers], ["1", "2"])
        self.assertEqual(
            [(child.tag, child.text) for child in customers[0]],
            [
                ("Netvisorkey", "1"),
                ("Name", "Port Oy"),
                ("Code", "C-1"),
                ("OrganisationIdentifier", "1234567-8"),
                ("CustomerGroupID", None),
                ("CustomerGroupName", "Ports"),
                ("Uri", f"{BASE_URL}/getcustomer.nv?id=1"),
            ],
        )

    def test_keyword_matches_prefix_of_name_code_business_id_or_name_extension(self):
        self.add_customer(CUSTOMER_PORT)
        self.add_customer(CUSTOMER_SHIP)

        def keys(keyword: str) -> list[str | None]:
            return [c.findtext("Netvisorkey") for c in self.customers(f"?keyword={keyword}")]

        self.assertEqual(keys("1234"), ["1"])
        self.assertEqual(keys("Ship"), ["2"])
        self.assertEqual(keys("C-"), ["1", "2"])
        self.assertEqual(keys("Harb"), ["1"])
        self.assertEqual(keys("4567"), [])
        self.assertEqual(keys("Oy"), [])


class SalesInvoiceTest(AppTestCase):
    def test_add_returns_sequential_identifiers_and_persists(self):
        self.assertEqual(self.add_salesinvoice(), "1")
        self.assertEqual(self.add_salesinvoice(), "2")

        self.assertEqual(set(netvisorData.salesinvoices), {"1", "2"})
        self.assertEqual(self.write_data.call_count, 2)

    def test_post_without_add_method_is_ignored(self):
        response = self.client.post("/salesinvoice.nv?method=edit&id=1", data=SALESINVOICE)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(netvisorData.salesinvoices, {})

    def test_get_returns_stored_invoice_with_customer(self):
        self.add_customer(CUSTOMER_PORT)
        key = self.add_salesinvoice()

        response = self.client.get(f"/getsalesinvoice.nv?netvisorkey={key}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(statuses(response.data), ["OK"])
        invoice = parse(response.data).find("SalesInvoice")
        assert invoice is not None
        expected = {
            "SalesInvoiceNetvisorKey": "1",
            "SalesInvoiceDate": "2026-11-05",
            "SalesInvoiceValueDate": "2026-11-05",
            "SalesInvoiceDeliveryDate": "2026-11-03",
            "SalesInvoiceDueDate": "2026-11-19",
            "SalesInvoiceReferencenumber": "1232",
            "SalesInvoiceAmount": "124.50",
            "SellerIdentifier": "Seller",
            "InvoiceStatus": "Open",
            "SalesInvoiceYourReference": "PO-42",
            "InvoicingCustomerCode": "C-1",
            "InvoicingCustomerName": "Port Oy",
            "InvoicingCustomerNameExtension": "Harbour",
            "InvoicingCustomerNetvisorKey": "1",
            "InvoicingCustomerOrganizationIdentifier": "1234567-8",
            "InvoicingCustomerAddressline": "Satamatie 1",
            "InvoicingCustomerPostnumber": "48100",
            "InvoicingCustomerTown": "Kotka",
            "InvoicingCustomerCountryCode": "FI",
        }
        self.assertEqual({tag: invoice.findtext(tag) for tag in expected}, expected)
        self.assertEqual(
            {child.tag: dict(child.attrib) for child in invoice if child.attrib},
            {
                "SalesInvoiceDate": {"format": "ansi"},
                "SalesInvoiceValueDate": {"format": "ansi"},
                "SalesInvoiceDeliveryDate": {"format": "ansi"},
                "SalesInvoiceDueDate": {"format": "ansi"},
                "SellerIdentifier": {"type": "name"},
                "InvoiceStatus": {"status": "open"},
                "InvoicingCustomerCountryCode": {"type": "ISO-3166"},
            },
        )
        self.assertIsNone(invoice.find("LastSentInvoicePDFBase64Data"))

    def test_get_resolves_customer_identified_by_customer_code(self):
        self.add_customer(CUSTOMER_SHIP)
        self.add_customer(CUSTOMER_PORT)
        key = self.add_salesinvoice(
            SALESINVOICE.replace(
                b'<invoicingcustomeridentifier type="netvisor">1<',
                b'<invoicingcustomeridentifier type="customer">C-1<',
            )
        )

        invoice = parse(self.client.get(f"/getsalesinvoice.nv?netvisorkey={key}").data).find("SalesInvoice")

        assert invoice is not None
        self.assertEqual(invoice.findtext("InvoicingCustomerNetvisorKey"), "2")
        self.assertEqual(invoice.findtext("InvoicingCustomerName"), "Port Oy")

    def test_status_update_is_visible_in_get(self):
        key = self.add_salesinvoice()
        self.client.post(f"/updatesalesinvoicestatus.nv?netvisorkey={key}&status=paid")

        status = parse(self.client.get(f"/getsalesinvoice.nv?netvisorkey={key}").data).find(
            "SalesInvoice/InvoiceStatus"
        )

        assert status is not None
        self.assertEqual((status.get("status"), status.text), ("paid", "Paid"))

    def test_pdf_is_embedded_only_for_pdfimage_pdf(self):
        key = self.add_salesinvoice()

        for query in ("", "&pdfimage=nopdf", "&pdfimage=lastsentprintservice"):
            response = self.client.get(f"/getsalesinvoice.nv?netvisorkey={key}{query}")
            self.assertIsNone(parse(response.data).find("SalesInvoice/LastSentInvoicePDFBase64Data"), query)

    def test_get_with_pdfimage_embeds_invoice_pdf(self):
        self.add_customer(CUSTOMER_PORT)
        key = self.add_salesinvoice()

        response = self.client.get(f"/getsalesinvoice.nv?netvisorkey={key}&pdfimage=pdf")

        pdf = parse(response.data).findtext("SalesInvoice/LastSentInvoicePDFBase64Data")
        assert pdf is not None
        self.assertEqual(base64.b64decode(pdf), Path(__file__).with_name("invoice.pdf").read_bytes())


class SalesInvoiceErrorsTest(AppTestCase):
    """Pinned to what the Netvisor sandbox returned on 2026-11-05 (UseHTTPResponseStatusCodes=1)."""

    def setUp(self):
        super().setUp()
        netvisorData.salesinvoices["1"] = {
            "invoicingcustomeridentifier": {"#text": "1"},
            "salesinvoicestatus": {"#text": "open"},
        }

    def test_unknown_invoice_returns_ok_with_empty_salesinvoice(self):
        response = self.client.get("/getsalesinvoice.nv?netvisorkey=999999999&pdfimage=pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(statuses(response.data), ["OK"])
        salesinvoice = parse(response.data).find("SalesInvoice")
        assert salesinvoice is not None
        self.assertEqual(len(salesinvoice), 0)

    def test_status_update_for_unknown_invoice_is_rejected(self):
        response = self.client.post("/updatesalesinvoicestatus.nv?netvisorkey=999999999&status=open")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            statuses(response.data),
            ["FAILED", "INVALID_DATA :: Data form incorrect:. No invoice found for the given ID, ID: 999999999"],
        )

    def test_invalid_status_is_rejected_before_invoice_lookup(self):
        response = self.client.post("/updatesalesinvoicestatus.nv?netvisorkey=999999999&status=unsent")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            statuses(response.data),
            ["FAILED", "INVALID_DATA :: Data form incorrect:. Invalid invoice status"],
        )

    def test_status_update_for_known_invoice_succeeds_and_persists(self):
        response = self.client.post("/updatesalesinvoicestatus.nv?netvisorkey=1&status=paid")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(statuses(response.data), ["OK"])
        self.assertEqual(inserted_identifier(response.data), "1")
        self.assertEqual(netvisorData.salesinvoices["1"]["salesinvoicestatus"]["#text"], "paid")
        self.write_data.assert_called_once()


if __name__ == "__main__":
    unittest.main()
