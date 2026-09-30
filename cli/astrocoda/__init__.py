"""Astrocoda CLI - free, ungated scaffolding for the Astrocoda boilerplate.

There is no account, no license key and no activation call.  The one thing the
CLI insists on is integrity: ``init`` will not write a single file until the
template's signed manifest has been verified and every listed file re-hashed.

``__version__`` is the version of *this package* and is read from the installed
distribution metadata, so it cannot drift from ``pyproject.toml``.  It is
deliberately not used as the template version -- see
``config.DEFAULT_TEMPLATE_VERSION`` for that, which is the release users get
scaffolded onto.
"""

from importlib.metadata import PackageNotFoundError, version as _dist_version

try:
    __version__ = _dist_version("astrocoda-cli")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.2.0"

__all__ = ["__version__"]
