"""集中管理页面和导出中反复出现的中文文案。"""


# 通用字段标题，模板和导出都尽量从这里读取，减少多处改标题的成本。
UI_LABELS = {
    "contract_name": "合同名称",
    "contract_number": "合同编号",
    "contract_type": "合同类型",
    "storage_mode": "保存模式",
    "party_name": "甲方名称",
    "contract_amount": "合同金额",
    "invoice_status": "是否开票",
    "start_date": "开始日期",
    "end_date": "截止日期",
    "responsible_person": "负责人",
    "status": "状态",
    "file": "文件",
    "remark": "备注",
    "date": "日期",
    "month": "月份",
    "face_amount": "票面金额",
    "actual_amount": "实际金额",
    "upload_file": "上传文件",
    "download_file": "下载文件",
    "record_file": "记录文件",
    "bill_file": "发票文件",
    "receipt_file": "收据文件",
    "maintenance_file": "维保文件",
    "ticket_records": "票据记录",
    "empty_remark": "点击添加备注",
}


# 票据状态决定记录标题使用“发票/收票”还是“开据/收据”。
INVOICE_MODE_LABELS = {
    "发票": {
        "income_record_title": "开票记录",
        "expense_record_title": "收票记录",
        "income_file": "发票文件",
        "expense_file": "发票文件",
        "disabled_income_message": "该合同设置为开收据，开票记录不可用。",
        "empty_income_message": "暂无开票记录",
        "empty_expense_message": "暂无收票记录",
    },
    "收据": {
        "income_record_title": "开据记录",
        "expense_record_title": "收据记录",
        "income_file": "发票文件",
        "expense_file": "收据文件",
        "disabled_income_message": "该合同设置为开收据，开票记录不可用。",
        "empty_income_message": "暂无开据记录",
        "empty_expense_message": "暂无收据记录",
    },
}


# 合同类型对应的扩展记录标题，后续新增类型时优先改这里。
PROJECT_RECORD_LABELS = {
    "维保": {
        "button": "维保记录",
        "new_title": "新增维保记录",
        "list_title": "维保记录",
        "file": "维保文件",
        "empty": "暂无维保记录",
    },
    "评估": {
        "button": "评估记录",
        "new_title": "新增评估记录",
        "list_title": "评估记录",
        "file": "评估文件",
        "empty": "暂无评估记录",
    },
    "检测": {
        "button": "检测记录",
        "new_title": "新增检测记录",
        "list_title": "检测记录",
        "file": "检测文件",
        "empty": "暂无检测记录",
    },
    "改造": {
        "button": "改造记录",
        "new_title": "新增改造记录",
        "list_title": "改造记录",
        "file": "改造文件",
        "empty": "暂无改造记录",
    },
    "新建": {
        "button": "新建记录",
        "new_title": "新增新建记录",
        "list_title": "新建记录",
        "file": "新建文件",
        "empty": "暂无新建记录",
    },
}


# 函数说明：根据是否开票返回票据记录文案。
def invoice_mode_labels(invoice_status: str) -> dict:
    return INVOICE_MODE_LABELS["收据" if invoice_status == "开收据" else "发票"]


# 函数说明：根据合同类型返回扩展项目记录文案。
def project_record_labels(contract_type: str) -> dict:
    return PROJECT_RECORD_LABELS.get(contract_type, PROJECT_RECORD_LABELS["维保"])


# 函数说明：把全局文案注入所有模板。
def labels_context(_request) -> dict:
    return {"labels": UI_LABELS}
