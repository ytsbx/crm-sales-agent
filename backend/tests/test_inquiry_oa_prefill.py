"""询价提交 OA 前，CRM 已有资料要按同序模板字段带过去（不再让人重填一遍）。"""
import asyncio
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

from app.modules.oa import inquiry as oa


def _session(customer=None, contact=None):
    from app.modules.customer.model import Contact, Customer

    async def get(model, pk):
        if model is Customer:
            return customer
        if model is Contact:
            return contact
        return None

    return Obj(get=AsyncMock(side_effect=get))


def test_prefill_composes_customer_contact_and_reuses_crm_fields():
    customer = Obj(name='宏远包装', deleted_at=None)
    contact = Obj(name='张三', mobile='13800000000', phone=None, deleted_at=None)
    inquiry = Obj(customer_id=1, contact_id=2, inquiry_no='XQ-20261123-0001',
                  title='定制帆布袋', remark='客户要求周五前回复')
    data = asyncio.run(oa.submission_prefill(_session(customer, contact), inquiry))
    assert data == {
        'customer_contact': '宏远包装、张三、13800000000',
        'product_reference': 'XQ-20261123-0001',
        'special_note': '客户要求周五前回复',
    }


def test_prefill_falls_back_to_title_and_skips_deleted_contact():
    customer = Obj(name='宏远包装', deleted_at=None)
    contact = Obj(name='张三', mobile='13800000000', phone=None, deleted_at='2026-01-01')
    inquiry = Obj(customer_id=1, contact_id=2, inquiry_no=None, title='定制帆布袋', remark=None)
    data = asyncio.run(oa.submission_prefill(_session(customer, contact), inquiry))
    assert data == {
        'customer_contact': '宏远包装',
        'product_reference': '定制帆布袋',
    }


def test_prefill_without_customer_only_keeps_product_reference():
    inquiry = Obj(customer_id=None, contact_id=None, inquiry_no='XQ-1', title='定制袋', remark='')
    data = asyncio.run(oa.submission_prefill(_session(), inquiry))
    assert data == {'product_reference': 'XQ-1'}


def test_prefill_uses_phone_when_mobile_is_empty():
    customer = Obj(name='宏远包装', deleted_at=None)
    contact = Obj(name='张三', mobile=None, phone='010-88886666', deleted_at=None)
    inquiry = Obj(customer_id=1, contact_id=2, inquiry_no='XQ-1', title='定制袋', remark=None)
    data = asyncio.run(oa.submission_prefill(_session(customer, contact), inquiry))
    assert data['customer_contact'] == '宏远包装、张三、010-88886666'


def test_prefill_falls_back_to_primary_contact_when_inquiry_has_none():
    from app.modules.customer.model import Customer

    customer = Obj(id=1, name='宏远包装', deleted_at=None)
    primary = Obj(name='李四', mobile=None, phone='0571-88886666', deleted_at=None)

    class Result:
        def scalars(self):
            return self

        def first(self):
            return primary

    async def get(model, pk):
        return customer if model is Customer else None

    session = Obj(get=AsyncMock(side_effect=get), execute=AsyncMock(return_value=Result()))
    inquiry = Obj(customer_id=1, contact_id=None, inquiry_no='XQ-1', title='定制袋', remark=None)
    data = asyncio.run(oa.submission_prefill(session, inquiry))
    assert data['customer_contact'] == '宏远包装、李四、0571-88886666'


