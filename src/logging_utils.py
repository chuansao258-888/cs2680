import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def create_log_file():
    log_dir = Path.cwd() / "madsLoop_logs"
    log_dir.mkdir(exist_ok=True)

    return log_dir / f"{uuid4()}.jsonl"


def write_event(log_file, event, **fields):
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }

    with log_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")