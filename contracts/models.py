from datetime import timedelta
from decimal import Decimal
from pathlib import PurePath
import re

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import NoReverseMatch, reverse
from django.utils import timezone


# 将编号片段提取为固定宽度的纯数字字符串。
def normalize_contract_number_part(value, width: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d+\.0+", text):
        text = str(int(float(text)))
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    return digits[-width:].zfill(width)


# 规范合同位置编号，空值回退为 000。
def normalize_storage_location_number(value) -> str:
    return normalize_contract_number_part(value, 3) or "000"


# 规范项目记录位置编号，空值回退为 000000。
def normalize_record_position_number(value) -> str:
    return normalize_contract_number_part(value, 6) or "000000"


# 规范项目记录年月编号，空值按记录日期回退为 YYMM。
def normalize_record_date_number(value, record_date=None) -> str:
    date_number = normalize_contract_number_part(value, 4)
    if date_number:
        return date_number
    if record_date:
        return f"{str(record_date.year)[-2:]}{record_date.month:02d}"
    return "0000"


# 规范项目记录分册编号，空值回退为 01。
def normalize_record_volume_number(value) -> str:
    return normalize_contract_number_part(value, 2) or "01"


# 清理项目文件夹名称，避免 Windows 和 URL 路径中的非法字符。
# 函数说明：封装可复用的业务处理。
def safe_project_folder_name(contract: "Contract") -> str:
    raw_name = contract.contract_number or "未编号合同"
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).strip(" ._")
    return safe_name or "未编号合同"


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
    record = getattr(instance, "record", None)
    if record is not None:
        return record.contract
    return getattr(instance, "contract", instance)


