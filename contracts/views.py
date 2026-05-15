import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps
from html import escape
import io
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import zipfile
import xml.etree.ElementTree as ET

from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
import reversion
from reversion.models import Revision, Version

from .forms import (
    AppSettingForm,
    ContractForm,
    ContractImportUploadForm,
    LoginForm,
    default_contract_number,
    default_contract_numbers,
)
from .labels import UI_LABELS, invoice_mode_labels, project_record_labels
from .models import (
    AppSetting,
    Contract,
    ContractFile,
    InvoiceRecord,
    InvoiceRecordFileVersion,
    MaintenanceRecord,
    MaintenanceRecordFileVersion,
    OperationLog,
    PaymentRecord,
    PaymentRecordFileVersion,
    SettlementFile,
    normalize_contract_number_part,
    normalize_storage_location_number,
    safe_project_folder_name,
    safe_text_folder_name,
)

STAT_START_DATE = date(2018, 1, 2)
STAT_START_MONTH = date(2018, 1, 1)
STAT_START_YEAR = 2018
FULL_DAILY_WINDOW_DAYS = 200
MONTHLY_WINDOW_MONTHS = 36


# 回收站内合同默认保留 7 天。
TRASH_RETENTION_DAYS = 7
RECORD_FILE_VERSION_LIMIT = 10
SUPER_ADMIN_USERNAME = "superuser"
SUPER_ADMIN_PASSWORD = "superuser123"
ROLE_GROUPS = {
    "管理员": "对应项目中的超级管理员：拥有合同应用和账号权限，实际后台入口仍只允许 superuser 进入。",
    "财务": "对应项目中的管理员：可维护合同、票据、收付款、结算和设置等业务数据。",
    "资料": "对应项目中的普通用户：可维护合同、合同文件、结算文件和项目记录，不分配财务记录权限。",
    "职员": "对应项目中的游客：只读查看合同、文件和项目记录。",
}
# 记录类型到收入/支出方向的映射，统计图和总览卡片都依赖这里归类。
INCOME_TYPES = {"开票", "开据"}
EXPENSE_TYPES = {"收票", "收据"}
TYPE_SIDE = {
    "开票": "income",
    "开据": "income",
    "收票": "expense",
    "收据": "expense",
}


# 保留内部返回地址，避免保存后丢失列表筛选、排序和滚动恢复参数。
def safe_internal_path(value: str, fallback: str) -> str:
    return value if value and value.startswith("/") and not value.startswith("//") else fallback


# 给已有 URL 合并查询参数，用于返回列表时恢复选中行和滚动位置。
def merge_query_params(url: str, params: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({key: value for key, value in params.items() if value})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


# 从列表进入子页面时统一读取返回状态，供保存、取消和返回按钮复用。
def list_return_state(request, contract_id: int | str | None = None) -> dict:
    fallback_url = reverse("contracts:contract_list")
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    scroll_position = request.POST.get("scroll") or request.GET.get("scroll") or ""
    return_id = request.POST.get("return_id") or request.GET.get("return_id") or str(contract_id or "")
    return_url = merge_query_params(
        safe_internal_path(next_url, fallback_url),
        {"restore_scroll": scroll_position, "return_id": return_id},
    )
    return {
        "next_url": next_url,
        "scroll_position": scroll_position,
        "return_id": return_id,
        "return_url": return_url,
    }


# 重定向到另一个合同子页面时保留来源列表状态。
def redirect_with_current_query(request, url: str):
    query_string = request.GET.urlencode()
    return redirect(f"{url}?{query_string}" if query_string else url)


# 解析表单日期字符串，统一兼容斜杠和短横线格式。
def parse_form_date(value):
    if not value:
        return None
    return parse_date(str(value).strip().replace("/", "-"))


# 返回指定日期所在月份的第一天，默认使用今天。
def current_month_start(today=None):
    today = today or timezone.localdate()
    return today.replace(day=1)


# 生成起止日期之间的每日统计单位。
def daily_units(start_date, end_date):
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return [start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1)]


# 生成从系统统计起始年到目标年的年度统计单位。
def yearly_units_until(year: int) -> list[int]:
    return list(range(STAT_START_YEAR, max(year, STAT_START_YEAR) + 1))


# 在日期上增减月份，并自动处理月底天数溢出。
def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


# 返回指定日期所在月份的最后一天。
def month_end(value: date) -> date:
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


# 将统计年月限制在系统起始时间和当前月份之间。
def capped_period_month(year: int, month: int | None = None) -> date:
    today = timezone.localdate()
    end_year = max(year, STAT_START_YEAR)
    end_month = month or (today.month if end_year == today.year else 12)
    if end_year >= today.year:
        end_year = today.year
        end_month = min(end_month, today.month)
    end_month = min(max(end_month, 1), 12)
    return date(end_year, end_month, 1)


# 生成从系统起始月到目标月份的月度统计单位。
def monthly_units_until(year: int, month: int | None = None) -> list[date]:
    end = capped_period_month(year, month)
    units = []
    current = STAT_START_MONTH
    while current <= end:
        units.append(current)
        current = add_months(current, 1)
    return units


# 生成用于趋势图显示的固定长度月度窗口。
def monthly_units_window(year: int, month: int | None = None) -> list[date]:
    end = capped_period_month(year, month)
    start = max(STAT_START_MONTH, add_months(end, -(MONTHLY_WINDOW_MONTHS - 1)))
    units = []
    current = start
    while current <= end:
        units.append(current)
        current = add_months(current, 1)
    return units


# 生成按日统计时使用的日期窗口。
def daily_window_for_period(year: int, month: int) -> tuple[date, date]:
    today = timezone.localdate()
    end_month = capped_period_month(year, month)
    end = min(month_end(end_month), today)
    start = max(STAT_START_DATE, end - timedelta(days=FULL_DAILY_WINDOW_DAYS - 1))
    return start, end


# 将统计周期和目标月份封装成查询参数。
def period_month_params(period: str, value: date) -> str:
    return f"period={period}&year={value.year}&month={value.month}"


# 取得单个合同统计的起始日期，缺省时使用系统起始日。
def contract_stat_start_date(contract: Contract) -> date:
    return contract.start_date or STAT_START_DATE


# 取得单个合同统计的起始月份。
def contract_stat_start_month(contract: Contract) -> date:
    start = contract_stat_start_date(contract)
    return date(start.year, start.month, 1)


# 生成单个合同可用的年度统计单位。
def contract_yearly_units_until(contract: Contract, year: int) -> list[int]:
    start_year = contract_stat_start_date(contract).year
    return list(range(start_year, max(year, start_year) + 1))


# 生成单个合同趋势图使用的月度窗口。
def contract_monthly_units_window(contract: Contract, year: int, month: int | None = None) -> list[date]:
    contract_start = contract_stat_start_month(contract)
    end = max(capped_period_month(year, month), contract_start)
    start = max(contract_start, add_months(end, -(MONTHLY_WINDOW_MONTHS - 1)))
    units = []
    current = start
    while current <= end:
        units.append(current)
        current = add_months(current, 1)
    return units


# 生成单个合同按日统计时使用的日期窗口。
def contract_daily_window_for_period(contract: Contract, year: int, month: int) -> tuple[date, date]:
    today = timezone.localdate()
    contract_start = contract_stat_start_date(contract)
    end_month = capped_period_month(year, month)
    end = min(month_end(end_month), today)
    if end < contract_start:
        end = contract_start
    start = max(contract_start, end - timedelta(days=FULL_DAILY_WINDOW_DAYS - 1))
    return start, end


# 从请求参数解析单个合同统计图的日期范围。
def contract_chart_range_from_request(request, contract: Contract):
    period, year, month, _, _, _ = chart_range_from_request(request)
    today = timezone.localdate()
    contract_start = contract_stat_start_date(contract)
    start_month = contract_stat_start_month(contract)
    current_month = current_month_start(today)

    if period == "full":
        start, end = contract_daily_window_for_period(contract, year, month)
        prev_month = add_months(date(end.year, end.month, 1), -6)
        next_month = add_months(date(end.year, end.month, 1), 6)
        prev_target = max(prev_month, start_month)
        next_target = min(next_month, current_month)
        can_prev = start > contract_start
        can_next = end < today
        return {
            "period": period,
            "year": year,
            "month": month,
            "range_label": f"{start.strftime('%Y-%m-%d')} - {end.strftime('%Y-%m-%d')}",
            "prev_params": period_month_params("full", prev_target),
            "next_params": period_month_params("full", next_target),
            "can_prev": can_prev,
            "can_next": can_next,
        }

    if period == "all":
        return {
            "period": period,
            "year": year,
            "month": month,
            "range_label": "当月统计",
            "prev_params": "period=all",
            "next_params": "period=all",
            "can_prev": False,
            "can_next": False,
        }

    if period == "year":
        units = contract_yearly_units_until(contract, year)
        current_year = today.year
        can_prev = units[0] > contract_start.year
        can_next = units[-1] < current_year
        return {
            "period": period,
            "year": year,
            "month": month,
            "range_label": f"{units[0]} - {units[-1]} 年",
            "prev_params": f"period=year&year={max(contract_start.year, year - 1)}&month={month}",
            "next_params": f"period=year&year={min(current_year, year + 1)}&month={month}",
            "can_prev": can_prev,
            "can_next": can_next,
        }

    units = contract_monthly_units_window(contract, year, month)
    prev_month = add_months(units[-1], -MONTHLY_WINDOW_MONTHS)
    next_month = add_months(units[-1], MONTHLY_WINDOW_MONTHS)
    prev_target = max(prev_month, start_month)
    next_target = min(next_month, current_month)
    can_prev = units[0] > start_month
    can_next = units[-1] < current_month
    return {
        "period": period,
        "year": year,
        "month": month,
        "range_label": f"{units[0].strftime('%Y-%m')} - {units[-1].strftime('%Y-%m')}",
        "prev_params": period_month_params("month", prev_target),
        "next_params": period_month_params("month", next_target),
        "can_prev": can_prev,
        "can_next": can_next,
    }


# 判断当前请求是否处于管理员模式。
# 函数说明：封装可复用的业务处理。
def is_admin_mode(request) -> bool:
    return bool(
        request.user.is_authenticated
        and request.user.is_staff
        and request.user.username != SUPER_ADMIN_USERNAME
        and not request.session.get("guest_mode", False)
        and not request.session.get("normal_mode", False)
    )


# 判断当前请求是否处于超级管理员模式。
def is_super_admin_mode(request) -> bool:
    return bool(
        request.user.is_authenticated
        and request.user.is_staff
        and request.user.is_superuser
        and request.user.username == SUPER_ADMIN_USERNAME
        and request.session.get("super_admin_mode", False)
        and not request.session.get("guest_mode", False)
        and not request.session.get("normal_mode", False)
    )


# 判断当前请求是否处于普通用户模式。
# 函数说明：封装可复用的业务处理。
def is_normal_mode(request) -> bool:
    return bool(
        request.user.is_authenticated
        and not request.user.is_staff
        and request.session.get("normal_mode", False)
        and not request.session.get("guest_mode", False)
    )


# 普通用户继承管理员的大部分操作，只屏蔽发票/收据类新增入口。
def can_manage(request) -> bool:
    return is_admin_mode(request) or is_normal_mode(request) or is_super_admin_mode(request)


# 函数说明：封装可复用的业务处理。
def can_add_money_records(request) -> bool:
    return is_admin_mode(request) or is_super_admin_mode(request)


# 限制只有管理员或普通用户模式才能访问写入类页面。
def admin_required(view_func):
    # 内部函数：在调用原视图前执行权限检查。
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if can_manage(request):
            return view_func(request, *args, **kwargs)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"error": "登录状态已失效，请刷新页面后重新登录。"}, status=401)
        return redirect("contracts:login")

    return wrapper


# 发票/收据类记录只允许管理员新增，普通用户和游客都不展示也不能直连。
def money_record_required(view_func):
    # 内部函数：在调用原视图前执行权限检查。
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if can_add_money_records(request):
            return view_func(request, *args, **kwargs)
        return redirect("contracts:login")

    return wrapper


# 账号密码等真正的管理员能力不下放给普通用户。
# 函数说明：封装可复用的业务处理。
def true_admin_required(view_func):
    # 内部函数：在调用原视图前执行权限检查。
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if is_admin_mode(request) or is_super_admin_mode(request):
            return view_func(request, *args, **kwargs)
        return redirect("contracts:login")

    return wrapper


# 给模板上下文统一补充登录模式信息。
# 函数说明：封装可复用的业务处理。
def context_with_auth(request, context: dict | None = None) -> dict:
    data = context or {}
    data["is_admin_mode"] = is_admin_mode(request)
    data["is_super_admin_mode"] = is_super_admin_mode(request)
    data["is_normal_mode"] = is_normal_mode(request)
    data["is_guest_mode"] = bool(request.session.get("guest_mode", False))
    data["can_manage"] = can_manage(request)
    data["can_add_money_records"] = can_add_money_records(request)
    return data


# 根据当前会话状态返回使用文档中展示的权限名称。
def role_label_for_request(request) -> str:
    if is_super_admin_mode(request):
        return "超级管理员"
    if is_admin_mode(request):
        return "管理员"
    if is_normal_mode(request):
        return "普通用户"
    if request.session.get("guest_mode", False):
        return "游客"
    return "未登录"


# 按当前用户权限组装使用文档章节。
def usage_docs_for_request(request) -> list[dict]:
    common_read = [
        "在合同列表中搜索合同名称、合同编号、原合同编号、甲方名称或负责人。",
        "单击表格行可选中项目，双击或点击查看详情进入合同详情。",
        "在详情页查看合同基础信息、合同文件和项目记录。",
    ]
    business_sections = [
        {
            "title": "产值计算逻辑",
            "items": [
                "默认统计总览和产值趋势图显示“未来可到期产值”：按某一天查看时，只统计该日尚未到期且已有开始日期、截止日期和合同金额的合同。",
                "未来可到期产值按合同金额在开始日期至截止日期之间线性分摊；某日累计值 = 合同金额 / 合同天数 × 已经过天数。",
                "产值计算面板选择同一天作为开始和结束日期时，仍按未来可到期产值口径计算，不计入已经到期的合同。",
                "产值计算面板选择日期范围时显示“当前已完成产值”：对每个合同分别计算结束日累计产值减开始日累计产值，再将多个合同相加。",
                "范围计算不因结束日跨过合同截止日而排除该合同；此时结束日累计产值按合同总金额封顶。",
                "若范围起始日已经达到或超过合同截止日，该合同在这个范围内按 0 处理，不纳入当前已完成产值。",
            ],
        },
        {
            "title": "三种图表说明",
            "items": [
                "票据业务图用于查看开票/开据与收款/收据的业务变化，可通过图表右上角开关在发票口径和收据口径之间切换。",
                "票据业务图的发票口径展示开票金额和收款金额；收据口径展示开据金额和收据金额，数据来自票据记录的实际金额。",
                "合同金额图按年份汇总合同金额，用合同签订日期优先归属年份，没有签订日期时使用开始日期，再没有则使用创建时间。",
                "合同金额图反映合同签订规模，不等同于已开票、已收款或已完成产值。",
                "未来可到期产值图按每日展示尚未到期合同的累计可到期产值，默认作为统计总览的主图显示。",
                "未来可到期产值图只支持全部和当月统计两类日期范围；按年、按月统计按钮在该图表下会自动禁用。",
            ],
        },
        {
            "title": "发票和收据逻辑",
            "items": [
                "合同的“是否开票”决定可录入的票据类型：开收据合同使用开据和收据记录，待开票或票已结合同使用开票和收票记录。",
                "待开票表示合同还需要开票；票已结表示票据流程已完成；开收据表示该合同按收据流程管理，不走发票流程。",
                "开票/开据记录表示对外开出的票据或收据金额，收票/收据记录表示对应已收到或已回收的金额。",
                "票面金额记录票据本身金额，实际金额记录纳入统计的真实业务金额；两者互不自动覆盖。",
                "实际金额为空时按 0 保存和统计，不会自动等于票面金额，因此只填票面金额不会增加收款金额。",
                "合同列表中的开票金额、收款金额和开票未收款金额都以实际金额为基础；开票未收款金额 = 开票或开据实际金额 - 收款或收据实际金额。",
                "票据业务图、项目统计弹窗和导出 Excel 同样使用实际金额作为统计口径，票面金额主要用于明细核对。",
                "合同详情中不同票据状态会限制可用记录区域，例如开收据合同不显示开票记录区域，而按收据记录维护。",
            ],
        },
        {
            "title": "导出逻辑",
            "items": [
                "合同列表导出 Excel 会按当前搜索、筛选、排序结果导出，不会额外导出未出现在当前列表条件内的合同。",
                "统计总览导出 Excel 会按照当前图表类型和统计范围生成对应数据；票据业务导出包含开票/开据与收款/收据两类金额。",
                "单个合同统计弹窗导出 Excel 只导出该合同在所选统计范围内的票据业务数据。",
                "合同记录导出会将单个合同下的开票、收票、收据和项目记录整理到 Excel，便于移交或核对。",
                "导入模板由 openpyxl 生成，说明内容写在标题批注中，标题行以下不放示例文字，避免被误导入为正式数据。",
            ],
        },
        {
            "title": "合同金额汇总逻辑",
            "items": [
                "合同列表顶部的总金额按当前列表条件下未删除合同的合同金额汇总。",
                "进行中、即将到期、已到期、待归档和已归档等状态由合同截止日期、归档年限和归档标记共同决定。",
                "开票金额和收款金额按票据记录的实际金额统计；实际金额未填写时按 0 处理，不再自动等于票面金额。",
                "票面金额用于记录票据本身金额，实际金额用于收款、开票未收款和统计图表中的实际业务汇总。",
                "合同金额趋势图按合同签订日期优先、开始日期其次、创建时间兜底归入对应年份。",
            ],
        },
        {
            "title": "归档与回收逻辑",
            "items": [
                "合同截止日期早于当前日期时显示已到期；超过归档年限后进入待归档状态。",
                "归档时会根据存储位置编号生成归档编号，并导出一份合同快照 JSON 用于留存。",
                "归档后会清理该合同及关联记录的历史版本，减少长期数据占用。",
                "删除合同不会立即物理删除，而是移入回收站；回收站内项目可在保留期内恢复。",
                "回收站中的合同超过系统保留天数后会在访问回收站时自动清理。",
            ],
        },
        {
            "title": "导入与撤回逻辑",
            "items": [
                "合同导入按合同编号校验重复；票据和项目记录导入按合同显示编号匹配目标合同。",
                "导入页右侧可通过项目名称查找显示编号；项目名称可能重复，应以显示编号作为最终导入依据。",
                "票据导入中实际金额为空时按 0 保存；票面金额和实际金额彼此独立。",
                "合同列表标题旁的撤回按钮可单击撤回最近一次支持的操作，也可悬停预览最近 10 条并选择撤回到某一位置。",
                "撤回仅处理系统支持的新增、删除、恢复等操作；不支持的历史操作会在预览中标明。",
            ],
        },
    ]
    if is_super_admin_mode(request):
        return [
            {
                "title": "超级管理员",
                "items": [
                    "可使用合同总览、合同列表、归档项目、回收站、操作日志和设置。",
                    "可新增、编辑、删除合同，导入和导出 Excel，导出合同快照。",
                    "可添加票据、项目记录、结算文件，并管理系统设置和用户密码。",
                    "可查看和恢复回收站项目，查看所有操作日志。",
                ],
            }
        ] + business_sections
    if is_admin_mode(request):
        return [
            {
                "title": "管理员",
                "items": [
                    "可使用合同总览、合同列表、归档项目、回收站、操作日志和设置。",
                    "可新增、编辑、删除合同，导入和导出 Excel。",
                    "可添加票据、项目记录和结算文件。",
                    "可查看和恢复回收站项目，查看操作日志。",
                ],
            }
        ] + business_sections
    if is_normal_mode(request):
        return [
            {
                "title": "普通用户",
                "items": [
                    "可使用合同列表、归档项目和回收站。",
                    "可新增、编辑合同，导入合同 Excel，查看合同详情和项目记录。",
                    "不可新增票据、收付款记录、结算文件，不可进入系统设置和操作日志。",
                    "导入 Excel 仅支持合同导入，不支持票据或项目记录导入。",
                ],
            }
        ] + business_sections
    if request.session.get("guest_mode", False):
        return [
            {
                "title": "游客模式",
                "items": [
                    *common_read,
                    "可查看合同列表、合同详情和回收站。",
                    "不可新增、编辑、删除、导入或导出数据。",
                    "需要修改数据时，请先退出游客并使用正式账号登录。",
                ],
            }
        ] + business_sections
    return [
        {
            "title": "未登录",
            "items": [
                "登录后会根据账号权限显示对应功能。",
                "也可以使用游客模式查看有限的合同信息。",
            ],
        }
    ] + business_sections


# 获取请求来源 IP，优先读取代理转发头。
def client_ip_address(request) -> str | None:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR") or None


# 写入操作日志，并关联被操作对象和当前用户信息。
def log_operation(
    request,
    action: str,
    obj=None,
    object_type: str = "",
    object_name: str = "",
    object_id: str = "",
    detail: str = "",
    version_obj=None,
) -> None:
    user = request.user if request.user.is_authenticated else None
    if obj is not None:
        object_type = object_type or getattr(getattr(obj, "_meta", None), "verbose_name", obj.__class__.__name__)
        object_name = object_name or str(obj)
        object_id = object_id or str(getattr(obj, "pk", "") or "")
    history_obj = version_obj if version_obj is not None else obj
    content_type = None
    object_pk = ""
    if history_obj is not None and getattr(history_obj, "pk", None):
        content_type = ContentType.objects.get_for_model(history_obj, for_concrete_model=False)
        object_pk = str(history_obj.pk)
    ip_address = client_ip_address(request)
    if reversion.is_active():
        if user:
            reversion.set_user(user)
        comment_parts = [action, object_type, object_name, detail, f"IP: {ip_address}" if ip_address else ""]
        reversion.set_comment(" | ".join(part for part in comment_parts if part))
    OperationLog.objects.create(
        user=user,
        username=getattr(user, "username", "") if user else "游客",
        role=role_label_for_request(request),
        action=action,
        object_type=object_type,
        object_name=object_name[:255],
        object_id=object_id,
        content_type=content_type,
        object_pk=object_pk,
        detail=detail,
        ip_address=ip_address,
    )


