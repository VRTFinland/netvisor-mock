import base64
import functools
import json
import os
import re
import threading
from datetime import date, datetime
from xml.parsers.expat import ExpatError
from zoneinfo import ZoneInfo

import xmltodict
from flask import Flask, Response, request
from lxml import etree
from lxml.builder import E

from payroll_mock import payroll

app = Flask(__name__)
app.register_blueprint(payroll)


# Netvisor's local time; GISGRO sends changedsince in Europe/Helsinki.
NETVISOR_TIMEZONE = ZoneInfo("Europe/Helsinki")


def netvisor_now() -> str:
    return datetime.now(NETVISOR_TIMEZONE).strftime("%Y-%m-%dT%H:%M:%S")


class NetvisorData:
    def __init__(self):
        data = self.read_data_json()
        self.customers = data.get("customers", {})
        self.salesinvoices = data.get("salesinvoices", {})
        self.customer_count = data.get("customersCount", 0)
        self.salesinvoice_count = data.get("salesinvoicesCount", 0)
        self.businessIdCustomerMap = data.get("businessIdCustomerMap", {})
        # Netvisor-local change time per customer key; customers stored before it existed have none.
        self.customer_changed = data.get("customerChangedTimes", {})

    def read_data_json(self):
        """Create data.json -file if it doesn't exist."""
        if os.path.isfile("data.json"):
            with open("data.json") as fh:
                return json.loads(fh.read())
        return self.reset_data()

    def snapshot(self):
        return {
            "customers": self.customers,
            "salesinvoices": self.salesinvoices,
            "customersCount": self.customer_count,
            "salesinvoicesCount": self.salesinvoice_count,
            "businessIdCustomerMap": self.businessIdCustomerMap,
            "customerChangedTimes": self.customer_changed,
        }

    def reset_data(self):
        self.customers = {}
        self.salesinvoices = {}
        self.businessIdCustomerMap = {}
        self.customer_changed = {}
        self.salesinvoice_count = 0
        self.customer_count = 0
        self.write_data()
        return self.snapshot()

    def write_data(self):
        # Write and rename, so an interrupted write never leaves a truncated data.json behind.
        with open("data.json.tmp", "w") as fh:
            fh.write(json.dumps(self.snapshot(), indent=4))
        os.replace("data.json.tmp", "data.json")

    def _map_business_id(self, customer_id, customer):
        self.businessIdCustomerMap = {
            business_id: key for business_id, key in self.businessIdCustomerMap.items() if key != customer_id
        }
        business_id = text_of(base_information(customer).get("externalidentifier"))
        if business_id:
            self.businessIdCustomerMap[business_id] = customer_id

    def add_customer(self, customer):
        self.customer_count += 1
        customer_id = str(self.customer_count)
        self.customers[customer_id] = customer
        self._map_business_id(customer_id, customer)
        self.customer_changed[customer_id] = netvisor_now()
        self.write_data()
        return customer_id

    def edit_customer(self, customer_id, customer):
        self.customers[customer_id] = customer
        self._map_business_id(customer_id, customer)
        self.customer_changed[customer_id] = netvisor_now()
        self.write_data()
        return customer_id

    def add_salesinvoice(self, salesinvoice):
        self.salesinvoice_count += 1
        salesinvoice_id = str(self.salesinvoice_count)
        self.salesinvoices[salesinvoice_id] = salesinvoice
        self.write_data()
        return salesinvoice_id


netvisorData = NetvisorData()
BASE_URL = os.environ.get("BASE_URL", "http://0.0.0.0:5001")
# The dev server is threaded; every invoicing request reads or writes netvisorData under this lock.
_data_lock = threading.RLock()


def synchronized(view):
    @functools.wraps(view)
    def locked(*args, **kwargs):
        with _data_lock:
            return view(*args, **kwargs)

    return locked


def parse_resource(resource, required_section=None):
    """The <root><resource> element of the request body as xmltodict data, or a FAILED response."""
    try:
        payload = xmltodict.parse(request.data)
    except ExpatError:
        return None, generate_failed_response("INVALID_DATA :: Data form incorrect:. Invalid XML document")
    root_element = payload.get("root") if isinstance(payload, dict) else None
    data = root_element.get(resource) if isinstance(root_element, dict) else None
    if not isinstance(data, dict) or (required_section and not isinstance(data.get(required_section), dict)):
        return None, generate_failed_response(
            f"INVALID_DATA :: Data form incorrect:. Missing {required_section or resource}"
        )
    return data, None


