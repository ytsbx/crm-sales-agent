"""打样：制作依据快照（2026-10-06，第一批返修遗留项）。

起因：`sample_items.drawing_version` 只是**一串自由文字**（如 "DWG-2026-A3"），
它和任何文件之间没有任何关联。而通用附件接口只判断"能不能看见这张打样单"，
不管这张单是否已经制作/寄出——于是一张已经做出来的打样单，
图纸仍能从 `DELETE /business-files/{id}` 解绑、或直接把文件删掉，
事后谁也证明不了"当时按哪份图纸做的"。

本次两件事：

1. `sample_requests.basis_files`：登记制作完成时**显式指定**本次实际采用的附件，
   存下文件 id / 文件名 / sha256 / 大小 / 当时的挂载类别。
   只记文件名不够——文件可以被换掉，必须是能自证的校验值。
2. 附件写入锁（见 `app/modules/file/access.py` 的 `sample_write_lock_label`）：
   已制作 / 已寄出 / 已签收之后，该打样单的已有附件**不可解绑、不可删除**；
   新增附件只能显式标为「后续补充资料」（`category=supplement`）——
   制作依据与事后补进来的资料要能分得开，不能一律禁掉，
   否则验收报告、整改说明这类正常补料也补不进去。

Revision ID: d4f8b2c6a1e9
Revises: c9d3e7b1f5a4
"""

from alembic import op

revision = "d4f8b2c6a1e9"
down_revision = "c9d3e7b1f5a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 可空：老数据没有制作依据快照。**不回填、不猜**——那些单子当时到底按哪份
    # 文件做的，今天已经无从查证；补一个假的上去比空着更坏。
    op.execute(
        "ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS basis_files JSONB"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sample_requests DROP COLUMN IF EXISTS basis_files")
