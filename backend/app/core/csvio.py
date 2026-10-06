"""CSV 导入导出公共工具。

客户、线索、产品三个模块都要"下载模板 / 导入 / 导出"。抽在一起是因为
三个模块的编码坑完全一样，各写一份必然有一份漏掉：

1. **导出要带 BOM**（utf-8-sig）：不带的话 Excel 双击打开中文全乱码；
2. **导入要容忍 GBK**：中文 Excel 默认另存是 GBK，只认 utf-8 会让用户
   一脸茫然地看到"文件编码无法识别"；
3. **表头缺失要报清楚缺哪个**，而不是 IndexError。
"""

import csv
import io

from fastapi import UploadFile

from app.core.errors import AppError, ErrorCode

#: 导入时依次尝试的编码。utf-8-sig 放最前，因为带 BOM 的文件用 utf-8 解会多出 \ufeff。
IMPORT_ENCODINGS = ("utf-8-sig", "utf-8", "gbk")


def csv_bytes(rows: list[list], headers: list[str]) -> bytes:
    """生成带 BOM 的 CSV：Excel 双击打开不乱码。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def decode_upload(raw: bytes, *, label: str = "文件") -> str:
    """把上传的字节按 utf-8-sig / utf-8 / gbk 依次解码。"""
    for encoding in IMPORT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AppError(
        ErrorCode.PARAM_ERROR, f"{label}编码无法识别，请另存为 UTF-8 或 GBK 的 CSV"
    )


def _norm(name: str) -> str:
    """表头归一：去空格、全角括号转半角——Excel 手填表头最容易在这两个地方跑偏。"""
    return (name or "").replace("（", "(").replace("）", ")").replace(" ", "").strip()


async def parse_csv_upload(
    file: UploadFile,
    *,
    required_headers: list[str],
    label: str = "文件",
) -> list[dict]:
    """解析上传的 CSV，校验必需表头，丢掉空行。

    返回 `csv.DictReader` 的行列表，调用方按中文表头取值。
    """
    raw = await file.read()
    return parse_csv_bytes(raw, required_headers=required_headers, label=label)


def parse_csv_bytes(
    raw: bytes,
    *,
    required_headers: list[str],
    label: str = "文件",
) -> list[dict]:
    """`parse_csv_upload` 的字节版。

    单独留一个字节入口，是因为导入还要算**文件摘要**（预览快照按文件比对），
    而 `UploadFile` 只能读一次 —— 让调用方先 `read()` 再交给这里，
    比"读完再想办法塞回 UploadFile"干净得多。
    """
    text = decode_upload(raw, label=label)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise AppError(ErrorCode.PARAM_ERROR, f"{label}是空的，或者没有表头")
    field_map = {_norm(name): name for name in reader.fieldnames if name}
    missing = [name for name in required_headers if _norm(name) not in field_map]
    if missing:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"表头缺少：{'、'.join(missing)}。请先下载导入模板按格式填写",
        )
    # 行的键同样归一化，调用方按模板表头原样取值即可
    return [
        {_norm(k): (v or "") for k, v in row.items() if k}
        for row in reader
        if any((v or "").strip() for v in row.values())
    ]


__all__ = [
    "IMPORT_ENCODINGS",
    "csv_bytes",
    "decode_upload",
    "parse_csv_bytes",
    "parse_csv_upload",
]
