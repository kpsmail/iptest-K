import urllib.request
import urllib.error
import re
import os
import socket
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

# =============================================================================
# 脚本功能概述（无需安装第三方依赖即可运行）：
#   1. 测试本机是否能通过 HTTP GET 访问指定的“ping”网址（https://www.google.com/generate_204）(https://www.gstatic.com/generate_204)
#   2. 从预设的一组 URL 中并发抓取内容（使用 urllib），提取 IPv4 地址（支持带端口与不带端口）
#   3. 对提取到的所有 IP 进行“TCP 端口连通性检测”
#      - 不带端口的 IP 会用 common_ports 中的端口进行检测
#      - 带端口的 IP 只检测该自带端口
#   4. 对“可能可用节点”获取国家代码，并写入本地文件 ip.txt（格式 ip:port#country）
#   5. 控制台打印各步骤进度和结果
# =============================================================================

# -------------------------------
# 全局变量与配置信息
# -------------------------------
URLS = [
    'https://raw.githubusercontent.com/Fido6/bestip/refs/heads/dtaa/bestiphk.txt',
    'https://raw.githubusercontent.com/Fido6/bestip/refs/heads/dtaa/bestipjp.txt',
    'https://raw.githubusercontent.com/Fido6/bestip/refs/heads/dtaa/bestipsg.txt'
]

# 支持匹配形如 "1.2.3.4" 或 "1.2.3.4:8080"
IP_PATTERN = r'\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b'
PING_URL = 'https://www.gstatic.com/generate_204'
OUTPUT_FILE = 'ip.txt'
MAX_WORKERS = 5
RETRY_LIMIT = 2
common_ports = [443] # [80, 443, 1080]
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0 Safari/537.36'
)

# ip_set 存放 (ip, port_or_None) 的元组
ip_set = set()
# alive_ip_set 存放 (ip, port) 的元组，port 为 int（表示已确认开放的端口）
alive_ip_set = set()
MAX_CHECK_WORKERS = 30

# -------------------------------
# 函数：测试本机能否访问指定的 PING_URL
# -------------------------------
def test_connectivity():
    print(f"[*] 正在测试本机网络连通性，访问：{PING_URL} …")
    try:
        req = urllib.request.Request(PING_URL, headers={'User-Agent': USER_AGENT}, method='GET')
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.getcode() == 204:
                print("[√] 本机网络连通性正常。\n")
            else:
                print(f"[!] 返回状态码 {response.getcode()}，请检查网络环境。\n")
    except Exception as e:
        print(f"[×] 测试失败：{e}，请检查 DNS 或网络设置。\n")


# -------------------------------
# 函数：验证 IP 是否合法
# -------------------------------
def is_valid_ip(ip):
    try:
        parts = ip.split('.')
        if len(parts) != 4:
            return False
        for part in parts:
            if not part.isdigit():
                return False
            n = int(part)
            if n < 0 or n > 255:
                return False
        return True
    except Exception:
        return False


# -------------------------------
# 函数：解析 ip[:port] 字符串，返回 (ip, port_or_None)
# -------------------------------
def parse_ip_port(s):
    if ':' in s:
        ip_part, port_part = s.split(':', 1)
        if not is_valid_ip(ip_part):
            return None
        if not port_part.isdigit():
            return None
        port = int(port_part)
        if 1 <= port <= 65535:
            return (ip_part, port)
        else:
            return None
    else:
        if is_valid_ip(s):
            return (s, None)
        else:
            return None


# -------------------------------
# 函数：从字符串中提取所有符合 IPv4 或 IPv4:port 格式的地址
# 返回值：[(ip, port_or_None), ...]
# -------------------------------
def extract_ips_from_text(text):
    raw_matches = re.findall(IP_PATTERN, text)
    results = []
    for m in raw_matches:
        parsed = parse_ip_port(m)
        if parsed:
            results.append(parsed)
    # 去重并返回
    return list(set(results))


# -------------------------------
# 函数：并发抓取单个 URL 的内容
# -------------------------------
def fetch_url(url, retry=0):
    try:
        print(f"[*] 正在抓取：{url}")
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT}, method='GET')
        with urllib.request.urlopen(req, timeout=20) as response:
            raw_bytes = response.read()
            try:
                content = raw_bytes.decode('utf-8', errors='ignore')
            except:
                content = raw_bytes.decode('iso-8859-1', errors='ignore')
            return extract_ips_from_text(content)
    except Exception as e:
        if retry < RETRY_LIMIT:
            print(f"    [!] 请求失败，正在重试第 {retry + 1} 次：{url}")
            return fetch_url(url, retry + 1)
        else:
            print(f"    [!] 请求失败，跳过该地址：{url}")
            return []


