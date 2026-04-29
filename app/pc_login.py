"""
云电脑首页登录脚本。
"""

import atexit  # 新增导入 atexit 模块
import datetime
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional

import ddddocr
import requests
from DrissionPage import ChromiumOptions, ChromiumPage

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

LOGIN_URL = "https://pc.ctyun.cn/#/login"
DESKTOP_URL = "https://pc.ctyun.cn/#/desktop-list"
DESKTOP_DETAIL_URL_KEY = "/desktop?id="
HANG_SECONDS = 80 * 60


def init_browser_options(running_in_docker: bool) -> ChromiumOptions:
    """初始化 Chromium 启动参数。"""
    options = ChromiumOptions()
    options.set_argument("--no-sandbox")
    options.set_argument("--disable-gpu")
    options.set_argument("--disable-dev-shm-usage")
    options.set_argument("--window-size=1920,1080")
    if running_in_docker:
        options.headless()
    return options


def get_auth_data_file(username: str, running_in_docker: bool) -> str:
    """构造账号专属 authData 文件路径。"""
    if running_in_docker:
        return f"/app/data/ctyun_authData_{username}_.json"
    return f"./ctyun_authData_{username}_.json"


def save_auth_data(page: ChromiumPage, file_path: str) -> None:
    """保存 localStorage.authData 到本地。"""
    try:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        auth_data = read_auth_data(page)
        if not auth_data:
            print(f"[-] 未获取到 authData，跳过保存: {file_path}")
            return
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(auth_data, f, ensure_ascii=False, indent=4)
        print("[*] authData 已保存")
    except Exception as e:
        print(f"[!] 保存 authData 失败: {e}")


def load_auth_data_from_file(file_path: str) -> dict:
    """从本地读取 authData。"""
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            auth_data = json.load(f)
        if isinstance(auth_data, dict):
            return auth_data
        return {}
    except Exception as e:
        print(f"[!] 读取 authData 文件失败: {e}")
        return {}


def get_device_code(username: str, running_in_docker: bool) -> str:
    """读取或输入 web_device_code。"""
    env_device = os.getenv("DEVICECODE")
    if env_device:
        return env_device.strip()

    if os.getenv("RUNNING_IN_DOCKER") == "true":
        file_path = f"/app/data/.devicecode_{username}"
    else:
        file_path = f"./.devicecode_{username}"
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            value = f.read().strip()
            if value:
                return value
    device_code = ""
    while not device_code:
        device_code = input("请输入 web_device_code: ").strip()
        if not device_code:
            print("[-] web_device_code 不能为空，请重新输入。")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(device_code)
    print(f"[*] web_device_code 已保存: {file_path}")
    return device_code


def set_web_device_code(page: ChromiumPage, device_code: str) -> None:
    """写入 localStorage.web_device_code。"""
    script = f"localStorage.setItem('web_device_code', {json.dumps(device_code)});"
    page.run_js(script)
    print("[*] 已写入 localStorage.web_device_code")


def get_auth_expired_at_ms(hours: int = 72) -> int:
    """生成 authExpiredAt 时间戳（毫秒）。"""
    return int((time.time() + hours * 3600) * 1000)


def inject_auth_data_if_exists(page: ChromiumPage, auth_data_file: str) -> None:
    """若本地 authData 文件存在则注入 localStorage.authData。"""
    auth_data = load_auth_data_from_file(auth_data_file)
    if not auth_data:
        return
    auth_expired_at = str(get_auth_expired_at_ms(hours=72))
    page.run_js(
        f"localStorage.setItem('authExpiredAt', {json.dumps(auth_expired_at)});"
    )
    auth_data_text = json.dumps(auth_data, ensure_ascii=False, separators=(",", ":"))
    page.run_js(f"localStorage.setItem('authData', {json.dumps(auth_data_text)});")
    print(f"[*] 已注入 localStorage.authData: {auth_data_file}")


def inject_local_storage_session(
    page: ChromiumPage, device_code: str, auth_data_file: str
) -> None:
    """注入 web_device_code、authData"""
    set_web_device_code(page, device_code)
    inject_auth_data_if_exists(page, auth_data_file)


def first_available(page: ChromiumPage, selectors: list[str], timeout: float = 2):
    """按顺序查找首个可用元素。"""
    for selector in selectors:
        ele = page.ele(selector, timeout=timeout)
        if ele:
            return ele
    return None


