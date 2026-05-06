# dataclass 用来快速定义只保存数据的类。
from dataclasses import dataclass
# datetime 用来生成创建时间和更新时间。
from datetime import datetime
# Optional 表示某个字段可以是指定类型，也可以是 None。
from typing import Optional


@dataclass
class Contract:
    # 合同在程序内使用的数据结构，对应数据库 contracts 表的字段。
    id: Optional[int] = None
    contract_name: str = ""
    contract_number: str = ""
    party_name: str = ""
    amount: float = 0.0
    sign_date: str = ""
    start_date: str = ""
    end_date: str = ""
    status: str = "进行中"
    file_path: str = ""
    remark: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row) -> "Contract":
        # 将 SQLite 查询结果转换成 Contract 对象，方便界面层使用。
        return cls(
            id=row["id"],
            contract_name=row["contract_name"],
            contract_number=row["contract_number"],
            party_name=row["party_name"],
            amount=float(row["amount"] or 0),
            sign_date=row["sign_date"] or "",
            start_date=row["start_date"] or "",
            end_date=row["end_date"] or "",
            status=row["status"] or "",
            file_path=row["file_path"] or "",
            remark=row["remark"] or "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )


def now_text() -> str:
    # 统一生成数据库中 created_at / updated_at 使用的时间文本。
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
