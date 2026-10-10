# catch.py
import base64
import binascii
import csv
import json
import random
import subprocess
import time
from pathlib import Path
import requests

CACHE_FILE = Path.home() / ".netease_album_export" / "cookies.json"

# ========== 输出目录 ==========
EXPORT_ROOT = Path("导出数据")
ALBUM_TXT = EXPORT_ROOT / "专辑" / "文本"
ALBUM_CSV = EXPORT_ROOT / "专辑" / "表格"
SONG_TXT = EXPORT_ROOT / "歌曲与歌单" / "文本"
SONG_CSV = EXPORT_ROOT / "歌曲与歌单" / "表格"


def _ensure_dirs():
    for d in [ALBUM_TXT, ALBUM_CSV, SONG_TXT, SONG_CSV]:
        d.mkdir(parents=True, exist_ok=True)


# ========== 通用请求头 ==========

def _common_headers():
    return {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
        'Referer': 'https://music.163.com/',
    }


# ========== Cookie 缓存 ==========

def load_cached_cookies():
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and 'MUSIC_U' in data:
            print("[缓存] 加载 Cookie")
            return data
    except Exception as e:
        print(f"[缓存] 读取失败: {e}")
    return None


def save_cookies(cookie_dict):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cookie_dict, f, ensure_ascii=False, indent=2)
        print("[缓存] Cookie 已保存")
    except Exception as e:
        print(f"[缓存] 保存失败: {e}")


# ========== 浏览器路径查找 ==========

