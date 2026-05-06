from datetime import timedelta
from decimal import Decimal
from functools import wraps
from pathlib import Path

from django.contrib.auth import authenticate, login, logout
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    AppSettingForm,
    ContractForm,
    InvoiceRecordForm,
    LoginForm,
    PaymentRecordForm,
    default_contract_number,
)
from .models import AppSetting, Contract, InvoiceRecord, PaymentRecord


def is_admin_mode(request) -> bool:
    return bool(request.user.is_authenticated and not request.session.get("guest_mode", False))


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if is_admin_mode(request):
            return view_func(request, *args, **kwargs)
        return redirect("contracts:login")

    return wrapper


def context_with_auth(request, context: dict | None = None) -> dict:
    data = context or {}
    data["is_admin_mode"] = is_admin_mode(request)
    data["is_guest_mode"] = bool(request.session.get("guest_mode", False))
    return data


def delete_file_if_enabled(file_field) -> None:
    if not file_field or not AppSetting.current().delete_source_file:
        return
    try:
        path = Path(file_field.path)
    except ValueError:
        return
    if path.exists() and path.is_file():
        path.unlink()


def expiring_contract_queryset():
    today = timezone.localdate()
    expiring_limit = today + timedelta(days=30)
    return Contract.objects.filter(
        end_date__isnull=False,
        end_date__gte=today,
        end_date__lte=expiring_limit,
    ).order_by("end_date")


def chart_points(rows: list[dict], key: str, max_amount: Decimal) -> str:
    if not rows:
        return ""

    left = Decimal("50")
    top = Decimal("40")
    width = Decimal("810")
    height = Decimal("200")
    count = len(rows)
    points = []
    for index, row in enumerate(rows):
        x = left + (width * Decimal(index) / Decimal(max(count - 1, 1)))
        ratio = Decimal(row[key]) / max_amount
        y = top + height - height * ratio
        points.append(f"{float(x):.1f},{float(y):.1f}")
    return " ".join(points)


def dashboard(request):
    total_amount = Contract.objects.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    total_invoice = InvoiceRecord.objects.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    total_payment = PaymentRecord.objects.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    payment_rate = (total_payment / total_amount * Decimal("100")) if total_amount else Decimal("0")

    dates = sorted(
        set(InvoiceRecord.objects.values_list("record_date", flat=True))
        | set(PaymentRecord.objects.values_list("record_date", flat=True))
    )
    invoice_by_date = {
        row["record_date"]: row["total"] or Decimal("0")
        for row in InvoiceRecord.objects.values("record_date").annotate(total=Sum("amount"))
    }
    payment_by_date = {
        row["record_date"]: row["total"] or Decimal("0")
        for row in PaymentRecord.objects.values("record_date").annotate(total=Sum("amount"))
    }
    chart_rows = [
        {
            "date": item,
            "label": item.strftime("%m-%d"),
            "invoice": invoice_by_date.get(item, Decimal("0")),
            "payment": payment_by_date.get(item, Decimal("0")),
        }
        for item in dates
    ]
    max_amount = max(
        [row["invoice"] for row in chart_rows] + [row["payment"] for row in chart_rows],
        default=Decimal("0"),
    ) or Decimal("1")

    recent_contracts = list(Contract.objects.order_by("-created_at")[:8])
    context = context_with_auth(
        request,
        {
            "total_amount": total_amount,
            "total_invoice": total_invoice,
            "total_payment": total_payment,
            "payment_rate": payment_rate,
            "chart_rows": chart_rows,
            "invoice_points": chart_points(chart_rows, "invoice", max_amount),
            "payment_points": chart_points(chart_rows, "payment", max_amount),
            "expiring_contracts": expiring_contract_queryset(),
            "recent_contracts": recent_contracts,
            "recent_blank_rows": range(max(0, 6 - len(recent_contracts))),
            "active_nav": "dashboard",
        },
    )
    return render(request, "contracts/dashboard.html", context)


def contract_list(request):
    keyword = request.GET.get("q", "").strip()
    contracts = Contract.objects.all()
    if keyword:
        contracts = contracts.filter(
            Q(contract_name__icontains=keyword)
            | Q(contract_number__icontains=keyword)
            | Q(party_name__icontains=keyword)
        )

    total_amount = contracts.aggregate(total=Sum("amount"))["total"] or 0
    context = context_with_auth(
        request,
        {
            "contracts": contracts,
            "keyword": keyword,
            "total_amount": total_amount,
            "contract_count": contracts.count(),
            "expiring_contracts": expiring_contract_queryset(),
            "active_nav": "contracts",
        },
    )
    return render(request, "contracts/contract_list.html", context)


