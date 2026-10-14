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

from app.core.config import settings
from app.core.errors import AppError, ErrorCode

#: 导入时依次尝试的编码。utf-8-sig 放最前，因为带 BOM 的文件用 utf-8 解会多出 \ufeff。
IMPORT_ENCODINGS = ("utf-8-sig", "utf-8", "gbk")


#: 会被 Excel / WPS 当成**公式起始**的字符。CSV 没有"单元格类型"，
#: 只靠引号包起来**挡不住** —— Excel 打开时照样按公式解析（第四批审查 P2 实测：
#: 客户名 `=1+1` 导出后仍是 `=1+1`，打开就是公式）。
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _safe_csv_text(value: object) -> object:
    """导出前给用户文本加保护；数字**原样**返回，保住数值语义。

    为什么只动字符串：`csv.writer` 写出来的是纯文本，Excel 靠"看起来像不像公式"
    来决定要不要求值。所以只要给**以公式字符开头的字符串**加一个前导单引号
    （Excel 的"强制文本"约定），公式就不会被求值；而**数值列**如果被转成字符串，
    排序、求和、透视表就全废了 —— 所以 `int` / `float` / `Decimal` 一律不动。

    为什么不加上下文无关的通用转义：客户名、产品名、备注这些是**外部输入**
    （客户自己填的、导入进来的），最坏情况是 `=HYPERLINK(...)` 这种外链公式。
    成本只有"如果用户真的想以 `=` 开头，前面会多一个不可见引号"。
    """
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def csv_bytes(rows: list[list], headers: list[str]) -> bytes:
    """生成带 BOM 的 CSV：Excel 双击打开不乱码。

    ⚠️ 这里是**全站 CSV 导出的唯一出口**（客户 / 线索 / 产品 / SKU / 费用 …），
    所以公式注入的防护也落在这里一处 —— 各模块自己转义迟早漏一个。
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([_safe_csv_text(h) for h in headers])
    writer.writerows([[_safe_csv_text(cell) for cell in row] for row in rows])
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
    raw = await read_upload_bytes(file)
    return parse_csv_bytes(raw, required_headers=required_headers, label=label)


async def read_upload_bytes(file: UploadFile) -> bytes:
    """分块读取导入文件，并在读入内存前执行统一大小上限。"""
    limit = settings.max_upload_mb * 1024 * 1024
    if file.size is not None and file.size > limit:
        raise AppError(ErrorCode.PARAM_ERROR, f"文件超过 {settings.max_upload_mb} MB 限制", 422)

    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise AppError(ErrorCode.PARAM_ERROR, f"文件超过 {settings.max_upload_mb} MB 限制", 422)
        chunks.append(chunk)
    return b"".join(chunks)


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

    # ⚠️ **某一行比表头列多**时，`DictReader` 会把多出来的值放进**列表**、
    # 挂在 `None` 键下（实测 `{'客户名称': '', None: ['多余值']}`）。从前这里直接
    # 对所有值调 `.strip()`，于是 `AttributeError: 'list' object has no attribute
    # 'strip'` —— 崩在**逐行处理之前**，整份文件 500，用户只看到"服务器内部错误"，
    # 连是哪一行的错都不知道（第四批审查 P2，2026-10-12 实测）。
    #
    # 现在的处理：**报清楚是哪一行、多了几个值**，让用户去改文件。
    # 不静默丢掉多余列 —— 丢掉会让"填错列的人"以为导入成功了，
    # 比报错更糟（他以为自己填的那个字段进去了）。
    rows: list[dict] = []
    for offset, row in enumerate(reader, start=2):  # 第 1 行是表头
        extra = row.pop(None, None)
        mapped = row.values()
        # 空行的判据：**表头列和多余列全是空白**。只判表头列是不够的 ——
        # `,多余值` 这一行的表头列恰好是空的，会被误当成空行**静默丢掉**，
        # 而它其实带着没人要的数据（我第一版就这么写错了，测出来才发现）。
        blank = not any((v or "").strip() for v in mapped if isinstance(v, str)) and not any(
            str(v).strip() for v in (extra or [])
        )
        if blank:
            continue
        if extra:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"第 {offset} 行的列数比表头多（多出 {len(extra)} 个值："
                f"{'、'.join(str(v) for v in extra[:3])}）。"
                "常见原因：某个单元格里填了逗号却没加引号，或者多写了一列。"
                "请对照模板核对这一行后重新上传",
            )
        # 行的键同样归一化，调用方按模板表头原样取值即可
        rows.append({_norm(k): (v or "") for k, v in row.items() if k})
    return rows


__all__ = [
    "IMPORT_ENCODINGS",
    "csv_bytes",
    "decode_upload",
    "parse_csv_bytes",
    "parse_csv_upload",
    "read_upload_bytes",
]
