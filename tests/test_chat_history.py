"""Offline chat history tests: synthetic OpenIDs/text and temporary SQLite only."""
from dataclasses import replace
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from jx3_bot.errors import ConfigurationError, StorageError
from jx3_bot.integrations.qq.models import ReceivedMessage
from jx3_bot.observability import LogSettings, Observer
from jx3_bot.modules.chat_history import ChatRepository, ChatSettings, SCHEMA
from jx3_bot.store import Store

DAY = 86400
NOW = 1791882000.0


class SyntheticObserver:
    """Exercises repository sanitizer delegation without console escaping or IO."""
    def __init__(self):
        self.calls = []
        self.events = []

    def redact(self, value):
        self.calls.append(value)
        value = value.replace("SYNTHETIC_SECRET", "[REDACTED]")
        return re.sub(r"https?://[^\s<>]+", "[URL隐藏]", value)

    def event(self, name, **fields):
        self.events.append((name, fields))


def message(**values):
    defaults = dict(app_id="app", scene="group", conversation_id="g", user_id="u",
                    message_id="in-1", timestamp=NOW, event_type="GROUP_MESSAGE_CREATE",
                    content="普通测试聊天", sender_name=None, is_bot=None, bot_author=False,
                    self_author=False, truncated=False, content_format="text", received_at=None)
    defaults.update(values)
    return ReceivedMessage(**defaults)


def reply(**values):
    defaults = dict(app="app", scene="group", grp="g", msg="in-1", seq=1,
                    content="成功测试回复", payload=None, expires=NOW + 300,
                    state="sending", attempts=1)
    defaults.update(values)
    return defaults


class ChatSettingsTests(unittest.TestCase):
    def test_archives_all_supported_scenes_without_config(self):
        cfg = ChatSettings.from_env({})
        self.assertEqual(cfg, ChatSettings())
        self.assertTrue(cfg.allows("group", "g"))
        self.assertTrue(cfg.allows("c2c", "u"))
        self.assertFalse(cfg.allows("unknown", "g"))
        self.assertFalse(cfg.allows("group", "bad id"))
        self.assertEqual(cfg.max_text_chars, 16384)

    def test_legacy_opt_in_and_retention_environment_keys_are_ignored(self):
        cfg = ChatSettings.from_env({"BOT_CHAT_ENABLED": "false", "BOT_CHAT_GROUPS": "bad id",
                                     "BOT_CHAT_USERS": "", "BOT_CHAT_TEXT_DAYS": "1",
                                     "BOT_CHAT_DEDUP_DAYS": "2", "BOT_CHAT_MAX_TEXT_CHARS": "128"})
        self.assertEqual(cfg.max_text_chars, 128)
        self.assertTrue(cfg.allows("group", "unlisted-group"))
        self.assertTrue(cfg.allows("c2c", "unlisted-user"))
        self.assertNotIn("bad id", repr(cfg))

    def test_text_limit_is_validated(self):
        for value in ("1.0", "1_0", "+1", "-1", "", "true", 1, True, None, "127", "65537"):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                ChatSettings.from_env({"BOT_CHAT_MAX_TEXT_CHARS": value})
        for value in (127, 65537, True):
            with self.assertRaises(ConfigurationError):
                ChatSettings(max_text_chars=value)
        self.assertEqual(ChatSettings(max_text_chars=65536).max_text_chars, 65536)


class ChatRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "chat.sqlite"
        self.observer = SyntheticObserver()
        self.store = Store(self.path, self.observer)
        self.store.bind_app("app")
        with self.store.transaction() as db:
            for statement in SCHEMA:
                db.execute(statement)
        self.chat = ChatRepository(self.store)
        self.assertTrue(self.chat.settings.allows("group", "g"))
        self.cfg = ChatSettings()
        self.chat.configure(self.cfg)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def receive(self, item=None, now=NOW, **values):
        with self.store.transaction() as db:
            return self.chat.record_received(db, item if item is not None else message(**values), now)

    def send(self, task=None, response=None, now=NOW + 1):
        with self.store.transaction() as db:
            return self.chat.record_sent(db, task or reply(), response if response is not None else {"id": "out-1", "timestamp": NOW + 1}, now)

    def clean(self, now):
        with self.store.transaction() as db:
            return self.chat.cleanup(db, now)

    def rows(self, table):
        return [dict(row) for row in self.store._read(f"SELECT * FROM {table} ORDER BY id")]

    def groups(self, **kwargs):
        kwargs.pop("now", None)  # Compatibility with historical query fixtures.
        return self.chat.group_messages("app", kwargs.pop("group", "g"), **kwargs)

    def users(self, **kwargs):
        kwargs.pop("now", None)
        return self.chat.user_messages("app", kwargs.pop("user", "u"), **kwargs)

    def clone(self, table, row, **changes):
        row = dict(row)
        row.pop("id", None)
        row.update(changes)
        self.store.db.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))

    def test_four_business_tables_and_no_cancelled_columns(self):
        names = {r[0] for r in self.store._read("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'chat_%'")}
        business_tables = {statement.split("(", 1)[0].split()[-1] for statement in SCHEMA
                           if statement.startswith("CREATE TABLE")}
        self.assertEqual(business_tables, {"chat_users", "chat_groups", "chat_user_messages", "chat_group_messages"})
        self.assertTrue(business_tables.issubset(names))
        for table in ("chat_user_messages", "chat_group_messages"):
            columns = {r[1] for r in self.store._read(f"PRAGMA table_info({table})")}
            self.assertFalse(columns.intersection({"at_me", "is_at_me", "at_me_source", "status", "send_key", "local_send_key", "session_id", "identity_id", "peer_user_id"}))
        self.assertIn("user_id", {r[1] for r in self.store._read("PRAGMA table_info(chat_user_messages)")})
        self.assertFalse(self.store._read("PRAGMA foreign_key_check"))

    def test_default_archives_unlisted_group_and_private_chat(self):
        self.chat.configure(ChatSettings())
        self.assertIsNotNone(self.receive(conversation_id="other", content="群聊保存"))
        self.assertIsNotNone(self.receive(scene="c2c", conversation_id="u2", user_id="u2",
                                          event_type="C2C_MESSAGE_CREATE", message_id="c2c-2", content="单聊保存"))
        self.assertEqual(self.rows("chat_group_messages")[0]["content"], "群聊保存")
        self.assertEqual(self.rows("chat_user_messages")[0]["content"], "单聊保存")

    def test_group_and_c2c_share_user_identity_not_conversations(self):
        self.receive(sender_name="合成昵称")
        self.receive(scene="c2c", conversation_id="u", event_type="C2C_MESSAGE_CREATE", message_id="same")
        self.assertEqual(len(self.rows("chat_users")), 1)
        self.assertEqual(len(self.rows("chat_groups")), 1)
        self.assertEqual(self.users()[0]["user_id"], self.groups()[0]["sender_user_id"])
        self.assertEqual(self.groups()[0]["direction"], "in")
        self.assertEqual(self.groups()[0]["source"], "qq_event")
        self.assertIsNone(self.groups()[0]["confirmed_at"])
        self.assertIsNone(self.groups()[0]["msg_seq"])

    def test_application_and_group_isolation(self):
        self.receive()
        self.receive(app_id="other-app")
        self.receive(conversation_id="g2")
        self.assertEqual(len(self.rows("chat_group_messages")), 3)
        self.assertEqual(len(self.groups()), 1)
        self.assertEqual(len(self.rows("chat_users")), 2)
        self.assertEqual(len(self.rows("chat_groups")), 3)

    def test_full_and_at_duplicate_do_not_modify_first_projection(self):
        first = self.receive(sender_name="先到昵称")
        self.assertIsNone(self.receive(event_type="GROUP_AT_MESSAGE_CREATE", content="不同投影", sender_name="不能覆盖"))
        self.assertIsNotNone(first)
        self.assertEqual(len(self.rows("chat_group_messages")), 1)
        self.assertEqual(self.groups()[0]["event_type"], "GROUP_MESSAGE_CREATE")
        self.assertEqual(self.rows("chat_users")[0]["display_name"], "先到昵称")

    def test_user_names_are_safe_and_old_events_do_not_regress_metadata(self):
        self.receive(timestamp=NOW + 10, sender_name="新SYNTHETIC_SECRET", is_bot=True)
        self.receive(timestamp=NOW, message_id="older", sender_name="旧昵称", is_bot=False)
        self.receive(timestamp=NOW + 20, message_id="unnamed")
        user = self.rows("chat_users")[0]
        self.assertEqual(user["display_name"], "新[REDACTED]")
        self.assertEqual(user["name_updated_at"], NOW + 10)
        self.assertEqual(user["last_seen_at"], NOW + 20)
        self.assertEqual(user["first_seen_at"], NOW)
        self.assertEqual(user["is_bot"], 1)
        self.assertEqual(self.rows("chat_group_messages")[1]["sender_name"], "旧昵称")

    def test_redaction_precedes_truncation_and_keeps_newlines(self):
        self.chat.configure(replace(self.cfg, max_text_chars=128))
        body = "x" * 123 + "SYNTHETIC_SECRET" + "\n尾部"
        self.receive(content=body, sender_name="SYNTHETIC_SECRET\n昵称")
        row = self.groups()[0]
        self.assertTrue(row["truncated"])
        self.assertEqual(len(row["content"]), 128)
        self.assertNotIn("SYNTH", row["content"])
        self.assertEqual(row["sender_name"], "[REDACTED]\n昵称")
        self.receive(message_id="multiline", content="第一行\n第二行\t末尾")
        self.assertEqual(self.groups()[0]["content"], "第一行\n第二行\t末尾")
        self.assertEqual(self.observer.calls[0], body)

    def test_real_observer_sanitizes_credentials_urls_and_preserves_markdown_lines(self):
        self.store.observer = Observer(LogSettings(chat=False, receive="off"), ("SYNTHETIC_SECRET",))
        self.chat.configure(replace(self.cfg, max_text_chars=128))
        body = "# 合成标题\nSYNTHETIC_SECRET\nBearer synthetic-bearer\nhttps://synthetic.invalid/private\n尾部"
        self.receive(content=body, sender_name="SYNTHETIC_SECRET\n昵称")
        row = self.groups()[0]
        self.assertEqual(row["sender_name"], "[REDACTED]\n昵称")
        self.assertIn("# 合成标题\n", row["content"])
        self.assertIn("[AUTH REDACTED]", row["content"])
        self.assertIn("[URL隐藏]", row["content"])
        self.assertNotIn("synthetic-bearer", row["content"])
        self.assertNotIn("SYNTHETIC_SECRET", row["content"])
        self.assertNotIn("\\n", row["content"])

    def test_received_time_fallback_is_preserved_without_retention(self):
        self.receive(timestamp=None, received_at=NOW, now=NOW + DAY)
        row = self.rows("chat_group_messages")[0]
        self.assertIsNone(row["message_at"])
        self.assertEqual(row["received_at"], NOW)
        self.assertEqual(row["created_at"], NOW + DAY)
        self.clean(NOW + 1000 * DAY)
        self.assertEqual(self.groups()[0]["content"], "普通测试聊天")
        self.assertIsNone(self.receive(timestamp=None, received_at=NOW, now=NOW + 1001 * DAY))

    def test_restart_keeps_history_without_retention_policy(self):
        self.receive()
        self.chat.configure(ChatSettings())
        self.clean(NOW + 1000 * DAY)
        self.store.close()
        self.store = Store(self.path, self.observer)
        self.chat = ChatRepository(self.store)
        self.assertEqual(self.groups()[0]["content"], "普通测试聊天")
        self.assertIsNone(self.receive(now=NOW + 1001 * DAY))

    def test_read_only_store_queries_do_not_create_or_update_tables(self):
        self.receive()
        reader = Store(self.path, self.observer, read_only=True)
        try:
            rows = reader.chat.group_messages("app", "g")
            self.assertEqual(len(rows), 1)
            self.assertEqual(reader.db.total_changes, 0)
            self.assertEqual(reader.db.execute("PRAGMA query_only").fetchone()[0], 1)
        finally:
            reader.close()

    def test_normalized_omission_and_non_text_are_preserved(self):
        self.receive(content="[图片]", content_format="non_text", truncated=True)
        self.assertEqual(self.groups()[0]["content_format"], "non_text")
        self.assertTrue(self.groups()[0]["truncated"])

    def test_external_bot_archives_without_bot_identity_invention(self):
        self.receive(bot_author=True, is_bot=True, user_id="external-bot")
        self.assertEqual(self.rows("chat_users")[0]["user_openid"], "external-bot")
        self.assertEqual(self.rows("chat_users")[0]["is_bot"], 1)

    def test_self_echo_matches_existing_out_and_unmatched_only_diagnoses(self):
        self.receive()
        self.send()
        self.assertIsNone(self.receive(self_author=True, user_id="app-self", message_id="out-1"))
        self.assertIsNone(self.receive(self_author=True, user_id="app-self", message_id="unknown-self"))
        self.assertEqual(len(self.rows("chat_group_messages")), 2)
        self.assertEqual(len(self.rows("chat_users")), 1)
        self.assertEqual(self.observer.events, [("chat.self_unmatched", {"scene": "group"})])

    def test_success_out_has_real_qq_id_and_same_scope_reply_target(self):
        incoming = self.receive()
        projection = self.send()
        row = self.rows("chat_group_messages")[1]
        self.assertEqual(row["qq_message_id"], "out-1")
        self.assertEqual(row["reply_to_id"], incoming)
        self.assertIsNone(row["sender_user_id"])
        self.assertIsNone(row["received_at"])
        self.assertIsNone(row["event_type"])
        self.assertEqual(row["confirmed_at"], NOW + 1)
        self.assertEqual(projection["direction"], "out")
        self.assertEqual(len(self.rows("chat_users")), 1)

    def test_c2c_out_uses_user_id_without_creating_robot_user(self):
        projection = self.send(task=reply(scene="c2c", grp="u", msg="legacy"))
        self.assertIsNotNone(projection)
        row = self.users(now=NOW + 2)[0]
        self.assertEqual(row["direction"], "out")
        self.assertIsNone(row["reply_to_id"])
        self.assertEqual(self.rows("chat_users")[0]["user_openid"], "u")
        self.assertEqual(self.rows("chat_groups"), [])

    def test_markdown_out_preserves_actual_payload_content_not_empty_outbox_content(self):
        payload = {"msg_type": 2, "markdown": {"content": "# 标题\nSYNTHETIC_SECRET"}, "extra": "DO_NOT_COPY"}
        projection = self.send(task=reply(content="", payload=json.dumps(payload)), response={"id": "md-id", "timestamp": "2026-10-13T10:00:00Z", "ext_info": "DO_NOT_COPY"})
        self.assertEqual(projection["content"], "# 标题\n[REDACTED]")
        self.assertEqual(projection["content_format"], "markdown")
        self.assertNotIn("DO_NOT_COPY", repr(projection))
        self.assertEqual(self.rows("chat_group_messages")[0]["content"], projection["content"])

    def test_text_payload_is_actual_content_and_success_missing_timestamp_stays_missing(self):
        projection = self.send(task=reply(content="unused", payload={"msg_type": 0, "content": "实际\n内容"}), response={"id": "plain-id"})
        self.assertEqual(projection["content"], "实际\n内容")
        row = self.groups(now=NOW + 2)[0]
        self.assertIsNone(row["message_at"])
        self.assertEqual(row["timeline_source"], "created_at")
        self.assertEqual(row["timeline_at"], NOW + 1)

    def test_invalid_success_confirmation_does_not_insert_history(self):
        for response in ({}, {"id": None}, {"id": ""}, {"id": "bad id"}, {"id": 1}):
            with self.subTest(response=response), self.assertRaises(StorageError):
                self.send(response=response)
        self.assertEqual(self.rows("chat_group_messages"), [])
        self.assertEqual(self.rows("chat_groups"), [])

    def test_success_archive_retry_is_idempotent(self):
        self.receive()
        self.send()
        self.assertIsNone(self.send())
        self.assertEqual(len(self.rows("chat_group_messages")), 2)
        with self.assertRaises(StorageError):
            self.send(response={"id": "another-out", "timestamp": NOW + 2})
        self.assertEqual(len(self.rows("chat_group_messages")), 2)

    def test_legacy_queue_without_archived_in_does_not_fabricate_input(self):
        self.receive(conversation_id="g2")
        self.send()
        rows = self.rows("chat_group_messages")
        self.assertEqual([r["direction"] for r in rows], ["in", "out"])
        self.assertIsNone(rows[1]["reply_to_id"])

    def test_receive_and_success_roll_back_with_callers_transaction(self):
        for writer in (lambda db: self.chat.record_received(db, message(), NOW),
                       lambda db: self.chat.record_sent(db, reply(), {"id": "out-1"}, NOW + 1)):
            with self.assertRaises(RuntimeError):
                with self.store.transaction() as db:
                    writer(db)
                    self.assertTrue(db.in_transaction)
                    raise RuntimeError("synthetic rollback")
            self.assertEqual(self.rows("chat_group_messages"), [])
            self.assertEqual(self.rows("chat_users"), [])
            self.assertEqual(self.rows("chat_groups"), [])

    def test_fk_forbids_cross_app_owner_sender_and_reply_target(self):
        self.receive()
        self.receive(app_id="other-app", message_id="other-app-in")
        a, b = self.rows("chat_group_messages")
        for changes in (dict(group_id=b["group_id"]), dict(sender_user_id=b["sender_user_id"])):
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.clone("chat_group_messages", a, qq_message_id="bad", **changes)
        self.send()
        out = self.rows("chat_group_messages")[-1]
        with self.assertRaises(sqlite3.IntegrityError):
            self.clone("chat_group_messages", out, qq_message_id="bad-target", msg_seq=2, reply_to_id=b["id"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("DELETE FROM chat_groups WHERE id=?", (a["group_id"],))

    def test_checks_reject_invalid_direction_ids_sender_and_send_fields(self):
        self.receive()
        row = self.rows("chat_group_messages")[0]
        for changes in (dict(direction="pending"), dict(qq_message_id=None), dict(msg_seq=1),
                        dict(confirmed_at=NOW), dict(sender_user_id=None), dict(content_format="raw"),
                        dict(truncated=2), dict(deleted_at=NOW), dict(dedup_expires_at=NOW),
                        dict(direction="out", source="bot_http", event_type=None, received_at=None,
                             sender_user_id=None, confirmed_at=NOW, msg_seq=0)):
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                values = dict(qq_message_id="invalid-row")
                values.update(changes)
                self.clone("chat_group_messages", row, **values)

    def test_reply_target_must_be_inbound_even_when_fk_scope_matches(self):
        self.send()
        outgoing = self.rows("chat_group_messages")[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.clone("chat_group_messages", outgoing, qq_message_id="invalid-reply", reply_to_id=outgoing["id"], msg_seq=2)

    def test_legacy_expiry_columns_never_retire_inbound_or_outbound_history(self):
        self.receive(sender_name="保留昵称")
        self.send(now=NOW + DAY, task=reply(expires=NOW + DAY + 300), response={"id": "out-1", "timestamp": NOW + DAY})
        with self.store.transaction() as db:
            db.execute("UPDATE chat_group_messages SET expires_at=?,dedup_expires_at=?", (NOW - DAY, NOW - DAY))
        counts = self.clean(NOW + 1000 * DAY)
        self.assertEqual(counts["retired_messages"], 0)
        self.assertEqual(counts["deleted_messages"], 0)
        rows = self.rows("chat_group_messages")
        self.assertEqual([r["content"] for r in rows], ["普通测试聊天", "成功测试回复"])
        self.assertEqual(rows[1]["reply_to_id"], rows[0]["id"])
        self.assertEqual(self.rows("chat_users")[0]["display_name"], "保留昵称")
        self.assertEqual(len(self.groups(now=NOW + 1000 * DAY)), 2)
        self.assertFalse(self.store._read("PRAGMA foreign_key_check"))
        self.assertIsNone(self.receive(now=NOW + 1001 * DAY))

    def test_old_unseen_event_is_archived_permanently(self):
        self.receive(timestamp=NOW - 1000 * DAY, sender_name="旧昵称", content="旧正文")
        self.clean(NOW + 1000 * DAY)
        row = self.groups()[0]
        self.assertEqual(row["content"], "旧正文")
        self.assertIsNone(row["deleted_at"])
        self.assertEqual(self.rows("chat_users")[0]["display_name"], "旧昵称")
        self.assertIsNotNone(self.receive(timestamp=NOW - 2000 * DAY, message_id="older"))

    def test_forget_exact_scope_tombstones_and_blocks_replay_after_dedup_deletion(self):
        self.receive(sender_name="共同昵称")
        self.receive(conversation_id="g2", message_id="g2-in", content="另群保留")
        self.receive(scene="c2c", conversation_id="u", message_id="private", event_type="C2C_MESSAGE_CREATE", content="单聊保留")
        counts = self.chat.forget("group", "g", NOW + 10)
        self.assertEqual(counts, {"retired_messages": 1, "tombstones": 1})
        self.assertEqual(self.groups(now=NOW + 11), [])
        self.assertEqual(self.groups(group="g2", now=NOW + 11)[0]["content"], "另群保留")
        self.assertEqual(self.users(now=NOW + 11)[0]["content"], "单聊保留")
        self.assertEqual(self.rows("chat_users")[0]["display_name"], "共同昵称")
        self.assertIsNone(self.receive(message_id="old-unseen", now=NOW + 11))
        self.clean(NOW + 1000 * DAY)
        self.assertIsNone(self.receive(now=NOW + 1001 * DAY))
        self.receive(message_id="new-input", timestamp=NOW + 1001 * DAY, now=NOW + 1001 * DAY)
        self.assertEqual(len(self.rows("chat_group_messages")), 3)

    def test_forget_preserves_other_application_records_and_scrubs_sole_profile(self):
        self.receive(sender_name="本应用昵称")
        self.receive(app_id="other-app", sender_name="其他应用昵称", message_id="other-app-in")
        self.chat.forget("group", "g", NOW + 1)
        users = self.rows("chat_users")
        self.assertIsNone(users[0]["display_name"])
        self.assertEqual(users[1]["display_name"], "其他应用昵称")
        self.assertEqual(self.rows("chat_group_messages")[1]["content"], "普通测试聊天")

    def test_forget_old_prearchive_outbox_cannot_resurrect_scope(self):
        self.chat.forget("group", "g", NOW + 10)
        self.assertIsNone(self.send(now=NOW + 11, response={"id": "old-confirmed-id"}))
        self.assertEqual(self.rows("chat_group_messages"), [])

    def test_forget_unknown_scope_still_prevents_old_events_and_requires_bound_app(self):
        self.chat.forget("group", "g", NOW + 10)
        self.assertIsNone(self.receive(now=NOW + 11))
        self.assertEqual(self.rows("chat_users"), [])
        self.store.db.execute("DELETE FROM meta WHERE key='app_id'")
        with self.assertRaises(StorageError):
            self.chat.forget("group", "g", NOW)
        for scene, target in (("other", "g"), ("group", "bad id")):
            with self.assertRaises(ConfigurationError):
                self.chat.forget(scene, target, NOW)

    def test_queries_are_read_only_bounded_half_open_with_stable_id_pagination(self):
        for index, stamp in enumerate((NOW, NOW + 1, NOW, NOW + 2)):
            self.receive(message_id=f"m-{index}", timestamp=stamp)
        before_changes = self.store.db.total_changes
        rows = self.groups(start=NOW, end=NOW + 2, limit=2)
        self.assertEqual([r["qq_message_id"] for r in rows], ["m-2", "m-1"])
        older = self.groups(start=NOW, end=NOW + 2, limit=2, before_id=rows[-1]["id"])
        self.assertEqual([r["qq_message_id"] for r in older], ["m-0"])
        self.assertEqual(self.store.db.total_changes, before_changes)
        self.assertFalse(self.store.db.in_transaction)
        self.assertEqual(len(self.groups(now=NOW + 31 * DAY)), 4)

    def test_query_validation_does_not_coerce_bad_types(self):
        for values in (dict(limit=0), dict(limit=1001), dict(limit=True), dict(limit="1"),
                       dict(before_id=0), dict(before_id=True), dict(before_id="1"),
                       dict(start=True), dict(start=float("nan")), dict(start=10**400), dict(end=-1),
                       dict(start=NOW, end=NOW), dict(start=NOW + 1, end=NOW),
                       dict(start="2026-10-13T00:00:00")):
            with self.subTest(values=values), self.assertRaises(ConfigurationError):
                self.groups(**values)
        with self.assertRaises(ConfigurationError):
            self.chat.user_messages("app", "bad id")

    def test_cleanup_and_forget_protect_store_nonchat_tables(self):
        with self.store.transaction() as db:
            db.execute("INSERT INTO usage VALUES('2026-10',7)")
            db.execute("INSERT INTO cities VALUES('synthetic-city','{}')")
            db.execute("INSERT INTO preferences(app,scene,grp,usr,input) VALUES('app','group','g','u','合成城市')")
            db.execute("INSERT INTO cache VALUES('synthetic-cache','{}',?)", (NOW + DAY,))
            db.execute("INSERT INTO inbox(app,scene,grp,msg,payload,created,expires,state) VALUES('app','group','g','queued','{}',?,?,'pending')", (NOW, NOW + 300))
            db.execute("INSERT INTO outbox(app,scene,grp,msg,content,seq,expires,state,attempts) VALUES('app','group','g','queued','保留任务',3,?,'uncertain',2)", (NOW + 300,))
            self.store._meta(db, "gateway", {"session_id": "synthetic-session", "seq": 7})
        tables = ("usage", "cities", "preferences", "cache", "inbox", "outbox")
        snapshots = {table: [tuple(r) for r in self.store._read(f"SELECT * FROM {table}")] for table in tables}
        gateway = self.store.meta("gateway")
        self.receive()
        self.chat.forget("group", "g", NOW + 10)
        self.clean(NOW + 91 * DAY)
        for table, snapshot in snapshots.items():
            self.assertEqual([tuple(r) for r in self.store._read(f"SELECT * FROM {table}")], snapshot)
        self.assertEqual(self.store.meta("gateway"), gateway)
        self.assertFalse(self.store._read("PRAGMA foreign_key_check"))

    def test_bad_c2c_sender_and_timestamp_are_rejected_without_partial_rows(self):
        with self.assertRaises(ConfigurationError):
            self.receive(scene="c2c", conversation_id="u", user_id="u2")
        for stamp in (True, float("nan"), float("inf"), -1, "2026-10-13T00:00:00"):
            with self.subTest(stamp=stamp), self.assertRaises(ConfigurationError):
                self.receive(timestamp=stamp)
        self.assertEqual(self.rows("chat_users"), [])
        self.assertEqual(self.rows("chat_group_messages"), [])


if __name__ == "__main__":
    unittest.main()
