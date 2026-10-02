import fcntl
import json
import os
import sqlite3
import struct
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def add_seconds(timestamp: str, seconds: float) -> str:
    return (datetime.fromisoformat(timestamp) + timedelta(seconds=seconds)).isoformat()


class InstanceLock:
    def __init__(self, data_dir: Path):
        self.file = (data_dir / ".service.lock").open("a")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.file.close()
            raise RuntimeError("此数据目录已有服务运行，请使用现有服务。") from error

    def close(self) -> None:
        self.file.close()


class Storage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.data_dir / "sense.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS guard (
                id INTEGER PRIMARY KEY CHECK (id = 1), room_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 0, last_error TEXT
            );
            INSERT OR IGNORE INTO guard(id) VALUES (1);
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, room_id TEXT NOT NULL, source_live_id TEXT NOT NULL,
                anchor_name TEXT NOT NULL, title TEXT NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT, end_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS segments (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                sequence INTEGER NOT NULL, epoch_id TEXT NOT NULL,
                start_at TEXT NOT NULL, end_at TEXT,
                sample_rate INTEGER NOT NULL, sample_count INTEGER NOT NULL DEFAULT 0,
                relative_path TEXT NOT NULL UNIQUE, state TEXT NOT NULL DEFAULT 'pending',
                UNIQUE(session_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS segments_session ON segments(session_id, state);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
                kind TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, detail TEXT
            );
        """)
        self._migrate_analysis()

    def _migrate_analysis(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            raise RuntimeError("数据库版本比当前程序新，请使用对应版本程序。")
        if version == 2:
            return
        # SQLite's backup API includes committed WAL pages, unlike a file copy.
        if self.db.execute("SELECT 1 FROM sessions LIMIT 1").fetchone():
            backup = self.data_dir / "backups" / f"before-analysis-v{version}-{uuid4().hex}.sqlite3"
            backup.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(backup) as target:
                self.db.backup(target)
        statements = [
            "ALTER TABLE guard ADD COLUMN realtime_analysis INTEGER NOT NULL DEFAULT 0",
            """CREATE TABLE epochs (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                closed_at TEXT)""",
            """INSERT INTO epochs(id,session_id,closed_at)
                SELECT epoch_id,session_id,? FROM segments GROUP BY epoch_id""",
            """CREATE TABLE analysis_runs (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                config_key TEXT NOT NULL, config_json TEXT NOT NULL, created_at TEXT NOT NULL,
                target_sequence INTEGER NOT NULL DEFAULT 0,
                processed_sequence INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'pending', last_error TEXT,
                pending_json TEXT, export_error TEXT,
                UNIQUE(session_id,config_key))""",
            """CREATE TABLE analysis_jobs (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES analysis_runs(id),
                job_key TEXT NOT NULL, sequence INTEGER NOT NULL, kind TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL, assignments_json TEXT NOT NULL DEFAULT '[]',
                last_error TEXT, elapsed_seconds REAL,
                UNIQUE(run_id,job_key))""",
            """CREATE TABLE speaker_profiles (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES analysis_runs(id),
                number INTEGER NOT NULL, namespace TEXT NOT NULL, embedding_json TEXT NOT NULL,
                clean_samples INTEGER NOT NULL, UNIQUE(run_id,number))""",
            """CREATE TABLE utterances (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL REFERENCES analysis_runs(id),
                job_id TEXT NOT NULL REFERENCES analysis_jobs(id),
                start_at TEXT NOT NULL, end_at TEXT NOT NULL,
                speaker_ids_json TEXT NOT NULL, text TEXT NOT NULL,
                flags_json TEXT NOT NULL, sources_json TEXT NOT NULL)""",
            "CREATE INDEX utterances_run ON utterances(run_id,ordinal)",
            "PRAGMA user_version=1",
        ]
        with self.db:
            if version == 0:
                for statement in statements:
                    self.db.execute(
                        statement, (utc_now(),) if statement.startswith("INSERT") else ()
                    )
            self.db.execute(
                "ALTER TABLE analysis_runs ADD COLUMN scope TEXT NOT NULL DEFAULT 'session' "
                "CHECK(scope IN ('session','segment'))"
            )
            self.db.execute(
                "ALTER TABLE analysis_runs ADD COLUMN segment_id TEXT REFERENCES segments(id)"
            )
            self.db.execute(
                "ALTER TABLE analysis_runs ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )
            self.db.execute(
                "CREATE INDEX analysis_scope ON analysis_runs(scope,segment_id,revision)"
            )
            self.db.execute(
                "CREATE TABLE utterance_sources (utterance_id TEXT NOT NULL REFERENCES "
                "utterances(id), segment_id TEXT NOT NULL REFERENCES segments(id), "
                "PRIMARY KEY(utterance_id,segment_id))"
            )
            self.db.execute(
                "CREATE INDEX utterance_segment ON utterance_sources(segment_id,utterance_id)"
            )
            for row in self.db.execute("SELECT id,sources_json FROM utterances").fetchall():
                self.db.executemany(
                    "INSERT OR IGNORE INTO utterance_sources VALUES (?,?)",
                    [
                        (row["id"], source["segment_id"])
                        for source in json.loads(row["sources_json"])
                    ],
                )
            self.db.execute("PRAGMA user_version=2")

    def set_realtime(self, enabled: bool) -> None:
        with self.db:
            self.db.execute("UPDATE guard SET realtime_analysis=? WHERE id=1", (int(enabled),))

    def open_epoch(self, session_id: str, epoch_id: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO epochs VALUES (?,?,NULL)", (epoch_id, session_id)
            )

    def close_epoch(self, epoch_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE epochs SET closed_at=? WHERE id=?", (utc_now(), epoch_id))

    def epoch_closed(self, epoch_id: str) -> bool:
        row = self.db.execute("SELECT closed_at FROM epochs WHERE id=?", (epoch_id,)).fetchone()
        return not row or row[0] is not None

    def close_stale_epochs(self) -> None:
        with self.db:
            self.db.execute("UPDATE epochs SET closed_at=? WHERE closed_at IS NULL", (utc_now(),))

    def guard(self) -> dict:
        return dict(self.db.execute("SELECT * FROM guard WHERE id=1").fetchone())

    def set_guard(self, room_id: str | None, enabled: bool, error: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE guard SET room_id=?, enabled=?, last_error=? WHERE id=1",
                (room_id, int(enabled), error),
            )

    def create_session(self, room_id: str, source_live_id: str, anchor: str, title: str) -> dict:
        session_id = uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
                (session_id, room_id, source_live_id, anchor, title, utc_now()),
            )
        return self.session(session_id)

    def session(self, session_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def open_session(self, room_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM sessions WHERE room_id=? AND ended_at IS NULL "
            "ORDER BY started_at DESC LIMIT 1",
            (room_id,),
        ).fetchone()
        return dict(row) if row else None

    def finish_session(self, session_id: str, reason: str) -> None:
        now = utc_now()
        with self.db:
            self.db.execute(
                "UPDATE sessions SET ended_at=?, end_reason=? WHERE id=? AND ended_at IS NULL",
                (now, reason, session_id),
            )
            self.db.execute(
                "UPDATE events SET ended_at=? WHERE session_id=? "
                "AND kind='gap' AND ended_at IS NULL",
                (now, session_id),
            )

    def sessions(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.db.execute(
            "SELECT s.*, COUNT(g.id) AS segment_count, "
            "COALESCE(SUM(1.0*g.sample_count/g.sample_rate),0) AS duration_seconds "
            "FROM sessions s LEFT JOIN segments g ON g.session_id=s.id AND g.state='ready' "
            "GROUP BY s.id ORDER BY s.started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    def reserve_segment(
        self, session_id: str, epoch_id: str, start_at: str, sample_rate: int
    ) -> dict:
        self.open_epoch(session_id, epoch_id)
        session = self.session(session_id)
        sequence = self.db.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM segments WHERE session_id=?", (session_id,)
        ).fetchone()[0]
        segment_id = uuid4().hex
        relative_path = f"audio/{session['room_id']}/{session_id}/{sequence:06d}_{segment_id}.wav"
        with self.db:
            self.db.execute(
                "INSERT INTO segments(id,session_id,sequence,epoch_id,start_at,sample_rate,"
                "relative_path) VALUES (?,?,?,?,?,?,?)",
                (segment_id, session_id, sequence, epoch_id, start_at, sample_rate, relative_path),
            )
        return self.segment(segment_id)

    def segment(self, segment_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
        return dict(row) if row else None

    def segment_path(self, segment: dict) -> Path:
        path = (self.data_dir / segment["relative_path"]).resolve()
        if not path.is_relative_to(self.data_dir):
            raise ValueError("音频路径超出数据目录。")
        return path

    def complete_segment(self, segment_id: str, samples: int) -> None:
        segment = self.segment(segment_id)
        end_at = add_seconds(segment["start_at"], samples / segment["sample_rate"])
        with self.db:
            self.db.execute(
                "UPDATE segments SET state='ready',sample_count=?,end_at=? WHERE id=?",
                (samples, end_at, segment_id),
            )

    def segments(self, session_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM segments WHERE session_id=? AND state='ready' "
            "ORDER BY sequence LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    def totals(self, session_id: str | None) -> tuple[int, float]:
        row = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(1.0*sample_count/sample_rate),0) "
            "FROM segments WHERE session_id=? AND state='ready'",
            (session_id,),
        ).fetchone()
        return row[0], row[1]

    def last_segment_end(self, session_id: str) -> str | None:
        row = self.db.execute(
            "SELECT end_at FROM segments WHERE session_id=? AND state='ready' "
            "ORDER BY sequence DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return row[0] if row else None

    def open_gap(self, session_id: str, detail: str, started_at: str | None = None) -> None:
        if self.db.execute(
            "SELECT 1 FROM events WHERE session_id=? AND kind='gap' AND ended_at IS NULL",
            (session_id,),
        ).fetchone():
            return
        self.event(session_id, "gap", detail, started_at=started_at)

    def close_gap(self, session_id: str, ended_at: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE events SET ended_at=? WHERE session_id=? "
                "AND kind='gap' AND ended_at IS NULL",
                (ended_at, session_id),
            )

    def event(
        self, session_id: str | None, kind: str, detail: str, started_at: str | None = None
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO events(session_id,kind,started_at,detail) VALUES (?,?,?,?)",
                (session_id, kind, started_at or utc_now(), detail),
            )

    def events(self, session_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM events WHERE session_id=? ORDER BY id", (session_id,)
            ).fetchall()
        ]

    def recover(self) -> list[str]:
        failures = []
        rows = self.db.execute("SELECT * FROM segments WHERE state='pending'").fetchall()
        for row in rows:
            segment = dict(row)
            final = self.segment_path(segment)
            partial = final.with_suffix(".wav.part")
            candidate = final if final.exists() else partial
            try:
                if not candidate.exists():
                    raise ValueError("临时音频文件不存在")
                samples = repair_wav(candidate, segment["sample_rate"])
                if not samples:
                    raise ValueError("临时音频不含完整采样")
                if candidate == partial:
                    os.replace(partial, final)
                self.complete_segment(segment["id"], samples)
                self.event(
                    segment["session_id"], "recovered", f"恢复音频分段 {segment['sequence']}"
                )
            except (OSError, ValueError, wave.Error, EOFError) as error:
                detail = f"分段 {segment['sequence']} 无法恢复：{error}"
                failures.append(detail)
                with self.db:
                    self.db.execute(
                        "UPDATE segments SET state='failed' WHERE id=?", (segment["id"],)
                    )
                self.event(segment["session_id"], "recovery_failed", detail)
        return failures

    def close(self) -> None:
        self.db.close()


def repair_wav(path: Path, sample_rate: int) -> int:
    # Only repair files produced by our PCM writer. The data chunk's size can be
    # stale after a crash; actual complete samples on disk are the source of truth.
    with path.open("r+b") as file:
        header = file.read(44)
        if (
            len(header) != 44
            or header[:4] != b"RIFF"
            or header[8:12] != b"WAVE"
            or header[12:16] != b"fmt "
            or header[36:40] != b"data"
            or struct.unpack_from("<IHHI", header, 16) != (16, 1, 1, sample_rate)
            or struct.unpack_from("<HH", header, 32) != (2, 16)
        ):
            raise ValueError("不是预期的 16 位单声道 PCM WAV")
        size = file.seek(0, os.SEEK_END)
        data_bytes = (size - 44) // 2 * 2
        file.truncate(44 + data_bytes)
        file.seek(4)
        file.write(struct.pack("<I", 36 + data_bytes))
        file.seek(40)
        file.write(struct.pack("<I", data_bytes))
        file.flush()
        os.fsync(file.fileno())
    return data_bytes // 2
