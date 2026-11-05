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

from app import BASE_URL, NetvisorData, app, netvisorData

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
            "customer_changed": {},
            "write_data": self.write_data,
        }
        for name, value in state.items():
            patcher = patch.object(netvisorData, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

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

        real_write = NetvisorData.write_data.__get__(netvisorData)
        with (
            tempfile.TemporaryDirectory() as tmp,
            contextlib.chdir(tmp),
            patch.object(netvisorData, "write_data", real_write),
        ):
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
                "customerChangedTimes": {},
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
                ("CustomerGroupID", "1"),
                ("CustomerGroupName", "Ports"),
                ("Uri", f"{BASE_URL}/getcustomer.nv?id=1"),
            ],
        )

    def test_customer_without_code_or_group_shows_key_and_default_group(self):
        self.add_customer(CUSTOMER_SHIP.replace(b"<internalidentifier>C-2</internalidentifier>", b""))

        customer = self.customers()[0]

        self.assertEqual(customer.findtext("Code"), "1")
        self.assertEqual(customer.findtext("CustomerGroupName"), "Asiakasryhmä 1")

    def test_keyword_matches_case_insensitive_substring_of_name_code_business_id_or_name_extension(self):
        self.add_customer(CUSTOMER_PORT)
        self.add_customer(CUSTOMER_SHIP)

        def keys(keyword: str) -> list[str | None]:
            return [c.findtext("Netvisorkey") for c in self.customers(f"?keyword={keyword}")]

        self.assertEqual(keys("1234"), ["1"])
        self.assertEqual(keys("4567"), ["1"])
        self.assertEqual(keys("ship"), ["2"])
        self.assertEqual(keys("oy"), ["1"])
        self.assertEqual(keys("c-"), ["1", "2"])
        self.assertEqual(keys("rbou"), ["1"])
        self.assertEqual(keys("Kotka"), [])

    def test_changedsince_lists_customers_changed_at_or_after_the_given_local_time(self):
        self.add_customer(CUSTOMER_PORT)
        self.add_customer(CUSTOMER_SHIP)
        netvisorData.customer_changed.update({"1": "2026-11-01T08:00:00", "2": "2026-11-05T12:30:00"})

        def keys(query: str) -> list[str | None]:
            return [c.findtext("Netvisorkey") for c in self.customers(query)]

        self.assertEqual(keys("?changedsince=2026-11-05T12:30:00"), ["2"])
        self.assertEqual(keys("?changedsince=2026-11-05T12:30:01"), [])
        self.assertEqual(keys("?changedsince=2026-11-01"), ["1", "2"])
        self.assertEqual(keys("?changedsince=2026-11-02 00:00:00"), ["2"])
        self.assertEqual(keys("?keyword=Port&changedsince=2030-01-01"), ["1"])

    def test_customer_without_change_time_is_always_listed(self):
        self.add_customer(CUSTOMER_PORT)
        netvisorData.customer_changed.clear()

        self.assertEqual(len(self.customers("?changedsince=2030-01-01")), 1)

    def test_add_and_edit_record_the_change_time(self):
        self.add_customer(CUSTOMER_PORT)
        added = netvisorData.customer_changed["1"]
        netvisorData.customer_changed["1"] = "2000-01-01T00:00:00"

        self.client.post("/customer.nv?method=edit&id=1", data=CUSTOMER_SHIP)

        self.assertRegex(added, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
        self.assertGreater(netvisorData.customer_changed["1"], "2000-01-01T00:00:00")

    def test_invalid_changedsince_is_rejected(self):
        response = self.client.get("/customerlist.nv?changedsince=bogus")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            statuses(response.data),
            ["FAILED", "INVALID_DATA :: Data form incorrect:. Date is invalid: bogus Enter date in format: YYYY-MM-DD"],
        )


