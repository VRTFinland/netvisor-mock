import base64
import json
import os
from datetime import datetime

import xmltodict
from flask import Flask, Response, request
from lxml import etree
from lxml.builder import E

from payroll_mock import payroll

app = Flask(__name__)
app.register_blueprint(payroll)


class NetvisorData:
    def __init__(self):
        data = self.read_data_json()
        self.customers = data.get("customers", {})
        self.salesinvoices = data.get("salesinvoices", {})
        self.customer_count = data.get("customersCount", 0)
        self.salesinvoice_count = data.get("salesinvoicesCount", 0)
        self.businessIdCustomerMap = data.get("businessIdCustomerMap", {})

    def read_data_json(self):
        """Create data.json -file if it doesn't exist."""

        if os.path.isfile("data.json"):
            with open("data.json") as fh:
                data = json.loads(fh.read())
                return data
        else:
            return self.reset_data()

    def reset_data(self):
        self.customers = {}
        self.salesinvoices = {}
        self.businessIdCustomerMap = {}
        self.salesinvoice_count = 0
        self.customer_count = 0

        with open("data.json", "w") as fh:
            data = {
                "customers": self.customers,
                "salesinvoices": self.salesinvoices,
                "customersCount": self.customer_count,
                "salesinvoicesCount": self.salesinvoice_count,
                "businessIdCustomerMap": self.businessIdCustomerMap,
            }
            fh.write(json.dumps(data, indent=4))
            return data

    def write_data(self):
        with open("data.json", "w") as fh:
            data = {
                "customers": self.customers,
                "salesinvoices": self.salesinvoices,
                "customersCount": self.customer_count,
                "salesinvoicesCount": self.salesinvoice_count,
                "businessIdCustomerMap": self.businessIdCustomerMap,
            }
            fh.write(json.dumps(data, indent=4))

    def add_customer(self, payload):
        self.customer_count += 1
        customer_id = str(self.customer_count)
        self.customers[customer_id] = payload.get("root").get("customer")
        business_id = payload.get("root").get("customer").get("customerbaseinformation").get("externalidentifier")
        self.businessIdCustomerMap[business_id] = customer_id
        self.write_data()
        return customer_id

    def edit_customer(self, customer_id, payload):
        self.customers[customer_id] = payload.get("root").get("customer")
        self.write_data()
        return customer_id

    def add_salesinvoice(self, payload):
        self.salesinvoice_count += 1
        salesinvoice_id = str(self.salesinvoice_count)
        self.salesinvoices[salesinvoice_id] = payload.get("root").get("salesinvoice")
        self.write_data()
        return salesinvoice_id


netvisorData = NetvisorData()
BASE_URL = os.environ.get("BASE_URL", "http://0.0.0.0:5001")


@app.route("/reset", methods=["POST"])
def reset():
    netvisorData.reset_data()
    return Response(None, status=204)


@app.route("/", methods=["GET"])
def root():
    return "Hello!"


@app.route("/customerlist.nv", methods=["GET"])
def get_customer_list():
    keyword = request.args.get("keyword")
    return generate_customerlist_response(keyword)


@app.route("/customer.nv", methods=["POST"])
def create_customer():
    payload = xmltodict.parse(request.data)

    if request.args.get("method") == "add":
        print("Adding customer")
        customer_id = netvisorData.add_customer(payload)
        return generate_inserted_data_response(customer_id)
    else:
        print("Edit customer")
        customer_id = request.args.get("id")
        netvisorData.edit_customer(customer_id, payload)
        return generate_inserted_data_response(customer_id)


@app.route("/getsalesinvoice.nv", methods=["GET"])
def get_salesinvoice():
    netvisor_key = request.args.get("netvisorkey")
    pdf_image = request.args.get("pdfimage")

    return generate_get_salesinvoice_response(netvisor_key, pdf_image)


@app.route("/salesinvoice.nv", methods=["POST"])
def post_salesinvoice():
    if request.args.get("method") == "add":
        payload = xmltodict.parse(request.data)
        salesinvoice_id = netvisorData.add_salesinvoice(payload)
        return generate_inserted_data_response(salesinvoice_id)
    else:
        return Response(None, status=204)


# Documented values for updatesalesinvoicestatus.nv; the Netvisor sandbox rejects e.g. "unsent".
SALESINVOICE_STATUSES = {"open", "overdue", "reminded", "requested", "collected", "paid", "creditloss"}


