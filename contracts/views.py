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
    safe_project_folder_name,
    safe_text_folder_name,
)


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


def parse_form_date(value):
    if not value:
        return None
    return parse_date(str(value).strip().replace("/", "-"))


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


def client_ip_address(request) -> str | None:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR") or None


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


def contract_file_folder(contract: Contract) -> Path:
    contract_type_folder = safe_text_folder_name(contract.contract_type)
    contract_number_folder = safe_project_folder_name(contract)
    return Path(settings.MEDIA_ROOT) / "contracts" / contract_type_folder / contract_number_folder


def ensure_contract_file_folder(contract: Contract) -> Path:
    folder = contract_file_folder(contract)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


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
def preview_file_path(file_field) -> Path:
    relative_name = typed_preview_file_name(file_field)
    return Path(settings.MEDIA_ROOT) / relative_name


def contract_for_file_field(file_field) -> Contract | None:
    instance = getattr(file_field, "instance", None)
    if instance is None:
        return None
    record = getattr(instance, "record", None)
    if record is not None:
        return getattr(record, "contract", None)
    return getattr(instance, "contract", instance if isinstance(instance, Contract) else None)


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
def record_file_version_model_for(record):
    for record_model, version_model in RECORD_FILE_VERSION_MODELS.items():
        if isinstance(record, record_model):
            return version_model
    return None


# 新增一条记录附件版本，并让记录自身指向最新版本文件。
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
def prune_record_file_versions(record, limit: int = RECORD_FILE_VERSION_LIMIT) -> None:
    version_model = record_file_version_model_for(record)
    if version_model is None:
        return
    stale_versions = list(version_model.objects.filter(record=record).order_by("-created_at", "-id")[limit:])
    for version in stale_versions:
        delete_file_from_storage(version.file)
        version.delete()


# 删除记录时清理它的所有附件版本文件。
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
            actual_amount=actual_amount or None,
            remark=remark,
        )
        attach_record_file_version(record, uploaded_file)
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
            actual_amount=actual_amount or None,
            remark=remark,
        )
        attach_record_file_version(record, uploaded_file)
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
    # 实际金额优先用于统计；未填实际金额时回退到票面金额。
    return record.actual_amount if record.actual_amount is not None else record.amount


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
        "invoice_rate": "收款率",
        "receipt_primary": "收票金额" if has_invoice else "收据金额",
        "receipt_secondary": "付款金额",
        "receipt_rate": "利润率",
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
    remarks = request.POST.getlist("remark")
    saved_count = 0
    for index, record_date in enumerate(dates):
        month = months[index] if index < len(months) else ""
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
            remark=remark,
        )
        attach_record_file_version(record, uploaded_file)
        saved_count += 1
    return saved_count


# 生成合同类型扩展记录编号：文件编号 + 记录日期年份 + 周期序列 + 类型编号。
def maintenance_record_number(contract: Contract, record_date) -> str:
    record_year = record_date.year if hasattr(record_date, "year") else int(str(record_date)[:4])
    sign_year = (contract.sign_date or contract.start_date or timezone.localdate()).year
    file_number = normalize_contract_number_part(contract.original_contract_inner_number, 4)
    type_code = Contract.CONTRACT_TYPE_CODES.get(contract.contract_type, "06")
    period_sequence = f"{record_year - sign_year + 1:02d}"
    return f"{file_number}{record_year}{period_sequence}{type_code}"


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
def archive_pending_contracts() -> list[Contract]:
    contracts = Contract.objects.filter(is_deleted=False, end_date__isnull=False).order_by("end_date")
    return [contract for contract in contracts if contract.status == "待归档"]


# 查询归档页合同：待归档排前，已归档在后，各自按截止日期升序。
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
# 函数说明：封装可复用的业务处理。
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


# 按签订年份汇总合同金额，仅用于年度总览趋势图。
def yearly_signed_contract_amounts(year: int) -> list[float]:
    start_year = year - 11
    totals_by_year = {unit: Decimal("0") for unit in range(start_year, year + 1)}
    for contract in Contract.objects.filter(is_deleted=False):
        signed_year = (contract.sign_date or contract.start_date or contract.created_at).year
        if start_year <= signed_year <= year:
            totals_by_year[signed_year] += contract.amount
    return [float(totals_by_year[unit]) for unit in range(start_year, year + 1)]


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
# 函数说明：封装可复用的业务处理。
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


# 函数说明：封装可复用的业务处理。
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

    # 内部函数：按统计范围生成图表时间标签。
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
# 函数说明：封装可复用的业务处理。
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

    # 内部函数：按统计范围生成图表时间标签。
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


