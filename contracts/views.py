from django.db.models import Q, Sum
from django.shortcuts import render

from .models import Contract


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
    context = {
        "contracts": contracts,
        "keyword": keyword,
        "total_amount": total_amount,
        "contract_count": contracts.count(),
    }
    return render(request, "contracts/contract_list.html", context)
