"""文件中心接口（对齐 03-API §31）。"""

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.config import settings
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.file.access import (
    SUPPLEMENT_CATEGORY,
    SUPPLEMENT_LABEL,
    can_access_file,
    protection_label,
    sample_write_lock_label,
    visible_object,
)
from app.modules.file import access, service, storage
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.user.model import User

logger = logging.getLogger("crm.file")

router = APIRouter(tags=["File"])

# 原件保护判据（哪些类别不可破坏、对应什么人话）**集中在 access.PROTECTED_CATEGORIES**，
# 不在这里再维护一份：删除、解绑、以后任何会动到原件的入口都调
# `file_protection_label` / `protection_label`。两处口径分叉出来的那一份就是绕过通道。


def serialize_file(record: FileRecord, uploader: str | None = None) -> dict:
    return {
        "id": record.id,
        "file_name": record.file_name,
        "mime_type": record.mime_type,
        "size": record.size,
        "storage_provider": record.storage_provider,
        # 前端据此决定显示"预览"还是"下载"
        "previewable": is_previewable(record),
        "uploaded_by": record.uploaded_by,
        "uploader_name": uploader,
        "created_at": record.created_at,
    }


@router.post("/files/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    business_type: str | None = Form(default=None),
    business_id: int | None = Form(default=None),
    category: str | None = Form(default=None),
    user: CurrentUser = Depends(require_permission("file:manage")),
    session: AsyncSession = Depends(get_db),
):
    if (business_type is None) != (business_id is None):
        raise AppError(ErrorCode.PARAM_ERROR, "业务类型和业务 id 必须同时提供", 422)
    if business_type is not None and business_id is not None and not await visible_object(
        session, user, business_type=business_type, business_id=business_id, write=True
    ):
        # 上传是**写入**：只看"能不能看见"不够。看见了别人的报价不等于能往上加附件，
        # 而且没有该模块写入权的人也不该借附件改变那个业务对象的内容。
        raise AppError(
            ErrorCode.DATA_SCOPE_DENIED,
            "不能给该业务对象上传附件：它不在你的数据范围内，或你没有该模块的维护权限",
            403,
        )
    # 打样单制作/寄出之后：新附件只能标成「后续补充资料」。
    # 制作依据（图纸、规格书）必须在制作当时就指定好，事后混进来的资料
    # 不能和依据混为一谈——见 access.sample_write_lock_label。
    if business_type == "sample" and business_id is not None:
        lock = await sample_write_lock_label(session, business_id)
        if lock is not None and (category or "").strip() != SUPPLEMENT_CATEGORY:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"{lock}，不能再往上加制作依据类的附件。"
                f"事后补进来的资料请把类别选成「{SUPPLEMENT_LABEL}」"
                f"（category={SUPPLEMENT_CATEGORY}）——两者要能分得开，"
                f"否则事后说不清当时是按哪份资料做的",
                422,
            )

    object_key, size, checksum = await storage.save_upload(file)
    record = FileRecord(
        storage_provider=settings.storage_provider,
        object_key=object_key,
        file_name=file.filename or "未命名文件",
        mime_type=file.content_type,
        size=size,
        checksum=checksum,
        uploaded_by=user.id,
    )
    try:
        session.add(record)
        await session.flush()

        if business_type and business_id:
            session.add(
                BusinessFile(
                    business_type=business_type,
                    business_id=business_id,
                    file_id=record.id,
                    category=category,
                )
            )
        await write_audit(
            session,
            operator_id=user.id,
            action="upload",
            business_type="file",
            business_id=record.id,
            # 目标业务对象要写进审计：附件挂到哪个对象上，等于把访问范围交给谁。
            # 只记 business_type 时，事后查不出这份文件到底挂到了哪一条业务记录上
            # （挂载/解绑的审计也是同一个道理，见 attach/detach）。
            after={
                "file_name": record.file_name,
                "size": size,
                "target": business_type,
                "target_id": business_id,
                "category": category,
            },
            ip=client_ip(request),
        )
        await session.commit()
    except Exception:
        # 登记失败（约束冲突 / 连接断开 / 审计写入报错）时，刚写上盘的那份文件
        # 没有任何记录指向它——不清理就永远堆在盘上，而且和"上传成功"的文件
        # 长得一模一样，事后谁也分不清哪份是垃圾。
        # 补偿逻辑统一在 `file.service`：回款凭证那个专用上传入口共用同一份，
        # 不再各写一遍（第十一批 11.8 —— 它原先就是漏了这段）。
        await service.discard_unregistered_upload(session, object_key)
        raise
    return ok(serialize_file(record, user.name), "上传成功")