# -------------------------------
# 函数：并发抓取所有 URL 并提取 IP
# -------------------------------
def fetch_and_extract_ips():
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {executor.submit(fetch_url, url): url for url in URLS}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                ips = future.result()
                for ip_tuple in ips:
                    ip_set.add(ip_tuple)
            except Exception as exc:
                print(f"    [!] 异常发生在抓取 {url}: {exc}")
    print(f"\n[*] 抓取完成，共提取到 {len(ip_set)} 个唯一 IP 或 IP:port 条目。\n")


# -------------------------------
# 函数：测试某个 IP:port 是否能建立 TCP 连接
# -------------------------------
def check_port_open(ip, port, timeout=3):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.close()
        return True
    except Exception:
        return False


# -------------------------------
# 函数：为某个 IP 检查一组端口，返回所有开放的端口列表
# -------------------------------
def check_ports_for_ip(ip, ports, timeout=3):
    open_ports = []
    # 使用较小的并行度检查单个 IP 的多个端口
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(ports)))) as executor:
        future_to_port = {executor.submit(check_port_open, ip, port, timeout): port for port in ports}
        for future in as_completed(future_to_port):
            port = future_to_port[future]
            try:
                if future.result():
                    open_ports.append(port)
            except Exception:
                pass
    return open_ports


# -------------------------------
# 函数：对提取到的 IP 进行可用性检测
# 说明：
#  - 对于 (ip, None) : 使用 common_ports 检测，若有开放端口则记录每个开放端口 (ip, open_port)
#  - 对于 (ip, port)  : 仅检测该 port，若开放则记录 (ip, port)
# -------------------------------
def filter_alive_ips():
    print("[*] 开始对提取到的 IP 进行端口连通性检测……")
    alive_items = []
    with ThreadPoolExecutor(max_workers=MAX_CHECK_WORKERS) as executor:
        future_to_item = {}
        for ip, port in ip_set:
            if port is None:
                ports_to_check = common_ports
            else:
                ports_to_check = [port]
            # 提交工作，返回 (ip, ports_checked) 结果
            future = executor.submit(check_ports_for_ip, ip, ports_to_check)
            future_to_item[future] = (ip, ports_to_check)

        for future in as_completed(future_to_item):
            ip, ports_checked = future_to_item[future]
            try:
                open_ports = future.result()
                if open_ports:
                    for p in open_ports:
                        alive_items.append((ip, p))
                        print(f"    [√] 节点可用：{ip}:{p}")
                else:
                    # 如果这是来源带端口的条目，打印不可用；如果来源不带端口，打印不可用也是有意义
                    print(f"    [×] 节点不可用：{ip} （检测端口：{','.join(map(str, ports_checked))}）")
            except Exception as exc:
                print(f"    [!] 异常发生在检测 {ip}: {exc}")
    alive_ip_set.update(alive_items)
    print(f"\n[*] 可用性检测完成，共 {len(alive_ip_set)} 个可能可用 IP:port。\n")


# -------------------------------
# 函数：查询 IP 地理位置并写入文件（格式：ip:port#country）
# -------------------------------
def get_ip_location_and_write():
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            # 对 alive_ip_set 进行排序，先按 IP 字符串排序，再按端口
            for ip, port in sorted(alive_ip_set, key=lambda x: (x[0], x[1])):
                country = 'Unknown'
                try:
                    api_url = f'https://ipinfo.io/{ip}/json'
                    req = urllib.request.Request(api_url, headers={'User-Agent': USER_AGENT}, method='GET')
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read().decode('utf-8', errors='ignore'))
                        country = data.get('country', 'Unknown')
                except Exception:
                    pass
                f.write(f"{ip}:{port}#{country}-{port}\n")
        print(f"[*] 可用 IP 已写入文件：{OUTPUT_FILE}\n")
    except Exception as e:
        print(f"[!] 写入文件时出错：{e}")


# -------------------------------
# 主入口函数
# -------------------------------
if __name__ == '__main__':
    test_connectivity()
    fetch_and_extract_ips()
    filter_alive_ips()
    get_ip_location_and_write()
    print("[*] 脚本执行完毕。")
