from django.db import migrations


def shelf_position_number_from_sequence(sequence_number, setting):
    sequence_number = int(sequence_number or 0)
    if not sequence_number:
        return "000000"
    capacity = max(int(getattr(setting, "record_position_column_capacity", 1) or 1), 1)
    column_count = max(int(getattr(setting, "record_position_column_count", 1) or 1), 1)
    start_column = min(max(int(getattr(setting, "record_position_start_column", 1) or 1), 1), column_count)
    cabinet = max(int(getattr(setting, "record_position_cabinet_number", 1) or 1), 1)
    start_file = max(int(getattr(setting, "record_position_start_file_number", 1) or 1), 1)
    direction = getattr(setting, "record_position_direction", "decrement")
    offset = sequence_number - start_file + 1
    if offset > 0:
        column_steps = (offset - 1) // capacity
        rank = ((offset - 1) % capacity) + 1
        forward_direction = direction
        cabinet_step = 1
    else:
        column_steps = ((-offset - 1) // capacity) + 1
        rank = capacity - ((-offset - 1) % capacity)
        forward_direction = "decrement" if direction == "increment" else "increment"
        cabinet_step = -1
    if forward_direction == "increment":
        column_index = start_column - 1 + column_steps
        cabinet += cabinet_step * (column_index // column_count)
        column = (column_index % column_count) + 1
    else:
        column_index = start_column - 1 - column_steps
        wraps = ((-column_index - 1) // column_count + 1) if column_index < 0 else 0
        cabinet += cabinet_step * wraps
        column = (column_index % column_count) + 1
    return f"{max(cabinet, 1):02d}{column:02d}{rank:02d}"


def refresh_shelf_positions(apps, schema_editor):
    AppSetting = apps.get_model("contracts", "AppSetting")
    MaintenanceRecordVolumeSequence = apps.get_model("contracts", "MaintenanceRecordVolumeSequence")
    setting = AppSetting.objects.filter(pk=1).first() or AppSetting()
    for sequence in MaintenanceRecordVolumeSequence.objects.order_by("id"):
        sequence.shelf_position_number = shelf_position_number_from_sequence(sequence.real_sequence_number, setting)
        sequence.save(update_fields=["shelf_position_number"])


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0044_appsetting_record_position_reserved_slots_and_more"),
    ]

    operations = [
        migrations.RunPython(refresh_shelf_positions, migrations.RunPython.noop),
    ]
