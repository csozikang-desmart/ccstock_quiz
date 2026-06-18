# -*- coding: utf-8 -*-
"""双创科技赛道 H5 答题小游戏 —— 后端服务

启动：python server.py
接口：
  GET  /                 首页 index.html
  GET  /api/quiz?n=8     获取随机题包
  POST /api/result       提交答案，返回结算
  GET  /api/refresh      强制刷新行情缓存
  GET  /api/portfolio    查看缓存行情

设计要点：
- 仅依赖 Python 标准库 http.server + urllib，无需 flask / akshare
- 数据源：腾讯证券自选股后端
    实时报价（含 PE/PB）：http://qt.gtimg.cn/q=sz300750,sh688981,...
    日线（前复权）：     http://web.ifzq.gtimg.cn/appstock/app/fqkline/get
- 公司池：双创科技赛道前 150 家（科创板 75 + 创业板 75），见 companies.py
- 网络偶发失败时回退到 cache.json，保证可玩
- 启动后立即响应 HTTP，行情在后台线程刷新；缓存为空时降级为业务/热点题
"""
import os
import re
import sys
import json
import time
import uuid
import random
import datetime as dt
import threading
import socketserver
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 项目内导入
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from companies import COMPANIES, HOT_TOPICS, DISTRACTOR_POOL  # noqa: E402

CACHE_FILE = os.path.join(HERE, "cache.json")
QUIZ_STORE = {}  # quiz_id -> 题包（内存）
LOCK = threading.Lock()
HTTP_TIMEOUT = 12  # 腾讯接口超时

# 全部小写，避免 GBK 编码问题
USER_AGENT = "Mozilla/5.0 (Linux; Android 12; TencentStock/8.6)"


# ----------------------- 腾讯接口 ----------------------- #
def _tencent_symbol(code):
    """6 位代码 → 腾讯前缀（sh / sz / bj）"""
    code = str(code).strip().zfill(6)
    if code[0] in ("6", "9", "5"):    # 沪市 A/B / 科创板
        return "sh" + code
    if code[0] in ("0", "3", "2"):    # 深市 A / 创业板
        return "sz" + code
    if code[0] in ("8", "4"):         # 北交所
        return "bj" + code
    return "sz" + code


def _http_get(url, encoding="utf-8"):
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Referer": "http://stockapp.finance.qq.com/"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read().decode(encoding, errors="replace")


def fetch_quotes_batch(codes):
    """腾讯批量实时报价；一次最多 ~50 个代码。
    返回 {code: {name, current, prev_close, pe_ttm, pb, change_pct, time}}
    """
    out = {}
    BATCH = 50
    for i in range(0, len(codes), BATCH):
        chunk = codes[i:i + BATCH]
        syms = ",".join(_tencent_symbol(c) for c in chunk)
        url = "http://qt.gtimg.cn/q=" + syms
        try:
            text = _http_get(url, encoding="gbk")
        except Exception as e:
            print(f"[tencent] quote batch {i} 失败：{e}", flush=True)
            continue
        # 解析 v_xxxxx="...";\n
        for m in re.finditer(r'v_(?P<sym>\w+)="(?P<val>[^"]*)"', text):
            sym = m.group("sym")
            parts = m.group("val").split("~")
            code = sym[2:]
            if len(parts) < 6:
                continue
            try:
                current = float(parts[3]) if parts[3] else None
            except (ValueError, IndexError):
                current = None

            def _safe(idx):
                if idx >= len(parts):
                    return None
                v = parts[idx]
                if not v or v == "-":
                    return None
                try:
                    return float(v)
                except ValueError:
                    return None

            out[code] = {
                "name": parts[1] if len(parts) > 1 else "",
                "current": current,
                "prev_close": _safe(4),
                "change_pct": _safe(32),
                "pe_ttm": _safe(39),
                "pb": _safe(46),
                "time": parts[30] if len(parts) > 30 else "",
            }
    return out


