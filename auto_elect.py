# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import html
import re
import random
import sys
import time
from datetime import datetime
from typing import Any, Optional
import requests


# ============================ 配置区 ============================

# 浏览器登录选课系统后，从开发者工具里复制的完整 Cookie。
# 注意：Cookie 会过期，如果提示会话失效，请重新登录并替换这里的内容。
COOKIE = (
    "semester.id=134; "
    "JSESSIONID=87F091384A01ABAD54DDEE8283795CB9.std6; "
    "GSESSIONID=87F091384A01ABAD54DDEE8283795CB9.std6;"
    "JSESSIONID=20755C0B2EC6937DE806C264E314D84B.std6"
) #cookie（每次登陆需替换！！！）

COURSE_ID = "494876" #课程代码（需替换！！！）

PROFILE_ID = "4459" #个人资料ID（需替换！！！）

REQUEST_INTERVAL = 3

JITTER = 0.3

# 最大尝试次数。设为 0 表示无限循环，直到成功或手动停止。
MAX_ATTEMPTS = 0

# 成功时是否让电脑发出提示音（响铃）。
BEEP_ON_SUCCESS = True

# 日志文件路径。设为空字符串 "" 则只打印到终端。
LOG_FILE = "auto_elect.log"

# 是否保存最近一次服务端原始响应，方便排查 HTML 表格里的具体提示。
SAVE_LAST_RESPONSE = True
LAST_RESPONSE_FILE = "last_response.html"

# 判断“选课成功”的文本关键词。
SUCCESS_KEYWORDS = [
    "选课成功",
    "选课 成功",
    "操作成功",
    "已选上",
    "选课已完成",
    "已经选过",
    "已选过",
    "success",
]

# 常见失败提示，用于更清楚地打印原因。脚本默认会继续重试。
FAILURE_KEYWORDS = [
    "人数已满",
    "选课人数已满",
    "教学班已满",
    "名额已满",
    "已满",
    "没有可选",
    "没有余量",
    "无余量",
    "余量不足",
    "容量不足",
    "已达上限",
    "已达到上限",
    "超出容量",
    "不在选课时间",
    "未到选课时间",
    "未开放",
    "系统繁忙",
    "请稍后再试",
    "时间冲突",
    "课程冲突",
    "重复选课",
    "已选该课",
    "已选过",
]

# 出现这些关键词时，说明 Cookie 大概率失效或需要重新登录，脚本会停止。
SESSION_INVALID_KEYWORDS = [
    "请先登录",
    "尚未登录",
    "登录已过期",
    "会话已失效",
    "session expired",
    "not logged in",
    "无权限访问",
]

# ============================ 配置区结束 ============================


BASE_URL = "https://classes.tju.edu.cn"
ELECT_URL = (
    f"{BASE_URL}/eams/stdElectCourse!batchOperator.action?profileId={PROFILE_ID}"
)
REFERER_URL = f"{BASE_URL}/eams/stdElectCourse!defaultPage.action"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    line = f"[{now_str()}] {msg}"
    print(line, flush=True)
    if LOG_FILE:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def build_session() -> requests.Session:
    """创建带浏览器请求头和 Cookie 的 Session。"""
    session = requests.Session()
    #下面这个session.headers.update部分需要整个检查并替换成自己的配置！！！
    session.headers.update(
        {
            "Accept": "text/html, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Connection": "keep-alive",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": BASE_URL,
            "Referer": REFERER_URL,
            "Sec-Ch-Ua": (
                '"Microsoft Edge";v="153", "Not_A Brand";v="8", '
                '"Chromium";v="153"'
            ),
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0"
            ),
            "X-Requested-With": "XMLHttpRequest",
            # 原样带上浏览器里的 Cookie，避免 requests 对重复 JSESSIONID 做处理。
            "Cookie": COOKIE.strip(),
        }
    )
    return session