def _find_browser(browser_name):
    paths = {
        'chrome': [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        'edge': [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
    }
    for p in paths.get(browser_name, []):
        if Path(p).exists():
            return p
    return None


# ========== 辅助函数 ==========

def _is_process_running(proc_name):
    """检查进程是否在运行（psutil 优先，回退 tasklist）"""
    try:
        import psutil
        target = proc_name.lower()
        for p in psutil.process_iter(['name']):
            try:
                if (p.info.get('name') or '').lower() == target:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False
    except ImportError:
        try:
            result = subprocess.run(
                ['tasklist', '/FI', f'IMAGENAME eq {proc_name}', '/NH'],
                capture_output=True, text=True,
                encoding='gbk', errors='ignore',
                timeout=5
            )
            return proc_name.lower() in (result.stdout or '').lower()
        except Exception:
            return False


def _is_port_in_use(port):
    """检查端口是否被占用"""
    try:
        result = subprocess.run(
            ['netstat', '-ano'],
            capture_output=True, text=True,
            encoding='gbk', errors='ignore',
            timeout=5
        )
        return f':{port} ' in (result.stdout or '')
    except Exception:
        return False


def _find_free_port(start=9222, max_tries=20):
    """从 start 开始找一个空闲端口"""
    for p in range(start, start + max_tries):
        if not _is_port_in_use(p):
            return p
    return None


def _kill_proc(proc):
    """安全地关闭子进程"""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    except Exception:
        pass


# ========== 方式一：远程调试协议（复用日常配置目录） ==========

def try_remote_debugging(browser_name='chrome'):
    """
    独立目录 + 远程调试协议。
    启动前无条件杀掉同名浏览器进程（Chromium 的进程转发行为会吞掉调试参数）。
    """
    try:
        import websocket
    except ImportError:
        print("[浏览器] 未安装 websocket-client（pip install websocket-client）")
        return None

    browser_path = _find_browser(browser_name)
    if not browser_path:
        print(f"[浏览器] 未找到 {browser_name}")
        return None

    if browser_name == 'chrome':
        proc_name = 'chrome.exe'
        display_name = 'Chrome'
    elif browser_name == 'edge':
        proc_name = 'msedge.exe'
        display_name = 'Edge'
    else:
        print(f"[浏览器] 不支持 {browser_name}")
        return None

    # ========== ⭐ 无条件杀掉所有同名进程 ==========
    # Chromium 有"进程转发"行为：只要有同名进程在跑，
    # 新启动的命令会把参数转发过去然后自己退出（返回码 0）。
    # 所以必须先杀干净。
    print(f"\n[浏览器] 正在清理所有 {display_name} 进程...")
    killed_any = False
    for round_idx in range(5):
        try:
            result = subprocess.run(
                ['taskkill', '/F', '/IM', proc_name],
                capture_output=True, text=True,
                encoding='gbk', errors='ignore', timeout=10
            )
            # taskkill 成功时会输出 SUCCESS 或中文提示
            stdout = result.stdout or ''
            if '成功' in stdout or 'SUCCESS' in stdout.upper():
                killed_any = True
        except Exception:
            pass
        time.sleep(1.5)
        if not _is_process_running(proc_name):
            break
    else:
        print(f"[浏览器] ⚠️  {proc_name} 仍在运行")
        print(f"[浏览器] 请手动打开任务管理器，结束所有 {display_name} 进程后重试")
        return None

    if killed_any:
        print(f"[浏览器] 已清理 {display_name} 进程")
    else:
        print(f"[浏览器] 没有残留进程")
    time.sleep(2)  # 等端口和句柄释放

    # ========== 独立持久化目录 ==========
    debug_dir = str(Path.home() / ".netease_album_export" / f"{browser_name}_profile")
    Path(debug_dir).mkdir(parents=True, exist_ok=True)

    # 清理可能残留的锁文件
    for lock_name in ['SingletonLock', 'SingletonCookie', 'SingletonSocket']:
        p = Path(debug_dir) / lock_name
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    first_time = not (Path(debug_dir) / "Default" / "Cookies").exists()
    if first_time:
        print("\n" + "=" * 60)
        print(f"  ⚠️  首次使用 {display_name} 自动导入")
        print("=" * 60)
        print(f"  需要用 {display_name} 登录一次网易云音乐。")
        print(f"  登录页面可能加载较慢，请耐心等待。")
        print("=" * 60)
        print(f"  ⭐ 登录完成后【不要关闭浏览器】，脚本会自动检测")
        print("=" * 60)
        input("\n  按回车继续...")

    # ========== 找空闲端口 ==========
    port = _find_free_port(9222)
    if not port:
        print("[浏览器] 找不到空闲端口")
        return None

    print(f"\n[浏览器] 启动 {display_name} 调试实例...")
    print(f"[浏览器] 配置目录：{debug_dir}")
    print(f"[浏览器] 使用端口：{port}")

    proc = subprocess.Popen([
        browser_path,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={debug_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-features=TrackingProtection3pcd,PrivacySandboxSettings4,"
        "msTrackingPrevention,EdgeTrackingPrevention,"
        "msEdgeWelcomePage,msEdgeIdentityFeaturePromotion,"
        "SafeBrowsingEnhancedProtection,SafeBrowsing",
        "--ignore-certificate-errors",
        "https://music.163.com"
    ])

    # ========== 等端口 ==========
    print("[浏览器] 等待调试端口就绪...")
    port_ready = False
    for i in range(30):
        time.sleep(2)
        if proc.poll() is not None:
            print(f"[浏览器] {display_name} 已退出（返回码 {proc.returncode}）")
            print(f"[浏览器] 可能原因：")
            print(f"  1. 杀进程后还有残留（重启电脑试试）")
            print(f"  2. 配置目录损坏（删除 {debug_dir} 后重试）")
            return None
        try:
            requests.get(
                f"http://localhost:{port}/json",
                timeout=3,
                proxies={"http": None, "https": None}
            )
            port_ready = True
            break
        except Exception:
            if (i + 1) % 5 == 0:
                print(f"[浏览器] 第 {i+1} 次尝试，端口未就绪...")

    if not port_ready:
        print("[浏览器] 60 秒内端口未就绪")
        _kill_proc(proc)
        return None

    print("[浏览器] 调试端口已就绪")
    print()
    print("=" * 60)
    print(f"  ⭐ 请在 {display_name} 中登录网易云音乐")
    print(f"  ⭐ 登录完成后【不要关闭浏览器】，脚本会自动检测")
    print(f"  ⭐ 如果登录按钮点不动，直接在地址栏访问：")
    print(f"     https://music.163.com/#/login")
    print("=" * 60)
    print()

    # ========== 抓 Cookie ==========
    cookie_dict = None
    deadline = time.time() + 600
    last_status = None

    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"\n[浏览器] ⚠️  {display_name} 已被关闭")
            print("[浏览器] 登录后请不要手动关闭浏览器")
            break

        try:
            resp = requests.get(
                f"http://localhost:{port}/json",
                timeout=5,
                proxies={"http": None, "https": None}
            )
            tabs = resp.json()
            if not tabs:
                time.sleep(2)
                continue

            target_tab = None
            for tab in tabs:
                if 'music.163.com' in tab.get('url', ''):
                    target_tab = tab
                    break
            if not target_tab:
                target_tab = tabs[0]

            ws_url = target_tab['webSocketDebuggerUrl']
            ws = websocket.create_connection(ws_url, timeout=10)
            ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
            raw = ws.recv()
            ws.close()
            result = json.loads(raw)

            cookies = result.get('result', {}).get('cookies', [])
            temp = {
                c['name']: c['value']
                for c in cookies
                if 'music.163.com' in c.get('domain', '')
            }

            if 'MUSIC_U' in temp:
                cookie_dict = temp
                print(f"\n[浏览器] ✅ 检测到 MUSIC_U，共 {len(cookie_dict)} 个 Cookie")
                break

            if last_status != 'waiting':
                print("[浏览器] 等待登录中...（登录后请不要关闭浏览器）")
                last_status = 'waiting'
        except Exception as e:
            print(f"[浏览器] 检测中: {e}")

        time.sleep(2)

    _kill_proc(proc)
    print("[浏览器] 已关闭调试实例")

    if cookie_dict:
        return cookie_dict
    print("[浏览器] 未获取到 Cookie")
    return None

# ========== 方式二：Firefox 静态读取 ==========

def try_firefox_cookies():
    try:
        import rookiepy
    except ImportError:
        print("[Firefox] 未安装 rookiepy（pip install rookiepy）")
        return None

    try:
        print("[Firefox] 尝试读取 cookies.sqlite...")
        cookies = rookiepy.firefox(["music.163.com"])
        cookie_dict = {c['name']: c['value'] for c in cookies}
        if 'MUSIC_U' in cookie_dict:
            print("[Firefox] 成功")
            return cookie_dict
        print("[Firefox] 未找到 MUSIC_U，请确认已在 Firefox 登录网易云并关闭 Firefox")
    except Exception as e:
        print(f"[Firefox] 失败: {e}")
    return None


# ========== 方式三：rookiepy 静态读取 ==========

def try_rookiepy(browser_name='edge'):
    try:
        import rookiepy
    except ImportError:
        print("[自动] 未安装 rookiepy（pip install rookiepy）")
        return None

    try:
        func = getattr(rookiepy, browser_name)
        cookies = func(["music.163.com"])
        cookie_dict = {c['name']: c['value'] for c in cookies}
        if 'MUSIC_U' in cookie_dict:
            print(f"[自动] {browser_name} 成功")
            return cookie_dict
        print(f"[自动] {browser_name} 未找到 MUSIC_U")
    except Exception as e:
        print(f"[自动] {browser_name} 失败: {e}")
    return None


# ========== 方式四：手动粘贴 ==========

def manual_input():
    MANUAL_COOKIE_FILE = Path("netease_cookies.txt")
    if MANUAL_COOKIE_FILE.exists():
        content = MANUAL_COOKIE_FILE.read_text(encoding='utf-8').strip()
    else:
        print("\n请手动粘贴 Cookie")
        content = input("Cookie: ").strip()
    if not content:
        return None
    cookie_dict = {}
    for item in content.split(';'):
        item = item.strip()
        if '=' in item:
            k, v = item.split('=', 1)
            cookie_dict[k.strip()] = v.strip()
    if 'MUSIC_U' in cookie_dict:
        print("[手动] 成功")
        return cookie_dict
    print("[手动] 未找到 MUSIC_U")
    return None


# ========== 方式五：手机号 + 验证码登录 ==========

_NONCE = '0CoJUm6Qyw8W8jud'
_IV = '0102030405060708'
_PUB_KEY = '010001'
_MODULUS = (
    '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7'
    'b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280'
    '104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932'
    '575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b'
    '3ece0462db0a22b8e7'
)
_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'


def _aes_encrypt(text, key):
    from Crypto.Cipher import AES
    data = text.encode('utf-8')
    pad = 16 - len(data) % 16
    data = data + bytes([pad]) * pad
    cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, _IV.encode('utf-8'))
    encrypted = cipher.encrypt(data)
    return base64.b64encode(encrypted).decode('utf-8')


def _rsa_encrypt(text, pub_key, modulus):
    text = text[::-1]
    rs = pow(
        int(binascii.hexlify(text.encode('utf-8')), 16),
        int(pub_key, 16),
        int(modulus, 16),
    )
    return format(rs, 'x').zfill(256)


def _random_key(length=16):
    return ''.join(random.choice(_CHARS) for _ in range(length))


def _weapi_encrypt(data):
    text = json.dumps(data, ensure_ascii=False)
    sec_key = _random_key(16)
    enc_text = _aes_encrypt(_aes_encrypt(text, _NONCE), sec_key)
    enc_sec_key = _rsa_encrypt(sec_key, _PUB_KEY, _MODULUS)
    return {'params': enc_text, 'encSecKey': enc_sec_key}


def _send_captcha(session, phone, countrycode='86'):
    data = {'cellphone': phone, 'ctcode': countrycode}
    payload = _weapi_encrypt(data)
    try:
        resp = session.post(
            'https://music.163.com/weapi/sms/captcha/sent',
            data=payload, timeout=15,
            proxies={"http": None, "https": None}
        )
        result = resp.json()
    except Exception as e:
        print(f"[验证码] 请求失败: {e}")
        return False

    code = result.get('code')
    msg = result.get('message', '')
    print(f"[验证码] code={code} msg={msg}")

    if code == 200:
        return True
    if code == 400:
        print("[验证码] 手机号格式错误")
    elif code == 503:
        print("[验证码] 发送过于频繁，请稍后重试")
    return False


def _login_with_captcha(session, phone, captcha, countrycode='86'):
    data = {
        'phone': phone,
        'countrycode': countrycode,
        'captcha': captcha,
        'rememberLogin': 'true',
    }
    payload = _weapi_encrypt(data)
    try:
        resp = session.post(
            'https://music.163.com/weapi/login/cellphone',
            data=payload, timeout=15,
            proxies={"http": None, "https": None}
        )
        result = resp.json()
    except Exception as e:
        print(f"[登录] 请求失败: {e}")
        return None

    code = result.get('code')
    msg = result.get('message', '')
    print(f"[登录] code={code} msg={msg}")

    if code == 200:
        cookie_dict = session.cookies.get_dict()
        if 'MUSIC_U' in cookie_dict:
            return cookie_dict
        print(f"[登录] 缺少 MUSIC_U：{list(cookie_dict.keys())}")
    elif code == 502:
        print("[登录] 验证码错误或已过期")
    elif code == 501:
        print("[登录] 账号不存在")
    elif code == 400:
        print("[登录] 请求参数错误")
    return None


def try_phone_login():
    import sys as _sys
    try:
        import Crypto  # noqa
    except ImportError:
        print("[手机登录] 未安装 pycryptodome，正在自动安装...")
        try:
            subprocess.check_call([_sys.executable, '-m', 'pip', 'install', 'pycryptodome'])
            import Crypto  # noqa
        except Exception as e:
            print(f"[手机登录] 自动安装失败: {e}")
            print("[手机登录] 请手动执行: pip install pycryptodome")
            return None

    print("\n" + "=" * 60)
    print("  手机号 + 验证码登录")
    print("=" * 60)

    phone = input("请输入手机号（如 13800138000）: ").strip()
    if not phone:
        print("[手机登录] 未输入手机号")
        return None

    countrycode = input("国家码（直接回车默认 86）: ").strip() or '86'

    session = requests.Session()
    session.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
        'Referer': 'https://music.163.com/',
    })

    try:
        session.get('https://music.163.com/', timeout=15,
                    proxies={"http": None, "https": None})
    except Exception:
        pass

    print(f"\n[手机登录] 正在向 {phone} 发送验证码...")
    if not _send_captcha(session, phone, countrycode):
        print("[手机登录] 验证码发送失败")
        return None

    print("[手机登录] 验证码已发送，请查看手机短信\n")
    captcha = input("请输入收到的验证码: ").strip()
    if not captcha:
        print("[手机登录] 未输入验证码")
        return None

    print("[手机登录] 正在登录...")
    cookie_dict = _login_with_captcha(session, phone, captcha, countrycode)

    if cookie_dict:
        print("[手机登录] 登录成功！")
        return cookie_dict

    print("[手机登录] 登录失败")
    return None