class CustomerErrorsTest(AppTestCase):
    """Pinned to what the Netvisor sandbox returned on 2026-11-05."""

    def assert_rejected(self, query: str, message: str):
        response = self.client.post("/customer.nv" + query, data=CUSTOMER_PORT)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(statuses(response.data), ["FAILED", "INVALID_DATA :: Data form incorrect:. " + message])
        self.assertEqual(netvisorData.customers, {})
        self.write_data.assert_not_called()

    def test_edit_of_unknown_customer(self):
        self.assert_rejected("?method=edit&id=999999999", "Cannot be updated. Customer not found, ID: 999999999")

    def test_edit_without_id(self):
        self.assert_rejected("?method=edit", "Parameter not valid: id missing")

    def test_unknown_method(self):
        self.assert_rejected("?method=bogus", "Parameter not valid: method: bogus. Allowed values: add, edit")

    def test_missing_method(self):
        self.assert_rejected("", "Parameter not valid: method missing")


class GetCustomerTest(AppTestCase):
    def get(self, query: str):
        return self.client.get("/getcustomer.nv" + query)

    def test_single_customer_is_a_bare_customer_element(self):
        self.add_customer(CUSTOMER_PORT)

        for query in ("?id=1", "?idlist=1"):
            response = self.get(query)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(statuses(response.data), ["OK"])
            root = parse(response.data)
            self.assertIsNone(root.find("Customers"), query)
            self.assertEqual(root.findtext("Customer/CustomerBaseInformation/NetvisorKey"), "1", query)

    def test_customer_details_follow_the_sandbox_structure(self):
        self.add_customer(CUSTOMER_PORT)

        customer = parse(self.get("?id=1").data).find("Customer")

        assert customer is not None
        self.assertEqual(
            [section.tag for section in customer],
            [
                "CustomerBaseInformation",
                "CustomerFinvoiceDetails",
                "CustomerDeliveryDetails",
                "CustomerContactDetails",
                "CustomerContactPersons",
                "CustomerAdditionalInformation",
            ],
        )
        base_information = customer.find("CustomerBaseInformation")
        assert base_information is not None
        base = {child.tag: child.text for child in base_information}
        self.assertEqual(
            base,
            {
                "NetvisorKey": "1",
                "InternalIdentifier": "C-1",
                "ExternalIdentifier": "1234567-8",
                "OrganizationUnitNumber": None,
                "CustomerGroupNetvisorKey": "1",
                "CustomerGroupName": "Ports",
                "Name": "Port Oy",
                "NameExtension": "Harbour",
                "StreetAddress": "Satamatie 1",
                "AdditionalStreetAddress": None,
                "City": "Kotka",
                "PostNumber": "48100",
                "Country": "FI",
                "PhoneNumber": None,
                "FaxNumber": None,
                "Email": None,
                "EmailInvoicingAddress": None,
                "HomePageUri": None,
                "IsActive": "1",
                "IsPrivateCustomer": "0",
            },
        )
        self.assertEqual(customer.xpath("string(CustomerBaseInformation/Country/@type)"), "ISO-3166")
        self.assertEqual(customer.findtext("CustomerAdditionalInformation/InvoicingLanguage"), "FI")

    def test_several_customers_are_wrapped_and_unknown_keys_skipped(self):
        self.add_customer(CUSTOMER_PORT)
        self.add_customer(CUSTOMER_SHIP)

        root = parse(self.get("?idlist=2,1,999999999").data)

        self.assertEqual(
            [key.text for key in root.iterfind("Customers/Customer/CustomerBaseInformation/NetvisorKey")], ["2", "1"]
        )
        self.assertEqual(root.findtext("Customer/CustomerBaseInformation/NetvisorKey"), None)
        one = parse(self.get("?idlist=1,999999999").data)
        self.assertEqual(one.findtext("Customer/CustomerBaseInformation/NetvisorKey"), "1")

    def test_no_known_customer_is_rejected_with_the_requested_identifiers(self):
        for query, requested in [
            ("?id=999999999", "999999999"),
            ("?idlist=999999998,999999999", "999999998,999999999"),
        ]:
            response = self.get(query)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(
                statuses(response.data),
                [
                    "FAILED",
                    f"INVALID_DATA :: Data form incorrect:. Customer not found with Netvisor identifier: {requested}",
                ],
            )


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