@app.route("/updatesalesinvoicestatus.nv", methods=["POST"])
def post_salesinvoicestatus():
    netvisor_key = request.args.get("netvisorkey")
    status = request.args.get("status")

    # Same order, texts and HTTP status as the Netvisor sandbox (2026-11-05, UseHTTPResponseStatusCodes=1).
    if status not in SALESINVOICE_STATUSES:
        return generate_failed_response("INVALID_DATA :: Data form incorrect:. Invalid invoice status")
    salesinvoice = netvisorData.salesinvoices.get(netvisor_key)
    if salesinvoice is None:
        return generate_failed_response(
            f"INVALID_DATA :: Data form incorrect:. No invoice found for the given ID, ID: {netvisor_key}"
        )

    salesinvoice.get("salesinvoicestatus", {})["#text"] = status
    netvisorData.write_data()
    return generate_inserted_data_response(netvisor_key)


def generate_customer_element(customer, key):
    """One customerlist.nv <Customer>; Code is the customer code imported as <internalidentifier>."""
    return E.Customer(
        E.Netvisorkey(key),
        E.Name(text_of(customer.get("name"))),
        E.Code(text_of(customer.get("internalidentifier"))),
        E.OrganisationIdentifier(text_of(customer.get("externalidentifier"))),
        E.CustomerGroupID(""),
        E.CustomerGroupName(text_of(customer.get("customergroupname"))),
        E.Uri(f"{BASE_URL}/getcustomer.nv?id={key}"),
    )


# customerlist.nv searches name, customer code, business ID and name extension for "keyword*".
CUSTOMERLIST_KEYWORD_FIELDS = ("name", "internalidentifier", "externalidentifier", "nameextension")


def matches_keyword(customer, keyword: str | None) -> bool:
    if not keyword:
        return True
    return any(text_of(customer.get(field)).startswith(keyword) for field in CUSTOMERLIST_KEYWORD_FIELDS)


def generate_customerlist_response(keyword: str | None) -> Response:
    """Create mock response for GET customerlist.nv -request."""
    customer_list = [
        generate_customer_element(value["customerbaseinformation"], key)
        for key, value in netvisorData.customers.items()
        if matches_keyword(value["customerbaseinformation"], keyword)
    ]

    response = E.Root(E.ResponseStatus(E.Status("OK"), E.TimeStamp(create_timestamp())), E.Customerlist(*customer_list))
    return Response(etree.tostring(response, pretty_print=True), content_type="text/html")


def create_timestamp():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def generate_response_status(status="OK"):
    response_status = E.ResponseStatus(E.Status(status), E.TimeStamp(create_timestamp()))
    return response_status


def generate_failed_response(message, http_status=400):
    data = E.Root(E.ResponseStatus(E.Status("FAILED"), E.Status(message), E.TimeStamp(create_timestamp())))
    return Response(etree.tostring(data), status=http_status, content_type="text/html")


# English labels for <InvoiceStatus status="...">: the documented example is Finnish ("Maksettu") and GISGRO
# requests X-Netvisor-Interface-Language EN, so these texts are an assumption; the status codes are documented.
INVOICE_STATUS_LABELS = {
    "open": "Open",
    "unsent": "Unsent",
    "paid": "Paid",
    "overdue": "Overdue",
    "reminded": "Reminded",
    "requested": "Requested",
    "collected": "Collected",
    "creditloss": "Credit loss",
    "rejected": "Rejected",
}


def text_of(value, default=""):
    """xmltodict turns an element with attributes (e.g. <salesinvoicedate format="ansi">) into a dict."""
    if isinstance(value, dict):
        value = value.get("#text")
    return default if value is None else value


def find_invoicing_customer(identifier):
    """Resolve <invoicingcustomeridentifier type="netvisor|customer">; returns (netvisor key, base information)."""
    key = text_of(identifier)
    if isinstance(identifier, dict) and identifier.get("@type") == "customer":
        for netvisor_key, customer in netvisorData.customers.items():
            if customer["customerbaseinformation"].get("internalidentifier") == key:
                return netvisor_key, customer["customerbaseinformation"]
        return "", {}
    return key, netvisorData.customers.get(key, {}).get("customerbaseinformation", {})


