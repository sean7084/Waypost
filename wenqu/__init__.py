"""Wenqu (问渠) - customer-facing AI helpdesk mini-program backend.

Ported from the standalone FastAPI helpdesk service into a Django app so it
shares Waypost's accounts, JWT auth, storage and deployment stack. This package
holds the data models and services; the REST surface lives in ``api.wenqu_*``.
"""