def fill_credentials(page: ChromiumPage, username: str, password: str) -> None:
    """填写账号与密码。"""
    account_input = first_available(
        page,
        [
            'css:input[placeholder*="手机号"]',
            'css:input[placeholder*="账号"]',
            'css:input[type="text"]',
        ],
        timeout=60,
    )
    password_input = first_available(
        page,
        [
            'css:input[placeholder*="密码"]',
            'css:input[type="password"]',
        ],
        timeout=10,
    )

    if not account_input or not password_input:
        raise RuntimeError("未找到账号或密码输入框。")

    account_input.clear()
    account_input.input(username)
    password_input.clear()
    password_input.input(password)


def fill_captcha_if_possible(page: ChromiumPage) -> bool:
    """识别并填写图形验证码。"""
    captcha_img = page.ele("css:img.code-img", timeout=2)
    captcha_input = first_available(
        page,
        [
            'css:input[placeholder*="请输入验证码"]',
            'xpath://input[contains(@placeholder,"请输入验证码")]',
        ],
        timeout=1,
    )

    if not captcha_img or not captcha_input:
        return False

    try:
        image_bytes = captcha_img.get_screenshot(as_bytes=True)
        captcha_code = get_bytes_numeric_captcha(image_bytes).strip()
        print(f"[*] 图形验证码识别结果: {captcha_code}")
        if not captcha_code:
            return False
        time.sleep(1)
        captcha_input.clear()
        captcha_input.input(captcha_code)
        print(f"[*] 已填写图形验证码: {captcha_code}")
        time.sleep(2)
        return True
    except Exception as e:
        print(f"[!] 图形验证码识别失败: {e}")
        return False


def click_login_button(page: ChromiumPage) -> None:
    """点击登录按钮。"""
    login_btn = page.ele("css:button.btn-submit-pc", timeout=5)
    if not login_btn:
        raise RuntimeError("未找到登录按钮 button.btn-submit-pc。")
    login_btn.click()


def get_latest_toast(page: ChromiumPage, timeout: float = 4) -> str:
    """读取最新 toast 文本。"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        toast_eles = page.eles("css:.el-message__content")
        text_list = [
            ele.text.strip()
            for ele in toast_eles
            if ele and ele.text and ele.text.strip()
        ]
        if text_list:
            return text_list[-1]
        time.sleep(0.2)
    return ""


def refresh_captcha_image(page: ChromiumPage) -> None:
    """点击验证码图片以刷新验证码。"""
    captcha_img = page.ele("css:img.code-img", timeout=1)
    if captcha_img:
        captcha_img.click()


def read_auth_data(page: ChromiumPage) -> dict:
    """读取 localStorage.authData。"""
    raw_data = page.run_js("return localStorage.getItem('authData');")
    if not raw_data:
        return {}
    if isinstance(raw_data, dict):
        return raw_data
    if isinstance(raw_data, str):
        try:
            return json.loads(raw_data)
        except json.JSONDecodeError:
            return {}
    return {}


def is_login_success(page: ChromiumPage) -> bool:
    """判断是否已登录成功。"""
    if DESKTOP_URL in page.url or "/desktop-list" in page.url:
        return True
    if "/login" in page.url:
        return False
    time.sleep(1)
    auth_data = read_auth_data(page)
    return bool(auth_data.get("logined"))


def wait_desktop_list_refresh_done(page: ChromiumPage, timeout: int = 60) -> None:
    """等待 desktop-list 刷新动画结束。"""
    end_time = time.time() + timeout
    seen_loading = False
    while time.time() < end_time:
        current_url = page.url or ""
        date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\r[*] {date} 页面状态检测中...", end="")
        if "/login" in current_url or DESKTOP_DETAIL_URL_KEY in current_url:
            return

        loading_ele = page.ele("css:.rotate-animtion", timeout=0.3)
        if loading_ele:
            seen_loading = True
            time.sleep(0.2)
            continue

        if seen_loading:
            # 连续未检测到刷新动画，视为加载完成
            time.sleep(0.8)
            if not page.ele("css:.rotate-animtion", timeout=0.2):
                return

        # 刷新动画可能很快结束，出现关键元素时也可提前退出
        if page.ele("css:div.empty-desc", timeout=0.2):
            return
        if page.ele("css:div.desktopcom-enter", timeout=0.2):
            return
        time.sleep(1)
    print("\r[*] desktop-list 刷新超时。")


def get_desktop_state(page: ChromiumPage) -> str:
    """识别 desktop-list 的状态。"""
    current_url = page.url or ""
    if DESKTOP_DETAIL_URL_KEY in current_url:
        return "desktop_entered_auto"
    if "/login" in current_url:
        return "auth_expired"

    empty_desc = page.ele("css:div.empty-desc", timeout=0.5)
    if empty_desc:
        return "no_desktop"

    enter_buttons = page.eles("css:div.desktopcom-enter")
    has_cloud_pc = False
    has_cloud_phone = False
    for btn in enter_buttons:
        text = (btn.text or "").strip()
        if "进入AI云电脑" in text:
            has_cloud_pc = True
        if "进入AI云手机" in text:
            has_cloud_phone = True

    if has_cloud_pc:
        return "has_pc_button"
    if has_cloud_phone:
        return "only_phone"
    return "unknown"


def click_enter_ai_pc(page: ChromiumPage) -> bool:
    """点击“进入AI云电脑”按钮。"""
    enter_buttons = page.eles("css:div.desktopcom-enter")
    for btn in enter_buttons:
        text = (btn.text or "").strip()
        if "进入AI云电脑" in text:
            btn.click()
            return True
    return False


def wait_desktop_opened(page: ChromiumPage, timeout: int = 270) -> bool:
    """等待进入云电脑页面。"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        current_url = page.url or ""
        if DESKTOP_DETAIL_URL_KEY in current_url:
            return True
        if "/login" in current_url:
            return False
        time.sleep(0.5)
    print("[*] 进入云电脑超时。")
    return False


