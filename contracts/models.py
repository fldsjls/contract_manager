from datetime import timedelta

from django.db import models
from django.utils import timezone


class Contract(models.Model):
    CONTRACT_TYPES = [
        ("维保", "维保"),
        ("评估", "评估"),
        ("检测", "检测"),
        ("改造", "改造"),
        ("新建", "新建"),
        ("其他项目", "其他项目"),
    ]
    INVOICE_STATUS = [
        ("不开票", "不开票"),
        ("开票", "开票"),
    ]

    contract_name = models.CharField("合同名称", max_length=200)
    contract_number = models.CharField("合同编号", max_length=50, unique=True)
    contract_type = models.CharField("合同类型", max_length=20, choices=CONTRACT_TYPES, default="其他项目")
    party_name = models.CharField("甲方名称", max_length=200)
    amount = models.DecimalField("金额", max_digits=14, decimal_places=2, default=0)
    invoice_status = models.CharField("是否开票", max_length=20, choices=INVOICE_STATUS, default="不开票")
    sign_date = models.DateField("签订日期", null=True, blank=True)
    start_date = models.DateField("开始日期", null=True, blank=True)
    end_date = models.DateField("截止日期", null=True, blank=True)
    file = models.FileField("合同文件", upload_to="contracts/", null=True, blank=True)
    remark = models.TextField("备注", blank=True)
    created_at = models.DateTimeField("创建时间", default=timezone.now)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["end_date", "-id"]
        verbose_name = "合同"
        verbose_name_plural = "合同"

    def __str__(self) -> str:
        return f"{self.contract_name}（{self.contract_number}）"

    @property
    def status(self) -> str:
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
        return {
            "已到期": "expired",
            "即将到期": "expiring",
            "进行中": "active",
        }.get(self.status, "active")


class RecordBase(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, verbose_name="所属合同")
    record_date = models.DateField("日期")
    amount = models.DecimalField("金额", max_digits=14, decimal_places=2, default=0)
    file = models.FileField("附件", upload_to="records/", null=True, blank=True)
    remark = models.CharField("备注", max_length=255, blank=True)
    created_at = models.DateTimeField("创建时间", default=timezone.now)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        abstract = True
        ordering = ["record_date", "id"]

    def __str__(self) -> str:
        return f"{self.contract.contract_name} - {self.record_date} - {self.amount}"


class InvoiceRecord(RecordBase):
    class Meta(RecordBase.Meta):
        verbose_name = "开票记录"
        verbose_name_plural = "开票记录"


class PaymentRecord(RecordBase):
    class Meta(RecordBase.Meta):
        verbose_name = "收票记录"
        verbose_name_plural = "收票记录"


class AppSetting(models.Model):
    delete_source_file = models.BooleanField("删除被替换或删除的已上传文件", default=False)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "系统设置"
        verbose_name_plural = "系统设置"

    def __str__(self) -> str:
        return "系统设置"

    @classmethod
    def current(cls) -> "AppSetting":
        setting, _ = cls.objects.get_or_create(pk=1)
        return setting
