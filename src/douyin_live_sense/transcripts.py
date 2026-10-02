import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .analysis_storage import AnalysisStorage


def beijing(timestamp: str) -> str:
    return (
        datetime.fromisoformat(timestamp)
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def speaker_label(utterance: dict, numbers: dict[str, int]) -> str:
    names = [f"人物 {numbers[identity]}" for identity in utterance["speaker_ids"]]
    if "speaker_uncertain" in utterance["flags"]:
        names.append("人物待确认")
    label = "、".join(names) or "人物待确认"
    return "重叠讲话：" + label if "overlap" in utterance["flags"] else label


def chronological(row: dict) -> tuple[str, str, str]:
    return row["start_at"], row["end_at"], row["id"]


def export_run(storage: AnalysisStorage, run: dict) -> dict[str, Path]:
    root = storage.store.data_dir / "analysis" / run["session_id"] / run["id"]
    root.mkdir(parents=True, exist_ok=True)
    numbers = {profile["id"]: profile["number"] for profile in storage.profiles(run["id"])}
    entries, rows, cursor = [], [], 0
    while page := storage.utterances(run["id"], cursor, 500):
        for row in page:
            row["speaker_label"] = speaker_label(row, numbers)
            row["model_config"] = json.loads(run["config_json"])
            rows.append(row)
            text = row["text"] or "[检测到讲话，但未识别出文字]"
            entries.append(
                (
                    chronological(row),
                    f"{beijing(row['start_at'])}–{beijing(row['end_at'])} | "
                    f"{row['speaker_label']} | {text}",
                )
            )
        cursor = page[-1]["ordinal"]
    for event in storage.store.events(run["session_id"]) if run["scope"] == "session" else []:
        if event["kind"] == "gap":
            end = beijing(event["ended_at"]) if event["ended_at"] else "等待恢复"
            entries.append(
                (
                    (
                        event["started_at"],
                        event["ended_at"] or event["started_at"],
                        str(event["id"]),
                    ),
                    f"{beijing(event['started_at'])}–{end} | 采集缺口 | {event['detail']}",
                )
            )
    entries.sort(key=lambda entry: entry[0])
    text = "\n".join(value for _, value in entries)
    note = "时间为音频接收时间（北京时间）；人物编号仅在本场次、此分析版本内有效。"
    if run["scope"] == "segment":
        note = (
            f"单片复核 v{run['revision']}；时间为音频接收时间（北京时间）；"
            "人物编号仅在此切片、此复核版本内有效，不更新整场记录。"
        )
    documents = {
        "txt": note + "\n\n" + text + "\n",
        "md": f"# 普通话直播记录\n\n{note}\n\n" + "\n\n".join(value for _, value in entries) + "\n",
        "jsonl": "\n".join(
            json.dumps(row, ensure_ascii=False) for row in sorted(rows, key=chronological)
        )
        + ("\n" if rows else ""),
    }
    paths = {}
    for extension, content in documents.items():
        final = root / f"transcript.{extension}"
        partial = final.with_suffix(final.suffix + ".tmp")
        with partial.open("w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(partial, final)
        paths[extension] = final
    with storage.db:
        storage.db.execute("UPDATE analysis_runs SET export_error=NULL WHERE id=?", (run["id"],))
    return paths
