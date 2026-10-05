"""默认下单模板里无法解析的占位符被原样印到对外 PDF。

Revision ID: b6e2a8c4d1f7
Revises: e2a6c0d4f8b3
Create Date: 2026-10-05

`biz_doc_templates` 的默认下单模板正文写着 `{{order.payment_terms}}`，但生成时的
sources 只提供 customer / extra（订单草稿路径连正式订单都没有）。`_fill_tokens`
对认不出的 token 是**原样保留**的（"宁可让人看见这里没填上，也不要静默给个空值"），
于是每一份下单文件、订单草稿需求单的对外 PDF 上都会印出字面量
`{{order.payment_terms}}`，而界面上没有任何提示。

代码侧已改：默认模板不再引用 order.*，并在有正式订单时补上 order 来源。
但 `ensure_default_templates` 只在"该类型一条都没有"时才建模板，**不会覆盖已存在的行**，
所以库里存量模板还会继续印那串语法——这份迁移把它们一并修掉。

只动「名字仍是默认名」或「备注写明是默认模板」且正文确实含该 token 的行；
业务方自建、改过名的模板不碰（那种模板里保留 order.* 是有意义的：有正式订单时
现在会真的取到订单字段）。

downgrade 故意为空：这是数据订正，不是结构变更；把 token 写回去等于重新引入
"对外文件印模板语法"的缺陷。
"""

from alembic import op

revision = "b6e2a8c4d1f7"
down_revision = "e2a6c0d4f8b3"
branch_labels = None
depends_on = None

#: 不能解析的 token 与替代文案。替代文案与 service._DEFAULT_BODY 里的新正文保持一致，
#: 这样"库里的默认模板"和"新装的默认模板"逐字相同。
_OLD_TOKEN = "{{order.payment_terms}}"
_NEW_TEXT = "以本文件所列付款条件栏为准"


def upgrade() -> None:
    op.execute(
        "UPDATE biz_doc_templates "
        f"SET body = replace(body, '{_OLD_TOKEN}', '{_NEW_TEXT}') "
        "WHERE doc_type = 'order_sheet' "
        f"  AND body LIKE '%{_OLD_TOKEN}%' "
        "  AND (name = '默认模板（待替换为正式模板）' OR remark LIKE '%默认模板%')"
    )


def downgrade() -> None:
    # 数据订正，不回滚（回滚即重新引入"对外文件印出模板语法"的缺陷）。
    pass
