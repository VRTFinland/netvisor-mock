# Netvisor Mock

This is dummy and limited implementation of Netvisor API for testing purposes.
Functionality is limited to following endpoints:
- [POST] /customer.nv
- [GET] /customerlist.nv
- [GET] /getcustomer.nv?id=<key> or ?idlist=<key>,<key>
- [GET] /getsalesinvoice.nv
- [POST] /salesinvoice.nv
- [POST] /updatesalesinvoicestatus.nv
- [GET] /payrollratiolist.nv?source=collectorratiolines
- [POST] /payrollperiodcollector.nv?useonlyexistingdimensions=1
- [POST] /workday.nv?useonlyexistingdimensions=1
- [GET] /getemployees.nv?includeendedemployments=0|1
- [POST] /employee.nv?method=add

Additionally mock API supports `[POST] /reset` -endpoint to clear stored
data.

### Invoicing behaviour

The responses follow the Netvisor sandbox (2026-11-05, with
`X-Netvisor-Authentication-UseHTTPResponseStatusCodes: 1`) and the Netvisor API documentation.
Errors answer HTTP 400 with `<Status>FAILED</Status>` and a second `<Status>` such as
`INVALID_DATA :: Data form incorrect:. Customer not found with Netvisor identifier: 9`.

- `customer.nv` requires `method=add|edit`; `edit` requires the `id` of a stored customer.
- `customerlist.nv?keyword=` matches a case-insensitive substring of the name, customer code,
  business ID or name extension. `changedsince=` (`YYYY-MM-DD` or `YYYY-MM-DDThh:mm:ss`,
  Europe/Helsinki) lists customers added or edited at or after that time and is ignored with
  a keyword. A customer without an imported `internalidentifier` shows its key as `Code`.
- `getcustomer.nv` returns a bare `<Customer>` for one match and `<Customers>` for several;
  unknown keys in `idlist` are skipped, and no match at all is an error.
- `getsalesinvoice.nv` returns an empty `<SalesInvoice />` with Status OK for an unknown key.
  The PDF is included only with `pdfimage=pdf`.
- `updatesalesinvoicestatus.nv` accepts `open`, `overdue`, `reminded`, `requested`,
  `collected`, `paid` and `creditloss`, and rejects an unknown invoice.

Payroll requests require Netvisor authentication headers signed with the received URL.
The default mock partner ID/key are `gisgro-test-partner`/`gisgro-test-partner-key`,
and the default organisation user key is `test-key`. MAC inputs use ISO-8859-15.
Set the corresponding
`NETVISOR_MOCK_PARTNER_ID`, `NETVISOR_MOCK_PARTNER_KEY`, `NETVISOR_MOCK_SENDER`,
or `NETVISOR_MOCK_USER_KEY` environment variable to change them.

The test-only `PUT /__test__/payroll/<organisation-id>/scenario` endpoint accepts
`{"scenario":"success","ratioNumbers":["1234"]}`. Available scenarios are
`success`, `business_rejection`, `auth_rejection`, `ambiguous`, `server_failure`,
and `timeout` (HTTP 408). Optional `userKey` and `sender` values change the
fake organisation's authentication context. An optional `rejectionMessage` sets the
free text of `business_rejection`, which answers HTTP 400 with
`INVALID_DATA :: <rejectionMessage>`.
`GET /__test__/payroll/<organisation-id>/requests` returns the received payroll
POST count and request path, query, XML body, and authentication header names.
`DELETE /__test__/payroll/<organisation-id>` clears only that context. These
controls do not affect invoice data or other organisations.

### Payroll employees

`PUT /__test__/payroll/<organisation-id>/employees` replaces the organisation's
employees with a JSON list:

```json
[{"netvisorKey": "4", "employeeNumber": "2", "firstName": "Matti", "lastName": "Meikäläinen",
  "employmentStatus": 1, "personalId": "010180-900A"}]
```

`netvisorKey` is required. `employmentStatus` is `1` (active, default) or `0` (ended).
`personalId` is optional and is listed as `<personalid type="finnishidentifier">`.
`GET /__test__/payroll/<organisation-id>/employees` returns the current list
without personal IDs.

The resources behave as the Netvisor sandbox did on 2026-11-04:

- `getemployees.nv` lists `Root/employees/employee` with `employeenumber`, `netvisorkey`,
  `personalid`, `firstname`, `lastname`, `realname`, `employmentstatus`, `payrollservice`,
  `resourcemanagement` and `lunchbenefit`. Ended employments are left out unless
  `includeendedemployments=1`.
- `employee.nv?method=add` requires `employeeidentifier`, `firstname`, `lastname`,
  `streetaddress`, `postnumber`, `city`, `payrollrulegroupname` and at least one
  `employmentperiod` with `startdate format="ansi"` and `profession`. A success answers
  `ResponseStatus` OK without `InsertedDataIdentifier`; the employee gets the next free
  numeric `netvisorkey` and is active. Read the key back from `getemployees.nv` by
  employee number.
- An existing identity code answers HTTP 400
  `INVALID_DATA :: Data form incorrect:. Employee information already exists: 010180-xxxx`
  (masked like Netvisor). An existing employee number answers HTTP 400
  `INVALID_DATA :: Data form incorrect:. Employee number already in use: <number>`.
- The scenarios apply to `employee.nv` as to the other payroll POSTs.

The request journal never shows an identity code: it replaces every `employee.nv`
`employeeidentifier`, and every `type="personalidentificationnumber"` one in `workday.nv` and
`payrollperiodcollector.nv`, with `[redacted]`. A body that does not parse but mentions an
employee identifier is withheld entirely.

## Requirements

* [uv](https://docs.astral.sh/uv/) (installs Python 3.15 automatically if needed)

Or

* docker.

## Install & run

```
$ uv sync
$ uv run flask run -p 5001
```

## Development

```
$ make check               # ruff, ty and the tests (make help lists the targets)
$ make format              # fix lint findings and format
```

## Docker

```
$ make build               # vrtfinland/netvisor-mock:dev for this machine
$ docker run -p 5001:5001 vrtfinland/netvisor-mock:dev
```

Publishing builds `linux/amd64` and `linux/arm64` with `docker buildx` and pushes them as one tag.
It needs a builder with the `docker-container` driver and push access to `vrtfinland/netvisor-mock`.
The target refuses an uncommitted tree or an existing tag:

```
$ docker buildx create --name multiarch --driver docker-container   # once
$ make publish TAG=6 BUILDER=multiarch
```
