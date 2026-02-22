from flask import Blueprint

from .api_routes.games import register_game_api_routes
from .api_routes.notifications import register_notification_api_routes
from .api_routes.quotes import register_quote_api_routes


def create_api_blueprint(
    *,
    quote_store,
    services,
    quote_anarchy_service,
    quote_blackline_service,
    quote_who_said_service,
    vapid_public_key: str,
    vapid_private_key: str,
):
    bp = Blueprint("api", __name__)

    context = {
        "quote_store": quote_store,
        "services": services,
        "quote_anarchy_service": quote_anarchy_service,
        "quote_blackline_service": quote_blackline_service,
        "quote_who_said_service": quote_who_said_service,
        "vapid_public_key": vapid_public_key,
        "vapid_private_key": vapid_private_key,
    }

    register_quote_api_routes(bp, context)
    register_game_api_routes(bp, context)
    register_notification_api_routes(bp, context)

    return bp
