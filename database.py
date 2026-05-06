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


# 合同编号重复时抛出的自定义异常，便于界面层显示对应提示。
class DuplicateContractNumberError(ValueError):
    pass


# 合同编号格式不符合 12 位数字要求时抛出的自定义异常。
class InvalidContractNumberError(ValueError):
    pass


# 校验合同编号格式：必须是 12 位数字。
def validate_contract_number(contract_number: str) -> None:
    if len(contract_number) != 12 or not contract_number.isdigit():
        raise InvalidContractNumberError("合同编号必须是 12 位数字")


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
                    invoice_status TEXT NOT NULL DEFAULT '不开票',
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
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_contracts_number_unique "
                "ON contracts(contract_number)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS invoice_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contract_id INTEGER NOT NULL,
                    record_date TEXT NOT NULL,
                    amount REAL NOT NULL DEFAULT 0,
                    remark TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS payment_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contract_id INTEGER NOT NULL,
                    record_date TEXT NOT NULL,
                    amount REAL NOT NULL DEFAULT 0,
                    remark TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_invoice_records_contract "
                "ON invoice_records(contract_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_payment_records_contract "
                "ON payment_records(contract_id)"
            )
            self.migrate_schema(conn)
            self.ensure_invoice_column(conn)

    # 创建数据库连接，并在操作结束后自动提交和关闭。
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
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
                invoice_status TEXT NOT NULL DEFAULT '不开票',
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
                sign_date, start_date, end_date, status, invoice_status, file_path,
                remark, created_at, updated_at
            )
            SELECT
                id, contract_name, contract_number, party_name, amount,
                sign_date, start_date, end_date, status, '不开票', file_path,
                remark, created_at, updated_at
            FROM contracts_old
            """
        )
        conn.execute("DROP TABLE contracts_old")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contracts_search "
            "ON contracts(contract_name, contract_number, party_name)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_contracts_number_unique "
            "ON contracts(contract_number)"
        )
        rows = conn.execute("SELECT id, end_date FROM contracts").fetchall()
        for item in rows:
            conn.execute(
                "UPDATE contracts SET status = ? WHERE id = ?",
                (calculate_status(item["end_date"] or ""), item["id"]),
            )

    # 确保旧数据库也具备“是否开局发票”字段。
    def ensure_invoice_column(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(contracts)").fetchall()
        }
        if "invoice_status" not in columns:
            conn.execute(
                "ALTER TABLE contracts "
                "ADD COLUMN invoice_status TEXT NOT NULL DEFAULT '不开票'"
            )

    # 检查合同编号是否已被其他合同使用。
    def contract_number_exists(self, contract_number: str, exclude_id: int | None = None) -> bool:
        with self.connect() as conn:
            if exclude_id is None:
                row = conn.execute(
                    "SELECT 1 FROM contracts WHERE contract_number = ? LIMIT 1",
                    (contract_number,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT 1 FROM contracts
                    WHERE contract_number = ? AND id != ?
                    LIMIT 1
                    """,
                    (contract_number, exclude_id),
                ).fetchone()
        return row is not None

    # 新增一份合同，并返回新合同的数据库 ID。
    def add_contract(self, contract: Contract) -> int:
        validate_contract_number(contract.contract_number)
        if self.contract_number_exists(contract.contract_number):
            raise DuplicateContractNumberError("合同编号已存在")

        timestamp = now_text()
        status = calculate_status(contract.end_date)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO contracts (
                    contract_name, contract_number, party_name, amount,
                    sign_date, start_date, end_date, status, invoice_status, file_path,
                    remark, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    contract.invoice_status,
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
        validate_contract_number(contract.contract_number)
        if self.contract_number_exists(contract.contract_number, contract.id):
            raise DuplicateContractNumberError("合同编号已存在")

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
                    invoice_status = ?,
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
                    contract.invoice_status,
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

    # 批量新增某份合同的开票记录。
    def add_invoice_records(self, contract_id: int, records: Iterable[dict]) -> None:
        self.add_contract_records("invoice_records", contract_id, records)

    # 批量新增某份合同的收款记录。
    def add_payment_records(self, contract_id: int, records: Iterable[dict]) -> None:
        self.add_contract_records("payment_records", contract_id, records)

    # 查询某份合同的开票记录，用于主窗口子条目和记录查看窗口展示。
    def list_invoice_records(self, contract_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, contract_id, record_date, amount, remark, created_at, updated_at
                FROM invoice_records
                WHERE contract_id = ?
                ORDER BY record_date ASC, id ASC
                """,
                (contract_id,),
            ).fetchall()
        return rows

    # 查询某份合同的收款记录，用于主窗口子条目和记录查看窗口展示。
    def list_payment_records(self, contract_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, contract_id, record_date, amount, remark, created_at, updated_at
                FROM payment_records
                WHERE contract_id = ?
                ORDER BY record_date ASC, id ASC
                """,
                (contract_id,),
            ).fetchall()
        return rows

    # 合并查询某份合同的开票和收款记录，方便在主窗口作为合同子条目显示。
    def list_contract_records(self, contract_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT '开票记录' AS record_type, id, contract_id, record_date, amount, remark
                FROM invoice_records
                WHERE contract_id = ?
                UNION ALL
                SELECT '收款记录' AS record_type, id, contract_id, record_date, amount, remark
                FROM payment_records
                WHERE contract_id = ?
                ORDER BY record_date ASC, record_type ASC, id ASC
                """,
                (contract_id, contract_id),
            ).fetchall()
        return rows

    # 向指定记录表批量写入记录；table 只允许内部固定表名。
    def add_contract_records(self, table: str, contract_id: int, records: Iterable[dict]) -> None:
        if table not in {"invoice_records", "payment_records"}:
            raise ValueError("未知记录表")

        timestamp = now_text()
        rows = [
            (
                contract_id,
                item["record_date"],
                float(item["amount"]),
                item.get("remark", ""),
                timestamp,
                timestamp,
            )
            for item in records
        ]
        if not rows:
            return

        with self.connect() as conn:
            conn.executemany(
                f"""
                INSERT INTO {table} (
                    contract_id, record_date, amount, remark, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    # 根据合同 ID 查询单份合同；查不到时返回 None。
    def get_contract(self, contract_id: int) -> Optional[Contract]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
        return Contract.from_row(row) if row else None

    # 查询合同列表，支持按合同名称、合同编号、甲方名称模糊搜索。
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
