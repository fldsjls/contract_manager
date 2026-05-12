from django import forms
from django.utils import timezone

from .models import AppSetting, Contract, InvoiceRecord, PaymentRecord


# 合同新增和编辑使用的表单。
# 表单类：配置表单字段、校验和控件表现。
class ContractForm(forms.ModelForm):
    # 表单字段、控件和模型绑定配置。
    # 元数据类：配置字段、排序或显示名称。
    class Meta:
        model = Contract
        fields = [
            "contract_name",
            "contract_number",
            "original_contract_folder",
            "original_contract_inner_number",
            "contract_type",
            "party_name",
            "amount",
            "invoice_status",
            "sign_date",
            "start_date",
            "end_date",
            "responsible_person",
            "remark",
        ]
        widgets = {
            "amount": forms.TextInput(attrs={"inputmode": "decimal", "data-step": "1000"}),
            "sign_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "remark": forms.Textarea(attrs={"rows": 4}),
        }

    # 方法说明：初始化对象字段、默认值或控件样式。
    def __init__(self, *args, **kwargs):
        # 初始化日期格式和通用控件样式。
        super().__init__(*args, **kwargs)
        for name in ("sign_date", "start_date", "end_date"):
            self.fields[name].input_formats = ["%Y-%m-%d"]
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
        self.fields["original_contract_folder"].widget.attrs.setdefault("placeholder", "原合同文件夹")
        self.fields["original_contract_inner_number"].widget.attrs.setdefault("placeholder", "文件夹内编号")

    # 方法说明：执行表单字段或整表校验。
    def clean_contract_number(self):
        # 校验合同编号必须是 12 位数字。
        value = self.cleaned_data["contract_number"].strip()
        if len(value) != 12 or not value.isdigit():
            raise forms.ValidationError("合同编号必须是 12 位数字。")
        return value

    # 方法说明：执行表单字段或整表校验。
    def clean(self):
        # 校验维保合同必须填写截止日期。
        cleaned_data = super().clean()
        contract_type = cleaned_data.get("contract_type")
        end_date = cleaned_data.get("end_date")
        if contract_type == "维保" and not end_date:
            self.add_error("end_date", "维保合同必须填写截止日期。")
        return cleaned_data


# 生成当前分钟对应的默认合同编号。
# 函数说明：封装可复用的业务处理。
def default_contract_number() -> str:
    return timezone.localtime().strftime("%Y%m%d%H%M")


# 开票和收票表单共用的基础表单。
# 表单类：配置表单字段、校验和控件表现。
class RecordFormBase(forms.ModelForm):
    # 记录表单的通用字段和日期控件。
    # 元数据类：配置字段、排序或显示名称。
    class Meta:
        fields = ["record_date", "amount", "file", "remark"]
        widgets = {
            "record_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        }

    # 方法说明：初始化对象字段、默认值或控件样式。
    def __init__(self, *args, **kwargs):
        # 初始化记录日期格式和控件样式。
        super().__init__(*args, **kwargs)
        self.fields["record_date"].input_formats = ["%Y-%m-%d"]
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


# 开票记录表单。
# 表单类：配置表单字段、校验和控件表现。
class InvoiceRecordForm(RecordFormBase):
    # 元数据类：配置字段、排序或显示名称。
    class Meta(RecordFormBase.Meta):
        model = InvoiceRecord


# 收票记录表单。
# 表单类：配置表单字段、校验和控件表现。
class PaymentRecordForm(RecordFormBase):
    # 元数据类：配置字段、排序或显示名称。
    class Meta(RecordFormBase.Meta):
        model = PaymentRecord


# 管理员登录表单。
# 表单类：配置表单字段、校验和控件表现。
class LoginForm(forms.Form):
    username = forms.CharField(label="账号", max_length=150)
    password = forms.CharField(label="密码", widget=forms.PasswordInput)


# 系统设置表单。
# 表单类：配置表单字段、校验和控件表现。
class AppSettingForm(forms.ModelForm):
    # 元数据类：配置字段、排序或显示名称。
    class Meta:
        model = AppSetting
        fields = ["delete_source_file", "image_root_path"]
