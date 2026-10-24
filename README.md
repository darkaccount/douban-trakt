# 豆瓣与 Trakt 影视看过列表比对与双向增量同步工具

本工具用于比对 **豆瓣（Douban）** 与 **Trakt** 的电影与电视剧看过列表，并将各自独有的影视同步至对方平台。支持本地单机运行与青龙面板定时自动化。

---

## 核心特性

1. **影视全支持（Movies & TV Shows）**：全量分页拉取 Trakt 的电影与电视剧集（含季集），豆瓣对应条目精准聚合。
2. **剧集多季智能对齐（Series Season Recognition）**：
   - 自动识别并归并豆瓣独立分季条目（如《继承之战 第一季/第二季》与 Trakt 母剧层级匹配）；
   - 防止由于豆瓣每季独立成条而导致的误判“豆瓣独有”。
3. **整季完成校验与防虚标机制（Season Completion Guard）**：
   - 对于电视剧集，Trakt 仅标记单集或未看完整季的剧集**绝不盲目同步至豆瓣**，避免污染豆瓣全剧看过列表；
   - 豆瓣条目若对应指定分季（如“第二季”），程序严格校验 Trakt 对应季是否 100% 观看完毕。
4. **只增不改，保留原始观影日期**：
   - 仅同步对方尚未标记的条目；
   - 对方已有记录**绝不重复标记或覆盖**；
   - 将豆瓣的观影日期同步至 Trakt，Trakt 原生支持写入历史时间戳。
5. **豆瓣未收录/下架条目忽略机制与自定义映射**：
   - 对于国内下架、锁死隐藏或未单独收录的条目（如特辑、短片、演唱会），自动沉淀到本地忽略表，不再干扰日常比对；
   - 支持根目录下放置 `custom_mappings.json` 自定义指定条目映射或排除规则（参考 `custom_mappings.example.json`）。
6. **免 VIP 方案（无需付费申请 Trakt API App）**：
   - 直接使用 Trakt 官方网页端已授权凭证，0 成本调用 Trakt 官方 API。
7. **两端智能缓存与多账号隔离**：
   - **Trakt 智能探测缓存**：每次比对先请求超轻量的 `/sync/last_activities`（耗时仅 50ms），未检测到新观影变动时**直接读取本地 SQLite 缓存**，0 冗余网络请求；
   - **豆瓣按时间倒序增量提前终止**：连续命中 15 部已缓存记录后立即停止网络爬取；
   - **多账号数据隔离**：SQLite 数据库基于 `(user_id, douban_id)` 与 `(account_id, trakt_id)` 联合主键完全隔离，多账号互不串号。
8. **极速本地比对与防风控保护**：
   - 毫秒级对齐两端已有片单，杜绝频繁爬取；内置受控写入随机延迟，有效保护豆瓣账号。
9. **交互式主菜单与青龙面板无缝集成**：
   - 本地直接运行 `python main.py` 即可进入数字选项主菜单；青龙面板提供分级解耦任务与全渠道通知推送。

---

## 项目目录结构

```text
douban-trakt/
├── core/                           # 核心业务模块（所有业务逻辑与工具类集中封装）
│   ├── __init__.py                 # 核心模块导出
│   ├── config.py                   # 环境变量解析与凭证提取
│   ├── database.py                 # 本地 SQLite 缓存与多账号隔离数据层
│   ├── douban_client.py            # 豆瓣爬虫客户端（带防风控机制与受控写入）
│   ├── trakt_client.py             # Trakt 官方 API 客户端（带轻量探测缓存）
│   ├── matcher.py                  # 多级智能比对引擎（片名/IMDb/季集聚合）
│   ├── syncer.py                   # 双向增量同步执行器（整季保护与历史回填）
│   ├── models.py                   # 统一电影与剧集数据结构定义
│   └── notify.py                   # 全渠道通知推送（适配青龙及主流通知Webhook）
├── ql_login.py                     # [青龙任务 1] 账号登录与凭证验证（配置后手动执行一次）
├── ql_sync.py                      # [青龙任务 2] 日常定时增量同步（每日自动定时执行，带通知）
├── main.py                         # 本地单机 CLI 终端 / 交互式数字主菜单
├── requirements.txt                # Python 依赖清单
├── .env.example                    # 环境变量配置模板
├── custom_mappings.example.json    # 自定义条目映射与忽略规则模板
├── LICENSE                         # MIT 开源许可协议
└── README.md                       # 项目说明文档
```

