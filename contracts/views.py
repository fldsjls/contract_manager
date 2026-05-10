from datetime import timedelta
from decimal import Decimal
from functools import wraps
import json
from pathlib import Path
import socket

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import (
    AppSettingForm,
    ContractForm,
    LoginForm,
    default_contract_number,
)
from .models import AppSetting, Contract, ContractFile, InvoiceRecord, MaintenanceRecord, PaymentRecord, SettlementFile


# 回收站内合同默认保留 7 天。
TRASH_RETENTION_DAYS = 7
# 记录类型到收入/支出方向的映射，统计图和总览卡片都依赖这里归类。
INCOME_TYPES = {"开票", "开据"}
EXPENSE_TYPES = {"收票", "收据"}
TYPE_SIDE = {
    "开票": "income",
    "开据": "income",
    "收票": "expense",
    "收据": "expense",
}


# 判断当前请求是否处于管理员模式。
def is_admin_mode(request) -> bool:
    return bool(
        request.user.is_authenticated
        and request.user.is_staff
        and not request.session.get("guest_mode", False)
        and not request.session.get("normal_mode", False)
    )


# 判断当前请求是否处于普通用户模式。
def is_normal_mode(request) -> bool:
    return bool(
        request.user.is_authenticated
        and not request.user.is_staff
        and request.session.get("normal_mode", False)
        and not request.session.get("guest_mode", False)
    )


# 普通用户继承管理员的大部分操作，只屏蔽发票/收据类新增入口。
def can_manage(request) -> bool:
    return is_admin_mode(request) or is_normal_mode(request)


def can_add_money_records(request) -> bool:
    return is_admin_mode(request)


# 限制只有管理员或普通用户模式才能访问写入类页面。
def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if can_manage(request):
            return view_func(request, *args, **kwargs)
        return redirect("contracts:login")

    return wrapper


# 发票/收据类记录只允许管理员新增，普通用户和游客都不展示也不能直连。
def money_record_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if can_add_money_records(request):
            return view_func(request, *args, **kwargs)
        return redirect("contracts:login")

    return wrapper


# 账号密码等真正的管理员能力不下放给普通用户。
def true_admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if is_admin_mode(request):
            return view_func(request, *args, **kwargs)
        return redirect("contracts:login")

    return wrapper


# 给模板上下文统一补充登录模式信息。
def context_with_auth(request, context: dict | None = None) -> dict:
    data = context or {}
    data["is_admin_mode"] = is_admin_mode(request)
    data["is_normal_mode"] = is_normal_mode(request)
    data["is_guest_mode"] = bool(request.session.get("guest_mode", False))
    data["can_manage"] = can_manage(request)
    data["can_add_money_records"] = can_add_money_records(request)
    return data


# 从磁盘上删除上传文件。
def delete_file_from_storage(file_field) -> None:
    if not file_field:
        return
    try:
        path = Path(file_field.path)
    except ValueError:
        return
    if path.exists() and path.is_file():
        path.unlink()


# 保存合同附件，必要时按系统设置替换旧文件。
def save_contract_files(contract: Contract, uploaded_files) -> None:
    uploaded_files = list(uploaded_files)
    if uploaded_files and AppSetting.current().delete_source_file:
        delete_contract_files(contract.files.values_list("id", flat=True))
        if contract.file:
            delete_file_from_storage(contract.file)
            contract.file = None
            contract.save(update_fields=["file"])
    next_order = contract.files.count()
    for index, item in enumerate(uploaded_files):
        ContractFile.objects.create(
            contract=contract,
            file=item,
            original_name=item.name,
            sort_order=next_order + index,
        )


# 保存合同附件并返回新建的文件对象，供即时上传接口使用。
def save_contract_files_and_return(contract: Contract, uploaded_files) -> list[ContractFile]:
    uploaded_files = list(uploaded_files)
    if uploaded_files and AppSetting.current().delete_source_file:
        delete_contract_files(contract.files.values_list("id", flat=True))
        if contract.file:
            delete_file_from_storage(contract.file)
            contract.file = None
            contract.save(update_fields=["file"])
    next_order = contract.files.count()
    return [
        ContractFile.objects.create(
            contract=contract,
            file=item,
            original_name=item.name,
            sort_order=next_order + index,
        )
        for index, item in enumerate(uploaded_files)
    ]


# 删除选中的合同附件记录和磁盘文件。
def delete_contract_files(file_ids) -> None:
    for item in ContractFile.objects.filter(id__in=file_ids):
        delete_file_from_storage(item.file)
        item.delete()


def preview_type_for_file(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}:
        return "image"
    return "unsupported"


# 永久清理超过保留期的回收站合同和关联文件。
def purge_expired_trash() -> None:
    cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
    expired_contracts = Contract.objects.filter(is_deleted=True, deleted_at__lt=cutoff)
    for contract in expired_contracts:
        delete_file_from_storage(contract.file)
        delete_contract_files(contract.files.values_list("id", flat=True))
        for record in contract.invoicerecord_set.all():
            delete_file_from_storage(record.file)
        for record in contract.paymentrecord_set.all():
            delete_file_from_storage(record.file)
        for record in contract.maintenancerecord_set.all():
            delete_file_from_storage(record.file)
        for item in contract.settlement_files.all():
            delete_file_from_storage(item.file)
        contract.delete()


# 从批量记录表单中读取多行开票或收票数据。
def save_records_from_request(request, contract: Contract, record_model) -> int:
    dates = request.POST.getlist("record_date")
    amounts = request.POST.getlist("amount")
    actual_amounts = request.POST.getlist("actual_amount")
    record_types = request.POST.getlist("record_type")
    remarks = request.POST.getlist("remark")
    saved_count = 0
    for index, record_date in enumerate(dates):
        amount = amounts[index] if index < len(amounts) else ""
        actual_amount = actual_amounts[index] if index < len(actual_amounts) else None
        record_type = record_types[index] if index < len(record_types) else ""
        remark = remarks[index] if index < len(remarks) else ""
        if not record_date or amount == "":
            continue
        record_model.objects.create(
            contract=contract,
            record_date=record_date,
            record_type=record_type,
            amount=amount,
            actual_amount=actual_amount or None,
            file=request.FILES.get(f"file_{index}"),
            remark=remark,
        )
        saved_count += 1
    return saved_count


