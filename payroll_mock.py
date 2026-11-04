import hashlib
import hmac
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from threading import Lock

from flask import Blueprint, Response, jsonify, request
from lxml import etree

payroll = Blueprint("payroll", __name__)
_lock = Lock()
_contexts = {}
_scenarios = {"success", "business_rejection", "auth_rejection", "ambiguous", "server_failure", "timeout"}
_partner_id = os.environ.get("NETVISOR_MOCK_PARTNER_ID", "gisgro-test-partner")
_partner_key = os.environ.get("NETVISOR_MOCK_PARTNER_KEY", "gisgro-test-partner-key")
_sender = os.environ.get("NETVISOR_MOCK_SENDER", "GISGRO")
_default_user_key = os.environ.get("NETVISOR_MOCK_USER_KEY", "test-key")
_auth_headers = (
    "X-Netvisor-Authentication-Sender",
    "X-Netvisor-Authentication-CustomerId",
    "X-Netvisor-Authentication-PartnerId",
    "X-Netvisor-Authentication-Timestamp",
    "X-Netvisor-Authentication-TimestampUnix",
    "X-Netvisor-Authentication-TransactionId",
    "X-Netvisor-Authentication-MAC",
    "X-Netvisor-Authentication-MACHashCalculationAlgorithm",
    "X-Netvisor-Authentication-UseHTTPResponseStatusCodes",
    "X-Netvisor-Interface-Language",
    "X-Netvisor-Organisation-ID",
)


def _context(org_id):
    return _contexts.setdefault(
        org_id,
        {
            "scenario": "success",
            "ratioNumbers": ["1234"],
            "userKey": _default_user_key,
            "sender": _sender,
            "rejectionMessage": None,
            "employees": [],
            "requests": [],
        },
    )


def _xml(status="OK", message=None, identifier=None, http_status=200):
    root = etree.Element("Root")
    response_status = etree.SubElement(root, "ResponseStatus")
    etree.SubElement(response_status, "Status").text = status
    if message:
        etree.SubElement(response_status, "Status").text = message
    etree.SubElement(response_status, "TimeStamp").text = "09.10.2026 12:00:00"
    if identifier:
        replies = etree.SubElement(root, "Replies")
        etree.SubElement(replies, "InsertedDataIdentifier").text = identifier
    return Response(etree.tostring(root), status=http_status, content_type="application/xml")


def _invalid(message):
    return _xml("FAILED", "INVALID_DATA :: " + message, http_status=400)


def _authenticated():
    headers = request.headers
    if any(not headers.get(name, "").strip() for name in _auth_headers):
        return False
    if headers["X-Netvisor-Authentication-MACHashCalculationAlgorithm"] != "HMACSHA256":
        return False
    if not re.fullmatch(r"[a-fA-F0-9]{64}", headers["X-Netvisor-Authentication-MAC"]):
        return False
    if not headers["X-Netvisor-Authentication-TimestampUnix"].isdigit():
        return False
    if headers["X-Netvisor-Authentication-UseHTTPResponseStatusCodes"] != "1":
        return False
    if headers["X-Netvisor-Interface-Language"] != "EN":
        return False
    if headers["X-Netvisor-Authentication-PartnerId"] != _partner_id:
        return False
    org_id = headers["X-Netvisor-Organisation-ID"]
    with _lock:
        context = _context(org_id)
        user_key = context["userKey"]
        sender = context["sender"]
    if headers["X-Netvisor-Authentication-Sender"] != sender:
        return False
    signed_values = (
        request.url,
        sender,
        headers["X-Netvisor-Authentication-CustomerId"],
        headers["X-Netvisor-Authentication-Timestamp"],
        "EN",
        org_id,
        headers["X-Netvisor-Authentication-TransactionId"],
        headers["X-Netvisor-Authentication-TimestampUnix"],
        user_key,
        _partner_key,
    )
    try:
        secret = (user_key + "&" + _partner_key).encode("iso-8859-15")
        message = "&".join(signed_values).encode("iso-8859-15")
    except UnicodeEncodeError:
        return False
    expected = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, headers["X-Netvisor-Authentication-MAC"].lower())