def fetch_kline(code, days=200):
    """腾讯前复权日线，返回 [[date_str, open, close, high, low, volume], ...]"""
    sym = _tencent_symbol(code)
    start = (dt.date.today() - dt.timedelta(days=days * 2)).strftime("%Y-%m-%d")
    # 640 表示取最多 640 根，足够覆盖 200 个交易日
    url = (f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={sym},day,{start},,640,qfq")
    text = _http_get(url, encoding="utf-8")
    data = json.loads(text)
    body = (data.get("data") or {}).get(sym, {}) or {}
    rows = body.get("qfqday") or body.get("day") or body.get("underlying5day") or []
    # 行格式：[date, open, close, high, low, volume]
    out = []
    for r in rows:
        if len(r) < 6:
            continue
        try:
            out.append([r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
        except (ValueError, TypeError):
            continue
    return out


def _nearest_row(rows, target_str):
    """rows 按 date 升序，返回 date<=target 的最后一条；无则首条"""
    picked = None
    for r in rows:
        if r[0] <= target_str:
            picked = r
        else:
            break
    return picked or rows[0]


def fetch_one(code, quote=None):
    """抓单家公司，返回 dict（含 close/ret_1m/ret_3m/ret_6m/ma5/ma20/pe/pb）"""
    try:
        rows = fetch_kline(code, days=200)
    except Exception as e:
        print(f"[tencent] {code} kline 失败：{e}", flush=True)
        return None
    if not rows:
        print(f"[tencent] {code} 无 K 线", flush=True)
        return None

    today = dt.date.today()
    cur_row = rows[-1]
    cur_price = float(cur_row[2])

    def price_at(months):
        target = (today - dt.timedelta(days=30 * months)).strftime("%Y-%m-%d")
        r = _nearest_row(rows, target)
        return float(r[2]) if r else None

    p1, p3, p6 = price_at(1), price_at(3), price_at(6)

    def ret(p):
        if not p or p == 0:
            return None
        return round((cur_price - p) / p * 100, 2)

    closes = [r[2] for r in rows]
    ma5 = sum(closes[-5:]) / min(5, len(closes)) if closes else None
    ma20 = sum(closes[-20:]) / min(20, len(closes)) if closes else None

    q = quote or {}
    info = {
        "code": code,
        "current": round(cur_price, 3),
        "p1m": p1, "p3m": p3, "p6m": p6,
        "ret_1m": ret(p1), "ret_3m": ret(p3), "ret_6m": ret(p6),
        "ma5": round(ma5, 3) if ma5 else None,
        "ma20": round(ma20, 3) if ma20 else None,
        "ma_state": ("多头" if (ma5 and ma20 and ma5 > ma20) else "空头"),
        "trade_date": cur_row[0],
        "pe_ttm": q.get("pe_ttm"),
        "pb": q.get("pb"),
    }
    return info


def refresh_cache():
    """刷新所有公司行情并写入 cache.json；返回 {code: info}"""
    codes = [c["code"] for c in COMPANIES]
    print(f"[refresh] 抓取腾讯实时报价（{len(codes)} 家）...", flush=True)
    quotes = {}
    try:
        quotes = fetch_quotes_batch(codes)
    except Exception as e:
        print(f"[refresh] 报价抓取异常：{e}", flush=True)

    result = {}
    total = len(COMPANIES)
    for i, c in enumerate(COMPANIES, 1):
        code = c["code"]
        info = None
        try:
            info = fetch_one(code, quote=quotes.get(code))
        except Exception as e:
            print(f"[refresh] {code} 异常：{e}", flush=True)
        if info:
            result[code] = info
            print(f"[refresh] ({i}/{total}) {code} {c['name']} OK "
                  f"close={info['current']} ret_6m={info['ret_6m']}", flush=True)
        else:
            print(f"[refresh] ({i}/{total}) {code} {c['name']} FAIL", flush=True)

    if result:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                       "source": "tencent",
                       "data": result}, f, ensure_ascii=False, indent=2)
        print(f"[refresh] 行情已缓存到 cache.json ({len(result)} 家)", flush=True)
        return result

    print("[refresh] 抓取全部失败，回退缓存", flush=True)
    return load_cache()


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                obj = json.load(f)
            return obj.get("data", obj)
        except Exception as e:
            print(f"[cache] 读取失败：{e}", flush=True)
    return {}


def load_cache_updated():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("updated_at")
        except Exception:
            return None
    return None


def get_market():
    """获取行情：只读缓存；HTTP 请求侧不触发抓取，避免阻塞。
    抓取由启动后台线程 / /api/refresh 负责。
    """
    return load_cache()


# ----------------------- 题库生成 ----------------------- #
def _gen_distractors(correct, n=3):
    """从 DISTRACTOR_POOL 抽 n 个与正确答案不同的干扰项"""
    pool = [d for d in DISTRACTOR_POOL if d != correct]
    if len(pool) < n:
        pool = pool + [d for d in DISTRACTOR_POOL if d not in pool]
    return random.sample(pool, n)


def _gen_hot_options(correct, n=4):
    """从 HOT_TOPICS 抽 n-1 个非正确热点 + 正确答案，洗牌"""
    others = [h for h in HOT_TOPICS if h != correct]
    opts = random.sample(others, min(n - 1, len(others))) + [correct]
    random.shuffle(opts)
    return opts


def build_quiz(n=8, market=None):
    market = market if market is not None else get_market()

    # 三类题分布：尽量均匀；行情为空时把 timing 降级为 business，保证可玩
    types_round = ["business", "hot", "timing"] if market else ["business", "hot", "business"]
    plan = []
    while len(plan) < n:
        plan.append(types_round[len(plan) % 3])
    plan = plan[:n]

    pool = [c for c in COMPANIES]
    random.shuffle(pool)

    questions = []
    used_codes = set()

    def pick_company(need_market=False):
        for c in pool:
            if c["code"] in used_codes:
                continue
            if need_market and c["code"] not in market:
                continue
            used_codes.add(c["code"])
            return c
        # 兜底：允许重复
        return random.choice(pool)

    for i, t in enumerate(plan):
        c = pick_company(need_market=(t == "timing"))
        if t == "business":
            opts = _gen_distractors(c["business"], 3) + [c["business"]]
            random.shuffle(opts)
            q = {
                "id": i, "type": "business", "company": c["name"], "code": c["code"],
                "prompt": f"以下哪个是【{c['name']}】（{c['board']}·{c['industry']}）的核心业务？",
                "options": opts,
            }
        elif t == "hot":
            opts = _gen_hot_options(c["hot_correct"], 4)
            q = {
                "id": i, "type": "hot", "company": c["name"], "code": c["code"],
                "prompt": f"以下热点中，【{c['name']}】最贴近哪一个？",
                "options": opts,
            }
        else:  # timing
            m = market.get(c["code"], {})
            pe = m.get("pe_ttm")
            pb = m.get("pb")
            r1 = m.get("ret_1m")
            ma_state = m.get("ma_state", "—")
            pe_txt = f"PE={pe:.1f}" if pe else "PE=暂缺"
            pb_txt = f"PB={pb:.2f}" if pb else ""
            r1_txt = f"近1月{r1:+.2f}%" if r1 is not None else "近1月暂缺"
            prompt = (f"【{c['name']}】当前 {pe_txt} {pb_txt}，{r1_txt}，"
                      f"均线 MA5 vs MA20 = {ma_state}。"
                      f"如果你现在入手并持有 6 个月，最可能的结果是？")
            q = {
                "id": i, "type": "timing", "company": c["name"], "code": c["code"],
                "prompt": prompt,
                "options": ["大涨", "小涨", "震荡", "下跌"],
                "context": {
                    "pe": pe, "pb": pb, "ret_1m": r1,
                    "ret_3m": m.get("ret_3m"), "ret_6m": m.get("ret_6m"),
                    "current": m.get("current"), "ma_state": ma_state,
                    "trade_date": m.get("trade_date"),
                },
            }
        questions.append(q)

    quiz_id = uuid.uuid4().hex
    payload = {"quiz_id": quiz_id, "questions": questions,
               "created_at": dt.datetime.now().isoformat(timespec="seconds")}
    with LOCK:
        QUIZ_STORE[quiz_id] = payload
    return payload


# ----------------------- 结算 ----------------------- #
def classify_timing(ret_6m):
    """按真实 6 个月涨幅归类；方向匹配 50 分"""
    if ret_6m is None:
        return None
    if ret_6m >= 15:
        return "大涨"
    if ret_6m > 0:
        return "小涨"
    if ret_6m >= -5:
        return "震荡"
    return "下跌"


def grade_answer(q, answer):
    """返回 (score, correct_option, is_correct)"""
    code = q["code"]
    cfg = next((c for c in COMPANIES if c["code"] == code), None)
    if q["type"] == "business":
        correct = cfg["business"] if cfg else None
    elif q["type"] == "hot":
        correct = cfg["hot_correct"] if cfg else None
    else:  # timing —— 真实方向
        ret_6m = (q.get("context") or {}).get("ret_6m")
        correct = classify_timing(ret_6m)

    is_correct = bool(correct) and answer == correct
    score = {("business", True): 30, ("business", False): 0,
             ("hot", True): 20, ("hot", False): 0,
             ("timing", True): 50, ("timing", False): 0}.get((q["type"], is_correct), 0)
    return score, correct, is_correct


def build_result(quiz_id, answers):
    with LOCK:
        quiz = QUIZ_STORE.get(quiz_id)
    if not quiz:
        return {"error": "quiz_id 无效或已过期"}, 400

    market = get_market()
    total = 0
    per_question = []
    codes_in_quiz = []

    for q in quiz["questions"]:
        ans = answers.get(str(q["id"]))
        score, correct, is_correct = grade_answer(q, ans)
        total += score
        codes_in_quiz.append(q["code"])
        item = {
            "id": q["id"], "type": q["type"], "company": q["company"], "code": q["code"],
            "your_answer": ans, "correct_answer": correct,
            "is_correct": is_correct, "score": score,
            "prompt": q["prompt"], "options": q["options"],
        }
        if q["type"] == "timing":
            item["context"] = q.get("context")
        per_question.append(item)

    # 组合等权平均
    uniq_codes = list(dict.fromkeys(codes_in_quiz))
    ret_1m_sum = ret_3m_sum = ret_6m_sum = 0.0
    cnt_have_1m = cnt_have_3m = cnt_have_6m = 0
    details = []
    for code in uniq_codes:
        m = market.get(code, {})
        cfg = next((c for c in COMPANIES if c["code"] == code), {})
        if m.get("ret_1m") is not None:
            ret_1m_sum += m["ret_1m"]; cnt_have_1m += 1
        if m.get("ret_3m") is not None:
            ret_3m_sum += m["ret_3m"]; cnt_have_3m += 1
        if m.get("ret_6m") is not None:
            ret_6m_sum += m["ret_6m"]; cnt_have_6m += 1
        details.append({
            "code": code, "name": cfg.get("name", ""), "board": cfg.get("board", ""),
            "industry": cfg.get("industry", ""),
            "current": m.get("current"), "pe": m.get("pe_ttm"), "pb": m.get("pb"),
            "ret_1m": m.get("ret_1m"), "ret_3m": m.get("ret_3m"),
            "ret_6m": m.get("ret_6m"), "trade_date": m.get("trade_date"),
        })

    def avg(s, c):
        return round(s / c, 2) if c else None

    portfolio = {
        "n_companies": len(uniq_codes),
        "ret_1m_avg": avg(ret_1m_sum, cnt_have_1m),
        "ret_3m_avg": avg(ret_3m_sum, cnt_have_3m),
        "ret_6m_avg": avg(ret_6m_sum, cnt_have_6m),
        "details": details,
    }

    # 段位
    if total < 100:
        rank = "韭菜新手"
    elif total < 200:
        rank = "散户达人"
    elif total < 300:
        rank = "投研老手"
    else:
        rank = "双创之星"

    return {
        "quiz_id": quiz_id,
        "total_score": total,
        "max_score": sum({"business": 30, "hot": 20, "timing": 50}[q["type"]]
                          for q in quiz["questions"]),
        "rank": rank,
        "portfolio": portfolio,
        "questions": per_question,
        "updated_at": load_cache_updated(),
    }, 200


# ----------------------- HTTP ----------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 简化日志
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))
        sys.stdout.flush()

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            return self._serve_file("index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._serve_file("app.js", "application/javascript; charset=utf-8")
        if path == "/style.css":
            return self._serve_file("style.css", "text/css; charset=utf-8")
        if path == "/api/quiz":
            n = int(qs.get("n", ["8"])[0])
            n = max(3, min(n, len(COMPANIES) * 3))
            try:
                payload = build_quiz(n=n)
            except Exception as e:
                return self._json({"error": str(e)}, 500)
            return self._json(payload)
        if path == "/api/refresh":
            data = refresh_cache()
            return self._json({"ok": True, "n": len(data),
                               "updated_at": load_cache_updated(),
                               "source": "tencent"})
        if path == "/api/portfolio":
            market = get_market()
            return self._json({"data": market, "updated_at": load_cache_updated(),
                               "source": "tencent"})
        return self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/result":
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            return self._json({"error": f"请求格式错误：{e}"}, 400)
        quiz_id = payload.get("quiz_id")
        answers = payload.get("answers", {})
        result, code = build_result(quiz_id, answers)
        return self._json(result, code)

    def _serve_file(self, name, ctype):
        fp = os.path.join(HERE, name)
        if not os.path.exists(fp):
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        with open(fp, "rb") as f:
            body = f.read()
        return self._send(200, body, ctype)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    print("=" * 60, flush=True)
    print("双创科技赛道 H5 答题小游戏  ·  数据源：腾讯证券自选股", flush=True)
    print(f"公司池：{len(COMPANIES)} 家（科创板 "
          f"{sum(1 for c in COMPANIES if c['board']=='科创板')} + "
          f"创业板 {sum(1 for c in COMPANIES if c['board']=='创业板')}）", flush=True)
    print(f"项目目录: {HERE}", flush=True)
    print("=" * 60, flush=True)

    if not load_cache():
        print("[startup] 未发现 cache.json，启动后将在后台抓取行情（首次答题可能稍慢）……",
              flush=True)
        threading.Thread(target=refresh_cache, daemon=True).start()
    else:
        print(f"[startup] 已读取缓存 cache.json (updated_at={load_cache_updated()})",
              flush=True)
        print("[startup] 如需刷新最新行情，访问 /api/refresh 或重启 server", flush=True)

    print(f"\n服务已启动：http://127.0.0.1:{port}/\n"
          f"局域网访问：http://<本机IP>:{port}/\n", flush=True)

    httpd = ThreadingHTTPServer((host, port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[shutdown] bye", flush=True)
        httpd.shutdown()


if __name__ == "__main__":
    main()
