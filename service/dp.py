# service/dp.py
"""数据库连接层。

默认使用 SQLite（零依赖、装完即用、单文件 .db），可选切回 MySQL。

设计目标：
- 程序装完就能用，不再要求用户额外安装 MySQL 服务；
- 对外接口保持不变（get_conn / BaseMapper._cursor），所有 mapper 无需改动；
- 在 SQLite 引擎下自动把 mapper 里的 MySQL 方言 SQL 翻译成 SQLite 能执行的写法，
  并把 %s 占位符换成 ?，同时让 cursor 按字典返回、支持 lastrowid / rowcount /
  executemany，上层完全无感。

引擎选择：
- 默认 SQLite，数据库文件放在「程序所在目录」下，名为 dmxt.db；
- 若程序目录下存在 db_config.json 且其中 engine == "mysql"，则走 MySQL（兼容旧部署）；
- 环境变量 DMXT_DB_ENGINE=mysql 也能强制切到 MySQL。
"""

import json
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime


def app_dir():
    """程序所在目录：源码运行取项目根，PyInstaller 打包后取 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    """用户数据目录：数据库、日志、配置、导出文件都放这里。

    - 打包后（标准安装）默认放 %APPDATA%\\全童全星上课点名系统，这样即使程序装在
      Program Files（只读）也能正常读写数据，卸载也不会丢数据；
    - 源码运行放项目根，方便开发调试；
    - 可用环境变量 DMXT_DATA_DIR 覆盖（便携部署 / 测试用）。
    """
    override = os.environ.get("DMXT_DATA_DIR")
    if override:
        return override
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
            d = os.path.join(base, "全童全星上课点名系统")
        else:
            d = os.path.join(os.path.expanduser("~"), ".dmxt")
        os.makedirs(d, exist_ok=True)
        return d
    return app_dir()


def config_path():
    return os.path.join(data_dir(), "db_config.json")


def db_path():
    """SQLite 数据库文件路径。

    可用环境变量 DMXT_DB_PATH 覆盖（测试/便携部署用），默认放用户数据目录下。
    """
    override = os.environ.get("DMXT_DB_PATH")
    if override:
        return override
    return os.path.join(data_dir(), "dmxt.db")


# MySQL 默认连接参数（仅当 engine=mysql 时使用）
MYSQL_DEFAULTS = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "123456",
    "database": "dmxt",
    "charset": "utf8mb4",
    "maxconnections": 10,
}

MYSQL_ENV_KEYS = {
    "host": "DMXT_DB_HOST",
    "port": "DMXT_DB_PORT",
    "user": "DMXT_DB_USER",
    "password": "DMXT_DB_PASSWORD",
    "database": "DMXT_DB_DATABASE",
    "charset": "DMXT_DB_CHARSET",
    "maxconnections": "DMXT_DB_MAXCONNECTIONS",
}


def load_config():
    """返回 {'engine': 'sqlite'|'mysql', ...}。

    优先级：环境变量 > db_config.json > 默认。默认 engine 是 sqlite。
    """
    cfg = {"engine": "sqlite"}
    cfg.update(MYSQL_DEFAULTS)

    path = config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in ("engine", "host", "port", "user", "password",
                          "database", "charset", "maxconnections"):
                    if k in data:
                        cfg[k] = data[k]
        except Exception as exc:  # noqa: BLE001
            print("[dmxt] 读取 %s 失败，改用默认配置：%s" % (path, exc),
                  file=sys.stderr)

    if os.environ.get("DMXT_DB_ENGINE"):
        cfg["engine"] = os.environ["DMXT_DB_ENGINE"]

    for key, env in MYSQL_ENV_KEYS.items():
        value = os.environ.get(env)
        if value not in (None, ""):
            cfg[key] = value

    try:
        cfg["port"] = int(cfg["port"])
        cfg["maxconnections"] = int(cfg["maxconnections"])
    except (TypeError, ValueError):
        cfg["port"] = MYSQL_DEFAULTS["port"]
        cfg["maxconnections"] = MYSQL_DEFAULTS["maxconnections"]

    return cfg


def is_sqlite():
    return load_config()["engine"] != "mysql"


# ---------------------------------------------------------------------------
# SQL 方言翻译：把 mapper 里的 MySQL 语法翻成 SQLite 能执行的写法。
# ---------------------------------------------------------------------------

# 需要翻译的 MySQL 关键字/函数（按「先长后短」顺序，避免 DATE_FORMAT 被 DATE 先吃）
_DIALECT_RULES = [
    # (正则, 替换, 说明)
    # upsert：MySQL 的 ON DUPLICATE KEY UPDATE col=VALUES(col) 翻译成
    # SQLite 的 ON CONFLICT(...) DO UPDATE SET col=excluded.col。冲突列取
    # VALUES(...) 里第一个被引用的列（这里都是主键/唯一键），简单起见用
    # excluded 引用，冲突目标省略列名（SQLite 允许不带目标，命中任意唯一约束）。
    (r"ON\s+DUPLICATE\s+KEY\s+UPDATE", "ON CONFLICT DO UPDATE SET", "upsert 关键字"),
    (r"\bVALUES\s*\(\s*([A-Za-z0-9_]+)\s*\)", r"excluded.\1", "VALUES(col) → excluded.col"),
    (r"\bDATE_FORMAT\s*\(\s*([^,]+?)\s*,\s*'%+Y-%+m-%+d'\s*\)",
     r"substr(COALESCE(\1, ''), 1, 10)", "DATE_FORMAT Y-m-d"),
    (r"\bAUTO_INCREMENT\b", "AUTOINCREMENT", "自增"),
    (r"\bCURDATE\s*\(\s*\)", "date('now','localtime')", "今天"),
    (r"\bNOW\s*\(\s*\)", "datetime('now','localtime')", "当前时刻"),
    (r"\bDATE\s*\(", "date(", "date() 函数"),
    (r"\bCAST\s*\(\s*([A-Za-z0-9_.]+)\s+AS\s+CHAR\s*\)", r"CAST(\1 AS TEXT)", "CAST AS CHAR"),
    (r"\bCOALESCE\s*\(", "coalesce(", "COALESCE 同名函数"),
    (r"\bNULLIF\s*\(", "nullif(", "NULLIF"),
    (r"`", "", "反引号"),
    (r"\bENGINE\s*=\s*\w+", "", "建表 ENGINE"),
    (r"\bDEFAULT\s+CHARSET\s*=\s*\w+", "", "建表 CHARSET"),
    (r"\bCHARACTER\s+SET\s+\w+", "", "建表 CHARACTER SET"),
    (r"\bCOLLATE\s+[\w.]+", "", "建表 COLLATE"),
    (r"\bUSING\s+BTREE\b", "", "索引 USING BTREE"),
    (r"\bROW_FORMAT\s*=\s*\w+", "", "建表 ROW_FORMAT"),
    (r"\bON\s+UPDATE\s+CURRENT_TIMESTAMP\b", "", "ON UPDATE CURRENT_TIMESTAMP"),
    (r"COMMENT\s+'[^']*'", "", "字段 COMMENT"),
    (r"\bUNSIGNED\b", "", "无符号"),
]

# 表内二级索引 / UNIQUE KEY / PRIMARY KEY（含 USING BTREE）需要拆出或删除。
_SECONDARY_KEY_RE = re.compile(
    r",\s*KEY\s+idx_\w+\s*\([^)]*\)", re.IGNORECASE)
_UNIQUE_KEY_RE = re.compile(
    r",\s*UNIQUE\s+KEY\s+\w+\s*\([^)]*\)", re.IGNORECASE)
_PRIMARY_KEY_RE = re.compile(
    r",\s*PRIMARY\s+KEY\s*\([^)]*\)\s*(?:USING\s+\w+)?", re.IGNORECASE)


def _placeholders(sql):
    """把 MySQL 的 %s 占位符替换成 SQLite 的 ?。

    只替换「独立的 %s」：前面不是 %、后面紧跟非单词字符（避免 LIKE '%xxx%' 和
    '%%Y-%%m-%%d' 这类字面量被误伤）。mapper 里 LIKE 的值是运行时拼进参数的
    （params 里带 %），SQL 文本里只有 %s 占位符本身，所以这里是安全的。
    """
    return re.sub(r"(?<!%)%s(?![\w%])", "?", sql)


def _clean_create(sql):
    """清理 CREATE TABLE 里的 MySQL 特有子句，使其能被 SQLite 接受。

    表级 KEY/UNIQUE KEY 定义 SQLite 不支持，删除（这个小体量应用没有它们
    也不影响功能正确性）；PRIMARY KEY 里若有 USING BTREE 一并删掉该修饰，
    但保留 PRIMARY KEY 本身（SQLite 支持表级 PRIMARY KEY）。
    """
    s = _SECONDARY_KEY_RE.sub("", sql)
    s = _UNIQUE_KEY_RE.sub("", sql)
    s = re.sub(r"\bUSING\s+BTREE\b", "", s, flags=re.IGNORECASE)
    return s


def translate_sql(sql):
    """把一段 MySQL SQL 翻译成 SQLite SQL。幂等：翻译过的再翻译仍稳定。"""
    s = sql
    for pattern, repl, _note in _DIALECT_RULES:
        s = re.sub(pattern, repl, s, flags=re.IGNORECASE)
    if re.search(r"\bCREATE\s+TABLE\b", s, re.IGNORECASE):
        s = _clean_create(s)
    s = _placeholders(s)
    return s


# ---------------------------------------------------------------------------
# SQLite 连接与字典游标
# ---------------------------------------------------------------------------

def _sqlite_connect(path):
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# 这些列在 MySQL 里是 datetime/timestamp，读回来是 datetime 对象；
# SQLite 里存成 TEXT，读回来是字符串。上层有 `.strftime()` 之类调用，
# 为了无感，在这里统一把这几列从字符串转回 datetime。
_DATETIME_COLUMNS = {"create_time", "update_time"}


def _dict_factory(cursor, row):
    """把行转成 dict（列名 → 值），与 PyMySQL 的 DictCursor 行为一致。

    额外把 create_time / update_time 的字符串值还原成 datetime，
    保证上层 .strftime() 等调用在 SQLite 下与 MySQL 下行为一致。
    """
    d = {}
    for idx, col in enumerate(cursor.description):
        name = col[0]
        value = row[idx]
        if name in _DATETIME_COLUMNS and isinstance(value, str):
            value = _parse_datetime(value)
        d[name] = value
    return d


def _parse_datetime(text):
    """把 'YYYY-MM-DD HH:MM:SS' 还原成 datetime；解析不了就原样返回字符串。"""
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return text


class _DictConnection:
    """包装 sqlite3.Connection，补齐 mapper 用到的接口。"""

    def __init__(self, raw):
        self._raw = raw

    def cursor(self):
        return _DictCursor(self._raw.cursor())

    def commit(self):
        return self._raw.commit()

    def rollback(self):
        return self._raw.rollback()

    def close(self):
        try:
            self._raw.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _DictCursor:
    """包装 sqlite3.Cursor，让 fetchone()/fetchall() 返回 dict，补 lastrowid 等。"""

    def __init__(self, raw):
        self._raw = raw
        self.lastrowid = None
        self.rowcount = -1

    def execute(self, sql, params=None):
        sql = translate_sql(sql)
        if params is None:
            self._raw.execute(sql)
        else:
            if isinstance(params, (list, tuple)):
                self._raw.execute(sql, tuple(params))
            else:
                self._raw.execute(sql, params)
        self.lastrowid = self._raw.lastrowid
        self.rowcount = self._raw.rowcount
        return self

    def executemany(self, sql, seq_of_params):
        sql = translate_sql(sql)
        self._raw.executemany(sql, seq_of_params)
        self.rowcount = self._raw.rowcount
        return self

    def fetchone(self):
        return self._raw.fetchone()

    def fetchall(self):
        return self._raw.fetchall()

    def close(self):
        try:
            self._raw.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __iter__(self):
        return iter(self._raw)


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

_pool = None
_pool_lock = threading.Lock()


def get_pool():
    """返回连接池（MySQL 时是 PooledDB；SQLite 时是轻量单例句柄）。"""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        cfg = load_config()
        if cfg["engine"] == "mysql":
            import pymysql
            from dbutils.pooled_db import PooledDB
            _pool = PooledDB(
                creator=pymysql,
                maxconnections=cfg["maxconnections"],
                host=cfg["host"],
                port=cfg["port"],
                user=cfg["user"],
                password=cfg["password"],
                database=cfg["database"],
                charset=cfg["charset"],
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=5,
            )
        else:
            _pool = _SQLitePool(db_path())
    return _pool


class _SQLitePool:
    """轻量「连接池」：SQLite 每次 get_conn 返回一个新的包装连接即可。"""

    def __init__(self, path):
        self.path = path

    def connection(self):
        return _DictConnection(_sqlite_connect(self.path))


def get_conn():
    return get_pool().connection()


def ensure_schema_sqlite():
    """首次使用 SQLite 时，确保常规三张表存在（假期表由 t_season_mapper 负责）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS t_class (
                  class_id TEXT NOT NULL PRIMARY KEY,
                  class_type TEXT,
                  class_time TEXT,
                  class_name TEXT,
                  class_hour REAL,
                  class_val INTEGER,
                  class_day TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS t_stu (
                  stu_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  stu_name TEXT,
                  stu_class_id TEXT,
                  stu_hour REAL,
                  phone TEXT,
                  stu_state TEXT NOT NULL DEFAULT '1'
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS t_sign (
                  dm_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  stu_name TEXT,
                  stu_class_id TEXT,
                  sign_hour TEXT,
                  sign_state TEXT,
                  sign_time TEXT,
                  bz TEXT,
                  update_time TEXT,
                  create_time TEXT
                )
            """)
        conn.commit()
    finally:
        conn.close()


def now_text():
    """当前本地时间字符串，SQLite 里 create_time/update_time 存 TEXT 用。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