@app.route("/reset", methods=["POST"])
@synchronized
def reset():
    netvisorData.reset_data()
    return Response(None, status=204)


@app.route("/", methods=["GET"])
def root():
    return "Hello!"


@app.route("/customerlist.nv", methods=["GET"])
@synchronized
def get_customer_list():
    keyword = request.args.get("keyword")
    changed_since = None
    # Netvisor ignores changedsince when a keyword is given.
    if not keyword and request.args.get("changedsince"):
        changed_since = parse_changed_since(request.args["changedsince"])
        if changed_since is None:
            return generate_failed_response(
                "INVALID_DATA :: Data form incorrect:. "
                f"Date is invalid: {request.args['changedsince']} Enter date in format: YYYY-MM-DD"
            )
    return generate_customerlist_response(keyword, changed_since)


def parse_changed_since(value: str) -> str | None:
    """Netvisor-local "YYYY-MM-DDThh:mm:ss" for the accepted changedsince forms, or None when invalid."""
    for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, pattern).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass
    try:
        return date.fromisoformat(value).strftime("%Y-%m-%dT00:00:00")
    except ValueError:
        return None


@app.route("/customer.nv", methods=["POST"])
@synchronized
def create_customer():
    # Texts as the Netvisor sandbox answered on 2026-11-05; "method missing" follows the "id missing" wording.
    method = request.args.get("method")
    if not method:
        return generate_failed_response("INVALID_DATA :: Data form incorrect:. Parameter not valid: method missing")
    if method not in {"add", "edit"}:
        return generate_failed_response(
            f"INVALID_DATA :: Data form incorrect:. Parameter not valid: method: {method}. Allowed values: add, edit"
        )
    customer_id = request.args.get("id")
    if method == "edit" and not customer_id:
        return generate_failed_response("INVALID_DATA :: Data form incorrect:. Parameter not valid: id missing")
    if method == "edit" and customer_id not in netvisorData.customers:
        return generate_failed_response(
            f"INVALID_DATA :: Data form incorrect:. Cannot be updated. Customer not found, ID: {customer_id}"
        )

    customer, error = parse_resource("customer", "customerbaseinformation")
    if error:
        return error
    if method == "add":
        customer_id = netvisorData.add_customer(customer)
    else:
        netvisorData.edit_customer(customer_id, customer)
    return generate_inserted_data_response(customer_id)


@app.route("/getcustomer.nv", methods=["GET"])
@synchronized
def get_customer():
    requested = request.args.get("idlist") or request.args.get("id")
    if not requested:
        return generate_failed_response("INVALID_DATA :: Data form incorrect:. Parameter not valid: id missing")
    keys = list(dict.fromkeys(key.strip() for key in requested.split(",")))
    found = [key for key in keys if key in netvisorData.customers]
    # Sandbox 2026-11-05: unknown keys are skipped; none found is 400, one is a bare <Customer>, more are wrapped.
    if not found:
        return generate_failed_response(
            f"INVALID_DATA :: Data form incorrect:. Customer not found with Netvisor identifier: {requested}"
        )
    customers = [generate_customer_details(key, netvisorData.customers[key]) for key in found]
    body = customers[0] if len(customers) == 1 else E.Customers(*customers)
    response = E.Root(generate_response_status(), body)
    return Response(etree.tostring(response, pretty_print=True), content_type="text/html")


@app.route("/getsalesinvoice.nv", methods=["GET"])
@synchronized
def get_salesinvoice():
    netvisor_key = request.args.get("netvisorkey")
    pdf_image = request.args.get("pdfimage")

    return generate_get_salesinvoice_response(netvisor_key, pdf_image)


@app.route("/salesinvoice.nv", methods=["POST"])
@synchronized
def post_salesinvoice():
    if request.args.get("method") != "add":
        return Response(None, status=204)
    salesinvoice, error = parse_resource("salesinvoice")
    if error:
        return error
    return generate_inserted_data_response(netvisorData.add_salesinvoice(salesinvoice))


# Documented values for updatesalesinvoicestatus.nv; the Netvisor sandbox rejects e.g. "unsent".
SALESINVOICE_STATUSES = {"open", "overdue", "reminded", "requested", "collected", "paid", "creditloss"}