# 按功能把项目文件保存到 media/contracts/合同类型/项目名称/功能文件夹/文件名。
def project_file_upload_path(instance, filename: str) -> str:
    contract = upload_contract_for(instance)
    upload_owner = getattr(instance, "record", instance)
    safe_filename = PurePath(filename).name
    if upload_owner.__class__.__name__ == "PaymentRecord" and contract.invoice_status == "开收据":
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
        subfolder = folder_map.get(upload_owner.__class__.__name__, "其他文件")
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
    ]
    # 票据状态用于控制发票记录或收据记录入口。
    INVOICE_STATUS = [
        ("开收据", "开收据"),
        ("待开票", "待开票"),
        ("票已结", "票已结"),
    ]
    STORAGE_MODES = [
        ("文件夹", "文件夹"),
        ("仅文档", "仅文档"),
    ]
    CONTRACT_TYPE_SEQUENCE_CODES = {
        "维保": "1",
        "评估": "2",
        "检测": "3",
        "改造": "4",
        "新建": "5",
    }
    CONTRACT_TYPE_CODES = {
        "维保": "W",
        "评估": "P",
        "检测": "J",
        "改造": "G",
        "新建": "X",
    }

    # 合同基础字段会映射成数据库 contracts_contract 表中的列。
    contract_name = models.CharField("合同名称", max_length=200)
    contract_number = models.CharField("合同编号", max_length=50, unique=True)
    original_contract_folder = models.CharField("原合同文件夹", max_length=100, blank=True)
    original_contract_inner_number = models.CharField("文件编号", max_length=100, blank=True)
    storage_location_number = models.CharField("位置编号", max_length=100, default="000", blank=True)
    contract_type = models.CharField("合同类型", max_length=20, choices=CONTRACT_TYPES, default="维保")
    storage_mode = models.CharField("保存模式", max_length=20, choices=STORAGE_MODES, default="文件夹")
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
        # 合同显示编号由类型 1 位、年份 2 位和文件编号 5 位组成。
        if self.uses_default_display_contract_number:
            return self.contract_number
        year = str((self.sign_date or self.start_date or self.created_at).year)
        file_number = normalize_contract_number_part(self.original_contract_inner_number, 5)
        type_code = self.CONTRACT_TYPE_CODES.get(self.contract_type, "")
        return f"{type_code}{year[-2:]}{file_number}"

    # 函数说明：封装可复用的业务处理。
    @property
    def uses_default_display_contract_number(self) -> bool:
        # 文件编号缺失时，列表回退显示默认自动编号。
        return not normalize_contract_number_part(self.original_contract_inner_number, 5)

    @property
    def is_document_only(self) -> bool:
        return self.storage_mode == "仅文档"

    @property
    def archive_due_date(self):
        if self.is_document_only:
            return add_years(self.start_date, int(self.archive_years or 0)) if self.start_date else None
        return self.end_date

    @property
    def missing_storage_position(self) -> bool:
        if self.is_document_only:
            return False
        folder_number = normalize_contract_number_part(self.original_contract_folder, 3)
        storage_number = normalize_storage_location_number(self.storage_location_number)
        return not folder_number or folder_number == "000" or storage_number == "000"

    @property
    def business_number_css_class(self) -> str:
        if self.uses_default_display_contract_number or self.missing_storage_position:
            return "default-contract-number"
        return ""

    @property
    def project_years(self) -> int:
        if not self.start_date or not self.end_date:
            return 0
        return max(self.end_date.year - self.start_date.year + 1, 1)

    # 函数说明：封装可复用的业务处理。
    @property
    def contract_number_sort_key(self) -> tuple[int, str]:
        # 默认编号排在最前，其余按显示编号降序。
        return (1 if self.uses_default_display_contract_number else 0, self.display_contract_number)

    # 函数说明：封装可复用的业务处理。
    @property
    def full_display_contract_number(self) -> str:
        # 详情页和导出使用“默认合同编号 + 显示合同编号”，中间不额外加分隔符。
        display_number = self.display_contract_number
        if display_number == self.contract_number:
            return self.contract_number
        return f"{self.contract_number}   {display_number}"

    @property
    def archive_number(self) -> str:
        # 存档编号由文件夹编号 3 位和位置编号 3 位组成。
        if self.is_document_only:
            return ""
        folder_number = normalize_contract_number_part(self.original_contract_folder, 3)
        if not folder_number:
            return ""
        location_number = normalize_storage_location_number(self.storage_location_number)
        return f"{folder_number}{location_number}"

    @property
    def archive_number_display(self) -> str:
        # 归档页编辑中即使文件夹编号为空，也用 000 补齐显示。
        if self.is_document_only:
            return ""
        folder_number = normalize_contract_number_part(self.original_contract_folder, 3) or "000"
        location_number = normalize_storage_location_number(self.storage_location_number)
        return f"{folder_number}{location_number}"

    # 函数说明：封装可复用的业务处理。
    @property
    def status(self) -> str:
        # 根据截止日期实时计算合同状态。
        if self.is_archived:
            return "已归档"
        if self.is_document_only:
            archive_due_date = self.archive_due_date
            if archive_due_date and archive_due_date <= timezone.localdate():
                return "待归档"
            return "进行中"
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
                total += record.actual_amount if record.actual_amount is not None else Decimal("0")
        for record in self.paymentrecord_set.all():
            if record.record_type in {"开票", "开据"}:
                total += record.actual_amount if record.actual_amount is not None else Decimal("0")
        return total

    # 函数说明：封装可复用的业务处理。
    @property
    def payment_total(self) -> Decimal:
        # 兼容旧模板命名：这里返回项目支出。
        total = Decimal("0")
        for record in self.invoicerecord_set.all():
            if record.record_type in {"收票", "收据"}:
                total += record.actual_amount if record.actual_amount is not None else Decimal("0")
        for record in self.paymentrecord_set.all():
            if record.record_type not in {"开票", "开据"}:
                total += record.actual_amount if record.actual_amount is not None else Decimal("0")
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
    date_number = models.CharField("年月编号", max_length=4, default="0000", blank=True)
    record_position_number = models.CharField("位置编号", max_length=100, default="000000", blank=True)
    storage_location_number = models.CharField("分册编号", max_length=100, default="01", blank=True)
    is_archived = models.BooleanField("是否归档", default=False)
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


# 保存单个合同分册对应的实序编号，避免每条记录重复存储同一档案位置。
class MaintenanceRecordVolumeSequence(models.Model):
    contract = models.ForeignKey(
        Contract,
        related_name="record_volume_sequences",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name="所属合同",
    )
    released_contract = models.ForeignKey(
        Contract,
        related_name="released_record_volume_sequences",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="释放来源合同",
    )
    storage_location_number = models.CharField("分册编号", max_length=100, default="01", blank=True)
    real_sequence_number = models.IntegerField("实序编号", default=0)
    shelf_position_number = models.CharField("排位", max_length=100, default="000000", blank=True)
    is_reserved = models.BooleanField("是否预留", default=False)
    created_at = models.DateTimeField("创建时间", default=timezone.now)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["real_sequence_number", "contract_id", "storage_location_number"]
        unique_together = ("contract", "storage_location_number")
        verbose_name = "项目记录分册实序"
        verbose_name_plural = "项目记录分册实序"

    def __str__(self) -> str:
        contract_name = self.contract.contract_name if self.contract_id else "空排位"
        return f"{contract_name} - {self.storage_location_number or '空'} - {self.real_sequence_number} - {self.shelf_position_number}"


