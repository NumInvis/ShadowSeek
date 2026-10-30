# ShadowSeek · 找鸡

面向 GsCore（早柚核心）的以图搜源插件：发送或引用一张图片，给出最可能的 X（Twitter）原帖或账号。

ShadowSeek 是独立实现，不是任何既有插件的 fork，也不携带上游提交历史。命令、文案、结论图与检索流程都重新设计过。

## 它能做什么

- 图片 + `找鸡`：对图片做完全匹配检索，多个识图引擎并发。
- 引用图片 + `找鸡`：读取被引用消息里的图片再检索。
- 只有下载到候选配图并完成同图比对，才会把来源标记为“已确认同图”；相似图只作为弱线索。
- 来源页没有直接给出 X 链接时，读取页面正文，提取 `@账号`、署名行，必要时用模型抽取一句原文，再去 X 限定搜索。
- 多个账号证据相当时并列展示，不强行判定归属。
- 同一张图按感知哈希缓存结果，减少重复请求。

## 安装

1. 将本目录放到 `gsuid_core/gsuid_core/plugins/ShadowSeek/`。
2. 在 GsCore 的虚拟环境中安装依赖：

```bash
.venv/bin/python -m pip install -r gsuid_core/plugins/ShadowSeek/requirements.txt
```

3. 重启 GsCore。首次启动会在 `data/ShadowSeek/config.json` 生成配置。
4. 打开网页控制台，在 `ShadowSeek` 面板填写需要的 Key，并按需调整引擎与白名单。

依赖：`httpx`、`Pillow`、`PicImageSearch`、`pyquery`、`lxml`。识图引擎由 PicImageSearch 提供，Google Lens 走 SerpApi。

## 命令

| 命令 | 作用 |
| --- | --- |
| `找鸡` / `觅影` / `图源定位` / `找原图` | 对当前图片或引用图片检索来源 |
| `找鸡帮助` | 查看用法 |

默认不强制命令前缀；如果 GsCore 配置了全局前缀，前缀 + 命令同样可用。

## 判定与展示

结果采用固定结构：`判定 / 账号 / 原帖 / 依据 / 可信度`。

- **已确认同图（高）**：候选帖配图与原图通过字节、长宽比、缩略图色差和 dHash 比对。
- **引用关系确认（高）**：原帖引用的帖子配图也通过同图比对。
- **来源署名 / 链接指向（中）**：来源页明确署名或直接给出链接，但未取得配图。
- **文字提及（低）**：只从页面文字里读到账号。

相似图引擎（如 Yandex、Google 相似图）给出的候选强度封顶为最低级，不能单独构成结论。

## 配置

| 键 | 说明 |
| --- | --- |
| `group_whitelist` / `private_whitelist` | 群 / 私聊白名单，留空表示全部可用 |
| `engines` | 启用的识图引擎：`google_lens`、`baidu`、`copyseeker`、`yandex`、`ascii2d`、`saucenao` |
| `serpapi_api_key` | SerpApi Key，用于 Google Lens 与 X 关键词检索 |
| `serpapi_visual` | 是否附带相似图（仅弱线索） |
| `saucenao_api_key` | 启用 `saucenao` 时必填 |
| `proxy` | 检索代理，可留空 |
| `search_timeout` / `x_lookup_timeout` | 识图 / X 读取超时 |
| `max_pages` / `max_posts` | 单次最多读取的来源页 / X 帖子数 |
| `cooldown_seconds` / `max_concurrency` | 用户冷却与全局并发上限 |
| `send_progress` / `send_result_image` | 进度提示 / 命中同图时附带结论图 |
| `cache_ttl_hours` | 结果缓存时长，0 关闭 |
| `x_keyword_search` | 是否用来源页文字去 X 反查 |
| `llm_enabled` / `llm_base_url` / `llm_api_key` / `llm_model` | 可选模型辅助，只抽取原文标识 |
| `nitter_base` | 可选 Nitter 地址，用于读取账号资料与图片 |
| `onebot_api_url` / `onebot_access_token` | 可选 OneBot HTTP 接口，用于读取引用消息图片 |

## 边界与隐私

- 只访问公网 HTTP(S) 页面；拒绝内网地址、凭据链接与非法端口，避免 SSRF。
- 只读取公开页面与公开元数据，不登录 X，也不使用 Cookie。
- 图片限制 10 MB / 30 MP；上传 SerpApi 前会压缩到 500 KB 内。
- 模型辅助是可选功能，默认关闭；启用后只发送来源页正文片段，不发送用户身份信息。

## 开发

```bash
# 语法与静态检查
python -m ruff check gsuid_core/plugins/ShadowSeek

# 单元测试
python -m pytest gsuid_core/plugins/ShadowSeek/tests -q
```

结构：

```
ShadowSeek/
├── __init__.py          # Plugins 注册
├── lens/
│   ├── command.py       # SV 命令、白名单、冷却、并发
│   ├── service.py       # 检索编排
│   ├── engines.py       # PicImageSearch 适配
│   ├── serpapi.py       # SerpApi Google Lens / X 搜索
│   ├── xmeta.py         # X 帖子与账号元数据
│   ├── clues.py         # 来源页正文与账号线索
│   ├── llm.py           # 可选模型抽取
│   ├── imageops.py      # 图片校验、压缩、同图比对
│   ├── rank.py          # 合并与排序
│   ├── cache.py         # dHash 结果缓存
│   ├── formatting.py    # 结果文案
│   ├── config.py        # 配置模板
│   ├── models.py        # 数据结构
│   └── urls.py          # URL 归一化与平台识别
├── assets/              # 图标、帮助图、结论图
└── tests/               # pytest
```

## 许可

MIT。见 [LICENSE](LICENSE)。
