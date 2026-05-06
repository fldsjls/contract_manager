from django import forms
from django.utils import timezone

from .models import AppSetting, Contract, InvoiceRecord, PaymentRecord


class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = [
            "contract_name",
            "contract_number",
            "contract_type",
            "party_name",
            "amount",
            "invoice_status",
            "sign_date",
            "start_date",
            "end_date",
            "file",
            "remark",
        ]
        widgets = {
            "sign_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "remark": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("sign_date", "start_date", "end_date"):
            self.fields[name].input_formats = ["%Y-%m-%d"]
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean_contract_number(self):
        value = self.cleaned_data["contract_number"].strip()
        if len(value) != 12 or not value.isdigit():
            raise forms.ValidationError("合同编号必须是 12 位数字。")
        return value

    def clean(self):
        cleaned_data = super().clean()
        contract_type = cleaned_data.get("contract_type")
        end_date = cleaned_data.get("end_date")
        if contract_type == "维保" and not end_date:
            self.add_error("end_date", "维保合同必须填写截止日期。")
        return cleaned_data


def default_contract_number() -> str:
    return timezone.localtime().strftime("%Y%m%d%H%M")


class RecordFormBase(forms.ModelForm):
    class Meta:
        fields = ["record_date", "amount", "file", "remark"]
        widgets = {
            "record_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["record_date"].input_formats = ["%Y-%m-%d"]
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class InvoiceRecordForm(RecordFormBase):
    class Meta(RecordFormBase.Meta):
        model = InvoiceRecord


class PaymentRecordForm(RecordFormBase):
    class Meta(RecordFormBase.Meta):
        model = PaymentRecord


class LoginForm(forms.Form):
    username = forms.CharField(label="账号", max_length=150)
    password = forms.CharField(label="密码", widget=forms.PasswordInput)


class AppSettingForm(forms.ModelForm):
    class Meta:
        model = AppSetting
        fields = ["delete_source_file"]
