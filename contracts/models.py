from datetime import timedelta
from decimal import Decimal
from pathlib import PurePath
import re

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


# 清理项目文件夹名称，避免 Windows 和 URL 路径中的非法字符。
# 函数说明：封装可复用的业务处理。
def safe_project_folder_name(contract: "Contract") -> str:
    raw_name = contract.contract_name or contract.contract_number or "未命名项目"
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).strip(" ._")
    return safe_name or "未命名项目"


# 函数说明：封装可复用的业务处理。
def safe_text_folder_name(value: str, fallback: str = "未分类") -> str:
    # 合同类型也会进入文件路径，和合同名称使用同一套 Windows 文件夹名清理规则。
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "").strip(" ._")
    return safe_name or fallback


# 获取默认文件预览根目录，通常就是项目 media 文件夹。
def default_preview_root_path() -> str:
    return str(settings.MEDIA_ROOT)


# 按年份增加日期，用于合同归档期限计算；2 月 29 日遇到非闰年时落到 2 月 28 日。
def add_years(value, years: int):
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


# 获取上传对象所属合同。
# 函数说明：封装可复用的业务处理。
def upload_contract_for(instance):
    return getattr(instance, "contract", instance)


# 按功能把项目文件保存到 media/contracts/合同类型/项目名称/功能文件夹/文件名。
def project_file_upload_path(instance, filename: str) -> str:
    contract = upload_contract_for(instance)
    safe_filename = PurePath(filename).name
    if instance.__class__.__name__ == "PaymentRecord" and contract.invoice_status == "开收据":
        subfolder = "收据文件"
    else:
        folder_map = {
            "Contract": "合同文件",
            "ContractFile": "合同文件",
            "SettlementFile": "结算文件",
            "InvoiceRecord": "发票文件",
            "PaymentRecord": "发票文件",
            "MaintenanceRecord": "维保文件",
        }
        subfolder = folder_map.get(instance.__class__.__name__, "其他文件")
    contract_type_folder = safe_text_folder_name(getattr(contract, "contract_type", ""))
    return f"contracts/{contract_type_folder}/{safe_project_folder_name(contract)}/{subfolder}/{safe_filename}"