# ========== 组合入口（catch.py 独立运行时用） ==========

def get_netease_cookie():
    cached = load_cached_cookies()
    if cached:
        return cached

    result = try_remote_debugging("chrome")
    if result:
        save_cookies(result)
        return result

    result = manual_input()
    if result:
        save_cookies(result)
        return result

    return None


# ========== 获取 UID ==========

def get_uid(cookie_dict):
    try:
        resp = requests.get(
            'https://music.163.com/api/nuser/account/get',
            headers=_common_headers(),
            cookies=cookie_dict, timeout=15,
        )
        data = resp.json()
        account = data.get('account') or {}
        profile = data.get('profile') or {}
        uid = account.get('id')
        nickname = profile.get('nickname', '')
        if uid:
            print(f"[账户] UID = {uid}，昵称：{nickname}")
        else:
            print("[账户] 未能获取 UID，Cookie 可能已失效")
        return uid
    except Exception as e:
        print(f"[账户] 获取 UID 失败: {e}")
        return None


# ========== 写出工具 ==========

def _write_albums(albums):
    _ensure_dirs()

    txt_path = ALBUM_TXT / "albums.txt"
    with open(txt_path, 'w', encoding='utf-8') as f:
        for a in albums:
            f.write(f"{a['album']} - {a['artist']}\n")

    csv_path = ALBUM_CSV / "albums.csv"
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(["专辑名", "音乐人"])
        for a in albums:
            w.writerow([a['album'], a['artist']])

    print(f"[写出] {txt_path}")
    print(f"[写出] {csv_path}")


