import random
from typing import Dict

class IdentityGenerator:
    """身份生成器 - 用于伪造真实的浏览器环境"""

    # 常见Chrome版本
    CHROME_VERSIONS = [
        "126.0.0.0", "127.0.0.0", "128.0.0.0", "129.0.0.0","130.0.0.0",
        "131.0.0.0", "132.0.0.0", "133.0.0.0", "134.0.0.0","135.0.0.0",
        "136.0.0.0", "137.0.0.0", "138.0.0.0", "139.0.0.0","140.0.0.0",
        "141.0.0.0", "142.0.0.0", "143.0.0.0", "144.0.0.0","145.0.0.0",
    ]

    # 常见操作系统
    OS_LIST = [
        ("Windows NT 10.0; Win64; x64", "Windows"),
        ("Windows NT 11.0; Win64; x64", "Windows"),
        ("Macintosh; Intel Mac OS X 10_15_7", "macOS"),
        ("Macintosh; Intel Mac OS X 11_6_0", "macOS"),
        ("Macintosh; Intel Mac OS X 12_0_0", "macOS"),
        ("Macintosh; Intel Mac OS X 13_0_0", "macOS"),
        ("X11; Linux x86_64", "Linux"),
        ("X11; Ubuntu; Linux x86_64", "Linux"),
    ]

    # 常见语言设置
    LANGUAGES = [
        "zh-CN,zh;q=0.9,en;q=0.8"
    ]

    @classmethod
    def generate_sec_ch_ua(cls, chrome_version: str = None, edg: bool = False) -> str:
        """生成sec-ch-ua头"""
        if not chrome_version:
            chrome_version = random.choice(cls.CHROME_VERSIONS)
        major_version = chrome_version.split('.')[0]

        # 随机选择Not A Brand格式
        not_a_brand_formats = [
            '"Not/A)Brand"',
            '"Not A(Brand"',
            '"Not=A?Brand"',
            '"Not.A/Brand"',
        ]
        not_a_brand = random.choice(not_a_brand_formats)
        if edg:
            return f'"Microsoft Edge";v="{major_version}", "Chromium";v="{major_version}", {not_a_brand};v="99"'

        return f'"Google Chrome";v="{major_version}", "Chromium";v="{major_version}", {not_a_brand};v="99"'

    @classmethod
    def generate_headers(cls) -> Dict[str, str]:
        """
        生成完整的伪造请求头

        Returns:
            Dict: 部分请求头
        """
        chrome_version = random.choice(cls.CHROME_VERSIONS)
        os_string, os_platform = random.choice(cls.OS_LIST)

        flag = random.uniform(0.0, 1.0)
        edg = ''
        if flag > 0.5:
            edg = f' Edg/{chrome_version}'
        # 基础UA模板
        ua = f"Mozilla/5.0 ({os_string}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36" + edg

        headers = {
            "user-agent": ua,
        }
        return headers

if __name__ == "__main__":

    for i in range(5):
        headers = IdentityGenerator.generate_headers()
        print(headers)