class RobustnessTest(AppTestCase):
    """Malformed or legacy data answers INVALID_DATA or is tolerated; it never stores half a request or a 500."""

    def test_malformed_or_incomplete_customer_is_rejected_without_storing(self):
        for body in (
            b"<root><customer>",
            b"<root><customer/></root>",
            b"<root><customer><customerbaseinformation/></customer></root>",
            b"<other/>",
        ):
            response = self.client.post("/customer.nv?method=add", data=body)
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(statuses(response.data)[0], "FAILED")
        self.assertEqual((netvisorData.customers, netvisorData.customer_count), ({}, 0))
        self.write_data.assert_not_called()

    def test_malformed_salesinvoice_is_rejected_without_storing(self):
        response = self.client.post("/salesinvoice.nv?method=add", data=b"<root><salesinvoice>")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(netvisorData.salesinvoices, {})

    def test_listing_and_invoices_tolerate_a_stored_customer_without_base_information(self):
        netvisorData.customers["1"] = None
        netvisorData.customers["2"] = {"customerbaseinformation": None}
        key = self.add_salesinvoice()

        self.assertEqual(self.client.get("/customerlist.nv?keyword=x").status_code, 200)
        self.assertEqual(len(parse(self.client.get("/customerlist.nv").data).findall("Customerlist/Customer")), 2)
        self.assertEqual(self.client.get("/getcustomer.nv?idlist=1,2").status_code, 200)
        self.assertEqual(self.client.get(f"/getsalesinvoice.nv?netvisorkey={key}").status_code, 200)

    def test_repeated_element_uses_its_first_occurrence(self):
        self.add_customer(CUSTOMER_PORT.replace(b"<name>Port Oy</name>", b"<name>Port Oy</name><name>Other</name>"))

        self.assertEqual(
            parse(self.client.get("/customerlist.nv").data).findtext("Customerlist/Customer/Name"), "Port Oy"
        )

    def test_status_update_works_without_or_with_an_attribute_less_status_element(self):
        for invoice in (
            SALESINVOICE.replace(b'<salesinvoicestatus type="netvisor">open</salesinvoicestatus>', b""),
            SALESINVOICE.replace(b' type="netvisor">open<', b">open<"),
        ):
            key = self.add_salesinvoice(invoice)
            response = self.client.post(f"/updatesalesinvoicestatus.nv?netvisorkey={key}&status=paid")
            self.assertEqual(response.status_code, 200)
            status = parse(self.client.get(f"/getsalesinvoice.nv?netvisorkey={key}").data).find(
                "SalesInvoice/InvoiceStatus"
            )
            assert status is not None
            self.assertEqual(status.get("status"), "paid")

    def test_control_characters_in_query_values_answer_invalid_data(self):
        for path in ("/getcustomer.nv?id=%00", "/updatesalesinvoicestatus.nv?netvisorkey=%01&status=open"):
            response = self.client.open(path, method="POST" if "update" in path else "GET")
            self.assertEqual(response.status_code, 400, path)
            self.assertEqual(statuses(response.data)[0], "FAILED")

    def test_edit_moves_the_business_id_mapping(self):
        self.add_customer(CUSTOMER_PORT)

        self.client.post("/customer.nv?method=edit&id=1", data=CUSTOMER_SHIP)

        self.assertEqual(netvisorData.businessIdCustomerMap, {"7654321-0": "1"})

    def test_write_is_atomic(self):
        real_write = NetvisorData.write_data.__get__(netvisorData)
        with (
            tempfile.TemporaryDirectory() as tmp,
            contextlib.chdir(tmp),
            patch.object(netvisorData, "write_data", real_write),
        ):
            self.add_customer()
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["data.json"])
            self.assertIn("1", json.loads(Path("data.json").read_text())["customers"])


if __name__ == "__main__":
    unittest.main()
