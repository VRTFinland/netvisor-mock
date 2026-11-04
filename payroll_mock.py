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
_scenarios = {
    "success", "business_rejection", "auth_rejection", "ambiguous", "server_failure", "timeout"
}
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
    return _contexts.setdefault(org_id, {"scenario": "success", "ratioNumbers": ["1234"],
                                         "userKey": _default_user_key, "sender": _sender, "requests": []})


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
    return _xml("FAILED", "AUTHENTICATION_FAILED :: Integration credentials are invalid",
                http_status=401)


def _query_is(expected):
    return set(request.args.keys()) == set(expected) and all(
        request.args.getlist(key) == [value] for key, value in expected.items()
    )


def _required(parent, name, attribute=None, allowed=None):
    element = parent.find(name)
    if element is None or not (element.text or "").strip():
        raise ValueError("Missing " + name)
    if attribute and (not element.get(attribute) or
                      (allowed and element.get(attribute) not in allowed)):
        raise ValueError("Invalid " + name + " " + attribute)
    if allowed and not attribute and element.text.strip() not in allowed:
        raise ValueError("Invalid " + name)
    return element


def _number(text):
    try:
        if not Decimal(text).is_finite():
            raise ValueError("Non-finite amount")
    except InvalidOperation:
        raise ValueError("Invalid amount")


def _validate_dimensions(line):
    for dimension in line.findall("dimension"):
        _required(dimension, "dimensionname")
        _required(dimension, "dimensionitem")


def _validate_xml(resource):
    if len(request.data) > 1_000_000 or b"<!DOCTYPE" in request.data.upper():
        raise ValueError("Invalid XML document")
    try:
        root = etree.fromstring(request.data, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        raise ValueError("Invalid XML document")
    if root.tag != "root" or len(root) != 1 or root[0].tag != resource:
        raise ValueError("Invalid resource root")
    data = root[0]
    date_element = _required(data, "date", "format", {"ansi"})
    if resource == "payrollperiodcollector" and set(date_element.attrib) != {"format"}:
        raise ValueError("Invalid collector date attributes")
    try:
        date.fromisoformat(date_element.text.strip())
    except ValueError:
        raise ValueError("Invalid date")
    _required(data, "employeeidentifier", "type", {"number", "personalidentificationnumber",
                                                     "netvisorkey"})
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


def _record_post(org_id):
    safe_query = {
        name: request.args.get(name) if name in {"method", "useonlyexistingdimensions"} else "<redacted>"
        for name in request.args.keys()
    }
    entry = {
        "path": request.path,
        "query": safe_query,
        "body": request.get_data(as_text=True),
        "headerNames": sorted(name for name in _auth_headers if request.headers.get(name)),
    }
    with _lock:
        context = _context(org_id)
        context["requests"].append(entry)
        return context["scenario"], len(context["requests"])


@payroll.route("/__test__/payroll/<org_id>/scenario", methods=["PUT"])
def configure_scenario(org_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) - {"scenario", "ratioNumbers", "userKey", "sender"}:
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
    with _lock:
        context = _context(org_id)
        context["scenario"] = payload["scenario"]
        context["ratioNumbers"] = ratio_numbers
        context["userKey"] = user_key
        context["sender"] = sender
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


def _post(resource):
    org_id = request.headers.get("X-Netvisor-Organisation-ID")
    if not org_id:
        return _auth_failure()
    scenario, sequence = _record_post(org_id)
    if not _authenticated() or scenario == "auth_rejection":
        return _auth_failure()
    if request.mimetype not in {"application/xml", "text/xml"}:
        return _invalid("Expected XML content type")
    expected_query = {"useonlyexistingdimensions": request.args.get("useonlyexistingdimensions")}
    if expected_query["useonlyexistingdimensions"] not in {"0", "1"}:
        return _invalid("Invalid dimension safety option")
    if not _query_is(expected_query):
        return _invalid("Unsupported payroll query")
    try:
        _validate_xml(resource)
    except ValueError as error:
        return _invalid(str(error))
    if scenario == "business_rejection":
        return _xml("FAILED", "INVALID_DATA :: Payroll line was rejected", http_status=400)
    if scenario == "ambiguous":
        return Response("<Root><ResponseStatus>", content_type="application/xml")
    if scenario == "server_failure":
        return _xml("FAILED", "TECHNICAL_ERROR :: Provider unavailable", http_status=503)
    if scenario == "timeout":
        return _xml("FAILED", "TECHNICAL_ERROR :: Request timed out", http_status=408)
    identifier = "mock-%s" % sequence if resource == "payrollperiodcollector" else None
    return _xml(identifier=identifier)


@payroll.route("/payrollperiodcollector.nv", methods=["POST"])
def payroll_period_collector():
    return _post("payrollperiodcollector")


@payroll.route("/workday.nv", methods=["POST"])
def workday():
    return _post("workday")