def generate_salesinvoice(netvisor_key, pdf_image):
    """getsalesinvoice.nv <SalesInvoice> with the documented elements the mock has data for, in documented order."""
    salesinvoice = netvisorData.salesinvoices.get(netvisor_key)
    if salesinvoice is None:
        # The Netvisor sandbox answers an unknown key with HTTP 200, Status OK and an empty <SalesInvoice />.
        return E.SalesInvoice()

    def get(field, default=""):
        return text_of(salesinvoice.get(field), default)

    customer_key, customer = find_invoicing_customer(salesinvoice.get("invoicingcustomeridentifier"))
    invoice_date = get("salesinvoicedate")
    status = get("salesinvoicestatus", "open")
    country = get("invoicingcustomercountrycode") or text_of(customer.get("country"), "FI")

    invoice_data = E.SalesInvoice(
        E.SalesInvoiceNetvisorKey(netvisor_key),
        E.SalesInvoiceNumber(get("salesinvoicenumber") or "5000001"),
        E.SalesInvoiceDate(invoice_date, format="ansi"),
        E.SalesInvoiceValueDate(get("salesinvoicevaluedate") or invoice_date, format="ansi"),
        E.SalesInvoiceDeliveryDate(get("salesinvoicedeliverydate") or invoice_date, format="ansi"),
        E.SalesInvoiceDueDate(get("salesinvoiceduedate"), format="ansi"),
        E.SalesInvoiceReferencenumber(get("salesinvoicereferencenumber")),
        E.SalesInvoiceAmount(get("salesinvoiceamount")),
        E.SellerIdentifier(get("selleridentifier"), type="name"),
        E.InvoiceStatus(INVOICE_STATUS_LABELS.get(status, status), status=status),
        E.SalesInvoiceFreeTextBeforeLines(get("salesinvoicefreetextbeforelines")),
        E.SalesInvoiceFreeTextAfterLines(get("salesinvoicefreetextafterlines")),
        E.SalesInvoiceOurReference(get("salesinvoiceourreference")),
        E.SalesInvoiceYourReference(get("salesinvoiceyourreference")),
        E.SalesInvoicePrivateComment(get("salesinvoiceprivatecomment")),
        E.SalesInvoiceAgreementIdentifier(get("salesinvoiceagreementidentifier")),
        E.InvoicingCustomerCode(text_of(customer.get("internalidentifier"))),
        E.InvoicingCustomerName(text_of(customer.get("name"))),
        E.InvoicingCustomerNameExtension(text_of(customer.get("nameextension"))),
        E.InvoicingCustomerNetvisorKey(customer_key),
        E.InvoicingCustomerOrganizationIdentifier(text_of(customer.get("externalidentifier"))),
        E.InvoicingCustomerAddressline(text_of(customer.get("streetaddress"))),
        E.InvoicingCustomerAdditionalAddressLine(text_of(customer.get("additionaladdressline"))),
        E.InvoicingCustomerPostnumber(text_of(customer.get("postnumber"))),
        E.InvoicingCustomerTown(text_of(customer.get("city"))),
        E.InvoicingCustomerCountryCode(country, type="ISO-3166"),
        E.MatchPartialPaymentsByDefault("No"),
    )

    # pdfimage: pdf = always, nopdf = never, lastsentprintservice (default) = only if sent to the print
    # service, which never happens in the mock.
    if pdf_image == "pdf":
        invoice_data.append(E.LastSentInvoicePDFBase64Data(generate_invoice_pdf()))

    return invoice_data


def generate_get_salesinvoice_response(netvisor_key, pdf_image):
    root = etree.Element("Root")
    root.append(generate_response_status())
    root.append(generate_salesinvoice(netvisor_key, pdf_image))

    return Response(etree.tostring(root, pretty_print=True), content_type="text/html")


def generate_invoice_pdf():
    with open("invoice.pdf", "rb") as invoice_pdf:
        return base64.b64encode(invoice_pdf.read()).decode("utf-8")


def generate_inserted_data_response(identifier):
    data = E.Root(
        E.ResponseStatus(E.Status("OK"), E.TimeStamp(create_timestamp())),
        E.Replies(E.InsertedDataIdentifier(identifier)),
    )

    return Response(etree.tostring(data), content_type="text/html")


if __name__ == "__main__":
    app.run()
