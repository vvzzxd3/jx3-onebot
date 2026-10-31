import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import signal
import sys
import time

import httpx
from dotenv import load_dotenv

from . import __version__
from .application import Application
from .modules.weather.cities import download_official, import_csv, MAX_BYTES, OFFICIAL_API
from .config import Settings
from .errors import BotError, ConfigurationError
from .gateway import Gateway
from .observability import Observer, ConsoleRuntime
from .routing import discover_routes
from .replies import validate_templates
from .qq_http import QQHTTP, QQTokens
from .store import Store
from .modules.weather.client import WeatherAuth, WeatherClient
from .modules.weather.service import WeatherService
from .modules.api.service import ServerStateService


def _acquire_instance_lock(handle):
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise ConfigurationError("Another official instance/importer holds this database lock") from None

        def release():
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

        return release

    try:
        import fcntl
    except ImportError:
        raise ConfigurationError("Official service instance locking requires POSIX fcntl or Windows msvcrt") from None
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ConfigurationError("Another official instance/importer holds this database lock") from None

    def release():
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    return release


@contextmanager
def instance_lock(path):
    canonical = Path(path).resolve()
    if canonical.exists() and canonical.stat().st_nlink != 1:
        raise ConfigurationError("Hard-linked database aliases are not supported")
    target = canonical.with_suffix(".lock")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+b")
    release = None
    try:
        release = _acquire_instance_lock(handle)
        yield
    finally:
        try:
            if release is not None:
                release()
        finally:
            handle.close()


@contextmanager
def stop_signal_handlers(loop, stop):
    loop_handlers = []
    fallback_handlers = {}

    def request_stop(_signum=None, _frame=None):
        loop.call_soon_threadsafe(stop.set)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, request_stop)
            except (NotImplementedError, RuntimeError):
                try:
                    fallback_handlers[signum] = signal.signal(signum, request_stop)
                except (AttributeError, OSError, ValueError):
                    continue
            else:
                loop_handlers.append(signum)
        yield
    finally:
        for signum in loop_handlers:
            loop.remove_signal_handler(signum)
        for signum, previous in fallback_handlers.items():
            signal.signal(signum, previous)


async def run_service(settings, store, observer=None):
    observer = observer or Observer(settings.logs, known_secrets=(settings.client_secret,))
    store.observer = observer
    store.bind_app(settings.app_id)
    store.chat.configure(settings.chat)
    store.cleanup(time.time())
    observer.event("chat.config", persistent=True, max_text_chars=settings.chat.max_text_chars)
    if not store.meta("city_source"):
        raise ConfigurationError("Import the official city CSV before starting the service")
    # Validate local formatting before any protected provider request.
    validate_templates()
    # JWT/key validation is offline and never prints a token.
    auth = WeatherAuth(settings)
    auth.headers()
    transport = httpx.AsyncHTTPTransport(retries=0)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False, transport=transport) as client:
        qq = QQHTTP(client, QQTokens(client, settings.app_id, settings.client_secret, observer=observer), observer)
        weather = WeatherService(store.weather, WeatherClient(client, store.weather, settings, auth), settings, observer=observer)
        server_state = ServerStateService(client)
        registry = discover_routes(services={"weather": weather, "server_state": server_state},
                                   command_prefix=settings.command_prefix)
        gateway = Gateway(qq, store, settings.app_id, ack_intervals=settings.ack_intervals, observer=observer, registry=registry)
        application = Application(store, weather, qq, gateway, settings.workers, observer=observer,
                                  registry=registry, reply_format=settings.reply_format)
        loop = asyncio.get_running_loop()
        with stop_signal_handlers(loop, application.stop):
            await application.run()


async def import_command(args, store):
    if args.download:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False,
                transport=httpx.AsyncHTTPTransport(retries=0)) as client:
            raw, revision = await download_official(client)
        return import_csv(store.weather, raw, OFFICIAL_API, revision)
    path = Path(args.csv)
    if path.stat().st_size > MAX_BYTES:
        raise ConfigurationError("City CSV is too large")
    return import_csv(store.weather, path.read_bytes(), "operator-provided:" + path.name)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes invalid values, including possible OpenIDs/secrets.
        super().error("Invalid command arguments; use --help for supported options and ranges")


def _chat_limit(value):
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("limit must be an integer from 1 to 1000") from None
    if not 1 <= limit <= 1000:
        raise argparse.ArgumentTypeError("limit must be an integer from 1 to 1000")
    return limit


def _conversation(value):
    if not value or len(value) > 256 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise argparse.ArgumentTypeError("conversation must be a nonempty OpenID without whitespace")
    return value


