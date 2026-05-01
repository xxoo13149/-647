from pathlib import Path
from typing import Any

import pandas as pd

from .constants import EMPTY_CELL_VALUE, OUTPUT_COLUMNS
from .utils import (
    choose_latest_publish_time,
    clean_multiline_text,
    clean_text,
    extract_labeled_value,
    fill_empty,
    infer_province,
    merge_distinct_text,
    normalize_city_name,
    normalize_publish_time_text,
    sanitize_filename,
    split_job_summary,
)


def normalize_job_record(item: dict[str, Any]) -> dict[str, str]:
    """标准化岗位记录字段。"""
    legacy_ability = clean_multiline_text(str(item.get("能力描述", "")))
    legacy_salary = ""
    legacy_location = ""
    legacy_experience = ""
    legacy_education = ""
    legacy_summary = ""
    legacy_tags = ""
    if legacy_ability:
        legacy_salary = extract_labeled_value(legacy_ability, "薪资")
        legacy_location = extract_labeled_value(legacy_ability, "地点")
        legacy_experience = extract_labeled_value(legacy_ability, "经验")
        legacy_education = extract_labeled_value(legacy_ability, "学历")
        legacy_tags = extract_labeled_value(legacy_ability, "技能标签")
        legacy_summary = extract_labeled_value(legacy_ability, "岗位摘要")

    legacy_work, legacy_requirement = split_job_summary(legacy_summary)

    record = {column: fill_empty(item.get(column, "")) for column in OUTPUT_COLUMNS}

    if record["招聘平台"] == EMPTY_CELL_VALUE:
        record["招聘平台"] = "智联招聘"
    if record["公司名称"] == EMPTY_CELL_VALUE:
        record["公司名称"] = fill_empty(item.get("招聘单位名称", ""))
    if record["岗位名称"] == EMPTY_CELL_VALUE:
        record["岗位名称"] = fill_empty(item.get("岗位名称", ""))
    if record["投递起始时间"] == EMPTY_CELL_VALUE:
        record["投递起始时间"] = fill_empty(normalize_publish_time_text(item.get("最新发布时间", "")))
    else:
        record["投递起始时间"] = fill_empty(normalize_publish_time_text(record["投递起始时间"]))

    fallback_map = {
        "薪资范围": legacy_salary,
        "详细地址": legacy_location,
        "经验要求": legacy_experience,
        "学历要求": legacy_education,
        "工作内容": legacy_work,
        "任职要求": legacy_requirement,
    }
    for column, fallback in fallback_map.items():
        if record[column] == EMPTY_CELL_VALUE and fallback:
            record[column] = fill_empty(fallback)

    if record["城市"] == EMPTY_CELL_VALUE:
        record["城市"] = fill_empty(normalize_city_name(record["详细地址"]))
    if record["所在省份"] == EMPTY_CELL_VALUE and record["城市"] != EMPTY_CELL_VALUE:
        record["所在省份"] = fill_empty(infer_province(record["城市"]))

    if legacy_tags:
        remark = "" if record["备注"] == EMPTY_CELL_VALUE else record["备注"]
        tag_remark = "技能标签：" + legacy_tags
        if tag_remark not in remark:
            record["备注"] = fill_empty("；".join([x for x in [remark, tag_remark] if x]))

    if record["公司名称"] == EMPTY_CELL_VALUE:
        record["公司名称"] = "未知单位"
    if record["岗位名称"] == EMPTY_CELL_VALUE:
        record["岗位名称"] = "未知岗位"

    return record


def load_existing_job_records(file_path: Path) -> list[dict[str, str]]:
    """读取已存在的岗位 Excel 记录。"""
    if not file_path.exists():
        return []

    try:
        df = pd.read_excel(file_path, dtype=str, engine="openpyxl").fillna("")
    except Exception as exc:
        print(f"警告：读取现有文件失败，将以新数据重建：{file_path}，原因：{exc}")
        return []

    if df.empty:
        return []

    if "序号" in df.columns:
        df = df.drop(columns=["序号"])

    records = []
    for _, row in df.iterrows():
        records.append(normalize_job_record(row.to_dict()))
    return records