# 保存开票记录附件的每次上传版本，记录本身的 file 字段指向最新版本。
class InvoiceRecordFileVersion(models.Model):
    record = models.ForeignKey(InvoiceRecord, related_name="file_versions", on_delete=models.CASCADE, verbose_name="所属开票记录")
    file = models.FileField("附件", upload_to=project_file_upload_path)
    original_name = models.CharField("原文件名", max_length=255, blank=True)
    created_at = models.DateTimeField("上传时间", default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "开票附件版本"
        verbose_name_plural = "开票附件版本"

    def __str__(self) -> str:
        return self.original_name or self.file.name


# 保存收票/收据记录附件的每次上传版本，记录本身的 file 字段指向最新版本。
class PaymentRecordFileVersion(models.Model):
    record = models.ForeignKey(PaymentRecord, related_name="file_versions", on_delete=models.CASCADE, verbose_name="所属收票记录")
    file = models.FileField("附件", upload_to=project_file_upload_path)
    original_name = models.CharField("原文件名", max_length=255, blank=True)
    created_at = models.DateTimeField("上传时间", default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "收票附件版本"
        verbose_name_plural = "收票附件版本"

    def __str__(self) -> str:
        return self.original_name or self.file.name


# 保存项目记录附件的每次上传版本，记录本身的 file 字段指向最新版本。
class MaintenanceRecordFileVersion(models.Model):
    record = models.ForeignKey(MaintenanceRecord, related_name="file_versions", on_delete=models.CASCADE, verbose_name="所属项目记录")
    file = models.FileField("附件", upload_to=project_file_upload_path)
    original_name = models.CharField("原文件名", max_length=255, blank=True)
    created_at = models.DateTimeField("上传时间", default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "项目记录附件版本"
        verbose_name_plural = "项目记录附件版本"

    def __str__(self) -> str:
        return self.original_name or self.file.name


# 保存系统级开关配置。
# 模型类：定义数据库字段和业务属性。
class AppSetting(models.Model):
    allow_partial_import_with_errors = models.BooleanField("Excel 导入存在错误时仍导入通过行", default=False)
    allow_force_contract_import_update = models.BooleanField("合同导入允许强行修改匹配行", default=False)
    record_position_cabinet_number = models.PositiveSmallIntegerField("记录位置柜号", default=1)
    record_position_end_cabinet_number = models.PositiveSmallIntegerField("记录位置终止柜号", default=99)
    record_position_column_count = models.PositiveSmallIntegerField("记录位置栏目量", default=12)
    record_position_column_capacity = models.CharField("记录位置栏目存放数", max_length=100, default="10")
    record_position_start_file_number = models.CharField("记录位置起始界限点", max_length=100, default="3441")
    record_position_start_column = models.PositiveSmallIntegerField("记录位置存放栏目", default=5)
    record_position_enable_insert_sort = models.BooleanField("记录位置启用插入重排序", default=False)
    record_position_force_empty_slot = models.BooleanField("记录位置强制空排位", default=False)
    record_position_reserved_slots = models.TextField("记录位置预留排位", blank=True)
    record_position_direction = models.CharField(
        "记录位置存放逻辑",
        max_length=20,
        choices=[("increment", "递增1"), ("decrement", "递减1")],
        default="decrement",
    )
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


# 保存系统操作审计日志，用于列表展示、导出和撤回判断。
class OperationLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="操作用户",
    )
    username = models.CharField("用户名", max_length=150, blank=True)
    role = models.CharField("角色", max_length=50, blank=True)
    action = models.CharField("动作", max_length=50)
    object_type = models.CharField("对象类型", max_length=100, blank=True)
    object_name = models.CharField("对象名称", max_length=255, blank=True)
    object_id = models.CharField("对象ID", max_length=50, blank=True)
    content_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="对象模型",
    )
    object_pk = models.CharField("对象主键", max_length=50, blank=True)
    detail = models.TextField("详情", blank=True)
    ip_address = models.GenericIPAddressField("IP地址", null=True, blank=True)
    created_at = models.DateTimeField("操作时间", default=timezone.now)
    is_undone = models.BooleanField("是否已撤回", default=False)
    undone_at = models.DateTimeField("撤回时间", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "操作日志"
        verbose_name_plural = "操作日志"

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.username} {self.action}"

    @property
    def history_url(self) -> str:
        if not self.content_type_id or not self.object_pk:
            return ""
        url_name = f"admin:{self.content_type.app_label}_{self.content_type.model}_history"
        try:
            return reverse(url_name, args=[self.object_pk])
        except NoReverseMatch:
            return ""