---

## 快速使用（本地单机运行）

### 1. 配置凭证
复制 `.env.example` 为 `.env`：
```bash
cp .env.example .env
```
用文本编辑器打开 `.env` 填写认证凭证。

> 💡 **推荐极简配置（两端各仅需 1 条环境变量）**：
> - `DOUBAN_AUTH`: 直接粘贴豆瓣完整 Cookie 字符串（自动提取用户数字 ID 及 CSRF 校验码）；或填写 `手机号/邮箱:密码`。
> - `TRAKT_AUTH`: 填写 `Client_ID#Access_Token`，或直接粘贴浏览器控制台获取的完整配置。

#### ① Trakt 凭证获取（3 秒搞定，无需申请 VIP）
提供以下两种极简方式（任选一种）：

- **方式一（推荐：控制台 1 键自动复制到剪贴板）**：
  1. 电脑浏览器打开并登录 [trakt.tv](https://trakt.tv)；
  2. 按 `F12` 打开开发者工具，切换到 **Console (控制台)**；
  3. 粘贴并回车运行下面这行命令：
     ```javascript
     copy(JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k => k.startsWith('oidc.user:')))).access_token)
     ```
     > 💡 **运行后你的 Access Token 字符串已自动存入系统剪贴板！**
     > 直接在青龙环境变量或 `.env` 的 `TRAKT_AUTH` 处按 `Ctrl + V` 粘贴即可（程序已内置官方公共 Web Client ID，无需手动拼接或查找 Client ID）。

- **方式二（F12 Network 网络请求头直接复制）**：
  在 [trakt.tv](https://trakt.tv) 页面按 `F12` 切换到 **Network (网络)**，刷新网页，在筛选框输入 `trakt`，点击任意请求，在 `Request Headers` 中找到 `authorization: Bearer xxx`，将 `xxx`（或整段请求头）直接粘贴到 `TRAKT_AUTH` 亦可识别。

#### ② 豆瓣凭证获取
1. `DOUBAN_USER_ID`: 打开你的豆瓣个人主页，URL 最后的 ID（如 `https://www.douban.com/people/your_user_id/` 中的 `your_user_id`）。*若使用 `DOUBAN_AUTH` 填完整 Cookie 则会自动提取，无需单独配置。*
2. `DOUBAN_COOKIE`: 在浏览器登录豆瓣后，按 `F12` -> `Network (网络)`，刷新页面，查看任意请求的 `Request Headers` 中的 `Cookie` 字符串（确保包含 `dbcl2` 和 `ck`）。

---

### 2. 使用方法

#### 推荐：交互式主菜单（直接运行）
```bash
python main.py
```
控制台将弹出交互式菜单，输入数字回车即可运行：
```text
============================================================
              🎬 Trakt <-> 豆瓣 影视同步助手
  当前账号配置：豆瓣 [已配置] | Trakt [已配置]
============================================================
  [1] 检查账号连通性 (Check)
  [2] 查看差异比对报告 (Diff 预览，极速本地匹配)
  [3] 将豆瓣独有影视同步到 Trakt (保留观影日期)
  [4] 将 Trakt 独有影视同步到 豆瓣 (受控写入)
  [5] 一键双向增量同步 (互相补全各自独有条目)
  [6] 深度解析：在线补全剩余未对齐条目的 IMDb 编号
  [7] 强制刷新 Trakt 远程缓存
  [8] 查看已忽略条目列表 (豆瓣未收录/下架条目)
  [0] 退出程序
------------------------------------------------------------
```

#### 进阶：命令行参数模式（适合自动化与脚本调用）
```bash
# 检查连通性
python main.py check

# 仅比对并输出差异（Diff 预览）
python main.py diff

# 仅将豆瓣独有同步到 Trakt
python main.py sync-to-trakt

# 仅将 Trakt 独有同步到 豆瓣
python main.py sync-to-douban

# 一键双向增量同步
python main.py sync-all

# 查看已忽略列表
python main.py ignored
```

#### 自定义映射与忽略规则（可选）
若个别冷门或合集条目两端 ID 无法自动匹配，可复制模板创建自定义映射表：
```bash
cp custom_mappings.example.json custom_mappings.json
```
在 `custom_mappings.json` 中配置 IMDb ID 或 Trakt Slug 与豆瓣条目 ID 的对应关系，或指定值为 `"ignore"` 彻底忽略。

---

## 青龙面板（Qinglong）定时任务部署指南

### 1. 为什么拉库会多出很多定时任务？
在青龙面板中，如果添加订阅时**未配置白名单**，青龙默认会扫描仓库内所有 `.py` 文件并将其全部强行注册为定时任务（包括底层的数据库、网络客户端等内部类库文件）。

为了保持任务列表干净整洁，**必须在订阅中指定白名单为 `ql_`，依赖文件填入 `core`**，青龙将仅识别 2 个标准化任务，其余内部模块作为依赖调用，绝不污染任务面板。

---

### 2. 添加青龙订阅（两种方式二选一）

#### 方式 A：青龙面板 Web UI「订阅管理」添加（推荐）
进入青龙面板 -> **订阅管理** -> **新建订阅**，填入以下参数：

| 配置项 | 填写内容 | 说明 |
| :--- | :--- | :--- |
| **名称** | `豆瓣与Trakt同步` | 自定义名称 |
| **类型** | `公开仓库` | GitHub 公开仓库 |
| **链接** | `https://github.com/darkaccount/douban-trakt.git` | 你的 GitHub 仓库地址 |
| **定时规则** | `0 0 * * *` | 每天凌晨自动拉取最新代码 |
| **白名单 (必须填写)** | `ql_` | **核心！只拉取 `ql_*.py` 任务脚本，杜绝内部模块生成多余任务** |
| **黑名单** | `core\|main` | 彻底过滤核心库与本地交互式入口 |
| **依赖文件 (必须填写)** | `core\|requirements` | **核心！将 `core/` 目录与 `requirements.txt` 同步到脚本执行区供调用** |
| **分支** | `main` | 仓库主分支 |

保存后点击 **运行** 订阅，面板的「定时任务」中将只创建干净的 2 个分级任务！

#### 方式 B：青龙命令行一键拉库
也可直接在青龙面板「终端」或容器命令行中执行：
```bash
ql repo https://github.com/darkaccount/douban-trakt.git "ql_" "core|main" "core|requirements" "main"
```

---

### 3. 两个分级任务说明

订阅同步完成后，「定时任务」列表中将呈现 2 个清晰解耦的任务：

| 任务名称 | 脚本命令 | 定时规则 (Cron) | 说明 |
| :--- | :--- | :--- | :--- |
| **豆瓣-Trakt 1.账号登录与凭证验证** | `task .../ql_login.py` | `0 0 1 1 *` *(占位)* | **手动执行**。配置好环境变量后运行一次，测试登录有效性、Token 自动续期与通知推送。 |
| **豆瓣-Trakt 2.日常定时增量同步** | `task .../ql_sync.py` | `0 4 * * *` *(每日4点)* | **唯一需要日常定时自动运行的任务**。全自动比对、剧集整季保护、增量双向同步与推送结果。 |

> 📌 **依赖说明**：无需单独的安装任务。两个任务启动时会自动检测并安装缺失的 `requests`、`beautifulsoup4`（仅首次缺失时安装，平时零开销）；容器重建后首次运行也会自动补装。若网络受限导致自动安装失败，可在「依赖管理」→「Python3」中手动添加，或直接重跑任务。
>
> 📌 **提示**：任务 1 的 Cron 设定为每年 1 月 1 日，仅用于青龙面板占位，日常保持静默，需要时点击右侧「运行」按钮手动触发即可。

---

### 4. 极简环境变量配置（两端各仅需 1 条变量）

在青龙面板「环境变量」界面添加以下变量：

| 变量名 | 内容格式与说明 |
| :--- | :--- |
| `DOUBAN_AUTH` | **推荐直接填入豆瓣完整 Cookie**。<br>*(脚本会自动识别并从中提取用户数字 ID 及 CSRF 码，无需额外设置 `DOUBAN_USER_ID`！)*<br>或填入 `豆瓣手机号/邮箱:密码`（若 Cookie 失效且无滑块验证时将尝试自动登录）。 |
| `TRAKT_AUTH` | **推荐直接填入 Trakt Access Token**（通过控制台一键复制即可）。<br>脚本兼容以下任意格式：<br>1. **纯 Token**：直接粘贴 `access_token` 字符串（自动套用官方 Client ID）<br>2. **带前缀**：`Bearer xxx`<br>3. **组合格式**：`Client_ID#Access_Token`<br>4. **控制台两行**：`TRAKT_CLIENT_ID=... TRAKT_ACCESS_TOKEN=...`<br>5. **Network 请求头**：直接粘贴包含 `authorization: Bearer ...` 的请求头 |

*(注：原有的 `DOUBAN_COOKIE`、`DOUBAN_USER_ID`、`TRAKT_CLIENT_ID`、`TRAKT_ACCESS_TOKEN` 分离配置依然完全兼容。)*

---

### 5. 常见问题：之前未填白名单多出了很多任务怎么办？
如果之前添加订阅时漏填了白名单，导致定时任务列表被多个内部模块任务淹没：
1. 进入青龙面板「订阅管理」，编辑原订阅，将 **白名单** 补填为 `ql_`，**依赖文件** 补填为 `core`，保存；
2. 进入「定时任务」，搜索或勾选多出的无用任务（如 `database.py`、`matcher.py`、`config.py` 等），直接批量删除；
3. 回到「订阅管理」，点击运行更新订阅，青龙将清爽地只保留 2 个标准化任务。

---

## 同步语义与数据安全须知

### 同步语义：只增量补全，不做删除

本工具是**单向补全式增量同步**，而非镜像同步：

- 仅将对方**尚未标记**的条目补写到另一端，已有记录绝不重复标记或覆盖；
- **在一端删除看片记录，不会同步删除另一端的记录**（例如在 Trakt 取消标记后，豆瓣上对应记录会保留）；
- Trakt 侧本地缓存基于 `last_activities` 时间戳判断变动，该接口不反映删除操作。若你在 Trakt 网站上删除过记录并希望本地缓存与远端重新对齐，请运行 `python main.py diff --refresh-trakt` 强制全量重拉，或删除 `cache.db` 后重新同步。

### 凭证与隐私

- `cache.db` 本地缓存中的 `platform_credentials` 表**明文保存**自动续期的豆瓣 Cookie 与 Trakt Token，`.env` 亦含明文凭证：两者均已加入 `.gitignore`，请勿将它们提交到仓库或同步到网盘/群晖等外部位置；
- 内置的默认 Trakt `Client ID` 来自 Trakt 官方网页端公共凭证（`TRAKT_AUTH` 只填 Token 时自动套用）。该 ID 属官方前端资产，**存在被官方更换或限制的风险**；生产环境建议在 [trakt.tv/oauth/applications](https://trakt.tv/oauth/applications) 自建应用并显式配置 `TRAKT_CLIENT_ID`；
- 大量写入豆瓣操作受随机延迟保护，但自动化标记本身始终存在触发风控的可能，请保持默认延迟设置，勿调小 `SYNC_DELAY_MIN/MAX`。

---

## 开源协议

本项目采用 [MIT License](LICENSE) 授权许可。
