import aiohttp
import uvicorn
from fastapi import FastAPI
from httpx_oauth.clients.discord import DiscordOAuth2
from starlette.middleware.cors import CORSMiddleware

import api.routers.action as action
import api.routers.analytics as analytics
import api.routers.authgrant as authgrant
import api.routers.chapter as chapter
import api.routers.client as client
import api.routers.discord as discord
import api.routers.event as event
import api.routers.journal as journal
import api.routers.label as label
import api.routers.page as page
import api.routers.preset as preset
import api.routers.template as template
import api.routers.test as test
import api.routers.trade as trade
import api.routers.user as user
from api.dependencies import messenger, set_http_session, get_http_session
from api.env import ENV
from api.models.user import UserRead, UserCreate
from api.routers import labelgroup
from api.users import fastapi_users, auth_backend
from api.utils.responses import OK
from lib.utils import setup_logger
from lib.db.models import Event, Client
from lib.db.models.action import Action
from lib.db.models.authgrant import AuthGrant
from lib.db.utils import run_migrations

VERSION = 1
# PREFIX = f'/api/v{VERSION}'
PREFIX = ""

app = FastAPI(
    docs_url=PREFIX + "/docs",
    openapi_url=PREFIX + "/openapi.json",
    title="TradeAlpha",
    description="Trade Analytics and Journaling platform",
    version=f"0.0.{VERSION}",
    terms_of_service="https://example.com/terms/",
    contact={
        "name": "Deadpoolio the Amazing",
        "url": "https://x-force.example.com/contact/",
        "email": "dp@x-force.example.com",
    },
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
)

# app.add_middleware(SessionMiddleware, secret_key='SECRET')
# app.add_middleware(CSRFMiddleware, secret='SECRET', sensitive_cookies=[settings.session_cookie_name])
# app.add_midleware(DbSessionMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fastapi_users.get_verify_router(UserRead), prefix=PREFIX)
app.include_router(fastapi_users.get_reset_password_router(), prefix=PREFIX)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate), prefix=PREFIX
)
app.include_router(fastapi_users.get_auth_router(backend=auth_backend), prefix=PREFIX)

discord_oauth = DiscordOAuth2(
    ENV.OAUTH2_CLIENT_ID,
    ENV.OAUTH2_CLIENT_SECRET.get_secret_value(),
    scopes=["identify", "email", "guilds"],
)

OAUTH_PREFIX = "/oauth/discord"
OAUTH2_REDIRECT_URI = ENV.FRONTEND_URL + "/api"

app.include_router(
    fastapi_users.get_custom_oauth_router(
        discord_oauth,
        user_schema=UserRead,
        backend=auth_backend,
        state_secret="SECRET",
        redirect_url=OAUTH2_REDIRECT_URI + OAUTH_PREFIX + "/callback",
    ),
    prefix=PREFIX + OAUTH_PREFIX,
)

ASSOC_PREFIX = OAUTH_PREFIX + "/associate"

app.include_router(
    fastapi_users.get_oauth_associate_router(
        discord_oauth,
        user_schema=UserRead,
        state_secret="SECRET",
        redirect_url=OAUTH2_REDIRECT_URI + ASSOC_PREFIX + "/callback",
    ),
    prefix=PREFIX + ASSOC_PREFIX,
)

for module in (
    # auth,
    authgrant,
    chapter,
    discord,
    labelgroup,
    label,
    analytics,
    journal,
    template,
    user,
    event,
    test,
    trade,
    action,
    client,
    page,
    preset,
):
    app.include_router(module.router)


@app.on_event("startup")
async def on_start():
    run_migrations()
    setup_logger()
    set_http_session(aiohttp.ClientSession())

    messenger.listen_class_all(Event)
    messenger.listen_class_all(Client)
    messenger.listen_class_all(Action)
    messenger.listen_class_all(AuthGrant)


@app.on_event("shutdown")
async def on_stop():
    await get_http_session().close()


@app.get("/status")
def status():
    return OK()


def run():
    uvicorn.run(app, host="localhost", port=5000)


if __name__ == "__main__":
    run()
