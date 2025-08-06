import requests
import json
import os
import shutil
from datetime import datetime, date, timedelta
from email import message_from_file
from bs4 import BeautifulSoup

# ==============================================================================
# 1. 配置区
# ==============================================================================
API_BASE_URL = "innerweb.com"

LOGIN_TOKEN = "PASTE_YOUR_LATEST_TOKEN_HERE" 
COOKIE = "PASTE_YOUR_LATEST_COOKIE_HERE"

DOWNLOAD_DIR = "knowledge_platform_downloads"
TIME_WINDOW_DAYS = 7
MAX_ARTICLES_PER_SECTION = 50

ARXIV_CONFIG = {"sections": {"AI": 1}}
ACCOUNTS_CONFIG = {
    "accounts": {"新智元": 20, "机器之心": 39},
    "list_payload_template": {
      "labelId": None, "accountId": "WILL_BE_REPLACED_BY_SCRIPT", "pageIndex": 1,
      "pageSize": 20, "orderMode": None, "orderType": 0, "startDate": None,
      "endDate": None, "searchKey": None
    }
}

# ==============================================================================
# 2. 核心代码区
# ==============================================================================
def convert_mhtml_to_txt(mhtml_path):
    """[v11 新增] 读取 mhtml 文件，提取正文转为 txt，并删除原文件"""
    print(f"    - 准备转换文件: {os.path.basename(mhtml_path)}...")
    try:
        with open(mhtml_path, 'r', encoding='utf-8', errors='ignore') as f:
            msg = message_from_file(f)
        
        html_content = ""
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                html_content = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'ignore')
                break
        
        if not html_content:
            print(f"    [!] 未在 {os.path.basename(mhtml_path)} 中找到 HTML 内容。"); return

        soup = BeautifulSoup(html_content, 'lxml')
        text = soup.get_text(separator='\n', strip=True)
        
        txt_path = os.path.splitext(mhtml_path)[0] + '.txt'
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"    ✔ 转换成功: {os.path.basename(txt_path)}")
        
        os.remove(mhtml_path)
        print(f"    - 已删除原始文件: {os.path.basename(mhtml_path)}")

    except Exception as e:
        print(f"    [!] 转换 MHTML 文件时出错: {e}")

def download_from_url(session, url, full_path):
    """使用 GET 请求从直接 URL 下载文件 (用于 arXiv PDF)"""
    try:
        print(f"    - 准备从直接链接下载: {os.path.basename(full_path)}...")
        response = session.get(url, stream=True, verify=False, timeout=20)
        response.raise_for_status()
        with open(full_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192): f.write(chunk)
        print(f"    ✔ 下载成功: {full_path}")
    except requests.exceptions.RequestException as e:
        print(f"    [!] 从 URL 下载时发生网络错误: {e}")

def download_from_api(session, article, subfolder):
    """使用 POST 请求通过 API 下载文件 (用于公众号 MHTML)"""
    article_id = article.get("articleId")
    article_title = article.get("articleTitle", f"Article_ID_{article_id}")
    download_payload = {"articleId": article_id, "downloadType": 1}
    
    print(f"    - 准备从 API 下载: {article_title[:50]}...")
    try:
        response = session.post(f"{API_BASE_URL}/api/message/knowledge-platform/v1/download/singleArticle", json=download_payload, stream=True, timeout=20)
        response.raise_for_status()
        
        content_disp = response.headers.get('Content-Disposition', '')
        filename = f"article_{article_id}.mhtml"
        if 'filename=' in content_disp:
            try: filename = content_disp.split('filename=')[1].strip('"').encode('ISO-8859-1').decode('utf-8')
            except UnicodeDecodeError: filename = content_disp.split('filename=')[1].strip('"')
        
        filename = "".join(i for i in filename if i not in r'\/:*?"<>|')
        full_path = os.path.join(DOWNLOAD_DIR, subfolder, filename)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        with open(full_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192): f.write(chunk)
        print(f"    ✔ 下载成功: {full_path}")
        
        convert_mhtml_to_txt(full_path)

    except requests.exceptions.RequestException as e:
        print(f"    [!] 从 API 下载时发生网络错误: {e}")