def open_points_center_and_print(page: ChromiumPage, timeout: int = 60) -> None:
    """打开积分中心并输出积分详情。"""
    try:
        locator = "xpath://span[contains(string(), '积分中心')]"
        target_element = page.ele(locator, timeout=120)

        if not target_element:
            print("\r[-] 未找到积分中心入口。")
            return

        clicked = target_element.click(by_js=True)
        if not clicked:
            print("\r[-] 积分中心入口点击失败。")
            return
        time.sleep(5)
        end_time = time.time() + timeout
        while time.time() < end_time:
            if page.ele('css:iframe[src*="points.html"]', timeout=0.5):
                break
            time.sleep(0.3)

        iframe_ele = page.ele('css:iframe[src*="points.html"]', timeout=30)
        if not iframe_ele:
            print("\r[-] 未找到积分中心 iframe。")
            return

        frame = page.get_frame(iframe_ele)
        if not frame:
            print("\r[-] 无法切换到积分中心 iframe。")
            return

        time.sleep(5)
        general_points = ""
        try:
            root_element = frame.ele("tag:div@class:points-list", timeout=60)
        except Exception:
            print("[*] 积分中心页面加载过久")

        if root_element:
            # @@ 表示同时满足多个属性匹配，定位同时拥有 flex 和 flex-column 类的 div 区块
            block_elements = root_element.eles("tag:div@@class:flex@@class:flex-column")

            for block in block_elements:
                title_element = block.ele("tag:p@class:text-title")
                desc_element = block.ele("tag:p@class:text-desc")

                # 安全提取文本内容，避免因元素不存在而引发 AttributeError
                value_text = title_element.text.strip() if title_element else ""
                name_text = desc_element.text.strip() if desc_element else ""

                # 执行匹配逻辑：精确匹配，或包含“通用积分”且排除“云智手机”
                if name_text == "通用积分" or (
                    "通用积分" in name_text and "云智手机" not in name_text
                ):
                    general_points = value_text
                    break

        if not general_points:
            print("\r[-] 未读取到通用积分。")
            return

        print(f"\r[*] 目前积分: {general_points}")
    except Exception as e:
        print(f"[-] 无法获取积分中心数据：{e}")


