import base64
import json
import os

import xmltodict
from lxml import etree
from lxml.builder import E
from datetime import datetime

from flask import Flask, request, Response

app = Flask(__name__)


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
            with open('data.json', 'r') as fh:
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

        with open('data.json', 'w') as fh:
            data = {
                "customers": self.customers,
                "salesinvoices": self.salesinvoices,
                "customersCount": self.customer_count,
                "salesinvoicesCount": self.salesinvoice_count,
                "businessIdCustomerMap": self.businessIdCustomerMap
            }
            fh.write(json.dumps(data, indent=4))
            return data

    def write_data(self):
        with open('data.json', 'w') as fh:
            data = {
                "customers": self.customers,
                "salesinvoices": self.salesinvoices,
                "customersCount": self.customer_count,
                "salesinvoicesCount": self.salesinvoice_count,
                "businessIdCustomerMap": self.businessIdCustomerMap
            }
            fh.write(json.dumps(data, indent=4))

    def add_customer(self, payload, customer_id=None):
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


def generate_customer_element(customer, key):
    return E.Customer(
        E.Netvisorkey(key),
        E.Name(customer.get("name", "")),
        E.Code(customer.get("code", "")),
        E.OrganisationIdentifier(customer.get("externalidentifier", "")),
        E.Uri(f"{BASE_URL}/getcustomer.nv?id={key}")
    )


def generate_customerlist_response(keyword: str) -> str:
    """
    Create mock response for GET customerlist.nv -request.

    :rtype string:
    :return: XML-response as string.
    """
    customer_list = []

    for key, value in netvisorData.customers.items():
        if not keyword:
            customer_list.append(
                generate_customer_element(value.get("customerbaseinformation"), key)
            )
        else:
            if value.get("customerbaseinformation").get("externalidentifier") == keyword:
                customer_list.append(
                    generate_customer_element(value.get("customerbaseinformation"), key)
                )

    response = E.Root(
        E.ResponseStatus(
            E.Status("OK"),
            E.TimeStamp(create_timestamp())
        ),
        E.Customerlist()
    )
    response.find("Customerlist").extend(customer_list)
    xml_response = etree.tostring(response, pretty_print=True)
    print(xml_response)

    return Response(xml_response, content_type="text/html")


def create_timestamp():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def create_date():
    return datetime.now().strftime("%Y-%m-%d")


def generate_response_status(status="OK"):
    response_status = E.ResponseStatus(
        E.Status(status),
        E.TimeStamp(create_timestamp())
    )
    return response_status


def create_ansi_date(element_name, value=create_date()):
    element = etree.SubElement(element_name, value)
    element.set("format", "ansi")
    element.text = value
    return element


def get_salesinvoice_amount(salesinvoice):
    return "12345"


def generate_salesinvoice(netvisor_key, pdf_image):
    salesinvoice = netvisorData.salesinvoices.get(netvisor_key)
    get = salesinvoice.get
    customer_id = salesinvoice.get("invoicingcustomeridentifier").get("#text")
    customer = netvisorData.customers.get(customer_id).get("customerbaseinformation")

    invoice_data = (
        E.SalesInvoice(
            E.SalesInvoiceNetvisorKey(netvisor_key),
            E.SalesInvoiceNumber("5000001"),
            E.SalesInvoiceDate(get("salesinvoicedate", "")),
            E.SalesInvoiceValueDate(get("salesinvoicevaluedate", "")),
            E.SalesInvoiceDeliveryDate(get("salesinvoicedate", "")),
            E.SalesInvoiceDueDate(get("salesinvoiceduedate", "")),
            E.SalesInvoiceReferenceNumber(get("salesinvoicereferencenumber", "")),
            E.SalesInvoiceAmount(get_salesinvoice_amount(salesinvoice)),
            E.SellerIdentifier(get("selleridentifier", ""), type="name"),
            E.InvoiceStatus(get("invoicestatus", "OPEN")),
            E.SalesInvoiceFreeTextBeforeLines(""),
            E.SalesInvoiceFreeTextAfterLines(""),
            E.SalesInvoiceOurReference(get("salesinvoiceourreference", "")),
            E.SalesInvoiceYourReference(get("salesinvoiceyourreference", "")),
            E.SalesInvoicePrivateComment(""),
            E.SalesInvoiceAgreementIdentifier(""),
            E.InvoicingCustomerName(customer.get("name", "")),
            E.InvoicingCustomerNameExtension(customer.get("invoicingcustomernameextension", "")),
            E.InvoicingCustomerNetvisorKey(customer_id),
            E.InvoicingCustomerOrganisationIdentifier(customer.get("externalidentifier", "")),
            E.InvoicingCustomerAddressLine(customer.get("streetaddress", "")),
            E.InvoicingCustomerAdditionalAddressLine(customer.get("additionaladdressline", "")),
            E.InvoicingCustomerPostnumber(customer.get("postnumber", "")),
            E.InvoicingCustomerTown(customer.get("city")),
            E.InvoicingCustomerCountryCode("FINLAND"),
            E.MatchPartialPaymentsByDefault("No"),
        )
    )

    if pdf_image:
        invoice_data.append(E.LastSentInvoicePDFBase64Data(generate_invoice_pdf()))

    return invoice_data


def generate_get_salesinvoice_response(netvisor_key, pdf_image):
    root = etree.Element("Root")
    root.append(generate_response_status())
    root.append(generate_salesinvoice(netvisor_key, pdf_image))

    return Response(etree.tostring(root, pretty_print=True), content_type="text/html")


def generate_invoice_pdf():
    with open("invoice.pdf", "rb") as invoice_pdf:
        return base64.b64encode(invoice_pdf.read()).decode('utf-8')


def generate_inserted_data_response(identifier):
    data = E.Root(
        E.ResponseStatus(
            E.Status("OK"),
            E.TimeStamp(create_timestamp())
        ),
        E.Replies(
            E.InsertedDataIdentifier(identifier)
        )
    )

    return Response(etree.tostring(data), content_type="text/html")


if __name__ == '__main__':
    app.run()