UNDO_LIMIT = 10


# 取得当前用户最近可预览的撤回候选操作。
def undo_target_queryset_for_request(request):
    logs = OperationLog.objects.filter(is_undone=False).exclude(action="撤回")
    if request.user.is_authenticated:
        logs = logs.filter(user=request.user)
    else:
        logs = logs.filter(username="游客")
    return list(logs.order_by("-created_at", "-id")[:UNDO_LIMIT])


# 根据操作日志中的对象类型和 ID 找回原业务对象。
def undo_log_object(log: OperationLog):
    if not log.content_type_id or not log.object_pk:
        return None
    model_class = log.content_type.model_class()
    if model_class is None:
        return None
    try:
        return model_class.objects.get(pk=log.object_pk)
    except model_class.DoesNotExist:
        return None


# 将操作日志转换为撤回浮层需要的预览数据。
def undo_log_preview(log: OperationLog) -> dict:
    obj = undo_log_object(log)
    supported = False
    effect = "暂不支持撤回"
    if obj is None:
        effect = "对象已不存在"
    elif isinstance(obj, Contract):
        if log.action == "新增" and not obj.is_deleted:
            supported = True
            effect = "撤回后合同会移入回收站"
        elif log.action == "删除" and obj.is_deleted:
            supported = True
            effect = "撤回后合同会从回收站恢复"
        elif log.action == "恢复" and not obj.is_deleted:
            supported = True
            effect = "撤回后合同会重新移入回收站"
    elif log.action == "新增" and isinstance(obj, (InvoiceRecord, PaymentRecord, MaintenanceRecord)):
        supported = True
        effect = "撤回后该记录会被删除"
    return {
        "id": log.pk,
        "time": timezone.localtime(log.created_at).strftime("%m-%d %H:%M"),
        "action": log.action,
        "object_type": log.object_type,
        "object_name": log.object_name,
        "detail": log.detail,
        "supported": supported,
        "effect": effect,
    }


# 执行单条操作日志的撤回动作。
def undo_operation_log(request, log: OperationLog) -> tuple[bool, str]:
    obj = undo_log_object(log)
    if obj is None:
        return False, "最近操作的对象已不存在，无法撤回。"

    with transaction.atomic():
        if isinstance(obj, Contract):
            if log.action == "新增" and not obj.is_deleted:
                obj.move_to_trash()
                log.is_undone = True
                log.undone_at = timezone.now()
                log.save(update_fields=["is_undone", "undone_at"])
                log_operation(request, "撤回", obj, detail=f"undo log #{log.pk}: moved created contract to trash")
                return True, f"已撤回新增合同：{obj.contract_name}"
            if log.action == "删除" and obj.is_deleted:
                obj.restore_from_trash()
                log.is_undone = True
                log.undone_at = timezone.now()
                log.save(update_fields=["is_undone", "undone_at"])
                log_operation(request, "撤回", obj, detail=f"undo log #{log.pk}: restored deleted contract")
                return True, f"已撤回删除合同：{obj.contract_name}"
            if log.action == "恢复" and not obj.is_deleted:
                obj.move_to_trash()
                log.is_undone = True
                log.undone_at = timezone.now()
                log.save(update_fields=["is_undone", "undone_at"])
                log_operation(request, "撤回", obj, detail=f"undo log #{log.pk}: moved restored contract back to trash")
                return True, f"已撤回恢复合同：{obj.contract_name}"

        if log.action == "新增" and isinstance(obj, (InvoiceRecord, PaymentRecord, MaintenanceRecord)):
            contract = obj.contract
            contract_name = contract.contract_name
            delete_record_file_versions(obj)
            obj.delete()
            log.is_undone = True
            log.undone_at = timezone.now()
            log.save(update_fields=["is_undone", "undone_at"])
            log_operation(request, "撤回", contract, object_type=log.object_type, detail=f"undo log #{log.pk}: deleted created record")
            return True, f"已撤回 {contract_name} 的{log.object_type or '记录'}。"

    return False, "最近操作暂不支持撤回。"


# 视图函数：返回撤回预览并执行单步或批量撤回。
@admin_required
def undo_last_operation(request):
    logs = undo_target_queryset_for_request(request)
    if request.method == "GET":
        items = []
        blocked = False
        for index, log in enumerate(logs):
            item = undo_log_preview(log)
            blocked = blocked or not item["supported"]
            item["selectable"] = not blocked
            item["undo_count"] = index + 1
            items.append(item)
        return JsonResponse({"ok": True, "items": items})

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "只允许 GET 或 POST 撤回操作。"}, status=405)

    target_id = request.POST.get("target_id") or request.headers.get("X-Undo-Target")
    if target_id:
        target_id = int(target_id)
        target_index = next((index for index, log in enumerate(logs) if log.pk == target_id), None)
        if target_index is None:
            return JsonResponse({"ok": False, "error": "选择的撤回位置已失效，请重新打开预览。"}, status=400)
        selected_logs = logs[: target_index + 1]
        preview_items = [undo_log_preview(log) for log in selected_logs]
        if any(not item["supported"] for item in preview_items):
            return JsonResponse({"ok": False, "error": "所选位置之前包含暂不支持撤回的操作，无法一次撤回。"}, status=400)
        messages = []
        for log in selected_logs:
            ok, message = undo_operation_log(request, log)
            if not ok:
                return JsonResponse({"ok": False, "error": message}, status=400)
            messages.append(message)
        return JsonResponse({"ok": True, "message": f"已撤回 {len(messages)} 条操作。", "details": messages})

    for log in logs:
        ok, message = undo_operation_log(request, log)
        if ok:
            return JsonResponse({"ok": True, "message": message})
    return JsonResponse({"ok": False, "error": "最近 10 条操作中没有可撤回项。"}, status=400)


# 批量获取指定模型和动作对应的 Django 权限。
def permissions_for_models(codenames: list[str], actions: list[str]):
    permission_codenames = [
        f"{action}_{model_codename}"
        for model_codename in codenames
        for action in actions
    ]
    return Permission.objects.filter(
        content_type__app_label="contracts",
        content_type__model__in=codenames,
        codename__in=permission_codenames,
    )


# 确保系统内置角色组和权限绑定存在。
def ensure_role_groups():
    document_models = ["contract", "contractfile", "maintenancerecord", "settlementfile"]
    finance_models = ["contract", "contractfile", "invoicerecord", "paymentrecord", "settlementfile", "appsetting"]
    view_models = ["contract", "contractfile", "maintenancerecord", "settlementfile"]
    role_permissions = {
        "管理员": Permission.objects.filter(content_type__app_label__in=["auth", "contracts"]),
        "财务": permissions_for_models(finance_models, ["add", "change", "delete", "view"]),
        "资料": permissions_for_models(document_models, ["add", "change", "delete", "view"]),
        "职员": permissions_for_models(view_models, ["view"]),
    }
    for group_name in ROLE_GROUPS:
        group, _created = Group.objects.get_or_create(name=group_name)
        group.permissions.set(role_permissions[group_name])


# 确保内置超级管理员账号存在并刷新基础状态。
def ensure_special_superuser():
    ensure_role_groups()
    User = get_user_model()
    user, _created = User.objects.get_or_create(username=SUPER_ADMIN_USERNAME)
    changed_fields = []
    if not user.is_staff:
        user.is_staff = True
        changed_fields.append("is_staff")
    if not user.is_superuser:
        user.is_superuser = True
        changed_fields.append("is_superuser")
    if not user.is_active:
        user.is_active = True
        changed_fields.append("is_active")
    if not user.check_password(SUPER_ADMIN_PASSWORD):
        user.set_password(SUPER_ADMIN_PASSWORD)
        changed_fields.append("password")
    if changed_fields:
        user.save(update_fields=changed_fields)
    admin_group = Group.objects.filter(name="管理员").first()
    if admin_group and not user.groups.filter(pk=admin_group.pk).exists():
        user.groups.add(admin_group)
    return user


# 从磁盘上删除上传文件。
# 函数说明：封装可复用的业务处理。
def delete_file_from_storage(file_field) -> None:
    if not file_field:
        return
    try:
        path = Path(file_field.path)
    except ValueError:
        return
    if path.exists() and path.is_file():
        path.unlink()


# 函数说明：封装可复用的业务处理。
def safe_folder_name(value: str, fallback: str = "未命名项目") -> str:
    # Windows 文件夹名不能包含部分特殊字符，统一替换后再用于外部图片目录。
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "").strip(" ._")
    return safe_name or fallback


# 视图函数：处理页面请求并返回响应。
def contract_image_folder(contract: Contract) -> Path:
    # 图片查看和上传文件使用同一套相对目录：contracts/合同类型/默认合同编号。
    root_path = AppSetting.current().image_root_path.strip() or AppSetting._meta.get_field("image_root_path").default
    contract_type_folder = safe_folder_name(contract.contract_type, "未分类")
    contract_number_folder = safe_folder_name(contract.contract_number, "未编号合同")
    return Path(root_path) / "contracts" / contract_type_folder / contract_number_folder


# 函数说明：封装可复用的业务处理。
def ensure_contract_image_folder(contract: Contract) -> Path:
    # 新建合同和点击图片查看时都保证目标文件夹已经存在。
    folder = contract_image_folder(contract)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# 返回合同普通附件的存储目录。
def contract_file_folder(contract: Contract) -> Path:
    contract_type_folder = safe_text_folder_name(contract.contract_type)
    contract_number_folder = safe_project_folder_name(contract)
    return Path(settings.MEDIA_ROOT) / "contracts" / contract_type_folder / contract_number_folder


# 确保合同普通附件目录存在并返回路径。
def ensure_contract_file_folder(contract: Contract) -> Path:
    folder = contract_file_folder(contract)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# 在 Windows 中打开并尽量居中显示资源管理器窗口。
def center_windows_explorer_for_folder(folder: Path) -> None:
    if os.name != "nt":
        return

    def worker():
        try:
            import ctypes
            from ctypes import wintypes
        except ImportError:
            return

        user32 = ctypes.windll.user32
        target_title = folder.name.lower()
        if not target_title:
            return

        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        found_hwnds = []

        def enum_window(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, len(class_name))
            if class_name.value not in {"CabinetWClass", "ExploreWClass"}:
                return True
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, len(title))
            if target_title in title.value.lower():
                found_hwnds.append(hwnd)
            return True

        for _attempt in range(12):
            found_hwnds.clear()
            user32.EnumWindows(EnumWindowsProc(enum_window), 0)
            if found_hwnds:
                hwnd = found_hwnds[-1]
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                width = max(rect.right - rect.left, 900)
                height = max(rect.bottom - rect.top, 640)
                screen_width = user32.GetSystemMetrics(0)
                screen_height = user32.GetSystemMetrics(1)
                x = max((screen_width - width) // 2, 0)
                y = max((screen_height - height) // 2, 0)
                HWND_TOPMOST = -1
                HWND_NOTOPMOST = -2
                SWP_SHOWWINDOW = 0x0040
                user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height, SWP_SHOWWINDOW)
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.15)
                user32.SetWindowPos(hwnd, HWND_NOTOPMOST, x, y, width, height, SWP_SHOWWINDOW)
                return
            time.sleep(0.15)

    threading.Thread(target=worker, daemon=True).start()


# 生成带合同类型目录的预览相对路径，保持和当前文件保存规则一致。
# 根据文件类型生成预览文件名。
def typed_preview_file_name(file_field) -> Path:
    file_name = getattr(file_field, "name", "")
    if not file_name:
        return Path()

    instance = getattr(file_field, "instance", None)
    contract = getattr(instance, "contract", instance)
    parts = Path(file_name).parts
    if contract and len(parts) >= 2 and parts[0] == "contracts":
        type_folder = safe_text_folder_name(getattr(contract, "contract_type", ""))
        if parts[1] != type_folder:
            return Path("contracts") / type_folder / Path(*parts[1:])
    return Path(file_name)


# 文件统一从项目 media 目录读取，图片目录只用于“图片查看”。
# 计算原文件对应的预览文件路径。
def preview_file_path(file_field) -> Path:
    relative_name = typed_preview_file_name(file_field)
    return Path(settings.MEDIA_ROOT) / relative_name


# 从不同文件字段反查其所属合同。
def contract_for_file_field(file_field) -> Contract | None:
    instance = getattr(file_field, "instance", None)
    if instance is None:
        return None
    record = getattr(instance, "record", None)
    if record is not None:
        return getattr(record, "contract", None)
    return getattr(instance, "contract", instance if isinstance(instance, Contract) else None)


# 尝试修复旧数据中文件字段和实际文件夹不一致的问题。
def repair_file_field_path(file_field) -> bool:
    if not file_field or not getattr(file_field, "name", ""):
        return False
    current_path = preview_file_path(file_field)
    if current_path.exists():
        typed_name = typed_preview_file_name(file_field).as_posix()
        if typed_name and typed_name != file_field.name:
            file_field.name = typed_name
            instance = getattr(file_field, "instance", None)
            field_name = getattr(getattr(file_field, "field", None), "name", "")
            if instance is not None and field_name:
                instance.save(update_fields=[field_name])
        return True

    contract = contract_for_file_field(file_field)
    if contract is None:
        return False

    root = contract_file_folder(contract)
    if not root.exists():
        return False

    instance = getattr(file_field, "instance", None)
    target_names = {
        Path(file_field.name).name.lower(),
        Path(getattr(instance, "original_name", "") or "").name.lower(),
    }
    target_names.discard("")
    if not target_names:
        return False

    matches = [path for path in root.rglob("*") if path.is_file() and path.name.lower() in target_names]
    if not matches:
        return False

    chosen = max(matches, key=lambda path: path.stat().st_mtime)
    relative_name = chosen.relative_to(Path(settings.MEDIA_ROOT)).as_posix()
    file_field.name = relative_name
    field_name = getattr(getattr(file_field, "field", None), "name", "")
    if instance is not None and field_name:
        instance.save(update_fields=[field_name])
    return True


# 为合同列表批量补充文件是否存在的展示状态。
def hydrate_contract_file_status(contracts: list[Contract]) -> None:
    for contract in contracts:
        preview_file = None
        for item in contract.files.order_by("sort_order", "id"):
            if repair_file_field_path(item.file):
                preview_file = item
                break

        legacy_file_available = False
        if preview_file is None and contract.file:
            legacy_file_available = repair_file_field_path(contract.file)

        contract.preview_file = preview_file
        contract.legacy_file_available = legacy_file_available
        contract.file_is_uploaded = bool(preview_file or legacy_file_available)


# 返回文件内容给预览页，避免局域网用户直接触发浏览器下载。
# 按系统文件根目录配置返回文件预览或下载响应。
def file_response_from_setting(file_field, download: bool = False):
    if not repair_file_field_path(file_field):
        raise Http404("文件不存在或保存路径不正确。")
    file_path = preview_file_path(file_field)
    return FileResponse(open(file_path, "rb"), as_attachment=download, filename=Path(file_field.name).name)


# 保存合同附件，必要时按系统设置替换旧文件。
# 函数说明：封装可复用的业务处理。
def save_contract_files(contract: Contract, uploaded_files) -> None:
    uploaded_files = list(uploaded_files)
    next_order = contract.files.count()
    for index, item in enumerate(uploaded_files):
        ContractFile.objects.create(
            contract=contract,
            file=item,
            original_name=item.name,
            sort_order=next_order + index,
        )


# 保存合同附件并返回新建的文件对象，供即时上传接口使用。
# 函数说明：封装可复用的业务处理。
def save_contract_files_and_return(contract: Contract, uploaded_files) -> list[ContractFile]:
    uploaded_files = list(uploaded_files)
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
# 函数说明：封装可复用的业务处理。
def delete_contract_files(file_ids) -> None:
    for item in ContractFile.objects.filter(id__in=file_ids):
        delete_file_from_storage(item.file)
        item.delete()


RECORD_FILE_VERSION_MODELS = {
    InvoiceRecord: InvoiceRecordFileVersion,
    PaymentRecord: PaymentRecordFileVersion,
    MaintenanceRecord: MaintenanceRecordFileVersion,
}


# 获取记录附件版本模型。
# 根据记录实例取得对应的文件版本模型。
def record_file_version_model_for(record):
    for record_model, version_model in RECORD_FILE_VERSION_MODELS.items():
        if isinstance(record, record_model):
            return version_model
    return None


# 新增一条记录附件版本，并让记录自身指向最新版本文件。
# 给票据或项目记录追加一个文件版本。
def attach_record_file_version(record, uploaded_file):
    if not uploaded_file:
        return None
    version_model = record_file_version_model_for(record)
    if version_model is None:
        return None
    version = version_model.objects.create(
        record=record,
        file=uploaded_file,
        original_name=uploaded_file.name,
    )
    record.file = version.file.name
    record.save(update_fields=["file", "updated_at"])
    prune_record_file_versions(record)
    return version


# 只保留单条记录最近的附件版本，超出的旧版本连同磁盘文件一起清理。
# 将记录文件版本数量裁剪到系统保留上限。
def prune_record_file_versions(record, limit: int = RECORD_FILE_VERSION_LIMIT) -> None:
    version_model = record_file_version_model_for(record)
    if version_model is None:
        return
    stale_versions = list(version_model.objects.filter(record=record).order_by("-created_at", "-id")[limit:])
    for version in stale_versions:
        delete_file_from_storage(version.file)
        version.delete()


# 删除记录时清理它的所有附件版本文件。
# 删除记录关联的所有文件版本及实体文件。
def delete_record_file_versions(record) -> None:
    version_model = record_file_version_model_for(record)
    if version_model is None:
        delete_file_from_storage(record.file)
        return
    versions = list(version_model.objects.filter(record=record))
    if not versions:
        delete_file_from_storage(record.file)
        return
    for version in versions:
        delete_file_from_storage(version.file)


# 函数说明：封装可复用的业务处理。
def preview_type_for_file(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}:
        return "image"
    return "unsupported"


# 永久清理超过保留期的回收站合同和关联文件。
# 函数说明：封装可复用的业务处理。
def purge_expired_trash() -> None:
    cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
    expired_contracts = Contract.objects.filter(is_deleted=True, deleted_at__lt=cutoff)
    for contract in expired_contracts:
        delete_file_from_storage(contract.file)
        delete_contract_files(contract.files.values_list("id", flat=True))
        for record in contract.invoicerecord_set.all():
            delete_record_file_versions(record)
        for record in contract.paymentrecord_set.all():
            delete_record_file_versions(record)
        for record in contract.maintenancerecord_set.all():
            delete_record_file_versions(record)
        for item in contract.settlement_files.all():
            delete_file_from_storage(item.file)
        contract.delete()


# 从批量记录表单中读取多行开票或收票数据。
# 函数说明：封装可复用的业务处理。
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
        parsed_record_date = parse_form_date(record_date)
        if not parsed_record_date or amount == "":
            continue
        uploaded_file = request.FILES.get(f"file_{index}")
        record = record_model.objects.create(
            contract=contract,
            record_date=parsed_record_date,
            record_type=record_type,
            amount=amount,
            actual_amount=actual_amount if actual_amount not in {None, ""} else Decimal("0"),
            remark=remark,
        )
        attach_record_file_version(record, uploaded_file)
        log_operation(request, "新增", contract, object_type="票据记录", object_name=str(record), object_id=str(record.pk), version_obj=record)
        saved_count += 1
    return saved_count


# 函数说明：封装可复用的业务处理。
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
        parsed_record_date = parse_form_date(record_date)
        if not parsed_record_date or amount == "" or record_model is None:
            continue
        uploaded_file = request.FILES.get(f"file_{index}")
        record = record_model.objects.create(
            contract=contract,
            record_date=parsed_record_date,
            record_type=record_type,
            amount=amount,
            actual_amount=actual_amount if actual_amount not in {None, ""} else Decimal("0"),
            remark=remark,
        )
        attach_record_file_version(record, uploaded_file)
        log_operation(request, "新增", contract, object_type="票据记录", object_name=str(record), object_id=str(record.pk), version_obj=record)
        saved_count += 1
    return saved_count


# 函数说明：封装可复用的业务处理。
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


# 函数说明：封装可复用的业务处理。
def record_amount_for_stats(record) -> Decimal:
    # 实际/收付款金额单独统计；未填时按 0 计算，不自动等于票面金额。
    return record.actual_amount if record.actual_amount is not None else Decimal("0")


# 函数说明：封装可复用的业务处理。
def record_side(record) -> str:
    # 旧数据可能没有明确类型，按模型给出兜底方向。
    return TYPE_SIDE.get(record.record_type, "income" if isinstance(record, InvoiceRecord) else "expense")


# 函数说明：封装可复用的业务处理。
def add_income_expense(target: dict, record) -> None:
    # 将单条记录累加到按日期/年份/月度汇总的目标字典。
    amount = record_amount_for_stats(record)
    if record_side(record) == "income":
        target["income"] = target.get("income", Decimal("0")) + amount
    else:
        target["expense"] = target.get("expense", Decimal("0")) + amount


# 函数说明：封装可复用的业务处理。
def project_mode_labels(contract: Contract) -> dict:
    # 开收据的合同把“开票/收票”文案替换成“开据/收据”。
    has_invoice = contract.invoice_status != "开收据"
    return {
        "invoice_primary": "开票金额" if has_invoice else "开据金额",
        "invoice_secondary": "收款金额" if has_invoice else "收款金额",
        "invoice_rate": "开票未收款金额",
        "receipt_primary": "收票金额" if has_invoice else "收据金额",
        "receipt_secondary": "付款金额",
        "receipt_rate": "收票未付款金额",
    }