# 定义合同主表、附件表、开票记录表、收票记录表、维护保养记录表和系统设置表。
# 模型类：定义数据库字段和业务属性。
class Contract(models.Model):
    # 合同类型用于表单下拉选择。
    CONTRACT_TYPES = [
        ("维保", "维保"),
        ("评估", "评估"),
        ("检测", "检测"),
        ("改造", "改造"),
        ("新建", "新建"),
        ("其他", "其他"),
    ]
    # 票据状态用于控制发票记录或收据记录入口。
    INVOICE_STATUS = [
        ("开收据", "开收据"),
        ("待开票", "待开票"),
        ("票已结", "票已结"),
    ]
    CONTRACT_TYPE_CODES = {
        value: f"{index:02d}"
        for index, (value, _label) in enumerate(CONTRACT_TYPES, start=1)
    }

    # 合同基础字段会映射成数据库 contracts_contract 表中的列。
    contract_name = models.CharField("合同名称", max_length=200)
    contract_number = models.CharField("合同编号", max_length=50, unique=True)
    original_contract_folder = models.CharField("原合同文件夹", max_length=100, blank=True)
    original_contract_inner_number = models.CharField("文件编号", max_length=100, blank=True)
    contract_type = models.CharField("合同类型", max_length=20, choices=CONTRACT_TYPES, default="维保")
    party_name = models.CharField("甲方名称", max_length=200)
    amount = models.DecimalField("金额", max_digits=14, decimal_places=2, default=0)
    invoice_status = models.CharField("是否开票", max_length=20, choices=INVOICE_STATUS, default="开收据")
    sign_date = models.DateField("签订日期", null=True, blank=True)
    start_date = models.DateField("开始日期", null=True, blank=True)
    end_date = models.DateField("截止日期", null=True, blank=True)
    responsible_person = models.CharField("负责人", max_length=100, blank=True)
    archive_years = models.PositiveSmallIntegerField("归档时间", default=3, validators=[MinValueValidator(1)])
    file = models.FileField("合同文件", upload_to=project_file_upload_path, null=True, blank=True)
    remark = models.TextField("备注", blank=True)
    is_archived = models.BooleanField("是否归档", default=False)
    archived_at = models.DateTimeField("归档时间", null=True, blank=True)
    is_deleted = models.BooleanField("是否删除", default=False)
    deleted_at = models.DateTimeField("删除时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", default=timezone.now)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    # 元数据类：配置字段、排序或显示名称。
    class Meta:
        # 默认按截止日期和编号排序，后台显示名称使用中文。
        ordering = ["end_date", "-id"]
        verbose_name = "合同"
        verbose_name_plural = "合同"

    # 方法说明：返回对象的可读名称。
    def __str__(self) -> str:
        # 后台和调试输出中显示合同名称与编号。
        return f"{self.contract_name}（{self.display_contract_number}）"

    # 函数说明：封装可复用的业务处理。
    @property
    def display_contract_number(self) -> str:
        # 列表显示编号由签订年份、文件编号、文件编号和合同类型编码组成。
        if self.uses_default_display_contract_number:
            return self.contract_number
        year = str((self.sign_date or self.start_date or self.created_at).year)
        folder = str(self.original_contract_folder).strip().zfill(2)
        inner_number = str(self.original_contract_inner_number).strip().zfill(4)
        type_code = self.CONTRACT_TYPE_CODES.get(self.contract_type, "06")
        return f"{year}{folder}{inner_number}{type_code}"

    # 函数说明：封装可复用的业务处理。
    @property
    def uses_default_display_contract_number(self) -> bool:
        # 文件编号或文件编号任一缺失时，列表回退显示默认自动编号。
        return not (self.original_contract_folder and self.original_contract_inner_number)

    # 函数说明：封装可复用的业务处理。
    @property
    def contract_number_sort_key(self) -> tuple[int, str]:
        # 默认编号排在最前，其余按显示编号降序。
        return (1 if self.uses_default_display_contract_number else 0, self.display_contract_number)

    # 函数说明：封装可复用的业务处理。
    @property
    def full_display_contract_number(self) -> str:
        # 详情和导出保留自动编号，并追加原合同文件夹和文件编号。
        parts = [
            self.contract_number,
            self.original_contract_folder,
            self.original_contract_inner_number,
        ]
        return "-".join(part for part in parts if part)

    # 函数说明：封装可复用的业务处理。
    @property
    def status(self) -> str:
        # 根据截止日期实时计算合同状态。
        if self.is_archived:
            return "已归档"
        if not self.end_date:
            return "进行中"

        today = timezone.localdate()
        if self.end_date <= add_years(today, -int(self.archive_years or 0)):
            return "待归档"
        if self.end_date < today:
            return "已到期"
        if self.end_date <= today + timedelta(days=30):
            return "即将到期"
        return "进行中"

    # 函数说明：封装可复用的业务处理。
    @property
    def status_class(self) -> str:
        # 把中文状态转换成页面样式类名。
        return {
            "已归档": "archived",
            "待归档": "archiving",
            "已到期": "expired",
            "即将到期": "expiring",
            "进行中": "active",
        }.get(self.status, "active")

    # 函数说明：封装可复用的业务处理。
    @property
    def latest_file(self):
        # 取排序最靠前的一份合同文件供列表和详情页预览。
        return self.files.order_by("sort_order", "id").first()

    # 函数说明：封装可复用的业务处理。
    @property
    def invoice_total(self) -> Decimal:
        # 兼容旧模板命名：这里返回项目收入。
        total = Decimal("0")
        for record in self.invoicerecord_set.all():
            if record.record_type not in {"收票", "收据"}:
                total += record.actual_amount if record.actual_amount is not None else record.amount
        for record in self.paymentrecord_set.all():
            if record.record_type in {"开票", "开据"}:
                total += record.actual_amount if record.actual_amount is not None else record.amount
        return total

    # 函数说明：封装可复用的业务处理。
    @property
    def payment_total(self) -> Decimal:
        # 兼容旧模板命名：这里返回项目支出。
        total = Decimal("0")
        for record in self.invoicerecord_set.all():
            if record.record_type in {"收票", "收据"}:
                total += record.actual_amount if record.actual_amount is not None else record.amount
        for record in self.paymentrecord_set.all():
            if record.record_type not in {"开票", "开据"}:
                total += record.actual_amount if record.actual_amount is not None else record.amount
        return total

    # 函数说明：封装可复用的业务处理。
    @property
    def payment_rate(self) -> Decimal:
        # 用收票金额除以合同金额，得到单个项目的收款率。
        return (self.payment_total / self.amount * Decimal("100")) if self.amount else Decimal("0")

    # 函数说明：封装可复用的业务处理。
    def move_to_trash(self) -> None:
        # 将合同移入回收站，保留一周内可恢复。
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

    # 函数说明：封装可复用的业务处理。
    def restore_from_trash(self) -> None:
        # 从回收站恢复合同，恢复后重新出现在合同列表和统计中。
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

    # 函数说明：封装可复用的业务处理。
    def archive(self) -> None:
        # 将达到归档条件的合同标记为已归档，保留在专门的归档项目页。
        self.is_archived = True
        self.archived_at = timezone.now()
        self.save(update_fields=["is_archived", "archived_at", "updated_at"])


