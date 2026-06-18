# 双创科技赛道 H5 答题小游戏

围绕科创板、创业板前 150 家科技公司，通过“核心业务 / 热点关联 / 入手时机”随机选择题进行互动。结算时给出得分、段位，并展示所答公司等权组合的近 1 月、近 3 月、近半年真实阶段收益。

行情数据来自 **腾讯证券自选股** 后端（`qt.gtimg.cn` + `web.ifzq.gtimg.cn`），纯 Python 标准库 `urllib` 抓取。

游戏还内置 Web Audio 合成的 BGM / 音效，以及答题推进时随机解锁的 20 条硬核科技知识。

## 目录结构

```text
h5-quiz/
  server.py        # Python 后端：腾讯行情 + HTTP 服务 + 题库 + 结算
  companies.py     # 公司池（150 家）+ HOT_TOPICS + DISTRACTOR_POOL
  cache.json       # 行情缓存，运行时自动生成（已加入 .gitignore）
  index.html       # H5 单页（开始 / 答题 / 结算）
  app.js           # 游戏交互 + Web Audio + 知识解锁
  style.css        # 移动端样式
  requirements.txt # 仅标准库，无第三方依赖
  render.yaml      # Render 一键部署配置
  README.md
```

## 本地启动

```bash
cd D:\202606\h5-quiz
python server.py
```

访问：

```text
http://127.0.0.1:8000/
http://本机IP:8000/   # 同一局域网的手机
```

## 接口

- `GET /api/quiz?n=8`：生成随机题包
- `POST /api/result`：提交答案并结算
- `GET /api/refresh`：手动刷新行情缓存
- `GET /api/portfolio`：查看缓存行情

## 行情口径

后端从腾讯接口抓取后计算：

- 当前价（前复权最新收盘）
- 1 / 3 / 6 个月前价格
- 1 / 3 / 6 个月阶段涨跌幅 %
- MA5 / MA20 走势（多头 / 空头）
- PE（动态）、PB（实时报价）

首次启动若无 `cache.json`，后台线程会自动抓取；腾讯接口偶发失败时回退缓存，保证可玩。

## 题型与计分

- 业务题 30 分：判断公司核心业务（干扰项从 50+ 业务池随机抽取）
- 热点题 20 分：判断公司最相关热点（选项从 30 个热点池随机抽取）
- 时机题 50 分：根据 PE/PB、近 1 月涨跌、MA5 vs MA20 判断半年方向

时机题参考答案按真实近半年涨跌幅划分：

- `>= 15%`：大涨
- `0% ~ 15%`：小涨
- `-5% ~ 0%`：震荡
- `< -5%`：下跌

## 段位

- `< 100`：韭菜新手
- `100 ~ 199`：散户达人
- `200 ~ 299`：投研老手
- `>= 300`：双创之星

## 部署到 Render（免费）

Render 免费版：
- 750 小时 / 月（一个 Web Service 长时间可用）
- 15 分钟无访问会休眠，再次访问冷启动约 30~60s
- 新加坡区离国内最近，访问延迟最低
- HTTPS 自动开启

步骤：

1. 把整个 `h5-quiz/` 目录推送到 GitHub（建议 Public 仓库；Private 也行，需在 Render 里授权）

   ```bash
   cd D:\202606\h5-quiz
   git init
   git add .
   git commit -m "init: h5-quiz tencent data source + audio + tech facts"
   git branch -M main
   git remote add origin https://github.com/<你的用户名>/h5-quiz.git
   git push -u origin main
   ```

2. 打开 https://render.com，注册 / 登录（GitHub 账号即可）

3. 右上角 **New +** → **Blueprint** → 选择刚才的 GitHub 仓库

   - Render 会自动识别根目录的 `render.yaml`
   - 计划选 **Free**
   - 区域选 **Singapore**
   - 点击 **Apply**

4. 等待构建（1~2 分钟），完成后会得到一个地址，例如：

   ```text
   https://h5-quiz.onrender.com
   ```

5. 浏览器或手机直接访问即可。首次访问服务可能从休眠冷启动，后台会同步抓取行情（约 30~60s 后 timing 题就会有数据）。

### 国内访问注意事项

- Render 免费服务的 `*.onrender.com` 域名在国内 **绝大多数网络环境可访问**，但偶尔会有波动；如果你绑定了自有域名 + Cloudflare 反代会更稳定
- 服务 15 分钟无访问会休眠，冷启动时浏览器可能转圈 30~60 秒，耐心等待或访问一次唤醒

## 数据源说明

| 项 | 路径 |
|---|---|
| 实时报价（PE/PB/价格） | `http://qt.gtimg.cn/q=sz300750,sh688981,...` |
| 前复权日 K | `http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=...` |

请求头带 UA / Referer，按 50 个代码一批分批拉取，避免触发风控。

## 注意

本项目仅用于学习与互动演示，行情数据来自公开接口，不构成任何投资建议。