@router.get("/files/{file_id}")
async def get_file(
    file_id: int,
    user: CurrentUser = Depends(require_permission("file:view")),
    session: AsyncSession = Depends(get_db),
):
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    if not await access.can_access_file(session, user, file_id):
        raise AppError(ErrorCode.FORBIDDEN, "该文件所在的业务对象不在你的数据范围内", 403)
    return ok(serialize_file(record))


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: int,
    user: CurrentUser = Depends(require_permission("file:view")),
    session: AsyncSession = Depends(get_db),
):
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    # 附件挂在业务对象上，必须反查可见性；只校验 file:view 会让任何人按 id 取走别人的文件
    if not await access.can_access_file(session, user, file_id):
        raise AppError(ErrorCode.FORBIDDEN, "该文件所在的业务对象不在你的数据范围内", 403)
    path = storage.absolute_path(record.object_key)
    if not path.exists():
        raise AppError(ErrorCode.NOT_FOUND, "文件内容已丢失", 404)
    return FileResponse(
        path,
        media_type=record.mime_type or "application/octet-stream",
        filename=record.file_name,
        headers={
            # 中文文件名要走 RFC 5987，否则浏览器会乱码
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(record.file_name)}"
        },
    )


# 能内联预览的类型。刻意只放"浏览器原生渲染且不容易执行脚本"的。
PREVIEWABLE_MIME_PREFIXES = ("image/",)
PREVIEWABLE_MIME_EXACT = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "text/markdown",
}

# 显式拒绝内联的类型。前缀匹配（image/*）会漏掉下面这些，
# 所以必须先按"危险清单"挡一道：
#   svg  : 是图片但能内嵌 <script>，内联即 XSS
#   html/xml: 同理
# 这类一律降级为下载（浏览器下载后本地打开的风险由用户自己承担，
# 至少不会以我们站点的身份执行）。
DANGEROUS_MIME_EXACT = {
    "image/svg+xml",
    "text/html",
    "application/xhtml+xml",
    "text/xml",
    "application/xml",
}
DANGEROUS_SUFFIXES = {".svg", ".svgz", ".html", ".htm", ".xhtml", ".xml"}

PREVIEWABLE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".pdf",
    ".txt",
    ".csv",
    ".md",
    ".log",
}


def is_previewable(record: FileRecord) -> bool:
    mime = (record.mime_type or "").lower()
    suffix = Path(record.file_name or "").suffix.lower()
    # 危险清单优先于一切：mime 说是图片也不行
    if mime in DANGEROUS_MIME_EXACT or suffix in DANGEROUS_SUFFIXES:
        return False
    if mime.startswith(PREVIEWABLE_MIME_PREFIXES) or mime in PREVIEWABLE_MIME_EXACT:
        return True
    return suffix in PREVIEWABLE_SUFFIXES


@router.get("/files/{file_id}/preview")
async def preview_file(
    file_id: int,
    user: CurrentUser = Depends(require_permission("file:view")),
    session: AsyncSession = Depends(get_db),
):
    """内联预览（PRD §25 的「预览」）。

    与 download 的区别只在于 `Content-Disposition: inline`：让浏览器直接渲染，
    而不是弹下载框。**不是**把文件内容转成 HTML —— 那样等于自己造 XSS 通道。
    不可预览的类型返回 `inline=false` 并让前端走下载，不做静默降级。
    """
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    if not await access.can_access_file(session, user, file_id):
        raise AppError(ErrorCode.FORBIDDEN, "该文件所在的业务对象不在你的数据范围内", 403)
    path = storage.absolute_path(record.object_key)
    if not path.exists():
        raise AppError(ErrorCode.NOT_FOUND, "文件内容已丢失", 404)

    if not is_previewable(record):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{record.file_name}」的类型不支持在线预览，请下载后查看",
            422,
        )

    return FileResponse(
        path,
        media_type=record.mime_type or "application/octet-stream",
        filename=record.file_name,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(record.file_name)}",
            # 防嗅探：浏览器不得把内容当成别的类型执行
            "X-Content-Type-Options": "nosniff",
        },
    )


#: 文件名的长度上限，与 `FileRecord.file_name` 的列宽一致。
#: 两处不一致时会出现"校验放行、落库被截断/报错"，所以写成一个常量。
FILE_NAME_MAX = 255