# 保存合同可重复上传的附件文件。
# 模型类：定义数据库字段和业务属性。
class ContractFile(models.Model):
    contract = models.ForeignKey(Contract, related_name="files", on_delete=models.CASCADE, verbose_name="所属合同")
    file = models.FileField("合同文件", upload_to=project_file_upload_path)
    original_name = models.CharField("原文件名", max_length=255, blank=True)
    sort_order = models.PositiveIntegerField("排序", default=0)
    created_at = models.DateTimeField("上传时间", default=timezone.now)

    # 元数据类：配置字段、排序或显示名称。
    class Meta:
        # 文件按用户调整的顺序显示。
        ordering = ["sort_order", "id"]
        verbose_name = "合同文件"
        verbose_name_plural = "合同文件"

    # 方法说明：返回对象的可读名称。
    def __str__(self) -> str:
        # 优先显示原始文件名。
        return self.original_name or self.file.name


# 保存合同结算文件，和合同正文、记录附件分目录归档。
# 模型类：定义数据库字段和业务属性。
class SettlementFile(models.Model):
    contract = models.ForeignKey(Contract, related_name="settlement_files", on_delete=models.CASCADE, verbose_name="所属合同")
    file = models.FileField("结算文件", upload_to=project_file_upload_path)
    original_name = models.CharField("原文件名", max_length=255, blank=True)
    created_at = models.DateTimeField("上传时间", default=timezone.now)

    # 元数据类：配置字段、排序或显示名称。
    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "结算文件"
        verbose_name_plural = "结算文件"

    # 方法说明：返回对象的可读名称。
    def __str__(self) -> str:
        return self.original_name or self.file.name


# 开票和收票记录共用的抽象基础表。
# 模型类：定义数据库字段和业务属性。
class RecordBase(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, verbose_name="所属合同")
    record_date = models.DateField("日期")
    record_type = models.CharField("类型", max_length=20, blank=True)
    amount = models.DecimalField("金额", max_digits=14, decimal_places=2, default=0)
    actual_amount = models.DecimalField("实际金额", max_digits=14, decimal_places=2, null=True, blank=True)
    file = models.FileField("附件", upload_to=project_file_upload_path, null=True, blank=True)
    remark = models.CharField("备注", max_length=255, blank=True)
    created_at = models.DateTimeField("创建时间", default=timezone.now)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    # 元数据类：配置字段、排序或显示名称。
    class Meta:
        # 抽象模型不单独建表，只给子类复用字段。
        abstract = True
        ordering = ["record_date", "id"]

    # 方法说明：返回对象的可读名称。
    def __str__(self) -> str:
        # 后台中显示记录所属合同、日期和金额。
        return f"{self.contract.contract_name} - {self.record_date} - {self.amount}"


# 开票记录表。
# 模型类：定义数据库字段和业务属性。
class InvoiceRecord(RecordBase):
    # 元数据类：配置字段、排序或显示名称。
    class Meta(RecordBase.Meta):
        verbose_name = "开票记录"
        verbose_name_plural = "开票记录"


# 收票记录表。
# 模型类：定义数据库字段和业务属性。
class PaymentRecord(RecordBase):
    # 元数据类：配置字段、排序或显示名称。
    class Meta(RecordBase.Meta):
        verbose_name = "收票记录"
        verbose_name_plural = "收票记录"


# 维护保养记录表。
# 模型类：定义数据库字段和业务属性。
class MaintenanceRecord(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, verbose_name="所属合同")
    record_date = models.DateField("日期")
    month = models.CharField("月份", max_length=30)
    file = models.FileField("附件", upload_to=project_file_upload_path, null=True, blank=True)
    remark = models.CharField("备注", max_length=255, blank=True)
    created_at = models.DateTimeField("创建时间", default=timezone.now)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    # 元数据类：配置字段、排序或显示名称。
    class Meta:
        # 维护保养记录按日期和创建顺序显示。
        ordering = ["record_date", "id"]
        verbose_name = "维护保养记录"
        verbose_name_plural = "维护保养记录"

    # 方法说明：返回对象的可读名称。
    def __str__(self) -> str:
        # 后台中显示记录所属合同、日期和月份。
        return f"{self.contract.contract_name} - {self.record_date} - {self.month}"


# 保存系统级开关配置。
# 模型类：定义数据库字段和业务属性。
class AppSetting(models.Model):
    delete_source_file = models.BooleanField("上传时是否删除原文件", default=False)
    image_root_path = models.CharField(
        "图片保存位置",
        max_length=500,
        default=r"C:\Users\YF\Desktop\ocr_image_renamer\整理后图片",
        blank=True,
    )
    preview_root_path = models.CharField(
        "文件预览位置",
        max_length=500,
        default=default_preview_root_path,
        blank=True,
    )
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    # 元数据类：配置字段、排序或显示名称。
    class Meta:
        verbose_name = "系统设置"
        verbose_name_plural = "系统设置"

    # 方法说明：返回对象的可读名称。
    def __str__(self) -> str:
        # 后台中显示固定的系统设置名称。
        return "系统设置"

    # 函数说明：封装可复用的业务处理。
    @classmethod
    def current(cls) -> "AppSetting":
        # 获取唯一的系统设置记录，首次访问时自动创建。
        setting, _ = cls.objects.get_or_create(pk=1)
        return setting
