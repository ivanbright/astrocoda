"""Astrocoda CLI - licensed access to the Astrocoda boilerplate.

Commands require a valid license: ``astrocoda login <key>`` exchanges a
license key for a short lived token signed by the license server, and every
setup command re-verifies that token online before doing work.  There is no
offline bypass: if the seller's license endpoint is unreachable, commands fail
closed.
"""

__version__ = "0.1.0"