# 函数说明：封装可复用的业务处理。
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


# 函数说明：封装可复用的业务处理。
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
# 函数说明：封装可复用的业务处理。
def save_maintenance_records_from_request(request, contract: Contract) -> int:
    dates = request.POST.getlist("record_date")
    months = request.POST.getlist("month")
    storage_locations = request.POST.getlist("storage_location_number")
    remarks = request.POST.getlist("remark")
    saved_count = 0
    for index, record_date in enumerate(dates):
        month = months[index] if index < len(months) else ""
        storage_location = storage_locations[index] if index < len(storage_locations) else ""
        remark = remarks[index] if index < len(remarks) else ""
        parsed_record_date = parse_form_date(record_date)
        if not parsed_record_date:
            continue
        if not month and "-" in record_date:
            month = record_date[:7]
        if "-" in month:
            year_text, month_text = month.split("-", 1)
            month = f"{year_text}年{int(month_text):02d}月"
        uploaded_file = request.FILES.get(f"file_{index}")
        record = MaintenanceRecord.objects.create(
            contract=contract,
            record_date=parsed_record_date,
            month=month,
            storage_location_number=normalize_storage_location_number(storage_location),
            remark=remark,
        )
        attach_record_file_version(record, uploaded_file)
        log_operation(request, "新增", contract, object_type="项目记录", object_name=str(record), object_id=str(record.pk), version_obj=record)
        saved_count += 1
    return saved_count


# 生成合同类型扩展记录编号：文件编号 + 记录年份后两位 + 周期序列 + 类型编号 + 存储编号。
# 按合同、日期和存储位置生成项目记录编号。
def maintenance_record_number(contract: Contract, record_date, storage_location_number: str = "") -> str:
    record_year = record_date.year if hasattr(record_date, "year") else int(str(record_date)[:4])
    sign_year = (contract.sign_date or contract.start_date or timezone.localdate()).year
    file_number = normalize_contract_number_part(contract.original_contract_inner_number, 4)
    type_code = Contract.CONTRACT_TYPE_CODES.get(contract.contract_type, "06")
    period_sequence = f"{record_year - sign_year + 1:02d}"
    storage_location = normalize_storage_location_number(storage_location_number)
    return f"{file_number}{str(record_year)[-2:]}{period_sequence}{type_code}{storage_location}"


# 查询 30 天内即将到期的合同。
# 函数说明：封装可复用的业务处理。
def expiring_contract_queryset():
    today = timezone.localdate()
    expiring_limit = today + timedelta(days=30)
    return Contract.objects.filter(
        is_deleted=False,
        end_date__isnull=False,
        end_date__gte=today,
        end_date__lte=expiring_limit,
    ).order_by("end_date")


# 查询已经到达归档期限的合同，和即将到期项目一样按截止日期排序。
# 筛选已经到期但尚未归档的合同。
def archive_pending_contracts() -> list[Contract]:
    contracts = Contract.objects.filter(is_deleted=False, end_date__isnull=False).order_by("end_date")
    return [contract for contract in contracts if contract.status == "待归档"]


# 查询归档页合同：待归档排前，已归档在后，各自按截止日期升序。
# 获取归档页面展示的合同列表。
def archive_contracts_for_page() -> list[Contract]:
    contracts = Contract.objects.filter(is_deleted=False, end_date__isnull=False).order_by("end_date", "id")
    pending = [contract for contract in contracts if contract.status == "待归档"]
    archived = [contract for contract in contracts if contract.status == "已归档"]
    return pending + archived


# 把按日期汇总的数据转换成 SVG 折线图坐标。
# 函数说明：封装可复用的业务处理。
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
# 函数说明：封装可复用的业务处理。
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
# 函数说明：封装可复用的业务处理。
def chart_range_from_request(request):
    today = timezone.localdate()
    period = request.GET.get("period", "all")
    if period not in ("full", "all", "year", "month"):
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

    if period == "full":
        start, end = daily_window_for_period(year, month)
        prev_month = add_months(date(end.year, end.month, 1), -6)
        next_month = add_months(date(end.year, end.month, 1), 6)
        prev_params = period_month_params("full", max(prev_month, STAT_START_MONTH))
        next_params = period_month_params("full", min(next_month, current_month_start(today)))
        return period, year, month, prev_params, next_params, f"{start.strftime('%Y-%m-%d')} - {end.strftime('%Y-%m-%d')}"

    if period == "all":
        prev_params = "period=all"
        next_params = "period=all"
        return period, year, month, prev_params, next_params, "当月统计"

    if period == "year":
        prev_params = f"period=year&year={year - 1}&month={month}"
        next_params = f"period=year&year={year + 1}&month={month}"
        return period, year, month, prev_params, next_params, f"{STAT_START_YEAR} - {max(year, STAT_START_YEAR)} 年"

    prev_params = f"period=month&year={year - 1}&month={month}"
    next_params = f"period=month&year={year + 1}&month={month}"
    month_units = monthly_units_window(year, month)
    prev_month = add_months(month_units[-1], -MONTHLY_WINDOW_MONTHS)
    next_month = add_months(month_units[-1], MONTHLY_WINDOW_MONTHS)
    prev_params = period_month_params("month", max(prev_month, STAT_START_MONTH))
    next_params = period_month_params("month", min(next_month, current_month_start(today)))
    return period, year, month, prev_params, next_params, f"{month_units[0].strftime('%Y-%m')} - {month_units[-1].strftime('%Y-%m')}"


# 按统计范围汇总开票/收票记录，生成趋势图行数据。
# 函数说明：封装可复用的业务处理。
def build_chart_rows(period: str, year: int, month: int) -> list[dict]:
    invoice_queryset = InvoiceRecord.objects.filter(contract__is_deleted=False)
    payment_queryset = PaymentRecord.objects.filter(contract__is_deleted=False)
    if period == "full":
        today = timezone.localdate()
        start, end = daily_window_for_period(year, month)
        units = daily_units(start, end)
        totals_by_day = {}
        for record in invoice_queryset.filter(record_date__gte=start, record_date__lte=end):
            add_income_expense(totals_by_day.setdefault(record.record_date, {}), record)
        for record in payment_queryset.filter(record_date__gte=start, record_date__lte=end):
            add_income_expense(totals_by_day.setdefault(record.record_date, {}), record)
        return [
            {
                "label": unit.strftime("%Y-%m-%d"),
                "income": totals_by_day.get(unit, {}).get("income", Decimal("0")),
                "expense": totals_by_day.get(unit, {}).get("expense", Decimal("0")),
            }
            for unit in units
        ]

    if period == "all":
        today = timezone.localdate()
        start = current_month_start(today)
        units = daily_units(start, today)
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
        units = yearly_units_until(year)
        totals_by_unit = {}
        for record in invoice_queryset.filter(record_date__year__gte=STAT_START_YEAR, record_date__year__lte=units[-1]):
            unit = record.record_date.year
            add_income_expense(totals_by_unit.setdefault(unit, {}), record)
        for record in payment_queryset.filter(record_date__year__gte=STAT_START_YEAR, record_date__year__lte=units[-1]):
            unit = record.record_date.year
            add_income_expense(totals_by_unit.setdefault(unit, {}), record)
        return [
            {
                "label": f"{unit}年",
                "income": totals_by_unit.get(unit, {}).get("income", Decimal("0")),
                "expense": totals_by_unit.get(unit, {}).get("expense", Decimal("0")),
            }
            for unit in units
        ]

    totals_by_unit = {}
    units = monthly_units_window(year, month)
    start = units[0]
    end_unit = units[-1]
    end = date(end_unit.year, end_unit.month, calendar.monthrange(end_unit.year, end_unit.month)[1])
    for record in invoice_queryset.filter(record_date__gte=start, record_date__lte=end):
        unit = date(record.record_date.year, record.record_date.month, 1)
        add_income_expense(totals_by_unit.setdefault(unit, {}), record)
    for record in payment_queryset.filter(record_date__gte=start, record_date__lte=end):
        unit = date(record.record_date.year, record.record_date.month, 1)
        add_income_expense(totals_by_unit.setdefault(unit, {}), record)
    return [
        {
            "label": unit.strftime("%Y-%m"),
            "income": totals_by_unit.get(unit, {}).get("income", Decimal("0")),
            "expense": totals_by_unit.get(unit, {}).get("expense", Decimal("0")),
        }
        for unit in units
    ]


# 按签订年份汇总合同金额，仅用于年度总览趋势图。
# 按签订年份汇总合同金额趋势。
def yearly_signed_contract_amounts(year: int) -> list[float]:
    units = yearly_units_until(year)
    totals_by_year = {unit: Decimal("0") for unit in units}
    for contract in Contract.objects.filter(is_deleted=False):
        signed_year = (contract.sign_date or contract.start_date or contract.created_at).year
        if STAT_START_YEAR <= signed_year <= units[-1]:
            totals_by_year[signed_year] += contract.amount
    return [float(totals_by_year[unit]) for unit in units]


# 构建未来可到期产值趋势图的每日数据。
def build_production_cumulative_rows(start_date=None, end_date=None) -> list[dict]:
    today = timezone.localdate()
    start = start_date or current_month_start(today)
    end = end_date or today
    if end < start:
        start, end = end, start
    units = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    label_format = "%Y-%m-%d" if start.year != end.year else "%m-%d"
    contracts = Contract.objects.filter(
        is_deleted=False,
        amount__gt=0,
        start_date__isnull=False,
        end_date__isnull=False,
    )
    rows = []
    for unit in units:
        total = Decimal("0")
        for contract in contracts:
            contract_days = max((contract.end_date - contract.start_date).days, 0)
            if not contract_days or unit <= contract.start_date or unit > contract.end_date:
                continue
            production_days = max((unit - contract.start_date).days, 0)
            total += (contract.amount / Decimal(contract_days)) * Decimal(production_days)
        rows.append({"label": unit.strftime(label_format), "amount": total})
    return rows


# 计算单个合同在指定日期的累计完成产值。
def contract_production_value_at(contract: Contract, target_date: date) -> Decimal:
    contract_days = max((contract.end_date - contract.start_date).days, 0)
    if not contract_days or target_date <= contract.start_date:
        return Decimal("0")
    production_days = min(max((target_date - contract.start_date).days, 0), contract_days)
    return (contract.amount / Decimal(contract_days)) * Decimal(production_days)


# 获取当前主机的局域网 IP，用于设置页提示其他用户访问地址。
# 函数说明：封装可复用的业务处理。
def local_ip_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


# 根据当前统计范围过滤合同和记录查询。
# 函数说明：封装可复用的业务处理。
def scoped_querysets(period: str, year: int, month: int):
    contracts = Contract.objects.filter(is_deleted=False)
    invoice_records = InvoiceRecord.objects.filter(contract__is_deleted=False)
    payment_records = PaymentRecord.objects.filter(contract__is_deleted=False)
    if period == "full":
        contracts = contracts.filter(created_at__date__gte=STAT_START_DATE)
        invoice_records = invoice_records.filter(record_date__gte=STAT_START_DATE)
        payment_records = payment_records.filter(record_date__gte=STAT_START_DATE)
    elif period == "all":
        today = timezone.localdate()
        start = current_month_start(today)
        contracts = contracts.filter(created_at__date__gte=start, created_at__date__lte=today)
        invoice_records = invoice_records.filter(record_date__gte=start, record_date__lte=today)
        payment_records = payment_records.filter(record_date__gte=start, record_date__lte=today)
    elif period == "year":
        end_year = yearly_units_until(year)[-1]
        contracts = contracts.filter(created_at__year__gte=STAT_START_YEAR, created_at__year__lte=end_year)
        invoice_records = invoice_records.filter(record_date__year__gte=STAT_START_YEAR, record_date__year__lte=end_year)
        payment_records = payment_records.filter(record_date__year__gte=STAT_START_YEAR, record_date__year__lte=end_year)
    elif period == "month":
        units = monthly_units_window(year, month)
        start = units[0]
        end_unit = units[-1]
        end = date(end_unit.year, end_unit.month, calendar.monthrange(end_unit.year, end_unit.month)[1])
        contracts = contracts.filter(created_at__date__gte=start, created_at__date__lte=end)
        invoice_records = invoice_records.filter(record_date__gte=start, record_date__lte=end)
        payment_records = payment_records.filter(record_date__gte=start, record_date__lte=end)
    return contracts, invoice_records, payment_records


# 生成总览导出使用的统计单位。
def dashboard_export_units(period: str, year: int, start_date=None, end_date=None):
    if period == "full":
        today = timezone.localdate()
        start = start_date or STAT_START_DATE
        end = end_date or today
        units = daily_units(start, end)
        labels = [unit.strftime("%Y-%m-%d") for unit in units]
        return units, labels, lambda record: record.record_date, lambda record: units[0] <= record.record_date <= units[-1]
    if period == "all":
        today = timezone.localdate()
        start = start_date or current_month_start(today)
        end = end_date or today
        units = daily_units(start, end)
        labels = [unit.strftime("%Y-%m-%d") for unit in units]
        return units, labels, lambda record: record.record_date, lambda record: units[0] <= record.record_date <= units[-1]
    if period == "year":
        units = yearly_units_until(year)
        labels = [f"{unit}年" for unit in units]
        return units, labels, lambda record: record.record_date.year, lambda record: STAT_START_YEAR <= record.record_date.year <= units[-1]
    units = monthly_units_until(year)
    end_unit = units[-1]
    end = date(end_unit.year, end_unit.month, calendar.monthrange(end_unit.year, end_unit.month)[1])
    labels = [unit.strftime("%Y-%m") for unit in units]
    return units, labels, lambda record: date(record.record_date.year, record.record_date.month, 1), lambda record: units[0] <= record.record_date <= end


# 按统计单位汇总总览导出的票据业务行。
def dashboard_project_export_rows(period: str, year: int, start_date=None, end_date=None):
    units, labels, unit_for_record, record_in_scope = dashboard_export_units(period, year, start_date, end_date)
    unit_index = {unit: index for index, unit in enumerate(units)}
    contracts = Contract.objects.filter(is_deleted=False).order_by("contract_name", "id").prefetch_related(
        "invoicerecord_set",
        "paymentrecord_set",
    )
    rows_by_sheet = {
        "开票": [],
        "收票": [],
        "开据": [],
        "收据": [],
    }
    merge_refs_by_sheet = {
        "开票": [],
        "收票": [],
        "开据": [],
        "收据": [],
    }
    row_counts_by_sheet = {
        "开票": 0,
        "收票": 0,
        "开据": 0,
        "收据": 0,
    }
    for contract in contracts:
        primary_totals = {
            "invoice": [Decimal("0") for _unit in units],
            "receipt": [Decimal("0") for _unit in units],
        }
        secondary_totals = {
            "invoice": [Decimal("0") for _unit in units],
            "receipt": [Decimal("0") for _unit in units],
        }
        records = list(contract.invoicerecord_set.all()) + list(contract.paymentrecord_set.all())
        for record in records:
            if not record_in_scope(record):
                continue
            index = unit_index.get(unit_for_record(record))
            if index is None:
                continue
            if record_side(record) == "income":
                primary_totals["invoice"][index] += record.amount
                secondary_totals["invoice"][index] += record_amount_for_stats(record)
            else:
                primary_totals["receipt"][index] += record.amount
                secondary_totals["receipt"][index] += record_amount_for_stats(record)
        invoice_sheet_name = "开据" if contract.invoice_status == "开收据" else "开票"
        receipt_sheet_name = "收据" if contract.invoice_status == "开收据" else "收票"
        first_row = 2 + row_counts_by_sheet[invoice_sheet_name]
        second_row = first_row + 1
        mode_labels = project_mode_labels(contract)
        merge_refs_by_sheet[invoice_sheet_name].append(f"A{first_row}:A{second_row}")
        rows_by_sheet[invoice_sheet_name].append([contract.contract_name, mode_labels["invoice_primary"], *primary_totals["invoice"]])
        rows_by_sheet[invoice_sheet_name].append(["", mode_labels["invoice_secondary"], *secondary_totals["invoice"]])
        row_counts_by_sheet[invoice_sheet_name] += 2

        first_row = 2 + row_counts_by_sheet[receipt_sheet_name]
        second_row = first_row + 1
        merge_refs_by_sheet[receipt_sheet_name].append(f"A{first_row}:A{second_row}")
        rows_by_sheet[receipt_sheet_name].append([contract.contract_name, mode_labels["receipt_primary"], *primary_totals["receipt"]])
        rows_by_sheet[receipt_sheet_name].append(["", mode_labels["receipt_secondary"], *secondary_totals["receipt"]])
        row_counts_by_sheet[receipt_sheet_name] += 2
    headers = ["项目名称", "金额类型", *labels]
    numeric_columns = set(range(3, len(headers) + 1))
    return headers, rows_by_sheet, merge_refs_by_sheet, numeric_columns


# 生成单个合同统计导出使用的统计单位。
def contract_stats_export_units(contract: Contract, period: str, year: int, month: int, start_date=None, end_date=None):
    if period in {"full", "all"}:
        today = timezone.localdate()
        default_start, default_end = (
            contract_daily_window_for_period(contract, year, month)
            if period == "full"
            else (max(current_month_start(today), contract_stat_start_date(contract)), today)
        )
        start = max(start_date or default_start, contract_stat_start_date(contract))
        end = end_date or default_end
        if end < start:
            start, end = end, start
        units = daily_units(start, end)
        labels = [unit.strftime("%Y-%m-%d") for unit in units]
        return units, labels, lambda record: record.record_date, lambda record: units[0] <= record.record_date <= units[-1]
    if period == "year":
        units = contract_yearly_units_until(contract, year)
        labels = [f"{unit}年" for unit in units]
        return units, labels, lambda record: record.record_date.year, lambda record: units[0] <= record.record_date.year <= units[-1]
    units = contract_monthly_units_window(contract, year, month)
    end = month_end(units[-1])
    labels = [unit.strftime("%Y-%m") for unit in units]
    return units, labels, lambda record: date(record.record_date.year, record.record_date.month, 1), lambda record: units[0] <= record.record_date <= end


# 按统计单位汇总单个合同导出的票据业务行。
def contract_stats_export_rows(contract: Contract, period: str, year: int, month: int, start_date=None, end_date=None):
    units, labels, unit_for_record, record_in_scope = contract_stats_export_units(contract, period, year, month, start_date, end_date)
    unit_index = {unit: index for index, unit in enumerate(units)}
    primary_totals = {
        "invoice": [Decimal("0") for _unit in units],
        "receipt": [Decimal("0") for _unit in units],
    }
    secondary_totals = {
        "invoice": [Decimal("0") for _unit in units],
        "receipt": [Decimal("0") for _unit in units],
    }
    records = list(contract.invoicerecord_set.all()) + list(contract.paymentrecord_set.all())
    for record in records:
        if not record_in_scope(record):
            continue
        index = unit_index.get(unit_for_record(record))
        if index is None:
            continue
        if record_side(record) == "income":
            primary_totals["invoice"][index] += record.amount
            secondary_totals["invoice"][index] += record_amount_for_stats(record)
        else:
            primary_totals["receipt"][index] += record.amount
            secondary_totals["receipt"][index] += record_amount_for_stats(record)
    labels_for_contract = project_mode_labels(contract)
    invoice_headers = ["日期", labels_for_contract["invoice_primary"], labels_for_contract["invoice_secondary"]]
    receipt_headers = ["日期", labels_for_contract["receipt_primary"], labels_for_contract["receipt_secondary"]]
    invoice_rows = [
        [label, primary_totals["invoice"][index], secondary_totals["invoice"][index]]
        for index, label in enumerate(labels)
    ]
    receipt_rows = [
        [label, primary_totals["receipt"][index], secondary_totals["receipt"][index]]
        for index, label in enumerate(labels)
    ]
    invoice_sheet_name = "开据" if contract.invoice_status == "开收据" else "开票"
    receipt_sheet_name = "收据" if contract.invoice_status == "开收据" else "收票"
    return invoice_sheet_name, invoice_headers, invoice_rows, receipt_sheet_name, receipt_headers, receipt_rows, {2, 3}