def _auth_failure():
    return _xml("FAILED", "AUTHENTICATION_FAILED :: Integration credentials are invalid", http_status=401)


def _query_is(expected):
    return set(request.args.keys()) == set(expected) and all(
        request.args.getlist(key) == [value] for key, value in expected.items()
    )


def _required(parent, name, attribute=None, allowed=None):
    element = parent.find(name)
    if element is None or not (element.text or "").strip():
        raise ValueError("Missing " + name)
    if attribute and (not element.get(attribute) or (allowed and element.get(attribute) not in allowed)):
        raise ValueError("Invalid " + name + " " + attribute)
    if allowed and not attribute and element.text.strip() not in allowed:
        raise ValueError("Invalid " + name)
    return element


def _number(text):
    try:
        if not Decimal(text).is_finite():
            raise ValueError("Non-finite amount")
    except InvalidOperation:
        raise ValueError("Invalid amount") from None


def _validate_dimensions(line):
    for dimension in line.findall("dimension"):
        _required(dimension, "dimensionname")
        _required(dimension, "dimensionitem")


def _parse_resource(resource):
    """The single <root><resource> child of the request body, parsed without entities or network access."""
    if len(request.data) > 1_000_000 or b"<!DOCTYPE" in request.data.upper():
        raise ValueError("Invalid XML document")
    try:
        root = etree.fromstring(request.data, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        raise ValueError("Invalid XML document") from None
    if root.tag != "root" or len(root) != 1 or root[0].tag != resource:
        raise ValueError("Invalid resource root")
    return root[0]


def _validate_time_resource(resource):
    data = _parse_resource(resource)
    date_element = _required(data, "date", "format", {"ansi"})
    if resource == "payrollperiodcollector" and set(date_element.attrib) != {"format"}:
        raise ValueError("Invalid collector date attributes")
    try:
        date.fromisoformat(date_element.text.strip())
    except ValueError:
        raise ValueError("Invalid date") from None
    _required(data, "employeeidentifier", "type", {"number", "personalidentificationnumber", "netvisorkey"})
    if resource == "payrollperiodcollector":
        lines = data.findall("payrollratioline")
        if not lines:
            raise ValueError("Missing payrollratioline")
        for line in lines:
            _number(_required(line, "amount").text)
            _number(_required(line, "payrollratio", "type", {"number"}).text)
            _validate_dimensions(line)
    else:
        if date_element.get("method", "replace") not in {"replace", "increment"}:
            raise ValueError("Invalid date method")
        lines = data.findall("workdayhour")
        if not lines:
            raise ValueError("Missing workdayhour")
        for line in lines:
            _number(_required(line, "hours").text)
            _number(_required(line, "collectorratio", "type", {"number"}).text)
            _required(line, "acceptancestatus", allowed={"accepted", "confirmed"})
            _required(line, "description")
            _validate_dimensions(line)


def _record_post(org_id, body):
    safe_query = {
        name: request.args.get(name) if name in {"method", "useonlyexistingdimensions"} else "<redacted>"
        for name in request.args
    }
    entry = {
        "path": request.path,
        "query": safe_query,
        "body": body,
        "headerNames": sorted(name for name in _auth_headers if request.headers.get(name)),
    }
    with _lock:
        context = _context(org_id)
        context["requests"].append(entry)
        return context["scenario"], len(context["requests"])


@payroll.route("/__test__/payroll/<org_id>/scenario", methods=["PUT"])
def configure_scenario(org_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) - {
        "scenario",
        "ratioNumbers",
        "userKey",
        "sender",
        "rejectionMessage",
    }:
        return jsonify(error="Invalid scenario configuration"), 400
    if payload.get("scenario") not in _scenarios:
        return jsonify(error="Unknown scenario"), 400
    ratio_numbers = payload.get("ratioNumbers", ["1234"])
    if not isinstance(ratio_numbers, list) or any(
        not isinstance(number, str) or not number.strip() for number in ratio_numbers
    ):
        return jsonify(error="Invalid ratioNumbers"), 400
    user_key = payload.get("userKey", _default_user_key)
    if not isinstance(user_key, str) or not user_key.strip():
        return jsonify(error="Invalid userKey"), 400
    sender = payload.get("sender", _sender)
    if not isinstance(sender, str) or not sender.strip():
        return jsonify(error="Invalid sender"), 400
    rejection_message = payload.get("rejectionMessage")
    if rejection_message is not None and (not isinstance(rejection_message, str) or not rejection_message.strip()):
        return jsonify(error="Invalid rejectionMessage"), 400
    with _lock:
        context = _context(org_id)
        context["scenario"] = payload["scenario"]
        context["ratioNumbers"] = ratio_numbers
        context["userKey"] = user_key
        context["sender"] = sender
        context["rejectionMessage"] = rejection_message
    return jsonify(scenario=payload["scenario"], ratioNumbers=ratio_numbers)


@payroll.route("/__test__/payroll/<org_id>/requests", methods=["GET"])
def requests_for_context(org_id):
    with _lock:
        requests = list(_context(org_id)["requests"])
    return jsonify(count=len(requests), requests=requests)


@payroll.route("/__test__/payroll/<org_id>", methods=["DELETE"])
def clear_context(org_id):
    with _lock:
        _contexts.pop(org_id, None)
    return Response(status=204)


@payroll.route("/payrollratiolist.nv", methods=["GET"])
def payroll_ratio_list():
    if not _authenticated():
        return _auth_failure()
    if not _query_is({"source": "collectorratiolines"}):
        return _invalid("Unsupported payroll ratio list query")
    with _lock:
        numbers = list(_context(request.headers["X-Netvisor-Organisation-ID"])["ratioNumbers"])
    root = etree.Element("Root")
    status = etree.SubElement(root, "ResponseStatus")
    etree.SubElement(status, "Status").text = "OK"
    etree.SubElement(status, "TimeStamp").text = "09.10.2026 12:00:00"
    ratios = etree.SubElement(root, "PayrollRatios")
    for index, number in enumerate(numbers, 1):
        ratio = etree.SubElement(ratios, "PayrollRatio")
        etree.SubElement(ratio, "Identifier").text = str(index)
        etree.SubElement(ratio, "Source").text = "collectorratiolines"
        etree.SubElement(ratio, "RatioNumber").text = number
    return Response(etree.tostring(root), content_type="application/xml")


def _payroll_post(query_error, parse, accept, record_body=None):
    """Shared POST flow: journal, authenticate, check the query and body, apply the scenario, then accept."""
    org_id = request.headers.get("X-Netvisor-Organisation-ID")
    if not org_id:
        return _auth_failure()
    body = request.get_data(as_text=True)
    scenario, sequence = _record_post(org_id, record_body(body) if record_body else body)
    if not _authenticated() or scenario == "auth_rejection":
        return _auth_failure()
    if request.mimetype not in {"application/xml", "text/xml"}:
        return _invalid("Expected XML content type")
    error = query_error()
    if error:
        return _invalid(error)
    try:
        data = parse()
    except ValueError as error:
        return _invalid(str(error))
    if scenario == "business_rejection":
        with _lock:
            message = _context(org_id)["rejectionMessage"]
        return _invalid(message or "Payroll line was rejected")
    if scenario == "ambiguous":
        return Response("<Root><ResponseStatus>", content_type="application/xml")
    if scenario == "server_failure":
        return _xml("FAILED", "TECHNICAL_ERROR :: Provider unavailable", http_status=503)
    if scenario == "timeout":
        return _xml("FAILED", "TECHNICAL_ERROR :: Request timed out", http_status=408)
    return accept(org_id, data, sequence)


def _dimension_query_error():
    expected_query = {"useonlyexistingdimensions": request.args.get("useonlyexistingdimensions")}
    if expected_query["useonlyexistingdimensions"] not in {"0", "1"}:
        return "Invalid dimension safety option"
    if not _query_is(expected_query):
        return "Unsupported payroll query"
    return None


def _time_post(resource):
    def accept(_org_id, _data, sequence):
        return _xml(identifier=f"mock-{sequence}" if resource == "payrollperiodcollector" else None)

    return _payroll_post(_dimension_query_error, lambda: _validate_time_resource(resource), accept)


@payroll.route("/payrollperiodcollector.nv", methods=["POST"])
def payroll_period_collector():
    return _time_post("payrollperiodcollector")


@payroll.route("/workday.nv", methods=["POST"])
def workday():
    return _time_post("workday")


# --- Employees (getemployees.nv, employee.nv) -------------------------------------------------------------
# Behaviour pinned to the Netvisor sandbox on 2026-11-04 (VRTfront docs/research/2026-11-04-netvisor-employee-
# sync-api.md): a successful add answers OK without InsertedDataIdentifier, duplicates are 400 INVALID_DATA, and
# getemployees.nv leaves ended employments out unless includeendedemployments=1.

_EMPLOYEE_FIELDS = {"netvisorKey", "employeeNumber", "firstName", "lastName", "employmentStatus", "personalId"}
_IDENTITY_CODE = re.compile(r"(<employeeidentifier\b[^>]*>)[^<]*(</employeeidentifier>)")


def _masked_identity_code(code):
    """Netvisor shows an identity code as its date part only, e.g. 010180-xxxx."""
    return code[:7] + "xxxx"


def _redact_identity_codes(body):
    return _IDENTITY_CODE.sub(r"\1<redacted>\2", body)


def _seeded_employee(item, index):
    if not isinstance(item, dict) or set(item) - _EMPLOYEE_FIELDS:
        raise ValueError(f"Invalid employee {index}")
    employee = {
        "netvisorKey": item.get("netvisorKey"),
        "employeeNumber": item.get("employeeNumber", ""),
        "firstName": item.get("firstName", ""),
        "lastName": item.get("lastName", ""),
        "employmentStatus": str(item.get("employmentStatus", "1")),
        "personalId": item.get("personalId"),
    }
    if not isinstance(employee["netvisorKey"], str) or not employee["netvisorKey"].strip():
        raise ValueError(f"Invalid employee {index} netvisorKey")
    if any(not isinstance(employee[name], str) for name in ("employeeNumber", "firstName", "lastName")):
        raise ValueError(f"Invalid employee {index} names or number")
    if employee["employmentStatus"] not in {"0", "1"}:
        raise ValueError(f"Invalid employee {index} employmentStatus")
    if employee["personalId"] is not None and not isinstance(employee["personalId"], str):
        raise ValueError(f"Invalid employee {index} personalId")
    return employee


def _public_employee(employee):
    return {name: value for name, value in employee.items() if name != "personalId"}


@payroll.route("/__test__/payroll/<org_id>/employees", methods=["PUT"])
def seed_employees(org_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, list):
        return jsonify(error="Expected a JSON list of employees"), 400
    try:
        employees = [_seeded_employee(item, index) for index, item in enumerate(payload)]
    except ValueError as error:
        return jsonify(error=str(error)), 400
    for name in ("netvisorKey", "employeeNumber", "personalId"):
        values = [employee[name] for employee in employees if employee[name]]
        if len(values) != len(set(values)):
            return jsonify(error=f"Duplicate {name}"), 400
    with _lock:
        _context(org_id)["employees"] = employees
    return jsonify(count=len(employees))


@payroll.route("/__test__/payroll/<org_id>/employees", methods=["GET"])
def employees_for_context(org_id):
    with _lock:
        employees = [_public_employee(employee) for employee in _context(org_id)["employees"]]
    return jsonify(count=len(employees), employees=employees)


@payroll.route("/getemployees.nv", methods=["GET"])
def get_employees():
    if not _authenticated():
        return _auth_failure()
    include_ended = request.args.get("includeendedemployments", "0")
    if include_ended not in {"0", "1"} or set(request.args) - {"includeendedemployments"}:
        return _invalid("Unsupported employee list query")
    with _lock:
        employees = list(_context(request.headers["X-Netvisor-Organisation-ID"])["employees"])
    root = etree.Element("Root")
    status = etree.SubElement(root, "ResponseStatus")
    etree.SubElement(status, "Status").text = "OK"
    etree.SubElement(status, "TimeStamp").text = "09.10.2026 12:00:00"
    listed = etree.SubElement(root, "employees")
    for employee in employees:
        if employee["employmentStatus"] == "0" and include_ended != "1":
            continue
        element = etree.SubElement(listed, "employee")
        etree.SubElement(element, "employeenumber").text = employee["employeeNumber"]
        etree.SubElement(element, "netvisorkey").text = employee["netvisorKey"]
        if employee["personalId"]:
            personal_id = etree.SubElement(element, "personalid", type="finnishidentifier")
            personal_id.text = employee["personalId"]
        etree.SubElement(element, "firstname").text = employee["firstName"]
        etree.SubElement(element, "lastname").text = employee["lastName"]
        etree.SubElement(element, "realname").text = f"{employee['firstName']} {employee['lastName']}".strip()
        etree.SubElement(element, "employmentstatus").text = employee["employmentStatus"]
        etree.SubElement(element, "payrollservice").text = "1"
        etree.SubElement(element, "resourcemanagement").text = "0"
        etree.SubElement(element, "lunchbenefit").text = "0"
    return Response(etree.tostring(root), content_type="application/xml")


def _employee_query_error():
    if not _query_is({"method": "add"}):
        return "Unsupported employee query"
    return None


def _parse_employee():
    """Required elements of employee.dtd plus the employment GISGRO always sends."""
    employee = _parse_resource("employee")
    base = employee.find("employeebaseinformation")
    payroll_information = employee.find("employeepayrollinformation")
    if base is None or payroll_information is None:
        raise ValueError("Missing employeebaseinformation or employeepayrollinformation")
    for name in ("employeeidentifier", "firstname", "lastname"):
        _required(base, name)
    for name in ("streetaddress", "postnumber", "city", "payrollrulegroupname"):
        _required(payroll_information, name)
    number = payroll_information.findtext("employeenumber", "").strip()
    if number and not (number.isdigit() and len(number) <= 10):
        raise ValueError("Invalid employeenumber")
    periods = employee.find("employmentperiods")
    if periods is None:
        raise ValueError("Missing employmentperiods")
    period_list = periods.findall("employmentperiod")
    if not period_list:
        raise ValueError("Missing employmentperiod")
    for period in period_list:
        start = _required(period, "startdate", "format", {"ansi"})
        try:
            date.fromisoformat(start.text.strip())
        except ValueError:
            raise ValueError("Invalid startdate") from None
        _required(period, "profession")
    return {
        "personalId": base.findtext("employeeidentifier").strip(),
        "firstName": base.findtext("firstname").strip(),
        "lastName": base.findtext("lastname").strip(),
        "employeeNumber": number,
    }


def _accept_employee(org_id, new, _sequence):
    with _lock:
        employees = _context(org_id)["employees"]
        if any(employee["personalId"] == new["personalId"] for employee in employees):
            message = f"Employee information already exists: {_masked_identity_code(new['personalId'])}"
            return _invalid("Data form incorrect:. " + message)
        if new["employeeNumber"] and any(employee["employeeNumber"] == new["employeeNumber"] for employee in employees):
            return _invalid(f"Data form incorrect:. Employee number already in use: {new['employeeNumber']}")
        keys = [int(employee["netvisorKey"]) for employee in employees if employee["netvisorKey"].isdigit()]
        employees.append({**new, "netvisorKey": str(max(keys, default=0) + 1), "employmentStatus": "1"})
    return _xml()


@payroll.route("/employee.nv", methods=["POST"])
def add_employee():
    return _payroll_post(_employee_query_error, _parse_employee, _accept_employee, _redact_identity_codes)
