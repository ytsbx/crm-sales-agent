from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select

from app.modules.recycle.service import _search


def test_search_filters_before_pagination_and_treats_wildcards_literally():
    engine = create_engine('sqlite://')
    table = Table('items', MetaData(), Column('id', Integer, primary_key=True), Column('name', String))
    table.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(table.insert(), [{'id': 1, 'name': '其他'}, {'id': 2, 'name': '合同甲'},
                                      {'id': 3, 'name': '合同乙'}, {'id': 4, 'name': '100%样品'}])
        query = _search(select(table.c.id).order_by(table.c.id), ' 合同 ', table.c.name)
        assert conn.execute(query.limit(1).offset(1)).scalars().all() == [3]
        assert conn.execute(_search(select(table.c.id), '%', table.c.name)).scalars().all() == [4]
        assert len(conn.execute(_search(select(table.c.id), ' ', table.c.name)).all()) == 4
