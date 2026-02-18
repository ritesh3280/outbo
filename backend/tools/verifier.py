"""Email verification utilities.

Provides DNS MX record checks to verify that a domain accepts email.
No external API keys needed — uses standard DNS resolution.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def check_mx_record(domain: str) -> bool:
    """Check if a domain has MX records (i.e., it can receive email).

    Args:
        domain: The domain to check (e.g. "stripe.com").

    Returns:
        True if MX records exist, False otherwise.
    """
    try:
        import dns.resolver

        result = await asyncio.to_thread(
            dns.resolver.resolve, domain, "MX"
        )
        has_mx = len(result) > 0
        logger.debug("MX check for %s: %s (%d records)", domain, has_mx, len(result))
        return has_mx
    except Exception as e:
        logger.debug("MX check failed for %s: %s", domain, e)
        return False


async def get_mx_records(domain: str) -> list[str]:
    """Get MX records for a domain.

    Returns:
        List of MX hostnames sorted by priority.
    """
    try:
        import dns.resolver

        result = await asyncio.to_thread(
            dns.resolver.resolve, domain, "MX"
        )
        records = sorted(result, key=lambda r: r.preference)
        return [str(r.exchange).rstrip(".") for r in records]
    except Exception as e:
        logger.debug("MX lookup failed for %s: %s", domain, e)
        return []