def parse_json(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def clean_response_text(text: str) -> str:
    """把 HTML 响应转成可读的纯文本，避免只看到 table/div 标签。"""
    if not text:
        return ""

    # 去掉 script/style 内部内容，避免干扰。
    text = re.sub(
        r"(?is)<(script|style)[^>]*>.*?</\1>",
        " ",
        text,
    )
    # 把标签替换成空格，再还原 HTML 实体。
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    # 合并连续空白。
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_success(payload: Any, text: str) -> bool:
    """根据 JSON 字段或响应文本判断是否成功。"""
    clean_text = clean_response_text(text)
    # 忽略空白后匹配，兼容“选课 成功”“选课成功”等写法。
    compact_text = re.sub(r"\s+", "", clean_text.lower())
    compact_keywords = [re.sub(r"\s+", "", kw.lower()) for kw in SUCCESS_KEYWORDS]

    if isinstance(payload, dict):
        # 有些系统会返回 success/flag/status/result 等布尔字段。
        for key in ("success", "ok", "flag", "result", "status"):
            if key in payload:
                value = str(payload[key]).strip().lower()
                if value in {"true", "1", "success", "ok", "yes", "成功", "已选"}:
                    return True
                if value in {"false", "0", "fail", "failed", "error", "失败"}:
                    return False

        # 有些系统会把提示放在 msg/message 字段里。
        for key in ("msg", "message", "info"):
            if key in payload and isinstance(payload[key], str):
                candidate = payload[key]
                compact_candidate = re.sub(r"\s+", "", candidate.lower())
                if any(kw in compact_candidate for kw in compact_keywords):
                    return True

    return any(kw in compact_text for kw in compact_keywords)


def describe_failure(payload: Any, text: str) -> str:
    """从响应中提取一句比较有意义的失败原因。"""
    if isinstance(payload, dict):
        for key in ("msg", "message", "info", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return clean_response_text(value)

    clean_text = clean_response_text(text)
    lower_text = clean_text.lower()
    for kw in FAILURE_KEYWORDS:
        if kw.lower() in lower_text:
            return kw

    return clean_text[:500] if clean_text else "（无响应内容）"


def session_expired(response: requests.Response) -> bool:
    text = clean_response_text(response.text).lower()
    if any(kw.lower() in text for kw in SESSION_INVALID_KEYWORDS):
        return True

    # 被重定向到登录页，也视为会话失效。
    if response.history:
        final_url = response.url.lower()
        if "login" in final_url or "logout" in final_url or "/auth/" in final_url:
            return True
    return False


def attempt_select(session: requests.Session) -> requests.Response:
    data = {
        "optype": "true",
        "operator0": f"{COURSE_ID}:true:0",
    }
    return session.post(ELECT_URL, data=data, timeout=15)


def beep() -> None:
    if not BEEP_ON_SUCCESS:
        return
    try:
        print("\a", end="", flush=True)
    except Exception:
        pass


def main() -> int:
    if not COOKIE.strip():
        log("未配置 COOKIE，请先登录选课系统并复制完整 Cookie。")
        return 1

    log(f"课程 ID：{COURSE_ID}")
    log(f"选课地址：{ELECT_URL}")
    log(
        f"检测间隔：{REQUEST_INTERVAL} 秒（抖动 ±{JITTER} 秒）"
    )
    log("开始循环检测空余名额，按 Ctrl+C 可停止。")
    log("=" * 60)

    session = build_session()
    attempt = 0
    consecutive_errors = 0

    try:
        while True:
            attempt += 1
            if MAX_ATTEMPTS and attempt > MAX_ATTEMPTS:
                log(f"已达到最大尝试次数 {MAX_ATTEMPTS}，停止。")
                break

            try:
                response = attempt_select(session)
            except requests.exceptions.RequestException as exc:
                consecutive_errors += 1
                log(f"第 {attempt} 次请求失败：{exc}")
                if consecutive_errors >= 10:
                    log("连续 10 次网络请求失败，请检查网络或 Cookie 后重试。")
                    return 1
                time.sleep(max(2.0, REQUEST_INTERVAL * 3))
                continue

            consecutive_errors = 0
            text = response.text
            payload = parse_json(text)

            if session_expired(response):
                log("检测到 Cookie 已失效或会话过期，请重新登录并更新 COOKIE。")
                log(f"HTTP {response.status_code}，最终地址：{response.url}")
                log(f"响应片段：{clean_response_text(text)[:500]}")
                return 2

            if response.status_code != 200:
                log(
                    f"第 {attempt} 次：HTTP {response.status_code}，"
                    f"响应片段：{clean_response_text(text)[:300] or '(空)'}"
                )
            elif detect_success(payload, text):
                log("=" * 60)
                log("🎉 检测到名额并已完成选课！")
                log(f"响应内容：{clean_response_text(text)[:500]}")
                beep()
                log("程序已自动停止，无需再手动操作。")
                return 0
            else:
                reason = describe_failure(payload, text)
                log(f"第 {attempt} 次：未成功，原因：{reason}")

            if SAVE_LAST_RESPONSE:
                try:
                    with open(LAST_RESPONSE_FILE, "w", encoding="utf-8") as f:
                        f.write(text)
                except OSError:
                    pass

            sleep_time = max(
                0.2, REQUEST_INTERVAL + random.uniform(-JITTER, JITTER)
            )
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        log("已手动停止。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
