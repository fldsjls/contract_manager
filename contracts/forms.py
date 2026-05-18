from django import forms
from django.utils import timezone

from .models import (
    AppSetting,
    Contract,
    InvoiceRecord,
    PaymentRecord,
    normalize_contract_number_part,
    normalize_storage_location_number,
)


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
            "storage_location_number",
            "contract_type",
            "party_name",
            "amount",
            "invoice_status",
            "sign_date",
            "start_date",
            "end_date",
            "responsible_person",
            "archive_years",
            "remark",
        ]
        widgets = {
            "amount": forms.TextInput(attrs={"inputmode": "decimal", "data-step": "1000"}),
            "archive_years": forms.NumberInput(attrs={"min": 1, "step": 1}),
            "sign_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "remark": forms.Textarea(attrs={"rows": 4}),
        }

    # 方法说明：初始化对象字段、默认值或控件样式。
    def __init__(self, *args, skip_display_number_unique: bool = False, **kwargs):
        self.skip_display_number_unique = skip_display_number_unique
        # 初始化日期格式和通用控件样式。
        super().__init__(*args, **kwargs)
        for name in ("sign_date", "start_date", "end_date"):
            self.fields[name].input_formats = ["%Y-%m-%d"]
        self.fields["end_date"].required = True
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
        self.fields["contract_number"].label = "默认编号"
        self.fields["original_contract_folder"].label = "文件夹编号"
        self.fields["original_contract_inner_number"].label = "文件编号"
        self.fields["storage_location_number"].label = "位置编号"
        self.fields["archive_years"].label = "归档时间（年）"
        self.fields["original_contract_folder"].required = False
        self.fields["original_contract_inner_number"].required = True
        self.fields["contract_number"].widget.attrs.update(
            {
                "readonly": "readonly",
                "aria-readonly": "true",
                "title": "默认合同编号自动生成，不能手动修改。",
            }
        )
        self.fields["original_contract_folder"].widget.attrs.pop("placeholder", None)
        self.fields["original_contract_inner_number"].widget.attrs.pop("placeholder", None)
        self.fields["storage_location_number"].widget.attrs.pop("placeholder", None)
        self.fields["original_contract_folder"].widget.attrs.update(
            {
                "maxlength": "3",
                "inputmode": "numeric",
                "pattern": r"\d{0,3}",
            }
        )
        if not self.is_bound:
            storage_value = self.initial.get("storage_location_number") or getattr(self.instance, "storage_location_number", "")
            if normalize_storage_location_number(storage_value) == "000":
                self.initial["storage_location_number"] = ""
        self.fields["original_contract_inner_number"].widget.attrs.update(
            {
                "maxlength": "5",
                "inputmode": "numeric",
                "pattern": r"\d{0,5}",
            }
        )
        self.fields["storage_location_number"].widget.attrs.update(
            {
                "maxlength": "3",
                "inputmode": "numeric",
                "pattern": r"\d{0,3}",
            }
        )

    # 方法说明：执行表单字段或整表校验。
    def clean_contract_number(self):
        # 校验默认业务编号必须是 12 位数字。
        value = self.cleaned_data["contract_number"].strip()
        if len(value) != 12 or not value.isdigit():
            raise forms.ValidationError("业务编号必须是 12 位数字。")
        return value

    # 方法说明：执行表单字段或整表校验。
    def clean_archive_years(self):
        value = self.cleaned_data["archive_years"]
        if value < 1:
            raise forms.ValidationError("归档时间至少为 1 年。")
        return value

    # 方法说明：执行表单字段或整表校验。
    def clean_original_contract_folder(self):
        return normalize_contract_number_part(self.cleaned_data.get("original_contract_folder"), 3)

    # 方法说明：执行表单字段或整表校验。
    def clean_original_contract_inner_number(self):
        return normalize_contract_number_part(self.cleaned_data.get("original_contract_inner_number"), 5)

    # 将合同位置编号统一补齐为三位数字。
    def clean_storage_location_number(self):
        return normalize_storage_location_number(self.cleaned_data.get("storage_location_number"))

    # 方法说明：执行表单字段或整表校验。
    def clean(self):
        # 截止日期用于状态、归档和产值计算，所有合同都必须填写。
        cleaned_data = super().clean()
        contract_type = cleaned_data.get("contract_type")
        end_date = cleaned_data.get("end_date")
        if not end_date:
            self.add_error("end_date", "必须填写截止日期。")

        file_number = normalize_contract_number_part(cleaned_data.get("original_contract_inner_number"), 5)
        if file_number and not self.skip_display_number_unique:
            base_date = cleaned_data.get("sign_date") or cleaned_data.get("start_date") or timezone.localdate()
            display_contract_number = (
                f"{file_number}"
            )
            display_contract_number = f"{Contract.CONTRACT_TYPE_CODES.get(contract_type, '')}{str(base_date.year)[-2:]}{file_number}"
            candidates = Contract.objects.filter(
                original_contract_inner_number__gt="",
                is_deleted=False,
            )
            if self.instance and self.instance.pk:
                candidates = candidates.exclude(pk=self.instance.pk)
            if any(contract.display_contract_number == display_contract_number for contract in candidates):
                self.add_error(
                    "original_contract_inner_number",
                    f"显示合同编号 {display_contract_number} 已存在，不能重复。",
                )
        return cleaned_data