def wait_for_points_with_points(
    page: ChromiumPage, total_seconds: int = HANG_SECONDS, step: int = 10
) -> None:
    """进入云电脑后挂机等待积分，结束前打印积分详情。"""
    print("[*] 已进入云电脑")
    remaining = total_seconds
    # 超时时间
    max_time = 360
    refresh_retry_count_max = 13
    last_progress = 0
    packet_retry_count = 0
    refresh_retry_count = 0
    last_progress_update_time = time.time()
    url = "https://desk.ctyun.cn/selforder/api/marketing/userPoints/getTaskList"
    # 初始状态开启监听和界面
    page.listen.start(url)
    open_points_center_and_print(page)
    packet = page.listen.wait(timeout=20)
    while remaining > 0:
        # 开始挂机，获取积分中心数据，然后无限循环，并获取网络数据包判断是否完成挂机
        current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not packet:
            packet_retry_count += 1
            print(
                f"\r[-] {current_time_str} 未捕获到积分数据包，正在重试 ({packet_retry_count}/6)"
            )
            time.sleep(10)

            if packet_retry_count >= 6:
                print(f"[-] {current_time_str} 连续 6 次未捕获到数据包，程序终止。")
                sys.exit(1)

            page.refresh()
            time.sleep(5)
            page.listen.start(url)
            open_points_center_and_print(page)
            packet = page.listen.wait(timeout=20)
            continue

        # 成功捕获到包，清零数据包重试计数器
        packet_retry_count = 0
        headers = packet.request.headers
        current_progress = fetch_current_progress(url, headers)

        if current_progress is not None and current_progress > 0:
            print(
                f"\r[-] {current_time_str} 挂机剩余 {60 - (current_progress // 60)} 分钟。",
                end="",
            )
            # 进度发生实际变化
            if current_progress != last_progress:
                print(
                    f"\r[-] {current_time_str} 进度更新，目前已挂机 {current_progress // 60} 分钟。"
                )
                last_progress = current_progress
                last_progress_update_time = time.time()
            # 3600 代表任务完成
            if current_progress >= 3600:
                print(f"\r[-] {current_time_str} 挂机任务完成。")
                sys.exit(0)

        time_since_last_update = time.time() - last_progress_update_time

        if time_since_last_update >= max_time:
            refresh_retry_count += 1
            print(f"\n[-] {current_time_str} 刷新页面 ({refresh_retry_count}/13)")

            if refresh_retry_count >= refresh_retry_count_max:
                print(f"[-] {current_time_str} 刷新页面重试次数达到上限，程序终止。")
                sys.exit(1)

            # 刷新页面
            page.refresh()
            time.sleep(5)

            # 刷新页面后，必须重置最后更新时间戳，避免下一轮循环直接再次触发刷新
            last_progress_update_time = time.time()
            continue

        time.sleep(step)
        remaining -= step
    print("\r[*] 挂机等待完成。")


def fetch_current_progress(url: str, headers: Dict[str, str]) -> int:
    """
    向指定的 URL 发起 GET 请求，并直接解析提取 currentProgress 的值。
    Args:
        url (str): 接口的目标 URL。
        headers (Dict[str, str]): 请求头字典。

    Returns:
        Optional[Any]: 成功提取到进度值则返回该值；如果请求失败或数据不存在则返回 None。
    """
    try:
        for k in list(headers.keys()):
            if str(k).startswith(":"):
                del headers[k]

        response = requests.get(url, headers=headers, timeout=10)

        response.raise_for_status()

        data = response.json()
        task_list = data.get("data")

        for task in task_list:
            if task.get("taskDefName") == "使用1小时":
                return task.get("currentProgress")
        return 0

    except (requests.RequestException, ValueError) as error:
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{current_time}] 获取或解析数据失败: {error}")
        return 0


def execute_login(
    page: ChromiumPage,
    username: str,
    password: str,
    max_retries: int = 6,
) -> bool:
    """执行云电脑登录流程。"""
    # 最大重试次数设置
    for attempt_ in range(1, max_retries + 1):
        try:
            page.get(LOGIN_URL)

            # 等待页面开始加载，如果 30 秒未响应，DrissionPage 可能会抛出异常或后续操作失败
            is_loaded = page.wait.doc_loaded(timeout=30)

            if not is_loaded:
                raise TimeoutError("[*] 等待页面加载响应超时 (30秒)")

            # 填写账号密码
            fill_credentials(page, username, password)
            break

        except Exception:
            # save_screenshot(page)
            if attempt_ < max_retries:
                print("[*] 等待 3 秒后进行下一次进入...")
                time.sleep(3)
            else:
                print(f"[-] 已达到最大重试次数 ({max_retries} 次)，网页加载失败。")
                sys.exit(1)

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[*] 登录尝试 {attempt}/{max_retries}")
            if is_login_success(page):
                return True

            fill_captcha_if_possible(page)
            click_login_button(page)

            toast_text = get_latest_toast(page, timeout=20)
            if toast_text:
                print(f"[*] 登录提示: {toast_text}")

            if "用户名或密码错误" in toast_text:
                return False
            if "图形验证码错误" in toast_text:
                refresh_captcha_image(page)
                continue
            if "请输入图形验证码" in toast_text:
                continue

            if is_login_success(page):
                return True
        except Exception:
            if attempt < max_retries:
                print("[*] 等待 3 秒后进行下一次重试登录...")
                time.sleep(3)
            else:
                print(f"[-] 已达到最大重试次数 ({max_retries} 次)")
                sys.exit(1)

    return False


