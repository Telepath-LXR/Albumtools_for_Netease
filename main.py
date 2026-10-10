# main.py
import sys
import traceback
from pathlib import Path

from catch import (
    load_cached_cookies,
    save_cookies,
    try_phone_login,
    try_remote_debugging,
    manual_input,
    get_uid,
    export_albums,
    export_playlists,
    export_liked_songs,
    _write_albums,
    _write_songs,
    _write_playlists,
    _ensure_dirs,
    EXPORT_ROOT,
)

import generate_review as gr


# ============================================================
#  全局状态
# ============================================================

STATE = {
    "cookie": None,
    "uid": None,
    "data_exported": False,
}


# ============================================================
#  步骤：获取 Cookie
# ============================================================

def step_get_cookie():
    cookie = load_cached_cookies()
    if cookie:
        print("[缓存] 检测到本地 Cookie 缓存")
        choice = input("是否使用缓存？(Y/n): ").strip().lower()
        if choice == 'n':
            cookie = None

    if not cookie:
        print("\n请选择获取 Cookie 的方式：")
        print("  1. 手机号 + 验证码登录（⚠️ 有封号风险，建议用小号）")
        print("  2. Chrome 自动导入（复用日常登录，无需重新登录）")
        print("  3. Edge   自动导入（复用日常登录，无需重新登录）")
        print("  4. 手动粘贴 Cookie")
        print("  0. 返回主菜单")
        choice = input("请输入编号 [1]: ").strip() or "1"

        if choice == "0":
            print("[返回] 取消获取 Cookie。")
            return False
        elif choice == "1":
            cookie = try_phone_login()
        elif choice == "2":
            cookie = try_remote_debugging("chrome")
        elif choice == "3":
            cookie = try_remote_debugging("edge")
        elif choice == "4":
            cookie = manual_input()
        else:
            print("[错误] 无效选项。")
            return False

        if cookie:
            save_cookies(cookie)

    if not cookie:
        print("[失败] 未获取到 Cookie。")
        return False

    uid = get_uid(cookie)
    if not uid:
        print("[失败] Cookie 无效或已过期，无法获取 UID。")
        return False

    STATE["cookie"] = cookie
    STATE["uid"] = uid
    print("[完成] Cookie 获取成功。")
    return True


# ============================================================
#  步骤：导出所有数据
# ============================================================

def step_export_data():
    cookie = STATE["cookie"]
    uid = STATE["uid"]
    if not cookie or not uid:
        print("[错误] 请先获取 Cookie。")
        return False

    try:
        _ensure_dirs()

        print("\n" + "=" * 60)
        print("  开始导出数据")
        print("=" * 60)

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

        STATE["data_exported"] = True

        print("\n" + "=" * 60)
        print("  数据导出完成")
        print(f"  输出根目录：{EXPORT_ROOT.absolute()}")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n[错误] 导出过程中出现异常：{e}")
        traceback.print_exc()
        return False


# ============================================================
#  步骤：生成 AI 点评
# ============================================================

def step_generate_review():
    if not STATE["data_exported"] and not EXPORT_ROOT.exists():
        print("[错误] 还没有导出数据，请先执行导出。")
        return False

    print()
    print("=" * 60)
    print("  AI 点评生成")
    print("=" * 60)
    print()
    print("请选择点评人格：")
    print("  1. 猫娘不屑之锐评版")
    print("  2. 资深音乐收藏爱好者")
    print("  3. 暗恋的女神（娇羞版）")
    print("  4. 粗犷不懂事的体育男生")
    print("  5. 周杰伦爱好者")
    print("  6. 国内偶像爱好者 / 追星 fan")
    print("  7. 典型通讯录音乐爱好者")
    print("  8. 派蒙 · 冒险伙伴")
    print("  9. 孔子 · 礼乐秩序")
    print("  0. 返回主菜单")
    print()

    choice = input("请输入编号 [0]: ").strip() or "0"

    if choice == "0":
        print("[返回] 取消生成点评。")
        return False

    if choice not in gr.MODES:
        print("[错误] 无效选项。")
        return False

    mode = gr.MODES[choice]
    print(f"\n[模式] {mode['name']}")
    print("[读取] 正在读取导出数据...")

    try:
        data_section = gr.build_data_section()
        if not data_section.strip():
            print("[错误] 未读到任何数据，请先完成数据导出。")
            return False

        print(f"[读取] 数据主体共 {len(data_section)} 字符")

        # 拼装最终文件：人格 prompt + 反人机规则 + 数据
        final = (
            "=" * 60 + "\n"
            + "【系统指令 / Prompt】\n"
            + "=" * 60 + "\n\n"
            + mode["prompt"]
            + "\n\n"
            + gr.ANTI_ROBOT_RULES
            + "\n\n"
            + "=" * 60 + "\n"
            + "【以下为用户数据，请基于此进行分析】\n"
            + "=" * 60 + "\n\n"
            + data_section
        )

        output_path = Path(mode["output"])
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(final)

        print(f"\n[完成] 已生成：{output_path.absolute()}")
        print(f"[大小] {len(final)} 字符")
        print()
        print("把该文件的全部内容复制给任意大语言模型（Claude / GPT / 豆包 / Kimi 等），")
        print("即可得到对应人格的点评和推荐。")
        return True

    except Exception as e:
        print(f"\n[错误] 生成点评时出现异常：{e}")
        traceback.print_exc()
        return False


# ============================================================
#  主菜单
# ============================================================

def print_main_menu():
    print()
    print("=" * 60)
    print("  网易云音乐数据导出 + AI 点评工具")
    print("=" * 60)

    cookie_status = "已获取" if STATE["cookie"] else "未获取"
    data_status = "已导出" if STATE["data_exported"] else "未导出"

    print()
    print(f"  当前状态：Cookie {cookie_status}，数据 {data_status}")
    print()
    print("  1. 获取 / 更新 Cookie")
    print("  2. 导出所有数据（专辑 + 歌单 + 喜欢的音乐）")
    print("  3. 生成 AI 点评文件")
    print("  0. 退出程序")
    print()


def main_loop():
    while True:
        print_main_menu()
        choice = input("请输入编号 [0]: ").strip() or "0"

        if choice == "1":
            step_get_cookie()

        elif choice == "2":
            if not STATE["cookie"]:
                print("\n[提示] 请先执行选项 1 获取 Cookie。")
                continue
            step_export_data()

        elif choice == "3":
            step_generate_review()

        elif choice == "0":
            print("\n[退出] 感谢使用，再见。")
            return 0

        else:
            print("\n[错误] 无效选项，请重新输入。")


# ============================================================
#  入口
# ============================================================

def main():
    try:
        return main_loop()
    except KeyboardInterrupt:
        print("\n\n[中断] 用户取消。")
        return 130
    except Exception as e:
        print(f"\n[致命错误] {e}")
        traceback.print_exc()
        return 1
    finally:
        try:
            input("\n按回车键关闭窗口...")
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())