# 视图函数：处理页面请求并返回响应。
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
                "date": record.record_date.strftime("%Y-%m-%d"),
                "record_number": maintenance_record_number(contract, record.record_date),
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
@true_admin_required
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
        "show_contract_amount_line": chart_period == "year",
        "contract_amounts": yearly_signed_contract_amounts(chart_year) if chart_period == "year" else [],
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

    recent_contracts = list(active_contracts.order_by("-contract_number", "-id")[:6])
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


def json_safe_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "name"):
        return value.name
    return value


def model_snapshot(obj) -> dict:
    return {field.name: json_safe_value(getattr(obj, field.attname)) for field in obj._meta.fields}


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


def versions_for_object(obj) -> list[dict]:
    versions = Version.objects.get_for_object(obj).select_related("revision", "revision__user")
    return [version_snapshot(version) for version in versions]


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


def contract_snapshot_objects(contract: Contract) -> list:
    objects = [contract]
    for _, queryset in contract_snapshot_related_groups(contract):
        objects.extend(list(queryset))
    return objects


def archive_contract_snapshot_to_file(contract: Contract, reason: str) -> Path:
    payload = contract_snapshot_payload(contract)
    payload["archive_reason"] = reason
    archive_dir = settings.BASE_DIR / "archives" / "contracts"
    archive_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^0-9A-Za-z_-]+", "_", contract.contract_number or str(contract.pk)).strip("_")
    archive_path = archive_dir / f"contract_snapshot_{contract.pk}_{safe_name}_{timezone.localtime():%Y%m%d%H%M%S}.json"
    archive_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return archive_path


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
    total_amount = sum((contract.amount for contract in contracts), Decimal("0"))
    contract_count = len(contracts)
    active_contracts = [contract for contract in contracts if contract.status == "进行中"]
    expired_contracts = [contract for contract in contracts if contract.status == "已到期"]
    active_total_amount = sum((contract.amount for contract in active_contracts), Decimal("0"))
    expired_total_amount = sum((contract.amount for contract in expired_contracts), Decimal("0"))
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
        UI_LABELS["contract_number"],
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
                contract.full_display_contract_number,
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
        build_contract_list_xlsx(headers, rows),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="contract_list.xlsx"'
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
}


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


def normalize_import_value(field_name: str, value):
    text = normalize_import_cell(value)
    if not text:
        return ""

    if field_name in {"sign_date", "start_date", "end_date"}:
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

    if field_name == "archive_years":
        if re.fullmatch(r"\d+\.0+", text):
            return str(int(float(text)))

    return text


def xlsx_column_number(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha()).upper()
    number = 0
    for char in letters:
        number = number * 26 + ord(char) - 64
    return number


def xlsx_plain_text(element) -> str:
    if element is None:
        return ""
    return "".join(element.itertext())


def parse_contract_import_xlsx_with_stdlib(uploaded_file):
    uploaded_file.seek(0)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(uploaded_file) as archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [xlsx_plain_text(item) for item in shared_root.findall("x:si", namespace)]
        sheet_root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    parsed_rows = []
    for row_element in sheet_root.findall(".//x:sheetData/x:row", namespace):
        row_values = {}
        for cell in row_element.findall("x:c", namespace):
            ref = cell.attrib.get("r", "")
            column_number = xlsx_column_number(ref)
            cell_type = cell.attrib.get("t", "")
            value = ""
            if cell_type == "inlineStr":
                value = xlsx_plain_text(cell.find("x:is", namespace))
            else:
                raw_value = xlsx_plain_text(cell.find("x:v", namespace))
                if cell_type == "s" and raw_value.isdigit() and int(raw_value) < len(shared_strings):
                    value = shared_strings[int(raw_value)]
                else:
                    value = raw_value
            row_values[column_number] = value
        if row_values:
            max_column = max(row_values)
            parsed_rows.append([row_values.get(index, "") for index in range(1, max_column + 1)])
    return parsed_rows