def _write_songs(songs, base_name):
    _ensure_dirs()

    txt_path = SONG_TXT / f"{base_name}.txt"
    with open(txt_path, 'w', encoding='utf-8') as f:
        for s in songs:
            f.write(f"{s['name']} - {s['artist']}  [{s['album']}]\n")

    csv_path = SONG_CSV / f"{base_name}.csv"
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(["歌曲名", "音乐人", "专辑"])
        for s in songs:
            w.writerow([s['name'], s['artist'], s['album']])

    print(f"[写出] {txt_path}")
    print(f"[写出] {csv_path}")


def _write_playlists(playlists_data, base_name):
    _ensure_dirs()

    txt_path = SONG_TXT / f"{base_name}.txt"
    with open(txt_path, 'w', encoding='utf-8') as f:
        for pl in playlists_data:
            f.write(pl["title"] + "\n")
            for t in pl["tracks"]:
                f.write(f"  - {t['name']} - {t['artist']}  [{t['album']}]\n")
            f.write("\n")

    csv_path = SONG_CSV / f"{base_name}.csv"
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(["歌单名", "歌曲名", "音乐人", "专辑"])
        for pl in playlists_data:
            for t in pl["tracks"]:
                w.writerow([pl["title"], t['name'], t['artist'], t['album']])

    print(f"[写出] {txt_path}")
    print(f"[写出] {csv_path}")


