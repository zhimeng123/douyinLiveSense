import json
from uuid import uuid4

from .models import config_key
from .storage import Storage, utc_now


class AnalysisStorage:
    def __init__(self, store: Storage):
        self.store = store
        self.db = store.db

    def run(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def latest(self, session_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM analysis_runs WHERE session_id=? AND scope='session' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def ready_max(self, session_id: str) -> int:
        return self.db.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM segments WHERE session_id=? AND state='ready'",
            (session_id,),
        ).fetchone()[0]

    def request(self, session_id: str, config: dict, retry: bool = False) -> dict:
        if not self.store.session(session_id):
            raise KeyError("场次不存在。")
        key = config_key(config)
        row = self.db.execute(
            "SELECT * FROM analysis_runs WHERE session_id=? AND config_key=?", (session_id, key)
        ).fetchone()
        # A request made before model preparation keeps its run ID when models arrive.
        latest = self.latest(session_id)
        if row is None and latest and not json.loads(latest["config_json"])["models"]:
            row = latest
        with self.db:
            if row is None:
                run_id = uuid4().hex
                self.db.execute(
                    "INSERT INTO analysis_runs(id,session_id,config_key,config_json,created_at) "
                    "VALUES (?,?,?,?,?)",
                    (run_id, session_id, key, json.dumps(config), utc_now()),
                )
            else:
                run_id = row["id"]
                if not row["processed_sequence"]:
                    self.db.execute(
                        "UPDATE analysis_runs SET config_key=?,config_json=? WHERE id=?",
                        (key, json.dumps(config), run_id),
                    )
            self.db.execute(
                "UPDATE analysis_runs SET target_sequence=MAX(target_sequence,?) WHERE id=?",
                (self.ready_max(session_id), run_id),
            )
            self.db.execute(
                "UPDATE analysis_runs SET state='pending' WHERE id=? "
                "AND target_sequence>processed_sequence AND state IN ('complete','waiting_audio')",
                (run_id,),
            )
            if retry:
                self.db.execute(
                    "UPDATE analysis_jobs SET state='pending',attempts=0,last_error=NULL "
                    "WHERE run_id=? AND state='failed'",
                    (run_id,),
                )
                self.db.execute(
                    "UPDATE analysis_runs SET state='pending',last_error=NULL WHERE id=?",
                    (run_id,),
                )
        return self.run(run_id)

    def latest_segment(self, segment_id: str, completed: bool = False) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM analysis_runs WHERE scope='segment' AND segment_id=? "
            + ("AND state='complete' " if completed else "")
            + "ORDER BY revision DESC LIMIT 1",
            (segment_id,),
        ).fetchone()
        return dict(row) if row else None

    def request_segment(
        self, segment_id: str, config: dict, reanalyse: bool = False, retry: bool = False
    ) -> dict:
        segment = self.store.segment(segment_id)
        if not segment:
            raise KeyError("切片不存在。")
        if segment["state"] != "ready":
            raise ValueError("切片尚未保存完成，请等待 WAV 文件关闭。")
        latest = self.latest_segment(segment_id)
        if latest and (not reanalyse or latest["state"] not in {"complete", "needs_attention"}):
            if retry and latest["state"] == "needs_attention":
                with self.db:
                    self.db.execute(
                        "UPDATE analysis_jobs SET state='pending',attempts=0,last_error=NULL "
                        "WHERE run_id=? AND state='failed'",
                        (latest["id"],),
                    )
                    self.db.execute(
                        "UPDATE analysis_runs SET state='pending',last_error=NULL WHERE id=?",
                        (latest["id"],),
                    )
            return self.run(latest["id"])
        revision = latest["revision"] + 1 if latest else 1
        run_id = uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO analysis_runs(id,session_id,config_key,config_json,created_at,"
                "target_sequence,processed_sequence,scope,segment_id,revision) "
                "VALUES (?,?,?,?,?,?,?,'segment',?,?)",
                (
                    run_id,
                    segment["session_id"],
                    f"segment:{segment_id}:{revision}:{config_key(config)}",
                    json.dumps(config),
                    utc_now(),
                    segment["sequence"],
                    segment["sequence"] - 1,
                    segment_id,
                    revision,
                ),
            )
        return self.run(run_id)

    def bind_models(self, run: dict, config: dict) -> dict:
        # Resolving a setup request must not expand its manual audio snapshot.
        with self.db:
            self.db.execute(
                "UPDATE analysis_runs SET config_key=?,config_json=? WHERE id=?",
                (
                    f"segment:{run['segment_id']}:{run['revision']}:{config_key(config)}"
                    if run["scope"] == "segment"
                    else config_key(config),
                    json.dumps(config),
                    run["id"],
                ),
            )
        return self.run(run["id"])

    def recover(self) -> None:
        with self.db:
            self.db.execute("UPDATE analysis_jobs SET state='pending' WHERE state='running'")
            self.db.execute("UPDATE analysis_runs SET state='pending' WHERE state='processing'")

    def runs(self, priority_session: str | None) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM analysis_runs ORDER BY (scope='segment') DESC, created_at, rowid",
            ).fetchall()
        ]

    def set_state(self, run_id: str, state: str, error: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE analysis_runs SET state=?,last_error=? "
                "WHERE id=? AND (state!=? OR last_error IS NOT ?)",
                (state, error, run_id, state, error),
            )

    def profiles(self, run_id: str) -> list[dict]:
        profiles = [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM speaker_profiles WHERE run_id=? ORDER BY number", (run_id,)
            ).fetchall()
        ]
        for profile in profiles:
            profile["embedding"] = json.loads(profile.pop("embedding_json"))
        return profiles

    def next_job(self, run: dict, live: bool) -> dict | None:
        job = self.db.execute(
            "SELECT * FROM analysis_jobs WHERE run_id=? AND state='pending' "
            "ORDER BY sequence, rowid LIMIT 1",
            (run["id"],),
        ).fetchone()
        if job:
            return dict(job)
        core = self.db.execute(
            "SELECT * FROM segments WHERE session_id=? AND state='ready' "
            "AND sequence>? AND sequence<=? ORDER BY sequence LIMIT 1",
            (run["session_id"], run["processed_sequence"], run["target_sequence"]),
        ).fetchone()
        pending = json.loads(run["pending_json"]) if run["pending_json"] else None
        if core:
            core = dict(core)
            next_segment = self.db.execute(
                "SELECT * FROM segments WHERE session_id=? AND state='ready' AND sequence=? "
                "AND epoch_id=? AND sequence<=?",
                (run["session_id"], core["sequence"] + 1, core["epoch_id"], run["target_sequence"]),
            ).fetchone()
            closed = self.store.epoch_closed(core["epoch_id"])
            if live and not closed and next_segment is None:
                self.set_state(run["id"], "waiting_context")
                return None
            context = [
                dict(row)
                for row in self.db.execute(
                    "SELECT * FROM segments WHERE session_id=? AND state='ready' "
                    "AND epoch_id=? AND sequence BETWEEN ? AND ? AND sequence<=? ORDER BY sequence",
                    (
                        run["session_id"],
                        core["epoch_id"],
                        core["sequence"] - 1,
                        core["sequence"] + 1,
                        self.ready_max(run["session_id"])
                        if run["scope"] == "segment"
                        else run["target_sequence"],
                    ),
                ).fetchall()
            ]
            payload = {
                "core": core,
                "segments": context,
                "flush": run["scope"] == "segment"
                or (next_segment is None and (closed or not live)),
            }
            sequence, kind, job_key = core["sequence"], "window", core["id"]
        elif pending and (not live or self.store.epoch_closed(pending["epoch_id"])):
            payload = {"core": None, "segments": [], "flush": True}
            sequence, kind = run["processed_sequence"], "flush"
            last = pending["sources"][-1]
            job_key = f"flush:{last['segment_id']}:{last['end_sample']}"
        else:
            self.set_state(run["id"], "waiting_audio" if live else "complete")
            return None
        previous = self.db.execute(
            "SELECT assignments_json FROM analysis_jobs WHERE run_id=? "
            "AND state='complete' AND kind='window' ORDER BY sequence DESC LIMIT 1",
            (run["id"],),
        ).fetchone()
        payload.update(
            {
                "config": json.loads(run["config_json"]),
                "pending": pending,
                "profiles": self.profiles(run["id"]),
                "previous_assignments": json.loads(previous[0]) if previous else [],
            }
        )
        job_id = uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO analysis_jobs(id,run_id,job_key,sequence,kind,payload_json) "
                "VALUES (?,?,?,?,?,?)",
                (job_id, run["id"], job_key, sequence, kind, json.dumps(payload)),
            )
        return dict(self.db.execute("SELECT * FROM analysis_jobs WHERE id=?", (job_id,)).fetchone())

    def begin(self, job: dict) -> None:
        with self.db:
            self.db.execute(
                "UPDATE analysis_jobs SET state='running',attempts=attempts+1 WHERE id=?",
                (job["id"],),
            )
            self.db.execute(
                "UPDATE analysis_runs SET state='processing',last_error=NULL WHERE id=?",
                (job["run_id"],),
            )

    def fail(self, job: dict, error: str) -> None:
        attempts = self.db.execute(
            "SELECT attempts FROM analysis_jobs WHERE id=?", (job["id"],)
        ).fetchone()[0]
        with self.db:
            self.db.execute(
                "UPDATE analysis_jobs SET state=?,last_error=? WHERE id=?",
                ("pending" if attempts < 2 else "failed", error, job["id"]),
            )
            self.db.execute(
                "UPDATE analysis_runs SET state=?,last_error=? WHERE id=?",
                ("pending" if attempts < 2 else "needs_attention", error, job["run_id"]),
            )

    def commit(self, job: dict, result: dict) -> None:
        # One transaction is the checkpoint. A crash before commit replays the whole job.
        with self.db:
            state = self.db.execute(
                "SELECT state FROM analysis_jobs WHERE id=?", (job["id"],)
            ).fetchone()[0]
            if state != "running":
                raise RuntimeError("分析任务不处于运行状态，拒绝重复提交。")
            for profile in result["profiles"]:
                self.db.execute(
                    "INSERT INTO speaker_profiles VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET embedding_json=excluded.embedding_json, "
                    "clean_samples=excluded.clean_samples",
                    (
                        profile["id"],
                        job["run_id"],
                        profile["number"],
                        profile["namespace"],
                        json.dumps(profile["embedding"]),
                        profile["clean_samples"],
                    ),
                )
            for utterance in result["utterances"]:
                self.db.execute(
                    "INSERT INTO utterances(id,run_id,job_id,start_at,end_at,speaker_ids_json,"
                    "text,flags_json,sources_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        utterance["id"],
                        job["run_id"],
                        job["id"],
                        utterance["start_at"],
                        utterance["end_at"],
                        json.dumps(utterance["speaker_ids"]),
                        utterance["text"],
                        json.dumps(utterance["flags"]),
                        json.dumps(utterance["sources"]),
                    ),
                )
                self.db.executemany(
                    "INSERT OR IGNORE INTO utterance_sources VALUES (?,?)",
                    [(utterance["id"], source["segment_id"]) for source in utterance["sources"]],
                )
            self.db.execute(
                "UPDATE analysis_jobs SET state='complete',assignments_json=?,elapsed_seconds=?,"
                "last_error=NULL WHERE id=?",
                (json.dumps(result["assignments"]), result.get("elapsed_seconds"), job["id"]),
            )
            self.db.execute(
                "UPDATE analysis_runs SET processed_sequence=MAX(processed_sequence,?),"
                "pending_json=?,state='pending',last_error=NULL,"
                "export_error='文档待更新' WHERE id=?",
                (
                    job["sequence"],
                    json.dumps(result["pending"]) if result["pending"] else None,
                    job["run_id"],
                ),
            )

    def utterances(self, run_id: str, after: int = 0, limit: int = 100) -> list[dict]:
        return [
            decode_utterance(dict(row))
            for row in self.db.execute(
                "SELECT * FROM utterances WHERE run_id=? AND ordinal>? ORDER BY ordinal LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
        ]

    def utterance(self, utterance_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM utterances WHERE id=?", (utterance_id,)).fetchone()
        return decode_utterance(dict(row)) if row else None

    def segment_utterances(
        self, segment_id: str, scope: str, after: int = 0, limit: int = 100
    ) -> tuple[dict | None, list[dict]]:
        segment = self.store.segment(segment_id)
        run = (
            self.latest(segment["session_id"])
            if scope == "session"
            else self.latest_segment(segment_id, completed=True)
        )
        if not run:
            return None, []
        rows = self.db.execute(
            "SELECT u.* FROM utterances u JOIN utterance_sources s ON s.utterance_id=u.id "
            "WHERE s.segment_id=? AND u.run_id=? AND u.ordinal>? ORDER BY u.ordinal LIMIT ?",
            (segment_id, run["id"], after, limit),
        ).fetchall()
        return run, [decode_utterance(dict(row)) for row in rows]

    def segment_status(self, segment_id: str) -> dict:
        latest = self.latest_segment(segment_id)
        available = self.latest_segment(segment_id, completed=True)
        return {
            "state": latest["state"] if latest else "not_requested",
            "requested_run_id": latest["id"] if latest else None,
            "available_run_id": available["id"] if available else None,
            "revision": latest["revision"] if latest else None,
            "available_revision": available["revision"] if available else None,
            "last_error": latest["last_error"] if latest else None,
            "export_error": available["export_error"] if available else None,
        }

    def segment_summaries(self, segments: list[dict]) -> list[dict]:
        if not segments:
            return []
        whole = self.latest(segments[0]["session_id"])
        ids = [segment["id"] for segment in segments]
        placeholders = ",".join("?" for _ in ids)
        reviews = self.db.execute(
            f"SELECT * FROM analysis_runs WHERE scope='segment' AND segment_id IN ({placeholders}) "
            "ORDER BY revision DESC",
            ids,
        ).fetchall()
        latest, available = {}, {}
        for row in reviews:
            latest.setdefault(row["segment_id"], row)
            if row["state"] == "complete":
                available.setdefault(row["segment_id"], row)
        jobs = {
            row["sequence"]: row["state"]
            for row in self.db.execute(
                "SELECT sequence,state FROM analysis_jobs WHERE run_id=? AND kind='window'",
                (whole["id"] if whole else None,),
            ).fetchall()
        }
        for segment in segments:
            review, done = latest.get(segment["id"]), available.get(segment["id"])
            sequence = segment["sequence"]
            segment["analysis"] = {
                "session_state": jobs.get(
                    sequence,
                    "pending"
                    if whole and sequence <= whole["target_sequence"]
                    else "not_requested",
                ),
                "session_run_id": whole["id"] if whole else None,
                "review_state": review["state"] if review else "not_requested",
                "review_run_id": review["id"] if review else None,
                "available_run_id": done["id"] if done else None,
                "available_revision": done["revision"] if done else None,
            }
        return segments

    def queue(self, active: dict | None, limit: int = 50, offset: int = 0) -> dict:
        # Include requested cores before next_job materializes their window records.
        cte = """WITH tasks AS (
            SELECT r.id run_id,r.scope,r.revision,r.state,r.last_error,r.created_at,
                s.id segment_id,s.session_id,s.sequence,s.start_at,s.end_at,'window' kind
            FROM analysis_runs r JOIN segments s ON s.session_id=r.session_id
            WHERE s.state='ready' AND s.sequence>r.processed_sequence
                AND s.sequence<=r.target_sequence
                AND r.state NOT IN ('complete','waiting_audio','needs_attention')
            UNION ALL
            SELECT r.id,r.scope,r.revision,r.state,r.last_error,r.created_at,
                s.id,s.session_id,s.sequence,s.start_at,s.end_at,'flush'
            FROM analysis_runs r JOIN segments s ON s.session_id=r.session_id
                AND s.sequence=r.processed_sequence
            WHERE r.pending_json IS NOT NULL AND r.processed_sequence=r.target_sequence
                AND r.state NOT IN ('complete','waiting_audio','needs_attention')
        ), waiting AS (
            SELECT * FROM tasks WHERE NOT (run_id=? AND sequence=? AND kind=?)
        ) """
        exclude = (active["run_id"], active["sequence"], active["kind"]) if active else ("", -1, "")
        count = self.db.execute(cte + "SELECT COUNT(*) FROM waiting", exclude).fetchone()[0]
        rows = [
            dict(row)
            for row in self.db.execute(
                cte + "SELECT * FROM waiting ORDER BY (scope='segment') DESC,created_at,run_id,"
                "sequence LIMIT ? OFFSET ?",
                (*exclude, limit, offset),
            ).fetchall()
        ]
        for row in rows:
            row["wait_reason"] = (
                "等待模型：" + (row["last_error"] or "需要准备模型")
                if row["state"] == "needs_setup"
                else "等待相邻音频上下文"
                if row["state"] == "waiting_context"
                else "等待当前分析完成"
            )
        failures = [
            dict(row)
            for row in self.db.execute(
                "SELECT r.id run_id,r.scope,r.revision,r.session_id,r.segment_id,r.last_error,"
                "j.sequence,j.kind FROM analysis_runs r LEFT JOIN analysis_jobs j ON j.run_id=r.id "
                "AND j.state='failed' WHERE r.state='needs_attention' "
                "ORDER BY r.created_at LIMIT 100"
            ).fetchall()
        ]
        return {
            "waiting_count": count,
            "items": rows,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(rows) < count,
            "failures": failures,
            "failed_count": self.db.execute(
                "SELECT COUNT(*) FROM analysis_runs WHERE state='needs_attention'"
            ).fetchone()[0],
        }

    def requests(self, active: dict | None, limit: int = 50, offset: int = 0) -> dict:
        active_id = active["run_id"] if active else ""
        total = self.db.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0]
        rows = self.db.execute(
            "SELECT r.id run_id,r.session_id,r.scope,r.segment_id,r.revision,r.state,"
            "r.created_at,r.target_sequence,r.processed_sequence,r.last_error,r.export_error,"
            "r.pending_json IS NOT NULL pending_speech,"
            "s.anchor_name,s.title,s.started_at session_started_at,"
            "COUNT(c.id) total_segments,"
            "COALESCE(SUM(c.sequence<=r.processed_sequence),0) completed_segments,"
            "COALESCE(SUM(1.0*c.sample_count/c.sample_rate),0) target_seconds,"
            "COALESCE(SUM(CASE WHEN c.sequence<=r.processed_sequence "
            "THEN 1.0*c.sample_count/c.sample_rate END),0) processed_seconds "
            "FROM analysis_runs r JOIN sessions s ON s.id=r.session_id "
            "LEFT JOIN segments c ON c.session_id=r.session_id AND c.state='ready' "
            "AND c.sequence<=r.target_sequence AND (r.scope='session' OR c.id=r.segment_id) "
            "GROUP BY r.id ORDER BY (r.id=?) DESC,"
            "(r.state NOT IN ('complete','waiting_audio')) DESC,"
            "CASE WHEN r.state NOT IN ('complete','waiting_audio') "
            "THEN r.scope='segment' ELSE 0 END DESC,"
            "CASE WHEN r.state NOT IN ('complete','waiting_audio') THEN r.created_at END,"
            "r.created_at DESC,r.rowid DESC LIMIT ? OFFSET ?",
            (active_id, limit, offset),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["pending_speech"] = bool(item["pending_speech"])
            progress = (
                100 * item["completed_segments"] / item["total_segments"]
                if item["total_segments"]
                else 0
            )
            # Finishing deferred speech is required before a request is complete.
            if item["pending_speech"] or item["state"] not in {"complete", "waiting_audio"}:
                progress = min(progress, 99)
            item["progress_percent"] = round(progress, 1)
            items.append(item)
        return {
            "items": items,
            "total_count": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(items) < total,
        }

    def request_segments(
        self, run_id: str, active: dict | None, limit: int = 50, offset: int = 0
    ) -> dict:
        run = self.run(run_id)
        if not run:
            raise KeyError("分析请求不存在。")
        condition = (
            "s.session_id=? AND s.state='ready' AND s.sequence<=? AND (?='session' OR s.id=?)"
        )
        args = (run["session_id"], run["target_sequence"], run["scope"], run["segment_id"])
        total = self.db.execute(
            f"SELECT COUNT(*) FROM segments s WHERE {condition}", args
        ).fetchone()[0]
        rows = self.db.execute(
            "SELECT s.id segment_id,s.session_id,s.sequence,s.start_at,s.end_at,"
            "j.state job_state,j.last_error FROM segments s LEFT JOIN analysis_jobs j "
            "ON j.run_id=? AND j.kind='window' AND j.job_key=s.id "
            f"WHERE {condition} ORDER BY s.sequence LIMIT ? OFFSET ?",
            (run_id, *args, limit, offset),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item.update(run_id=run_id, scope=run["scope"], revision=run["revision"])
            item["state"] = (
                "complete"
                if item["sequence"] <= run["processed_sequence"]
                else "running"
                if active
                and active["run_id"] == run_id
                and active["kind"] == "window"
                and active["sequence"] == item["sequence"]
                else "failed"
                if item["job_state"] == "failed"
                else run["state"]
                if run["state"] in {"needs_setup", "waiting_context", "needs_attention"}
                else "pending"
            )
            item.pop("job_state")
            items.append(item)
        return {
            "run_id": run_id,
            "items": items,
            "total_count": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(items) < total,
        }

    def status(self, session_id: str) -> dict:
        run = self.latest(session_id)
        if not run:
            return {
                "state": "not_requested",
                "session_id": session_id,
                "backlog_seconds": 0,
                "unrequested_seconds": self.store.totals(session_id)[1],
            }
        sums = self.db.execute(
            "SELECT COALESCE(SUM(CASE WHEN sequence<=? THEN 1.0*sample_count/sample_rate END),0),"
            "COALESCE(SUM(CASE WHEN sequence<=? THEN 1.0*sample_count/sample_rate END),0),"
            "COALESCE(SUM(1.0*sample_count/sample_rate),0) FROM segments "
            "WHERE session_id=? AND state='ready'",
            (run["processed_sequence"], run["target_sequence"], session_id),
        ).fetchone()
        result = {
            key: value for key, value in run.items() if key not in {"pending_json", "config_json"}
        }
        result.update(
            {
                "processed_seconds": sums[0],
                "target_seconds": sums[1],
                "backlog_seconds": max(0, sums[1] - sums[0]),
                "unrequested_seconds": max(0, sums[2] - sums[1]),
                "pending_speech": bool(run["pending_json"]),
                "utterance_count": self.db.execute(
                    "SELECT COUNT(*) FROM utterances WHERE run_id=?", (run["id"],)
                ).fetchone()[0],
                "speakers": [
                    {"id": p["id"], "number": p["number"]} for p in self.profiles(run["id"])
                ],
                "config": json.loads(run["config_json"]),
            }
        )
        return result


def decode_utterance(row: dict) -> dict:
    for name in ("speaker_ids", "flags", "sources"):
        row[name] = json.loads(row.pop(name + "_json"))
    return row
