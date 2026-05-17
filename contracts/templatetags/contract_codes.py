from django import template


register = template.Library()


# 把业务编号或记录编号按前端展示规则加入短横线。
@register.filter
def display_code(value) -> str:
    text = str(value or "").strip()
    if len(text) >= 8 and text[0].isalpha() and text[1:].isdigit():
        parts = [text[:1], text[1:3], text[3:8]]
        if len(text) >= 20:
            parts.append(text[8:12])
            parts.append(text[12:18])
            parts.append(text[18:20])
            if len(text) > 20:
                parts.append(text[20:])
        elif len(text) >= 18:
            parts.append(text[8:12])
            parts.append(text[12:16])
            parts.append(text[16:18])
            if len(text) > 18:
                parts.append(text[18:])
        elif len(text) >= 17:
            parts.append(text[8:12])
            parts.append(text[12:15])
            parts.append(text[15:17])
            if len(text) > 17:
                parts.append(text[17:])
        elif len(text) >= 15:
            parts.append(text[8:10])
            parts.append(text[10:13])
            parts.append(text[13:15])
            if len(text) > 15:
                parts.append(text[15:])
        elif len(text) >= 11:
            parts.append(text[8:11])
            if len(text) >= 13:
                parts.append(text[11:13])
            if len(text) > 13:
                parts.append(text[13:])
        return "-".join(part for part in parts if part)
    return text


# 把存档编号显示为“文件夹编号-位置编号”。
@register.filter
def archive_code(value) -> str:
    text = str(value or "").strip()
    if len(text) == 6 and text.isdigit():
        return f"{text[:3]}-{text[3:]}"
    if len(text) == 5 and text.isdigit():
        return f"{text[:3]}-{text[3:].zfill(3)}"
    return text
