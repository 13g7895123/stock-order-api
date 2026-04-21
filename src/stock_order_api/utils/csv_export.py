"""CSV 匯出工具。"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path


def export_rows(
    rows: Sequence[Mapping[str, object]],
    kind: str,
    out_dir: Path | str = "exports",
    fieldnames: Sequence[str] | None = None,
) -> Path:
    """把 list[dict] 匯出成 CSV。回傳實際寫入的檔案路徑。

    檔名：`YYYYMMDD_HHMMSS_<kind>.csv`
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{ts}_{kind}.csv"

    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return path

    cols = list(fieldnames) if fieldnames else list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def models_to_rows(models: Iterable[object]) -> list[dict[str, object]]:
    """把 pydantic model 列表轉成 list[dict]（不依賴 pydantic）。"""
    out: list[dict[str, object]] = []
    for m in models:
        if hasattr(m, "model_dump"):
            out.append(m.model_dump(mode="json"))
        elif hasattr(m, "dict"):
            out.append(m.dict())
        elif isinstance(m, dict):
            out.append(dict(m))
        else:
            out.append({"value": str(m)})
    return out