def save_typed_records_from_request(request, contract: Contract, record_model_by_type: dict[str, type]) -> int:
    dates = request.POST.getlist("record_date")
    amounts = request.POST.getlist("amount")
    actual_amounts = request.POST.getlist("actual_amount")
    record_types = request.POST.getlist("record_type")
    remarks = request.POST.getlist("remark")
    saved_count = 0
    for index, record_date in enumerate(dates):
        amount = amounts[index] if index < len(amounts) else ""
        actual_amount = actual_amounts[index] if index < len(actual_amounts) else None
        record_type = record_types[index] if index < len(record_types) else ""
        remark = remarks[index] if index < len(remarks) else ""
        record_model = record_model_by_type.get(record_type)
        if not record_date or amount == "" or record_model is None:
            continue
        record_model.objects.create(
            contract=contract,
            record_date=record_date,
            record_type=record_type,
            amount=amount,
            actual_amount=actual_amount or None,
            file=request.FILES.get(f"file_{index}"),
            remark=remark,
        )
        saved_count += 1
    return saved_count


def income_expense_totals(invoice_records, payment_records) -> tuple[Decimal, Decimal]:
    # 兼容发票和收据两张表，按 record_type 而不是模型名判断收入/支出。
    income_total = Decimal("0")
    expense_total = Decimal("0")
    for record in invoice_records:
        amount = record_amount_for_stats(record)
        if record_side(record) == "income":
            income_total += amount
        else:
            expense_total += amount
    for record in payment_records:
        amount = record_amount_for_stats(record)
        if record_side(record) == "income":
            income_total += amount
        else:
            expense_total += amount
    return income_total, expense_total


def record_amount_for_stats(record) -> Decimal:
    # 实际金额优先用于统计；未填实际金额时回退到票面金额。
    return record.actual_amount if record.actual_amount is not None else record.amount


def record_side(record) -> str:
    # 旧数据可能没有明确类型，按模型给出兜底方向。
    return TYPE_SIDE.get(record.record_type, "income" if isinstance(record, InvoiceRecord) else "expense")


def add_income_expense(target: dict, record) -> None:
    # 将单条记录累加到按日期/年份/月度汇总的目标字典。
    amount = record_amount_for_stats(record)
    if record_side(record) == "income":
        target["income"] = target.get("income", Decimal("0")) + amount
    else:
        target["expense"] = target.get("expense", Decimal("0")) + amount


def project_mode_labels(contract: Contract) -> dict:
    # 不开发票的合同把“开票/收票”文案替换成“开据/收据”。
    has_invoice = contract.invoice_status != "不开票"
    return {
        "invoice_primary": "开票金额" if has_invoice else "开据金额",
        "invoice_secondary": "收款金额" if has_invoice else "收款金额",
        "invoice_rate": "收款率",
        "receipt_primary": "收票金额" if has_invoice else "收据金额",
        "receipt_secondary": "付款金额",
        "receipt_rate": "利润率",
    }


def project_mode_totals(records) -> dict:
    # 项目统计弹窗需要同时展示票面金额和实际金额两组口径。
    totals = {
        "invoice_primary": Decimal("0"),
        "invoice_secondary": Decimal("0"),
        "receipt_primary": Decimal("0"),
        "receipt_secondary": Decimal("0"),
    }
    for record in records:
        side = "invoice" if record_side(record) == "income" else "receipt"
        totals[f"{side}_primary"] += record.amount
        totals[f"{side}_secondary"] += record_amount_for_stats(record)
    return totals


def project_mode_chart_rows(rows: list[dict], records) -> list[dict]:
    # 在已有时间轴行上叠加收入/支出的票面金额和实际金额。
    by_label = {row["label"]: row for row in rows}
    for row in rows:
        row.update(
            {
                "income_primary": Decimal("0"),
                "income_secondary": Decimal("0"),
                "expense_primary": Decimal("0"),
                "expense_secondary": Decimal("0"),
            }
        )
    for record, label in records:
        row = by_label.get(label)
        if row is None:
            continue
        side = record_side(record)
        row[f"{side}_primary"] += record.amount
        row[f"{side}_secondary"] += record_amount_for_stats(record)
    return rows


# 从批量记录表单中读取多行维护保养数据。
def save_maintenance_records_from_request(request, contract: Contract) -> int:
    dates = request.POST.getlist("record_date")
    months = request.POST.getlist("month")
    remarks = request.POST.getlist("remark")
    saved_count = 0
    for index, record_date in enumerate(dates):
        month = months[index] if index < len(months) else ""
        remark = remarks[index] if index < len(remarks) else ""
        if not record_date or not month:
            continue
        if "-" in month:
            year_text, month_text = month.split("-", 1)
            month = f"{year_text}年{int(month_text)}月"
        MaintenanceRecord.objects.create(
            contract=contract,
            record_date=record_date,
            month=month,
            file=request.FILES.get(f"file_{index}"),
            remark=remark,
        )
        saved_count += 1
    return saved_count


# 查询 30 天内即将到期的合同。
def expiring_contract_queryset():
    today = timezone.localdate()
    expiring_limit = today + timedelta(days=30)
    return Contract.objects.filter(
        is_deleted=False,
        end_date__isnull=False,
        end_date__gte=today,
        end_date__lte=expiring_limit,
    ).order_by("end_date")


# 把按日期汇总的数据转换成 SVG 折线图坐标。
def chart_points(rows: list[dict], key: str, max_amount: Decimal) -> str:
    if not rows:
        return ""

    left = Decimal("70")
    top = Decimal("70")
    width = Decimal("770")
    height = Decimal("160")
    count = len(rows)
    points = []
    for index, row in enumerate(rows):
        x = left + (width * Decimal(index) / Decimal(max(count - 1, 1)))
        ratio = Decimal(row[key]) / max_amount
        y = top + height - height * ratio
        points.append(f"{float(x):.1f},{float(y):.1f}")
    return " ".join(points)


