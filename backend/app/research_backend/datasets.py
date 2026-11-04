"""Immutable tabular inputs. No uploaded code, formulas, or pickle is executed."""
import csv
import hashlib
import io
import json
import math
import uuid
import zipfile
from collections import Counter
from pathlib import Path

MISSING = {"", "na", "n/a", "null", "none", "nan"}
MAX_ROWS, MAX_COLUMNS = 50000, 100


def scalar(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("数据包含 Infinity 等非有限数值，请先清理")
        return value
    value = str(value).strip()
    if len(value) > 4000:
        raise ValueError("单个单元格文本不得超过 4000 字符")
    if value.lower() in MISSING:
        return None
    return value


def parse_table(filename, content):
    if not content or len(content) > 20 * 1024 * 1024:
        raise ValueError("数据文件不能为空且不得超过 20 MB")
    suffix = Path(filename).suffix.lower()
    warnings = []
    if suffix in {".csv", ".tsv"}:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = content.decode("gb18030")
                warnings.append("文件按 GB18030 解码；建议保存为 UTF-8 CSV。")
            except UnicodeDecodeError as exc:
                raise ValueError("无法识别文件编码，请使用 UTF-8 CSV") from exc
        csv.field_size_limit(100000)
        iterator = csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ",", strict=True)
        try:
            raw = []
            for row in iterator:
                if not any(str(value).strip() for value in row):
                    continue
                raw.append(row)
                if len(raw) > MAX_ROWS + 1:
                    raise ValueError("交互分析最多支持 50000 行，请先分批整理")
        except csv.Error as exc:
            raise ValueError("CSV 格式无效，请检查分隔符、引号或超长字段") from exc
    elif suffix == ".xlsx":
        from openpyxl import load_workbook
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if len(archive.infolist()) > 5000 or sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                    raise ValueError("工作簿解压规模过大，请另存为 CSV")
            book = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
            try:
                sheet = book.worksheets[0]
                if (sheet.max_column or 0) > MAX_COLUMNS:
                    raise ValueError("最多支持 100 列，请先删除多余列")
                if (sheet.max_row or 0) > MAX_ROWS + 1:
                    raise ValueError("工作表范围超过 50000 行，请清除多余格式或另存为 CSV")
                raw = []
                for row in sheet.iter_rows(values_only=True, max_row=min(sheet.max_row or MAX_ROWS + 2, MAX_ROWS + 2), max_col=sheet.max_column or MAX_COLUMNS):
                    if not any(value is not None for value in row):
                        continue
                    raw.append(list(row))
                    if len(raw) > MAX_ROWS + 1:
                        raise ValueError("交互分析最多支持 50000 行")
                warnings.append(f"只读取首个工作表「{sheet.title}」；公式仅读取已保存的缓存值，不执行或重新计算。")
            finally:
                book.close()
        except (zipfile.BadZipFile, KeyError, IndexError) as exc:
            raise ValueError("无法读取 XLSX，请检查文件或另存为 CSV") from exc
    else:
        raise ValueError("仅支持 CSV、TSV、XLSX 数据文件")
    if len(raw) < 3:
        raise ValueError("数据需要一行列名和至少两行观测值")
    names = [str(value).strip() if value is not None else "" for value in raw[0]]
    if not 1 <= len(names) <= MAX_COLUMNS or any(not name or len(name) > 100 for name in names):
        raise ValueError("需要 1–100 个非空列名，每个不超过 100 字符")
    if len(names) != len(set(names)):
        raise ValueError("列名不能重复，请先为每列设置唯一名称")
    rows = []
    for index, row in enumerate(raw[1:], start=2):
        if len(row) != len(names):
            raise ValueError(f"第 {index} 行的列数与表头不一致")
        rows.append([scalar(value) for value in row])
    columns = []
    for index, name in enumerate(names):
        values = [row[index] for row in rows if row[index] is not None]
        converted = []
        numeric = bool(values)
        for value in values:
            try:
                number = float(value)
                if not math.isfinite(number) or abs(number) > 1e100:
                    raise ValueError("数值必须有限且绝对值不超过 1e100")
                converted.append(number)
            except (TypeError, ValueError):
                numeric = False
                break
        if numeric:
            for row in rows:
                if row[index] is not None:
                    row[index] = float(row[index])
        columns.append(dict(name=name, kind="numeric" if numeric else "categorical", missing=len(rows) - len(values), unique=len(Counter(str(value) for value in values))))
    if any(column["missing"] for column in columns):
        warnings.append("空单元格及 NA / N/A / null / None / NaN 视为缺失值；不自动填补。")
    return dict(columns=names, rows=rows), columns, warnings


def add_dataset(store, owner, wid, filename, content, title=None, source_id=None, page=None, note=""):
    store.workspace(owner, wid)
    if source_id:
        store.get(owner, wid, "source", source_id)
    if page is not None and not source_id:
        raise ValueError("关联论文页码时必须同时选择文献")
    data, columns, warnings = parse_table(filename, content)
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    record_id = str(uuid.uuid4())
    directory = store.root / wid / "datasets" / record_id
    directory.mkdir(parents=True)
    (directory / "data.json").write_bytes(encoded)
    (directory / ("original" + Path(filename).suffix.lower())).write_bytes(content)
    return store.put(owner, wid, "dataset", dict(title=title or Path(filename).name[:200], filename=Path(filename.replace("\\", "/")).name[:200],
        row_count=len(data["rows"]), columns=columns, preview=data["rows"][:15],
        data_hash=hashlib.sha256(encoded).hexdigest(), original_hash=hashlib.sha256(content).hexdigest(),
        source_id=source_id, page=page, note=note, warnings=warnings), record_id=record_id)


def read_dataset(store, owner, wid, dataset_id):
    item = store.get(owner, wid, "dataset", dataset_id)
    content = (store.root / wid / "datasets" / item["id"] / "data.json").read_bytes()
    if hashlib.sha256(content).hexdigest() != item["data_hash"]:
        raise ValueError("数据校验失败：文件与导入版本不一致，请重新导入")
    return item, json.loads(content)
