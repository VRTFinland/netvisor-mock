# Netvisor Mock

This is dummy and limited implementation of Netvisor API for testing purposes.
Functionality is limited to following endpoints:
- [POST] /customer.nv
- [GET] /customerlist.nv
- [GET] /getsalesinvoice.nv
- [POST] /salesinvoice.nv
- [POST] /updatesalesinvoicestatus.nv
- [GET] /payrollratiolist.nv?source=collectorratiolines
- [POST] /payrollperiodcollector.nv?useonlyexistingdimensions=1
- [POST] /workday.nv?useonlyexistingdimensions=1

Additionally mock API supports `[POST] /reset` -endpoint to clear stored
data.

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
$ uv run ruff check        # lint
$ uv run ruff format       # format
$ uv run ty check          # type check
$ uv run python -m unittest
```

## Docker

```
$ docker build -t netvisor-mock .
$ docker run -p 5001:5001 netvisor-mock
```
