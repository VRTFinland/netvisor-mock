# Netvisor Mock

This is dummy and limited implementation of Netvisor API for testing purposes.
Functionality is limited to following endpoints:
- [POST] /customer.nv
- [GET] /customerlist.nv
- [GET] /getsalesinvoice.nv
- [POST] /salesinvoice.nv

Additionally mock API supports `[POST] /reset` -endpoint to clear stored
data.

## Requirements

* Python > 3.6 
* pipenv
* libxslt & libxml (for lxml).

Or

* docker.

## Install & run

```
$ pipenv install
$ FLASK_APP=app.py pipenv run flask run -p 5001
```

