"""文件模块的服务层。

放**跨入口共用**的写操作与判据。可见性/原件保护的**类别表**仍在 `access.py`，
磁盘操作仍在 `storage.py` —— 这里不重复它们，只把它们组织成"删文件之前必须过
的那道闸门"，让所有会动到文件的入口走同一份。
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.file import storage
from app.modules.file.access import file_protection_label, sample_basis_lock_label
from app.modules.file.model import BusinessFile, FileRecord

logger = logging.getLogger("crm.file")


async def discard_unregistered_upload(session: AsyncSession, object_key: str) -> None:
    """上传登记失败后的补偿：把本次刚写上盘、但库里没有登记的那份文件删掉。

    为什么要有它（第十一批 11.8）：通用上传入口一直有这段补偿，而**回款凭证**
    这个专用上传入口漏了 —— 登记失败（约束冲突 / 连接断开 / 审计写入报错）时
    事务回滚、库里没有记录，可盘上那份文件还在。它和"上传成功"的文件长得
    一模一样，事后谁也分不清哪份是垃圾、也不敢删。两个入口共用这一份，
    避免再分叉出第三个各写各的。

    两条纪律（与通用上传原来那段的写法一致，不能省）：
    - 只按**本次的 object_key** 精确删，绝不做"按时间/前缀批量清"；
    - 删之前**先确认库里确实没有这条登记**。少了这一步，就可能把一条
      **已经提交成功**的记录变成"记录在、文件没了"——那比留个孤儿文件更糟。
    """
    try:
        await session.rollback()
        registered = (
            await session.execute(
                select(FileRecord.id).where(FileRecord.object_key == object_key)
            )
        ).first()
        if registered is None:
            storage.delete_object(object_key)
    except Exception:  # noqa: BLE001 —— 清理失败不该盖掉真正的登记错误
        logger.warning("上传登记失败，且临时文件清理失败：%s", object_key, exc_info=True)


async def lock_file_row(session: AsyncSession, file_id: int) -> None:
    """给一份**已经存在**的文件加引用之前，先把它的行锁住。

    为什么需要（第十一批 11.2 第 7 条）：删除那两个入口是"**先锁文件行 → 查引用
    → 删**"。建立引用的一方如果不拿同一把锁，它完全可以插在"查完引用"和"真删"
    中间 —— 结果是一条指向已删文件的悬空引用（合同登记了签署件，文件却没了）。

    哪几处需要它：**给已存在的文件新增引用**的入口（通用挂载、给产品挂附件、
    合同登记签署）。**不需要**的是"新建文件 + 同一事务里立刻关联"那两处 ——
    刚插入的行别的会话根本看不见，没有竞争。
    """
    await session.execute(
        select(FileRecord.id).where(FileRecord.id == file_id).with_for_update()
    )


@dataclass(frozen=True)
class FileUsage:
    """一份文件当前被谁用着。

    分三档，因为三种档位对应三种处理，混在一起就会做错：
    - `protected`：受"原件不可破坏"保护的引用（已签原件 / 生成稿 / 已锁定打样的
      制作依据）——**不许删文件**；
    - `other_holders`：别处还在正常引用（另一条回款的凭证、单据生成稿、打样依据…）
      ——**不许删文件**，但解绑当前这一处是允许的；
    - `business_links`：挂在业务对象上的附件条数。它同时意味着"解绑要解几条"。
    """

    protected: str | None = None
    other_holders: list[str] = field(default_factory=list)
    business_links: int = 0

    @property
    def shared(self) -> bool:
        """除当前这处之外，别处还在用这份文件。"""
        return bool(self.other_holders) or self.business_links > 0


async def inspect_file_usage(
    session: AsyncSession,
    file_id: int,
    *,
    exclude_payment_id: int | None = None,
) -> FileUsage:
    """这份文件还被谁用着（第十一批 11.2）。

    为什么要单独做一个判据：引用文件的列有**五处**，而且**一处外键都没有** ——
    数据库不会替我们守住"文件还被引用着"这件事，全靠应用层查。
    - `business_files.file_id`：通用附件挂载（一对多）；
    - `contract_documents.generated_file_id`：合同生成稿；
    - `biz_docs.file_id`：单据生成稿；
    - `payment_records.voucher_file_id`：回款凭证；
    - `sample_requests.basis_files`（jsonb 快照）：打样的制作依据。

    此前只有**通用删除**入口看了第一处，于是回款凭证那个专用删除入口把一份
    "同时是已签合同扫描件"的文件整个删掉了（文件记录和磁盘文件一起没）——
    已签合同的原件就此消失。判据集中在这里，谁要删文件都得先过它。
    """
    from app.modules.bizdoc.model import BizDoc
    from app.modules.contract.model import ContractDocument
    from app.modules.payment.model import PaymentRecord

    protected = await file_protection_label(session, file_id)
    if protected is None:
        # 打样单已制作/寄出/签收时，那份图纸就是"当时按它做的"的凭证
        protected = await sample_basis_lock_label(session, file_id)

    business_links = (
        await session.execute(
            select(BusinessFile.id).where(BusinessFile.file_id == file_id)
        )
    ).all()

    others: list[str] = []
    if (
        await session.execute(
            select(ContractDocument.id).where(ContractDocument.generated_file_id == file_id)
        )
    ).first():
        others.append("合同的生成稿")
    if (
        await session.execute(select(BizDoc.id).where(BizDoc.file_id == file_id))
    ).first():
        others.append("单据的生成稿")

    payment_stmt = select(PaymentRecord.id).where(PaymentRecord.voucher_file_id == file_id)
    if exclude_payment_id is not None:
        # 调用方是"某条回款在删自己的凭证"时，自己的那条不算"别处引用"
        payment_stmt = payment_stmt.where(PaymentRecord.id != exclude_payment_id)
    if (await session.execute(payment_stmt)).first():
        others.append("另一条回款的凭证")

    # 打样依据只在 jsonb 快照里（`basis_files`），不在 `business_files` 表上，
    # 所以必须单独查一次 —— 这是最容易漏掉的一处。
    basis_hit = (
        await session.execute(
            text(
                "select 1 from sample_requests where basis_files is not null"
                " and exists (select 1 from jsonb_array_elements(basis_files) e"
                "             where (e->>'file_id')::bigint = :fid)"
                " limit 1"
            ),
            {"fid": file_id},
        )
    ).first()
    if basis_hit:
        others.append("打样的制作依据")

    return FileUsage(
        protected=protected, other_holders=others, business_links=len(business_links)
    )