def _rfc3339(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value):
        raise argparse.ArgumentTypeError("timestamp must be RFC3339 with an explicit timezone")
    try:
        return datetime.fromisoformat(value.upper().replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        raise argparse.ArgumentTypeError("timestamp must be a valid RFC3339 instant") from None


def parser():
    result = SafeArgumentParser(description="Standalone QQ official bot (no NoneBot/OneBot)")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    result.add_argument("--env-file", default=".env.production", help="Production config by default; use --env-file .env.test for local tests; chat commands load no env file")
    result.add_argument("--database", help="Override BOT_DATABASE")
    sub = result.add_subparsers(dest="command")
    result.set_defaults(command="run")
    sub.add_parser("run", help="Connect to official QQ; run only after local configuration")
    sub.add_parser("check-config", help="Validate local config and JWT without network")
    sub.add_parser("usage", help="Show this module's current monthly attempts without network")
    history = sub.add_parser("chat-history", help="Read authorized chat history offline; output contains personal chat data")
    history.add_argument("--scene", choices=("group", "c2c"), required=True)
    history.add_argument("--conversation", type=_conversation, required=True, help="Conversation OpenID, not a QQ number")
    history.add_argument("--limit", type=_chat_limit, required=True, metavar="1..1000")
    history.add_argument("--start", type=_rfc3339, help="RFC3339 instant with timezone, inclusive")
    history.add_argument("--end", type=_rfc3339, help="RFC3339 instant with timezone, exclusive")
    sub.add_parser("chat-cleanup", help="Retire expired chat data only; offline logical deletion, not secure erasure")
    forget = sub.add_parser("chat-forget", help="Forget one chat collection only; offline logical deletion, not secure erasure")
    forget.add_argument("--scene", choices=("group", "c2c"), required=True)
    forget.add_argument("--conversation", type=_conversation, required=True, help="Conversation OpenID, not a QQ number")
    forget.add_argument("--confirm", action="store_true", required=True, help="Confirm irreversible logical deletion")
    city = sub.add_parser("import-cities", help="Atomic official city CSV import/update")
    source = city.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="Operator-provided official city CSV")
    source.add_argument("--download", action="store_true", help="Download public GitHub list; no paid API calls")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    os.umask(0o077)
    # Chat operations use only the selected database; never load provider credentials.
    chat_command = args.command in {"chat-history", "chat-cleanup", "chat-forget"}
    if not chat_command:
        # Only the selected production/test config is loaded, never a legacy .env file.
        load_dotenv(args.env_file, override=False)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    runtime = None
    env = dict(os.environ)
    if args.database:
        env["BOT_DATABASE"] = args.database
    elif env.get("BOT_DATABASE") == "data/official/bot.sqlite3":
        # The private production config was renamed without inspecting credentials.
        # Redirect only its exact former default; explicit --database remains authoritative.
        env["BOT_DATABASE"] = "data/bot.sqlite3"
    database = Path(env.get("BOT_DATABASE", "data/bot.sqlite3"))
    try:
        if args.command == "chat-history" and args.start is not None and args.end is not None and args.start >= args.end:
            raise ConfigurationError("Chat history start must precede end")
        if chat_command and not database.is_file():
            raise ConfigurationError("Chat operations require an existing bound database")
        if args.command == "check-config":
            settings = Settings.from_env(env)
            validate_templates()
            WeatherAuth(settings).headers()
            print("Official configuration valid (offline; no provider requests)")
            return 0
        with instance_lock(database):
            store = Store(database, read_only=True) if args.command == "chat-history" else Store(database)
            try:
                if args.command == "import-cities":
                    result = asyncio.run(import_command(args, store))
                    print(json.dumps(result, ensure_ascii=False))
                elif chat_command:
                    app_id = store.meta("app_id")
                    if not isinstance(app_id, str) or not app_id:
                        raise ConfigurationError("Chat operations require a database bound to a QQ app")
                    if args.command == "chat-history":
                        logging.getLogger("jx3_bot").warning("Chat history output contains authorized personal chat data; protect terminal output and exports")
                        query = store.chat.user_messages if args.scene == "c2c" else store.chat.group_messages
                        result = query(app_id, args.conversation, limit=args.limit, start=args.start, end=args.end)
                    elif args.command == "chat-cleanup":
                        with store.transaction() as db:
                            result = store.chat.cleanup(db, time.time())
                    else:
                        result = store.chat.forget(args.scene, args.conversation, time.time())
                    print(json.dumps(result, ensure_ascii=False))
                elif args.command == "usage":
                    zone = store.meta("usage_timezone", env.get("QWEATHER_USAGE_TIMEZONE", "Asia/Shanghai"))
                    month, count = store.weather.usage(zone, time.time())
                    print(json.dumps({"month": month, "attempts": count, "timezone": zone}, ensure_ascii=False))
                else:
                    settings = Settings.from_env(env)
                    observer = Observer(settings.logs, known_secrets=(settings.client_secret,))
                    runtime = ConsoleRuntime(observer, sys.stdout)
                    observer.event("log.config", receive=settings.logs.receive, chat=settings.logs.chat,
                                   message_text=settings.logs.message_text)
                    asyncio.run(run_service(settings, store, observer))
            finally:
                store.close()
        return 0
    except BotError as error:
        # These application exceptions are deliberately constructed without secrets.
        logging.getLogger("jx3_bot").error("%s", error)
        return 2
    except (OSError, ValueError):
        # Never render arbitrary external exception bodies, paths or URLs.
        logging.getLogger("jx3_bot").error("Official command failed; check configuration/storage/provider status (no secrets logged)")
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        # Unexpected failures must not emit external exception bodies or secret-bearing tracebacks.
        logging.getLogger("jx3_bot").error("Official command stopped; error_type=%s (details suppressed)", type(error).__name__)
        return 2
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    sys.exit(main())
