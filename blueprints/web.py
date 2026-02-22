from flask import Blueprint

from .web_routes.core import register_core_routes
from .web_routes.quotes import register_quote_routes
from .web_routes.social import register_social_routes


def create_web_blueprint(
    *,
    quote_store,
    ai_worker,
    services,
    quote_anarchy_service,
    quote_blackline_service,
    quote_who_said_service,
    uk_tz,
    edit_pin: str,
    vapid_public_key: str,
    per_page_quote_limit: int,
    support_url: str,
    support_label: str,
    sponsor_contact_url: str,
    sponsor_contact_email: str,
    affiliate_disclosure: str,
    adsense_client_id: str,
    adsense_slot_inline: str,
    adsense_slot_footer: str,
    google_adsense_account: str,
    robots_disallow_all: bool,
):
    bp = Blueprint("web", __name__)

    context = {
        "quote_store": quote_store,
        "ai_worker": ai_worker,
        "services": services,
        "quote_anarchy_service": quote_anarchy_service,
        "quote_blackline_service": quote_blackline_service,
        "quote_who_said_service": quote_who_said_service,
        "uk_tz": uk_tz,
        "edit_pin": edit_pin,
        "vapid_public_key": vapid_public_key,
        "per_page_quote_limit": per_page_quote_limit,
        "support_url": support_url,
        "support_label": support_label,
        "sponsor_contact_url": sponsor_contact_url,
        "sponsor_contact_email": sponsor_contact_email,
        "affiliate_disclosure": affiliate_disclosure,
        "adsense_client_id": adsense_client_id,
        "adsense_slot_inline": adsense_slot_inline,
        "adsense_slot_footer": adsense_slot_footer,
        "google_adsense_account": google_adsense_account,
        "robots_disallow_all": robots_disallow_all,
    }

    register_core_routes(bp, context)
    register_quote_routes(bp, context)
    register_social_routes(bp, context)

    return bp
