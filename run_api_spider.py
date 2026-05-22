#!/usr/bin/env python3
"""
API 直接调用爬虫入口（无需浏览器，无人机验证）
基于 zl 项目原理
"""
import sys
import os

# 确保能找到 job_crawler 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
from pathlib import Path
from job_crawler.api_spider import crawl_keywords

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 直播/影视/传媒 关键词列表
KEYWORDS = [
    "视频剪辑师", "音频编辑", "摄影/摄像", "编导", "导演", "编剧", "制片人",
    "美术指导", "录音/音效", "影视制作", "影视策划", "影视发行", "编辑",
    "美编", "文案编辑", "主编/副主编", "总编/副总编", "采编", "记者",
    "撰稿人", "校对录入", "排版设计", "印刷排版", "出版发行", "发行管理",
    "主播", "带货主播", "中控/场控/助播", "演员/模特", "服装/试衣模特",
    "水族馆表演演员", "经纪人", "星探", "模特经纪人", "演出经纪人",
    "直播运营", "短视频运营", "新媒体运营", "内容运营", "用户运营",
    "社群运营", "活动运营", "电商运营", "直播选品", "直播商务",
    "媒介投放", "广告投放", "品牌推广", "公关经理", "舆情监控",
    "艺人统筹", "节目统筹", "制片助理", "导演助理", "摄影助理",
    "灯光师", "道具师", "服装师", "化妆师", "造型师", "场务",
    "后期制作", "特效制作", "动画制作", "调色师", "剪辑助理",
    "配音演员", "配音导演", "录音师", "混音师", "音效设计师",
    "舞美设计", "舞台监督", "剧场管理", "影院管理", "放映员",
    "策展人", "展览设计", "艺术管理", "文化项目管理", "非遗传承",
    "直播场控", "直播运营助理", "短视频编导", "短视频拍摄",
    "直播投流", "直播数据分析", "直播客服", "直播供应链",
    "娱乐主播", "游戏主播", "才艺主播", "知识主播", "户外主播",
    "直播公会运营", "直播培训师", "直播内容策划", "直播活动运营",
]

def main():
    """主入口"""
    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 60)
    print("智联招聘 API 直接调用爬虫")
    print("无需浏览器，无人机验证")
    print("=" * 60)
    print(f"关键词数量: {len(KEYWORDS)}")
    print(f"输出目录: {output_dir.absolute()}")
    print(f"目标条数: 1000")
    print("=" * 60)
    
    result = crawl_keywords(
        keywords=KEYWORDS,
        job_type_level_1="直播/影视/传媒",
        total_pages=50,  # 每个关键词抓 50 页
        output_dir=output_dir,
        random_wait_range=(1, 3),  # 1-3 秒随机等待
        max_items=1000,
    )
    
    print("=" * 60)
    print(f"✅ 完成！共抓取 {result['total']} 条")
    print(f"📁 文件: {result['file']}")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
