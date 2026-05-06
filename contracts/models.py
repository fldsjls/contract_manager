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