class FileRename(BaseModel):
    """改文件名的入参。只收一个名字，多余的字段不认（`extra` 默认禁止）。"""

    file_name: str


@router.patch("/files/{file_id}")
async def rename_file(
    file_id: int,
    payload: FileRename,
    request: Request,
    user: CurrentUser = Depends(require_permission("file:manage")),
    session: AsyncSession = Depends(get_db),
):
    """改这份文件的**展示名**（2026-10-08）。

    ## 为什么只改展示名

    `files` 表里名字分两处：`file_name` 是给人看的（列表里显示、下载时落成本地文件名），
    `object_key` 是磁盘上的存放位置。**改名绝不动 `object_key`** —— 动它就要搬文件，
    搬运过程中任何一步失败都会留下"记录指着不存在的路径"（下载时才 404）。

    ## 为什么合同那类原件不许改名

    名字本身是线索：合同的生成稿叫「销售合同 xxx V1.pdf」，随手改成「…V2.pdf」之后，
    台账上就对不上了——真出纠纷时"客户签的到底是哪一版"说不清。
    所以这里**与"不可删除"共用同一份判据**（`file.service.inspect_file_usage` 的
    `protected`）：不改名和不能删是同一类限制，原件只许走专门流程（作废 / 开修订版）。

    ## 名字的校验（都在这里给中文原因，不靠框架的通用报错）

    - 空、或去掉首尾空白后为空 → 400；
    - 超过 `FILE_NAME_MAX` → 400（列宽上限）；
    - **含控制字符（含换行/制表）→ 400**：这个名字会进 `Content-Disposition`
      响应头，带换行的名字等于往响应头里注入一行。
    """
    if not await access.can_access_file(session, user, file_id):
        raise AppError(ErrorCode.DATA_SCOPE_DENIED, "该文件不在你的可见范围内", 403)
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)

    name = (payload.file_name or "").strip()
    if not name:
        raise AppError(ErrorCode.PARAM_ERROR, "文件名不能是空的", 400)
    if len(name) > FILE_NAME_MAX:
        raise AppError(
            ErrorCode.PARAM_ERROR, f"文件名最多 {FILE_NAME_MAX} 个字，现在是 {len(name)}", 400
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise AppError(
            ErrorCode.PARAM_ERROR, "文件名里不能有换行、制表这类控制字符", 400
        )

    usage = await service.inspect_file_usage(session, file_id)
    if usage.protected is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该文件是{usage.protected}，名字不能改——它记的就是「当时是哪一份」；"
            "确需纠错请走作废等专门流程",
            422,
        )

    before = record.file_name
    if before == name:
        # 名字没变就不写审计：一次"点了保存但什么都没改"不该在流水里留一条
        return ok(serialize_file(record), "文件名没有变化")
    record.file_name = name
    await write_audit(
        session,
        operator_id=user.id,
        action="rename",
        business_type="file",
        business_id=file_id,
        before={"file_name": before},
        after={"file_name": name},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize_file(record), "文件名已修改")


