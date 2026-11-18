# mapper/base_mapper.py
from contextlib import contextmanager

from service.dp import get_conn, ensure_schema_sqlite, is_sqlite


class BaseMapper:
    """
    所有 Mapper 的公共父类。
    """

    @contextmanager
    def _cursor(self):
        # SQLite 引擎下首次访问时确保常规三张表已建好（幂等）。
        if is_sqlite():
            ensure_schema_sqlite()
        conn = get_conn()
        try:
            cursor = conn.cursor()
            yield cursor, conn
        finally:
            cursor.close()
            conn.close()

    def _fetch_one(self, cursor, cls):
        row = cursor.fetchone()
        return cls(**row) if row else None

    def _fetch_all(self, cursor, cls):
        return [cls(**row) for row in cursor.fetchall()]
