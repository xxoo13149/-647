"""
智联招聘 API 直接调用爬虫（无需浏览器，无人机验证）
基于 zl 项目原理整合
"""
import json
import logging
import random
import time
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Any

import requests

from .extract import extract_to_xlsx, parse_items, build_record, clean_header, HEADERS
from .ua_true import IdentityGenerator

logger = logging.getLogger(__name__)

# API 端点
SEARCH_URL = "https://fe-api.zhaopin.com/c/i/search/positions"

# 基础请求头
BASE_HEADERS = {
    "referer": "https://www.zhaopin.com/",
    "content-type": "application/json",
}

# 基础请求体
BASE_JSON_DATA = {
    "S_SOU_FULL_INDEX": "",
    "S_SOU_WORK_CITY": "",  # 空字符串表示全国
    "order": 4,
    "pageSize": 20,
    "pageIndex": 1,
    "eventScenario": "pcSearchedSouSearch",
    "anonymous": 1,
    "clickFilterBlackCompany": False,
    "platform": 13,
    "version": "0.0.0",
}


class SkipKeywordError(Exception):
    """跳过当前关键词"""
    pass


def fetch_page(
    keyword: str,
    page: int,
    city_code: str = "",
    max_retries: int = 3,
    retry_base_wait: float = 1.0,
) -> tuple[dict, int]:
    """
    抓取单页数据
    
    Returns:
        (response_data, item_count)
    """
    json_data = deepcopy(BASE_JSON_DATA)
    json_data["S_SOU_FULL_INDEX"] = keyword
    json_data["pageIndex"] = page
    if city_code:
        json_data["S_SOU_WORK_CITY"] = city_code

    for attempt in range(1, max_retries + 1):
        try:
            # 生成动态请求头
            request_headers = BASE_HEADERS.copy()
            request_headers.update(IdentityGenerator.generate_headers())
            
            response = requests.post(
                SEARCH_URL,
                headers=request_headers,
                json=json_data,
                timeout=20,
            )
            
            # 500 错误：跳过当前关键词
            if response.status_code == 500:
                logger.error("[%s] 第%d页返回500，跳过当前关键词", keyword, page)
                raise SkipKeywordError
            
            # 200 但空数据：跳过
            if response.status_code == 200:
                try:
                    resp_json = response.json()
                    if not resp_json.get('data', {}).get('list'):
                        logger.error("[%s] 第%d页返回200但数据为空，跳过", keyword, page)
                        raise SkipKeywordError
                except (json.JSONDecodeError, KeyError):
                    pass
            
            response.raise_for_status()
            response_data = response.json()
            items = parse_items(response_data)
            
            logger.info("[%s] 第%d页成功，获取%d条", keyword, page, len(items))
            return response_data, len(items)
            
        except SkipKeywordError:
            raise
        except Exception as exc:
            if attempt >= max_retries:
                logger.error("[%s] 第%d页失败%d次，终止: %s", keyword, page, max_retries, exc)
                raise
            
            wait_seconds = retry_base_wait * (2 ** (attempt - 1))
            logger.warning("[%s] 第%d页第%d次失败，%s秒后重试: %s", 
                          keyword, page, attempt, wait_seconds, exc)
            time.sleep(wait_seconds)
    
    return {}, 0


def crawl_keyword(
    keyword: str,
    job_type_level_1: str = "直播/影视/传媒",
    job_type_level_2: str = "",
    total_pages: int = 50,
    page_size: int = 20,
    random_wait_range: tuple = (1, 5),
    output_file: Path = None,
    progress_file: Path = None,
    city_code: str = "",
) -> dict[str, Any]:
    """
    抓取单个关键词的所有页
    
    Returns:
        {"total": 总条数, "file": 输出文件路径}
    """
    if output_file is None:
        output_file = Path("提取结果.xlsx")
    
    # 加载断点
    progress = {"keyword": keyword, "page": 1}
    if progress_file and progress_file.exists():
        try:
            progress = json.loads(progress_file.read_text(encoding="utf-8"))
            if progress.get("keyword") != keyword:
                progress = {"keyword": keyword, "page": 1}
        except json.JSONDecodeError:
            pass
    
    start_page = progress.get("page", 1)
    total_count = 0
    
    logger.info("开始抓取 [%s]，从第%d页开始", keyword, start_page)
    
    for page in range(start_page, total_pages + 1):
        # 随机等待
        wait_seconds = random.uniform(*random_wait_range)
        logger.info("[%s] 第%d页等待%.2f秒", keyword, page, wait_seconds)
        time.sleep(wait_seconds)
        
        try:
            response_data, count = fetch_page(keyword, page, city_code)
        except SkipKeywordError:
            # 保存进度，跳到下一关键词
            if progress_file:
                progress_file.write_text(
                    json.dumps({"keyword": keyword, "page": page + 1}, ensure_ascii=False),
                    encoding="utf-8"
                )
            break
        except Exception:
            logger.error("[%s] 第%d页异常终止", keyword, page)
            raise
        
        # 保存数据
        try:
            result = extract_to_xlsx(
                response_data, 
                output_file, 
                job_type_level_2=job_type_level_2 or keyword,
                job_type_level_1=job_type_level_1,
            )
            total_count += result["count"]
            logger.info("[%s] 第%d页保存成功，追加%d条", keyword, page, result["count"])
        except Exception as exc:
            logger.error("[%s] 第%d页保存失败: %s", keyword, page, exc)
            raise
        
        # 保存进度
        if progress_file:
            progress_file.write_text(
                json.dumps({"keyword": keyword, "page": page + 1}, ensure_ascii=False),
                encoding="utf-8"
            )
        
        # 空数据提前结束
        if count == 0:
            logger.warning("[%s] 第%d页无数据，提前结束", keyword, page)
            break
    
    logger.info("[%s] 抓取完成，共%d条", keyword, total_count)
    return {"total": total_count, "file": str(output_file)}


def crawl_keywords(
    keywords: List[str],
    job_type_level_1: str = "直播/影视/传媒",
    total_pages: int = 50,
    output_dir: Path = None,
    random_wait_range: tuple = (1, 5),
    max_items: int = 1000,
) -> dict[str, Any]:
    """
    批量抓取多个关键词
    
    Args:
        keywords: 关键词列表
        job_type_level_1: 岗位类型一级
        total_pages: 每个关键词抓多少页
        output_dir: 输出目录
        random_wait_range: 随机等待范围
        max_items: 最大总条数
    
    Returns:
        {"total": 总条数, "file": 输出文件}
    """
    if output_dir is None:
        output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 输出文件名
    from .output import sanitize_filename
    base_name = sanitize_filename(job_type_level_1, fallback="未知分类")
    output_file = output_dir / f"{base_name}.xlsx"
    
    total_count = 0
    
    for keyword in keywords:
        if total_count >= max_items:
            logger.info("已达最大条数%d，停止", max_items)
            break
        
        logger.info("=" * 50)
        logger.info("开始关键词: %s", keyword)
        
        try:
            result = crawl_keyword(
                keyword=keyword,
                job_type_level_1=job_type_level_1,
                job_type_level_2=keyword,
                total_pages=total_pages,
                output_file=output_file,
                random_wait_range=random_wait_range,
            )
            total_count += result["total"]
        except SkipKeywordError:
            logger.warning("[%s] 被跳过", keyword)
            continue
        except Exception as exc:
            logger.error("[%s] 异常: %s", keyword, exc)
            raise
    
    logger.info("=" * 50)
    logger.info("全部完成，总计%d条，文件: %s", total_count, output_file)
    
    return {"total": total_count, "file": str(output_file)}