# 给每个图表日期补充坐标，供 SVG 绘制圆点使用。
def enrich_chart_rows(rows: list[dict], max_amount: Decimal) -> list[dict]:
    if not rows:
        return rows

    left = Decimal("70")
    top = Decimal("70")
    width = Decimal("770")
    height = Decimal("160")
    count = len(rows)
    for index, row in enumerate(rows):
        x = left + (width * Decimal(index) / Decimal(max(count - 1, 1)))
        if index == 0:
            amount_label_x = x + Decimal("44")
        elif index == count - 1:
            amount_label_x = x - Decimal("10")
        else:
            amount_label_x = x + Decimal("28")
        for key in ("income", "expense"):
            ratio = Decimal(row[key]) / max_amount
            y = top + height - height * ratio
            row[f"{key}_x"] = f"{float(x):.1f}"
            row[f"{key}_y"] = f"{float(y):.1f}"
            if key == "income":
                label_y = max(top - Decimal("18"), y - Decimal("16"))
            else:
                label_y = min(top + height + Decimal("18"), y + Decimal("24"))
            row[f"{key}_label_x"] = f"{float(amount_label_x):.1f}"
            row[f"{key}_label_y"] = f"{float(label_y):.1f}"
        row["date_label_x"] = f"{float(x):.1f}"
    return rows


# 根据请求参数确定统计趋势图的时间范围。
def chart_range_from_request(request):
    today = timezone.localdate()
    period = request.GET.get("period", "all")
    if period not in ("all", "year", "month"):
        period = "all"
    try:
        year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year
    try:
        month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        month = today.month
    month = min(max(month, 1), 12)

    if period == "all":
        prev_params = "period=all"
        next_params = "period=all"
        return period, year, month, prev_params, next_params, "全部"

    if period == "year":
        prev_params = f"period=year&year={year - 1}&month={month}"
        next_params = f"period=year&year={year + 1}&month={month}"
        return period, year, month, prev_params, next_params, f"{year - 11} - {year} 年"

    prev_params = f"period=month&year={year - 1}&month={month}"
    next_params = f"period=month&year={year + 1}&month={month}"
    return period, year, month, prev_params, next_params, f"{year} 年"


# 按统计范围汇总开票/收票记录，生成趋势图行数据。
def build_chart_rows(period: str, year: int, month: int) -> list[dict]:
    invoice_queryset = InvoiceRecord.objects.filter(contract__is_deleted=False)
    payment_queryset = PaymentRecord.objects.filter(contract__is_deleted=False)
    if period == "all":
        today = timezone.localdate()
        units = [today - timedelta(days=offset) for offset in range(11, -1, -1)]
        totals_by_day = {}
        for record in invoice_queryset.filter(record_date__gte=units[0], record_date__lte=today):
            add_income_expense(totals_by_day.setdefault(record.record_date, {}), record)
        for record in payment_queryset.filter(record_date__gte=units[0], record_date__lte=today):
            add_income_expense(totals_by_day.setdefault(record.record_date, {}), record)
        return [
            {
                "label": unit.strftime("%m-%d"),
                "income": totals_by_day.get(unit, {}).get("income", Decimal("0")),
                "expense": totals_by_day.get(unit, {}).get("expense", Decimal("0")),
            }
            for unit in units
        ]

    if period == "year":
        start_year = year - 11
        totals_by_unit = {}
        for record in invoice_queryset.filter(record_date__year__gte=start_year, record_date__year__lte=year):
            unit = record.record_date.year
            add_income_expense(totals_by_unit.setdefault(unit, {}), record)
        for record in payment_queryset.filter(record_date__year__gte=start_year, record_date__year__lte=year):
            unit = record.record_date.year
            add_income_expense(totals_by_unit.setdefault(unit, {}), record)
        units = list(range(start_year, year + 1))
        return [
            {
                "label": f"{unit}年",
                "income": totals_by_unit.get(unit, {}).get("income", Decimal("0")),
                "expense": totals_by_unit.get(unit, {}).get("expense", Decimal("0")),
            }
            for unit in units
        ]

    totals_by_unit = {}
    for record in invoice_queryset.filter(record_date__year=year):
        unit = record.record_date.month
        add_income_expense(totals_by_unit.setdefault(unit, {}), record)
    for record in payment_queryset.filter(record_date__year=year):
        unit = record.record_date.month
        add_income_expense(totals_by_unit.setdefault(unit, {}), record)
    units = list(range(1, 13))
    return [
        {
            "label": f"{unit:02d}月",
            "income": totals_by_unit.get(unit, {}).get("income", Decimal("0")),
            "expense": totals_by_unit.get(unit, {}).get("expense", Decimal("0")),
        }
        for unit in units
    ]


# 获取当前主机的局域网 IP，用于设置页提示其他用户访问地址。
def local_ip_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


# 根据当前统计范围过滤合同和记录查询。
def scoped_querysets(period: str, year: int, month: int):
    contracts = Contract.objects.filter(is_deleted=False)
    invoice_records = InvoiceRecord.objects.filter(contract__is_deleted=False)
    payment_records = PaymentRecord.objects.filter(contract__is_deleted=False)
    if period == "year":
        start_year = year - 11
        contracts = contracts.filter(created_at__year__gte=start_year, created_at__year__lte=year)
        invoice_records = invoice_records.filter(record_date__year__gte=start_year, record_date__year__lte=year)
        payment_records = payment_records.filter(record_date__year__gte=start_year, record_date__year__lte=year)
    elif period == "month":
        contracts = contracts.filter(created_at__year=year)
        invoice_records = invoice_records.filter(record_date__year=year)
        payment_records = payment_records.filter(record_date__year=year)
    return contracts, invoice_records, payment_records