# ========== 导出专辑 ==========

def export_albums(cookie_dict):
    if not cookie_dict:
        return []
    limit = 100
    offset = 0
    albums = []
    headers = _common_headers()
    while True:
        url = f'https://music.163.com/api/album/sublist?limit={limit}&offset={offset}'
        try:
            resp = requests.get(url, headers=headers, cookies=cookie_dict, timeout=15)
            data = resp.json()
        except Exception as e:
            print(f'[专辑] 请求出错: {e}')
            break
        items = data.get('data', [])
        if not items:
            print('[专辑] 没有更多专辑了。')
            break
        for a in items:
            artists = ' / '.join([x['name'] for x in a.get('artists', [])])
            albums.append({"album": a['name'], "artist": artists})
        print(f'[专辑] 已抓取 {len(albums)} 张...')
        offset += limit
        time.sleep(0.8)
    print(f'[专辑] 完成！共 {len(albums)} 张')
    return albums


# ========== 导出歌单 ==========

def export_playlists(cookie_dict, uid, fetch_tracks=True):
    if not cookie_dict or not uid:
        return {"created": [], "subscribed": []}

    headers = _common_headers()
    all_playlists = []
    offset = 0
    limit = 1000

    while True:
        url = f'https://music.163.com/api/user/playlist?uid={uid}&limit={limit}&offset={offset}'
        try:
            resp = requests.get(url, headers=headers, cookies=cookie_dict, timeout=15)
            data = resp.json()
        except Exception as e:
            print(f'[歌单] 请求出错: {e}')
            break
        playlists = data.get('playlist', [])
        if not playlists:
            break
        all_playlists.extend(playlists)
        if len(playlists) < limit:
            break
        offset += limit
        time.sleep(0.8)

    created = []
    subscribed = []

    for idx, pl in enumerate(all_playlists, 1):
        name = pl.get('name', '未命名')
        count = pl.get('trackCount', 0)
        creator = (pl.get('creator') or {}).get('nickname', '')
        desc = (pl.get('description') or '').strip().replace('\n', ' ')
        pid = pl.get('id')

        if pl.get('specialType') == 5 or name == '我喜欢的音乐':
            continue

        is_subscribed = pl.get('subscribed', False)
        title = f"{name}（{count} 首）"
        if is_subscribed:
            title += f" — 来自 {creator}"
        if desc:
            title += f"  |  {desc[:80]}"

        tracks = []
        if fetch_tracks and pid:
            tracks = _fetch_playlist_tracks(cookie_dict, pid)
            print(f'[歌单] [{idx}/{len(all_playlists)}] {name} 抓取到 {len(tracks)} 首')
            time.sleep(0.4)

        entry = {"title": title, "tracks": tracks}
        if is_subscribed:
            subscribed.append(entry)
        else:
            created.append(entry)

    print(f'[歌单] 创建 {len(created)} 个，收藏 {len(subscribed)} 个')
    return {"created": created, "subscribed": subscribed}


