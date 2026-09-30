"""Stripe billing webhook.

The only endpoint in Astrocoda that is not authenticated with an API key.
Trust is established by verifying the raw request body against the Stripe
signing secret, so a forged payload can never flip a subscription state.

Event handling is idempotent: replaying ``checkout.session.completed`` reuses
the existing user row and re-mints nothing.
"""

from __future__ import annotations

import logging

import stripe
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.db import (
    SessionDependency,
    User,
    generate_api_key,
    get_user_by_email,
    get_user_by_stripe_customer_id,
    normalise_email,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

STRIPE_SIGNATURE_HEADER: str = "stripe-signature"

#: Subscription states that grant (or keep) pipeline access.
ACTIVE_SUBSCRIPTION_STATES: frozenset[str] = frozenset({"active", "trialing"})


class WebhookResponse(BaseModel):
    """Acknowledgement body. Echoes enough context to debug in the Stripe UI."""

    received: bool = True
    event_type: str
    event_id: str | None = None
    status: str = Field(description="processed | ignored | error")
    user_id: str | None = None
    email: str | None = None
    is_active: bool | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_id(value: str | object | None) -> str | None:
    """Normalise Stripe's ``str | object | None`` unions into a plain id.

    Stripe returns expandable fields as either a bare id string or a full
    object, so both shapes have to resolve to the same identifier.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    identifier = getattr(value, "id", None)
    if isinstance(identifier, str) and identifier:
        return identifier
    return None


def _session_email(checkout_session: stripe.Checkout.Session) -> str | None:
    """Pull the buyer email from whichever field this API version populated."""
    details = checkout_session.customer_details
    if details is not None and details.email:
        return str(details.email)
    legacy_email = getattr(checkout_session, "customer_email", None)
    return str(legacy_email) if legacy_email else None


async def _upsert_customer(
    session: AsyncSession,
    *,
    email: str,
    stripe_customer_id: str | None,
    api_key_hint: str | None = None,
) -> User:
    """Create or reactivate the user behind a completed checkout.

    Lookup order matters: the Stripe customer id is authoritative, so a repeat
    purchase under a different email still resolves to the original account
    instead of creating a duplicate row.
    """
    user: User | None = None
    if stripe_customer_id:
        user = await get_user_by_stripe_customer_id(session, stripe_customer_id)
    if user is None:
        user = await get_user_by_email(session, email)

    if user is None:
        user = User(
            email=normalise_email(email),
            api_key=api_key_hint or generate_api_key(),
            stripe_customer_id=stripe_customer_id,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info("Provisioned user %s from Stripe checkout", user.id)
        return user

    if stripe_customer_id:
        user.stripe_customer_id = stripe_customer_id
    user.is_active = True
    await session.commit()
    await session.refresh(user)
    logger.info("Reactivated user %s from Stripe checkout", user.id)
    return user


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@router.post(
    "/stripe",
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
    summary="Receive Stripe subscription events",
    description=(
        "Verifies the `Stripe-Signature` header against `STRIPE_WEBHOOK_SECRET` and "
        "synchronises `users.is_active` with the Stripe subscription lifecycle."
    ),
)
async def stripe_webhook(
    request: Request,
    session: SessionDependency,
) -> WebhookResponse:
    """Validate and process an inbound Stripe event."""
    raw_body = await request.body()
    signature = request.headers.get(STRIPE_SIGNATURE_HEADER)
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing {STRIPE_SIGNATURE_HEADER} header.",
        )

    try:
        event: stripe.Event = stripe.Webhook.construct_event(
            payload=raw_body,
            sig_header=signature,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except (stripe.error.SignatureVerificationError, ValueError) as exc:
        logger.warning("Rejected Stripe webhook with invalid signature: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe signature.",
        ) from exc

    event_id = event.id
    logger.info("Handling Stripe event %s (%s)", event_id, event.type)

    if event.type == "checkout.session.completed":
        checkout_session = event.data.object
        email = _session_email(checkout_session)
        if not email:
            logger.warning("Checkout %s completed without a customer email", checkout_session.id)
            return WebhookResponse(
                event_type=event.type,
                event_id=event_id,
                status="ignored",
                detail="Checkout session did not contain a customer email.",
            )

        stripe_customer_id = _extract_id(checkout_session.customer)
        api_key_hint = checkout_session.client_reference_id or None
        user = await _upsert_customer(
            session,
            email=email,
            stripe_customer_id=stripe_customer_id,
            api_key_hint=api_key_hint,
        )
        return WebhookResponse(
            event_type=event.type,
            event_id=event_id,
            status="processed",
            user_id=str(user.id),
            email=user.email,
            is_active=True,
        )

    if event.type == "customer.subscription.deleted":
        subscription = event.data.object
        stripe_customer_id = _extract_id(subscription.customer)
        if not stripe_customer_id:
            return WebhookResponse(
                event_type=event.type,
                event_id=event_id,
                status="ignored",
                detail="Subscription payload did not contain a customer id.",
            )

        user = await get_user_by_stripe_customer_id(session, stripe_customer_id)
        if user is None:
            logger.warning("No user mapped to Stripe customer %s", stripe_customer_id)
            return WebhookResponse(
                event_type=event.type,
                event_id=event_id,
                status="ignored",
                detail="No Astrocoda user is mapped to this Stripe customer.",
            )

        user.is_active = False
        await session.commit()
        logger.info("Deactivated user %s after subscription cancellation", user.id)
        return WebhookResponse(
            event_type=event.type,
            event_id=event_id,
            status="processed",
            user_id=str(user.id),
            email=user.email,
            is_active=False,
        )

    if event.type == "customer.subscription.updated":
        subscription = event.data.object
        stripe_customer_id = _extract_id(subscription.customer)
        if not stripe_customer_id:
            return WebhookResponse(
                event_type=event.type,
                event_id=event_id,
                status="ignored",
                detail="Subscription payload did not contain a customer id.",
            )

        user = await get_user_by_stripe_customer_id(session, stripe_customer_id)
        if user is None:
            return WebhookResponse(
                event_type=event.type,
                event_id=event_id,
                status="ignored",
                detail="No Astrocoda user is mapped to this Stripe customer.",
            )

        should_activate = str(subscription.status) in ACTIVE_SUBSCRIPTION_STATES
        if user.is_active != should_activate:
            user.is_active = should_activate
            await session.commit()
        return WebhookResponse(
            event_type=event.type,
            event_id=event_id,
            status="processed",
            user_id=str(user.id),
            email=user.email,
            is_active=user.is_active,
        )

    return WebhookResponse(
        event_type=event.type,
        event_id=event_id,
        status="ignored",
        detail="Event type is not handled by Astrocoda.",
    )


__all__ = [
    "STRIPE_SIGNATURE_HEADER",
    "WebhookResponse",
    "router",
    "stripe_webhook",
]