# 按统计范围生成单个合同的开票/收票趋势数据。
def build_contract_chart_rows(contract: Contract, period: str, year: int, month: int) -> list[dict]:
    invoice_queryset = contract.invoicerecord_set.all()
    payment_queryset = contract.paymentrecord_set.all()
    if period == "all":
        today = timezone.localdate()
        units = [today - timedelta(days=offset) for offset in range(11, -1, -1)]
        totals_by_day = {}
        for record in invoice_queryset.filter(record_date__gte=units[0], record_date__lte=today):
            add_income_expense(totals_by_day.setdefault(record.record_date, {}), record)
        for record in payment_queryset.filter(record_date__gte=units[0], record_date__lte=today):
            add_income_expense(totals_by_day.setdefault(record.record_date, {}), record)
        return [
            {
                "label": unit.strftime("%m-%d"),
                "income": totals_by_day.get(unit, {}).get("income", Decimal("0")),
                "expense": totals_by_day.get(unit, {}).get("expense", Decimal("0")),
            }
            for unit in units
        ]

    if period == "year":
        start_year = year - 11
        totals_by_unit = {}
        for record in invoice_queryset.filter(record_date__year__gte=start_year, record_date__year__lte=year):
            add_income_expense(totals_by_unit.setdefault(record.record_date.year, {}), record)
        for record in payment_queryset.filter(record_date__year__gte=start_year, record_date__year__lte=year):
            add_income_expense(totals_by_unit.setdefault(record.record_date.year, {}), record)
        return [
            {
                "label": f"{unit}年",
                "income": totals_by_unit.get(unit, {}).get("income", Decimal("0")),
                "expense": totals_by_unit.get(unit, {}).get("expense", Decimal("0")),
            }
            for unit in range(start_year, year + 1)
        ]

    totals_by_unit = {}
    for record in invoice_queryset.filter(record_date__year=year):
        add_income_expense(totals_by_unit.setdefault(record.record_date.month, {}), record)
    for record in payment_queryset.filter(record_date__year=year):
        add_income_expense(totals_by_unit.setdefault(record.record_date.month, {}), record)
    return [
        {
            "label": f"{unit:02d}月",
            "income": totals_by_unit.get(unit, {}).get("income", Decimal("0")),
            "expense": totals_by_unit.get(unit, {}).get("expense", Decimal("0")),
        }
        for unit in range(1, 13)
    ]


def build_contract_mode_chart_rows(contract: Contract, period: str, year: int, month: int) -> list[dict]:
    records = list(contract.invoicerecord_set.all()) + list(contract.paymentrecord_set.all())
    if period == "all":
        today = timezone.localdate()
        units = [today - timedelta(days=offset) for offset in range(11, -1, -1)]
        unit_for_record = lambda record: record.record_date
        filtered_records = [record for record in records if units[0] <= record.record_date <= today]
    elif period == "year":
        start_year = year - 11
        units = list(range(start_year, year + 1))
        unit_for_record = lambda record: record.record_date.year
        filtered_records = [record for record in records if start_year <= record.record_date.year <= year]
    else:
        units = list(range(1, 13))
        unit_for_record = lambda record: record.record_date.month
        filtered_records = [record for record in records if record.record_date.year == year]

    totals_by_unit = {}
    for record in filtered_records:
        unit = unit_for_record(record)
        side = record_side(record)
        totals = totals_by_unit.setdefault(
            unit,
            {
                "invoice_primary": Decimal("0"),
                "invoice_secondary": Decimal("0"),
                "receipt_primary": Decimal("0"),
                "receipt_secondary": Decimal("0"),
            },
        )
        mode = "invoice" if side == "income" else "receipt"
        totals[f"{mode}_primary"] += record.amount
        totals[f"{mode}_secondary"] += record_amount_for_stats(record)

    def label_for_unit(unit):
        if period == "all":
            return unit.strftime("%m-%d")
        if period == "year":
            return f"{unit}年"
        return f"{unit:02d}月"

    return [
        {
            "label": label_for_unit(unit),
            "invoice_primary": totals_by_unit.get(unit, {}).get("invoice_primary", Decimal("0")),
            "invoice_secondary": totals_by_unit.get(unit, {}).get("invoice_secondary", Decimal("0")),
            "receipt_primary": totals_by_unit.get(unit, {}).get("receipt_primary", Decimal("0")),
            "receipt_secondary": totals_by_unit.get(unit, {}).get("receipt_secondary", Decimal("0")),
        }
        for unit in units
    ]


# 返回单个合同的统计弹窗数据。
def build_dashboard_mode_chart_rows(invoice_records, payment_records, period: str, year: int, month: int) -> list[dict]:
    records = list(invoice_records) + list(payment_records)
    if period == "all":
        today = timezone.localdate()
        units = [today - timedelta(days=offset) for offset in range(11, -1, -1)]
        unit_for_record = lambda record: record.record_date
        filtered_records = [record for record in records if units[0] <= record.record_date <= today]
    elif period == "year":
        start_year = year - 11
        units = list(range(start_year, year + 1))
        unit_for_record = lambda record: record.record_date.year
        filtered_records = [record for record in records if start_year <= record.record_date.year <= year]
    else:
        units = list(range(1, 13))
        unit_for_record = lambda record: record.record_date.month
        filtered_records = [record for record in records if record.record_date.year == year]

    totals_by_unit = {}
    for record in filtered_records:
        unit = unit_for_record(record)
        totals = totals_by_unit.setdefault(
            unit,
            {
                "invoice_primary": Decimal("0"),
                "invoice_secondary": Decimal("0"),
                "receipt_primary": Decimal("0"),
                "receipt_secondary": Decimal("0"),
            },
        )
        mode = "invoice" if record_side(record) == "income" else "receipt"
        totals[f"{mode}_primary"] += record.amount
        totals[f"{mode}_secondary"] += record_amount_for_stats(record)

    def label_for_unit(unit):
        if period == "all":
            return unit.strftime("%m-%d")
        if period == "year":
            return f"{unit}年"
        return f"{unit:02d}月"

    return [
        {
            "label": label_for_unit(unit),
            "invoice_primary": totals_by_unit.get(unit, {}).get("invoice_primary", Decimal("0")),
            "invoice_secondary": totals_by_unit.get(unit, {}).get("invoice_secondary", Decimal("0")),
            "receipt_primary": totals_by_unit.get(unit, {}).get("receipt_primary", Decimal("0")),
            "receipt_secondary": totals_by_unit.get(unit, {}).get("receipt_secondary", Decimal("0")),
        }
        for unit in units
    ]