def process_section(session, name, payload_template, id_key, id_value, is_arxiv):
    """核心逻辑: 处理单个版块"""
    print(f"\n[+] 正在处理板块: {name}")
    
    articles_to_download = []
    latest_date = None
    stop_collecting = False
    page_index = 1
    payload = payload_template.copy()
    payload[id_key] = id_value

    while not stop_collecting:
        print(f"  - 正在获取第 {page_index} 页...")
        payload["pageIndex"] = page_index
        try:
            response = session.post(f"{API_BASE_URL}/api/message/knowledge-platform/v1/articles/article", json=payload)
            response.raise_for_status()
            data = response.json().get("data", {})
            articles_on_page = data.get("sectionArticleList", [])

            if not articles_on_page:
                print("  - 本页无数据，该板块处理结束。"); break

            if page_index == 1 and articles_on_page:
                first_article_time_str = articles_on_page[0].get("articlePublishTime", "").split(" ")[0]
                latest_date = datetime.strptime(first_article_time_str, "%Y-%m-%d").date()
                print(f"  - 已设定最新日期基准为: {latest_date.strftime('%Y-%m-%d')}")
            
            if not latest_date: break
            start_date_limit = latest_date - timedelta(days=TIME_WINDOW_DAYS - 1)

            for article in articles_on_page:
                article_time_str = article.get("articlePublishTime", "").split(" ")[0]
                try: article_date = datetime.strptime(article_time_str, "%Y-%m-%d").date()
                except (ValueError, TypeError): continue

                if article_date < start_date_limit:
                    print(f"  - 遇到超出时间窗口的文章 ({article_date})，停止收集。")
                    stop_collecting = True; break
                if len(articles_to_download) >= MAX_ARTICLES_PER_SECTION:
                    print(f"  - 已收集到 {MAX_ARTICLES_PER_SECTION} 篇，达到上限，停止收集。")
                    stop_collecting = True; break
                articles_to_download.append(article)
            page_index += 1
        except requests.exceptions.RequestException as e:
            print(f"  [!] 获取文章列表时发生网络错误: {e}"); break
        except (json.JSONDecodeError, AttributeError, KeyError) as e:
            print(f"  [!] 解析服务器返回的JSON数据时出错: {e}, 内容: {response.text}"); break
            
    print(f"\n  [*] 收集完成，总共需要下载 {len(articles_to_download)} 篇文章。")
    subfolder = f"arXiv/{name}" if is_arxiv else f"公众号/{name}"
    os.makedirs(os.path.join(DOWNLOAD_DIR, subfolder), exist_ok=True)

    for article in articles_to_download:
        if is_arxiv and article.get("articlePath"):
            full_url = article["articlePath"]
            filename = os.path.basename(full_url)
            filename = "".join(i for i in filename if i not in r'\/:*?"<>|')
            full_path = os.path.join(DOWNLOAD_DIR, subfolder, filename)
            download_from_url(session, full_url, full_path)
        elif not is_arxiv:
            download_from_api(session, article, subfolder)
        else:
            print(f"    [!] 找不到可用的下载链接或下载方式，跳过文章: {article.get('articleTitle')}")

def finalize_directory_structure(temp_dir):
    """[v11 新增] 归档整理最终的文件夹结构"""
    print(f"\n--- [最终阶段] 开始归档整理文件夹 ---")
    if not os.path.isdir(temp_dir):
        print(f"  [!] 临时下载目录 '{temp_dir}' 不存在，跳过归档。"); return

    try:
        data_dir = os.path.join(temp_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        print(f"  - 已创建 'data' 目录: {data_dir}")

        copied_files_count = 0
        for root, dirs, files in os.walk(temp_dir):
            if 'data' in dirs: dirs.remove('data')
            
            for file in files:
                if file.endswith(('.pdf', '.txt')):
                    src_path = os.path.join(root, file)
                    shutil.copy2(src_path, data_dir)
                    copied_files_count += 1
        print(f"  - 已复制 {copied_files_count} 个文件到 'data' 目录。")

        now = datetime.now()
        # 格式: 2025.8.6-15:40
        try:
            timestamp_name = now.strftime('%Y.%-m.%-d-%H:%M')
        except ValueError: # 兼容 Windows
            timestamp_name = f"{now.year}.{now.month}.{now.day}-{now.hour:02d}:{now.minute:02d}"

        final_dir_path = os.path.join(os.path.dirname(os.path.abspath(temp_dir)) or '.', timestamp_name)
        if os.path.exists(final_dir_path):
            timestamp_name = now.strftime('%Y.%-m.%-d-%H:%M:%S')
            final_dir_path = os.path.join(os.path.dirname(os.path.abspath(temp_dir)) or '.', timestamp_name)

        os.rename(temp_dir, final_dir_path)
        print(f"  ✔ 归档完成！最终文件夹: {final_dir_path}")
    except Exception as e:
        print(f"  [!] 归档过程中发生错误: {e}")

def main():
    if "PASTE_YOUR" in LOGIN_TOKEN or "PASTE_YOUR" in COOKIE:
        print("[!!!] 错误：请在脚本的配置区填入你最新的 LOGIN_TOKEN 和 COOKIE！"); return

    session = requests.Session()
    session.headers.update({
        'Accept': '*/*', 'Accept-Language': 'zh-CN,zh;q=0.9', 'Connection': 'keep-alive',
        'Content-Type': 'application/json', 'Origin': API_BASE_URL, 'Referer': f'{API_BASE_URL}/knowledgePlatform/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36',
        'logintoken': LOGIN_TOKEN, 'Cookie': COOKIE
    })

    print("\n--- [阶段1/2] 开始处理 arXiv 论文 ---")
    payload_arxiv = {"labelId": None, "pageSize": 20, "orderMode": 0, "orderType": 1,
                     "startDate": None, "endDate": None, "searchKey": None}
    for section_name, section_id in ARXIV_CONFIG["sections"].items():
        process_section(session, section_name, payload_arxiv, "sectionId", section_id, is_arxiv=True)

    print("\n--- [阶段2/2] 开始处理公众号文章 ---")
    payload_account = ACCOUNTS_CONFIG["list_payload_template"]
    for account_name, account_id in ACCOUNTS_CONFIG["accounts"].items():
        process_section(session, account_name, payload_account, "accountId", account_id, is_arxiv=False)

    print("\n--- 所有下载与转换任务完成 ---")
    finalize_directory_structure(DOWNLOAD_DIR)

if __name__ == "__main__":
    main()