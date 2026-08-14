"""Adapters that translate external provider payloads into domain models.

Provider-specific response shapes must stop at this package boundary.  Code in
the rest of the application should consume models from :mod:`investing_bot.models`.
"""