def contract_stats_data(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    period, year, month, _, _, range_label = chart_range_from_request(request)
    chart_rows = build_contract_chart_rows(contract, period, year, month)
    mode_chart_rows = build_contract_mode_chart_rows(contract, period, year, month)
    invoice_records = contract.invoicerecord_set.all()
    payment_records = contract.paymentrecord_set.all()
    if period == "year":
        start_year = year - 11
        invoice_records = invoice_records.filter(record_date__year__gte=start_year, record_date__year__lte=year)
        payment_records = payment_records.filter(record_date__year__gte=start_year, record_date__year__lte=year)
    elif period == "month":
        invoice_records = invoice_records.filter(record_date__year=year)
        payment_records = payment_records.filter(record_date__year=year)
    income_total, expense_total = income_expense_totals(invoice_records, payment_records)
    mode_totals = project_mode_totals(list(invoice_records) + list(payment_records))
    labels = project_mode_labels(contract)
    income_rate = (mode_totals["invoice_secondary"] / contract.amount * Decimal("100")) if contract.amount else Decimal("0")
    expense_rate = (mode_totals["receipt_secondary"] / contract.amount * Decimal("100")) if contract.amount else Decimal("0")
    payment_rate = (income_total / contract.amount * Decimal("100")) if contract.amount else Decimal("0")
    return JsonResponse(
        {
            "contract_name": contract.contract_name,
            "amount": float(contract.amount),
            "income_total": float(income_total),
            "expense_total": float(expense_total),
            "invoice_total": float(income_total),
            "payment_total": float(expense_total),
            "payment_rate": float(payment_rate),
            "period": period,
            "year": year,
            "month": month,
            "range_label": range_label,
            "labels": labels,
            "modes": {
                "invoice": {
                    "primary_total": float(mode_totals["invoice_primary"]),
                    "secondary_total": float(mode_totals["invoice_secondary"]),
                    "rate": float(income_rate),
                    "chart": {
                        "primary": [float(row["invoice_primary"]) for row in mode_chart_rows],
                        "secondary": [float(row["invoice_secondary"]) for row in mode_chart_rows],
                    },
                },
                "receipt": {
                    "primary_total": float(mode_totals["receipt_primary"]),
                    "secondary_total": float(mode_totals["receipt_secondary"]),
                    "rate": float(expense_rate),
                    "chart": {
                        "primary": [float(row["receipt_primary"]) for row in mode_chart_rows],
                        "secondary": [float(row["receipt_secondary"]) for row in mode_chart_rows],
                    },
                },
            },
            "chart": {
                "labels": [row["label"] for row in chart_rows],
                "income": [float(row["income"]) for row in chart_rows],
                "expense": [float(row["expense"]) for row in chart_rows],
                "invoice": [float(row["income"]) for row in chart_rows],
                "payment": [float(row["expense"]) for row in chart_rows],
            },
        }
    )


# 返回单个合同的类型记录月份日历数据。
def maintenance_record_data(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year
    records = contract.maintenancerecord_set.filter(record_date__year=year).order_by("record_date", "id")
    grouped_records = {}
    for record in records:
        grouped_records.setdefault(record.record_date.month, []).append(
            {
                "date": f"{record.record_date.year}年{record.record_date.month}月{record.record_date.day}日",
                "month": record.month,
                "remark": record.remark,
                "has_file": bool(record.file),
                "file_url": record.file.url if record.file else "",
            }
        )
    return JsonResponse(
        {
            "contract_name": contract.contract_name,
            "year": year,
            "months": [
                {
                    "month": month,
                    "label": f"{month}月",
                    "has_records": month in grouped_records,
                    "records": grouped_records.get(month, []),
                }
                for month in range(1, 13)
            ],
        }
    )


# 渲染合同文件预览页，避免局域网用户直接触发浏览器下载。
def contract_file_preview(request, pk: int):
    item = get_object_or_404(ContractFile, pk=pk, contract__is_deleted=False)
    return_from = request.GET.get("from")
    if return_from == "list":
        return_url = reverse("contracts:contract_list")
    elif return_from == "edit":
        return_url = reverse("contracts:contract_update", args=[item.contract.id])
    else:
        return_url = reverse("contracts:contract_detail", args=[item.contract.id])
    preview_type = preview_type_for_file(item.file.name)
    return render(
        request,
        "contracts/file_preview.html",
        context_with_auth(
            request,
            {
                "contract": item.contract,
                "file_item": item,
                "file_name": item.original_name or Path(item.file.name).name,
                "file_url": item.file.url,
                "preview_type": preview_type,
                "return_url": return_url,
                "delete_url": reverse("contracts:contract_file_delete", args=[item.id]),
                "active_nav": "contracts",
            },
        ),
    )


@admin_required
# 从预览页删除当前合同附件。
def contract_file_delete(request, pk: int):
    item = get_object_or_404(ContractFile, pk=pk, contract__is_deleted=False)
    contract_id = item.contract_id
    if request.method == "POST":
        delete_file_from_storage(item.file)
        item.delete()
    return redirect("contracts:contract_detail", pk=contract_id)


# 渲染早期单文件字段的预览页，兼容旧数据。
def legacy_contract_file_preview(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if not contract.file:
        return redirect("contracts:contract_detail", pk=contract.pk)
    return_from = request.GET.get("from")
    if return_from == "list":
        return_url = reverse("contracts:contract_list")
    elif return_from == "edit":
        return_url = reverse("contracts:contract_update", args=[contract.id])
    else:
        return_url = reverse("contracts:contract_detail", args=[contract.id])
    preview_type = preview_type_for_file(contract.file.name)
    return render(
        request,
        "contracts/file_preview.html",
        context_with_auth(
            request,
            {
                "contract": contract,
                "file_name": Path(contract.file.name).name,
                "file_url": contract.file.url,
                "preview_type": preview_type,
                "return_url": return_url,
                "delete_url": reverse("contracts:legacy_contract_file_delete", args=[contract.id]),
                "active_nav": "contracts",
            },
        ),
    )


@admin_required
# 从预览页删除早期单文件字段中的合同文件。
def legacy_contract_file_delete(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method == "POST" and contract.file:
        delete_file_from_storage(contract.file)
        contract.file = None
        contract.save(update_fields=["file", "updated_at"])
    return redirect("contracts:contract_detail", pk=contract.pk)


# 渲染统计总览页面。
def dashboard(request):
    purge_expired_trash()
    chart_period, chart_year, chart_month, prev_range_params, next_range_params, chart_range_label = chart_range_from_request(request)
    active_contracts, scoped_invoice_records, scoped_payment_records = scoped_querysets(chart_period, chart_year, chart_month)
    total_amount = active_contracts.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    total_income, total_expense = income_expense_totals(scoped_invoice_records, scoped_payment_records)
    mode_totals = project_mode_totals(list(scoped_invoice_records) + list(scoped_payment_records))
    income_rate = (mode_totals["invoice_secondary"] / total_amount * Decimal("100")) if total_amount else Decimal("0")
    expense_rate = (mode_totals["receipt_secondary"] / total_amount * Decimal("100")) if total_amount else Decimal("0")
    payment_rate = (total_income / total_amount * Decimal("100")) if total_amount else Decimal("0")

    chart_rows = build_chart_rows(chart_period, chart_year, chart_month)
    mode_chart_rows = build_dashboard_mode_chart_rows(
        scoped_invoice_records,
        scoped_payment_records,
        chart_period,
        chart_year,
        chart_month,
    )
    chart_data = {
        "labels": [row["label"] for row in mode_chart_rows],
        "income": [float(row["income"]) for row in chart_rows],
        "expense": [float(row["expense"]) for row in chart_rows],
        "invoice": [float(row["income"]) for row in chart_rows],
        "payment": [float(row["expense"]) for row in chart_rows],
        "modes": {
            "invoice": {
                "labels": {
                    "primary": "总开票/开据金额",
                    "secondary": "总收款金额",
                    "rate": "收款率",
                },
                "primary_total": float(mode_totals["invoice_primary"]),
                "secondary_total": float(mode_totals["invoice_secondary"]),
                "rate": float(income_rate),
                "chart": {
                    "primary": [float(row["invoice_primary"]) for row in mode_chart_rows],
                    "secondary": [float(row["invoice_secondary"]) for row in mode_chart_rows],
                },
            },
            "receipt": {
                "labels": {
                    "primary": "总收票/收据金额",
                    "secondary": "总付款金额",
                    "rate": "利润率",
                },
                "primary_total": float(mode_totals["receipt_primary"]),
                "secondary_total": float(mode_totals["receipt_secondary"]),
                "rate": float(expense_rate),
                "chart": {
                    "primary": [float(row["receipt_primary"]) for row in mode_chart_rows],
                    "secondary": [float(row["receipt_secondary"]) for row in mode_chart_rows],
                },
            },
        },
    }
    max_amount = max(
        [row["income"] for row in chart_rows] + [row["expense"] for row in chart_rows],
        default=Decimal("0"),
    ) or Decimal("1")
    chart_rows = enrich_chart_rows(chart_rows, max_amount)

    recent_contracts = list(active_contracts.order_by("-created_at"))
    context = context_with_auth(
        request,
        {
            "total_amount": total_amount,
            "total_income": total_income,
            "total_expense": total_expense,
            "total_invoice": total_income,
            "total_payment": total_expense,
            "dashboard_mode_totals": mode_totals,
            "dashboard_income_rate": income_rate,
            "dashboard_expense_rate": expense_rate,
            "payment_rate": payment_rate,
            "chart_rows": chart_rows,
            "chart_data": chart_data,
            "chart_has_lines": len(chart_rows) > 1,
            "income_points": chart_points(chart_rows, "income", max_amount),
            "expense_points": chart_points(chart_rows, "expense", max_amount),
            "invoice_points": chart_points(chart_rows, "income", max_amount),
            "payment_points": chart_points(chart_rows, "expense", max_amount),
            "chart_period": chart_period,
            "chart_year": chart_year,
            "chart_month": chart_month,
            "chart_range_label": chart_range_label,
            "prev_range_params": prev_range_params,
            "next_range_params": next_range_params,
            "expiring_contracts": expiring_contract_queryset(),
            "recent_contracts": recent_contracts,
            "recent_blank_rows": range(max(0, 5 - len(recent_contracts))),
            "active_nav": "dashboard",
        },
    )
    return render(request, "contracts/dashboard.html", context)


# 渲染合同列表页面，并处理搜索和表头排序。
def contract_list(request):
    purge_expired_trash()
    keyword = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "id").strip()
    direction = request.GET.get("direction", "asc").strip()
    if direction not in ("asc", "desc"):
        direction = "asc"
    sort_fields = {
        "id": "id",
        "contract_name": "contract_name",
        "contract_number": "contract_number",
        "contract_type": "contract_type",
        "party_name": "party_name",
        "amount": "amount",
        "invoice_status": "invoice_status",
        "start_date": "start_date",
        "end_date": "end_date",
        "status": "end_date",
        "payment_rate": "id",
    }
    contracts = Contract.objects.filter(is_deleted=False)
    if keyword:
        contracts = contracts.filter(
            Q(contract_name__icontains=keyword)
            | Q(contract_number__icontains=keyword)
            | Q(party_name__icontains=keyword)
        )
    if sort in sort_fields:
        prefix = "-" if direction == "desc" else ""
        contracts = contracts.order_by(f"{prefix}{sort_fields[sort]}", "id")

    total_amount = contracts.aggregate(total=Sum("amount"))["total"] or 0
    contract_count = contracts.count()
    contracts = list(contracts)
    if sort == "payment_rate":
        contracts.sort(key=lambda item: item.payment_rate, reverse=direction == "desc")
    context = context_with_auth(
        request,
        {
            "contracts": contracts,
            "keyword": keyword,
            "sort": sort,
            "direction": direction,
            "total_amount": total_amount,
            "contract_count": contract_count,
            "expiring_contracts": expiring_contract_queryset(),
            "active_nav": "contracts",
        },
    )
    return render(request, "contracts/contract_list.html", context)


# 渲染单个合同详情页面。
def contract_detail(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if is_normal_mode(request):
        return redirect("contracts:maintenance_record_list", pk=contract.pk)
    primary_file = contract.latest_file
    context = context_with_auth(
        request,
        {
            "contract": contract,
            "contract_files": contract.files.all(),
            "primary_file": primary_file,
            "maintenance_records": contract.maintenancerecord_set.all(),
            "invoice_records": contract.invoicerecord_set.all(),
            "payment_records": contract.paymentrecord_set.all(),
            "active_nav": "contracts",
        },
    )
    return render(request, "contracts/contract_detail.html", context)


@admin_required
def contract_remark_update(request, pk: int):
    # 详情页允许直接补充或修改合同备注，不必进入完整编辑表单。
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method == "POST":
        contract.remark = request.POST.get("remark", "").strip()
        contract.save(update_fields=["remark", "updated_at"])
    next_url = request.POST.get("next") or reverse("contracts:contract_detail", args=[contract.pk])
    return redirect(next_url)


RECORD_MODEL_MAP = {
    "invoice": InvoiceRecord,
    "payment": PaymentRecord,
    "maintenance": MaintenanceRecord,
}


def record_model_for_kind(kind: str):
    # 前端提交的记录来源标记会映射到具体模型。
    return RECORD_MODEL_MAP.get(kind)


@admin_required
def record_delete(request, pk: int):
    # 详情页三类记录共用同一个删除入口，前端用 kind:id 标记来源表。
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method != "POST":
        return redirect("contracts:contract_detail", pk=contract.pk)
    next_url = request.POST.get("next", "")
    for record_key in request.POST.getlist("record_ids"):
        try:
            kind, raw_id = record_key.split(":", 1)
            record_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        record_model = record_model_for_kind(kind)
        if record_model is None:
            continue
        try:
            record = record_model.objects.get(pk=record_id, contract=contract)
        except record_model.DoesNotExist:
            continue
        delete_file_from_storage(record.file)
        record.delete()
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("contracts:contract_detail", pk=contract.pk)


@admin_required
def record_file_update(request, kind: str, pk: int):
    # 单条记录只保留一个附件，新上传文件会覆盖并删除旧文件。
    record_model = record_model_for_kind(kind)
    if record_model is None:
        return redirect("contracts:contract_list")
    record = get_object_or_404(record_model, pk=pk, contract__is_deleted=False)
    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        if uploaded_file:
            delete_file_from_storage(record.file)
            record.file = uploaded_file
            record.save(update_fields=["file", "updated_at"])
    next_url = request.POST.get("next", "")
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("contracts:contract_detail", pk=record.contract_id)


@admin_required
def record_remark_update(request, kind: str, pk: int):
    # 详情页记录备注允许原位编辑，保存后回到当前列表位置。
    record_model = record_model_for_kind(kind)
    if record_model is None:
        return redirect("contracts:contract_list")
    record = get_object_or_404(record_model, pk=pk, contract__is_deleted=False)
    if request.method == "POST":
        record.remark = request.POST.get("remark", "").strip()
        record.save(update_fields=["remark", "updated_at"])
    next_url = request.POST.get("next", "")
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("contracts:contract_detail", pk=record.contract_id)


def maintenance_record_list(request, pk: int):
    # 所有合同类型的扩展记录共用 MaintenanceRecord 表和同一个列表模板。
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    record_label = f"{contract.contract_type}记录"
    context = context_with_auth(
        request,
        {
            "contract": contract,
            "record_label": record_label,
            "primary_file": contract.latest_file,
            "maintenance_records": contract.maintenancerecord_set.all(),
            "active_nav": "contracts",
        },
    )
    return render(request, "contracts/maintenance_record_list.html", context)


@admin_required
# 新增合同并保存随表单上传的合同文件。
def contract_create(request):
    if request.method == "POST":
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            contract = form.save()
            save_contract_files(contract, request.FILES.getlist("files"))
            return redirect("contracts:contract_list")
    else:
        today = timezone.localdate()
        form = ContractForm(
            initial={
                "contract_number": default_contract_number(),
                "contract_type": "维保",
                "sign_date": today,
                "start_date": today,
            }
        )
    return render(
        request,
        "contracts/contract_form.html",
        context_with_auth(
            request,
            {
                "form": form,
                "title": "新增合同",
                "contract_files": [],
                "default_contract_number": default_contract_number(),
                "active_nav": "contracts",
            },
        ),
    )


@admin_required
def settlement_file_list(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method == "POST":
        if request.POST.get("action") == "delete_files":
            for item in SettlementFile.objects.filter(id__in=request.POST.getlist("delete_files"), contract=contract):
                delete_file_from_storage(item.file)
                item.delete()
            return redirect("contracts:settlement_file_list", pk=contract.pk)
        for item in request.FILES.getlist("files"):
            SettlementFile.objects.create(contract=contract, file=item, original_name=item.name)
        return redirect("contracts:settlement_file_list", pk=contract.pk)
    return render(
        request,
        "contracts/settlement_files.html",
        context_with_auth(
            request,
            {
                "contract": contract,
                "settlement_files": contract.settlement_files.all(),
                "active_nav": "contracts",
            },
        ),
    )


@admin_required
def settlement_file_preview(request, pk: int):
    item = get_object_or_404(SettlementFile, pk=pk, contract__is_deleted=False)
    return render(
        request,
        "contracts/file_preview.html",
        context_with_auth(
            request,
            {
                "contract": item.contract,
                "file_name": item.original_name or Path(item.file.name).name,
                "file_url": item.file.url,
                "preview_type": preview_type_for_file(item.file.name),
                "return_url": reverse("contracts:settlement_file_list", args=[item.contract_id]),
                "delete_url": reverse("contracts:settlement_file_delete", args=[item.id]),
                "active_nav": "contracts",
            },
        ),
    )


@admin_required
def settlement_file_delete(request, pk: int):
    item = get_object_or_404(SettlementFile, pk=pk, contract__is_deleted=False)
    contract_id = item.contract_id
    if request.method == "POST":
        delete_file_from_storage(item.file)
        item.delete()
    return redirect("contracts:settlement_file_list", pk=contract_id)


@admin_required
# 处理合同编辑页中的即时文件上传请求。
def contract_file_upload(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method != "POST":
        return JsonResponse({"error": "只允许上传文件。"}, status=405)

    uploaded_files = request.FILES.getlist("files")
    replace_existing = bool(uploaded_files and AppSetting.current().delete_source_file)
    saved_files = save_contract_files_and_return(contract, uploaded_files)
    return JsonResponse(
        {
            "replace": replace_existing,
            "files": [
                {
                    "id": item.id,
                    "name": item.original_name or item.file.name,
                    "url": item.file.url,
                    "preview_url": f"{reverse('contracts:contract_file_preview', args=[item.id])}?from=edit",
                }
                for item in saved_files
            ]
        }
    )


@admin_required
# 保存编辑页拖拽后的合同文件顺序。
def contract_file_reorder(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method != "POST":
        return JsonResponse({"error": "只允许保存排序。"}, status=405)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "排序数据格式不正确。"}, status=400)
    file_ids = payload.get("file_ids", [])
    valid_ids = set(contract.files.values_list("id", flat=True))
    ordered_ids = [int(item) for item in file_ids if str(item).isdigit() and int(item) in valid_ids]
    for index, file_id in enumerate(ordered_ids):
        ContractFile.objects.filter(id=file_id, contract=contract).update(sort_order=index)
    return JsonResponse({"ok": True})


@admin_required
# 编辑合同基础信息，并处理批量删除合同文件。
def contract_update(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    old_file = contract.file
    if request.method == "POST":
        if request.POST.get("action") == "delete_files":
            delete_contract_files(request.POST.getlist("delete_files"))
            return redirect("contracts:contract_update", pk=contract.pk)

        form = ContractForm(request.POST, request.FILES, instance=contract)
        if form.is_valid():
            updated = form.save()
            if "file" in request.FILES and old_file and old_file != updated.file:
                delete_file_from_storage(old_file)
            save_contract_files(updated, request.FILES.getlist("files"))
            return redirect("contracts:contract_list")
    else:
        form = ContractForm(instance=contract)
    return render(
        request,
        "contracts/contract_form.html",
        context_with_auth(
            request,
            {
                "form": form,
                "title": "编辑合同",
                "contract": contract,
                "contract_files": contract.files.all(),
                "default_contract_number": default_contract_number(),
                "active_nav": "contracts",
            },
        ),
    )


@admin_required
# 将合同移入回收站，一周内可从回收站恢复。
def contract_delete(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method == "POST":
        contract.move_to_trash()
        return redirect("contracts:contract_list")
    return render(
        request,
        "contracts/contract_confirm_delete.html",
        context_with_auth(request, {"contract": contract, "active_nav": "contracts"}),
    )


@admin_required
# 根据合同是否开票，进入开票或收票记录新增入口。
def record_add(request, pk: int):
    # 现在“添加记录”只负责进入合同类型扩展记录表单。
    get_object_or_404(Contract, pk=pk, is_deleted=False)
    return redirect("contracts:maintenance_record_create", pk=pk)


@money_record_required
# 新增一批开票记录。
def invoice_record_create(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if contract.invoice_status == "不开票":
        return redirect("contracts:contract_list")

    if request.method == "POST":
        if save_typed_records_from_request(request, contract, {"开票": InvoiceRecord, "收票": PaymentRecord}):
            return redirect("contracts:contract_list")
    return render(
        request,
        "contracts/record_form.html",
        context_with_auth(
            request,
            {
                "contract": contract,
                "title": "新增发票记录",
                "today": timezone.localdate(),
                "record_type_options": ["开票", "收票"],
                "amount_label": "票面金额",
                "actual_amount_field": True,
                "file_label": "发票文件",
                "active_nav": "contracts",
            },
        ),
    )


@money_record_required
# 新增一批收票记录。
def payment_record_create(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if contract.invoice_status != "不开票":
        return redirect("contracts:record_add", pk=pk)
    if request.method == "POST":
        if save_typed_records_from_request(request, contract, {"开据": PaymentRecord, "收据": PaymentRecord}):
            return redirect("contracts:contract_list")
    return render(
        request,
        "contracts/record_form.html",
        context_with_auth(
            request,
            {
                "contract": contract,
                "title": "新增收据记录",
                "today": timezone.localdate(),
                "record_type_options": ["开据", "收据"],
                "amount_label": "票面金额",
                "actual_amount_field": True,
                "file_label": "收据文件",
                "active_nav": "contracts",
            },
        ),
    )


@admin_required
# 新增一批维护保养记录。
def maintenance_record_create(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method == "POST":
        if save_maintenance_records_from_request(request, contract):
            return redirect("contracts:contract_list")
    record_titles = {
        "维保": "新增维保记录",
        "评估": "新增评估记录",
        "检测": "新增检测记录",
        "改造": "新增改造记录",
        "新建": "新增新建记录",
        "其他": "新增其他记录",
    }
    return render(
        request,
        "contracts/record_form.html",
        context_with_auth(
            request,
            {
                "contract": contract,
                "title": record_titles.get(contract.contract_type, f"新增{contract.contract_type}记录"),
                "today": timezone.localdate(),
                "month_field": True,
                "form_kind": "maintenance",
                "current_month": timezone.localdate().strftime("%Y-%m"),
                "file_label": f"{contract.contract_type}文件",
                "active_nav": "contracts",
            },
        ),
    )


# 显示回收站合同，超过一周的删除项会先自动清理。
def trash_list(request):
    purge_expired_trash()
    trashed_contracts = Contract.objects.filter(is_deleted=True).order_by("-deleted_at", "-id")
    return render(
        request,
        "contracts/trash.html",
        context_with_auth(
            request,
            {
                "contracts": trashed_contracts,
                "retention_days": TRASH_RETENTION_DAYS,
                "active_nav": "trash",
            },
        ),
    )


@admin_required
# 从回收站恢复合同。
def contract_restore(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=True)
    if request.method == "POST":
        contract.restore_from_trash()
    return redirect("contracts:trash")


@admin_required
# 显示和保存系统设置。
def settings_view(request):
    setting = AppSetting.current()
    host_ip = local_ip_address()
    if request.method == "POST":
        form = AppSettingForm(request.POST, instance=setting)
        if form.is_valid():
            form.save()
            return redirect("contracts:settings")
    else:
        form = AppSettingForm(instance=setting)
    return render(
        request,
        "contracts/settings.html",
        context_with_auth(
            request,
            {
                "form": form,
                "host_ip": host_ip,
                "lan_url": f"http://{host_ip}:8000",
                "active_nav": "settings",
            },
        ),
    )


@true_admin_required
# 修改当前管理员账号的登录密码。
def password_change(request):
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            return redirect("contracts:settings")
    else:
        form = PasswordChangeForm(request.user)
    return render(
        request,
        "contracts/password_change.html",
        context_with_auth(request, {"form": form, "active_nav": "settings"}),
    )


# 处理用户账号密码登录，并按账号身份自动进入管理员或普通用户模式。
def login_view(request):
    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            user = authenticate(
                request,
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password"],
            )
            if user is None:
                form.add_error(None, "账号或密码不正确。")
            else:
                login(request, user)
                request.session["guest_mode"] = False
                request.session["normal_mode"] = not user.is_staff
                return redirect("contracts:dashboard")
    else:
        form = LoginForm()
    return render(request, "contracts/login.html", {"form": form})


# 直接进入游客模式，不需要填写账号和密码。
def guest_login_view(request):
    logout(request)
    request.session["guest_mode"] = True
    request.session["normal_mode"] = False
    return redirect("contracts:dashboard")


# 普通用户现在使用账号密码登录，保留旧地址用于回到登录页。
def normal_login_view(request):
    return redirect("contracts:login")


# 退出当前登录或游客会话。
def logout_view(request):
    logout(request)
    request.session.flush()
    return redirect("contracts:login")