# 视图函数：导出总览统计图对应的 Excel 数据。
@true_admin_required
def dashboard_export(request):
    chart_type = request.GET.get("chart_type", "ticket").strip()
    if chart_type not in {"ticket", "contract_amount", "production_cumulative"}:
        chart_type = "ticket"
    period = request.GET.get("period", "all")
    if period not in ("full", "all", "year", "month"):
        period = "all"
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year
    if chart_type == "contract_amount":
        labels = [f"{unit}年" for unit in yearly_units_until(year)]
        rows = [[label, amount] for label, amount in zip(labels, yearly_signed_contract_amounts(year))]
        response = HttpResponse(
            build_project_stats_xlsx(
                [
                    {
                        "name": "合同金额图",
                        "xml": build_project_stats_sheet_xml(["日期", "合同金额"], rows, [], {2}),
                    }
                ]
            ),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="contract_amount_chart.xlsx"'
        return response
    start_date = parse_form_date(request.GET.get("start_date")) if period in {"full", "all"} else None
    end_date = parse_form_date(request.GET.get("end_date")) if period in {"full", "all"} else None
    if chart_type == "production_cumulative":
        if period == "full" and start_date is None:
            start_date = STAT_START_DATE
        rows = [[row["label"], row["amount"]] for row in build_production_cumulative_rows(start_date, end_date)]
        response = HttpResponse(
            build_project_stats_xlsx(
                [
                    {
                        "name": "产值累计图",
                        "xml": build_project_stats_sheet_xml(["日期", "产值累计"], rows, [], {2}),
                    }
                ]
            ),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="production_cumulative_chart.xlsx"'
        return response
    headers, rows_by_sheet, merge_refs_by_sheet, numeric_columns = dashboard_project_export_rows(
        period,
        year,
        start_date,
        end_date,
    )
    sheets = [
        {
            "name": "开票",
            "xml": build_project_stats_sheet_xml(headers, rows_by_sheet["开票"], merge_refs_by_sheet["开票"], numeric_columns),
        },
        {
            "name": "收票",
            "xml": build_project_stats_sheet_xml(headers, rows_by_sheet["收票"], merge_refs_by_sheet["收票"], numeric_columns),
        },
        {
            "name": "开据",
            "xml": build_project_stats_sheet_xml(headers, rows_by_sheet["开据"], merge_refs_by_sheet["开据"], numeric_columns),
        },
        {
            "name": "收据",
            "xml": build_project_stats_sheet_xml(headers, rows_by_sheet["收据"], merge_refs_by_sheet["收据"], numeric_columns),
        },
    ]
    response = HttpResponse(
        build_project_stats_xlsx(sheets),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="dashboard_project_stats.xlsx"'
    return response


# 视图函数：导出单个合同统计弹窗中的 Excel 数据。
@true_admin_required
def contract_stats_export(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    period = request.GET.get("period", "all")
    if period not in ("full", "all", "year", "month"):
        period = "all"
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year
    try:
        month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        month = today.month
    month = min(max(month, 1), 12)
    start_date = parse_form_date(request.GET.get("start_date")) if period in {"full", "all"} else None
    end_date = parse_form_date(request.GET.get("end_date")) if period in {"full", "all"} else None
    (
        invoice_sheet_name,
        invoice_headers,
        invoice_rows,
        receipt_sheet_name,
        receipt_headers,
        receipt_rows,
        numeric_columns,
    ) = contract_stats_export_rows(
        contract,
        period,
        year,
        month,
        start_date,
        end_date,
    )
    sheets = [
        {
            "name": invoice_sheet_name,
            "xml": build_project_stats_sheet_xml(invoice_headers, invoice_rows, [], numeric_columns, title=contract.contract_name),
        },
        {
            "name": receipt_sheet_name,
            "xml": build_project_stats_sheet_xml(receipt_headers, receipt_rows, [], numeric_columns, title=contract.contract_name),
        },
    ]
    response = HttpResponse(
        build_project_stats_xlsx(sheets),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="contract_{contract.pk}_stats.xlsx"'
    return response


# 按统计范围生成单个合同的开票/收票趋势数据。
# 函数说明：封装可复用的业务处理。
def build_contract_chart_rows(contract: Contract, period: str, year: int, month: int) -> list[dict]:
    invoice_queryset = contract.invoicerecord_set.all()
    payment_queryset = contract.paymentrecord_set.all()
    if period == "full":
        start, end = contract_daily_window_for_period(contract, year, month)
        units = daily_units(start, end)
        totals_by_day = {}
        for record in invoice_queryset.filter(record_date__gte=start, record_date__lte=end):
            add_income_expense(totals_by_day.setdefault(record.record_date, {}), record)
        for record in payment_queryset.filter(record_date__gte=start, record_date__lte=end):
            add_income_expense(totals_by_day.setdefault(record.record_date, {}), record)
        return [
            {
                "label": unit.strftime("%Y-%m-%d"),
                "income": totals_by_day.get(unit, {}).get("income", Decimal("0")),
                "expense": totals_by_day.get(unit, {}).get("expense", Decimal("0")),
            }
            for unit in units
        ]

    if period == "all":
        today = timezone.localdate()
        start = max(current_month_start(today), contract_stat_start_date(contract))
        units = daily_units(start, today)
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
        units = contract_yearly_units_until(contract, year)
        totals_by_unit = {}
        for record in invoice_queryset.filter(record_date__year__gte=units[0], record_date__year__lte=units[-1]):
            add_income_expense(totals_by_unit.setdefault(record.record_date.year, {}), record)
        for record in payment_queryset.filter(record_date__year__gte=units[0], record_date__year__lte=units[-1]):
            add_income_expense(totals_by_unit.setdefault(record.record_date.year, {}), record)
        return [
            {
                "label": f"{unit}年",
                "income": totals_by_unit.get(unit, {}).get("income", Decimal("0")),
                "expense": totals_by_unit.get(unit, {}).get("expense", Decimal("0")),
            }
            for unit in units
        ]

    totals_by_unit = {}
    units = contract_monthly_units_window(contract, year, month)
    end_unit = units[-1]
    end = date(end_unit.year, end_unit.month, calendar.monthrange(end_unit.year, end_unit.month)[1])
    for record in invoice_queryset.filter(record_date__gte=units[0], record_date__lte=end):
        add_income_expense(totals_by_unit.setdefault(date(record.record_date.year, record.record_date.month, 1), {}), record)
    for record in payment_queryset.filter(record_date__gte=units[0], record_date__lte=end):
        add_income_expense(totals_by_unit.setdefault(date(record.record_date.year, record.record_date.month, 1), {}), record)
    return [
        {
            "label": unit.strftime("%Y-%m"),
            "income": totals_by_unit.get(unit, {}).get("income", Decimal("0")),
            "expense": totals_by_unit.get(unit, {}).get("expense", Decimal("0")),
        }
        for unit in units
    ]


# 函数说明：封装可复用的业务处理。
def build_contract_mode_chart_rows(contract: Contract, period: str, year: int, month: int) -> list[dict]:
    records = list(contract.invoicerecord_set.all()) + list(contract.paymentrecord_set.all())
    if period == "full":
        start, end = contract_daily_window_for_period(contract, year, month)
        units = daily_units(start, end)
        unit_for_record = lambda record: record.record_date
        filtered_records = [record for record in records if start <= record.record_date <= end]
    elif period == "all":
        today = timezone.localdate()
        start = max(current_month_start(today), contract_stat_start_date(contract))
        units = daily_units(start, today)
        unit_for_record = lambda record: record.record_date
        filtered_records = [record for record in records if units[0] <= record.record_date <= today]
    elif period == "year":
        units = contract_yearly_units_until(contract, year)
        unit_for_record = lambda record: record.record_date.year
        filtered_records = [record for record in records if units[0] <= record.record_date.year <= units[-1]]
    else:
        units = contract_monthly_units_window(contract, year, month)
        end_unit = units[-1]
        end = date(end_unit.year, end_unit.month, calendar.monthrange(end_unit.year, end_unit.month)[1])
        unit_for_record = lambda record: date(record.record_date.year, record.record_date.month, 1)
        filtered_records = [record for record in records if units[0] <= record.record_date <= end]

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

    # 内部函数：按统计范围生成图表时间标签。
    def label_for_unit(unit):
        if period == "full":
            return unit.strftime("%Y-%m-%d")
        if period == "all":
            return unit.strftime("%m-%d")
        if period == "year":
            return f"{unit}年"
        return unit.strftime("%Y-%m")

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
# 函数说明：封装可复用的业务处理。
def build_dashboard_mode_chart_rows(invoice_records, payment_records, period: str, year: int, month: int) -> list[dict]:
    records = list(invoice_records) + list(payment_records)
    if period == "full":
        start, end = daily_window_for_period(year, month)
        units = daily_units(start, end)
        unit_for_record = lambda record: record.record_date
        filtered_records = [record for record in records if start <= record.record_date <= end]
    elif period == "all":
        today = timezone.localdate()
        start = current_month_start(today)
        units = daily_units(start, today)
        unit_for_record = lambda record: record.record_date
        filtered_records = [record for record in records if units[0] <= record.record_date <= today]
    elif period == "year":
        units = yearly_units_until(year)
        unit_for_record = lambda record: record.record_date.year
        filtered_records = [record for record in records if STAT_START_YEAR <= record.record_date.year <= units[-1]]
    else:
        units = monthly_units_window(year, month)
        end_unit = units[-1]
        end = date(end_unit.year, end_unit.month, calendar.monthrange(end_unit.year, end_unit.month)[1])
        unit_for_record = lambda record: date(record.record_date.year, record.record_date.month, 1)
        filtered_records = [record for record in records if units[0] <= record.record_date <= end]

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

    # 内部函数：按统计范围生成图表时间标签。
    def label_for_unit(unit):
        if period == "full":
            return unit.strftime("%Y-%m-%d")
        if period == "all":
            return unit.strftime("%m-%d")
        if period == "year":
            return f"{unit}年"
        return unit.strftime("%Y-%m")

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


# 视图函数：处理页面请求并返回响应。
def contract_stats_data(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    range_state = contract_chart_range_from_request(request, contract)
    period = range_state["period"]
    year = range_state["year"]
    month = range_state["month"]
    range_label = range_state["range_label"]
    chart_rows = build_contract_chart_rows(contract, period, year, month)
    mode_chart_rows = build_contract_mode_chart_rows(contract, period, year, month)
    invoice_records = contract.invoicerecord_set.all()
    payment_records = contract.paymentrecord_set.all()
    if period == "full":
        start, end = contract_daily_window_for_period(contract, year, month)
        invoice_records = invoice_records.filter(record_date__gte=start, record_date__lte=end)
        payment_records = payment_records.filter(record_date__gte=start, record_date__lte=end)
    elif period == "all":
        today = timezone.localdate()
        start = max(current_month_start(today), contract_stat_start_date(contract))
        invoice_records = invoice_records.filter(record_date__gte=start, record_date__lte=today)
        payment_records = payment_records.filter(record_date__gte=start, record_date__lte=today)
    elif period == "year":
        units = contract_yearly_units_until(contract, year)
        invoice_records = invoice_records.filter(record_date__year__gte=units[0], record_date__year__lte=units[-1])
        payment_records = payment_records.filter(record_date__year__gte=units[0], record_date__year__lte=units[-1])
    elif period == "month":
        units = contract_monthly_units_window(contract, year, month)
        end_unit = units[-1]
        end = date(end_unit.year, end_unit.month, calendar.monthrange(end_unit.year, end_unit.month)[1])
        invoice_records = invoice_records.filter(record_date__gte=units[0], record_date__lte=end)
        payment_records = payment_records.filter(record_date__gte=units[0], record_date__lte=end)
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
            "prev_params": range_state["prev_params"],
            "next_params": range_state["next_params"],
            "can_prev": range_state["can_prev"],
            "can_next": range_state["can_next"],
            "start_date": contract_stat_start_date(contract).strftime("%Y-%m-%d"),
            "labels": labels,
            "modes": {
                "invoice": {
                    "primary_total": float(mode_totals["invoice_primary"]),
                    "secondary_total": float(mode_totals["invoice_secondary"]),
                    "outstanding_total": float(mode_totals["invoice_primary"] - mode_totals["invoice_secondary"]),
                    "rate": float(income_rate),
                    "chart": {
                        "primary": [float(row["invoice_primary"]) for row in mode_chart_rows],
                        "secondary": [float(row["invoice_secondary"]) for row in mode_chart_rows],
                    },
                },
                "receipt": {
                    "primary_total": float(mode_totals["receipt_primary"]),
                    "secondary_total": float(mode_totals["receipt_secondary"]),
                    "outstanding_total": float(mode_totals["receipt_primary"] - mode_totals["receipt_secondary"]),
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
                "date": record.record_date.strftime("%Y-%m-%d"),
                "record_number": maintenance_record_number(
                    contract,
                    record.record_date,
                    record.storage_location_number,
                ),
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
                    "label": f"{month:02d}月",
                    "has_records": month in grouped_records,
                    "records": grouped_records.get(month, []),
                }
                for month in range(1, 13)
            ],
        }
    )


# 渲染合同文件预览页，避免局域网用户直接触发浏览器下载。
# 视图函数：处理页面请求并返回响应。
def contract_file_preview(request, pk: int):
    item = get_object_or_404(ContractFile, pk=pk, contract__is_deleted=False)
    file_exists = repair_file_field_path(item.file)
    return_from = request.GET.get("from")
    return_state = list_return_state(request, item.contract_id)
    if return_from == "list":
        return_url = return_state["return_url"]
    elif return_from == "edit":
        return_url = merge_query_params(
            reverse("contracts:contract_update", args=[item.contract.id]),
            {
                "next": return_state["next_url"],
                "scroll": return_state["scroll_position"],
                "return_id": return_state["return_id"],
            },
        )
    else:
        return_url = merge_query_params(
            reverse("contracts:contract_detail", args=[item.contract.id]),
            {
                "next": return_state["next_url"],
                "scroll": return_state["scroll_position"],
                "return_id": return_state["return_id"],
            },
        )
    file_content_url = reverse("contracts:configured_file_content", args=["contract", item.id])
    preview_type = preview_type_for_file(item.file.name) if file_exists else "missing"
    return render(
        request,
        "contracts/file_preview.html",
        context_with_auth(
            request,
            {
                "contract": item.contract,
                "file_item": item,
                "file_name": item.original_name or Path(item.file.name).name,
                "file_url": file_content_url if file_exists else "",
                "download_url": f"{file_content_url}?download=1" if file_exists else "",
                "preview_type": preview_type,
                "return_url": return_url,
                "delete_url": reverse("contracts:contract_file_delete", args=[item.id]),
                "active_nav": "contracts",
            },
        ),
    )


# 视图函数：处理页面请求并返回响应。
@admin_required
# 从预览页删除当前合同附件。
def contract_file_delete(request, pk: int):
    item = get_object_or_404(ContractFile, pk=pk, contract__is_deleted=False)
    contract_id = item.contract_id
    if request.method == "POST":
        object_name = item.original_name or Path(item.file.name).name
        contract_name = item.contract.contract_name
        delete_file_from_storage(item.file)
        item.delete()
        log_operation(request, "删除", object_type="合同文件", object_name=object_name, object_id=str(pk), detail=f"contract: {contract_name}", version_obj=item)
        next_url = request.POST.get("next", "")
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
    return redirect("contracts:contract_detail", pk=contract_id)


# 渲染早期单文件字段的预览页，兼容旧数据。
# 函数说明：封装可复用的业务处理。
def legacy_contract_file_preview(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if not contract.file:
        return redirect("contracts:contract_detail", pk=contract.pk)
    file_exists = repair_file_field_path(contract.file)
    return_from = request.GET.get("from")
    return_state = list_return_state(request, contract.pk)
    if return_from == "list":
        return_url = return_state["return_url"]
    elif return_from == "edit":
        return_url = merge_query_params(
            reverse("contracts:contract_update", args=[contract.id]),
            {
                "next": return_state["next_url"],
                "scroll": return_state["scroll_position"],
                "return_id": return_state["return_id"],
            },
        )
    else:
        return_url = merge_query_params(
            reverse("contracts:contract_detail", args=[contract.id]),
            {
                "next": return_state["next_url"],
                "scroll": return_state["scroll_position"],
                "return_id": return_state["return_id"],
            },
        )
    file_content_url = reverse("contracts:configured_file_content", args=["legacy", contract.id])
    preview_type = preview_type_for_file(contract.file.name) if file_exists else "missing"
    return render(
        request,
        "contracts/file_preview.html",
        context_with_auth(
            request,
            {
                "contract": contract,
                "file_name": Path(contract.file.name).name,
                "file_url": file_content_url if file_exists else "",
                "download_url": f"{file_content_url}?download=1" if file_exists else "",
                "preview_type": preview_type,
                "return_url": return_url,
                "delete_url": reverse("contracts:legacy_contract_file_delete", args=[contract.id]),
                "active_nav": "contracts",
            },
        ),
    )


# 视图函数：处理页面请求并返回响应。
@admin_required
# 从预览页删除早期单文件字段中的合同文件。
def legacy_contract_file_delete(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method == "POST" and contract.file:
        object_name = Path(contract.file.name).name
        delete_file_from_storage(contract.file)
        contract.file = None
        contract.save(update_fields=["file", "updated_at"])
        log_operation(request, "删除", contract, object_type="合同文件", object_name=object_name, detail=f"contract: {contract.contract_name}")
        next_url = request.POST.get("next", "")
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
    return redirect("contracts:contract_detail", pk=contract.pk)


# 从 media 目录读取文件内容，供 PDF、图片和下载使用。
def configured_file_content(request, kind: str, pk: int):
    download = request.GET.get("download") == "1"
    if kind == "contract":
        item = get_object_or_404(ContractFile, pk=pk, contract__is_deleted=False)
        return file_response_from_setting(item.file, download)
    if kind == "legacy":
        contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
        return file_response_from_setting(contract.file, download)
    if kind == "settlement":
        item = get_object_or_404(SettlementFile, pk=pk, contract__is_deleted=False)
        return file_response_from_setting(item.file, download)
    record_model = record_model_for_kind(kind)
    if record_model is not None:
        record = get_object_or_404(record_model, pk=pk, contract__is_deleted=False)
        return file_response_from_setting(record.file, download)
    raise Http404("文件类型不存在。")


# 渲染统计总览页面。
# 视图函数：处理页面请求并返回响应。
def production_contracts_from_dashboard_request(request) -> tuple[list[Contract], dict]:
    today = timezone.localdate()
    project_code = request.GET.get("production_project_code", "").strip()
    start_date_value = request.GET.get("production_start_date", "").strip()
    end_date_value = request.GET.get("production_end_date", "").strip()
    keyword = request.GET.get("production_q", "").strip()
    filter_contract_type = request.GET.get("production_contract_type", "").strip()
    filter_invoice_status = request.GET.get("production_invoice_status", "").strip()
    filter_status = request.GET.get("production_status", "").strip()
    filter_responsible_person = request.GET.get("production_responsible_person", "").strip()
    has_filter_values = any(
        [keyword, filter_contract_type, filter_invoice_status, filter_status, filter_responsible_person]
    )
    has_production_inputs = bool(project_code) or bool(start_date_value) or bool(end_date_value) or has_filter_values
    filter_active = has_filter_values or not has_production_inputs
    project_mode = bool(project_code) and not filter_active
    filter_mode = filter_active and not project_code
    parsed_start_date = parse_date(start_date_value) if start_date_value else None
    parsed_end_date = parse_date(end_date_value) if end_date_value else None

    contracts = Contract.objects.filter(
        is_deleted=False,
        amount__gt=0,
        start_date__isnull=False,
        end_date__isnull=False,
    )
    message = ""
    mode = "idle"

    if project_mode:
        mode = "project"
        contracts = [contract for contract in contracts if contract.display_contract_number == project_code]
        if not contracts:
            message = "未找到该项目显示编码，或项目缺少合同金额、起始日期、截止日期。"
    elif filter_mode:
        mode = "filter"
        if keyword:
            contracts = contracts.filter(
                Q(contract_name__icontains=keyword)
                | Q(contract_number__icontains=keyword)
                | Q(original_contract_folder__icontains=keyword)
                | Q(original_contract_inner_number__icontains=keyword)
                | Q(party_name__icontains=keyword)
                | Q(responsible_person__icontains=keyword)
            )

        valid_contract_types = {value for value, _ in Contract.CONTRACT_TYPES}
        valid_invoice_statuses = {value for value, _ in Contract.INVOICE_STATUS}
        if filter_contract_type in valid_contract_types:
            contracts = contracts.filter(contract_type=filter_contract_type)
        if filter_invoice_status in valid_invoice_statuses:
            contracts = contracts.filter(invoice_status=filter_invoice_status)
        if filter_responsible_person:
            contracts = contracts.filter(responsible_person__icontains=filter_responsible_person)

        status_choices = {"进行中", "即将到期", "已到期", "待归档", "已归档"}
        contracts = list(contracts)
        if not keyword and filter_status not in {"待归档", "已归档"}:
            contracts = [contract for contract in contracts if contract.status not in {"待归档", "已归档"}]
        if filter_status in status_choices:
            contracts = [contract for contract in contracts if contract.status == filter_status]
    else:
        contracts = []
        if project_code and filter_active:
            message = "项目显示编码和筛选不能同时使用。"

    contracts = list(contracts)
    if project_mode and contracts:
        effective_start_date = parsed_start_date or contracts[0].start_date
    elif filter_mode:
        effective_start_date = parsed_start_date or today
    else:
        effective_start_date = parsed_start_date or today
    effective_end_date = parsed_end_date or today

    production_rows = []
    production_total = Decimal("0")
    is_single_day_cumulative = effective_start_date == effective_end_date
    for contract in contracts:
        contract_days = max((contract.end_date - contract.start_date).days, 0)
        if not contract_days:
            continue
        if is_single_day_cumulative:
            if effective_end_date > contract.end_date:
                continue
            start_value = Decimal("0")
            end_value = contract_production_value_at(contract, effective_end_date)
            production_days = max((effective_end_date - contract.start_date).days, 0)
        else:
            if effective_start_date >= contract.end_date:
                continue
            range_start = max(contract.start_date, effective_start_date or contract.start_date)
            start_value = contract_production_value_at(contract, effective_start_date)
            end_value = contract_production_value_at(contract, effective_end_date)
            production_days = max((min(effective_end_date, contract.end_date) - range_start).days, 0)
        daily_amount = contract.amount / Decimal(contract_days)
        production_amount = max(end_value - start_value, Decimal("0"))
        if not is_single_day_cumulative and production_amount <= 0:
            continue
        production_total += production_amount
        production_rows.append(
            {
                "contract": contract,
                "daily_amount": daily_amount,
                "production_days": production_days,
                "production_amount": production_amount,
            }
        )

    if project_mode and production_rows:
        project_title = production_rows[0]["contract"].contract_name
    elif filter_mode and production_rows:
        project_title = f"已筛选 {len(production_rows)} 个项目"
    else:
        project_title = ""

    return production_rows, {
        "total": production_total,
        "count": len(production_rows),
        "project_title": project_title,
        "start_date": effective_start_date,
        "end_date": effective_end_date,
        "project_code": project_code,
        "keyword": keyword,
        "contract_type": filter_contract_type,
        "invoice_status": filter_invoice_status,
        "status": filter_status,
        "responsible_person": filter_responsible_person,
        "filter_active": has_filter_values,
        "project_mode": project_mode,
        "filter_mode": filter_mode,
        "mode": mode,
        "metric_label": "未来可到期产值" if is_single_day_cumulative else "当前已完成产值",
        "metric_mode": "future_due" if is_single_day_cumulative else "completed_range",
        "message": message,
    }


# 视图函数：渲染统计总览页面和产值计算面板。
@true_admin_required
def dashboard(request):
    purge_expired_trash()
    chart_type = request.GET.get("chart_type", "production_cumulative").strip()
    valid_chart_types = {"ticket", "contract_amount", "production_cumulative"}
    if chart_type not in valid_chart_types:
        chart_type = "production_cumulative"
    chart_period, chart_year, chart_month, prev_range_params, next_range_params, chart_range_label = chart_range_from_request(request)
    if chart_type == "contract_amount":
        chart_period = "year"
        prev_range_params = f"chart_type=contract_amount&period=year&year={chart_year - 1}&month={chart_month}"
        next_range_params = f"chart_type=contract_amount&period=year&year={chart_year + 1}&month={chart_month}"
        chart_range_label = f"{STAT_START_YEAR} - {max(chart_year, STAT_START_YEAR)} 年"
    elif chart_type == "production_cumulative":
        if chart_period not in {"full", "all"}:
            chart_period = "all"
        prev_range_params = f"chart_type=production_cumulative&{prev_range_params}"
        next_range_params = f"chart_type=production_cumulative&{next_range_params}"
    else:
        prev_range_params = f"chart_type=ticket&{prev_range_params}"
        next_range_params = f"chart_type=ticket&{next_range_params}"
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
    contract_amount_rows = yearly_signed_contract_amounts(chart_year)
    production_start_date = None
    production_end_date = None
    if chart_period == "full":
        production_start_date, production_end_date = daily_window_for_period(chart_year, chart_month)
    production_cumulative_rows = build_production_cumulative_rows(production_start_date, production_end_date)
    chart_data = {
        "labels": [row["label"] for row in mode_chart_rows],
        "chart_type": chart_type,
        "contract_amount_labels": [f"{unit}年" for unit in yearly_units_until(chart_year)],
        "contract_amounts": contract_amount_rows,
        "production_cumulative_labels": [row["label"] for row in production_cumulative_rows],
        "production_cumulative_amounts": [float(row["amount"]) for row in production_cumulative_rows],
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

    production_rows, production_summary = production_contracts_from_dashboard_request(request)
    recent_contracts = list(active_contracts.order_by("-contract_number", "-id")[:100])
    production_search_contracts = [
        {
            "name": contract.contract_name,
            "number": contract.display_contract_number,
            "party": contract.party_name,
            "responsible": contract.responsible_person,
        }
        for contract in active_contracts.order_by("-contract_number", "-id")
    ]
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
            "chart_type": chart_type,
            "chart_range_label": chart_range_label,
            "chart_export_start_date": daily_window_for_period(chart_year, chart_month)[0] if chart_period == "full" else current_month_start(),
            "chart_export_end_date": timezone.localdate(),
            "prev_range_params": prev_range_params,
            "next_range_params": next_range_params,
            "production_rows": production_rows,
            "production_summary": production_summary,
            "production_search_contracts": production_search_contracts,
            "contract_type_choices": Contract.CONTRACT_TYPES,
            "invoice_status_choices": Contract.INVOICE_STATUS,
            "status_choices": ["进行中", "即将到期", "已到期", "待归档", "已归档"],
            "expiring_contracts": expiring_contract_queryset(),
            "archive_pending_contracts": archive_pending_contracts(),
            "recent_contracts": recent_contracts,
            "active_nav": "dashboard",
        },
    )
    return render(request, "contracts/dashboard.html", context)


# 函数说明：封装可复用的业务处理。
def sort_contracts_by_number(contracts: list[Contract], direction: str, explicit_sort: bool) -> None:
    # 默认列表仍按原始编号倒序；用户点击表头时红色默认编号固定在前，其余按显示编号升降序切换。
    if explicit_sort:
        default_number_contracts = [contract for contract in contracts if contract.uses_default_display_contract_number]
        display_number_contracts = [contract for contract in contracts if not contract.uses_default_display_contract_number]
        default_number_contracts.sort(key=lambda item: item.display_contract_number, reverse=direction == "desc")
        display_number_contracts.sort(key=lambda item: item.display_contract_number, reverse=direction == "desc")
        contracts[:] = default_number_contracts + display_number_contracts
    else:
        contracts.sort(key=lambda item: item.contract_number, reverse=True)


# 函数说明：封装可复用的业务处理。
def contracts_for_list_request(request):
    # 合同列表和 Excel 导出共用这一套搜索、筛选、排序规则，避免两处结果不一致。
    keyword = request.GET.get("q", "").strip()
    filter_contract_type = request.GET.get("contract_type", "").strip()
    filter_invoice_status = request.GET.get("invoice_status", "").strip()
    filter_status = request.GET.get("status", "").strip()
    filter_responsible_person = request.GET.get("responsible_person", "").strip()
    explicit_sort = "sort" in request.GET
    sort = request.GET.get("sort", "contract_number").strip()
    direction = request.GET.get("direction", "desc").strip()
    if direction not in ("asc", "desc"):
        direction = "desc"

    sort_fields = {
        "id": "id",
        "contract_name": "contract_name",
        "contract_number": "contract_number",
        "contract_type": "contract_type",
        "party_name": "party_name",
        "responsible_person": "responsible_person",
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
            | Q(original_contract_folder__icontains=keyword)
            | Q(original_contract_inner_number__icontains=keyword)
            | Q(party_name__icontains=keyword)
            | Q(responsible_person__icontains=keyword)
        )

    valid_contract_types = {value for value, _ in Contract.CONTRACT_TYPES}
    valid_invoice_statuses = {value for value, _ in Contract.INVOICE_STATUS}
    if filter_contract_type in valid_contract_types:
        contracts = contracts.filter(contract_type=filter_contract_type)
    if filter_invoice_status in valid_invoice_statuses:
        contracts = contracts.filter(invoice_status=filter_invoice_status)
    if filter_responsible_person:
        contracts = contracts.filter(responsible_person__icontains=filter_responsible_person)

    status_choices = {"进行中", "即将到期", "已到期", "待归档", "已归档"}

    if sort in sort_fields and sort != "contract_number":
        prefix = "-" if direction == "desc" else ""
        contracts = contracts.order_by(f"{prefix}{sort_fields[sort]}", "id")

    contracts = list(contracts)
    if not keyword and filter_status not in {"待归档", "已归档"}:
        contracts = [contract for contract in contracts if contract.status not in {"待归档", "已归档"}]
    if filter_status in status_choices:
        contracts = [contract for contract in contracts if contract.status == filter_status]
    if sort == "contract_number":
        sort_contracts_by_number(contracts, direction, explicit_sort)
    if sort == "payment_rate":
        contracts.sort(key=lambda item: item.payment_rate, reverse=direction == "desc")
    return contracts


# 函数说明：封装可复用的业务处理。
def xlsx_cell_ref(row_index: int, column_index: int) -> str:
    # 把 1 开始的行列号转换为 Excel 的 A1 坐标。
    letters = ""
    while column_index:
        column_index, remainder = divmod(column_index - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row_index}"


# 函数说明：封装可复用的业务处理。
def xlsx_text_cell(row_index: int, column_index: int, value, style: int | None = None) -> str:
    # inlineStr 会让合同编号等长数字按文本显示，避免 Excel 自动转成科学计数法。
    style_attr = f' s="{style}"' if style is not None else ""
    text = escape(str(value or ""))
    return (
        f'<c r="{xlsx_cell_ref(row_index, column_index)}" t="inlineStr"{style_attr}>'
        f"<is><t>{text}</t></is></c>"
    )


# 函数说明：封装可复用的业务处理。
def xlsx_number_cell(row_index: int, column_index: int, value, style: int | None = None) -> str:
    style_attr = f' s="{style}"' if style is not None else ""
    number = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    return f'<c r="{xlsx_cell_ref(row_index, column_index)}"{style_attr}><v>{number}</v></c>'


# 函数说明：封装可复用的业务处理。
def text_display_width(value) -> int:
    # 中文字符在 Excel 中通常占用约两个英文字符宽度，用这个估算列宽更接近实际显示。
    width = 0
    for char in str(value or ""):
        width += 2 if ord(char) > 127 else 1
    return width


# 视图函数：处理页面请求并返回响应。
def contract_export_column_widths(headers, rows) -> list[int]:
    # 按标题和实际内容动态计算列宽，日期列设置最低宽度，保证中文日期完整显示。
    min_widths = [8, 18, 24, 10, 18, 12, 10, 18, 18, 12, 10, 10]
    max_widths = [10, 42, 36, 14, 34, 16, 12, 22, 22, 22, 12, 12]
    widths = []
    for column_index, header in enumerate(headers):
        candidates = [header]
        candidates.extend(row[column_index] for row in rows if column_index < len(row))
        content_width = max(text_display_width(value) for value in candidates) + 2
        min_width = min_widths[column_index] if column_index < len(min_widths) else 12
        max_width = max_widths[column_index] if column_index < len(max_widths) else 36
        widths.append(max(min_width, min(content_width, max_width)))
    return widths


# 函数说明：封装可复用的业务处理。
def build_contract_list_xlsx(headers, rows, numeric_columns: set[int] | None = None) -> bytes:
    # 使用标准库拼装最小 XLSX 包，避免依赖用户虚拟环境中未安装的 openpyxl。
    column_widths = contract_export_column_widths(headers, rows)
    cols = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(column_widths, start=1)
    )
    header_cells = "".join(
        xlsx_text_cell(1, index, header, style=1)
        for index, header in enumerate(headers, start=1)
    )
    sheet_rows = [f'<row r="1">{header_cells}</row>']
    numeric_columns = numeric_columns if numeric_columns is not None else {6}
    for row_index, row in enumerate(rows, start=2):
        cells = []
        for column_index, value in enumerate(row, start=1):
            if column_index in numeric_columns:
                cells.append(xlsx_number_cell(row_index, column_index, value, style=2))
            else:
                cells.append(xlsx_text_cell(row_index, column_index, value))
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    worksheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <cols>{cols}</cols>
  <sheetData>{"".join(sheet_rows)}</sheetData>
</worksheet>"""
    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="合同列表" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font></fonts>
  <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE9EEF5"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFCBD3DF"/></left><right style="thin"><color rgb="FFCBD3DF"/></right><top style="thin"><color rgb="FFCBD3DF"/></top><bottom style="thin"><color rgb="FFCBD3DF"/></bottom><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="2" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/></cellXfs>
</styleSheet>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as xlsx:
        xlsx.writestr("[Content_Types].xml", content_types)
        xlsx.writestr("_rels/.rels", root_rels)
        xlsx.writestr("xl/workbook.xml", workbook_xml)
        xlsx.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        xlsx.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
        xlsx.writestr("xl/styles.xml", styles_xml)
    return output.getvalue()


# 清洗 Excel 工作表名称，避免非法字符和长度超限。
def safe_xlsx_sheet_name(name: str) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", "", str(name or "Sheet")).strip() or "Sheet"
    return cleaned[:31]


# 生成项目统计工作表的 XML 内容。
def build_project_stats_sheet_xml(
    headers: list[str],
    rows: list[list],
    merge_refs: list[str],
    numeric_columns: set[int],
    title: str = "",
) -> str:
    column_widths = []
    for column_index, header in enumerate(headers, start=1):
        if column_index == 1:
            column_widths.append(34)
        elif column_index == 2:
            column_widths.append(12)
        else:
            column_widths.append(max(12, min(text_display_width(header) + 4, 18)))
    cols = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(column_widths, start=1)
    )
    header_row_index = 2 if title else 1
    data_start_index = header_row_index + 1
    sheet_rows = []
    title_merge_ref = ""
    if title:
        sheet_rows.append(f'<row r="1">{xlsx_text_cell(1, 1, title, style=1)}</row>')
        title_merge_ref = f"A1:{xlsx_cell_ref(1, len(headers))}"
    header_cells = "".join(
        xlsx_text_cell(header_row_index, index, header, style=1)
        for index, header in enumerate(headers, start=1)
    )
    sheet_rows.append(f'<row r="{header_row_index}">{header_cells}</row>')
    for row_index, row in enumerate(rows, start=data_start_index):
        cells = []
        for column_index, value in enumerate(row, start=1):
            if column_index in numeric_columns:
                cells.append(xlsx_number_cell(row_index, column_index, value, style=2))
            else:
                cells.append(xlsx_text_cell(row_index, column_index, value))
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    merge_xml = ""
    all_merge_refs = [title_merge_ref] if title_merge_ref else []
    all_merge_refs.extend(merge_refs)
    if all_merge_refs:
        merge_xml = (
            f'<mergeCells count="{len(all_merge_refs)}">'
            + "".join(f'<mergeCell ref="{ref}"/>' for ref in all_merge_refs)
            + "</mergeCells>"
        )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <cols>{cols}</cols>
  <sheetData>{"".join(sheet_rows)}</sheetData>
  {merge_xml}
</worksheet>"""


# 将多个项目统计工作表打包为 XLSX 文件。
def build_project_stats_xlsx(sheets: list[dict]) -> bytes:
    sheet_entries = []
    rel_entries = []
    content_overrides = []
    for index, sheet in enumerate(sheets, start=1):
        sheet_name = escape(safe_xlsx_sheet_name(sheet["name"]))
        sheet_entries.append(f'<sheet name="{sheet_name}" sheetId="{index}" r:id="rId{index}"/>')
        rel_entries.append(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        )
        content_overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    style_rid = len(sheets) + 1
    workbook_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{"".join(sheet_entries)}</sheets>
</workbook>"""
    workbook_rels = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {"".join(rel_entries)}
  <Relationship Id="rId{style_rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    content_types = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  {"".join(content_overrides)}
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font></fonts>
  <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE9EEF5"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFCBD3DF"/></left><right style="thin"><color rgb="FFCBD3DF"/></right><top style="thin"><color rgb="FFCBD3DF"/></top><bottom style="thin"><color rgb="FFCBD3DF"/></bottom><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="2" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/></cellXfs>
</styleSheet>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as xlsx:
        xlsx.writestr("[Content_Types].xml", content_types)
        xlsx.writestr("_rels/.rels", root_rels)
        xlsx.writestr("xl/workbook.xml", workbook_xml)
        xlsx.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        for index, sheet in enumerate(sheets, start=1):
            xlsx.writestr(f"xl/worksheets/sheet{index}.xml", sheet["xml"])
        xlsx.writestr("xl/styles.xml", styles_xml)
    return output.getvalue()


# 使用 openpyxl 生成带标题批注的导入模板。
def build_commented_import_template_xlsx(sheets: list[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)
    header_fill = PatternFill("solid", fgColor="E9EEF5")
    border_side = Side(style="thin", color="CBD3DF")
    header_border = Border(left=border_side, right=border_side, top=border_side, bottom=border_side)
    for sheet in sheets:
        worksheet = workbook.create_sheet(title=safe_xlsx_sheet_name(sheet["name"]))
        headers = sheet["headers"]
        comments = sheet.get("comments", {})
        widths = contract_export_column_widths(headers, [])
        for column_index, header in enumerate(headers, start=1):
            cell = worksheet.cell(row=1, column=column_index, value=header)
            cell.font = Font(name="Arial", size=11, bold=True)
            cell.fill = header_fill
            cell.border = header_border
            cell.alignment = Alignment(vertical="center")
            comment_text = comments.get(header)
            if comment_text:
                cell.comment = Comment(comment_text, "合同管理系统")
            worksheet.column_dimensions[cell.column_letter].width = widths[column_index - 1]
        worksheet.freeze_panes = "A2"
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


# 将模型字段值转换为可序列化的快照值。
def json_safe_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "name"):
        return value.name
    return value


# 生成单个模型对象的字段快照。
def model_snapshot(obj) -> dict:
    return {field.name: json_safe_value(getattr(obj, field.attname)) for field in obj._meta.fields}


# 生成 reversion 历史版本的快照数据。
def version_snapshot(version) -> dict:
    revision = version.revision
    return {
        "version_id": version.pk,
        "revision_id": revision.pk,
        "date_created": timezone.localtime(revision.date_created).isoformat(),
        "user": getattr(revision.user, "username", "") if revision.user else "",
        "comment": revision.comment,
        "object_repr": version.object_repr,
        "format": version.format,
        "serialized_data": version.serialized_data,
    }


# 获取某个对象保留的历史版本快照。
def versions_for_object(obj) -> list[dict]:
    versions = Version.objects.get_for_object(obj).select_related("revision", "revision__user")
    return [version_snapshot(version) for version in versions]


# 组装合同快照导出所需的完整数据载荷。
def contract_snapshot_payload(contract: Contract) -> dict:
    related_groups = contract_snapshot_related_groups(contract)
    payload = {
        "exported_at": timezone.localtime().isoformat(),
        "policy": {
            "operation_logs_online_retention": "2 years",
            "snapshots_online_retention": "12 months",
            "single_contract_export_affects_global_archive": False,
        },
        "contract": {
            "model": contract._meta.label,
            "pk": contract.pk,
            "fields": model_snapshot(contract),
            "versions": versions_for_object(contract),
        },
        "related": {},
    }
    for key, queryset in related_groups:
        payload["related"][key] = [
            {
                "model": item._meta.label,
                "pk": item.pk,
                "fields": model_snapshot(item),
                "versions": versions_for_object(item),
            }
            for item in queryset
        ]
    return payload


# 列出合同快照需要包含的关联对象分组。
def contract_snapshot_related_groups(contract: Contract) -> list[tuple[str, object]]:
    return [
        ("contract_files", contract.files.all()),
        ("settlement_files", contract.settlement_files.all()),
        ("invoice_records", contract.invoicerecord_set.all()),
        ("payment_records", contract.paymentrecord_set.all()),
        ("maintenance_records", contract.maintenancerecord_set.all()),
        ("invoice_record_file_versions", InvoiceRecordFileVersion.objects.filter(record__contract=contract)),
        ("payment_record_file_versions", PaymentRecordFileVersion.objects.filter(record__contract=contract)),
        ("maintenance_record_file_versions", MaintenanceRecordFileVersion.objects.filter(record__contract=contract)),
    ]


# 汇总合同及其关联对象用于版本清理。
def contract_snapshot_objects(contract: Contract) -> list:
    objects = [contract]
    for _, queryset in contract_snapshot_related_groups(contract):
        objects.extend(list(queryset))
    return objects


# 将合同快照写入归档 JSON 文件。
def archive_contract_snapshot_to_file(contract: Contract, reason: str) -> Path:
    payload = contract_snapshot_payload(contract)
    payload["archive_reason"] = reason
    archive_dir = settings.BASE_DIR / "archives" / "contracts"
    archive_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^0-9A-Za-z_-]+", "_", contract.contract_number or str(contract.pk)).strip("_")
    archive_path = archive_dir / f"contract_snapshot_{contract.pk}_{safe_name}_{timezone.localtime():%Y%m%d%H%M%S}.json"
    archive_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return archive_path


# 清理合同归档后不再保留的 reversion 历史版本。
def clear_contract_snapshot_versions(contract: Contract) -> int:
    deleted_count = 0
    for obj in contract_snapshot_objects(contract):
        result, _ = Version.objects.get_for_object(obj).delete()
        deleted_count += result
    Revision.objects.filter(version__isnull=True).delete()
    return deleted_count


# 渲染合同列表页面，并处理搜索和表头排序。
# 视图函数：处理页面请求并返回响应。
def contract_list(request):
    purge_expired_trash()
    keyword = request.GET.get("q", "").strip()
    filter_contract_type = request.GET.get("contract_type", "").strip()
    filter_invoice_status = request.GET.get("invoice_status", "").strip()
    filter_status = request.GET.get("status", "").strip()
    filter_responsible_person = request.GET.get("responsible_person", "").strip()
    explicit_sort = "sort" in request.GET
    sort = request.GET.get("sort", "contract_number").strip()
    direction = request.GET.get("direction", "desc").strip()
    if direction not in ("asc", "desc"):
        direction = "desc"
    sort_fields = {
        "id": "id",
        "contract_name": "contract_name",
        "contract_number": "contract_number",
        "contract_type": "contract_type",
        "party_name": "party_name",
        "responsible_person": "responsible_person",
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
            | Q(original_contract_folder__icontains=keyword)
            | Q(original_contract_inner_number__icontains=keyword)
            | Q(party_name__icontains=keyword)
            | Q(responsible_person__icontains=keyword)
        )
    valid_contract_types = {value for value, _ in Contract.CONTRACT_TYPES}
    valid_invoice_statuses = {value for value, _ in Contract.INVOICE_STATUS}
    status_choices = ["进行中", "即将到期", "已到期", "待归档", "已归档"]
    if filter_contract_type in valid_contract_types:
        contracts = contracts.filter(contract_type=filter_contract_type)
    else:
        filter_contract_type = ""
    if filter_invoice_status in valid_invoice_statuses:
        contracts = contracts.filter(invoice_status=filter_invoice_status)
    else:
        filter_invoice_status = ""
    if filter_responsible_person:
        contracts = contracts.filter(responsible_person__icontains=filter_responsible_person)
    if filter_status not in status_choices:
        filter_status = ""
    if sort in sort_fields and sort != "contract_number":
        prefix = "-" if direction == "desc" else ""
        contracts = contracts.order_by(f"{prefix}{sort_fields[sort]}", "id")

    contracts = list(contracts)
    if not keyword and filter_status not in {"待归档", "已归档"}:
        contracts = [contract for contract in contracts if contract.status not in {"待归档", "已归档"}]
    if filter_status:
        contracts = [contract for contract in contracts if contract.status == filter_status]
    summary_contracts = [contract for contract in contracts if contract.status not in {"待归档", "已归档"}]
    total_amount = sum((contract.amount for contract in summary_contracts), Decimal("0"))
    contract_count = len(summary_contracts)
    active_contracts = [contract for contract in summary_contracts if contract.status == "进行中"]
    expired_contracts = [contract for contract in summary_contracts if contract.status == "已到期"]
    active_total_amount = sum((contract.amount for contract in active_contracts), Decimal("0"))
    expired_total_amount = sum((contract.amount for contract in expired_contracts), Decimal("0"))
    summary_records = []
    for contract in summary_contracts:
        summary_records.extend(contract.invoicerecord_set.all())
        summary_records.extend(contract.paymentrecord_set.all())
    summary_mode_totals = project_mode_totals(summary_records)
    if sort == "contract_number":
        sort_contracts_by_number(contracts, direction, explicit_sort)
    if sort == "payment_rate":
        contracts.sort(key=lambda item: item.payment_rate, reverse=direction == "desc")
    hydrate_contract_file_status(contracts)
    query_params = request.GET.copy()
    query_params.pop("sort", None)
    query_params.pop("direction", None)
    context = context_with_auth(
        request,
        {
            "contracts": contracts,
            "keyword": keyword,
            "sort": sort,
            "direction": direction,
            "show_sort_indicator": explicit_sort,
            "total_amount": total_amount,
            "contract_count": contract_count,
            "active_contract_count": len(active_contracts),
            "active_total_amount": active_total_amount,
            "expired_contract_count": len(expired_contracts),
            "expired_total_amount": expired_total_amount,
            "summary_invoice_amount": summary_mode_totals["invoice_primary"],
            "summary_income_amount": summary_mode_totals["invoice_secondary"],
            "summary_invoice_unpaid_amount": summary_mode_totals["invoice_primary"] - summary_mode_totals["invoice_secondary"],
            "summary_receipt_amount": summary_mode_totals["receipt_primary"],
            "summary_payment_amount": summary_mode_totals["receipt_secondary"],
            "summary_receipt_unpaid_amount": summary_mode_totals["receipt_primary"] - summary_mode_totals["receipt_secondary"],
            "contract_type_filter": filter_contract_type,
            "invoice_status_filter": filter_invoice_status,
            "status_filter": filter_status,
            "responsible_person_filter": filter_responsible_person,
            "contract_type_choices": Contract.CONTRACT_TYPES,
            "invoice_status_choices": Contract.INVOICE_STATUS,
            "status_choices": status_choices,
            "has_filters": bool(filter_contract_type or filter_invoice_status or filter_status or filter_responsible_person),
            "query_base": query_params.urlencode(),
            "export_query": request.GET.urlencode(),
            "expiring_contracts": expiring_contract_queryset(),
            "active_nav": "contracts",
        },
    )
    return render(request, "contracts/contract_list.html", context)


# 视图函数：处理页面请求并返回响应。
def contract_list_export(request):
    # 导出结果遵循当前合同列表的搜索、筛选和排序状态。
    contracts = contracts_for_list_request(request)
    hydrate_contract_file_status(contracts)
    headers = [
        "序号",
        UI_LABELS["contract_name"],
        "默认编号",
        "显示合同编号",
        UI_LABELS["contract_type"],
        UI_LABELS["party_name"],
        UI_LABELS["contract_amount"],
        UI_LABELS["invoice_status"],
        UI_LABELS["start_date"],
        UI_LABELS["end_date"],
        UI_LABELS["responsible_person"],
        UI_LABELS["status"],
        UI_LABELS["file"],
    ]
    rows = []
    for index, contract in enumerate(contracts, start=1):
        rows.append(
            [
                index,
                contract.contract_name,
                contract.contract_number,
                contract.display_contract_number,
                contract.contract_type,
                contract.party_name,
                float(contract.amount or 0),
                contract.invoice_status,
                contract.start_date.strftime("%Y-%m-%d") if contract.start_date else "",
                contract.end_date.strftime("%Y-%m-%d") if contract.end_date else "",
                contract.responsible_person or "",
                contract.status,
                "已上传" if contract.file_is_uploaded else "未上传",
            ]
        )

    response = HttpResponse(
        build_contract_list_xlsx(headers, rows, numeric_columns={7}),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="contract_list.xlsx"'
    return response


# 生成合同记录导出文件名中的稳定标识。
def contract_record_export_key(contract: Contract) -> str:
    return contract.display_contract_number or contract.contract_number or contract.contract_name


# 导出单个合同的票据和项目记录明细。
def contract_records_export(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    contract_key = contract_record_export_key(contract)
    start_date = parse_form_date(request.GET.get("start_date"))
    end_date = parse_form_date(request.GET.get("end_date"))
    if start_date and end_date and start_date > end_date:
        start_date, end_date = end_date, start_date

    def filter_record_dates(queryset):
        if start_date:
            queryset = queryset.filter(record_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(record_date__lte=end_date)
        return queryset

    record_headers = ["合同编号", "日期", "存储编号", "备注"]
    maintenance_records = filter_record_dates(contract.maintenancerecord_set.all()).order_by("record_date", "id")
    record_rows = [
        [
            contract_key,
            record.record_date.strftime("%Y-%m-%d") if record.record_date else "",
            record.storage_location_number or "00",
            record.remark or "",
        ]
        for record in maintenance_records
    ]

    sheets = [
        {
            "name": "记录",
            "xml": build_project_stats_sheet_xml(record_headers, record_rows, [], set()),
        }
    ]
    if contract.invoice_status == "开收据":
        invoice_sheet_names = ("开据", "收据")
    else:
        invoice_sheet_names = ("开票", "收票")

    all_money_records = sorted(
        list(filter_record_dates(contract.invoicerecord_set.all()))
        + list(filter_record_dates(contract.paymentrecord_set.all())),
        key=lambda record: (record.record_date, record.id),
    )
    for sheet_name in invoice_sheet_names:
        amount_label, actual_amount_label = INVOICE_IMPORT_SHEET_LABELS[sheet_name]
        headers = ["合同编号", "日期", amount_label, actual_amount_label, "备注"]
        rows = []
        for record in all_money_records:
            if record.record_type != sheet_name:
                continue
            rows.append(
                [
                    contract_key,
                    record.record_date.strftime("%Y-%m-%d") if record.record_date else "",
                    record.amount,
                    record_amount_for_stats(record),
                    record.remark or "",
                ]
            )
        sheets.append(
            {
                "name": sheet_name,
                "xml": build_project_stats_sheet_xml(headers, rows, [], {3, 4}),
            }
        )

    response = HttpResponse(
        build_project_stats_xlsx(sheets),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="contract_{contract.pk}_records.xlsx"'
    return response


CONTRACT_IMPORT_COLUMNS = [
    ("contract_name", "合同名称"),
    ("contract_type", "合同类型"),
    ("party_name", "甲方名称"),
    ("amount", "合同金额"),
    ("invoice_status", "是否开票"),
    ("sign_date", "签订日期"),
    ("start_date", "开始日期"),
    ("end_date", "截止日期"),
    ("responsible_person", "负责人"),
    ("original_contract_folder", "文件夹编号"),
    ("original_contract_inner_number", "文件编号"),
    ("storage_location_number", "存储编号"),
    ("archive_years", "归档时间（年）"),
    ("remark", "备注"),
]
CONTRACT_IMPORT_PREVIEW_COLUMNS = [
    "序号",
    UI_LABELS["contract_name"],
    UI_LABELS["contract_number"],
    UI_LABELS["contract_type"],
    UI_LABELS["party_name"],
    UI_LABELS["contract_amount"],
    UI_LABELS["invoice_status"],
    UI_LABELS["start_date"],
    UI_LABELS["end_date"],
    UI_LABELS["responsible_person"],
    UI_LABELS["status"],
    "错误",
]
INVOICE_IMPORT_PREVIEW_COLUMNS = [
    "序号",
    UI_LABELS["contract_name"],
    UI_LABELS["contract_number"],
    "类型",
    UI_LABELS["date"],
    UI_LABELS["face_amount"],
    UI_LABELS["actual_amount"],
    UI_LABELS["remark"],
    "错误",
]
MAINTENANCE_IMPORT_PREVIEW_COLUMNS = [
    "序号",
    UI_LABELS["contract_name"],
    UI_LABELS["contract_number"],
    UI_LABELS["date"],
    "记录编号",
    "存储编号",
    UI_LABELS["remark"],
    "错误",
]
CONTRACT_IMPORT_HEADER_ALIASES = {
    "金额": "amount",
    "合同金额": "amount",
    "归档时间": "archive_years",
    "归档时间（年）": "archive_years",
    "归档年限": "archive_years",
    "原合同文件夹": "original_contract_folder",
    "文件夹编号": "original_contract_folder",
    "原合同编号": "original_contract_inner_number",
    "文件编号": "original_contract_inner_number",
    "存储位置": "storage_location_number",
    "存储编号": "storage_location_number",
    "存储位置编号": "storage_location_number",
}
INVOICE_IMPORT_TYPES = {
    "开票": InvoiceRecord,
    "收票": PaymentRecord,
    "开据": PaymentRecord,
    "收据": PaymentRecord,
}
INVOICE_IMPORT_SHEET_LABELS = {
    "开票": ("开票金额", "收款金额"),
    "收票": ("收票金额", "付款金额"),
    "开据": ("开据金额", "收款金额"),
    "收据": ("收据金额", "付款金额"),
}


# 标准化 Excel 单元格原始值。
def normalize_import_cell(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


# 按导入字段类型标准化单元格值。
def normalize_import_value(field_name: str, value):
    text = normalize_import_cell(value)
    if not text:
        return ""

    if field_name in {"sign_date", "start_date", "end_date", "record_date"}:
        normalized = text.replace("年", "/").replace("月", "/").replace("日", "")
        normalized = normalized.replace(".", "/").replace("-", "/")
        match = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", normalized)
        if match:
            year, month, day = (int(part) for part in match.groups())
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return text
        if re.fullmatch(r"\d+(\.\d+)?", text):
            serial_number = float(text)
            if serial_number >= 20000:
                try:
                    return (date(1899, 12, 30) + timedelta(days=int(serial_number))).isoformat()
                except OverflowError:
                    return text

    if field_name == "original_contract_folder":
        return normalize_contract_number_part(text, 2)

    if field_name == "original_contract_inner_number":
        return normalize_contract_number_part(text, 4)

    if field_name == "storage_location_number":
        return normalize_storage_location_number(text)

    if field_name == "archive_years":
        if re.fullmatch(r"\d+\.0+", text):
            return str(int(float(text)))

    return text


# 将 XLSX 单元格列号转换为数字序号。
def xlsx_column_number(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha()).upper()
    number = 0
    for char in letters:
        number = number * 26 + ord(char) - 64
    return number


# 提取 XLSX 富文本节点中的纯文本内容。
def xlsx_plain_text(element) -> str:
    if element is None:
        return ""
    return "".join(element.itertext())


# 解析 XLSX 单元格的真实文本值。
def xlsx_cell_value(cell, shared_strings, namespace) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return xlsx_plain_text(cell.find("x:is", namespace))
    raw_value = xlsx_plain_text(cell.find("x:v", namespace))
    if cell_type == "s" and raw_value.isdigit() and int(raw_value) < len(shared_strings):
        return shared_strings[int(raw_value)]
    return raw_value


# 从 XLSX 工作表 XML 中还原二维行数据。
def xlsx_rows_from_sheet_root(sheet_root, shared_strings, namespace) -> list[list]:
    parsed_rows = []
    for row_element in sheet_root.findall(".//x:sheetData/x:row", namespace):
        row_values = {}
        for cell in row_element.findall("x:c", namespace):
            column_number = xlsx_column_number(cell.attrib.get("r", ""))
            row_values[column_number] = xlsx_cell_value(cell, shared_strings, namespace)
        if row_values:
            max_column = max(row_values)
            parsed_rows.append([row_values.get(index, "") for index in range(1, max_column + 1)])
    return parsed_rows


# 使用标准库解析 XLSX 文件中的所有工作表。
def parse_xlsx_sheets_with_stdlib(uploaded_file) -> dict[str, list[list]]:
    uploaded_file.seek(0)
    namespace = {
        "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    with zipfile.ZipFile(uploaded_file) as archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [xlsx_plain_text(item) for item in shared_root.findall("x:si", namespace)]
        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets_by_id = {
            rel.attrib["Id"]: rel.attrib["Target"].lstrip("/")
            for rel in rel_root.findall("rel:Relationship", namespace)
            if "Id" in rel.attrib and "Target" in rel.attrib
        }
        sheets = {}
        for sheet in workbook_root.findall(".//x:sheet", namespace):
            name = sheet.attrib.get("name", "Sheet")
            rel_id = sheet.attrib.get(f"{{{namespace['r']}}}id")
            target = targets_by_id.get(rel_id, "")
            sheet_path = target if target.startswith("xl/") else f"xl/{target}"
            if sheet_path in archive.namelist():
                sheet_root = ET.fromstring(archive.read(sheet_path))
                sheets[name] = xlsx_rows_from_sheet_root(sheet_root, shared_strings, namespace)
        if not sheets and "xl/worksheets/sheet1.xml" in archive.namelist():
            sheet_root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            sheets["Sheet1"] = xlsx_rows_from_sheet_root(sheet_root, shared_strings, namespace)
        return sheets


# 优先用 openpyxl 解析 XLSX，失败时退回标准库解析。
def parse_xlsx_sheets(uploaded_file) -> dict[str, list[list]]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return parse_xlsx_sheets_with_stdlib(uploaded_file)
    uploaded_file.seek(0)
    workbook = load_workbook(uploaded_file, read_only=True, data_only=True)
    return {
        worksheet.title: list(worksheet.iter_rows(values_only=True))
        for worksheet in workbook.worksheets
    }


# 使用标准库解析旧版单工作表合同导入文件。
def parse_contract_import_xlsx_with_stdlib(uploaded_file):
    sheets = parse_xlsx_sheets_with_stdlib(uploaded_file)
    return next(iter(sheets.values()), [])


# 解析合同导入 Excel 并兼容多种模板结构。
def parse_contract_import_xlsx(uploaded_file):
    rows = next(iter(parse_xlsx_sheets(uploaded_file).values()), [])
    if not rows:
        return [], ["Excel 文件为空。"]

    headers = [normalize_import_cell(value) for value in rows[0]]
    header_map = {}
    expected_fields = {field for field, _label in CONTRACT_IMPORT_COLUMNS}
    expected_labels = {label: field for field, label in CONTRACT_IMPORT_COLUMNS}
    for index, header in enumerate(headers):
        field_name = expected_labels.get(header) or CONTRACT_IMPORT_HEADER_ALIASES.get(header)
        if field_name in expected_fields:
            header_map[field_name] = index

    required_fields = ["contract_name", "contract_type", "party_name"]
    missing_headers = [
        dict(CONTRACT_IMPORT_COLUMNS)[field_name]
        for field_name in required_fields
        if field_name not in header_map
    ]
    if missing_headers:
        return [], [f"缺少必需列：{', '.join(missing_headers)}。"]

    parsed_rows = []
    for excel_row_number, values in enumerate(rows[1:], start=2):
        if not any(normalize_import_cell(value) for value in values):
            continue
        row_data = {}
        for field_name, _label in CONTRACT_IMPORT_COLUMNS:
            column_index = header_map.get(field_name)
            row_data[field_name] = (
                normalize_import_value(field_name, values[column_index])
                if column_index is not None and column_index < len(values)
                else ""
            )
        parsed_rows.append({"row_number": excel_row_number, "data": row_data})
    if len(parsed_rows) > 99:
        return parsed_rows, ["一次最多导入 99 条合同，请拆分 Excel 后再导入。"]
    return parsed_rows, []


# 校验合同导入预览行并标记错误信息。
def validate_contract_import_rows(parsed_rows, contract_numbers=None):
    contract_numbers = contract_numbers or default_contract_numbers(max(len(parsed_rows), 1))
    results = []
    display_numbers = {}
    for index, item in enumerate(parsed_rows):
        data = item["data"].copy()
        data["contract_number"] = contract_numbers[index]
        form = ContractForm(data=data)
        errors = []
        is_valid = form.is_valid()
        if not is_valid:
            for field_errors in form.errors.values():
                errors.extend(str(error) for error in field_errors)
        cleaned = form.cleaned_data
        folder = normalize_contract_number_part(data.get("original_contract_folder"), 2)
        inner_number = normalize_contract_number_part(data.get("original_contract_inner_number"), 4)
        storage_location = normalize_storage_location_number(data.get("storage_location_number"))
        if folder and inner_number:
            base_date = cleaned.get("sign_date") or cleaned.get("start_date") or timezone.localdate()
            contract_type = cleaned.get("contract_type") or data.get("contract_type")
            display_number = (
                f"{str(base_date.year)[-2:]}"
                f"{folder}"
                f"{inner_number}"
                f"{Contract.CONTRACT_TYPE_CODES.get(contract_type, '06')}"
                f"{storage_location}"
            )
            if display_number in display_numbers:
                errors.append(f"显示合同编号 {display_number} 与第 {display_numbers[display_number]} 行重复。")
            else:
                display_numbers[display_number] = item["row_number"]
        preview_contract = Contract(
            contract_number=data["contract_number"],
            contract_name=cleaned.get("contract_name") or data.get("contract_name", ""),
            original_contract_folder=cleaned.get("original_contract_folder") or folder,
            original_contract_inner_number=cleaned.get("original_contract_inner_number") or inner_number,
            storage_location_number=cleaned.get("storage_location_number") or storage_location,
            contract_type=cleaned.get("contract_type") or data.get("contract_type", ""),
            party_name=cleaned.get("party_name") or data.get("party_name", ""),
            amount=cleaned.get("amount") or Decimal("0"),
            invoice_status=cleaned.get("invoice_status") or data.get("invoice_status", ""),
            sign_date=cleaned.get("sign_date"),
            start_date=cleaned.get("start_date"),
            end_date=cleaned.get("end_date"),
            responsible_person=cleaned.get("responsible_person") or data.get("responsible_person", ""),
            archive_years=cleaned.get("archive_years") or 3,
        )
        invoice_status = preview_contract.invoice_status
        invoice_status_class = ""
        if invoice_status == "开收据":
            invoice_status_class = "invoice-receipt"
        elif invoice_status == "待开票":
            invoice_status_class = "invoice-pending"
        elif invoice_status == "票已结":
            invoice_status_class = "invoice-done"
        results.append(
            {
                "row_number": item["row_number"],
                "data": item["data"],
                "preview_cells": [
                    {"value": item["row_number"] - 1},
                    {"value": preview_contract.contract_name, "css_class": "truncate-cell", "title": preview_contract.contract_name},
                    {"value": preview_contract.contract_number},
                    {"value": preview_contract.contract_type},
                    {"value": preview_contract.party_name, "css_class": "truncate-cell", "title": preview_contract.party_name},
                    {"value": f"¥ {preview_contract.amount:.2f}"},
                    {"value": preview_contract.invoice_status, "css_class": f"invoice-status {invoice_status_class}".strip()},
                    {"value": preview_contract.start_date.strftime("%Y-%m-%d") if preview_contract.start_date else ""},
                    {"value": preview_contract.end_date.strftime("%Y-%m-%d") if preview_contract.end_date else ""},
                    {"value": preview_contract.responsible_person},
                    {"value": preview_contract.status, "css_class": f"status {preview_contract.status_class}"},
                    {"value": "；".join(errors), "css_class": "truncate-cell error-cell", "title": "；".join(errors)},
                ],
                "errors": errors,
                "ok": not errors,
            }
        )
    return results


# 将导入值转换为 Decimal，空值按无效处理。
def decimal_from_import(value):
    text = normalize_import_cell(value).replace(",", "")
    if text == "":
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


# 将导入值转换为 Decimal，空值按 0 处理。
def decimal_from_import_or_zero(value):
    amount = decimal_from_import(value)
    return amount if amount is not None else Decimal("0")


# 将导入值转换为日期对象。
def date_from_import(value):
    text = normalize_import_value("record_date", value)
    if not text:
        return None
    return parse_form_date(text)


# 构建导入记录时按显示编号查找合同的索引。
def import_contract_lookup() -> dict[str, Contract]:
    lookup = {}
    for contract in Contract.objects.filter(is_deleted=False):
        keys = {
            contract.contract_number,
            contract.display_contract_number,
            contract.full_display_contract_number,
            contract.contract_name,
        }
        for key in keys:
            normalized = normalize_import_cell(key)
            if normalized:
                lookup.setdefault(normalized, contract)
    return lookup


# 为导入页项目名称查编码功能准备搜索数据。
def project_code_lookup_items() -> list[dict]:
    contracts = Contract.objects.filter(is_deleted=False).order_by("contract_name", "id")
    return [
        {
            "code": contract.display_contract_number,
            "name": contract.contract_name,
            "party": contract.party_name,
            "type": contract.contract_type,
        }
        for contract in contracts
    ]


# 按字段映射解析票据或项目记录导入工作表。
def parse_record_import_rows_from_sheet(rows, field_map, row_builder):
    if not rows:
        return [], ["Excel 文件为空。"]
    headers = [normalize_import_cell(value) for value in rows[0]]
    header_map = {}
    for index, header in enumerate(headers):
        field_name = field_map.get(header)
        if field_name:
            header_map[field_name] = index
    missing_headers = [
        label
        for label, field_name in field_map.items()
        if field_name in {"contract_key", "record_date"} and field_name not in header_map
    ]
    if missing_headers:
        return [], [f"缺少必需列：{', '.join(missing_headers)}。"]
    parsed_rows = []
    for excel_row_number, values in enumerate(rows[1:], start=2):
        if not any(normalize_import_cell(value) for value in values):
            continue
        parsed_rows.append(row_builder(excel_row_number, values, header_map))
    return parsed_rows, []


# 解析票据导入 Excel。
def parse_invoice_import_xlsx(uploaded_file):
    sheets = parse_xlsx_sheets(uploaded_file)
    parsed_rows = []
    parse_errors = []
    for sheet_name, rows in sheets.items():
        record_type = normalize_import_cell(sheet_name)
        if record_type not in INVOICE_IMPORT_TYPES:
            continue
        amount_label, actual_amount_label = INVOICE_IMPORT_SHEET_LABELS[record_type]
        field_map = {
            "合同编号": "contract_key",
            "合同名称": "contract_key",
            "日期": "record_date",
            "记录日期": "record_date",
            amount_label: "amount",
            "票面金额": "amount",
            actual_amount_label: "actual_amount",
            "实际金额": "actual_amount",
            "备注": "remark",
        }

        def build_row(excel_row_number, values, header_map):
            def value_for(field_name):
                index = header_map.get(field_name)
                return values[index] if index is not None and index < len(values) else ""

            return {
                "row_number": excel_row_number,
                "sheet_name": record_type,
                "data": {
                    "contract_key": normalize_import_cell(value_for("contract_key")),
                    "record_type": record_type,
                    "record_date": normalize_import_value("record_date", value_for("record_date")),
                    "amount": normalize_import_cell(value_for("amount")),
                    "actual_amount": normalize_import_cell(value_for("actual_amount")),
                    "remark": normalize_import_cell(value_for("remark")),
                },
            }

        rows_for_sheet, errors = parse_record_import_rows_from_sheet(rows, field_map, build_row)
        parse_errors.extend([f"{record_type}：{error}" for error in errors])
        parsed_rows.extend(rows_for_sheet)
    if not parsed_rows and not parse_errors:
        parse_errors.append("未找到可导入的票据工作表，请使用“开票/收票”或“开据/收据”工作表。")
    if len(parsed_rows) > 300:
        parse_errors.append("一次最多导入 300 条票据记录，请拆分 Excel 后再导入。")
    return parsed_rows, parse_errors


# 校验票据导入预览行并绑定目标合同。
def validate_invoice_import_rows(parsed_rows):
    contract_lookup = import_contract_lookup()
    results = []
    for item in parsed_rows:
        data = item["data"].copy()
        errors = []
        contract = contract_lookup.get(data.get("contract_key", ""))
        record_date = date_from_import(data.get("record_date"))
        amount = decimal_from_import(data.get("amount"))
        actual_amount_text = normalize_import_cell(data.get("actual_amount"))
        actual_amount = decimal_from_import(data.get("actual_amount")) if actual_amount_text else Decimal("0")
        if contract is None:
            errors.append("未找到对应合同。")
        elif contract.invoice_status == "开收据" and data.get("record_type") not in {"开据", "收据"}:
            errors.append("该合同是开收据模式，请使用“开据/收据”工作表。")
        elif contract.invoice_status != "开收据" and data.get("record_type") not in {"开票", "收票"}:
            errors.append("该合同是开票模式，请使用“开票/收票”工作表。")
        if record_date is None:
            errors.append("日期格式不正确。")
        if amount is None:
            errors.append("票面金额不能为空且必须是数字。")
        if amount is not None and amount < 0:
            errors.append("票面金额不能小于 0。")
        if actual_amount_text and actual_amount is None:
            errors.append("实际金额必须是数字。")
        if actual_amount is not None and actual_amount < 0:
            errors.append("实际金额不能小于 0。")
        results.append(
            {
                "row_number": item["row_number"],
                "data": data,
                "contract_id": contract.pk if contract else None,
                "preview_cells": [
                    {"value": len(results) + 1},
                    {"value": contract.contract_name if contract else data.get("contract_key", ""), "css_class": "truncate-cell", "title": contract.contract_name if contract else data.get("contract_key", "")},
                    {"value": contract.display_contract_number if contract else ""},
                    {"value": data.get("record_type", "")},
                    {"value": record_date.strftime("%Y-%m-%d") if record_date else data.get("record_date", "")},
                    {"value": f"¥ {amount:.2f}" if amount is not None else data.get("amount", "")},
                    {"value": f"¥ {actual_amount:.2f}" if actual_amount is not None else data.get("actual_amount", "")},
                    {"value": data.get("remark", ""), "css_class": "truncate-cell", "title": data.get("remark", "")},
                    {"value": "；".join(errors), "css_class": "truncate-cell error-cell", "title": "；".join(errors)},
                ],
                "errors": errors,
                "ok": not errors,
            }
        )
    return results


# 解析项目记录导入 Excel。
def parse_maintenance_import_xlsx(uploaded_file):
    rows = next(iter(parse_xlsx_sheets(uploaded_file).values()), [])
    field_map = {
        "合同编号": "contract_key",
        "合同名称": "contract_key",
        "日期": "record_date",
        "记录日期": "record_date",
        "存储编号": "storage_location_number",
        "备注": "remark",
    }

    def build_row(excel_row_number, values, header_map):
        def value_for(field_name):
            index = header_map.get(field_name)
            return values[index] if index is not None and index < len(values) else ""

        return {
            "row_number": excel_row_number,
            "data": {
                "contract_key": normalize_import_cell(value_for("contract_key")),
                "record_date": normalize_import_value("record_date", value_for("record_date")),
                "storage_location_number": normalize_import_value("storage_location_number", value_for("storage_location_number")),
                "remark": normalize_import_cell(value_for("remark")),
            },
        }

    parsed_rows, parse_errors = parse_record_import_rows_from_sheet(rows, field_map, build_row)
    if len(parsed_rows) > 300:
        parse_errors.append("一次最多导入 300 条项目记录，请拆分 Excel 后再导入。")
    return parsed_rows, parse_errors


# 校验项目记录导入预览行并绑定目标合同。
def validate_maintenance_import_rows(parsed_rows):
    contract_lookup = import_contract_lookup()
    results = []
    for item in parsed_rows:
        data = item["data"].copy()
        errors = []
        contract = contract_lookup.get(data.get("contract_key", ""))
        record_date = date_from_import(data.get("record_date"))
        storage_location = normalize_storage_location_number(data.get("storage_location_number"))
        if contract is None:
            errors.append("未找到对应合同。")
        elif not str(contract.original_contract_inner_number or "").strip():
            errors.append("合同缺少文件编号，不能生成记录编号。")
        if record_date is None:
            errors.append("日期格式不正确。")
        record_number = maintenance_record_number(contract, record_date, storage_location) if contract and record_date else ""
        results.append(
            {
                "row_number": item["row_number"],
                "data": data,
                "contract_id": contract.pk if contract else None,
                "preview_cells": [
                    {"value": len(results) + 1},
                    {"value": contract.contract_name if contract else data.get("contract_key", ""), "css_class": "truncate-cell", "title": contract.contract_name if contract else data.get("contract_key", "")},
                    {"value": contract.display_contract_number if contract else ""},
                    {"value": record_date.strftime("%Y-%m-%d") if record_date else data.get("record_date", "")},
                    {"value": record_number},
                    {"value": storage_location},
                    {"value": data.get("remark", ""), "css_class": "truncate-cell", "title": data.get("remark", "")},
                    {"value": "；".join(errors), "css_class": "truncate-cell error-cell", "title": "；".join(errors)},
                ],
                "errors": errors,
                "ok": not errors,
            }
        )
    return results


# 组装合同、票据和项目记录导入预览上下文。
def contract_import_preview_context(
    request,
    upload_form,
    results=None,
    payload="",
    parse_errors=None,
    confirm_error="",
    completed=False,
    import_kind="contract",
    selected_contract=None,
):
    results = results or []
    valid_count = sum(1 for row in results if row["ok"])
    error_count = len(results) - valid_count
    allow_partial_import_with_errors = AppSetting.current().allow_partial_import_with_errors
    can_confirm_import = valid_count > 0 and (not error_count or allow_partial_import_with_errors)
    preview_columns = {
        "contract": CONTRACT_IMPORT_PREVIEW_COLUMNS,
        "invoice": INVOICE_IMPORT_PREVIEW_COLUMNS,
        "maintenance": MAINTENANCE_IMPORT_PREVIEW_COLUMNS,
    }.get(import_kind, CONTRACT_IMPORT_PREVIEW_COLUMNS)
    return context_with_auth(
        request,
        {
            "form": upload_form,
            "columns": CONTRACT_IMPORT_COLUMNS,
            "preview_columns": preview_columns,
            "results": results,
            "payload": payload,
            "parse_errors": parse_errors or [],
            "valid_count": valid_count,
            "error_count": error_count,
            "allow_partial_import_with_errors": allow_partial_import_with_errors,
            "can_confirm_import": can_confirm_import,
            "confirm_error": confirm_error,
            "completed": completed,
            "import_kind": import_kind,
            "selected_contract": selected_contract,
            "selected_contract_id": selected_contract.pk if selected_contract else "",
            "project_lookup_items": project_code_lookup_items(),
            "active_nav": "contracts",
        },
    )


# 视图函数：下载合同导入 Excel 模板。
@admin_required
def contract_import_template(request):
    headers = [label for _field, label in CONTRACT_IMPORT_COLUMNS]
    comments = {
        "合同名称": "必填。填写要导入的合同名称。",
        "合同类型": "必填。填写维保、项目或其他系统支持的合同类型。",
        "甲方名称": "必填。填写甲方单位名称。",
        "合同金额": "填写数字金额，例如 10000。",
        "是否开票": "填写开票状态，例如 开收据、待开票或票已给。",
        "签订日期": "日期格式：YYYY-MM-DD。",
        "开始日期": "日期格式：YYYY-MM-DD。",
        "截止日期": "日期格式：YYYY-MM-DD。",
        "负责人": "填写负责人姓名。",
        "文件夹编号": "填写 2 位文件夹编号，例如 01。",
        "文件编号": "填写 4 位文件编号，例如 0001。",
        "存储编号": "填写 2 位存储编号，例如 00。",
        "归档时间（年）": "填写归档年限数字，例如 3。",
        "备注": "可选。填写合同备注。",
    }
    response = HttpResponse(
        build_commented_import_template_xlsx(
            [{"name": "合同列表", "headers": headers, "comments": comments}]
        ),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="contract_import_template.xlsx"'
    return response


# 视图函数：下载票据导入 Excel 模板。
@true_admin_required
def invoice_import_template(request):
    sheets = []
    for record_type in ("开票", "收票"):
        amount_label, actual_amount_label = INVOICE_IMPORT_SHEET_LABELS[record_type]
        headers = ["合同编号", "日期", amount_label, actual_amount_label, "备注"]
        comments = {
            "合同编号": "填写已有合同编号或合同名称，用于匹配合同。",
            "日期": "必填。日期格式：YYYY-MM-DD。",
            amount_label: "必填。填写数字金额。",
            actual_amount_label: "可选。未填时按 0 计算。",
            "备注": "可选。填写票据备注。",
        }
        sheets.append(
            {
                "name": record_type,
                "headers": headers,
                "comments": comments,
            }
        )
    response = HttpResponse(
        build_commented_import_template_xlsx(sheets),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="invoice_import_template.xlsx"'
    return response


# 视图函数：下载项目记录导入 Excel 模板。
@true_admin_required
def record_import_template(request):
    headers = ["合同编号", "日期", "存储编号", "备注"]
    comments = {
        "合同编号": "填写已有合同编号或合同名称，用于匹配合同。",
        "日期": "必填。日期格式：YYYY-MM-DD。",
        "存储编号": "填写 2 位存储编号，例如 00。",
        "备注": "可选。填写项目记录备注。",
    }
    response = HttpResponse(
        build_commented_import_template_xlsx(
            [{"name": "记录", "headers": headers, "comments": comments}]
        ),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="record_import_template.xlsx"'
    return response


# 视图函数：处理合同、票据和项目记录的 Excel 导入。
@admin_required
def contract_import(request):
    selected_contract = None
    selected_contract_id = request.POST.get("contract_id") or request.GET.get("contract_id")
    if selected_contract_id:
        selected_contract = get_object_or_404(Contract, pk=selected_contract_id, is_deleted=False)
    if request.method == "POST" and request.POST.get("action") == "confirm":
        import_kind = request.POST.get("import_kind", "contract")
        if is_normal_mode(request) and import_kind != "contract":
            return redirect("contracts:login")
        try:
            parsed_rows = signing.loads(request.POST.get("payload", ""), max_age=3600)
        except signing.BadSignature:
            context = contract_import_preview_context(
                request,
                ContractImportUploadForm(),
                parse_errors=["导入预览已失效，请重新上传 Excel。"],
                import_kind=import_kind,
                selected_contract=selected_contract,
            )
            return render(request, "contracts/contract_import.html", context)

        try:
            contract_numbers = default_contract_numbers(max(len(parsed_rows), 1)) if import_kind == "contract" else []
            if import_kind == "invoice":
                results = validate_invoice_import_rows(parsed_rows)
            elif import_kind == "maintenance":
                results = validate_maintenance_import_rows(parsed_rows)
            else:
                results = validate_contract_import_rows(parsed_rows, contract_numbers)
        except DjangoValidationError as exc:
            context = contract_import_preview_context(
                request,
                ContractImportUploadForm(),
                parse_errors=[str(exc)],
                import_kind=import_kind,
                selected_contract=selected_contract,
            )
            return render(request, "contracts/contract_import.html", context)
        invalid_rows = [row for row in results if not row["ok"]]
        allow_partial = AppSetting.current().allow_partial_import_with_errors
        if invalid_rows and not allow_partial:
            payload = signing.dumps(parsed_rows)
            context = contract_import_preview_context(
                request,
                ContractImportUploadForm(),
                results=results,
                payload=payload,
                confirm_error="存在错误行，请在系统设置中开启“Excel 导入存在错误时仍导入通过行”，或返回修改 Excel。",
                import_kind=import_kind,
                selected_contract=selected_contract,
            )
            return render(request, "contracts/contract_import.html", context)

        created_count = 0
        with transaction.atomic():
            for index, row in enumerate(results):
                if not row["ok"]:
                    continue
                if import_kind == "invoice":
                    data = row["data"].copy()
                    contract = Contract.objects.get(pk=row["contract_id"])
                    record_model = INVOICE_IMPORT_TYPES[data["record_type"]]
                    record = record_model.objects.create(
                        contract=contract,
                        record_date=date_from_import(data["record_date"]),
                        record_type=data["record_type"],
                        amount=decimal_from_import(data["amount"]),
                        actual_amount=decimal_from_import_or_zero(data.get("actual_amount")),
                        remark=data.get("remark", ""),
                    )
                    log_operation(request, "新增", contract, object_type="票据记录", object_name=str(record), object_id=str(record.pk), detail=f"Excel import row: {row['row_number']}", version_obj=record)
                elif import_kind == "maintenance":
                    data = row["data"].copy()
                    contract = Contract.objects.get(pk=row["contract_id"])
                    record_date = date_from_import(data["record_date"])
                    month = record_date.strftime("%Y年%m月")
                    record = MaintenanceRecord.objects.create(
                        contract=contract,
                        record_date=record_date,
                        month=month,
                        storage_location_number=normalize_storage_location_number(data.get("storage_location_number")),
                        remark=data.get("remark", ""),
                    )
                    log_operation(request, "新增", contract, object_type="项目记录", object_name=str(record), object_id=str(record.pk), detail=f"Excel import row: {row['row_number']}", version_obj=record)
                else:
                    data = row["data"].copy()
                    data["contract_number"] = contract_numbers[index]
                    form = ContractForm(data=data)
                    if not form.is_valid():
                        raise RuntimeError("确认导入时数据校验失败，请重新上传 Excel。")
                    contract = form.save()
                    ensure_contract_image_folder(contract)
                    log_operation(request, "新增", contract, detail=f"Excel import row: {row['row_number']}")
                created_count += 1
        import_name = {"contract": "合同", "invoice": "票据记录", "maintenance": "项目记录"}.get(import_kind, "数据")
        return render(
            request,
            "contracts/contract_import.html",
            contract_import_preview_context(
                request,
                ContractImportUploadForm(),
                results=results,
                parse_errors=[f"已导入 {created_count} 条{import_name}。"],
                completed=True,
                import_kind=import_kind,
                selected_contract=selected_contract,
            ),
        )

    if request.method == "POST":
        import_kind = request.POST.get("import_kind", "contract")
        if is_normal_mode(request) and import_kind != "contract":
            return redirect("contracts:login")
        upload_form = ContractImportUploadForm(request.POST, request.FILES)
        if upload_form.is_valid():
            try:
                if import_kind == "invoice":
                    parsed_rows, parse_errors = parse_invoice_import_xlsx(upload_form.cleaned_data["excel_file"])
                elif import_kind == "maintenance":
                    parsed_rows, parse_errors = parse_maintenance_import_xlsx(upload_form.cleaned_data["excel_file"])
                else:
                    parsed_rows, parse_errors = parse_contract_import_xlsx(upload_form.cleaned_data["excel_file"])
                try:
                    if parsed_rows and not parse_errors:
                        if import_kind == "invoice":
                            results = validate_invoice_import_rows(parsed_rows)
                        elif import_kind == "maintenance":
                            results = validate_maintenance_import_rows(parsed_rows)
                        else:
                            results = validate_contract_import_rows(parsed_rows)
                    else:
                        results = []
                    payload = signing.dumps(parsed_rows) if parsed_rows and not parse_errors else ""
                except DjangoValidationError as exc:
                    results = []
                    payload = ""
                    parse_errors = [str(exc)]
            except RuntimeError as exc:
                results = []
                payload = ""
                parse_errors = [str(exc)]
            context = contract_import_preview_context(
                request,
                upload_form,
                results=results,
                payload=payload,
                parse_errors=parse_errors,
                import_kind=import_kind,
                selected_contract=selected_contract,
            )
            return render(request, "contracts/contract_import.html", context)
    else:
        upload_form = ContractImportUploadForm()
    return render(
        request,
        "contracts/contract_import.html",
        contract_import_preview_context(request, upload_form, selected_contract=selected_contract),
    )


# 视图函数：导出单个合同的归档快照。
@true_admin_required
def contract_snapshot_export(request, pk: int):
    if not is_super_admin_mode(request):
        return redirect("contracts:contract_list")
    contract = get_object_or_404(Contract, pk=pk)
    payload = contract_snapshot_payload(contract)
    exported_at = timezone.localtime().strftime("%Y%m%d%H%M%S")
    filename = f"contract_snapshot_{contract.pk}_{exported_at}.json"
    response = HttpResponse(
        json.dumps(payload, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# 渲染单个合同详情页面。
# 视图函数：处理页面请求并返回响应。
def contract_detail(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if is_normal_mode(request):
        return redirect_with_current_query(request, reverse("contracts:maintenance_record_list", args=[contract.pk]))
    primary_file = contract.latest_file
    invoice_labels = invoice_mode_labels(contract.invoice_status)
    project_labels = project_record_labels(contract.contract_type)
    return_state = list_return_state(request, contract.pk)
    context = context_with_auth(
        request,
        {
            "contract": contract,
            "contract_files": contract.files.all(),
            "primary_file": primary_file,
            "maintenance_records": contract.maintenancerecord_set.all(),
            "invoice_records": contract.invoicerecord_set.all(),
            "payment_records": contract.paymentrecord_set.all(),
            "invoice_labels": invoice_labels,
            "project_record_labels": project_labels,
            **return_state,
            "active_nav": "contracts",
        },
    )
    return render(request, "contracts/contract_detail.html", context)


# 视图函数：处理页面请求并返回响应。
@admin_required
def contract_remark_update(request, pk: int):
    # 详情页允许直接补充或修改合同备注，不必进入完整编辑表单。
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method == "POST":
        contract.remark = request.POST.get("remark", "").strip()
        contract.save(update_fields=["remark", "updated_at"])
        log_operation(request, "修改", contract, detail="updated contract remark")
    next_url = request.POST.get("next") or reverse("contracts:contract_detail", args=[contract.pk])
    return redirect(next_url)


# 函数说明：在合同列表中即时更新票据状态。
@admin_required
def contract_invoice_status_update(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method != "POST":
        return JsonResponse({"error": "只允许保存票据状态。"}, status=405)
    invoice_status = request.POST.get("invoice_status", "").strip()
    valid_statuses = {value for value, _ in Contract.INVOICE_STATUS}
    if invoice_status not in valid_statuses:
        return JsonResponse({"error": "票据状态不正确。"}, status=400)
    contract.invoice_status = invoice_status
    contract.save(update_fields=["invoice_status", "updated_at"])
    log_operation(request, "修改", contract, detail=f"invoice status: {invoice_status}")
    return JsonResponse({"ok": True, "invoice_status": contract.invoice_status})


RECORD_MODEL_MAP = {
    "invoice": InvoiceRecord,
    "payment": PaymentRecord,
    "maintenance": MaintenanceRecord,
}


# 函数说明：封装可复用的业务处理。
def record_model_for_kind(kind: str):
    # 前端提交的记录来源标记会映射到具体模型。
    return RECORD_MODEL_MAP.get(kind)


# 预览指定类型记录的当前文件。
def record_file_preview(request, kind: str, pk: int):
    record_model = record_model_for_kind(kind)
    if record_model is None:
        return redirect("contracts:contract_list")
    record = get_object_or_404(record_model, pk=pk, contract__is_deleted=False)
    file_exists = repair_file_field_path(record.file) if record.file else False
    file_content_url = reverse("contracts:configured_file_content", args=[kind, record.id])
    return_url = request.GET.get("next", "")
    if not return_url.startswith("/") or return_url.startswith("//"):
        return_url = reverse("contracts:contract_detail", args=[record.contract_id])
    upload_url = reverse("contracts:record_file_update", args=[kind, record.id])
    current_url = request.get_full_path()
    preview_type = "empty"
    if record.file:
        preview_type = preview_type_for_file(record.file.name) if file_exists else "missing"
    version_model = record_file_version_model_for(record)
    latest_version = version_model.objects.filter(record=record).first() if version_model else None
    file_name = (
        latest_version.original_name
        if latest_version and latest_version.original_name
        else Path(record.file.name).name if record.file else "未上传"
    )
    return render(
        request,
        "contracts/file_preview.html",
        context_with_auth(
            request,
            {
                "contract": record.contract,
                "file_name": file_name,
                "file_url": file_content_url if file_exists else "",
                "download_url": f"{file_content_url}?download=1" if file_exists else "",
                "preview_type": preview_type,
                "return_url": return_url,
                "upload_url": upload_url,
                "upload_next_url": current_url,
                "active_nav": "contracts",
            },
        ),
    )


# 视图函数：处理页面请求并返回响应。
@admin_required
def record_delete(request, pk: int):
    # 详情页三类记录共用同一个删除入口，前端用 kind:id 标记来源表。
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method != "POST":
        return redirect("contracts:contract_detail", pk=contract.pk)
    next_url = request.POST.get("next", "")
    deleted_count = 0
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
        delete_record_file_versions(record)
        record.delete()
        deleted_count += 1
    if deleted_count:
        log_operation(request, "删除", contract, detail=f"deleted records: {deleted_count}")
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("contracts:contract_detail", pk=contract.pk)


# 函数说明：封装可复用的业务处理。
@admin_required
def record_file_update(request, kind: str, pk: int):
    # 单条记录展示最新附件，新上传文件会新增版本并保留旧文件。
    record_model = record_model_for_kind(kind)
    if record_model is None:
        return redirect("contracts:contract_list")
    record = get_object_or_404(record_model, pk=pk, contract__is_deleted=False)
    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        if uploaded_file:
            attach_record_file_version(record, uploaded_file)
            log_operation(request, "上传", record.contract, object_type="记录附件", object_name=uploaded_file.name, object_id=str(record.pk), detail=f"record type: {getattr(record, 'record_type', 'project record')}", version_obj=record)
    next_url = request.POST.get("next", "")
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("contracts:contract_detail", pk=record.contract_id)


# 函数说明：封装可复用的业务处理。
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
        log_operation(request, "修改", record.contract, object_type="记录备注", object_name=str(record), object_id=str(record.pk), version_obj=record)
    next_url = request.POST.get("next", "")
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("contracts:contract_detail", pk=record.contract_id)


# 函数说明：封装可复用的业务处理。
def maintenance_record_list(request, pk: int):
    # 所有合同类型的扩展记录共用 MaintenanceRecord 表和同一个列表模板。
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    project_labels = project_record_labels(contract.contract_type)
    return_state = list_return_state(request, contract.pk)
    maintenance_records = list(contract.maintenancerecord_set.all())
    for record in maintenance_records:
        record.record_number = maintenance_record_number(
            contract,
            record.record_date,
            record.storage_location_number,
        )
    context = context_with_auth(
        request,
        {
            "contract": contract,
            "record_label": project_labels["list_title"],
            "project_record_labels": project_labels,
            "primary_file": contract.latest_file,
            "maintenance_records": maintenance_records,
            **return_state,
            "active_nav": "contracts",
        },
    )
    return render(request, "contracts/maintenance_record_list.html", context)


# 在日期上增减月份，并自动处理月底天数溢出。
def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


# 视图函数：处理页面请求并返回响应。
@admin_required
# 新增合同并保存随表单上传的合同文件。
def contract_create(request):
    return_state = list_return_state(request)
    if request.method == "POST":
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            contract = form.save()
            ensure_contract_image_folder(contract)
            uploaded_count = len(save_contract_files_and_return(contract, request.FILES.getlist("files")))
            detail = f"uploaded contract files: {uploaded_count}" if uploaded_count else ""
            log_operation(request, "新增", contract, detail=detail)
            return redirect(return_state["return_url"])
    else:
        today = timezone.localdate()
        form = ContractForm(
            initial={
                "contract_number": default_contract_number(),
                "contract_type": "维保",
                "sign_date": today,
                "start_date": today,
                "end_date": add_months(today, 1),
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
                **return_state,
                "cancel_url": return_state["return_url"],
                "active_nav": "contracts",
            },
        ),
    )


# 视图函数：处理页面请求并返回响应。
@admin_required
def contract_image_folder_open(request, pk: int):
    # 打开当前合同在图片整理目录中的专属文件夹，便于直接查看 OCR 整理后的图片。
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    try:
        folder = ensure_contract_image_folder(contract)
        if os.name == "nt":
            os.startfile(str(folder))
            center_windows_explorer_for_folder(folder)
    except OSError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "path": str(folder)})


# 视图函数：打开合同普通附件所在文件夹。
@admin_required
def contract_file_folder_open(request, pk: int):
    # 打开当前合同在 media 中的文件保存根目录。
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    try:
        folder = ensure_contract_file_folder(contract)
        if os.name == "nt":
            os.startfile(str(folder))
            center_windows_explorer_for_folder(folder)
    except OSError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "path": str(folder)})


# 函数说明：封装可复用的业务处理。
@admin_required
def settlement_file_list(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    return_state = list_return_state(request, contract.pk)
    if request.method == "POST":
        if request.POST.get("action") == "delete_files":
            deleted_count = 0
            for item in SettlementFile.objects.filter(id__in=request.POST.getlist("delete_files"), contract=contract):
                delete_file_from_storage(item.file)
                item.delete()
                deleted_count += 1
            if deleted_count:
                log_operation(request, "删除", contract, object_type="结算文件", detail=f"deleted settlement files: {deleted_count}")
            return redirect(
                merge_query_params(
                    reverse("contracts:settlement_file_list", args=[contract.pk]),
                    {
                        "next": return_state["next_url"],
                        "scroll": return_state["scroll_position"],
                        "return_id": return_state["return_id"],
                    },
                )
            )
        uploaded_count = 0
        for item in request.FILES.getlist("files"):
            SettlementFile.objects.create(contract=contract, file=item, original_name=item.name)
            uploaded_count += 1
        if uploaded_count:
            log_operation(request, "上传", contract, object_type="结算文件", detail=f"uploaded settlement files: {uploaded_count}")
        return redirect(
            merge_query_params(
                reverse("contracts:settlement_file_list", args=[contract.pk]),
                {
                    "next": return_state["next_url"],
                    "scroll": return_state["scroll_position"],
                    "return_id": return_state["return_id"],
                },
            )
        )
    return render(
        request,
        "contracts/settlement_files.html",
        context_with_auth(
            request,
            {
                "contract": contract,
                "settlement_files": contract.settlement_files.all(),
                **return_state,
                "active_nav": "contracts",
            },
        ),
    )


# 函数说明：封装可复用的业务处理。
@admin_required
def settlement_file_preview(request, pk: int):
    item = get_object_or_404(SettlementFile, pk=pk, contract__is_deleted=False)
    file_exists = repair_file_field_path(item.file)
    return_state = list_return_state(request, item.contract_id)
    settlement_return_url = merge_query_params(
        reverse("contracts:settlement_file_list", args=[item.contract_id]),
        {
            "next": return_state["next_url"],
            "scroll": return_state["scroll_position"],
            "return_id": return_state["return_id"],
        },
    )
    file_content_url = reverse("contracts:configured_file_content", args=["settlement", item.id])
    return render(
        request,
        "contracts/file_preview.html",
        context_with_auth(
            request,
            {
                "contract": item.contract,
                "file_name": item.original_name or Path(item.file.name).name,
                "file_url": file_content_url if file_exists else "",
                "download_url": f"{file_content_url}?download=1" if file_exists else "",
                "preview_type": preview_type_for_file(item.file.name) if file_exists else "missing",
                "return_url": settlement_return_url,
                "delete_url": reverse("contracts:settlement_file_delete", args=[item.id]),
                "active_nav": "contracts",
            },
        ),
    )


# 视图函数：处理页面请求并返回响应。
@admin_required
def settlement_file_delete(request, pk: int):
    item = get_object_or_404(SettlementFile, pk=pk, contract__is_deleted=False)
    contract_id = item.contract_id
    contract = item.contract
    if request.method == "POST":
        object_name = item.original_name or Path(item.file.name).name
        delete_file_from_storage(item.file)
        item.delete()
        log_operation(request, "删除", contract, object_type="结算文件", object_name=object_name, object_id=str(pk), version_obj=item)
        next_url = request.POST.get("next", "")
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
    return redirect("contracts:settlement_file_list", pk=contract_id)


# 视图函数：处理页面请求并返回响应。
@admin_required
# 处理合同编辑页中的即时文件上传请求。
def contract_file_upload(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method != "POST":
        return JsonResponse({"error": "只允许上传文件。"}, status=405)

    uploaded_files = request.FILES.getlist("files")
    saved_files = save_contract_files_and_return(contract, uploaded_files)
    if saved_files:
        log_operation(request, "上传", contract, object_type="合同文件", detail=f"uploaded contract files: {len(saved_files)}")
    return JsonResponse(
        {
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


# 视图函数：处理页面请求并返回响应。
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
    if ordered_ids:
        log_operation(request, "修改", contract, object_type="合同文件", detail="reordered contract files")
    return JsonResponse({"ok": True})


# 视图函数：处理页面请求并返回响应。
@admin_required
# 编辑合同基础信息，并处理批量删除合同文件。
def contract_update(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    old_file = contract.file
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    scroll_position = request.POST.get("scroll") or request.GET.get("scroll") or ""
    return_id = request.POST.get("return_id") or request.GET.get("return_id") or str(contract.pk)
    if request.method == "POST":
        if request.POST.get("action") == "delete_files":
            delete_ids = request.POST.getlist("delete_files")
            delete_contract_files(delete_ids)
            if delete_ids:
                log_operation(request, "删除", contract, object_type="合同文件", detail=f"deleted contract files: {len(delete_ids)}")
            edit_url = merge_query_params(
                reverse("contracts:contract_update", args=[contract.pk]),
                {"next": next_url, "scroll": scroll_position, "return_id": return_id},
            )
            return redirect(edit_url)

        form = ContractForm(request.POST, request.FILES, instance=contract)
        if form.is_valid():
            changed_labels = [
                str(form.fields[field_name].label or field_name)
                for field_name in form.changed_data
                if field_name in form.fields
            ]
            updated = form.save()
            if "file" in request.FILES and old_file and old_file != updated.file:
                delete_file_from_storage(old_file)
            uploaded_count = len(save_contract_files_and_return(updated, request.FILES.getlist("files")))
            detail_parts = []
            if changed_labels:
                detail_parts.append(f"changed fields: {', '.join(changed_labels)}")
            if uploaded_count:
                detail_parts.append(f"uploaded contract files: {uploaded_count}")
            if detail_parts:
                log_operation(request, "修改", updated, detail="; ".join(detail_parts))
            fallback_url = reverse("contracts:contract_list")
            target_url = safe_internal_path(next_url, fallback_url)
            target_url = merge_query_params(
                target_url,
                {"restore_scroll": scroll_position, "return_id": return_id},
            )
            return redirect(target_url)
    else:
        form = ContractForm(instance=contract)
    cancel_url = merge_query_params(
        safe_internal_path(next_url, reverse("contracts:contract_list")),
        {"restore_scroll": scroll_position, "return_id": return_id},
    )
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
                "next_url": next_url,
                "scroll_position": scroll_position,
                "return_id": return_id,
                "cancel_url": cancel_url,
            },
        ),
    )


# 视图函数：处理页面请求并返回响应。
@admin_required
# 将合同移入回收站，一周内可从回收站恢复。
def contract_delete(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method == "POST":
        return_state = list_return_state(request, contract.pk)
        contract.move_to_trash()
        log_operation(request, "删除", contract, detail="moved to trash")
        return redirect(return_state["return_url"])
    return render(
        request,
        "contracts/contract_confirm_delete.html",
        context_with_auth(request, {"contract": contract, "active_nav": "contracts"}),
    )


# 函数说明：封装可复用的业务处理。
@admin_required
# 根据合同是否开票，进入开票或收票记录新增入口。
def record_add(request, pk: int):
    # 现在“添加记录”只负责进入合同类型扩展记录表单。
    get_object_or_404(Contract, pk=pk, is_deleted=False)
    return redirect_with_current_query(request, reverse("contracts:maintenance_record_create", args=[pk]))


# 函数说明：封装可复用的业务处理。
@money_record_required
# 新增一批开票记录。
def invoice_record_create(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    return_state = list_return_state(request, contract.pk)
    if contract.invoice_status == "开收据":
        return redirect(return_state["return_url"])
    mode_labels = invoice_mode_labels(contract.invoice_status)

    if request.method == "POST":
        saved_count = save_typed_records_from_request(request, contract, {"开票": InvoiceRecord, "收票": PaymentRecord})
        if saved_count:
            log_operation(request, "新增", contract, object_type="票据记录", detail=f"saved records: {saved_count}")
            return redirect(return_state["return_url"])
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
                "amount_label": UI_LABELS["face_amount"],
                "actual_amount_field": True,
                "file_label": mode_labels["income_file"],
                **return_state,
                "active_nav": "contracts",
            },
        ),
    )


# 函数说明：封装可复用的业务处理。
@money_record_required
# 新增一批收票记录。
def payment_record_create(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    return_state = list_return_state(request, contract.pk)
    if contract.invoice_status != "开收据":
        return redirect_with_current_query(request, reverse("contracts:record_add", args=[pk]))
    mode_labels = invoice_mode_labels(contract.invoice_status)
    if request.method == "POST":
        saved_count = save_typed_records_from_request(request, contract, {"开据": PaymentRecord, "收据": PaymentRecord})
        if saved_count:
            log_operation(request, "新增", contract, object_type="收据记录", detail=f"saved records: {saved_count}")
            return redirect(return_state["return_url"])
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
                "amount_label": UI_LABELS["face_amount"],
                "actual_amount_field": True,
                "file_label": mode_labels["expense_file"],
                **return_state,
                "active_nav": "contracts",
            },
        ),
    )


# 函数说明：封装可复用的业务处理。
@admin_required
# 新增一批维护保养记录。
def maintenance_record_create(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    return_state = list_return_state(request, contract.pk)
    if not str(contract.original_contract_inner_number or "").strip():
        return redirect(return_state["return_url"])
    if request.method == "POST":
        saved_count = save_maintenance_records_from_request(request, contract)
        if saved_count:
            log_operation(request, "新增", contract, object_type="项目记录", detail=f"saved records: {saved_count}")
            return redirect(return_state["return_url"])
    project_labels = project_record_labels(contract.contract_type)
    record_file_number = normalize_contract_number_part(contract.original_contract_inner_number, 4)
    record_type_code = Contract.CONTRACT_TYPE_CODES.get(contract.contract_type, "06")
    record_sign_year = (contract.sign_date or contract.start_date or timezone.localdate()).year
    return render(
        request,
        "contracts/record_form.html",
        context_with_auth(
            request,
            {
                "contract": contract,
                "title": project_labels["new_title"],
                "today": timezone.localdate(),
                "month_field": True,
                "form_kind": "maintenance",
                "current_month": timezone.localdate().strftime("%Y-%m"),
                "record_file_number": record_file_number,
                "record_type_code": record_type_code,
                "record_sign_year": record_sign_year,
                "file_label": project_labels["file"],
                **return_state,
                "active_nav": "contracts",
            },
        ),
    )


# 显示回收站合同，超过一周的删除项会先自动清理。
# 视图函数：处理页面请求并返回响应。
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


# 视图函数：展示可归档合同列表。
@admin_required
def archive_list(request):
    contracts = archive_contracts_for_page()
    return render(
        request,
        "contracts/archive_list.html",
        context_with_auth(
            request,
            {
                "contracts": contracts,
                "active_nav": "archive",
            },
        ),
    )


# 视图函数：归档合同并生成归档快照。
@admin_required
def contract_archive(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method == "POST" and contract.status == "待归档" and not contract.uses_default_display_contract_number:
        old_display_number = contract.display_contract_number
        storage_location = normalize_storage_location_number(request.POST.get("storage_location_number"))
        if storage_location != normalize_storage_location_number(contract.storage_location_number):
            contract.storage_location_number = storage_location
            contract.save(update_fields=["storage_location_number", "updated_at"])
        contract.archive()
        archive_path = archive_contract_snapshot_to_file(contract, "contract archived")
        deleted_versions = clear_contract_snapshot_versions(contract)
        log_operation(
            request,
            "归档",
            contract,
            detail=(
                f"storage number: {old_display_number} -> {contract.display_contract_number}; "
                f"snapshot archived: {archive_path}; cleared versions: {deleted_versions}"
            ),
        )
    return redirect("contracts:archive_list")


# 视图函数：更新归档合同的存储位置编号。
@admin_required
def contract_storage_number_update(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method != "POST":
        return JsonResponse({"error": "只允许保存存储编号。"}, status=405)

    old_display_number = contract.display_contract_number
    storage_location = normalize_storage_location_number(request.POST.get("storage_location_number"))
    contract.storage_location_number = storage_location
    contract.save(update_fields=["storage_location_number", "updated_at"])
    log_operation(
        request,
        "修改",
        contract,
        detail=f"storage number: {old_display_number} -> {contract.display_contract_number}",
    )
    return JsonResponse(
        {
            "ok": True,
            "storage_location_number": storage_location,
            "display_contract_number": contract.display_contract_number,
        }
    )


# 视图函数：处理页面请求并返回响应。
@admin_required
# 从回收站恢复合同。
def contract_restore(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=True)
    if request.method == "POST":
        contract.restore_from_trash()
        log_operation(request, "恢复", contract, detail="restored from trash")
    return redirect("contracts:trash")


# 按筛选条件获取操作日志查询集。
def operation_logs_for_request(request):
    keyword = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    logs = OperationLog.objects.all()
    if keyword:
        logs = logs.filter(
            Q(username__icontains=keyword)
            | Q(role__icontains=keyword)
            | Q(object_type__icontains=keyword)
            | Q(object_name__icontains=keyword)
            | Q(detail__icontains=keyword)
            | Q(ip_address__icontains=keyword)
        )
    if action:
        logs = logs.filter(action=action)
    return logs, keyword, action


# 视图函数：展示操作日志列表。
@true_admin_required
def operation_log_list(request):
    logs, keyword, action = operation_logs_for_request(request)
    action_choices = OperationLog.objects.order_by().values_list("action", flat=True).distinct()
    return render(
        request,
        "contracts/operation_log_list.html",
        context_with_auth(
            request,
            {
                "logs": logs[:300],
                "keyword": keyword,
                "action_filter": action,
                "action_choices": action_choices,
                "active_nav": "operation_logs",
            },
        ),
    )


# 视图函数：导出操作日志 Excel。
@true_admin_required
def operation_log_export(request):
    if not is_super_admin_mode(request):
        return redirect("contracts:contract_list")
    logs, _, _ = operation_logs_for_request(request)
    headers = ["时间", "用户", "IP", "动作", "对象类型", "对象名称", "详情", "对象ID"]
    rows = [
        [
            timezone.localtime(log.created_at).strftime("%Y-%m-%d %H:%M:%S"),
            log.username,
            log.ip_address or "",
            log.action,
            log.object_type,
            log.object_name,
            log.detail,
            log.object_id,
        ]
        for log in logs
    ]
    response = HttpResponse(
        build_contract_list_xlsx(headers, rows, numeric_columns=set()),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="operation_logs.xlsx"'
    return response


# 渲染当前用户可见的使用文档页面。
def usage_docs(request):
    return render(
        request,
        "contracts/usage_docs.html",
        context_with_auth(
            request,
            {
                "role_label": role_label_for_request(request),
                "doc_sections": usage_docs_for_request(request),
                "active_nav": "docs",
            },
        ),
    )


# 视图函数：渲染系统设置页面并保存配置。
@admin_required
def settings_view(request):
    setting = AppSetting.current()
    host_ip = local_ip_address()
    can_edit_image_root_path = is_super_admin_mode(request)
    if request.method == "POST":
        form = AppSettingForm(
            request.POST,
            instance=setting,
            allow_image_root_path_edit=can_edit_image_root_path,
        )
        if form.is_valid():
            form.save()
            log_operation(request, "修改", setting, detail="updated system settings")
            return redirect("contracts:settings")
    else:
        form = AppSettingForm(instance=setting, allow_image_root_path_edit=can_edit_image_root_path)
    return render(
        request,
        "contracts/settings.html",
        context_with_auth(
            request,
            {
                "form": form,
                "host_ip": host_ip,
                "lan_url": f"http://{host_ip}:8000",
                "can_edit_image_root_path": can_edit_image_root_path,
                "active_nav": "settings",
            },
        ),
    )


# 函数说明：封装可复用的业务处理。
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
# 视图函数：处理页面请求并返回响应。
def login_view(request):
    ensure_special_superuser()
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
                request.session["super_admin_mode"] = user.username == SUPER_ADMIN_USERNAME and user.is_superuser
                if request.session["super_admin_mode"]:
                    return redirect("contracts:operation_log_list")
                return redirect("contracts:dashboard" if user.is_staff else "contracts:contract_list")
    else:
        form = LoginForm()
    return render(request, "contracts/login.html", {"form": form})


# 直接进入游客模式，不需要填写账号和密码。
# 函数说明：封装可复用的业务处理。
def guest_login_view(request):
    logout(request)
    request.session["guest_mode"] = True
    request.session["normal_mode"] = False
    request.session["super_admin_mode"] = False
    return redirect("contracts:contract_list")


# 普通用户现在使用账号密码登录，保留旧地址用于回到登录页。
# 函数说明：封装可复用的业务处理。
def normal_login_view(request):
    return redirect("contracts:login")


# 退出当前登录或游客会话。
# 函数说明：封装可复用的业务处理。
def logout_view(request):
    logout(request)
    request.session.flush()
    return redirect("contracts:login")
