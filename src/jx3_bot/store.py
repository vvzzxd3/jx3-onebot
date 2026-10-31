"""SQLite transactions are the sole budget/inbox acceptance boundary."""
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import threading

from .errors import StorageError
from dataclasses import replace
from .integrations.qq.models import GroupMessage, ReceivedMessage
from .modules.weather.repository import WeatherRepository
from .modules.chat_history.repository import ChatRepository, SCHEMA as CHAT_SCHEMA
from .observability import DEFAULT_OBSERVER


class Store:
    def __init__(self, path, observer=None, *, read_only=False):
        self.observer = observer or DEFAULT_OBSERVER
        self.path = Path(path)
        self.read_only = read_only
        self.lock = threading.RLock()
        if read_only:
            try:
                self.db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True,
                    timeout=5, isolation_level=None, check_same_thread=False)
                self.db.row_factory = sqlite3.Row
                self.db.execute("PRAGMA query_only=ON")
                self.db.execute("PRAGMA busy_timeout=5000")
            except sqlite3.Error:
                if hasattr(self, "db"):
                    self.db.close()
                raise StorageError("Cannot open existing chat storage read-only") from None
            self.weather = WeatherRepository(self)
            self.chat = ChatRepository(self)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.db = sqlite3.connect(str(path), timeout=5, isolation_level=None, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage(month TEXT PRIMARY KEY, attempts INTEGER NOT NULL CHECK(attempts>=0));
                CREATE TABLE IF NOT EXISTS cities(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aliases(name TEXT NOT NULL, city_id TEXT NOT NULL REFERENCES cities(id) ON DELETE CASCADE, PRIMARY KEY(name,city_id));
                CREATE TABLE IF NOT EXISTS preferences(app TEXT, grp TEXT, usr TEXT, input TEXT NOT NULL, scene TEXT NOT NULL DEFAULT 'group', PRIMARY KEY(app,scene,grp,usr));
                CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, data TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS inbox(app TEXT, grp TEXT, msg TEXT, payload TEXT, created REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL, scene TEXT NOT NULL DEFAULT 'group', PRIMARY KEY(app,scene,grp,msg));
                CREATE TABLE IF NOT EXISTS outbox(app TEXT, grp TEXT, msg TEXT, content TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 1, expires REAL NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, scene TEXT NOT NULL DEFAULT 'group', payload TEXT, PRIMARY KEY(app,scene,grp,msg), FOREIGN KEY(app,scene,grp,msg) REFERENCES inbox(app,scene,grp,msg) ON DELETE CASCADE);
            """)
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(outbox)")}
            if "scene" not in columns:
                self._migrate_scenes()
            # Chat-history replacement under the instance lock. The maintainer
            # waived old chat preservation, NOT weather counters or pending sends.
            with self.transaction() as db:
                if "kind" not in {r[1] for r in db.execute("PRAGMA table_info(inbox)")}:
                    db.execute("ALTER TABLE inbox ADD COLUMN kind TEXT NOT NULL DEFAULT 'command' CHECK(kind IN ('command','message'))")
                if 'confirmed_qq_message_id' not in {r[1] for r in db.execute('PRAGMA table_info(outbox)')}:
                    db.execute('ALTER TABLE outbox ADD COLUMN confirmed_qq_message_id TEXT')
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS outbox_confirmed_qq_id ON outbox(app,scene,grp,confirmed_qq_message_id) WHERE confirmed_qq_message_id IS NOT NULL")
                for name in ("feature_usage", "chat_messages", "analytics_receipts"):
                    db.execute(f"DROP TABLE IF EXISTS {name}")
                db.execute("DELETE FROM meta WHERE key LIKE 'analytics_%'")
                for statement in CHAT_SCHEMA:
                    db.execute(statement)
                self._meta(db, "chat_schema_version", 1)
        except (sqlite3.Error, StorageError):
            if hasattr(self, "db"):
                self.db.close()
            raise StorageError("Cannot open bot storage") from None
        self.weather = WeatherRepository(self)
        self.chat = ChatRepository(self)

    def _migrate_scenes(self):
        # Called under the CLI instance lock. One transaction preserves counters,
        # preferences, pending commands/replies, retry attempts and uncertain state.
        with self.transaction() as db:
            db.execute("CREATE TABLE preferences_v2(app TEXT,grp TEXT,usr TEXT,input TEXT NOT NULL,scene TEXT NOT NULL DEFAULT 'group',PRIMARY KEY(app,scene,grp,usr))")
            db.execute("CREATE TABLE inbox_v2(app TEXT,grp TEXT,msg TEXT,payload TEXT,created REAL NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL,scene TEXT NOT NULL DEFAULT 'group',PRIMARY KEY(app,scene,grp,msg))")
            db.execute("CREATE TABLE outbox_v2(app TEXT,grp TEXT,msg TEXT,content TEXT NOT NULL,seq INTEGER NOT NULL DEFAULT 1,expires REAL NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,scene TEXT NOT NULL DEFAULT 'group',payload TEXT,PRIMARY KEY(app,scene,grp,msg),FOREIGN KEY(app,scene,grp,msg) REFERENCES inbox_v2(app,scene,grp,msg) ON DELETE CASCADE)")
            db.execute("INSERT INTO preferences_v2(app,grp,usr,input) SELECT app,grp,usr,input FROM preferences")
            db.execute("INSERT INTO inbox_v2(app,grp,msg,payload,created,expires,state) SELECT app,grp,msg,payload,created,expires,state FROM inbox ORDER BY rowid")
            attempts = "attempts" if "attempts" in {r[1] for r in db.execute("PRAGMA table_info(outbox)")} else "0"
            db.execute(f"INSERT INTO outbox_v2(app,grp,msg,content,seq,expires,state,attempts) SELECT app,grp,msg,content,seq,expires,state,{attempts} FROM outbox")
            db.execute("DROP TABLE outbox")
            db.execute("DROP TABLE inbox")
            db.execute("DROP TABLE preferences")
            for table in ("preferences", "inbox", "outbox"):
                db.execute(f"ALTER TABLE {table}_v2 RENAME TO {table}")

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                yield self.db
                self.db.execute("COMMIT")
            except BaseException as error:
                try:
                    if self.db.in_transaction:
                        self.db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                if isinstance(error, sqlite3.Error):
                    raise StorageError("Storage transaction failed; requests are disabled") from None
                raise

    def _read(self, sql, args=()):
        with self.lock:
            try:
                return self.db.execute(sql, args).fetchall()
            except sqlite3.Error:
                raise StorageError("Storage read failed") from None

    @staticmethod
    def _meta(db, key, value):
        db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def meta(self, key, default=None):
        rows = self._read("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(rows[0][0]) if rows else default

    def bind_app(self, app_id):
        with self.transaction() as db:
            row = db.execute("SELECT value FROM meta WHERE key='app_id'").fetchone()
            if row and json.loads(row[0]) != app_id:
                raise StorageError("Database belongs to another QQ app; use a separate database")
            if db.execute("SELECT 1 FROM inbox WHERE app<>? LIMIT 1", (app_id,)).fetchone():
                raise StorageError("Database contains commands for another QQ app")
            if not row:
                db.execute("DELETE FROM meta WHERE key='gateway'")
            self._meta(db, "app_id", app_id)

    # Historical weather APIs delegate to the module's single SQL implementation.
    def bind_timezone(self, zone):
        return self.weather.bind_timezone(zone)

    def reserve(self, limit, zone, now):
        return self.weather.reserve(limit, zone, now)

    def usage(self, zone, now):
        return self.weather.usage(zone, now)

    def replace_cities(self, cities, provenance):
        return self.weather.replace_cities(cities, provenance)

    def find_cities(self, query):
        return self.weather.find_cities(query)

    def preference(self, app, group, user, scene="group"):
        return self.weather.preference(app, group, user, scene)

    def set_preference(self, app, group, user, value, scene="group"):
        return self.weather.set_preference(app, group, user, value, scene)

    def cache_get(self, key, now):
        return self.weather.cache_get(key, now)

    def cache_put(self, key, data, expires):
        return self.weather.cache_put(key, data, expires)

    def ready(self, session, seq):
        with self.transaction() as db:
            self._meta(db, "gateway", {"session_id": session, "seq": seq})

    def clear_session(self):
        with self.transaction() as db:
            db.execute("DELETE FROM meta WHERE key='gateway'")

    def clean_received(self, message):
        """Mask before truncation/serialization; never store console escape strings."""
        maximum = self.chat.settings.max_text_chars
        content = self.observer.redact(message.content)
        name = self.observer.redact(message.sender_name)[:128] if message.sender_name else None
        return replace(message, content=content[:maximum], sender_name=name,
                       summary=self.observer.redact(message.summary)[:512],
                       truncated=message.truncated or len(content) > maximum)

    def accept(self, session, seq, message, now, detailed=False):
        """Message, durable unmatched job and receive checkpoint commit together.

        Already-matched legacy payloads are accepted for old embedders/queue tests;
        production Gateway exclusively supplies ReceivedMessage.
        """
        if isinstance(message, ReceivedMessage):
            message = self.clean_received(message) if self.chat.allows(message) else None
        with self.transaction() as db:
            row = db.execute("SELECT value FROM meta WHERE key='gateway'").fetchone()
            checkpoint = json.loads(row[0]) if row else None
            if not checkpoint or checkpoint["session_id"] != session:
                raise StorageError("Gateway session mismatch")
            prior = checkpoint["seq"]
            if prior is not None and seq <= prior:
                return "stale_sequence" if detailed else False
            inserted = False
            outcome = "ignored"
            if message is not None:
                existing = db.execute("SELECT kind,state,payload FROM inbox WHERE app=? AND scene=? AND grp=? AND msg=?", message.storage_key).fetchone()
                archive_exists = False
                if isinstance(message, ReceivedMessage):
                    archive = self.chat.existing_message(db, message)
                    archive_exists = archive is not None
                    archived_id = self.chat.record_received(db, message, now)
                    if archived_id is not None:
                        archive = self.chat.existing_message(db, message)
                        # The durable archive is authoritative for this short job.
                        message = replace(message, content=archive['content'], sender_name=archive['sender_name'],
                                          truncated=bool(archive['truncated']),
                                          summary=message.summary if archive['deleted_at'] is None else '')
                    elif not archive_exists:
                        message = None
                if message is not None:
                    upgrade = (isinstance(message, ReceivedMessage) and existing
                        and existing['kind'] == 'message' and existing['state'] in ('pending','working','unmatched')
                        and archive is not None and archive['direction'] == 'in'
                        and archive['deleted_at'] is None
                        and message.event_type == 'GROUP_AT_MESSAGE_CREATE' and existing['payload']
                        and json.loads(existing['payload'])['event_type'] != 'GROUP_AT_MESSAGE_CREATE')
                    if not existing and not archive_exists or upgrade:
                        if not upgrade or existing['state'] == 'unmatched':
                            size = db.execute("SELECT COUNT(*) FROM inbox WHERE state IN ('pending','working')").fetchone()[0]
                            if size >= 256:
                                raise StorageError("Message queue full; gateway checkpoint not advanced")
                        payload = json.dumps(message.to_dict(), ensure_ascii=False)
                        kind = 'message' if isinstance(message, ReceivedMessage) else 'command'
                        if upgrade:
                            # Keep original identity/body; only stronger documented
                            # event context supplements the still-unexecuted task.
                            projected = json.loads(existing['payload'])
                            projected.update(event_type='GROUP_AT_MESSAGE_CREATE', content=archive['content'],
                                             sender_name=archive['sender_name'], truncated=bool(archive['truncated']))
                            payload = json.dumps(projected, ensure_ascii=False)
                            state = 'working' if existing['state'] == 'working' else 'pending'
                            db.execute("UPDATE inbox SET state=?,payload=? WHERE app=? AND scene=? AND grp=? AND msg=?", (state, payload, *message.storage_key))
                        else:
                            db.execute("INSERT INTO inbox(app,scene,grp,msg,payload,created,expires,state,kind) VALUES(?,?,?,?,?,?,?,?,?)", (*message.storage_key, payload, now, (message.timestamp if message.timestamp is not None else now)+300, 'pending', kind))
                        inserted = True
                    outcome = 'accepted' if inserted else 'duplicate_message'
            # Receive order is serialized by the transport; this is not max(s).
            self._meta(db, "gateway", {"session_id": session, "seq": seq})
            return outcome if detailed else inserted

    def recover(self):
        """Retire previous-process business jobs without replaying uncertain work.

        The gateway checkpoint and archived chats survive unchanged. Already
        planned replies remain outbox-owned, including their fixed sequence and
        retry policy; an in-flight send remains uncertain, never auto-resend.
        Must be called only while holding the single-instance file lock and
        before the new gateway or workers start.
        """
        with self.transaction() as db:
            retired = db.execute(
                "UPDATE inbox SET state='expired',payload=NULL "
                "WHERE state IN ('pending','working','unmatched')"
            ).rowcount
            replies = db.execute("UPDATE outbox SET state='uncertain' WHERE state='sending'").rowcount
        return {"recovered_commands": 0, "retired_commands": retired, "uncertain_replies": replies}

    def claim_command(self, now):
        message = None
        with self.transaction() as db:
            expired = db.execute("UPDATE inbox SET state='expired',payload=NULL WHERE state='pending' AND kind='command' AND expires<=?", (now,)).rowcount
            active = db.execute("SELECT app,scene,grp,payload FROM inbox WHERE state='working'").fetchall()
            busy = {(r['app'], r['scene'], r['grp'], json.loads(r['payload'])['user_id']) for r in active if r['payload']}
            pending = db.execute("SELECT * FROM inbox WHERE state='pending' AND (kind='message' OR expires>?) ORDER BY created,rowid", (now,)).fetchall()
            for row in pending:
                payload = json.loads(row['payload'])
                if (row['app'], row['scene'], row['grp'], payload['user_id']) in busy:
                    continue
                model = ReceivedMessage if row['kind'] == 'message' else GroupMessage
                candidate = model.from_dict(payload)
                if row['kind'] == 'message' or payload.get('from_received'):
                    archive = self.chat.existing_message(db, candidate)
                    if (not self.chat.allows(candidate) or archive is None or archive['direction'] != 'in'
                            or archive['deleted_at'] is not None):
                        # A restart after forget/retention must not replay a queued
                        # copy of retired chat text, logs or business commands.
                        db.execute("UPDATE inbox SET state='expired',payload=NULL WHERE app=? AND scene=? AND grp=? AND msg=?", candidate.storage_key)
                        expired += 1
                        continue
                db.execute("UPDATE inbox SET state='working' WHERE app=? AND scene=? AND grp=? AND msg=?", candidate.storage_key)
                message = candidate
                break
        if expired:
            self.observer.event("inbox.expired", count=expired)
        return message

    def plan_reply(self, message, content):
        from .replies import validate_payload
        payload = validate_payload(content) if isinstance(content, dict) else None
        text = payload.get("content", "") if payload else content
        with self.transaction() as db:
            row = db.execute('SELECT kind,state,payload FROM inbox WHERE app=? AND scene=? AND grp=? AND msg=?', message.storage_key).fetchone()
            if row and (row['kind'] == 'message' or row['payload'] and json.loads(row['payload']).get('from_received')) and row['state'] != 'working':
                raise StorageError('Processing task was retired before reply planning')
            if row and row['payload'] and json.loads(row['payload']).get('from_received'):
                archive = self.chat.existing_message(db, message)
                if archive is None or archive['deleted_at'] is not None:
                    raise StorageError('Source chat was retired before reply planning')
            elif row and row['state'] == 'expired' and row['payload'] is None:
                archive = self.chat.existing_message(db, message)
                if archive is not None and archive['deleted_at'] is not None:
                    raise StorageError('Source chat was retired before reply planning')
            db.execute("INSERT OR IGNORE INTO outbox(app,scene,grp,msg,content,payload,seq,expires,state) VALUES(?,?,?,?,?,?,?,?,?)", (*message.storage_key, text, json.dumps(payload, ensure_ascii=False) if payload else None, 1, message.timestamp+300, "pending"))
            db.execute("UPDATE inbox SET state='done',payload=NULL WHERE app=? AND scene=? AND grp=? AND msg=?", message.storage_key)

    def promote_command(self, message):
        """Persist the matched plan before handler execution, preserving old routing."""
        payload = message.to_dict()
        payload['from_received'] = True
        with self.transaction() as db:
            changed = db.execute("UPDATE inbox SET kind='command',payload=? WHERE state='working' AND app=? AND scene=? AND grp=? AND msg=?", (json.dumps(payload, ensure_ascii=False), *message.storage_key)).rowcount
            if changed != 1:
                raise StorageError('Matched command lost its durable processing task')

    def finish_command(self, message, state='done'):
        with self.transaction() as db:
            if state == 'unmatched':
                row = db.execute('SELECT kind,payload FROM inbox WHERE app=? AND scene=? AND grp=? AND msg=?', message.storage_key).fetchone()
                if row and row['kind'] == 'message' and row['payload']:
                    projected = json.loads(row['payload'])
                    # The AT alias may have committed while this generic snapshot
                    # was working. Requeue ONLY if nothing has been executed.
                    if (projected['event_type'] == 'GROUP_AT_MESSAGE_CREATE'
                            and message.event_type != 'GROUP_AT_MESSAGE_CREATE'):
                        state = 'pending'
                    else:
                        projected.update(content='', sender_name=None, summary='')
                    db.execute('UPDATE inbox SET state=?,payload=? WHERE app=? AND scene=? AND grp=? AND msg=?',
                               (state, json.dumps(projected, ensure_ascii=False), *message.storage_key))
                else:
                    db.execute("UPDATE inbox SET state=? WHERE app=? AND scene=? AND grp=? AND msg=?", (state, *message.storage_key))
            else:
                db.execute("UPDATE inbox SET state=?,payload=NULL WHERE app=? AND scene=? AND grp=? AND msg=?", (state, *message.storage_key))

    def scrub_received_copies(self, db, now, scope=None):
        """Remove retired chat bodies from new jobs, preserving transport keys/state.

        Called inside the repository's same retirement/forget transaction. Legacy
        matched commands and already-planned outbox payloads are not chat copies.
        """
        sql = 'SELECT * FROM inbox WHERE payload IS NOT NULL'
        args = ()
        if scope is not None:
            sql += ' AND app=? AND scene=? AND grp=?'
            args = scope
        changed = 0
        for row in db.execute(sql, args).fetchall():
            projected = json.loads(row['payload'])
            if row['kind'] == 'message':
                item = ReceivedMessage.from_dict(projected)
            elif row['kind'] == 'command' and projected.get('from_received'):
                item = GroupMessage.from_dict(projected)
            else:
                continue  # Historical commands do not belong to chat retention.
            archive = self.chat.existing_message(db, item)
            if archive is None or archive['deleted_at'] is not None:
                db.execute('UPDATE inbox SET state=?,payload=NULL WHERE app=? AND scene=? AND grp=? AND msg=?',
                           ('expired' if row['state'] in ('pending', 'working', 'unmatched') else row['state'],
                            *item.storage_key))
                changed += 1
        return changed

    def confirm_reply(self, reply, response, now):
        """Called only after QQ acknowledged a send; history + sent are atomic."""
        with self.transaction() as db:
            row = db.execute("SELECT state,seq,confirmed_qq_message_id FROM outbox WHERE app=? AND scene=? AND grp=? AND msg=?", (reply['app'], reply['scene'], reply['grp'], reply['msg'])).fetchone()
            if row is None or row['state'] not in ('sending', 'sent') or row['seq'] != reply['seq']:
                raise StorageError("Send confirmation has no matching fixed-sequence transport task")
            qq_id = response.get('id') if isinstance(response, dict) else None
            if not isinstance(qq_id, str) or not 0 < len(qq_id) <= 256:
                raise StorageError('Send confirmation requires a valid QQ message id')
            if row['state'] == 'sent' and row['confirmed_qq_message_id'] != qq_id:
                raise StorageError('Sent task cannot accept another or previously unbound confirmation')
            conflict = db.execute('SELECT 1 FROM outbox WHERE app=? AND scene=? AND grp=? AND confirmed_qq_message_id=? AND msg<>?',
                                  (reply['app'], reply['scene'], reply['grp'], qq_id, reply['msg'])).fetchone()
            if conflict:
                raise StorageError('QQ confirmation is already bound to another transport task')
            projected = self.chat.record_sent(db, reply, response, now)
            # A manual forget can happen while QQ has an in-flight POST.
            # Never claim a newly sent transport task without history.
            if row['state'] != 'sent' and projected is None:
                raise StorageError('QQ confirmation cannot archive a retired or revoked chat')
            db.execute("UPDATE outbox SET state='sent',confirmed_qq_message_id=? WHERE app=? AND scene=? AND grp=? AND msg=?", (qq_id, reply['app'], reply['scene'], reply['grp'], reply['msg']))
        return projected

    def claim_reply(self, now):
        result = None
        with self.transaction() as db:
            expired = db.execute("UPDATE outbox SET state='expired',content='',payload=NULL WHERE state='pending' AND expires<=?", (now,)).rowcount
            failed = db.execute("UPDATE outbox SET state='failed' WHERE state='pending' AND attempts>=3").rowcount
            rows = db.execute("SELECT * FROM outbox WHERE state='pending' AND expires>? AND attempts<3 ORDER BY rowid", (now,)).fetchall()
            row = next((r for r in rows if self.chat.can_send(db, r, now)), None)
            if row:
                db.execute("UPDATE outbox SET state='sending',attempts=attempts+1 WHERE app=? AND scene=? AND grp=? AND msg=?", (row["app"], row["scene"], row["grp"], row["msg"]))
                result = dict(row)
                result['attempts'] += 1
        if expired or failed:
            self.observer.event("outbox.retired", expired=expired, failed=failed)
        return result

    def reply_state(self, key, state):
        # Legacy direct callers supplied group-only three-part keys.
        if len(key) == 3:
            key = (key[0], "group", key[1], key[2])
        with self.transaction() as db:
            db.execute("UPDATE outbox SET state=? WHERE app=? AND scene=? AND grp=? AND msg=?", (state, *key))
            if state == "expired":
                db.execute("UPDATE outbox SET content='',payload=NULL WHERE app=? AND scene=? AND grp=? AND msg=?", key)

    def cleanup(self, now):
        with self.transaction() as db:
            caches = db.execute("DELETE FROM cache WHERE expires<=?", (now,)).rowcount
            inboxes = db.execute("DELETE FROM inbox WHERE created<?", (now-86400,)).rowcount
            replies = db.execute("UPDATE outbox SET content='',payload=NULL WHERE expires<=? AND (content<>'' OR payload IS NOT NULL)", (now,)).rowcount
            chats = self.chat.cleanup(db, now)
        if caches or inboxes or replies or any(chats.values()):
            self.observer.event("storage.cleanup", caches=caches, inboxes=inboxes, cleared_replies=replies, **chats)

    def close(self):
        with self.lock:
            self.db.close()
