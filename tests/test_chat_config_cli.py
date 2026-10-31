"""Synthetic, offline checks for persistent chat defaults and operator CLI."""
from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, ANY

from jx3_bot.__main__ import instance_lock, main, parser, run_service
from jx3_bot.config import Settings
from jx3_bot.errors import ConfigurationError
from jx3_bot.modules.chat_history.config import ChatSettings
from jx3_bot.store import Store


SYNTHETIC_ENV = {
    "QQ_APP_ID": "synthetic-app",
    "QQ_CLIENT_SECRET": "synthetic-secret-do-not-echo",
    "QWEATHER_API_HOST": "https://synthetic.qweatherapi.com",
    "QWEATHER_DEVELOPER_ID": "synthetic-developer",
    "QWEATHER_PROJECT_ID": "synthetic-project",
    "QWEATHER_CREDENTIAL_ID": "synthetic-credential",
    # Settings parsing does not open this path; no operator PEM is ever read.
    "QWEATHER_PRIVATE_KEY_FILE": "synthetic-unused-key-path",
}


class ChatConfigTests(unittest.TestCase):
    def test_default_archives_without_chat_configuration(self):
        value = Settings.from_env(SYNTHETIC_ENV)
        self.assertEqual(value.chat, ChatSettings())
        self.assertEqual(value.command_prefix, "")
        self.assertTrue(value.chat.allows("group", "synthetic-group"))
        self.assertTrue(value.chat.allows("c2c", "synthetic-user"))
        self.assertEqual(value.chat.max_text_chars, 16384)
        self.assertNotIn("analytics", Settings.__dataclass_fields__)

    def test_command_prefix_accepts_empty_or_printable_word_and_rejects_controls(self):
        self.assertEqual(Settings.from_env({**SYNTHETIC_ENV, "BOT_COMMAND_PREFIX": "/"}).command_prefix, "/")
        for invalid in (" ", "/ ", "\n", "\x00", "/" * 17):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(ConfigurationError):
                Settings.from_env({**SYNTHETIC_ENV, "BOT_COMMAND_PREFIX": invalid})

    def test_retired_scope_and_ttl_keys_cannot_disable_or_limit_archiving(self):
        value = Settings.from_env({**SYNTHETIC_ENV, "BOT_CHAT_ENABLED": "false",
            "BOT_CHAT_GROUPS": "synthetic-group-a", "BOT_CHAT_USERS": "synthetic-user-a",
            "BOT_CHAT_TEXT_DAYS": "1", "BOT_CHAT_DEDUP_DAYS": "2",
            "BOT_CHAT_MAX_TEXT_CHARS": "512"})
        self.assertTrue(value.chat.allows("group", "synthetic-unlisted"))
        self.assertTrue(value.chat.allows("c2c", "synthetic-unlisted"))
        self.assertEqual(value.chat.max_text_chars, 512)
        self.assertNotIn("synthetic-group-a", repr(value))

    def test_invalid_text_limit_never_echoes_values(self):
        for value in ("synthetic-secret-do-not-echo", "127", "65537"):
            with self.subTest(value=value), self.assertRaises(ConfigurationError) as raised:
                Settings.from_env({**SYNTHETIC_ENV, "BOT_CHAT_MAX_TEXT_CHARS": value})
            self.assertNotIn(value, str(raised.exception))

    def test_legacy_analytics_env_does_not_load_or_enable(self):
        with patch("builtins.open", side_effect=AssertionError("must not discover analytics config")):
            value = Settings.from_env({**SYNTHETIC_ENV, "BOT_ANALYTICS_CONFIG": "synthetic-unused-analytics.toml"})
        self.assertTrue(value.chat.allows("group", "any-group"))
        self.assertFalse(hasattr(value, "analytics"))


class ChatRunAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_run_passes_chat_preflight_without_credentials_or_network(self):
        settings = Settings.from_env(SYNTHETIC_ENV)
        store, observer = MagicMock(), MagicMock()
        steps = []
        store.bind_app.side_effect = lambda app: steps.append("bind")
        store.chat.configure.side_effect = lambda chat: steps.append("configure")
        store.cleanup.side_effect = lambda now: steps.append("cleanup")
        store.meta.return_value = None
        with patch("httpx.AsyncClient", side_effect=AssertionError("network")), \
                patch("jx3_bot.__main__.WeatherAuth", side_effect=AssertionError("credential load")):
            with self.assertRaisesRegex(ConfigurationError, "city CSV"):
                await run_service(settings, store, observer)
        self.assertEqual(steps, ["bind", "configure", "cleanup"])


class ChatCLITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "synthetic.sqlite"
        self.path.touch()
        self.common = ["--env-file", str(self.root / "do-not-load.env"), "--database", str(self.path)]
        self.query = ["chat-history", "--scene", "group", "--conversation", "synthetic-group", "--limit", "10"]
        self.store = MagicMock()
        self.store.meta.return_value = "database-bound-app"
        self.store.chat.group_messages.return_value = [{"direction": "in", "content": "synthetic sanitized chat"}]
        self.store.chat.user_messages.return_value = [{"direction": "out", "content": "synthetic reply"}]
        self.store.chat.cleanup.return_value = {"retired": 1}
        self.store.chat.forget.return_value = {"forgotten": 1}

    def offline_stack(self):
        stack = ExitStack()
        stack.enter_context(patch.dict(os.environ, {}, clear=True))
        for target in ("jx3_bot.__main__.load_dotenv", "jx3_bot.__main__.Settings.from_env",
                       "jx3_bot.__main__.WeatherAuth", "jx3_bot.__main__.QQTokens",
                       "httpx.AsyncClient", "httpx.AsyncHTTPTransport", "socket.create_connection"):
            stack.enter_context(patch(target, side_effect=AssertionError("offline operation touched provider/configuration")))
        return stack

    def test_default_command_starts_service_offline_and_redirects_legacy_config_path(self):
        parsed = parser().parse_args([])
        self.assertEqual(parsed.command, "run")
        self.assertEqual(parsed.env_file, ".env.production")
        with patch.dict(os.environ, {**SYNTHETIC_ENV, "BOT_DATABASE": "data/official/bot.sqlite3"}, clear=True), \
                patch("jx3_bot.__main__.load_dotenv") as load, \
                patch("jx3_bot.__main__.instance_lock", return_value=nullcontext()), \
                patch("jx3_bot.__main__.Store", return_value=self.store) as store, \
                patch("jx3_bot.__main__.Observer"), patch("jx3_bot.__main__.ConsoleRuntime"), \
                patch("jx3_bot.__main__.run_service", new_callable=AsyncMock) as service:
            self.assertEqual(main([]), 0)
        load.assert_called_once_with(".env.production", override=False)
        self.assertEqual(store.call_args.args, (Path("data/bot.sqlite3"),))
        self.assertEqual(service.await_count, 1)
        self.assertEqual(service.await_args.args[0].database, Path("data/bot.sqlite3"))

    def test_old_cli_entrypoints_are_removed(self):
        help_text = parser().format_help()
        for name in ("feature-usage", "analytics-cleanup", "analytics-forget"):
            self.assertNotIn(name, help_text)
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                parser().parse_args([name])
            self.assertEqual(raised.exception.code, 2)
        self.assertIn("chat-history", help_text)
        self.assertIn("personal chat data", help_text)

    def test_group_query_offline_bound_app_and_no_mutations(self):
        with self.offline_stack(), patch("jx3_bot.__main__.Store", return_value=self.store), \
                redirect_stdout(io.StringIO()) as output, self.assertLogs("jx3_bot", level="WARNING") as logs:
            result = main(self.common + self.query + ["--start", "2026-10-01T00:00:00+08:00", "--end", "2026-10-02T00:00:00Z"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), self.store.chat.group_messages.return_value)
        self.store.chat.group_messages.assert_called_once_with("database-bound-app", "synthetic-group", limit=10,
            start=datetime(2026, 9, 30, 16, tzinfo=timezone.utc).timestamp(),
            end=datetime(2026, 10, 2, tzinfo=timezone.utc).timestamp())
        self.assertIn("personal chat data", " ".join(logs.output))
        self.store.bind_app.assert_not_called()
        self.store.recover.assert_not_called()
        self.store.cleanup.assert_not_called()
        self.store.chat.configure.assert_not_called()
        self.store.chat.cleanup.assert_not_called()
        self.store.chat.forget.assert_not_called()
        self.store.close.assert_called_once()

    def test_user_query_and_limit_edges(self):
        for limit in (1, 1000):
            self.store.reset_mock()
            args = ["chat-history", "--scene", "c2c", "--conversation", "synthetic-user", "--limit", str(limit)]
            with self.subTest(limit=limit), self.offline_stack(), patch("jx3_bot.__main__.Store", return_value=self.store), \
                    redirect_stdout(io.StringIO()), self.assertLogs("jx3_bot", level="WARNING"):
                self.assertEqual(main(self.common + args), 0)
            self.store.chat.user_messages.assert_called_once_with("database-bound-app", "synthetic-user", limit=limit, start=None, end=None)
            self.store.chat.group_messages.assert_not_called()

    def test_missing_binding_rejected_without_query(self):
        self.store.meta.return_value = None
        with self.offline_stack(), patch("jx3_bot.__main__.Store", return_value=self.store), \
                redirect_stdout(io.StringIO()), self.assertLogs("jx3_bot", level="ERROR"):
            self.assertEqual(main(self.common + self.query), 2)
        self.store.chat.group_messages.assert_not_called()
        self.store.bind_app.assert_not_called()
        self.store.close.assert_called_once()

    def test_absent_database_is_not_created(self):
        absent = self.root / "absent.sqlite"
        with self.offline_stack(), patch("jx3_bot.__main__.Store") as storage, self.assertLogs("jx3_bot", level="ERROR"):
            self.assertEqual(main(["--database", str(absent)] + self.query), 2)
        storage.assert_not_called()
        self.assertFalse(absent.exists())

    def test_cleanup_forget_only_call_chat_repository_without_credentials(self):
        cases = (["chat-cleanup"], ["chat-forget", "--scene", "c2c", "--conversation", "synthetic-user", "--confirm"])
        for args in cases:
            self.store.reset_mock()
            with self.subTest(command=args[0]), self.offline_stack(), patch("jx3_bot.__main__.Store", return_value=self.store), \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(self.common + args), 0)
            self.assertIsInstance(json.loads(output.getvalue()), dict)
            if args[0] == "chat-cleanup":
                self.store.chat.cleanup.assert_called_once()
                self.store.chat.forget.assert_not_called()
            else:
                self.store.chat.forget.assert_called_once_with("c2c", "synthetic-user", ANY)
                self.store.chat.cleanup.assert_not_called()
            self.store.cleanup.assert_not_called()
            self.store.bind_app.assert_not_called()
            self.store.recover.assert_not_called()
            self.store.weather.assert_not_called()

    def test_cli_parameter_errors_do_not_echo_identifiers_or_secrets(self):
        cases = (
            ["chat-history", "--scene", "group", "--conversation", "synthetic-private-openid", "--limit", "0"],
            ["chat-history", "--scene", "group", "--conversation", "synthetic-private-openid", "--limit", "1001"],
            ["chat-history", "--scene", "group", "--conversation", "synthetic-private-openid", "--limit", "synthetic-secret-do-not-echo"],
            self.query + ["--start", "2026-10-01"],
            self.query + ["--start", "2026-10-01T00:00:00"],
            self.query + ["--start", "2026-02-30T00:00:00Z"],
            self.query + ["--start", "2026-10-01T00:00:00+01:60"],
            self.query + ["--start", "2026-10-01T00:00:00+24:00"],
            self.query + ["--start", "synthetic-secret-do-not-echo"],
            ["chat-history", "--scene", "synthetic-private-openid", "--conversation", "synthetic-private-openid", "--limit", "1"],
            ["chat-history", "--scene", "group", "--conversation", "", "--limit", "1"],
            ["chat-forget", "--scene", "group", "--conversation", "synthetic-private-openid"],
        )
        for args in cases:
            with self.subTest(command=args[0]), redirect_stderr(io.StringIO()) as output, \
                    patch("jx3_bot.__main__.Store") as storage, self.assertRaises(SystemExit) as raised:
                main(self.common + args)
            self.assertEqual(raised.exception.code, 2)
            self.assertNotIn("synthetic-private-openid", output.getvalue())
            self.assertNotIn("synthetic-secret-do-not-echo", output.getvalue())
            storage.assert_not_called()

    def test_empty_or_inverted_time_range_rejected_before_storage(self):
        for end in ("2026-10-01T00:00:00Z", "2026-09-01T00:00:00Z"):
            with self.subTest(end=end), self.offline_stack(), patch("jx3_bot.__main__.Store") as storage, \
                    self.assertLogs("jx3_bot", level="ERROR"):
                self.assertEqual(main(self.common + self.query + ["--start", "2026-10-01T00:00:00Z", "--end", end]), 2)
            storage.assert_not_called()

    def test_chat_commands_obey_canonical_instance_lock(self):
        alias = self.root / "alias.sqlite"
        try:
            alias.symlink_to(self.path)
        except OSError as error:
            self.skipTest(f"filesystem does not permit symlink capability check: {type(error).__name__}")
        for path in (self.path, alias):
            with self.subTest(path=path.name), instance_lock(self.path), self.offline_stack(), \
                    patch("jx3_bot.__main__.Store") as storage, self.assertLogs("jx3_bot", level="ERROR"):
                self.assertEqual(main(["--database", str(path)] + self.query), 2)
            storage.assert_not_called()

    def test_hardlinked_database_alias_is_rejected(self):
        alias = self.root / "hardlink.sqlite"
        try:
            os.link(self.path, alias)
        except OSError as error:
            self.skipTest(f"filesystem does not permit hardlink capability check: {type(error).__name__}")
        with self.offline_stack(), patch("jx3_bot.__main__.Store") as storage, self.assertLogs("jx3_bot", level="ERROR"):
            self.assertEqual(main(["--database", str(alias)] + self.query), 2)
        storage.assert_not_called()