# 生成当前分钟对应的默认合同编号。
# 函数说明：封装可复用的业务处理。
def default_contract_number() -> str:
    return default_contract_numbers()[0]


# 批量生成当前分钟内可用的默认合同编号。
def default_contract_numbers(count: int = 1) -> list[str]:
    if count < 1:
        return []

    prefix = timezone.localtime().strftime("%y%m%d%H%M")
    used_suffixes = {
        int(number[-2:])
        for number in Contract.objects.filter(contract_number__startswith=prefix).values_list(
            "contract_number", flat=True
        )
        if len(number) == 12 and number[-2:].isdigit()
    }
    available_suffixes = [suffix for suffix in range(1, 100) if suffix not in used_suffixes]
    if count > len(available_suffixes):
        raise forms.ValidationError("当前分钟可用合同编号不足，请稍后再试或减少本次导入数量。")
    return [f"{prefix}{suffix:02d}" for suffix in available_suffixes[:count]]


# 合同导入页的 Excel 上传表单。
class ContractImportUploadForm(forms.Form):
    excel_file = forms.FileField(label="Excel 文件")

    # 限制文件选择控件只显示 xlsx 文件。
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["excel_file"].widget.attrs.update(
            {
                "class": "hidden-file-input",
                "accept": ".xlsx",
            }
        )

    # 校验导入文件必须是当前支持的 xlsx 格式。
    def clean_excel_file(self):
        uploaded_file = self.cleaned_data["excel_file"]
        if not uploaded_file.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("请上传 .xlsx 格式的 Excel 文件。")
        return uploaded_file


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
    RECORD_POSITION_GENERATION_FIELDS = [
        "record_position_cabinet_number",
        "record_position_end_cabinet_number",
        "record_position_column_count",
        "record_position_column_capacity",
        "record_position_start_file_number",
        "record_position_start_column",
        "record_position_direction",
        "record_position_reserved_slots",
    ]

    record_position_cabinet_number = forms.CharField(
        label="记录位置起始柜号",
        max_length=2,
        min_length=2,
        widget=forms.TextInput(
            attrs={
                "maxlength": "2",
                "inputmode": "numeric",
                "pattern": r"\d{2}",
            }
        ),
    )
    record_position_end_cabinet_number = forms.CharField(
        label="记录位置终止柜号",
        max_length=2,
        min_length=2,
        widget=forms.TextInput(
            attrs={
                "maxlength": "2",
                "inputmode": "numeric",
                "pattern": r"\d{2}",
            }
        ),
    )
    record_position_start_column = forms.CharField(
        label="记录位置存放栏目",
        max_length=2,
        min_length=2,
        widget=forms.TextInput(
            attrs={
                "maxlength": "2",
                "inputmode": "numeric",
                "pattern": r"\d{2}",
            }
        ),
    )
    record_position_reserved_slots = forms.CharField(
        label="记录位置预留排位",
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "例如：011205;020101",
                "autocomplete": "off",
            }
        ),
    )

    # 根据当前权限控制图片保存目录和记录位置生成参数是否允许编辑。
    def __init__(
        self,
        *args,
        allow_image_root_path_edit: bool = True,
        allow_record_position_generation_edit: bool = True,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        image_field = self.fields["image_root_path"]
        numeric_fields = [
            "record_position_column_count",
            "record_position_column_capacity",
            "record_position_start_file_number",
        ]
        for field_name in numeric_fields:
            self.fields[field_name].widget.attrs.update({"min": "1", "step": "1", "inputmode": "numeric"})
        cabinet_value = self.initial.get("record_position_cabinet_number")
        if cabinet_value is None:
            cabinet_value = getattr(self.instance, "record_position_cabinet_number", 1)
        self.initial["record_position_cabinet_number"] = f"{int(cabinet_value or 1):02d}"
        end_cabinet_value = self.initial.get("record_position_end_cabinet_number")
        if end_cabinet_value is None:
            end_cabinet_value = getattr(self.instance, "record_position_end_cabinet_number", 99)
        self.initial["record_position_end_cabinet_number"] = f"{int(end_cabinet_value or 99):02d}"
        start_column_value = self.initial.get("record_position_start_column")
        if start_column_value is None:
            start_column_value = getattr(self.instance, "record_position_start_column", 1)
        self.initial["record_position_start_column"] = f"{int(start_column_value or 1):02d}"
        if not allow_image_root_path_edit:
            image_field.disabled = True
            image_field.widget.attrs["readonly"] = "readonly"
        if not allow_record_position_generation_edit:
            for field_name in self.RECORD_POSITION_GENERATION_FIELDS:
                field = self.fields[field_name]
                field.disabled = True
                field.widget.attrs["readonly"] = "readonly"
                field.widget.attrs["title"] = "只有超级管理员可修改记录位置编号生成参数。"

    # 柜号在界面中统一显示为两位，保存时仍转为数字字段。
    def clean_record_position_cabinet_number(self):
        value = str(self.cleaned_data["record_position_cabinet_number"] or "").strip()
        if not value.isdigit() or len(value) != 2:
            raise forms.ValidationError("柜号必须填写两位数字。")
        number = int(value)
        if number < 1:
            raise forms.ValidationError("柜号必须大于 00。")
        return number

    # 终止柜号在界面中统一显示为两位，且不能小于起始柜号。
    def clean_record_position_end_cabinet_number(self):
        value = str(self.cleaned_data["record_position_end_cabinet_number"] or "").strip()
        if not value.isdigit() or len(value) != 2:
            raise forms.ValidationError("终止柜号必须填写两位数字。")
        number = int(value)
        if number < 1:
            raise forms.ValidationError("终止柜号必须大于 00。")
        return number

    # 存放栏目在界面中统一显示为两位，保存时仍转为数字字段。
    def clean_record_position_start_column(self):
        value = str(self.cleaned_data["record_position_start_column"] or "").strip()
        if not value.isdigit() or len(value) != 2:
            raise forms.ValidationError("存放栏目必须填写两位数字。")
        number = int(value)
        if number < 1:
            raise forms.ValidationError("存放栏目必须大于 00。")
        column_count = self.cleaned_data.get("record_position_column_count")
        if column_count and number > int(column_count):
            raise forms.ValidationError("存放栏目不能大于栏目量。")
        return number

    # 方法说明：执行表单字段或整表校验。
    def clean(self):
        cleaned_data = super().clean()
        start_cabinet = cleaned_data.get("record_position_cabinet_number")
        end_cabinet = cleaned_data.get("record_position_end_cabinet_number")
        if start_cabinet and end_cabinet and end_cabinet < start_cabinet:
            self.add_error("record_position_end_cabinet_number", "终止柜号不能小于起始柜号。")
        return cleaned_data

    @staticmethod
    def _expand_record_position_reserved_part(part: str) -> list[str]:
        if part.isdigit() and len(part) == 6:
            return [part]
        ranges = [item.strip() for item in part.split(",")]
        if len(ranges) != 3:
            raise forms.ValidationError("预留排位可填写 6 位数字，或按 柜号范围,栏目范围,排位范围 批量填写。")
        expanded_ranges = []
        for value in ranges:
            bounds = [item.strip() for item in value.split("-")]
            if len(bounds) != 2 or any(not item.isdigit() or len(item) != 2 for item in bounds):
                raise forms.ValidationError("批量预留格式必须为 01-03,03-04,01-10 这类两位数字范围。")
            start, end = (int(bounds[0]), int(bounds[1]))
            step = 1 if start <= end else -1
            expanded_ranges.append(range(start, end + step, step))
        values = []
        for cabinet in expanded_ranges[0]:
            for column in expanded_ranges[1]:
                for rank in expanded_ranges[2]:
                    values.append(f"{cabinet:02d}{column:02d}{rank:02d}")
        return values

    # 预留排位支持单个 6 位值或 柜号范围,栏目范围,排位范围 批量值，多个值用英文分号隔开。
    def clean_record_position_reserved_slots(self):
        value = str(self.cleaned_data.get("record_position_reserved_slots") or "").strip()
        self.removed_record_position_reserved_slots = ""
        if not value:
            return ""
        parts = [item.strip() for item in value.split(";") if item.strip()]
        values = []
        removed_values = []
        seen = set()
        removed_seen = set()
        for part in parts:
            should_remove = part.startswith("-")
            normalized_part = part[1:].strip() if should_remove else part
            for expanded_value in self._expand_record_position_reserved_part(normalized_part):
                if should_remove:
                    if expanded_value in removed_seen:
                        continue
                    removed_seen.add(expanded_value)
                    removed_values.append(expanded_value)
                    continue
                if expanded_value in seen:
                    continue
                seen.add(expanded_value)
                values.append(expanded_value)
        self.removed_record_position_reserved_slots = ";".join(removed_values)
        return ";".join(value for value in values if value not in removed_seen)

    # 禁用时忽略提交值，始终保留数据库中的原目录。
    def clean_image_root_path(self):
        if self.fields["image_root_path"].disabled:
            return self.instance.image_root_path
        return self.cleaned_data["image_root_path"]

    # 元数据类：配置字段、排序或显示名称。
    class Meta:
        model = AppSetting
        fields = [
            "allow_partial_import_with_errors",
            "allow_force_contract_import_update",
            "record_position_cabinet_number",
            "record_position_end_cabinet_number",
            "record_position_column_count",
            "record_position_column_capacity",
            "record_position_start_file_number",
            "record_position_start_column",
            "record_position_enable_insert_sort",
            "record_position_force_empty_slot",
            "record_position_reserved_slots",
            "record_position_direction",
            "image_root_path",
        ]
