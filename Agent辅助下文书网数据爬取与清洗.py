import json
import re
import csv
import os
import time
import random
import threading
from Crypto.Util.Padding import unpad, pad
import tkinter as tk
from tkinter import messagebox, scrolledtext, filedialog
from urllib.parse import urlencode, quote
from datetime import datetime
import requests
import base64
import string
from Crypto.Cipher import DES3
from Crypto.Util.Padding import unpad
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== 爬虫全局配置 ====================
COOKIES = {
    "wzws_reurl": "L3dlYnNpdGUvd2Vuc2h1Lmljbw==",
    "SESSION": ""
}
MANUAL_TOKEN = ""
USER_AGENT = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"
HEADERS = {
    "Host": "wenshu.court.gov.cn",
    "Connection": "keep-alive",
    "sec-ch-ua-platform": "\"Windows\"",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "sec-ch-ua": "\"Not;A=Brand\";v=\"8\", \"Chromium\";v=\"150\", \"Google Chrome\";v=\"150\"",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "sec-ch-ua-mobile": "?0",
    "Origin": "https://wenshu.court.gov.cn",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
RECORD_FILE = "crawled_record.txt"

# ==================== 豆包AI配置 ====================
DOUBAO_API_KEY = ""
DOUBAO_MODEL = "doubao-1.5-pro-32k"
DOUBAO_API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
ENABLE_AI_CLEAN = False

# 合并CSV专用全局缓存
MERGE_CACHE = []
MERGE_HEADER = [
    '序号', '年份', '省份', '地市', '县级', '标题', '案号', '裁判法院',
    '裁判日期', '案由/罪名', '案件类型（一审）', '文书类型',
    '原告', '被告人', '刑期', '罚金', '全文'
]

# ==================== 爬虫原始工具函数 ====================
def random_salt(length=24):
    return ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(length))

def str_to_binary(s):
    return " ".join(bin(ord(ch))[2:] for ch in s)

def generate_ciphertext(salt=None, timestamp=None):
    if timestamp is None:
        timestamp = str(int(time.time() * 1000))
    if salt is None:
        salt = random_salt(24)
    iv_str = datetime.now().strftime('%Y%m%d')
    iv = iv_str.encode('utf-8')
    if len(iv) < 8:
        iv = iv.ljust(8, b'\x00')
    elif len(iv) > 8:
        iv = iv[:8]
    key = salt.encode('utf-8')
    if len(key) != 24:
        raise ValueError("salt 长度必须为24")
    plaintext = timestamp.encode('utf-8')
    cipher = DES3.new(key, DES3.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(plaintext, DES3.block_size))
    enc = base64.b64encode(encrypted).decode('utf-8')
    combined = salt + iv_str + enc
    return str_to_binary(combined), salt, timestamp

def decrypt_result(result_b64, secret_key):
    key = secret_key.encode('utf-8')
    if len(key) < 24:
        key = key.ljust(24, b'\x00')
    elif len(key) > 24:
        key = key[:24]
    iv_str = datetime.now().strftime('%Y%m%d')
    iv = iv_str.encode('utf-8')
    if len(iv) < 8:
        iv = iv.ljust(8, b'\x00')
    elif len(iv) > 8:
        iv = iv[:8]
    cipher = DES3.new(key, DES3.MODE_CBC, iv)
    encrypted = base64.b64decode(result_b64)
    decrypted = cipher.decrypt(encrypted)
    return unpad(decrypted, DES3.block_size).decode('utf-8')

def generate_token():
    return random_salt(24)