@router.delete("/files/{file_id}")
async def delete_file(
    file_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("file:manage")),
    session: AsyncSession = Depends(get_db),
):
    # 删文件也是写操作：看不到的文件不能删（`can_access_file` 按关联的业务对象判可见性）
    if not await can_access_file(session, user, file_id):
        raise AppError(ErrorCode.DATA_SCOPE_DENIED, "该文件不在你的可见范围内", 403)
    # **锁住文件行再查引用**：与"给这个文件加引用"的入口（通用挂载 / 产品挂附件 /
    # 合同登记签署 / 回款删凭证）用同一把锁。不加锁时，另一个事务可以在下面这段
    # 检查跑完之后、真正删除之前把引用挂上来（第十一批 11.2 第 7 条）。
    record = (
        await session.execute(
            select(FileRecord).where(FileRecord.id == file_id).with_for_update()
        )
    ).scalars().first()
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    links = (
        await session.execute(select(BusinessFile).where(BusinessFile.file_id == file_id))
    ).scalars().all()
    # ① 历史证据：已签署的原件、系统生成的原件、已锁定打样的制作依据，一律不许走通用删除。
    #    否则一次误删就把"签的是哪一版 / 当时按哪份做的"的唯一凭据抹掉了。
    # ② 别处还在引用（合同生成稿、单据生成稿、回款凭证、打样依据）：删文件影响它身上**所有**
    #    引用，所以哪怕只剩一处也算有主，不该从这一头清掉。
    #    这两档的判据统一在 `file.service.inspect_file_usage`（第十一批 11.2）——
    #    原实现只看了 `business_files` 一张表，那五处**没有外键**的专用列一处都没查，
    #    等于给"文件还被引用着"留了四个看不见的缺口。
    usage = await service.inspect_file_usage(session, file_id)
    if usage.protected is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该文件是{usage.protected}，不能删除；确需纠错请走作废等专门流程",
            422,
        )
    if usage.other_holders:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该文件还被「{'、'.join(usage.other_holders)}」引用着，"
            "请先在那处解除关联，不要直接删除原件",
            422,
        )
    # ③ 一个文件挂在多个业务对象上时，删它等于**一次影响全部对象**。
    #    这种情况先让使用者在对应位置解除关联，不要直接清原件。
    if len(links) > 1:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该文件仍被 {len(links)} 个业务对象引用，请先解除不需要的关联，不要直接删除原件",
            422,
        )
    file_name = record.file_name
    object_key = record.object_key
    for link in links:
        await session.delete(link)
    await session.delete(record)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="file",
        business_id=file_id,
        before={"file_name": file_name},
        ip=client_ip(request),
    )
    # **先提交数据库、再删磁盘**。顺序反过来时，磁盘已删而事务失败，
    # 库里会留下"记录还在、文件已不在"的坏数据（下载时才 404）。
    await session.commit()
    # 提交成功后再清原件；清不掉只留一个孤儿文件（无害），
    # 不回滚已经生效的删除——孤儿文件比"库盘脱节"好收拾。
    try:
        storage.delete_object(object_key)
    except Exception:  # noqa: BLE001 —— 清盘失败不该让已成功的删除回滚
        logger.warning("文件 %s 已从库中删除，但磁盘原件清理失败：%s", file_id, object_key, exc_info=True)
    return ok(None, "文件已删除")


@router.get("/business/{business_type}/{business_id}/files")
async def list_business_files(
    business_type: str,
    business_id: int,
    user: CurrentUser = Depends(require_permission("file:view")),
    session: AsyncSession = Depends(get_db),
):
    # 列附件会暴露文件名/上传人等元数据，与下载同一条可见性规则：
    # 业务对象本身不在数据范围内，附件清单也不给看（防按 id 枚举）。
    if not await access.visible_object(
        session, user, business_type=business_type, business_id=business_id
    ):
        raise AppError(ErrorCode.FORBIDDEN, "该业务对象不在你的数据范围内", 403)
    rows = (
        await session.execute(
            select(BusinessFile, FileRecord, User.name)
            .join(FileRecord, FileRecord.id == BusinessFile.file_id)
            .outerjoin(User, User.id == FileRecord.uploaded_by)
            .where(
                BusinessFile.business_type == business_type,
                BusinessFile.business_id == business_id,
            )
            .order_by(BusinessFile.id.desc())
        )
    ).all()
    return ok(
        [
            {
                **serialize_file(record, uploader),
                "business_file_id": link.id,
                "category": link.category,
                "remark": link.remark,
            }
            for link, record, uploader in rows
        ]
    )


