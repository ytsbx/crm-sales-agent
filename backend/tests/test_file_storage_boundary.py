"""§8.6 文件存储的目录边界与半文件清理（离线：只算路径 + 临时目录，不读系统文件）。

守的问题
--------
1. `storage.absolute_path` 用**字符串 startswith** 判断"在不在存储根目录内"。
   根目录取 `/tmp/crm-proof-files` 时，键 `../crm-proof-files-other/secret.txt`
   解析出来是 `/tmp/crm-proof-files-other/secret.txt`，字符串确实以根目录开头
   ——于是同前缀的**兄弟目录**被当成了根目录内。当前上传键是系统生成的 UUID、
   不由用户自由选择，所以这不是"现在就能任意读系统文件"，但它是一处已经确认的
   边界判断错误：边界一旦被当成字符串前缀，任何将来接受外部键的入口都会直接穿。
2. `save_upload` 只在"超限"这一条路径上删了半成品；**读取中断**（弱网、客户端断连）
   时那个半文件就留在盘上了。下载时才知道是半份，而且没人知道它是垃圾还是原件。

修法：`resolve()` 之后按**真实父子路径关系**判定（`Path.is_relative_to`），
并在任何异常路径上清掉**本次刚生成的那个**文件。清理只按本次的 object_key 精确删，
不扫目录、不按前缀删——否则迟早误删历史原件。

跑法：
    cd backend
    PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -m pytest -q tests/test_file_storage_boundary.py
"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.modules.file import storage


@pytest.fixture()
def root(tmp_path, monkeypatch):
    """把存储根指到 pytest 的临时目录：不碰仓库 data/，也不读真实系统文件。"""
    base = tmp_path / "crm-proof-files"
    base.mkdir()
    monkeypatch.setattr(settings, "file_root", str(base))
    return base


def _leftovers(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _make_dir_link(link: Path, target: Path) -> None:
    """在 `link` 处建一个指向 `target` 的目录链接，建不了就跳过这一档。

    先试符号链接；Windows 上普通进程常常没有建符号链接的特权
    （WinError 1314），而目录 junction 不需要那个特权，所以退化一次——
    否则"符号链接逃逸"这一档在开发机上永远测不到。
    """
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        created = subprocess.run(  # noqa: S603 —— 只是本机 cmd 内建命令，不连网
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if created.returncode == 0:
            return
    pytest.skip("本机既不给符号链接权限、也建不了目录 junction，跳过这一档")


# --------------------------------------------------------------- 路径边界


def test_normal_year_month_uuid_key_is_inside_root(root):
    """合法键：`2026/10/<uuid>.png` 必须仍然能解析，且落在根目录内。"""
    key = "2026/10/8f14e45fceea167a5a36dedd4bea2543.png"
    path = storage.absolute_path(key)

    assert path == (root / key).resolve()
    assert path.is_relative_to(root.resolve())


def test_sibling_directory_with_same_prefix_is_rejected(root):
    """同前缀兄弟目录：字符串 startswith 会放行，真实父子关系必须拒绝。

    这条就是 §8.6 的复现探针：只计算路径，不读那个文件。
    """
    with pytest.raises(AppError) as exc:
        storage.absolute_path("../crm-proof-files-other/secret.txt")

    assert "非法的文件路径" in exc.value.message


def test_absolute_key_escape_is_rejected(root, tmp_path):
    """绝对路径键：不能借 `root / "/x"` 落到根目录外。"""
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(AppError):
        storage.absolute_path(str(outside / "secret.txt"))


def test_parent_traversal_is_rejected(root):
    """普通 `..` 穿越：一直都要拦住（对照，别为了修同前缀把这条放松了）。"""
    with pytest.raises(AppError):
        storage.absolute_path("../../secret.txt")


def test_symlink_escape_is_rejected(tmp_path, root):
    """符号链接/junction 逃逸：根目录内的链接指向根目录**外**，resolve 后必须被拒。

    目标目录刻意取**同前缀**的兄弟目录：这样这条同时也是"字符串 startswith 不是
    目录归属"的证据——修前会放行，修后必须拒绝。
    """
    outside = tmp_path / "crm-proof-files-other"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    _make_dir_link(root / "link", outside)

    with pytest.raises(AppError) as exc:
        storage.absolute_path("link/secret.txt")

    assert "非法的文件路径" in exc.value.message


# --------------------------------------------------------------- 失败清理


class _BrokenUpload:
    """读到第二块时抛异常的上传，模拟客户端中途断连（弱网重试常见）。"""

    def __init__(self, filename: str = "half.png") -> None:
        self.filename = filename
        self.content_type = "image/png"
        self._reads = 0

    async def read(self, _size: int) -> bytes:
        self._reads += 1
        if self._reads == 1:
            return b"\x89PNG\r\n\x1a\n" + b"x" * 1024
        raise OSError("连接被对端重置")


class _OversizeUpload:
    """一次就读出超过上限内容的上传。"""

    def __init__(self, filename: str = "big.bin") -> None:
        self.filename = filename
        self.content_type = "application/octet-stream"
        self._sent = False

    async def read(self, _size: int) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return b"y" * 4096


def test_read_failure_leaves_no_half_file(root):
    """读取中途失败：不能留下半文件（修前那个 1032 字节的半成品会留在盘上）。"""
    with pytest.raises(OSError):
        asyncio.run(storage.save_upload(_BrokenUpload()))

    assert _leftovers(root) == []


def test_read_failure_does_not_touch_other_files(root):
    """失败补偿只清本次的文件，历史原件一个都不许动。"""
    old = root / "2026" / "09" / "signed-contract.pdf"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"original")

    with pytest.raises(OSError):
        asyncio.run(storage.save_upload(_BrokenUpload()))

    assert old.read_bytes() == b"original"
    assert _leftovers(root) == [old]


def test_oversize_upload_leaves_no_file(root, monkeypatch):
    """超限：也不能留半文件（原有行为，补一条防倒退）。"""
    monkeypatch.setattr(settings, "max_upload_mb", 0)

    with pytest.raises(AppError) as exc:
        asyncio.run(storage.save_upload(_OversizeUpload()))

    assert "MB 限制" in exc.value.message
    assert _leftovers(root) == []


def test_successful_upload_still_writes_the_content(root):
    """对照：正常上传仍要落盘并给出校验值，别把清理写成"全都不要了"。"""

    class _GoodUpload:
        filename = "good.png"
        content_type = "image/png"

        def __init__(self) -> None:
            self._chunks = [b"\x89PNG" + b"a" * 32, b""]

        async def read(self, _size: int) -> bytes:
            return self._chunks.pop(0)

    object_key, size, checksum = asyncio.run(storage.save_upload(_GoodUpload()))

    assert storage.absolute_path(object_key).read_bytes() == b"\x89PNG" + b"a" * 32
    assert size == 36
    assert len(checksum) == 64
