import asyncio
from datetime import datetime
from pathlib import Path

from .config import apply_cli_overrides, load_env_config, parse_cli_args, print_config_summary
from .category_presets import expand_zhaopin_keyword_groups
from .constants import ENV_FILE_NAME, OUTPUT_COLUMNS
from .crawled_links import build_crawled_link_store
from .fiftyone import crawl_51job, login_51job_profile
from .output import append_jobs_checkpoint, save_jobs_by_keyword
from .utils import human_sleep
from .zhaopin import crawl_zhaopin, login_zhaopin_profile


async def main() -> None:
    script_dir = Path(__file__).resolve().parents[1]
    env_path = script_dir / ENV_FILE_NAME
    args = parse_cli_args()

    try:
        settings = load_env_config(env_path)
        settings = apply_cli_overrides(settings, args, script_dir)
    except Exception as exc:
        print(f"配置加载失败：{exc}")
        print(f"请检查 {env_path} 后重试。")
        return

    if settings["platform"] == "51job" and settings["login_51job"]:
        await login_51job_profile(settings)
        return
    if settings["platform"] == "zhaopin" and settings["login_zhaopin"]:
        await login_zhaopin_profile(settings)
        return

    print(f"提示：程序将自动打开浏览器抓取 {settings['platform']} 平台数据。")
    print_config_summary(settings, env_path)
    crawled_link_store = build_crawled_link_store(settings)
    checkpoint_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    checkpoint_files: set[str] = set()

    def record_cli_checkpoint(keyword: str, jobs: list[dict], context: dict | None = None):
        export_keyword = str((context or {}).get("export_keyword") or keyword)
        checkpoint_path = append_jobs_checkpoint(
            jobs=jobs,
            output_dir=settings["output_dir"],
            keyword=export_keyword,
            session_id=checkpoint_session_id,
            context=context,
        )
        if checkpoint_path is not None:
            checkpoint_files.add(str(checkpoint_path))

    settings["page_result_callback"] = record_cli_checkpoint

    keyword_groups = expand_zhaopin_keyword_groups(settings["keywords"]) if settings["platform"] == "zhaopin" else [
        {"label": keyword, "searches": [{"search_keyword": keyword, "primary_category": keyword, "secondary_category": ""}]}
        for keyword in settings["keywords"]
    ]
    crawl_regions = settings["regions"] or [""]
    total_tasks = sum(len(group["searches"]) for group in keyword_groups) * len(crawl_regions)
    current_task = 0
    saved_file_count = 0
    raw_total = 0
    appended_total = 0
    updated_total = 0
    saved_files: list[str] = []

    for group in keyword_groups:
        keyword = str(group["label"])
        keyword_jobs = []
        for search in group["searches"]:
            search_keyword = str(search["search_keyword"])
            primary_category = str(search.get("primary_category") or keyword)
            secondary_category = str(search.get("secondary_category") or "")
            for city in crawl_regions:
                current_task += 1
                region_label = city or "不限地区"
                print(
                    f"\n任务进度：{current_task}/{total_tasks}（关键词={search_keyword}，地区={region_label}）"
                )
                if settings["platform"] == "51job":
                    region_jobs = await crawl_51job(
                        keyword=search_keyword,
                        city=city,
                        settings=settings,
                        crawled_link_store=crawled_link_store,
                    )
                else:
                    region_jobs = await crawl_zhaopin(
                        keyword=search_keyword,
                        city=city,
                        settings=settings,
                        crawled_link_store=crawled_link_store,
                    )
                    for item in region_jobs:
                        if primary_category:
                            item["岗位类型一级"] = primary_category
                        if secondary_category:
                            item["岗位类型二级"] = secondary_category
                keyword_jobs.extend(region_jobs)

                if current_task < total_tasks:
                    await human_sleep(*settings["delays"]["between_tasks"])

        if not keyword_jobs:
            print(f"\n关键词《{keyword}》未抓取到数据，已跳过写入。")
            continue

        keyword_summary = save_jobs_by_keyword(
            keyword_jobs,
            output_dir=settings["output_dir"],
            keyword=keyword,
        )
        saved_file_count += keyword_summary["file_count"]
        raw_total += keyword_summary["raw_count"]
        appended_total += keyword_summary["appended_count"]
        updated_total += keyword_summary["updated_count"]
        saved_files.extend(keyword_summary["saved_files"])

    if not saved_file_count:
        print("未抓取到数据，请稍后重试或检查配置。")
        return

    print(
        f"\n爬取完成！原始抓取 {raw_total} 条，"
        f"共写入 {saved_file_count} 个关键词文件。"
    )
    print(
        f"本次增量结果：新增 {appended_total} 条，"
        f"更新 {updated_total} 条。"
    )
    print(f"输出目录：{settings['output_dir']}")
    print("表格列：序号 | " + " | ".join(OUTPUT_COLUMNS))
    if checkpoint_files:
        print("本轮断点备份：")
        for path in sorted(checkpoint_files):
            print(f"- {path}")
    if saved_files:
        print("文件列表：")
        for path in saved_files:
            print(f"- {path}")


if __name__ == "__main__":
    asyncio.run(main())
