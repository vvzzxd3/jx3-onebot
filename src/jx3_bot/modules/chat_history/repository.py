"""Chat-only SQL. Receipt/success writers NEVER open or commit a transaction.

Existing expiry columns are legacy schema-compatibility placeholders, never
used to expire chat history. Explicit forget leaves a replay-blocking watermark;
logical deletion is not secure erasure of WAL files or backups.
"""
from datetime import datetime
import hashlib
import json
import math

from ...errors import ConfigurationError, StorageError
from .config import ChatSettings, valid_identity

# The legacy NOT NULL expiry columns remain for in-place compatibility only.
# No application path consults them or expires chat data automatically.
PERMANENT_EXPIRY = 1e300
TABLES = {"c2c": ("chat_user_messages", "user_id", "chat_users", "user_openid"),
          "group": ("chat_group_messages", "group_id", "chat_groups", "group_openid")}


def _message_schema(scene):
    table, owner, entity, _ = TABLES[scene]
    sender = ("sender_user_id INTEGER,\n"
              "FOREIGN KEY(sender_user_id,app_id) REFERENCES chat_users(id,app_id),"
              "CHECK((direction='in' AND sender_user_id IS NOT NULL) OR"
              " (direction='out' AND sender_user_id IS NULL)),") if scene == "group" else ""
    return f"""CREATE TABLE IF NOT EXISTS {table}(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id TEXT NOT NULL CHECK(length(app_id) BETWEEN 1 AND 256),
        {owner} INTEGER NOT NULL,
        direction TEXT NOT NULL CHECK(direction IN ('in','out')),
        qq_message_id TEXT NOT NULL CHECK(length(qq_message_id) BETWEEN 1 AND 256),
        reply_to_id INTEGER,
        msg_seq INTEGER,
        content TEXT NOT NULL,
        content_format TEXT NOT NULL CHECK(content_format IN ('text','markdown','non_text')),
        sender_name TEXT,
        source TEXT NOT NULL CHECK(source IN ('qq_event','bot_http')),
        event_type TEXT,
        message_at REAL CHECK(message_at IS NULL OR message_at>=0),
        received_at REAL CHECK(received_at IS NULL OR received_at>=0),
        confirmed_at REAL CHECK(confirmed_at IS NULL OR confirmed_at>=0),
        created_at REAL NOT NULL CHECK(created_at>=0),
        truncated INTEGER NOT NULL CHECK(truncated IN (0,1)),
        expires_at REAL NOT NULL CHECK(expires_at>=0),
        dedup_expires_at REAL NOT NULL CHECK(dedup_expires_at>=expires_at),
        deleted_at REAL CHECK(deleted_at IS NULL OR deleted_at>=0),
        {sender}
        UNIQUE(app_id,{owner},qq_message_id),
        UNIQUE(id,app_id,{owner}),
        FOREIGN KEY({owner},app_id) REFERENCES {entity}(id,app_id),
        FOREIGN KEY(reply_to_id,app_id,{owner}) REFERENCES {table}(id,app_id,{owner}),
        CHECK(reply_to_id IS NULL OR reply_to_id<>id),
        CHECK((direction='in' AND source='qq_event' AND event_type IS NOT NULL
               AND received_at IS NOT NULL AND confirmed_at IS NULL
               AND msg_seq IS NULL AND reply_to_id IS NULL)
           OR (direction='out' AND source='bot_http' AND event_type IS NULL
               AND received_at IS NULL AND confirmed_at IS NOT NULL
               AND msg_seq IS NOT NULL AND typeof(msg_seq)='integer' AND msg_seq BETWEEN 1 AND {5 if scene == 'group' else 4})),
        CHECK(deleted_at IS NULL OR (content='' AND sender_name IS NULL)))"""


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS chat_users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id TEXT NOT NULL CHECK(length(app_id) BETWEEN 1 AND 256),
        user_openid TEXT NOT NULL CHECK(length(user_openid) BETWEEN 1 AND 256),
        display_name TEXT,
        is_bot INTEGER CHECK(is_bot IS NULL OR is_bot IN (0,1)),
        name_updated_at REAL CHECK(name_updated_at IS NULL OR name_updated_at>=0),
        first_seen_at REAL NOT NULL CHECK(first_seen_at>=0),
        last_seen_at REAL NOT NULL CHECK(last_seen_at>=first_seen_at),
        created_at REAL NOT NULL CHECK(created_at>=0),
        updated_at REAL NOT NULL CHECK(updated_at>=created_at),
        UNIQUE(app_id,user_openid), UNIQUE(id,app_id))""",
    """CREATE TABLE IF NOT EXISTS chat_groups(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id TEXT NOT NULL CHECK(length(app_id) BETWEEN 1 AND 256),
        group_openid TEXT NOT NULL CHECK(length(group_openid) BETWEEN 1 AND 256),
        name TEXT,
        first_seen_at REAL NOT NULL CHECK(first_seen_at>=0),
        last_seen_at REAL NOT NULL CHECK(last_seen_at>=first_seen_at),
        created_at REAL NOT NULL CHECK(created_at>=0),
        updated_at REAL NOT NULL CHECK(updated_at>=created_at),
        UNIQUE(app_id,group_openid), UNIQUE(id,app_id))""",
    _message_schema("c2c"), _message_schema("group"),
) + tuple(statement for scene in ("c2c", "group") for statement in (
    f"CREATE INDEX IF NOT EXISTS {TABLES[scene][0]}_timeline ON {TABLES[scene][0]}(app_id,{TABLES[scene][1]},message_at,id)",
    f"CREATE INDEX IF NOT EXISTS {TABLES[scene][0]}_pagination ON {TABLES[scene][0]}(app_id,{TABLES[scene][1]},id)",
    f"CREATE UNIQUE INDEX IF NOT EXISTS {TABLES[scene][0]}_reply_seq ON {TABLES[scene][0]}(app_id,{TABLES[scene][1]},reply_to_id,msg_seq) WHERE direction='out' AND reply_to_id IS NOT NULL",
    # Composite FKs enforce application/conversation; triggers enforce the target direction.
    f"""CREATE TRIGGER IF NOT EXISTS {TABLES[scene][0]}_reply_insert
        BEFORE INSERT ON {TABLES[scene][0]} WHEN NEW.reply_to_id IS NOT NULL
        AND NOT EXISTS(SELECT 1 FROM {TABLES[scene][0]} WHERE id=NEW.reply_to_id
            AND app_id=NEW.app_id AND {TABLES[scene][1]}=NEW.{TABLES[scene][1]} AND direction='in')
        BEGIN SELECT RAISE(ABORT,'reply target must be an inbound message'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS {TABLES[scene][0]}_reply_update
        BEFORE UPDATE OF reply_to_id,app_id,{TABLES[scene][1]} ON {TABLES[scene][0]}
        WHEN NEW.reply_to_id IS NOT NULL
        AND NOT EXISTS(SELECT 1 FROM {TABLES[scene][0]} WHERE id=NEW.reply_to_id
            AND app_id=NEW.app_id AND {TABLES[scene][1]}=NEW.{TABLES[scene][1]} AND direction='in')
        BEGIN SELECT RAISE(ABORT,'reply target must be an inbound message'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS {TABLES[scene][0]}_referenced_direction
        BEFORE UPDATE OF direction ON {TABLES[scene][0]} WHEN NEW.direction<>'in'
        AND EXISTS(SELECT 1 FROM {TABLES[scene][0]} WHERE reply_to_id=OLD.id)
        BEGIN SELECT RAISE(ABORT,'referenced reply target must remain inbound'); END""",
)) + ("CREATE INDEX IF NOT EXISTS chat_group_messages_sender ON chat_group_messages(sender_user_id,app_id)",)


def _timestamp(value, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError
            value = stamp.timestamp()
        except (ValueError, OverflowError):
            raise ConfigurationError("Chat timestamps require finite epoch seconds or timezone-aware ISO timestamps") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError("Chat timestamps require finite nonnegative epoch seconds")
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise ConfigurationError("Chat timestamps require finite nonnegative epoch seconds") from None
    if not math.isfinite(value) or value < 0:
        raise ConfigurationError("Chat timestamps require finite nonnegative epoch seconds")
    return value


def _identity(value):
    if not valid_identity(value):
        raise ConfigurationError("Chat identity is invalid (value suppressed)")
    return value


class ChatRepository:
    def __init__(self, store):
        self.store = store
        self.settings = ChatSettings()

    @staticmethod
    def _meta(db, key, default=None):
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _floor(self, db, key, value):
        old = self._meta(db, key, 0)
        if value > old:
            self.store._meta(db, key, value)
        return max(value, old)

    @staticmethod
    def _scope_key(app, scene, conversation):
        raw = json.dumps([app, scene, conversation], ensure_ascii=False, separators=(",", ":"))
        return "chat_forget_before:" + hashlib.sha256(raw.encode()).hexdigest()

    def configure(self, settings):
        """Configure text safety only; never shorten or purge persistent history."""
        if not isinstance(settings, ChatSettings):
            raise ConfigurationError("ChatRepository requires ChatSettings")
        self.settings = settings

    def allows(self, message):
        return message is not None and self.settings.allows(message.scene, message.conversation_id)

    def _clean(self, value, maximum=None):
        if not isinstance(value, str):
            raise ConfigurationError("Chat text must be a string")
        # Shared sanitizer, without the console's newline escaping or console length limit.
        cleaned = self.store.observer.redact(value)
        if not isinstance(cleaned, str):
            raise StorageError("Chat sanitizer returned invalid text")
        maximum = self.settings.max_text_chars if maximum is None else maximum
        return cleaned[:maximum], len(cleaned) > maximum

    def _name(self, name):
        if name is None:
            return None
        name, _ = self._clean(name, min(256, self.settings.max_text_chars))
        return name if name.strip() else None

    def _user(self, db, app, user, name, is_bot, occurred, now):
        db.execute("""INSERT INTO chat_users(app_id,user_openid,display_name,is_bot,name_updated_at,
            first_seen_at,last_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(app_id,user_openid) DO UPDATE SET
            display_name=CASE WHEN excluded.display_name IS NOT NULL AND
                (chat_users.name_updated_at IS NULL OR excluded.name_updated_at>=chat_users.name_updated_at)
                THEN excluded.display_name ELSE chat_users.display_name END,
            name_updated_at=CASE WHEN excluded.display_name IS NOT NULL THEN
                MAX(COALESCE(chat_users.name_updated_at,0),excluded.name_updated_at) ELSE chat_users.name_updated_at END,
            is_bot=CASE WHEN excluded.last_seen_at>=chat_users.last_seen_at
                THEN COALESCE(excluded.is_bot,chat_users.is_bot) ELSE chat_users.is_bot END,
            first_seen_at=MIN(chat_users.first_seen_at,excluded.first_seen_at),
            last_seen_at=MAX(chat_users.last_seen_at,excluded.last_seen_at),
            updated_at=MAX(chat_users.updated_at,excluded.updated_at)""",
            (app, user, name, is_bot, occurred if name else None, occurred, occurred, now, now))
        return db.execute("SELECT id FROM chat_users WHERE app_id=? AND user_openid=?", (app, user)).fetchone()[0]

    def _group(self, db, app, group, occurred, now):
        db.execute("""INSERT INTO chat_groups(app_id,group_openid,first_seen_at,last_seen_at,created_at,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(app_id,group_openid) DO UPDATE SET
            first_seen_at=MIN(chat_groups.first_seen_at,excluded.first_seen_at),
            last_seen_at=MAX(chat_groups.last_seen_at,excluded.last_seen_at),
            updated_at=MAX(chat_groups.updated_at,excluded.updated_at)""", (app, group, occurred, occurred, now, now))
        return db.execute("SELECT id FROM chat_groups WHERE app_id=? AND group_openid=?", (app, group)).fetchone()[0]

    def _existing(self, db, app, scene, conversation, qq_id):
        table, owner, entity, external = TABLES[scene]
        return db.execute(f"SELECT m.* FROM {table} m JOIN {entity} e ON e.id=m.{owner} AND e.app_id=m.app_id WHERE m.app_id=? AND e.{external}=? AND m.qq_message_id=?",
                          (app, conversation, qq_id)).fetchone()

    def existing_message(self, db, message):
        """Transaction-scoped dedup/privacy lookup for the transport job owner."""
        return self._existing(db, message.app_id, message.scene,
                              message.conversation_id, message.message_id)

    def can_send(self, db, reply, now):
        """Read-only preflight: a manually forgotten input cannot emit an old reply."""
        app, scene, target = reply['app'], reply['scene'], reply['grp']
        if not self.settings.allows(scene, target):
            return False
        if self._blocked(db, app, scene, target, reply['expires'] - 300, now):
            return False
        parent = self._existing(db, app, scene, target, reply['msg'])
        return parent is None or (parent['direction'] == 'in' and parent['deleted_at'] is None)

    def _blocked(self, db, app, scene, conversation, occurred, now):
        floor = self._meta(db, self._scope_key(app, scene, conversation), 0)
        return occurred <= floor

    def record_received(self, db, message, now):
        """Insert a supported receipt; duplicate/forgotten/self receipts return None."""
        if not self.allows(message):
            return None
        now = _timestamp(now)
        app, conversation, qq_id = map(_identity, (message.app_id, message.conversation_id, message.message_id))
        scene = message.scene
        previous = self._existing(db, app, scene, conversation, qq_id)
        if message.self_author:
            if previous and previous["direction"] == "out":
                return None  # Already archived outbound; never materialize a fake inbound author.
            self.store.observer.event("chat.self_unmatched", scene=scene)
            return None
        if previous:
            return None
        platform_at = _timestamp(message.timestamp, optional=True)
        received = _timestamp(message.received_at, optional=True)
        occurred = platform_at if platform_at is not None else received if received is not None else now
        event_at = min(occurred, received if received is not None else now, now)
        if self._blocked(db, app, scene, conversation, event_at, now):
            return None
        user = _identity(message.user_id)
        if scene == "c2c" and user != conversation:
            raise ConfigurationError("C2C sender must match its user conversation")
        if message.content_format not in {"text", "markdown", "non_text"}:
            raise ConfigurationError("Unsupported chat content format")
        if not isinstance(message.event_type, str) or not message.event_type or len(message.event_type) > 128:
            raise ConfigurationError("Invalid chat event type")
        is_bot = message.is_bot
        if is_bot is not None and type(is_bot) is not bool:
            raise ConfigurationError("Chat bot marker must be boolean or absent")
        content, clipped = self._clean(message.content)
        name = self._name(message.sender_name)
        uid = self._user(db, app, user, name, None if is_bot is None else int(is_bot), occurred, now)
        table, owner, _, _ = TABLES[scene]
        owner_id = self._group(db, app, conversation, occurred, now) if scene == "group" else uid
        fields = ["app_id", owner, "direction", "qq_message_id", "content", "content_format", "sender_name",
                  "source", "event_type", "message_at", "received_at", "created_at", "truncated",
                  "expires_at", "dedup_expires_at", "deleted_at"]
        values = [app, owner_id, "in", qq_id, content, message.content_format, name, "qq_event", message.event_type,
                  platform_at, received if received is not None else now, now, int(clipped or message.truncated),
                  PERMANENT_EXPIRY, PERMANENT_EXPIRY, None]
        if scene == "group":
            fields.append("sender_user_id")
            values.append(uid)
        cursor = db.execute(f"INSERT INTO {table}({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", values)
        return cursor.lastrowid

    def record_sent(self, db, replyDict, responseDict, now):
        """Only call after HTTP success; caller atomically updates its outbox to sent."""
        reply, response = replyDict, responseDict
        scene, conversation = reply.get("scene", "group"), reply.get("grp")
        if not self.settings.allows(scene, conversation):
            return None
        now = _timestamp(now)
        app, conversation, trigger = map(_identity, (reply.get("app"), conversation, reply.get("msg")))
        if not isinstance(response, dict) or not valid_identity(response.get("id")):
            raise StorageError("Successful QQ reply requires a valid message id")
        qq_id = response["id"]
        seq = reply.get('seq')
        if type(seq) is not int or not 1 <= seq <= (5 if scene == 'group' else 4):
            raise ConfigurationError('Successful QQ reply requires a valid fixed scene-specific msg_seq')
        previous = self._existing(db, app, scene, conversation, qq_id)
        if previous:
            parent = self._existing(db, app, scene, conversation, trigger)
            expected_parent = parent['id'] if parent and parent['direction'] == 'in' else None
            if (previous['direction'] != 'out' or previous['msg_seq'] != seq
                    or previous['reply_to_id'] != expected_parent):
                raise StorageError('Successful QQ reply conflicts with another trigger or fixed sequence')
            return None  # Same task archive retry; transport additionally binds legacy ACKs.
        platform_at = _timestamp(response.get("timestamp"), optional=True)
        occurred = platform_at if platform_at is not None else now
        event_at = min(occurred, now)
        # Old pre-archive queues are allowed, but a manually forgotten trigger
        # must not be replayed after its text was erased.
        original = _timestamp(reply.get("expires")) - 300
        if self._blocked(db, app, scene, conversation, min(event_at, original), now):
            return None
        payload = reply.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                raise StorageError("Successful QQ reply has invalid stored payload") from None
        if payload is None:
            text, content_format = reply.get("content"), "text"
        elif isinstance(payload, dict) and payload.get("msg_type") == 2:
            markdown = payload.get("markdown")
            if not isinstance(markdown, dict):
                raise StorageError("Successful QQ reply has invalid Markdown payload")
            text, content_format = markdown.get("content"), "markdown"
        elif isinstance(payload, dict) and payload.get("msg_type") == 0:
            text, content_format = payload.get("content"), "text"
        else:
            raise StorageError("Successful QQ reply has unsupported stored payload")
        content, clipped = self._clean(text)
        table, owner, _, _ = TABLES[scene]
        owner_id = (self._group(db, app, conversation, event_at, now) if scene == "group" else
                    self._user(db, app, conversation, None, None, event_at, now))
        parent = db.execute(f"SELECT id FROM {table} WHERE app_id=? AND {owner}=? AND qq_message_id=? AND direction='in'",
                            (app, owner_id, trigger)).fetchone()
        reply_to = parent[0] if parent else None
        if reply_to is not None and db.execute(f"SELECT 1 FROM {table} WHERE app_id=? AND {owner}=? AND reply_to_id=? AND msg_seq=?", (app, owner_id, reply_to, seq)).fetchone():
            raise StorageError("Successful QQ reply conflicts with an existing fixed reply sequence")
        cursor = db.execute(f"""INSERT INTO {table}(app_id,{owner},direction,qq_message_id,reply_to_id,msg_seq,
            content,content_format,source,message_at,confirmed_at,created_at,truncated,expires_at,dedup_expires_at,deleted_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (app, owner_id, "out", qq_id, reply_to, seq, content, content_format, "bot_http", platform_at,
             now, now, int(clipped), PERMANENT_EXPIRY, PERMANENT_EXPIRY, None))
        return {"id": cursor.lastrowid, "app_id": app, "scene": scene, "conversation_id": conversation,
                "message_id": qq_id, "reply_to_id": reply_to, "msg_seq": seq, "content": content,
                "content_format": content_format, "sender_name": None, "timestamp": platform_at,
                "confirmed_at": now, "truncated": clipped, "direction": "out"}

    def cleanup(self, db, now):
        """Chat history has no automatic expiry; transport/cache cleanup is separate."""
        return {"retired_messages": 0, "deleted_messages": 0, "deleted_users": 0, "deleted_groups": 0}

    def forget(self, scene, conversation, now):
        """Forget only the bound app/scope; retain tombstones and a persistent replay cutoff."""
        if scene not in TABLES:
            raise ConfigurationError("Chat forget scene must be group or c2c")
        conversation, now = _identity(conversation), _timestamp(now)
        table, owner, entity, external = TABLES[scene]
        with self.store.transaction() as db:
            app = self._meta(db, "app_id")
            if not valid_identity(app):
                raise StorageError("Chat forget requires a bound QQ app")
            self._floor(db, self._scope_key(app, scene, conversation), now)
            scope = f"{owner} IN (SELECT id FROM {entity} WHERE app_id=? AND {external}=?) AND app_id=?"
            args = (app, conversation, app)
            count = db.execute(f"UPDATE {table} SET content='',sender_name=NULL,deleted_at=? WHERE {scope} AND deleted_at IS NULL", (now, *args)).rowcount
            # Shared user profiles must not erase names belonging to another scope.
            if scene == "c2c":
                db.execute("""UPDATE chat_users SET display_name=NULL,name_updated_at=NULL
                    WHERE app_id=? AND user_openid=? AND NOT EXISTS(
                        SELECT 1 FROM chat_group_messages m WHERE m.sender_user_id=chat_users.id AND m.app_id=chat_users.app_id)""", (app, conversation))
            else:
                db.execute("UPDATE chat_groups SET name=NULL WHERE app_id=? AND group_openid=?", (app, conversation))
                db.execute("""UPDATE chat_users SET display_name=NULL,name_updated_at=NULL WHERE app_id=?
                    AND id IN (SELECT sender_user_id FROM chat_group_messages m JOIN chat_groups g
                        ON g.id=m.group_id AND g.app_id=m.app_id WHERE g.app_id=? AND g.group_openid=?)
                    AND NOT EXISTS(SELECT 1 FROM chat_user_messages m WHERE m.user_id=chat_users.id AND m.app_id=chat_users.app_id)
                    AND NOT EXISTS(SELECT 1 FROM chat_group_messages m JOIN chat_groups g
                        ON g.id=m.group_id AND g.app_id=m.app_id WHERE m.sender_user_id=chat_users.id
                        AND m.app_id=chat_users.app_id AND g.group_openid<>?)""", (app, app, conversation, conversation))
            self.store.scrub_received_copies(db, now, (app, scene, conversation))
            # All short-lived transport copies carry private text, including
            # already sent and ambiguous attempts. A claimed sending task may
            # already be in flight, so only retire unsent pending tasks.
            # Historical queued transport tasks without an archived chat parent
            # must retain their original content/state across this migration.
            db.execute(f"""UPDATE outbox SET state=CASE WHEN state='pending' THEN 'expired' ELSE state END,
                content='',payload=NULL WHERE app=? AND scene=? AND grp=? AND EXISTS(
                    SELECT 1 FROM {table} m JOIN {entity} e ON e.id=m.{owner} AND e.app_id=m.app_id
                    WHERE m.app_id=outbox.app AND e.{external}=outbox.grp
                      AND m.qq_message_id=outbox.msg AND m.direction='in')""", (app, scene, conversation))
        return {"retired_messages": count, "tombstones": count}

    def _messages(self, scene, app, conversation, start, end, limit, before_id):
        app, conversation = _identity(app), _identity(conversation)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ConfigurationError("Chat history limit must be 1..1000")
        if before_id is not None and (type(before_id) is not int or not 1 <= before_id <= 2**63 - 1):
            raise ConfigurationError("Chat history before_id must be a positive SQLite integer")
        start, end = _timestamp(start, optional=True), _timestamp(end, optional=True)
        if start is not None and end is not None and start >= end:
            raise ConfigurationError("Chat history needs start < end (end exclusive)")
        table, owner, entity, external = TABLES[scene]
        where = [f"m.app_id=?", f"e.{external}=?", "m.deleted_at IS NULL"]
        values = [app, conversation]
        if start is not None:
            where.append("COALESCE(m.message_at,m.created_at)>=?")
            values.append(start)
        if end is not None:
            where.append("COALESCE(m.message_at,m.created_at)<?")
            values.append(end)
        if before_id is not None:
            where.append("m.id<?")
            values.append(before_id)
        values.append(limit)
        # Read-only: never cleanup or mutate from a history query. Stable ingestion
        # ID pagination also works when events arrive with older platform timestamps.
        rows = self.store._read(f"SELECT m.*,COALESCE(m.message_at,m.created_at) AS timeline_at, CASE WHEN m.message_at IS NULL THEN 'created_at' ELSE 'message_at' END AS timeline_source FROM {table} m JOIN {entity} e ON e.id=m.{owner} AND e.app_id=m.app_id WHERE {' AND '.join(where)} ORDER BY m.id DESC LIMIT ?", values)
        return [dict(row) for row in rows]

    def user_messages(self, app_id, user_openid, start=None, end=None, limit=100, before_id=None):
        return self._messages("c2c", app_id, user_openid, start, end, limit, before_id)

    def group_messages(self, app_id, group_openid, start=None, end=None, limit=100, before_id=None):
        return self._messages("group", app_id, group_openid, start, end, limit, before_id)
