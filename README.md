# Lite Recommender

在自己的电脑上检索论文、阅读评分，并让下一轮推荐参考这些反馈。适用于材料、力学、医学、机器人等不同研究方向；每个人维护自己的方向和数据。

**Python 3.10+；普通模式只使用 Python 标准库。无需 ChatGPT、Codex、订阅或购买服务器。** 可选语义增强需另装 Sentence Transformers；Elsevier 官方接口需使用者自己的获准 API 凭据。 本地网页、SQLite 数据库、推荐历史和评分保存在自己电脑上。真实文献检索和主页补摘要需要联网。PDF下载功能已冻结，当前界面不开放。

从本仓库右侧 **Releases** 下载稳定版。 [安装与升级](docs/GitHub发布与安装.md) · [完整使用说明](docs/使用说明.md) · [English Quick Start](docs/QUICKSTART_EN.md)

## 下载后如何启动

在 Releases 下载 `Lite_Recommender_v2.2.1.zip`，解压到长期保留的代码目录。安装 [Python 3.10 或更新版本](https://www.python.org/downloads/)。以下命令均在**解压后的项目根目录**执行。

建议把个人数据放在代码目录之外，方便之后更换程序版本。下面使用相邻的 `LiteRecommenderData` 文件夹；也可以替换为自己的绝对路径。

**Linux：**

```bash
bash scripts/run_linux.sh ../LiteRecommenderData
```

**Windows：**在项目文件夹打开命令提示符或 PowerShell，执行：

```powershell
.\scripts\run_windows.cmd -Workspace ..\LiteRecommenderData
```

也可以直接双击 `scripts/run_windows.cmd`；这会将数据存放在当前代码目录下的 `workspace/`。升级时要保留这个目录。

**macOS：**可在 Finder 双击 `scripts/run_macos.command`（数据默认保存在代码目录下的 `workspace/`），或从终端指定独立数据目录：

```bash
bash scripts/run_macos.command ../LiteRecommenderData
```

也可以直接使用 Python 命令：

```bash
python3 -m lite_recommender --workspace ../LiteRecommenderData init
python3 -m lite_recommender --workspace ../LiteRecommenderData serve --open
```

浏览器打开 `http://127.0.0.1:8765/`。保留启动程序的终端；按 `Ctrl+C` 停止服务。无需执行 `pip install` 即可从源码包运行。

Linux 已进行实际运行验证。Windows 和 macOS 提供启动方式，但本发布准备过程中未进行这两个平台的实机验收；遇到问题请附上系统、Python 版本和脱敏后的报错。当前是 Python 源码发行包，不是免安装 Python 的桌面安装程序。

## 每周推荐与三个手动入口

1. 新增方向，填写研究问题、核心概念和同义表达；勾选并启用方向。
2. 首次点击 **重选本周推荐** 生成当前周的可评分清单。
3. 阅读后评分，点击顶部 **保存全部反馈**。方向筛选后隐藏的修改也会保存；未修改、未评分论文不提交负反馈。
4. 设置中配置每方向 **核心5篇＋相关15篇**（默认），并按需要启用每周自动推荐。程序运行时按所选星期和时间执行；关机或关闭服务无法运行。

| 入口 | 适用情况 | 文献与评分 |
|---|---|---|
| 下周预览 | 看完本周还想继续阅读，或想看看最新反馈的效果 | 未展示过的新文献；不可评分，不更新持久偏好 |
| 重选本周推荐 | 对当前清单不满意，想换一批 | 替换本周清单，可评分；不把整轮自动当负反馈，沿用已生效策略 |
| 评分后加速迭代 | 所选方向当前清单全部评分并保存后，想快速细化兴趣 | 更新反馈偏好，提供新文献；这些评分参与后续每周推荐 |

预览使用当前已经发表的文献，并不模拟未来一周的发表情况。三种操作和每周自动推荐均排除该方向曾展示过的论文（包含预览），按DOI、arXiv和版本关联去重。历史已评分论文保留用于偏好学习；配置版本变更后按新版本隔离处理。

核心组优先完整概念匹配且有摘要的论文。相关组包括核心名额外的相关论文、邻近问题和标题相关但缺摘要的论文；仅标题条目明确标注证据范围，也可评分。程序尝试从公共主页补摘要并核对DOI或题名。

检索按两组目标自动扩展来源、同义词、邻近问题和分页，不固定每次200条，也不要求用户操作分页按钮。“检索与筛选过程”展示整个候选池和逐篇原因。找到足够数量后停止；网络、来源覆盖或运行边界导致不足时如实报告，不能保证完整召回或数据库全局最优。

每周历史只显示该周当前的正式清单；同周重选替换该历史位置。当前可评分清单和最新下周预览可切换，原始批次与评分仍保留。离线演示位于设置中，使用合成样例，与真实评分隔离。

## 推荐如何迭代

标题、摘要中的概念覆盖是主要依据；方法词只加分。用户星级、反馈原因及勾选标签更新有限词项偏好；可选 Sentence Transformers 在本机CPU做语义排序和高评分论文偏好匹配，不训练模型权重，不调用ChatGPT。未读、没时间、难取全文不直接作为研究兴趣的负反馈。

自由文字备注会保存，但不会自动解读为新的检索要求。没有新增有效反馈时反复保存不会重复学习。研究问题、关键词等变更会生成新的方向配置版本，旧评分保留但不自动跨版本学习。详见[反馈机制](docs/反馈与推荐机制.md)、[本地语义增强](docs/本地语义增强.md)。

## PDF功能冻结

PDF下载、导入、机构登录控制和下载报告已从当前网页移除。历史下载模块保留为冻结实现，恢复说明见[PDF冻结说明](docs/PDF功能冻结.md)。引文、JSON及Markdown周报导出和公共主页补摘要继续使用。

## 数据、备份与升级

```text
LiteRecommenderData/
├── settings.json                 推荐上限与周调度
├── topics.json                   方向目录注册
├── HEA/HEA_PN/profile.json        某个研究问题的配置
├── DDD/DDD_junction/profile.json
└── _shared/
    ├── library.sqlite            论文、评分、批次状态
    ├── cache/                    检索元数据缓存
    ├── runs/                     批次快照与报告
    ├── pdfs/                     已获取的 PDF
    ├── models/                   可选本地语义模型（不随源码分发）
    └── backups/                  手动创建的个人备份
```

推荐按「大方向／具体问题」整理；同一篇文献可共享元数据，各方向评分独立。数据库是当前状态来源，JSONL 历史文件不会自动双向同步。

网页可下载个人备份，也可运行：

```bash
python3 -m lite_recommender --workspace ../LiteRecommenderData backup
```

内置备份包含个人配置、反馈和报告，**不包含已下载 PDF**。完整备份应先停止程序，再复制整个数据目录。备份含个人研究信息，请勿上传公共仓库。

升级时解压新版本到另一个代码目录，保留原数据目录，使用同一个 `--workspace` 路径启动。不要覆盖或删除旧代码目录中的 `workspace/`，也不要从新空目录误启动后以为旧数据丢失。完整步骤见[数据保留与升级](docs/GitHub发布与安装.md#升级并保留评分)。

## 定时运行与设备范围

内置周调度默认关闭。启用后需要电脑开机且本地服务持续运行；停机期间不能执行。时区默认 `Asia/Shanghai`。可选 Linux 用户服务和 Windows 登录任务脚本不会自动注册，需明确安装。见[自动运行与恢复](docs/自动运行与恢复.md)。

当前仅监听本机 `127.0.0.1`。没有云端账号、跨设备同步、邮件推送或组内共享服务器功能。课题组可通过 GitHub 下载后各自运行，不同成员不会共享评分。手机不能通过自己的 `127.0.0.1` 连接另一台电脑上的服务。

## 验证与贡献

```bash
python3 -m lite_recommender verify
```

离线测试使用隔离数据与模拟响应，检索和 PDF 的真实可用性仍需单独联网核查。来源覆盖、摘要完整性和规则匹配可能影响结果；请核对 DOI、发表版本及原文。

源码发行包可验证包内清单：

```bash
python3 scripts/verify_release.py
```

这条命令适用于带 `MANIFEST.json` 的发行 ZIP；GitHub 自动生成的 Source code 压缩包不包含发行清单。

欢迎提交脱敏问题和改进，见[贡献说明](CONTRIBUTING.md)、[安全与隐私](SECURITY.md)。软件按 [MIT](LICENSE) 发布。源码构建白名单排除个人 workspace、数据库、反馈、PDF、缓存和凭据；不要直接把自己的运行目录压缩后分享。

## 文档

- [GitHub 下载、安装、升级与发布](docs/GitHub发布与安装.md)
- [完整中文使用说明](docs/使用说明.md)
- [方向配置说明](docs/方向配置说明.md)
- [反馈与推荐机制](docs/反馈与推荐机制.md)
- [本地语义增强](docs/本地语义增强.md)
- [学校访问与 PDF 导入](docs/学校访问与PDF导入.md)
- [旧项目迁移](docs/旧项目迁移.md)
- [PDF 保存](docs/PDF保存.md)
- [自动运行与恢复](docs/自动运行与恢复.md)
- [分享、发布与隐私](docs/发布与隐私.md)
- [English Quick Start](docs/QUICKSTART_EN.md)

## 学校访问与本地 PDF

当前支持可选Elsevier官方接口补摘要；PDF下载及机构浏览器界面保持冻结，参见[PDF冻结说明](docs/PDF功能冻结.md)。
