"""询价模型应能在隔离数据库建表，并保留既有状态索引。"""
from sqlalchemy import create_engine, inspect

from app.modules.inquiry.model import CustomInquiry


def test_inquiry_table_can_be_created_with_one_status_index():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    try:
        CustomInquiry.__table__.create(engine)
        indexes = inspect(engine).get_indexes('custom_inquiries')
        status = [index for index in indexes if index['name'] == 'ix_custom_inquiries_status']
        assert len(status) == 1
        assert status[0]['column_names'] == ['status']
        assert not status[0]['unique']
        assert any(index['column_names'] == ['customer_id'] for index in indexes)
    finally:
        engine.dispose()