def get_verification_token(page_id, s7):
    global MANUAL_TOKEN
    if MANUAL_TOKEN:
        return MANUAL_TOKEN
    url = "https://wenshu.court.gov.cn/website/wenshu/181217BMTKHNT2W0/index.html"
    headers = HEADERS.copy()
    enc_s7 = quote(s7, safe='')
    referer = f"https://wenshu.court.gov.cn/website/wenshu/181217BMTKHNT2W0/index.html?pageId={page_id}&s7={enc_s7}"
    headers["Referer"] = referer
    try:
        resp = requests.get(url, headers=headers, cookies=COOKIES, timeout=30, verify=False)
        resp.raise_for_status()
        pattern = r'<input[^>]*name="__RequestVerificationToken"[^>]*value="([^"]+)"'
        match = re.search(pattern, resp.text)
        if match:
            return match.group(1)
        else:
            raise Exception("未找到 token")
    except Exception as e:
        raise Exception(f"自动获取 token 失败，请手动填写 Token: {e}")

def clean_html_tags(text):
    if not text:
        return ""
    text = re.sub(r"<.*?>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def get_province(court_name):
    if not court_name:
        return ""
    pat = r"(北京|上海|天津|重庆|[\u4e00-\u9fa5]+?省|[\u4e00-\u9fa5]+?自治区)"
    res = re.search(pat, court_name)
    return res.group(1) if res else ""

def extract_city_county(court_name):
    city, county = "", ""
    if not court_name:
        return city, county
    m = re.search(r'([\u4e00-\u9fa5]+?)(市|自治州|州|地区)([\u4e00-\u9fa5]+?)(县|区|市)?人民法院', court_name)
    if m:
        city = m.group(1) + m.group(2)
        county = m.group(3) + (m.group(4) if m.group(4) else "")
    else:
        m2 = re.search(r'([\u4e00-\u9fa5]+?)(市|自治州|州|地区)?中级人民法院', court_name)
        if m2:
            city = m2.group(1) + (m2.group(2) if m2 else "")
        else:
            city = court_name.replace("人民法院", "").replace("高级", "")
    return city, county

def extract_all_field(raw_html, title=''):
    text = clean_html_tags(raw_html)
    res = {"原告": "", "被告人": "", "案由/罪名": "", "刑期": "", "罚金": ""}
    if not text:
        return res
    if title:
        title_clean = re.sub(r'^\([^)]+\)\s*', '', title)
        crime_match = re.search(r'([^，。；\n]+(?:纠纷|罪|合同|侵权|无效))', title_clean)
        if crime_match:
            res["案由/罪名"] = crime_match.group(1).strip()
    if not res["案由/罪名"]:
        head = text[:200]
        m = re.search(r'([^，。；\n]+(?:纠纷|罪|合同|侵权|无效))', head)
        if m:
            res["案由/罪名"] = m.group(1).strip()
    plaintiff_patterns = [r'被上诉人[:：]\s*([^，。；\n]+)',r'被申请人[:：]\s*([^，。；\n]+)',r'原告[:：]\s*([^，。；\n]+)',r'再审申请人[:：]\s*([^，。；\n]+)']
    for pat in plaintiff_patterns:
        m = re.search(pat, text)
        if m:
            res["原告"] = m.group(1).strip()
            break
    if not res["原告"]:
        company_match = re.search(r'([\u4e00-\u9fa5]+(?:有限|股份|集团|科技)公司)', text)
        if company_match:
            res["原告"] = company_match.group(1).strip()
    defendant_patterns = [r'上诉人[:：]\s*([^，。；\n]+)',r'被告[:：]\s*([^，。；\n]+)',r'再审被申请人[:：]\s*([^，。；\n]+)',r'一审第三人[:：]\s*([^，。；\n]+)',r'原审第三人[:：]\s*([^，。；\n]+)',r'被告人[:：]\s*([^，。；\n]+)']
    for pat in defendant_patterns:
        m = re.search(pat, text)
        if m:
            res["被告人"] = m.group(1).strip()
            break
    if not res["被告人"]:
        companies = re.findall(r'([\u4e00-\u9fa5]+(?:有限|股份|集团|科技)公司)', text)
        if len(companies) >= 2:
            res["被告人"] = companies[1].strip()
    for pat in [r'判处有期徒刑([^，。；\n]+)',r'判处(无期徒刑)',r'判处(死刑[^，。；\n]*)',r'判处拘役([^，。；\n]+)',r'判处管制([^，。；\n]+)']:
        m = re.search(pat, text)
        if m:
            res["刑期"] = m.group(0).strip()
            break
    fine_m = re.search(r'罚金([^，。；\n]+)', text)
    if fine_m:
        res["罚金"] = fine_m.group(1).strip()
    return res

def parse_detail(data):
    s1 = data.get('s1', '')
    s2 = data.get('s2', '')
    s7 = data.get('s7', '')
    s9 = data.get('s9', '')
    s6 = data.get('s6', '')
    s31 = data.get('s31', '')
    qw_raw = data.get('qwContent', '')
    field_info = extract_all_field(qw_raw, s1)
    province = get_province(s2)
    city, county = extract_city_county(s2)
    return {
        '序号': 0,
        '年份': s31[:4] if s31 else '',
        '省份': province,
        '地市': city,
        '县级': county,
        '标题': s1,
        '案号': s7,
        '裁判法院': s2,
        '裁判日期': s31,
        '案由/罪名': field_info["案由/罪名"],
        '案件类型（一审）': s9,
        '文书类型': '判决书' if s6 == '01' else s6,
        '原告': field_info["原告"],
        '被告人': field_info["被告人"],
        '刑期': field_info["刑期"],
        '罚金': field_info["罚金"],
        '全文': qw_raw
    }

def save_single_raw(parsed, case_no):
    safe_name = re.sub(r'[\\/*?:"<>|]', '_', case_no)
    fname = f"raw_{safe_name}.csv"
    fields = MERGE_HEADER
    parsed["序号"] = 1
    with open(fname, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        wr.writerow(parsed)
    return fname

def get_crawled_set():
    if not os.path.exists(RECORD_FILE):
        return set()
    with open(RECORD_FILE, "r", encoding="utf-8") as f:
        lines = [x.strip() for x in f.readlines() if x.strip()]
    return set(lines)

def add_record(case_no):
    with open(RECORD_FILE, "a", encoding="utf-8") as f:
        f.write(case_no + "\n")

def update_summary_log(date_str, count):
    log_file = "summary.log"
    lines = []
    updated = False
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    with open(log_file, 'w', encoding='utf-8') as f:
        for line in lines:
            if line.startswith(date_str + '|'):
                parts = line.strip().split('|')
                if len(parts) == 2:
                    old_count = int(parts[1])
                    new_count = old_count + count
                    f.write(f"{date_str}|{new_count}\n")
                    updated = True
                else:
                    f.write(line)
            else:
                f.write(line)
        if not updated:
            f.write(f"{date_str}|{count}\n")

def search_case(case_number):
    global MANUAL_TOKEN
    url = "https://wenshu.court.gov.cn/website/parse/rest.q4w"
    headers = HEADERS.copy()
    page_id = "36a357386e897df0418b103fb753b215"
    enc_case = quote(case_number, safe='')
    headers["Referer"] = f"https://wenshu.court.gov.cn/website/wenshu/181217BMTKHNT2W0/index.html?pageId={page_id}&s7={enc_case}"
    try:
        token = get_verification_token(page_id, case_number)
    except Exception as e:
        raise Exception(f"Token 获取失败: {e}")
    cond = [{"key": "s7", "value": case_number}]
    cond_str = json.dumps(cond, ensure_ascii=False)
    ciphertext, salt, ts = generate_ciphertext()
    data = {
        "pageId": page_id, "s7": case_number, "sortFields": "s50:desc",
        "ciphertext": ciphertext, "pageNum": "1", "pageSize": "5",
        "queryCondition": cond_str, "cfg": "com.lawyee.judge.dc.parse.dto.SearchDataDsoDTO@queryDoc",
        "__RequestVerificationToken": token, "wh": "828", "ww": "1536", "cs": "0",
    }
    resp = requests.post(url, headers=headers, cookies=COOKIES, data=data, timeout=30, verify=False)
    resp.raise_for_status()
    j = resp.json()
    if j.get("code") != 1:
        raise Exception(j.get("description", "搜索失败"))
    sk = j["secretKey"]
    enc_res = j["result"]
    plain = decrypt_result(enc_res, sk)
    data_json = json.loads(plain)
    rel = data_json.get("relWenshu", {})
    return list(rel.keys())

def get_detail(doc_id):
    global MANUAL_TOKEN
    url = "https://wenshu.court.gov.cn/website/parse/rest.q4w"
    headers = HEADERS.copy()
    headers["Referer"] = f"https://wenshu.court.gov.cn/website/wenshu/181107ANFZ0BXSK4/index.html?docId={doc_id}"
    ciphertext, salt, ts = generate_ciphertext()
    try:
        token = get_verification_token("36a357386e897df0418b103fb753b215", "dummy")
    except:
        token = MANUAL_TOKEN if MANUAL_TOKEN else generate_token()
    data = {
        "docId": doc_id, "ciphertext": ciphertext,
        "cfg": "com.lawyee.judge.dc.parse.dto.SearchDataDsoDTO@docInfoSearch",
        "__RequestVerificationToken": token, "wh": "256", "ww": "1536", "cs": "0",
    }
    resp = requests.post(url, headers=headers, cookies=COOKIES, data=data, timeout=30, verify=False)
    resp.raise_for_status()
    j = resp.json()
    if j.get("code") != 1:
        raise Exception(j.get("description", "详情获取失败"))
    sk = j["secretKey"]
    enc_res = j["result"]
    plain = decrypt_result(enc_res, sk)
    return json.loads(plain)

# ==================== 豆包AI清洗函数 ====================
def clean_with_doubao(full_text, title):
    """
    调用豆包API从全文提取结构化字段
    返回字典：{"原告": "", "被告人": "", "案由/罪名": "", "刑期": "", "罚金": ""}
    """
    if not full_text:
        return {}
    text_for_ai = full_text[:4000] if len(full_text) > 4000 else full_text
    prompt = f"""请根据以下裁判文书全文提取关键信息。返回纯JSON格式，不要有其他文字。

JSON字段：
- "原告": 民事案件的原告或行政案件的被上诉人/被申请人，若有多个用逗号分隔
- "被告人": 刑事案件的被告人或行政案件的上诉人/再审申请人，若有多个用逗号分隔
- "案由/罪名": 案由或罪名
- "刑期": 刑事判决中的刑期（如"有期徒刑三年"），若无则空
- "罚金": 判决中的罚金金额（如"罚金人民币十万元"），若无则空

文书标题：{title}

文书全文：
{text_for_ai}
"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DOUBAO_API_KEY}"
    }
    data = {
        "model": DOUBAO_MODEL,
        "messages": [
            {"role": "system", "content": "你是一个法律文书信息提取专家。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 500
    }
    try:
        resp = requests.post(DOUBAO_API_URL, headers=headers, json=data, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        content = result['choices'][0]['message']['content']
        content = re.sub(r'```json\s*', '', content)
        content = re.sub(r'```\s*', '', content)
        parsed = json.loads(content.strip())
        fields = ["原告", "被告人", "案由/罪名", "刑期", "罚金"]
        for f in fields:
            if f not in parsed:
                parsed[f] = ""
        return parsed
    except Exception as e:
        print(f"豆包API调用失败: {e}")
        return {}

# ==================== 内置二次清洗补全模块 ====================
def format_text_line_break(text, max_line_length=180):
    if not text:
        return ""
    text = re.sub(r'([。；！？])', r'\1\n', text)
    text = re.sub(r'([：；，、])', r'\1\n', text)
    lines = text.split('\n')
    formatted_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        while len(line) > max_line_length:
            split_idx = max(line.rfind('，', 0, max_line_length), line.rfind('、', 0, max_line_length), line.rfind(' ', 0, max_line_length))
            if split_idx == -1:
                formatted_lines.append(line[:max_line_length])
                line = line[max_line_length:]
            else:
                formatted_lines.append(line[:split_idx+1])
                line = line[split_idx+1:].strip()
        formatted_lines.append(line)
    return '\n'.join(formatted_lines)

def extract_max_penalty_amount(full_text):
    text = clean_html_tags(full_text)
    if not text:
        return ""
    penalty_keywords = ["赔偿","补偿","支付","给付","连带赔偿","连带支付","经济损失","合理开支","使用费","违约金","赔偿金","补偿金","占用费","利息","罚息","罚金","罚款","没收","违法所得","赃款"]
    sentence_pattern = r'[^。；！？\n]*?(?:' + '|'.join(penalty_keywords) + ')[^。；！？\n]*?'
    amount_pattern = r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)\s*(元|万元|亿元|美元|欧元)?'
    matched_segments = re.findall(sentence_pattern, text, re.DOTALL)
    if not matched_segments:
        return ""
    amount_list = []
    for seg in matched_segments:
        amount_match = re.search(amount_pattern, seg)
        if not amount_match:
            continue
        num_str = amount_match.group(1).replace(',', '')
        unit = amount_match.group(2) if amount_match.group(2) else "元"
        try:
            num_value = float(num_str)
        except:
            continue
        if unit == "万元":
            num_value *= 10000
        elif unit == "亿元":
            num_value *= 10000000
        elif unit == "美元":
            num_value *= 7.2
        elif unit == "欧元":
            num_value *= 7.8
        amount_list.append({"orig": amount_match.group(0), "val": num_value})
    if not amount_list:
        return ""
    amount_list.sort(key=lambda x: x["val"], reverse=True)
    return amount_list[0]["orig"]

def full_optimize_row(raw_row):
    row = raw_row.copy()
    court = row["裁判法院"]
    fulltxt = row["全文"]

    # 地域修正
    if "最高人民法院" in court:
        row["省份"] = "北京市"
        row["地市"] = "北京市"
        row["县级"] = "北京市"
    else:
        if not row["省份"]:
            row["省份"] = get_province(court)
        if not row["地市"] or not row["县级"]:
            c, cy = extract_city_county(court)
            if not row["地市"]:
                row["地市"] = c
            if not row["县级"]:
                row["县级"] = cy

    # 规则提取罚金（保留作为基础）
    row["罚金"] = extract_max_penalty_amount(fulltxt)

    # AI清洗补充
    if ENABLE_AI_CLEAN and DOUBAO_API_KEY:
        need_ai = False
        for key in ["原告", "被告人", "案由/罪名"]:
            if not row.get(key):
                need_ai = True
                break
        if need_ai:
            ai_result = clean_with_doubao(fulltxt, row.get("标题", ""))
            if ai_result:
                for key in ["原告", "被告人", "案由/罪名", "刑期", "罚金"]:
                    if ai_result.get(key):
                        row[key] = ai_result[key]

    # 全文换行
    row["全文"] = format_text_line_break(fulltxt)
    return row

def save_merge_final(log_cb):
    global MERGE_CACHE
    if not MERGE_CACHE:
        log_cb("无数据可合并")
        return
    out_name = "全部文书汇总_最终清洗版.csv"
    with open(out_name, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MERGE_HEADER)
        w.writeheader()
        w.writerows(MERGE_CACHE)
    log_cb(f"✅ 合并总表已生成：{out_name}")

def process_single_raw_file(raw_path, log_cb, output_mode):
    global MERGE_CACHE
    df_raw = []
    with open(raw_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            df_raw.append(r)
    for row in df_raw:
        opt_row = full_optimize_row(row)
        if output_mode == "merge":
            MERGE_CACHE.append(opt_row)
        else:
            safe = raw_path.replace("raw_","").replace(".csv","_清洗完成.csv")
            with open(safe, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, MERGE_HEADER)
                w.writeheader()
                w.writerow(opt_row)
            log_cb(f"已生成清洗文件：{safe}")
    os.remove(raw_path)

# ==================== 批量爬取主逻辑 ====================
def crawl_batch(case_file, max_total, max_days, log_cb, status_cb, total_cb, output_mode):
    global MERGE_CACHE
    MERGE_CACHE.clear()
    if not os.path.exists(case_file):
        log_cb(f"❌ 文件不存在：{case_file}")
        return
    with open(case_file, "r", encoding="utf-8") as f:
        all_cases = [x.strip() for x in f.readlines() if x.strip()]
    if not all_cases:
        log_cb("❌ 案号列表为空")
        return
    crawled = get_crawled_set()
    todo_list = [x for x in all_cases if x not in crawled]
    if not todo_list:
        log_cb("✅ 所有案号已爬取完成，无需重复执行")
        status_cb("全部完成")
        if output_mode == "merge":
            save_merge_final(log_cb)
        return
    total_count = len(crawled)
    start_time = datetime.now()
    today = datetime.now()
    log_cb(f"📌 已完成：{len(crawled)} 条，待爬取：{len(todo_list)} 条")
    for idx, case_no in enumerate(todo_list, 1):
        if max_total > 0 and total_count >= max_total:
            log_cb(f"⏹ 达到最大条数 {max_total}，停止")
            break
        if max_days > 0:
            run_h = (datetime.now() - start_time).total_seconds() / 3600
            if run_h >= max_days * 24:
                log_cb(f"⏱ 运行满 {max_days}，停止")
                break
        log_cb(f"🔍 [{idx}/{len(todo_list)}] 案号：{case_no}")

        doc_ids = None
        for attempt in range(1, 6):
            try:
                doc_ids = search_case(case_no)
                break
            except Exception as e:
                err_msg = str(e)
                if "登录" in err_msg or "SESSION" in err_msg:
                    log_cb("⚠️ Cookie失效，请重新填写SESSION")
                    status_cb("Cookie失效")
                    return
                if "Token" in err_msg:
                    log_cb("⚠️ Token 问题，请手动填写 Token")
                    status_cb("Token无效")
                    return
                log_cb(f"   ⚠️ 搜索尝试 {attempt}/5 失败：{err_msg}")
                if attempt == 5:
                    log_cb(f"❌ 搜索异常：{err_msg}")
                    break
                time.sleep(2)

        if not doc_ids:
            if doc_ids is None:
                continue
            else:
                log_cb(f"   ⚠️ 无匹配文书")
                add_record(case_no)
                continue

        detail_data = None
        for attempt in range(1, 6):
            try:
                detail_data = get_detail(doc_ids[0])
                break
            except Exception as e:
                err_msg = str(e)
                log_cb(f"   ⚠️ 详情尝试 {attempt}/5 失败：{err_msg}")
                if attempt == 5:
                    log_cb(f"❌ 获取详情失败：{err_msg}")
                    break
                time.sleep(2)

        if not detail_data:
            continue

        parsed_raw = parse_detail(detail_data)
        raw_file = save_single_raw(parsed_raw, case_no)
        process_single_raw_file(raw_file, log_cb, output_mode)
        add_record(case_no)
        total_count += 1
        log_cb(f"   ✅ 处理完成 | 标题：{parsed_raw['标题']}")
        status_cb(f"已爬：{total_count} 条")
        total_cb(str(total_count))
        update_summary_log(today.strftime("%Y-%m-%d"), 1)
        time.sleep(random.uniform(1, 3))
    log_cb(f"🏁 爬取任务结束，累计完成 {total_count} 条")
    if output_mode == "merge":
        save_merge_final(log_cb)
    status_cb("任务结束")

# ==================== GUI界面 ====================
class App:
    def __init__(self, root):
        self.root = root
        root.title("裁判文书爬取｜单文件/合并CSV｜豆包AI自动清洗")
        root.geometry("900x800")
        root.resizable(False, True)
        self.output_mode = tk.StringVar(value="single")

        # SESSION
        tk.Label(root, text="SESSION Cookie：", font=("微软雅黑", 10)).pack(pady=(8,0))
        f1 = tk.Frame(root)
        f1.pack(pady=4)
        self.session_entry = tk.Entry(f1, width=52)
        self.session_entry.pack(side=tk.LEFT, padx=5)
        self.session_entry.insert(0, COOKIES["SESSION"])
        tk.Button(f1, text="设置", command=self.set_cookie, bg="lightyellow").pack(side=tk.LEFT)

        # Token
        tk.Label(root, text="Token（留空自动获取，失败手动填）：", font=("微软雅黑", 10)).pack(pady=(8,0))
        f1b = tk.Frame(root)
        f1b.pack(pady=4)
        self.token_entry = tk.Entry(f1b, width=52)
        self.token_entry.pack(side=tk.LEFT, padx=5)
        self.token_entry.insert(0, "FLCJcdonGIaafAwWhSDIC1ae")
        tk.Button(f1b, text="设置Token", command=self.set_token, bg="lightgreen").pack(side=tk.LEFT)

        # UA
        tk.Label(root, text="User-Agent：", font=("微软雅黑", 10)).pack(pady=(8,0))
        f1c = tk.Frame(root)
        f1c.pack(pady=4)
        self.ua_entry = tk.Entry(f1c, width=72)
        self.ua_entry.pack(side=tk.LEFT, padx=5)
        self.ua_entry.insert(0, USER_AGENT)
        tk.Button(f1c, text="设置UA", command=self.set_ua, bg="lightcyan").pack(side=tk.LEFT)

        # ---- 豆包AI配置 ----
        tk.Label(root, text="豆包API Key（留空禁用AI清洗）：", font=("微软雅黑", 10)).pack(pady=(8,0))
        f1d = tk.Frame(root)
        f1d.pack(pady=4)
        self.api_entry = tk.Entry(f1d, width=50, show="*")
        self.api_entry.pack(side=tk.LEFT, padx=5)
        self.api_entry.insert(0, DOUBAO_API_KEY)
        self.enable_ai_var = tk.IntVar(value=1 if ENABLE_AI_CLEAN else 0)
        tk.Checkbutton(f1d, text="启用AI清洗", variable=self.enable_ai_var).pack(side=tk.LEFT, padx=10)
        tk.Button(f1d, text="设置API", command=self.set_api, bg="lightpink").pack(side=tk.LEFT)

        # 输出模式
        f_out = tk.Frame(root)
        f_out.pack(pady=6)
        tk.Label(f_out, text="CSV输出模式：", font=("微软雅黑",10,"bold")).pack(side=tk.LEFT,padx=5)
        tk.Radiobutton(f_out, text="一案一号单独生成CSV", variable=self.output_mode, value="single").pack(side=tk.LEFT,padx=10)
        tk.Radiobutton(f_out, text="全部文书合并为一张总表", variable=self.output_mode, value="merge").pack(side=tk.LEFT,padx=10)

        # 案号文件
        tk.Label(root, text="案号TXT文件：", font=("微软雅黑", 10)).pack(pady=(8,0))
        f2 = tk.Frame(root)
        f2.pack(pady=4)
        self.file_entry = tk.Entry(f2, width=62)
        self.file_entry.pack(side=tk.LEFT, padx=5)
        tk.Button(f2, text="浏览", command=self.select_file).pack(side=tk.LEFT)

        # 限制参数
        f3 = tk.Frame(root)
        f3.pack(pady=6)
        tk.Label(f3, text="最大爬取条数(0不限)：").pack(side=tk.LEFT)
        self.max_total = tk.Entry(f3, width=8)
        self.max_total.insert(0, "100")
        self.max_total.pack(side=tk.LEFT, padx=5)
        tk.Label(f3, text="最大运行天数(0不限)：").pack(side=tk.LEFT, padx=20)
        self.max_day = tk.Entry(f3, width=8)
        self.max_day.insert(0, "3")
        self.max_day.pack(side=tk.LEFT, padx=5)

        # 状态
        self.stat_var = tk.StringVar(value="等待启动")
        self.num_var = tk.StringVar(value="0")
        f4 = tk.Frame(root)
        f4.pack(pady=5)
        tk.Label(f4, text="已爬数量：").pack(side=tk.LEFT)
        tk.Label(f4, textvariable=self.num_var, font=("微软雅黑",14,"bold"), fg="blue").pack(side=tk.LEFT)
        tk.Label(f4, text="运行状态：", padx=20).pack(side=tk.LEFT)
        tk.Label(f4, textvariable=self.stat_var, fg="green").pack(side=tk.LEFT)

        # 日志
        tk.Label(root, text="运行日志", anchor="w").pack(padx=10)
        self.log_box = scrolledtext.ScrolledText(root, height=16, font=("宋体",9), bg="#f0f8ff")
        self.log_box.pack(fill="both", expand=True, padx=10, pady=4)

        # 按钮
        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="🚀 启动爬取+自动清洗", command=self.start_task, bg="#b8e1ff", font=("微软雅黑",12)).pack(side=tk.LEFT, padx=20)
        tk.Button(btn_frame, text="退出程序", command=root.quit, bg="#ffb8b8", font=("微软雅黑",12)).pack(side=tk.LEFT)

    def log(self, msg):
        self.log_box.insert(tk.END, msg + "\n")
        self.log_box.see(tk.END)

    def set_cookie(self):
        val = self.session_entry.get().strip()
        if val:
            COOKIES["SESSION"] = val
            self.log("✅ SESSION Cookie已生效")
        else:
            messagebox.showwarning("提示", "SESSION不能为空")

    def set_token(self):
        global MANUAL_TOKEN
        val = self.token_entry.get().strip()
        if val:
            MANUAL_TOKEN = val
            self.log(f"✅ Token已设置：{val[:10]}...")
        else:
            MANUAL_TOKEN = ""
            self.log("⚠️ Token清空，自动获取模式")

    def set_ua(self):
        global USER_AGENT, HEADERS
        val = self.ua_entry.get().strip()
        if val:
            USER_AGENT = val
            HEADERS["User-Agent"] = val
            self.log("✅ User-Agent更新完成")
        else:
            USER_AGENT = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"
            HEADERS["User-Agent"] = USER_AGENT
            self.log("ℹ️ UA恢复默认")

    def set_api(self):
        global DOUBAO_API_KEY, ENABLE_AI_CLEAN
        val = self.api_entry.get().strip()
        if val:
            DOUBAO_API_KEY = val
            ENABLE_AI_CLEAN = bool(self.enable_ai_var.get())
            self.log(f"✅ 豆包API Key已设置，AI清洗{'启用' if ENABLE_AI_CLEAN else '禁用'}")
        else:
            DOUBAO_API_KEY = ""
            ENABLE_AI_CLEAN = False
            self.log("⚠️ API Key为空，AI清洗禁用")

    def select_file(self):
        path = filedialog.askopenfilename(filetypes=[("文本*.txt", "*.txt"), ("全部文件", "*")])
        if path:
            self.file_entry.delete(0, tk.END)
            self.file_entry.insert(0, path)

    def start_task(self):
        if not COOKIES["SESSION"]:
            messagebox.showwarning("警告", "请先填写并设置SESSION Cookie")
            return
        fpath = self.file_entry.get().strip()
        if not os.path.exists(fpath):
            messagebox.showwarning("警告", "案号txt文件不存在")
            return
        try:
            mt = int(self.max_total.get().strip()) if self.max_total.get() else 0
        except:
            mt = 0
        try:
            md = int(self.max_day.get().strip()) if self.max_day.get() else 0
        except:
            md = 0
        mode = self.output_mode.get()
        self.log("="*40 + " 任务开始 " + "="*40)
        self.stat_var.set("运行中")
        threading.Thread(target=lambda: self.run_task(fpath, mt, md, mode), daemon=True).start()

    def run_task(self, fpath, maxt, maxd, mode):
        def log_cb(m): self.log(m)
        def st_cb(m): self.stat_var.set(m)
        def num_cb(v): self.num_var.set(v)
        crawl_batch(fpath, maxt, maxd, log_cb, st_cb, num_cb, mode)
        self.stat_var.set("任务结束")

if __name__ == "__main__":
    win = tk.Tk()
    App(win)
    win.mainloop()