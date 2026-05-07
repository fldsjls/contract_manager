from datetime import timedelta
from decimal import Decimal
from pathlib import PurePath
import re

from django.db import models
from django.utils import timezone


# 清理项目文件夹名称，避免 Windows 和 URL 路径中的非法字符。
def safe_project_folder_name(contract: "Contract") -> str:
    raw_name = contract.contract_name or contract.contract_number or "未命名项目"
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).strip(" ._")
    return safe_name or "未命名项目"


# 获取上传对象所属合同。
def upload_contract_for(instance):
    return getattr(instance, "contract", instance)


# 所有项目文件统一保存到 media/contracts/项目名称/文件名。
def project_file_upload_path(instance, filename: str) -> str:
    contract = upload_contract_for(instance)
    safe_filename = PurePath(filename).name
    return f"contracts/{safe_project_folder_name(contract)}/{safe_filename}"


# 定义合同主表、附件表、开票记录表、收票记录表和系统设置表。
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
    # 是否开票用于控制开票记录是否可用。
    INVOICE_STATUS = [
        ("不开票", "不开票"),
        ("开票", "开票"),
    ]

    # 合同基础字段会映射成数据库 contracts_contract 表中的列。
    contract_name = models.CharField("合同名称", max_length=200)
    contract_number = models.CharField("合同编号", max_length=50, unique=True)
    contract_type = models.CharField("合同类型", max_length=20, choices=CONTRACT_TYPES, default="维保")
    party_name = models.CharField("甲方名称", max_length=200)
    amount = models.DecimalField("金额", max_digits=14, decimal_places=2, default=0)
    invoice_status = models.CharField("是否开票", max_length=20, choices=INVOICE_STATUS, default="不开票")
    sign_date = models.DateField("签订日期", null=True, blank=True)
    start_date = models.DateField("开始日期", null=True, blank=True)
    end_date = models.DateField("截止日期", null=True, blank=True)
    file = models.FileField("合同文件", upload_to=project_file_upload_path, null=True, blank=True)
    remark = models.TextField("备注", blank=True)
    is_deleted = models.BooleanField("是否删除", default=False)
    deleted_at = models.DateTimeField("删除时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", default=timezone.now)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        # 默认按截止日期和编号排序，后台显示名称使用中文。
        ordering = ["end_date", "-id"]
        verbose_name = "合同"
        verbose_name_plural = "合同"

    def __str__(self) -> str:
        # 后台和调试输出中显示合同名称与编号。
        return f"{self.contract_name}（{self.contract_number}）"

    @property
    def status(self) -> str:
        # 根据截止日期实时计算合同状态。
        if not self.end_date:
            return "进行中"

        today = timezone.localdate()
        if self.end_date < today:
            return "已到期"
        if self.end_date <= today + timedelta(days=30):
            return "即将到期"
        return "进行中"

    @property
    def status_class(self) -> str:
        # 把中文状态转换成页面样式类名。
        return {
            "已到期": "expired",
            "即将到期": "expiring",
            "进行中": "active",
        }.get(self.status, "active")

    @property
    def latest_file(self):
        # 取排序最靠前的一份合同文件供列表和详情页预览。
        return self.files.order_by("sort_order", "id").first()

    @property
    def invoice_total(self) -> Decimal:
        # 汇总当前合同的全部开票记录金额。
        return self.invoicerecord_set.aggregate(total=models.Sum("amount"))["total"] or Decimal("0")

    @property
    def payment_total(self) -> Decimal:
        # 汇总当前合同的全部收票记录金额。
        return self.paymentrecord_set.aggregate(total=models.Sum("amount"))["total"] or Decimal("0")

    @property
    def payment_rate(self) -> Decimal:
        # 用收票金额除以合同金额，得到单个项目的收款率。
        return (self.payment_total / self.amount * Decimal("100")) if self.amount else Decimal("0")

    def move_to_trash(self) -> None:
        # 将合同移入回收站，保留一周内可恢复。
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

    def restore_from_trash(self) -> None:
        # 从回收站恢复合同，恢复后重新出现在合同列表和统计中。
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])


# 保存合同可重复上传的附件文件。
class ContractFile(models.Model):
    contract = models.ForeignKey(Contract, related_name="files", on_delete=models.CASCADE, verbose_name="所属合同")
    file = models.FileField("合同文件", upload_to=project_file_upload_path)
    original_name = models.CharField("原文件名", max_length=255, blank=True)
    sort_order = models.PositiveIntegerField("排序", default=0)
    created_at = models.DateTimeField("上传时间", default=timezone.now)

    class Meta:
        # 文件按用户调整的顺序显示。
        ordering = ["sort_order", "id"]
        verbose_name = "合同文件"
        verbose_name_plural = "合同文件"

    def __str__(self) -> str:
        # 优先显示原始文件名。
        return self.original_name or self.file.name


# 开票和收票记录共用的抽象基础表。
class RecordBase(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, verbose_name="所属合同")
    record_date = models.DateField("日期")
    amount = models.DecimalField("金额", max_digits=14, decimal_places=2, default=0)
    file = models.FileField("附件", upload_to=project_file_upload_path, null=True, blank=True)
    remark = models.CharField("备注", max_length=255, blank=True)
    created_at = models.DateTimeField("创建时间", default=timezone.now)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        # 抽象模型不单独建表，只给子类复用字段。
        abstract = True
        ordering = ["record_date", "id"]

    def __str__(self) -> str:
        # 后台中显示记录所属合同、日期和金额。
        return f"{self.contract.contract_name} - {self.record_date} - {self.amount}"


# 开票记录表。
class InvoiceRecord(RecordBase):
    class Meta(RecordBase.Meta):
        verbose_name = "开票记录"
        verbose_name_plural = "开票记录"


# 收票记录表。
class PaymentRecord(RecordBase):
    class Meta(RecordBase.Meta):
        verbose_name = "收票记录"
        verbose_name_plural = "收票记录"


# 保存系统级开关配置。
class AppSetting(models.Model):
    delete_source_file = models.BooleanField("上传时是否删除原文件", default=False)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "系统设置"
        verbose_name_plural = "系统设置"

    def __str__(self) -> str:
        # 后台中显示固定的系统设置名称。
        return "系统设置"

    @classmethod
    def current(cls) -> "AppSetting":
        # 获取唯一的系统设置记录，首次访问时自动创建。
        setting, _ = cls.objects.get_or_create(pk=1)
        return setting