def merge_job_records(
    existing_records: list[dict[str, str]],
    new_records: list[dict[str, str]],
) -> tuple[list[dict[str, str]], int, int]:
    """按岗位链接优先去重，缺失链接时回退到公司 + 岗位 + 城市。"""
    merged_map: dict[tuple[str, ...], dict[str, str]] = {}
    ordered_keys: list[tuple[str, ...]] = []

    for record in existing_records:
        normalized = normalize_job_record(record)
        key = build_record_key(normalized)
        if key in merged_map:
            continue
        merged_map[key] = normalized
        ordered_keys.append(key)

    appended_count = 0
    updated_count = 0

    for record in new_records:
        normalized = normalize_job_record(record)
        key = build_record_key(normalized)

        if key not in merged_map:
            merged_map[key] = normalized
            ordered_keys.append(key)
            appended_count += 1
            continue

        current = merged_map[key]
        changed = False

        merged_publish_time = choose_latest_publish_time(
            current["投递起始时间"],
            normalized["投递起始时间"],
        )
        if merged_publish_time and merged_publish_time != current["投递起始时间"]:
            current["投递起始时间"] = fill_empty(merged_publish_time)
            changed = True

        for column in OUTPUT_COLUMNS:
            if column == "投递起始时间":
                continue
            new_value = normalized[column]
            current_value = current[column]
            if new_value == EMPTY_CELL_VALUE:
                continue
            if current_value == EMPTY_CELL_VALUE or new_value != current_value:
                current[column] = new_value
                changed = True

        if changed:
            updated_count += 1

    merged_records = [merged_map[key] for key in ordered_keys]
    return merged_records, appended_count, updated_count


def build_record_key(record: dict[str, str]) -> tuple[str, ...]:
    """构造稳定去重键。"""
    link = clean_text(record.get("岗位链接", ""))
    if link and link != EMPTY_CELL_VALUE:
        return ("link", link)
    return (
        "fallback",
        clean_text(record.get("公司名称", "")),
        clean_text(record.get("岗位名称", "")),
        clean_text(record.get("城市", "")),
    )


def write_job_records_to_excel(file_path: Path, records: list[dict[str, str]]) -> None:
    """将岗位记录写入 Excel，序号每次按当前文件重排。"""
    df = pd.DataFrame(records)

    for column in OUTPUT_COLUMNS:
        if column not in df.columns:
            df[column] = ""

    df = df[OUTPUT_COLUMNS]
    df.insert(0, "序号", range(1, len(df) + 1))
    df.to_excel(file_path, index=False, engine="openpyxl")
    format_output_workbook(file_path)


def format_output_workbook(file_path: Path) -> None:
    """按岗位信息表模板的字段宽度做基础可读性格式化。"""
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return

    wb = load_workbook(file_path)
    ws = wb.active
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    header_fill = PatternFill("solid", fgColor="D9EAF7")
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    width_by_header = {
        "序号": 8,
        "招聘平台": 12,
        "岗位类别/大类": 16,
        "岗位名称": 28,
        "公司名称": 26,
        "公司规模": 14,
        "所在省份": 14,
        "城市": 12,
        "详细地址": 32,
        "学历要求": 12,
        "经验要求": 12,
        "薪资范围": 16,
        "福利标签": 24,
        "工作内容": 48,
        "任职要求": 48,
        "岗位链接": 44,
        "投递起始时间": 18,
        "投递截止时间": 18,
        "备注": 30,
    }

    for index, cell in enumerate(ws[1], start=1):
        letter = get_column_letter(index)
        ws.column_dimensions[letter].width = width_by_header.get(str(cell.value), 16)

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    wb.save(file_path)


def save_jobs_by_keyword(jobs: list[dict], output_dir: Path, keyword: str) -> dict[str, Any]:
    """按用户输入关键词分文件保存，并执行增量去重更新。"""
    if not jobs:
        return {
            "file_count": 0,
            "raw_count": 0,
            "appended_count": 0,
            "updated_count": 0,
            "saved_files": [],
        }

    base_name = sanitize_filename(keyword, fallback="未知关键词")
    file_name = f"{base_name}.xlsx"
    file_path = output_dir / file_name

    normalized_jobs = [normalize_job_record(item) for item in jobs]
    existing_records = load_existing_job_records(file_path)
    merged_records, appended_count, updated_count = merge_job_records(
        existing_records=existing_records,
        new_records=normalized_jobs,
    )

    write_job_records_to_excel(file_path, merged_records)
    print(
        f"关键词《{keyword}》写入完成：新增 {appended_count} 条，"
        f"更新 {updated_count} 条，当前共 {len(merged_records)} 条 -> {file_path}"
    )

    return {
        "file_count": 1,
        "raw_count": len(jobs),
        "appended_count": appended_count,
        "updated_count": updated_count,
        "saved_files": [str(file_path)],
    }
