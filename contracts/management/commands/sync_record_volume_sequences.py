from django.core.management.base import BaseCommand
from django.db import transaction

from contracts.models import AppSetting, Contract
from contracts.views import reserve_default_record_volume_sequence


# 管理命令：为已有合同补齐默认项目记录分册实序。
class Command(BaseCommand):
    help = "Backfill default record volume sequence reservations for active contracts."

    # 注册 dry-run 参数，便于先查看需要补齐的合同数量。
    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count contracts that need a default record volume sequence without creating rows.",
        )

    # 执行补齐逻辑，非 dry-run 时在事务中批量创建缺失的分册实序。
    def handle(self, *args, **options):
        setting = AppSetting.current()
        dry_run = bool(options["dry_run"])
        contracts = (
            Contract.objects.filter(is_deleted=False)
            .exclude(original_contract_inner_number="")
            .order_by("original_contract_inner_number", "id")
        )
        missing_contracts = [
            contract
            for contract in contracts
            if not contract.record_volume_sequences.filter(storage_location_number="01").exists()
        ]
        if dry_run:
            self.stdout.write(str(len(missing_contracts)))
            return
        created_count = 0
        with transaction.atomic():
            for contract in missing_contracts:
                sequence = reserve_default_record_volume_sequence(contract, setting)
                if sequence:
                    created_count += 1
        self.stdout.write(self.style.SUCCESS(f"Synced default record volume sequences: {created_count}"))
