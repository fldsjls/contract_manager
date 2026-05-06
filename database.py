# sqlite3 是 Python 内置的 SQLite 数据库模块。
import sqlite3
# contextmanager 用来把数据库连接封装成 with 语句可用的上下文管理器。
from contextlib import contextmanager
# date/datetime/timedelta 用于处理合同日期和到期时间计算。
from datetime import date, datetime, timedelta
# Path 用于跨平台处理数据库文件路径。
from pathlib import Path
# 类型注解，帮助说明函数参数和返回值。
from typing import Iterable, Iterator, Optional

# Contract 是合同数据模型，now_text 用于生成当前时间文本。
from models import Contract, now_text


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "contracts.db"

# 合同状态统一集中定义，避免界面和数据库层使用不同文本。
ACTIVE_STATUS = "进行中"
EXPIRING_STATUS = "即将到期"
EXPIRED_STATUS = "已到期"


# 将 yyyy-MM-dd 文本转换为 date；无法解析时返回 None。
def parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# 根据截止日期判断合同状态：已到期、即将到期或进行中。
def calculate_status(end_date: str) -> str:
    parsed = parse_date(end_date)
    if parsed is None:
        return ACTIVE_STATUS

    today = date.today()
    if parsed < today:
        return EXPIRED_STATUS
    if parsed <= today + timedelta(days=30):
        return EXPIRING_STATUS
    return ACTIVE_STATUS


class Database:
    # 初始化数据库管理对象，并确保数据库文件和表结构准备好。
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    # 首次运行时自动创建合同表和搜索索引。
    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS contracts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contract_name TEXT NOT NULL,
                    contract_number TEXT NOT NULL,
                    party_name TEXT NOT NULL,
                    amount REAL NOT NULL DEFAULT 0,
                    sign_date TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    status TEXT NOT NULL DEFAULT '进行中',
                    file_path TEXT,
                    remark TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_contracts_search "
                "ON contracts(contract_name, contract_number, party_name)"
            )
            self.migrate_schema(conn)

    # 创建数据库连接，并在操作结束后自动提交和关闭。
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # 兼容早期数据库结构；已有正确结构时直接跳过。
    def migrate_schema(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'contracts'"
        ).fetchone()
        if not row or "DEFAULT '进行中'" in (row["sql"] or ""):
            return

        conn.execute("ALTER TABLE contracts RENAME TO contracts_old")
        conn.execute(
            """
            CREATE TABLE contracts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_name TEXT NOT NULL,
                contract_number TEXT NOT NULL,
                party_name TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                sign_date TEXT,
                start_date TEXT,
                end_date TEXT,
                status TEXT NOT NULL DEFAULT '进行中',
                file_path TEXT,
                remark TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO contracts (
                id, contract_name, contract_number, party_name, amount,
                sign_date, start_date, end_date, status, file_path,
                remark, created_at, updated_at
            )
            SELECT
                id, contract_name, contract_number, party_name, amount,
                sign_date, start_date, end_date, status, file_path,
                remark, created_at, updated_at
            FROM contracts_old
            """
        )
        conn.execute("DROP TABLE contracts_old")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contracts_search "
            "ON contracts(contract_name, contract_number, party_name)"
        )
        rows = conn.execute("SELECT id, end_date FROM contracts").fetchall()
        for item in rows:
            conn.execute(
                "UPDATE contracts SET status = ? WHERE id = ?",
                (calculate_status(item["end_date"] or ""), item["id"]),
            )

    # 新增一份合同，并返回新合同的数据库 ID。
    def add_contract(self, contract: Contract) -> int:
        timestamp = now_text()
        status = calculate_status(contract.end_date)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO contracts (
                    contract_name, contract_number, party_name, amount,
                    sign_date, start_date, end_date, status, file_path,
                    remark, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contract.contract_name,
                    contract.contract_number,
                    contract.party_name,
                    contract.amount,
                    contract.sign_date,
                    contract.start_date,
                    contract.end_date,
                    status,
                    contract.file_path,
                    contract.remark,
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    # 根据合同 ID 更新合同内容，并刷新状态和更新时间。
    def update_contract(self, contract: Contract) -> None:
        if contract.id is None:
            raise ValueError("更新合同时缺少 ID")

        with self.connect() as conn:
            conn.execute(
                """
                UPDATE contracts
                SET contract_name = ?,
                    contract_number = ?,
                    party_name = ?,
                    amount = ?,
                    sign_date = ?,
                    start_date = ?,
                    end_date = ?,
                    status = ?,
                    file_path = ?,
                    remark = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    contract.contract_name,
                    contract.contract_number,
                    contract.party_name,
                    contract.amount,
                    contract.sign_date,
                    contract.start_date,
                    contract.end_date,
                    calculate_status(contract.end_date),
                    contract.file_path,
                    contract.remark,
                    now_text(),
                    contract.id,
                ),
            )

    # 根据合同 ID 删除一份合同。
    def delete_contract(self, contract_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))

    # 根据合同 ID 查询单份合同；查不到时返回 None。
    def get_contract(self, contract_id: int) -> Optional[Contract]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
        return Contract.from_row(row) if row else None

    # 查询合同列表，支持按合同名称、合同编号、对方名称模糊搜索。
    def list_contracts(self, keyword: str = "") -> list[Contract]:
        self.refresh_statuses()
        keyword = keyword.strip()
        with self.connect() as conn:
            if keyword:
                like = f"%{keyword}%"
                rows = conn.execute(
                    """
                    SELECT * FROM contracts
                    WHERE contract_name LIKE ?
                       OR contract_number LIKE ?
                       OR party_name LIKE ?
                    ORDER BY end_date ASC, id DESC
                    """,
                    (like, like, like),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM contracts ORDER BY end_date ASC, id DESC"
                ).fetchall()
        return [Contract.from_row(row) for row in rows]

    # 刷新所有合同状态，确保到期状态随日期变化。
    def refresh_statuses(self) -> None:
        with self.connect() as conn:
            rows = conn.execute("SELECT id, end_date FROM contracts").fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE contracts SET status = ?, updated_at = updated_at WHERE id = ?",
                    (calculate_status(row["end_date"] or ""), row["id"]),
                )

    # 统计全部合同金额总和。
    def total_amount(self) -> float:
        with self.connect() as conn:
            value = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM contracts").fetchone()[0]
        return float(value or 0)

    # 按状态统计合同数量。
    def count_by_status(self) -> dict[str, int]:
        self.refresh_statuses()
        result = {ACTIVE_STATUS: 0, EXPIRING_STATUS: 0, EXPIRED_STATUS: 0}
        with self.connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS count FROM contracts GROUP BY status").fetchall()
        for row in rows:
            result[row["status"]] = row["count"]
        return result

    # 返回指定天数内即将到期的合同，用于启动提醒和统计窗口。
    def expiring_contracts(self, days: int = 30) -> list[Contract]:
        today = date.today()
        limit = today + timedelta(days=days)
        contracts: Iterable[Contract] = self.list_contracts()
        return [
            item
            for item in contracts
            if parse_date(item.end_date) and today <= parse_date(item.end_date) <= limit
        ]