@router.post("/business/{business_type}/{business_id}/files")
async def attach_file(
    business_type: str,
    business_id: int,
    request: Request,
    file_id: int = Query(...),
    category: str | None = None,
    user: CurrentUser = Depends(require_permission("file:manage")),
    session: AsyncSession = Depends(get_db),
):
    # **两个方向都要校验**，缺一个就是越权通道：
    # ① 目标业务对象在不在你的数据范围内**且你有该模块的写入权**——否则能给别人的客户
    #    挂附件；"看得见"和"能改"是两件事，挂载就是在改那个对象的内容；
    # ② 源文件你能不能看——否则可以把别人的文件挂到自己的对象上，
    #    再走正常下载路径把它拿走（`can_access_file` 只要有一条可见关联就放行，
    #    所以"挂一条关联"本身就等于授权）。
    if not await visible_object(
        session, user, business_type=business_type, business_id=business_id, write=True
    ):
        raise AppError(
            ErrorCode.DATA_SCOPE_DENIED,
            "不能给该业务对象挂附件：它不在你的数据范围内，或你没有该模块的维护权限",
            403,
        )
    if not await can_access_file(session, user, file_id):
        raise AppError(ErrorCode.DATA_SCOPE_DENIED, "该文件不在你的可见范围内", 403)
    # ③ 打样锁：已制作/寄出后，挂上来的只能是「后续补充资料」。
    #    与上传接口同一条判据，避免"上传被拦、改用挂载绕过去"。
    if business_type == "sample":
        lock = await sample_write_lock_label(session, business_id)
        if lock is not None and (category or "").strip() != SUPPLEMENT_CATEGORY:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"{lock}，不能再往上挂制作依据类的附件。"
                f"事后补进来的资料请把类别选成「{SUPPLEMENT_LABEL}」"
                f"（category={SUPPLEMENT_CATEGORY}）",
                422,
            )
    # 给一个**已存在**的文件新增引用之前，先锁住它的行：与删除入口（通用删除、
    # 回款删凭证）串行化，避免"查引用时还没有、真正删掉后才挂上来"的悬空引用。
    # ⚠️ 用**锁后重读到的那一条**（2026-10-08 复审 11.2）：加锁前那次
    # `session.get` 是快照，identity map 里存着旧对象；拿到锁后继续用它，
    # 就会出现"锁等到了、文件却已经被删掉"。返回 None = 已经没了。
    record = await service.lock_file_row(session, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    link = BusinessFile(
        business_type=business_type,
        business_id=business_id,
        file_id=file_id,
        category=category,
    )
    session.add(link)
    await session.flush()
    # 附件挂载属于业务对象的内容变更，要留痕（谁能给客户/商机加附件）
    await write_audit(
        session,
        operator_id=user.id,
        action="attach",
        business_type=business_type,
        business_id=business_id,
        after={"file_id": file_id, "file_name": record.file_name, "category": category},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"business_file_id": link.id}, "已关联")


@router.delete("/business-files/{business_file_id}")
async def unlink_file(
    business_file_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("file:manage")),
    session: AsyncSession = Depends(get_db),
):
    link = await session.get(BusinessFile, business_file_id)
    if link is None:
        raise AppError(ErrorCode.NOT_FOUND, "关联不存在", 404)
    # 解绑同样是内容变更：不能拆别人对象上的附件。
    # ① `visible_object` 的 business_type / business_id 是**只能按名字传**的参数
    #    （签名里有 `*`），按位置传会直接 TypeError → 接口每次必 500；
    # ② 它返回布尔值，**不判返回值等于没校验**——「能下载某个共享文件」不等于
    #    「能拆掉它挂在别人业务对象上的关联」；
    # ③ 而且"看得见"也不等于"能改"：解绑要该模块的**写入权**（write=True），
    #    否则只有查看权的人能悄悄摘掉别人单据上的凭证关联。
    if not await visible_object(
        session, user,
        business_type=link.business_type, business_id=link.business_id, write=True,
    ):
        raise AppError(
            ErrorCode.DATA_SCOPE_DENIED,
            "不能解绑该附件：它所在的业务对象不在你的数据范围内，或你没有该模块的维护权限",
            403,
        )
    # ③ 已签 / 已生成的原件：**解绑和删除一样能毁掉证据**。拆掉关联后文件不再挂在
    #    任何业务对象上，而 `can_access_file` 对无关联文件只认上传者——其他人（含主管）
    #    从此永久拿不到这份签署原件，台账上"签的是哪一版"就再也对不上了。
    #    "原件不可无痕消失"要同时守住删除**和**解绑两条路径。
    protected = protection_label(link.category)
    if protected is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该关联指向{protected}，不能解绑；确需纠错请走作废等专门流程",
            422,
        )
    # ④ 打样单的制作依据（2026-10-06 补）：**解绑和删除一样能毁掉证据**——
    #    拆掉关联后文件不再挂在任何业务对象上，`can_access_file` 对无关联文件
    #    只认上传者，其他人（含主管）从此拿不到那份图纸，事后对不上账。
    #    已制作/寄出的单子，它的过程附件一律不许解绑。
    if link.business_type == "sample":
        lock = await sample_write_lock_label(session, link.business_id)
        if lock is not None:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"{lock}，不能解绑它的过程附件（可能是制作依据）。"
                f"确需更换资料请开修订版，让新版用新依据、旧版凭证原样留着",
                422,
            )
    before = {
        "business_type": link.business_type,
        "business_id": link.business_id,
        "file_id": link.file_id,
    }
    await session.delete(link)
    await write_audit(
        session,
        operator_id=user.id,
        action="detach",
        business_type=link.business_type,
        business_id=link.business_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已取消关联（文件本身保留）")


__all__ = ["Path"]