def _fetch_playlist_tracks(cookie_dict, playlist_id):
    headers = _common_headers()

    try:
        resp = requests.get(
            f'https://music.163.com/api/v6/playlist/detail?id={playlist_id}&n=1000',
            headers=headers, cookies=cookie_dict, timeout=15,
        )
        detail = resp.json()
    except Exception as e:
        print(f'  [子请求] 歌单详情失败: {e}')
        return []

    playlist = detail.get('playlist') or {}
    track_ids = playlist.get('trackIds') or []
    if not track_ids:
        return []

    ids = [t.get('id') for t in track_ids if t.get('id')]

    tracks = []
    batch_size = 100
    total_batches = (len(ids) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        batch = ids[batch_idx * batch_size:(batch_idx + 1) * batch_size]
        c_param = json.dumps([{"id": sid} for sid in batch], ensure_ascii=False)
        try:
            r = requests.get(
                'https://music.163.com/api/v3/song/detail',
                headers=headers, cookies=cookie_dict,
                params={'c': c_param}, timeout=20,
            )
            songs = (r.json().get('songs') or [])
            for s in songs:
                name = s.get('name', '未知')
                ar = s.get('ar') or s.get('artists') or []
                artists = ' / '.join(a.get('name', '') for a in ar)
                al = s.get('al') or s.get('album') or {}
                album = al.get('name', '')
                tracks.append({"name": name, "artist": artists, "album": album})
        except Exception as e:
            print(f'  [子请求] 第 {batch_idx + 1}/{total_batches} 批失败: {e}')
        time.sleep(0.4)

    return tracks


# ========== 导出喜欢的音乐 ==========

def export_liked_songs(cookie_dict, uid):
    if not cookie_dict or not uid:
        print('[喜欢] 缺少 Cookie 或 UID，跳过')
        return []

    headers = _common_headers()

    print('[喜欢] 正在获取红心歌曲 ID 列表...')
    try:
        resp = requests.get(
            f'https://music.163.com/api/song/like/get?uid={uid}',
            headers=headers, cookies=cookie_dict, timeout=15,
        )
        data = resp.json()
    except Exception as e:
        print(f'[喜欢] 获取 ID 列表失败: {e}')
        return []

    song_ids = data.get('ids') or []
    if not song_ids:
        print('[喜欢] 未获取到任何红心歌曲，Cookie 可能已失效')
        return []
    print(f'[喜欢] 共找到 {len(song_ids)} 首红心歌曲')

    songs = []
    batch_size = 100
    total_batches = (len(song_ids) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        batch = song_ids[batch_idx * batch_size:(batch_idx + 1) * batch_size]
        c_param = json.dumps([{"id": sid} for sid in batch], ensure_ascii=False)
        try:
            resp = requests.get(
                'https://music.163.com/api/v3/song/detail',
                headers=headers, cookies=cookie_dict,
                params={'c': c_param}, timeout=20,
            )
            detail = resp.json()
            items = detail.get('songs') or []
            for s in items:
                name = s.get('name', '未知')
                ar = s.get('ar') or s.get('artists') or []
                artists = ' / '.join(a.get('name', '') for a in ar)
                al = s.get('al') or s.get('album') or {}
                album = al.get('name', '')
                songs.append({"name": name, "artist": artists, "album": album})
            print(f'[喜欢] 第 {batch_idx + 1}/{total_batches} 批，累计 {len(songs)} 首')
        except Exception as e:
            print(f'[喜欢] 第 {batch_idx + 1} 批失败: {e}')
        time.sleep(0.5)

    print(f'[喜欢] 完成！共 {len(songs)} 首')
    return songs


# ========== 主流程（catch.py 独立运行时用） ==========

def main():
    print("=" * 60)
    print("  网易云音乐数据导出")
    print("=" * 60)

    cookie = get_netease_cookie()
    if not cookie:
        print("程序终止：未能获取 Cookie")
        return

    uid = get_uid(cookie)
    if not uid:
        print("程序终止：未能获取 UID")
        return

    _ensure_dirs()

    print("\n[1/3] 导出收藏的专辑...")
    albums = export_albums(cookie)
    _write_albums(albums)

    print("\n[2/3] 导出所有歌单（创建 + 收藏）...")
    playlists = export_playlists(cookie, uid)
    _write_playlists(playlists["created"], "playlists_created")
    _write_playlists(playlists["subscribed"], "playlists_subscribed")

    print("\n[3/3] 导出喜欢的音乐...")
    liked = export_liked_songs(cookie, uid)
    _write_songs(liked, "liked_songs")

    print("\n" + "=" * 60)
    print("  全部导出完成")
    print(f"  输出根目录：{EXPORT_ROOT.absolute()}")
    print("=" * 60)


if __name__ == "__main__":
    main()