def test_unavailable_oa_still_shows_the_same_sales_fields(monkeypatch):
    """同序读不到时，新建/编辑表单也要保留同序销售段的字段与顺序。"""
    from app.core.config import settings
    from app.modules.dingtalk import router as routes
    from app.modules.inquiry import service as inquiries
    from app.modules.oa import client as oa_client

    monkeypatch.setattr(settings, 'oa_inquiry_submission_enabled', True, raising=False)
    monkeypatch.setattr(settings, 'oa_inquiry_template_id', '', raising=False)
    monkeypatch.setattr(settings, 'oa_inquiry_template_code', oa.NATIVE_INQUIRY_CODE, raising=False)
    monkeypatch.setattr(oa_client, 'configured', lambda: False)
    inquiry = Obj(id=7, customer_id=None, contact_id=None, inquiry_no='XQ-1',
                  title='定制帆布袋', remark=None, oa_quote_user_id=None)
    monkeypatch.setattr(inquiries, 'get_visible_or_404', AsyncMock(return_value=inquiry))
    session = Obj(get=AsyncMock(return_value=Obj(name='提报同事', department_id=None)))
    data = asyncio.run(routes.inquiry_submission_options(7, user=Obj(id=1), session=session))['data']
    assert data['configured'] is False
    assert data['prefill']['applicant_name'] == '提报同事'
    assert [field['id'] for field in data['template_fields']][:4] == [
        'reporter', 'product_name', 'report_department', 'quoter',
    ]
    assert {field['id'] for field in data['fields']} >= {
        'quote_scope', 'reply_date', 'provide_sample', 'customer_contact',
        'product_reference', 'product_model', 'delivery_date', 'special_note', 'attachments',
    }


def test_submission_options_exposes_prefill_for_native_inquiry(monkeypatch):
    """接口层：同序原生询价模板的提交选项要带上 CRM 已有的三项。"""
    from app.core.config import settings
    from app.modules.customer.model import Contact, Customer
    from app.modules.dingtalk import router as routes
    from app.modules.inquiry import service as inquiries
    from app.modules.oa import client as oa_client
    from app.modules.user.model import User

    monkeypatch.setattr(settings, 'oa_inquiry_submission_enabled', True, raising=False)
    monkeypatch.setattr(settings, 'oa_inquiry_template_id', '', raising=False)
    monkeypatch.setattr(settings, 'oa_inquiry_template_code', oa.NATIVE_INQUIRY_CODE, raising=False)
    monkeypatch.setattr(oa_client, 'configured', lambda: True)
    template = {
        'id': 'tpl', 'code': oa.NATIVE_INQUIRY_CODE, 'version': 1,
        'applicant': {'id': 'person-1', 'name': '提报同事', 'department_id': 'dept-1', 'department_name': '销售部'},
        'people': [{'id': 'q-1', 'name': '报价员甲'}],
        'fields': [
            {'id': 'quoter', 'type': 'user', 'label': '对接报价员', 'required': True, 'user_scope': ['q-1']},
            {'id': 'reporter', 'type': 'user', 'label': '提报人', 'required': True},
            {'id': 'customer', 'type': 'text', 'label': '客户名称、联系人、联系方式', 'required': True},
            {'id': 'product_reference', 'type': 'text', 'label': '产品名称/编号', 'required': True},
            {'id': 'notes', 'type': 'text', 'label': '特别说明', 'required': False},
            {'id': 'reference_images', 'type': 'attachment', 'label': '产品参考图片', 'required': False},
        ],
    }
    monkeypatch.setattr(oa_client, 'list_templates', AsyncMock(return_value=[template]))
    inquiry = Obj(id=7, customer_id=1, contact_id=2, inquiry_no='XQ-20261123-0001',
                  title='定制帆布袋', remark='客户要求周五前回复', oa_quote_user_id=None)
    monkeypatch.setattr(inquiries, 'get_visible_or_404', AsyncMock(return_value=inquiry))
    customer = Obj(name='宏远包装', deleted_at=None)
    contact = Obj(name='张三', mobile='13800000000', phone=None, deleted_at=None)
    account = Obj(id=1, dingtalk_userid='bound-id')

    async def get(model, pk):
        return {User: account, Customer: customer, Contact: contact}.get(model)

    session = Obj(get=AsyncMock(side_effect=get))
    user = Obj(id=1, name='提报同事')
    data = asyncio.run(routes.inquiry_submission_options(7, user=user, session=session))['data']
    assert data['prefill_fields'] == {
        'customer_contact': '宏远包装、张三、13800000000',
        'product_reference': 'XQ-20261123-0001',
        'special_note': '客户要求周五前回复',
    }
    ids = {field['id'] for field in data['fields']}
    assert {'customer_contact', 'product_reference', 'special_note'} <= ids