def contract_detail(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk)
    context = context_with_auth(
        request,
        {
            "contract": contract,
            "invoice_records": contract.invoicerecord_set.all(),
            "payment_records": contract.paymentrecord_set.all(),
            "active_nav": "contracts",
        },
    )
    return render(request, "contracts/contract_detail.html", context)


@admin_required
def contract_create(request):
    if request.method == "POST":
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect("contracts:contract_list")
    else:
        form = ContractForm(initial={"contract_number": default_contract_number()})
    return render(
        request,
        "contracts/contract_form.html",
        context_with_auth(request, {"form": form, "title": "新增合同", "active_nav": "contracts"}),
    )


@admin_required
def contract_update(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk)
    old_file = contract.file
    if request.method == "POST":
        form = ContractForm(request.POST, request.FILES, instance=contract)
        if form.is_valid():
            updated = form.save()
            if "file" in request.FILES and old_file and old_file != updated.file:
                delete_file_if_enabled(old_file)
            return redirect("contracts:contract_list")
    else:
        form = ContractForm(instance=contract)
    return render(
        request,
        "contracts/contract_form.html",
        context_with_auth(
            request,
            {"form": form, "title": "编辑合同", "contract": contract, "active_nav": "contracts"},
        ),
    )


@admin_required
def contract_delete(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk)
    if request.method == "POST":
        delete_file_if_enabled(contract.file)
        contract.delete()
        return redirect("contracts:contract_list")
    return render(
        request,
        "contracts/contract_confirm_delete.html",
        context_with_auth(request, {"contract": contract, "active_nav": "contracts"}),
    )


@admin_required
def record_add(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk)
    if contract.invoice_status == "不开票":
        return redirect("contracts:payment_record_create", pk=pk)

    context = context_with_auth(
        request,
        {
            "contract": contract,
            "active_nav": "contracts",
        },
    )
    return render(request, "contracts/record_choice.html", context)


@admin_required
def invoice_record_create(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk)
    if contract.invoice_status == "不开票":
        return redirect("contracts:contract_list")

    if request.method == "POST":
        form = InvoiceRecordForm(request.POST, request.FILES)
        if form.is_valid():
            record = form.save(commit=False)
            record.contract = contract
            record.save()
            return redirect("contracts:contract_list")
    else:
        form = InvoiceRecordForm(initial={"record_date": timezone.localdate()})
    return render(
        request,
        "contracts/record_form.html",
        context_with_auth(
            request,
            {"form": form, "contract": contract, "title": "新增开票记录", "active_nav": "contracts"},
        ),
    )


@admin_required
def payment_record_create(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk)
    if request.method == "POST":
        form = PaymentRecordForm(request.POST, request.FILES)
        if form.is_valid():
            record = form.save(commit=False)
            record.contract = contract
            record.save()
            return redirect("contracts:contract_list")
    else:
        form = PaymentRecordForm(initial={"record_date": timezone.localdate()})
    return render(
        request,
        "contracts/record_form.html",
        context_with_auth(
            request,
            {"form": form, "contract": contract, "title": "新增收票记录", "active_nav": "contracts"},
        ),
    )


@admin_required
def settings_view(request):
    setting = AppSetting.current()
    if request.method == "POST":
        form = AppSettingForm(request.POST, instance=setting)
        if form.is_valid():
            form.save()
            return redirect("contracts:settings")
    else:
        form = AppSettingForm(instance=setting)
    return render(
        request,
        "contracts/settings.html",
        context_with_auth(request, {"form": form, "active_nav": "settings"}),
    )


def login_view(request):
    if request.method == "POST":
        if "guest" in request.POST:
            request.session["guest_mode"] = True
            return redirect("contracts:dashboard")

        form = LoginForm(request.POST)
        if form.is_valid():
            user = authenticate(
                request,
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password"],
            )
            if user is not None and user.is_staff:
                login(request, user)
                request.session["guest_mode"] = False
                return redirect("contracts:dashboard")
            form.add_error(None, "账号或密码不正确，或该账号不是管理员。")
    else:
        form = LoginForm()
    return render(request, "contracts/login.html", {"form": form})


def logout_view(request):
    logout(request)
    request.session.flush()
    return redirect("contracts:login")
