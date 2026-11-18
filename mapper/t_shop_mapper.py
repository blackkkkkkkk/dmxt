"""积分商城：商品 + 兑换记录，兑换时扣学员积分。

设计取舍（与 t_points_mapper 保持一致）：
- 积分余额不单独存字段，仍是 t_point_record 的 SUM(delta)。兑换成功会写一笔
  负数流水（备注写明商品与数量），所以「积分管理」页的余额、流水、统计卡
  会立刻同步，不需要两边对账，也不会出现商城扣了分而积分页没变。
- 兑换是单事务：校验余额/库存 → 扣库存 → 写扣分流水 → 写兑换记录，
  任一步失败整体回滚，不会出现「扣了分没记到兑换」。
- 兑换记录只追加，不提供改和删；发错货请到「积分管理」补一笔正分并写明原因。
- 商品不会物理删除（已有兑换记录时禁止删除，一律改为「下架」），
  保证历史记录里的商品名/单价快照始终有出处。
- 库存 -1 表示不限量；其余为剩余件数，兑换时按数量扣减。
"""

import logging
from contextlib import contextmanager
from datetime import datetime

from service import dp
from mapper.t_points_mapper import ensure_points_schema

MYSQL_SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS t_goods (
  goods_id INT NOT NULL AUTO_INCREMENT COMMENT '商品主键',
  goods_name VARCHAR(100) NOT NULL COMMENT '商品名称',
  cost INT NOT NULL COMMENT '兑换所需积分（单件）',
  stock INT NOT NULL DEFAULT -1 COMMENT '剩余库存，-1 表示不限量',
  note VARCHAR(255) DEFAULT NULL COMMENT '商品说明',
  state VARCHAR(8) NOT NULL DEFAULT '1' COMMENT '1 上架 / 0 下架',
  create_time VARCHAR(32) NOT NULL,
  PRIMARY KEY (goods_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    """
CREATE TABLE IF NOT EXISTS t_exchange (
  exchange_id INT NOT NULL AUTO_INCREMENT COMMENT '兑换记录主键',
  stu_id VARCHAR(32) NOT NULL,
  stu_name VARCHAR(50) NOT NULL COMMENT '当时姓名快照',
  class_id VARCHAR(32) NOT NULL COMMENT '当时班级快照',
  goods_id INT NOT NULL,
  goods_name VARCHAR(100) NOT NULL COMMENT '商品名快照',
  cost INT NOT NULL COMMENT '单件积分快照',
  qty INT NOT NULL COMMENT '兑换数量',
  total_cost INT NOT NULL COMMENT '本次共扣积分',
  note VARCHAR(255) DEFAULT NULL,
  create_time VARCHAR(32) NOT NULL,
  PRIMARY KEY (exchange_id),
  KEY idx_ex_stu (stu_id),
  KEY idx_ex_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
)

SQLITE_SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS t_goods (
  goods_id INTEGER PRIMARY KEY AUTOINCREMENT,
  goods_name TEXT NOT NULL,
  cost INTEGER NOT NULL,
  stock INTEGER NOT NULL DEFAULT -1,
  note TEXT,
  state TEXT NOT NULL DEFAULT '1',
  create_time TEXT NOT NULL
)
""",
    """
CREATE TABLE IF NOT EXISTS t_exchange (
  exchange_id INTEGER PRIMARY KEY AUTOINCREMENT,
  stu_id TEXT NOT NULL,
  stu_name TEXT NOT NULL,
  class_id TEXT NOT NULL,
  goods_id INTEGER NOT NULL,
  goods_name TEXT NOT NULL,
  cost INTEGER NOT NULL,
  qty INTEGER NOT NULL,
  total_cost INTEGER NOT NULL,
  note TEXT,
  create_time TEXT NOT NULL
)
""",
)

EXCHANGE_LIMIT = 5000        # 一次最多读这么多条兑换记录
MAX_QTY = 99                 # 单次兑换数量上限

_schema_ready = False


def ensure_shop_schema(force=False):
    """建表（幂等）。恢复旧备份后可 force=True 重新补建。"""
    global _schema_ready
    if _schema_ready and not force:
        return True
    if dp.is_sqlite():
        dp.ensure_schema_sqlite()
    # 兑换要写积分流水，积分表必须同时在
    ensure_points_schema(force=force)
    statements = SQLITE_SCHEMA if dp.is_sqlite() else MYSQL_SCHEMA
    conn = dp.get_conn()
    try:
        with conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        conn.commit()
    finally:
        conn.close()
    _schema_ready = True
    logging.info("积分商城表已就绪（t_goods / t_exchange）")
    return True


def _now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _cursor():
    if dp.is_sqlite():
        dp.ensure_schema_sqlite()
    conn = dp.get_conn()
    try:
        cursor = conn.cursor()
        yield cursor, conn
    finally:
        cursor.close()
        conn.close()


def _validate_goods(name, cost, stock, note=""):
    name = str(name or "").strip()
    if not name:
        raise ValueError("商品名称不能为空")
    if len(name) > 50:
        raise ValueError("商品名称最多 50 个字")
    try:
        cost = int(cost)
    except (TypeError, ValueError):
        raise ValueError("所需积分要填整数")
    if cost < 1 or cost > 99999:
        raise ValueError("所需积分要在 1 ~ 99999 之间")
    try:
        stock = int(stock)
    except (TypeError, ValueError):
        raise ValueError("库存要填整数，-1 表示不限量")
    if stock < -1 or stock > 999999:
        raise ValueError("库存要在 -1 ~ 999999 之间")
    note = str(note or "").strip()[:200]
    return name, cost, stock, note


class ShopMapper:
    """商品与兑换记录的读写；所有方法返回普通 dict，界面不碰 SQL。"""

    def __init__(self):
        ensure_shop_schema()

    # ------------------------------------------------------------ 商品
    def add_goods(self, name, cost, stock=-1, note=""):
        name, cost, stock, note = _validate_goods(name, cost, stock, note)
        with _cursor() as (cursor, conn):
            cursor.execute(
                "INSERT INTO t_goods "
                "(goods_name, cost, stock, note, state, create_time) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (name, cost, stock, note, "1", _now_text()))
            goods_id = cursor.lastrowid
            conn.commit()
        return self.get_goods(goods_id)

    def get_goods(self, goods_id):
        with _cursor() as (cursor, _):
            cursor.execute("SELECT * FROM t_goods WHERE goods_id = %s",
                           (goods_id,))
            row = cursor.fetchone()
        return dict(row) if row else None

    def update_goods(self, goods_id, name, cost, stock, note=""):
        name, cost, stock, note = _validate_goods(name, cost, stock, note)
        with _cursor() as (cursor, conn):
            cursor.execute(
                "UPDATE t_goods SET goods_name = %s, cost = %s, stock = %s, "
                "note = %s WHERE goods_id = %s",
                (name, cost, stock, note, goods_id))
            conn.commit()
        return self.get_goods(goods_id)

    def set_goods_state(self, goods_id, state):
        state = "1" if str(state) == "1" else "0"
        with _cursor() as (cursor, conn):
            cursor.execute("UPDATE t_goods SET state = %s WHERE goods_id = %s",
                           (state, goods_id))
            conn.commit()
        return self.get_goods(goods_id)

    def delete_goods(self, goods_id):
        """有兑换记录的商品不能删（历史要对得上），只能下架。"""
        with _cursor() as (cursor, conn):
            cursor.execute(
                "SELECT COUNT(*) AS n FROM t_exchange WHERE goods_id = %s",
                (goods_id,))
            used = int((cursor.fetchone() or {}).get("n") or 0)
            if used:
                raise ValueError("这个商品已经有 %d 条兑换记录，请改用「下架」" % used)
            cursor.execute("DELETE FROM t_goods WHERE goods_id = %s", (goods_id,))
            conn.commit()
        return True

    def list_goods(self, include_off=False):
        """上架商品在前，同状态按创建顺序；下架商品默认不显示。"""
        with _cursor() as (cursor, _):
            cursor.execute("SELECT * FROM t_goods ORDER BY state DESC, goods_id ASC")
            rows = cursor.fetchall()
        out = [dict(r) for r in rows]
        if not include_off:
            out = [r for r in out if str(r["state"]) == "1"]
        return out

    # ------------------------------------------------------------ 兑换
    def balance_of(self, stu_id):
        with _cursor() as (cursor, _):
            cursor.execute(
                "SELECT COALESCE(SUM(delta), 0) AS bal FROM t_point_record "
                "WHERE stu_id = %s", (str(stu_id),))
            row = cursor.fetchone()
        return int((row or {}).get("bal") or 0)

    def exchange(self, stu_id, stu_name, class_id, goods_id, qty=1, note=""):
        """兑换：扣库存 + 写一笔负积分流水 + 写兑换记录，全部同一事务。

        积分不够、库存不足、商品下架、数量非法时抛 ValueError，且不写任何数据。
        兑换不允许透支（兑换后余额必须 >= 0）。
        """
        stu_id = str(stu_id or "").strip()
        if not stu_id:
            raise ValueError("没有选中学生")
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            raise ValueError("兑换数量要填整数")
        if qty < 1 or qty > MAX_QTY:
            raise ValueError("单次兑换数量要在 1 ~ %d 之间" % MAX_QTY)
        stu_name = str(stu_name or "").strip() or "未命名"
        class_id = str(class_id or "").strip()
        note = str(note or "").strip()[:200]
        goods_id = int(goods_id)
        created = _now_text()

        with _cursor() as (cursor, conn):
            try:
                cursor.execute("SELECT * FROM t_goods WHERE goods_id = %s",
                               (goods_id,))
                goods = cursor.fetchone()
                if not goods:
                    raise ValueError("商品不存在，请刷新后重试")
                if str(goods["state"]) != "1":
                    raise ValueError("这个商品已下架")
                cost = int(goods["cost"])
                stock = int(goods["stock"])
                if 0 <= stock < qty:
                    raise ValueError("库存不足，目前只剩 %d 件" % stock)
                total = cost * qty

                cursor.execute(
                    "SELECT COALESCE(SUM(delta), 0) AS bal FROM t_point_record "
                    "WHERE stu_id = %s", (stu_id,))
                bal = int((cursor.fetchone() or {}).get("bal") or 0)
                if bal < total:
                    raise ValueError("积分不足：%s 当前 %d 分，本次需要 %d 分"
                                     % (stu_name, bal, total))

                point_note = "积分兑换：%s ×%d" % (goods["goods_name"], qty)
                cursor.execute(
                    "INSERT INTO t_point_record "
                    "(stu_id, stu_name, class_id, delta, balance_after, "
                    "note, create_time) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (stu_id, stu_name, class_id, -total, bal - total,
                     point_note, created))
                if stock >= 0:
                    cursor.execute(
                        "UPDATE t_goods SET stock = stock - %s "
                        "WHERE goods_id = %s", (qty, goods_id))
                cursor.execute(
                    "INSERT INTO t_exchange "
                    "(stu_id, stu_name, class_id, goods_id, goods_name, "
                    "cost, qty, total_cost, note, create_time) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (stu_id, stu_name, class_id, goods_id, goods["goods_name"],
                     cost, qty, total, note, created))
                exchange_id = cursor.lastrowid
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "exchange_id": exchange_id,
            "stu_id": stu_id,
            "stu_name": stu_name,
            "class_id": class_id,
            "goods_id": goods_id,
            "goods_name": goods["goods_name"],
            "cost": cost,
            "qty": qty,
            "total_cost": total,
            "balance_after": bal - total,
            "note": note,
            "create_time": created,
        }

    # ------------------------------------------------------------ 记录
    def query(self, class_id=None, stu_id=None, keyword=None,
              start=None, end=None, limit=EXCHANGE_LIMIT):
        """兑换记录，倒序（新的在前）；全部条件可选。"""
        sql = ("SELECT exchange_id, stu_id, stu_name, class_id, goods_id, "
               "goods_name, cost, qty, total_cost, note, create_time "
               "FROM t_exchange WHERE 1 = 1")
        params = []
        if class_id:
            sql += " AND class_id = %s"
            params.append(str(class_id))
        if stu_id:
            sql += " AND stu_id = %s"
            params.append(str(stu_id))
        if keyword:
            sql += " AND (stu_name LIKE %s OR goods_name LIKE %s)"
            like = "%%%s%%" % str(keyword).strip()
            params.extend((like, like))
        if start:
            sql += " AND DATE(create_time) >= %s"
            params.append(str(start)[:10])
        if end:
            sql += " AND DATE(create_time) <= %s"
            params.append(str(end)[:10])
        sql += " ORDER BY exchange_id DESC LIMIT %s"
        params.append(int(limit))
        with _cursor() as (cursor, _):
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def totals(self):
        """统计卡用：在售商品数、兑换笔数、累计消耗积分、参与兑换人数。"""
        with _cursor() as (cursor, _):
            cursor.execute(
                "SELECT COUNT(*) AS n FROM t_goods WHERE state = %s", ("1",))
            goods_count = int((cursor.fetchone() or {}).get("n") or 0)
            cursor.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(total_cost), 0) AS cost, "
                "COUNT(DISTINCT stu_id) AS people FROM t_exchange")
            row = cursor.fetchone() or {}
        return {
            "goods": goods_count,
            "exchanges": int(row.get("n") or 0),
            "cost": int(row.get("cost") or 0),
            "people": int(row.get("people") or 0),
        }