@app.route("/updatesalesinvoicestatus.nv", methods=["POST"])
@synchronized
def post_salesinvoicestatus():
    netvisor_key = request.args.get("netvisorkey")
    status = request.args.get("status")

    # Same order, texts and HTTP status as the Netvisor sandbox (2026-11-05, UseHTTPResponseStatusCodes=1).
    if status not in SALESINVOICE_STATUSES:
        return generate_failed_response("INVALID_DATA :: Data form incorrect:. Invalid invoice status")
    salesinvoice = netvisorData.salesinvoices.get(netvisor_key)
    if not isinstance(salesinvoice, dict):
        return generate_failed_response(
            f"INVALID_DATA :: Data form incorrect:. No invoice found for the given ID, ID: {netvisor_key}"
        )

    # Keep the imported attributes (e.g. type="netvisor"); the element may be missing or attribute-less.
    previous = salesinvoice.get("salesinvoicestatus")
    salesinvoice["salesinvoicestatus"] = {**(previous if isinstance(previous, dict) else {}), "#text": status}
    netvisorData.write_data()
    return generate_inserted_data_response(netvisor_key)


# Netvisor's default customer group, as the sandbox lists it for every customer.
DEFAULT_CUSTOMER_GROUP = ("1", "Asiakasryhmä 1")


def customer_code(customer, key):
    """The imported <internalidentifier>; the sandbox shows the Netvisor key as code when none was given."""
    return text_of(customer.get("internalidentifier")) or key


def generate_customer_element(customer, key):
    """One customerlist.nv <Customer>, in the sandbox's element order."""
    return E.Customer(
        E.Netvisorkey(key),
        E.Name(text_of(customer.get("name"))),
        E.Code(customer_code(customer, key)),
        E.OrganisationIdentifier(text_of(customer.get("externalidentifier"))),
        E.CustomerGroupID(DEFAULT_CUSTOMER_GROUP[0]),
        E.CustomerGroupName(text_of(customer.get("customergroupname")) or DEFAULT_CUSTOMER_GROUP[1]),
        E.Uri(f"{BASE_URL}/getcustomer.nv?id={key}"),
    )


def generate_customer_details(key, stored):
    """One getcustomer.nv <Customer> with the sections and element names the sandbox returned on 2026-11-05."""
    base = base_information(stored)
    finvoice = base_information(stored, "customerfinvoicedetails")
    delivery = base_information(stored, "customerdeliverydetails")
    contact = base_information(stored, "customercontactdetails")
    additional = base_information(stored, "customeradditionalinformation")

    def fields(source, mapping):
        return [getattr(E, tag)(text_of(source.get(field))) for tag, field in mapping]

    return E.Customer(
        E.CustomerBaseInformation(
            E.NetvisorKey(key),
            E.InternalIdentifier(customer_code(base, key)),
            E.ExternalIdentifier(text_of(base.get("externalidentifier"))),
            E.OrganizationUnitNumber(text_of(base.get("organizationunitnumber"))),
            E.CustomerGroupNetvisorKey(DEFAULT_CUSTOMER_GROUP[0]),
            E.CustomerGroupName(text_of(base.get("customergroupname")) or DEFAULT_CUSTOMER_GROUP[1]),
            *fields(
                base,
                [
                    ("Name", "name"),
                    ("NameExtension", "nameextension"),
                    ("StreetAddress", "streetaddress"),
                    ("AdditionalStreetAddress", "additionaladdressline"),
                    ("City", "city"),
                    ("PostNumber", "postnumber"),
                ],
            ),
            E.Country(text_of(base.get("country")) or "FI", type="ISO-3166"),
            *fields(
                base,
                [
                    ("PhoneNumber", "phonenumber"),
                    ("FaxNumber", "faxnumber"),
                    ("Email", "email"),
                    ("EmailInvoicingAddress", "emailinvoicingaddress"),
                    ("HomePageUri", "homepageuri"),
                ],
            ),
            E.IsActive(text_of(base.get("isactive")) or "1"),
            E.IsPrivateCustomer(text_of(base.get("isprivatecustomer")) or "0"),
        ),
        E.CustomerFinvoiceDetails(
            *fields(finvoice, [("FinvoiceAddress", "finvoiceaddress"), ("FinvoiceRouterCode", "finvoiceroutercode")])
        ),
        E.CustomerDeliveryDetails(
            *fields(
                delivery,
                [
                    ("DeliveryName", "deliveryname"),
                    ("DeliveryStreetAddress", "deliverystreetaddress"),
                    ("DeliveryCity", "deliverycity"),
                    ("DeliveryPostNumber", "deliverypostnumber"),
                ],
            )
        ),
        E.CustomerContactDetails(
            *fields(
                contact,
                [
                    ("ContactPerson", "contactperson"),
                    ("ContactPersonEmail", "contactpersonemail"),
                    ("ContactPersonPhone", "contactpersonphone"),
                ],
            )
        ),
        E.CustomerContactPersons(),
        E.CustomerAdditionalInformation(
            *fields(
                additional,
                [
                    ("Comment", "comment"),
                    ("CustomerAgreementIdentifier", "customeragreementidentifier"),
                    ("ReferenceNumber", "customerreferencenumber"),
                ],
            ),
            E.UseCreditorReferenceNumber("0"),
            E.UseOrderReferenceNumber("0"),
            *fields(
                additional,
                [
                    ("YourDefaultReference", "yourdefaultreference"),
                    ("DefaultTextBeforeInvoiceLines", "defaulttextbeforeinvoicelines"),
                    ("DefaultTextAfterInvoiceLines", "defaulttextafterinvoicelines"),
                ],
            ),
            E.TaxHandlingType("countrygroup"),
            E.BalanceLimit(),
            E.DiscountPercentage(),
            E.PriceGroup(),
            E.FactoringAccount(),
            E.InvoicingLanguage(text_of(additional.get("invoicinglanguage")) or "FI", type="ISO-3166"),
            E.EUStandardFinvoice("0"),
            E.InCollectionProhibitation("0"),
        ),
    )