def parse_contract_import_xlsx(uploaded_file):
    try:
        from openpyxl import load_workbook
    except ImportError:
        rows = parse_contract_import_xlsx_with_stdlib(uploaded_file)
    else:
        uploaded_file.seek(0)
        workbook = load_workbook(uploaded_file, read_only=True, data_only=True)
        worksheet = workbook.active
        rows = list(worksheet.iter_rows(values_only=True))
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
        if folder and inner_number:
            base_date = cleaned.get("sign_date") or cleaned.get("start_date") or timezone.localdate()
            contract_type = cleaned.get("contract_type") or data.get("contract_type")
            display_number = (
                f"{base_date.year}"
                f"{folder}"
                f"{inner_number}"
                f"{Contract.CONTRACT_TYPE_CODES.get(contract_type, '06')}"
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


def contract_import_preview_context(
    request,
    upload_form,
    results=None,
    payload="",
    parse_errors=None,
    confirm_error="",
    completed=False,
):
    results = results or []
    valid_count = sum(1 for row in results if row["ok"])
    error_count = len(results) - valid_count
    allow_partial_import_with_errors = AppSetting.current().allow_partial_import_with_errors
    can_confirm_import = valid_count > 0 and (not error_count or allow_partial_import_with_errors)
    return context_with_auth(
        request,
        {
            "form": upload_form,
            "columns": CONTRACT_IMPORT_COLUMNS,
            "preview_columns": CONTRACT_IMPORT_PREVIEW_COLUMNS,
            "results": results,
            "payload": payload,
            "parse_errors": parse_errors or [],
            "valid_count": valid_count,
            "error_count": error_count,
            "allow_partial_import_with_errors": allow_partial_import_with_errors,
            "can_confirm_import": can_confirm_import,
            "confirm_error": confirm_error,
            "completed": completed,
            "active_nav": "contracts",
        },
    )


@true_admin_required
def contract_import_template(request):
    headers = [label for _field, label in CONTRACT_IMPORT_COLUMNS]
    rows = [
        [
            "示例维保合同",
            "维保",
            "某某单位",
            10000,
            "开收据",
            timezone.localdate().strftime("%Y-%m-%d"),
            timezone.localdate().strftime("%Y-%m-%d"),
            (timezone.localdate() + timedelta(days=365)).strftime("%Y-%m-%d"),
            "张三",
            "01",
            "0001",
            3,
            "",
        ]
    ]
    response = HttpResponse(
        build_contract_list_xlsx(headers, rows, numeric_columns={4, 12}),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="contract_import_template.xlsx"'
    return response


@true_admin_required
def contract_import(request):
    if request.method == "POST" and request.POST.get("action") == "confirm":
        try:
            parsed_rows = signing.loads(request.POST.get("payload", ""), max_age=3600)
        except signing.BadSignature:
            context = contract_import_preview_context(
                request,
                ContractImportUploadForm(),
                parse_errors=["导入预览已失效，请重新上传 Excel。"],
            )
            return render(request, "contracts/contract_import.html", context)

        try:
            contract_numbers = default_contract_numbers(max(len(parsed_rows), 1))
            results = validate_contract_import_rows(parsed_rows, contract_numbers)
        except DjangoValidationError as exc:
            context = contract_import_preview_context(
                request,
                ContractImportUploadForm(),
                parse_errors=[str(exc)],
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
            )
            return render(request, "contracts/contract_import.html", context)

        created_count = 0
        with transaction.atomic():
            for index, row in enumerate(results):
                if not row["ok"]:
                    continue
                data = row["data"].copy()
                data["contract_number"] = contract_numbers[index]
                form = ContractForm(data=data)
                if not form.is_valid():
                    raise RuntimeError("确认导入时数据校验失败，请重新上传 Excel。")
                contract = form.save()
                ensure_contract_image_folder(contract)
                log_operation(request, "新增", contract, detail=f"Excel import row: {row['row_number']}")
                created_count += 1
        return render(
            request,
            "contracts/contract_import.html",
            contract_import_preview_context(
                request,
                ContractImportUploadForm(),
                results=results,
                parse_errors=[f"已导入 {created_count} 条合同。"],
                completed=True,
            ),
        )

    if request.method == "POST":
        upload_form = ContractImportUploadForm(request.POST, request.FILES)
        if upload_form.is_valid():
            try:
                parsed_rows, parse_errors = parse_contract_import_xlsx(upload_form.cleaned_data["excel_file"])
                try:
                    results = validate_contract_import_rows(parsed_rows) if parsed_rows and not parse_errors else []
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
            )
            return render(request, "contracts/contract_import.html", context)
    else:
        upload_form = ContractImportUploadForm()
    return render(
        request,
        "contracts/contract_import.html",
        contract_import_preview_context(request, upload_form),
    )


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
        record.record_number = maintenance_record_number(contract, record.record_date)
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


@admin_required
def contract_archive(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=False)
    if request.method == "POST" and contract.status == "待归档" and not contract.uses_default_display_contract_number:
        contract.archive()
        archive_path = archive_contract_snapshot_to_file(contract, "contract archived")
        deleted_versions = clear_contract_snapshot_versions(contract)
        log_operation(request, "归档", contract, detail=f"snapshot archived: {archive_path}; cleared versions: {deleted_versions}")
    return redirect("contracts:archive_list")


# 视图函数：处理页面请求并返回响应。
@admin_required
# 从回收站恢复合同。
def contract_restore(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, is_deleted=True)
    if request.method == "POST":
        contract.restore_from_trash()
        log_operation(request, "恢复", contract, detail="restored from trash")
    return redirect("contracts:trash")


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


@admin_required
def settings_view(request):
    setting = AppSetting.current()
    host_ip = local_ip_address()
    if request.method == "POST":
        form = AppSettingForm(request.POST, instance=setting)
        if form.is_valid():
            form.save()
            log_operation(request, "修改", setting, detail="updated system settings")
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