class ChatSQLiteCLITests(unittest.TestCase):
    offline_stack = ChatCLITests.offline_stack

    def setUp(self):
        ChatCLITests.setUp(self)
        self.now = time.time()
        with instance_lock(self.path):
            store = Store(self.path)
            try:
                store.bind_app("database-bound-app")
                store.chat.configure(ChatSettings())
                for scene, conversation, user in (("group", "synthetic-group", "synthetic-sender"),
                                                  ("c2c", "synthetic-user", "synthetic-user")):
                    received = SimpleNamespace(app_id="database-bound-app", scene=scene,
                        conversation_id=conversation, user_id=user, message_id="synthetic-in-" + scene,
                        timestamp=self.now, received_at=self.now, self_author=False,
                        content="synthetic " + scene + " content", content_format="text",
                        sender_name="synthetic name", is_bot=False, event_type="SYNTHETIC_MESSAGE_CREATE", truncated=False)
                    with store.transaction() as db:
                        store.chat.record_received(db, received, self.now)
                    with store.transaction() as db:
                        store.chat.record_sent(db,
                            {"scene": scene, "grp": conversation, "app": "database-bound-app",
                             "msg": received.message_id, "seq": 1, "expires": self.now + 300,
                             "content": "synthetic successful reply"},
                            {"id": "synthetic-out-" + scene, "timestamp": self.now}, self.now)
                with store.transaction() as db:
                    db.execute("INSERT INTO usage VALUES('2026-10',42)")
                    db.execute("INSERT INTO cache VALUES('synthetic-cache','{}',?)", (self.now - 1,))
                    store._meta(db, "gateway", {"session_id": "synthetic-session", "seq": 17})
                    db.execute("INSERT INTO inbox(app,scene,grp,msg,payload,created,expires,state) VALUES(?,?,?,?,?,?,?,?)",
                        ("database-bound-app", "group", "synthetic-group", "synthetic-queue", "{}", self.now - 90000, self.now - 1, "pending"))
                    db.execute("INSERT INTO outbox(app,scene,grp,msg,content,seq,expires,state) VALUES(?,?,?,?,?,?,?,?)",
                        ("database-bound-app", "group", "synthetic-group", "synthetic-queue", "synthetic queued reply", 1, self.now - 1, "pending"))
            finally:
                store.close()

    def test_real_query_read_only_returns_inbound_and_successful_outbound(self):
        before = self.path.read_bytes()
        captured = []

        def open_store(path, **kwargs):
            self.assertEqual(kwargs, {"read_only": True})
            store = Store(path, **kwargs)
            self.assertEqual(store.db.execute("PRAGMA query_only").fetchone()[0], 1)
            original_close = store.close

            def close():
                captured.append(store.db.total_changes)
                original_close()

            store.close = close
            return store

        for scene, conversation in (("group", "synthetic-group"), ("c2c", "synthetic-user")):
            args = ["chat-history", "--scene", scene, "--conversation", conversation, "--limit", "1000"]
            with self.subTest(scene=scene), self.offline_stack(), patch("jx3_bot.__main__.Store", side_effect=open_store), \
                    redirect_stdout(io.StringIO()) as output, self.assertLogs("jx3_bot", level="WARNING"):
                self.assertEqual(main(self.common + args), 0)
            rows = json.loads(output.getvalue())
            self.assertEqual([row["direction"] for row in rows], ["out", "in"])
            self.assertEqual(rows[0]["content"], "synthetic successful reply")
            self.assertEqual(rows[1]["content"], "synthetic " + scene + " content")
            self.assertTrue(all(row["app_id"] == "database-bound-app" for row in rows))
        self.assertEqual(captured, [0, 0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_real_unbound_query_does_not_bind_or_migrate(self):
        with instance_lock(self.path):
            store = Store(self.path)
            try:
                with store.transaction() as db:
                    db.execute("DELETE FROM meta WHERE key IN ('app_id','chat_schema_version')")
            finally:
                store.close()
        before = self.path.read_bytes()
        with self.offline_stack(), self.assertLogs("jx3_bot", level="ERROR"), redirect_stdout(io.StringIO()):
            self.assertEqual(main(self.common + self.query), 2)
        self.assertEqual(self.path.read_bytes(), before)
        store = Store(self.path, read_only=True)
        try:
            self.assertIsNone(store.meta("app_id"))
            self.assertIsNone(store.meta("chat_schema_version"))
        finally:
            store.close()

    def test_real_forget_and_cleanup_preserve_budget_and_transport(self):
        def protected_state():
            store = Store(self.path, read_only=True)
            try:
                return {table: [tuple(row) for row in store._read("SELECT * FROM " + table)]
                        for table in ("usage", "cache", "inbox", "outbox")}, store.meta("gateway")
            finally:
                store.close()

        before = protected_state()
        args = ["chat-forget", "--scene", "group", "--conversation", "synthetic-group", "--confirm"]
        with self.offline_stack(), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(self.common + args), 0)
        self.assertEqual(json.loads(output.getvalue())["retired_messages"], 2)
        self.assertEqual(protected_state(), before)
        with self.offline_stack(), redirect_stdout(io.StringIO()):
            self.assertEqual(main(self.common + ["chat-cleanup"]), 0)
        self.assertEqual(protected_state(), before)
        store = Store(self.path, read_only=True)
        try:
            self.assertEqual(store.chat.group_messages("database-bound-app", "synthetic-group"), [])
            self.assertEqual(len(store.chat.user_messages("database-bound-app", "synthetic-user")), 2)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