class NumericOcrSolver:
    """单例数字 OCR 识别器。"""

    _instance: Optional["NumericOcrSolver"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "NumericOcrSolver":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_engine()
        return cls._instance

    def _init_engine(self) -> None:
        self.ocr = ddddocr.DdddOcr(show_ad=False)
        self.ocr.set_ranges(0)

    def solve(self, image_data: bytes) -> str:
        try:
            return self.ocr.classification(image_data)
        except Exception:
            return ""


def get_bytes_numeric_captcha(image_bytes: bytes) -> str:
    solver = NumericOcrSolver()
    return solver.solve(image_bytes)


def main() -> None:
    username = os.getenv("APP_USER")
    password = os.getenv("APP_PASSWORD")
    running_in_docker = os.getenv("RUNNING_IN_DOCKER") == "true"

    if not username or not password:
        print("[!] 缺少环境变量 APP_USER 或 APP_PASSWORD。")
        sys.exit(1)

    auth_data_file = get_auth_data_file(username, running_in_docker)
    device_code = get_device_code(username, running_in_docker)

    options = init_browser_options(running_in_docker)
    page = ChromiumPage(addr_or_opts=options)
    atexit.register(page.quit)

    try:
        # 先注入本地会话，再进入 desktop-list 判断状态
        page.get(LOGIN_URL)
        inject_local_storage_session(page, device_code, auth_data_file)
        page.refresh()
        time.sleep(2)

        relogin_attempts = 0
        max_relogin_attempts = 3
        unknown_attempts = 0
        desktop_opened = False

        while True:
            page.get(DESKTOP_URL)
            time.sleep(1)
            wait_desktop_list_refresh_done(page, timeout=60)
            state = get_desktop_state(page)
            print(f"\r[*] desktop-list 状态: {state}")

            if state == "auth_expired" or state == "unknown":
                if relogin_attempts >= max_relogin_attempts:
                    print("[!] 重登次数已达上限。")
                    sys.exit(1)
                relogin_attempts += 1
                print(
                    f"[*] 检测到未登录或登录态过期，开始账号密码重登 ({relogin_attempts}/{max_relogin_attempts})"
                )
                if not execute_login(page, username, password):
                    print("[!] 重新登录失败。")
                    sys.exit(1)
                save_auth_data(page, auth_data_file)
                continue

            if state == "no_desktop":
                print("[*] 当前账号无云电脑资源，任务结束。")
                sys.exit(0)

            if state == "only_phone":
                print("[*] 当前账号仅有云手机资源，任务结束。")
                sys.exit(0)

            if state == "has_pc_button":
                print("[*] 检测到“进入AI云电脑”按钮，准备进入云电脑。")
                if not click_enter_ai_pc(page):
                    print("[!] 未能点击“进入AI云电脑”按钮。")
                    continue
                if not wait_desktop_opened(page, timeout=240):
                    print("[!] 点击后未进入云电脑页面。")
                    continue

                desktop_opened = True
                break

            if state == "desktop_entered_auto":
                print("[*] 已自动进入云电脑页面。")
                desktop_opened = True
                break

            unknown_attempts += 1
            if unknown_attempts >= 3:
                print("[!] 无法识别 desktop-list 页面状态，任务结束。")
                sys.exit(1)
            print(f"[-] 未识别到明确状态，重试中 ({unknown_attempts}/3)")
            time.sleep(2)

        if not desktop_opened:
            print("[!] 未进入云电脑页面。")
            sys.exit(1)

        auth_data = read_auth_data(page)
        mobile = auth_data.get("mobilephone") if auth_data else None
        if mobile:
            print(f"[*] 登录成功账号: {mobile}")
        else:
            print("[-] 登录成功，但未能读取 authData.mobilephone。")

        wait_for_points_with_points(page, HANG_SECONDS)
        page.quit()

    except Exception as e:
        # save_screenshot(page)
        print(f"[!] 执行异常: {e}")
        sys.exit(1)


def save_screenshot(page: ChromiumPage) -> None:
    file_name = f"{os.getenv('APP_USER')}_{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    if os.getenv("RUNNING_IN_DOCKER") == "true":
        path = "/app/data"
    else:
        path = "./"
    page.get_screenshot(path=path, name=file_name, full_page=True)


if __name__ == "__main__":
    print("[*] 开始进行云电脑挂机")
    main()
