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
fake organisation's authentication context.
`GET /__test__/payroll/<organisation-id>/requests` returns the received payroll
POST count and request path, query, XML body, and authentication header names.
`DELETE /__test__/payroll/<organisation-id>` clears only that context. These
controls do not affect invoice data or other organisations.

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