# customerlist.nv keyword: the sandbox matches a case-insensitive substring of name, customer code, business ID
# and name extension (e.g. "ridian", "meri" and "4567" all find "Merenkulku Meridian Ab", 1234567-8).
CUSTOMERLIST_KEYWORD_FIELDS = ("name", "internalidentifier", "externalidentifier", "nameextension")


def matches_keyword(customer, key, keyword: str | None) -> bool:
    if not keyword:
        return True
    values = [text_of(customer.get(field)) for field in CUSTOMERLIST_KEYWORD_FIELDS] + [customer_code(customer, key)]
    return any(keyword.casefold() in value.casefold() for value in values)


def changed_since_matches(key, changed_since: str | None) -> bool:
    if changed_since is None:
        return True
    changed = netvisorData.customer_changed.get(key)
    # A customer stored before change times were recorded is treated as changed, so a sync never misses it.
    return changed is None or changed >= changed_since


def generate_customerlist_response(keyword: str | None, changed_since: str | None = None) -> Response:
    """Create mock response for GET customerlist.nv -request."""
    customer_list = [
        generate_customer_element(base_information(value), key)
        for key, value in netvisorData.customers.items()
        if matches_keyword(base_information(value), key, keyword) and changed_since_matches(key, changed_since)
    ]

    response = E.Root(E.ResponseStatus(E.Status("OK"), E.TimeStamp(create_timestamp())), E.Customerlist(*customer_list))
    return Response(etree.tostring(response, pretty_print=True), content_type="text/html")


def create_timestamp():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def generate_response_status(status="OK"):
    response_status = E.ResponseStatus(E.Status(status), E.TimeStamp(create_timestamp()))
    return response_status


# Characters XML 1.0 cannot carry; a query value such as id=%00 must not turn an error answer into a 500.
_NON_XML_CHARACTERS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def generate_failed_response(message, http_status=400):
    message = _NON_XML_CHARACTERS.sub("", message)
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
    """Element text from xmltodict data: an element with attributes (e.g. <salesinvoicedate format="ansi">) is a
    dict and a repeated element is a list, whose first occurrence is used."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("#text")
    return default if value is None else str(value)


def base_information(customer, section="customerbaseinformation"):
    """A section of a stored customer (by default <customerbaseinformation>), or {} when it is missing."""
    value = customer.get(section) if isinstance(customer, dict) else None
    return value if isinstance(value, dict) else {}


def find_invoicing_customer(identifier):
    """Resolve <invoicingcustomeridentifier type="netvisor|customer">; returns (netvisor key, base information)."""
    key = text_of(identifier)
    if isinstance(identifier, dict) and identifier.get("@type") == "customer":
        for netvisor_key, customer in netvisorData.customers.items():
            if text_of(base_information(customer).get("internalidentifier")) == key:
                return netvisor_key, base_information(customer)
        return "", {}
    return key, base_information(netvisorData.customers.get(key))


def generate_salesinvoice(netvisor_key, pdf_image):
    """getsalesinvoice.nv <SalesInvoice> with the documented elements the mock has data for, in documented order."""
    salesinvoice = netvisorData.salesinvoices.get(netvisor_key)
    if not isinstance(salesinvoice, dict):
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
