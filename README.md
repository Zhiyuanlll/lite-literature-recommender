# Lite Recommender

在自己的电脑上检索论文、阅读评分，并让下一轮推荐参考这些反馈。适用于材料、力学、医学、机器人等不同研究方向；每个人维护自己的方向和数据。

**Python 3.10+；运行时只使用 Python 标准库。无需 ChatGPT、Codex、订阅、API Key 或购买服务器。** 本地网页、SQLite 数据库、推荐历史和评分保存在自己电脑上。真实文献检索和公开 PDF 下载需要联网。

从本仓库右侧 **Releases** 下载稳定版。 [安装与升级](docs/GitHub发布与安装.md) · [完整使用说明](docs/使用说明.md) · [English Quick Start](docs/QUICKSTART_EN.md)

## 下载后如何启动

在 Releases 下载 `Lite_Recommender_v2.0.4.zip`，解压到长期保留的代码目录。安装 [Python 3.10 或更新版本](https://www.python.org/downloads/)。以下命令均在**解压后的项目根目录**执行。

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

## 从第一轮开始

1. 点击研究方向旁的「＋」，填写研究问题、英文检索关键词、方法词和研究阶段。普通初始化没有预设研究方向。
2. 勾选方向，点击**检索预览**。它使用真实文献来源，不占正式周配额，可以调整关键词后重复预览。
3. 满意后启用方向并点击**检索并推荐**，生成正式批次。
4. 阅读论文，填写 1–5 分、评分原因和关联关键词／方法词。明确的偏好反馈用于下一轮的有限权重调整。
5. 在批次右上角「导出」保存 BibTeX、JSON、Markdown 周报，或下载公开原文 PDF。

「离线演示」使用明显标记的合成论文，不需要网络，不计入真实周配额。通用例子可按需加载，默认未启用：

```bash
python3 -m lite_recommender --workspace ../LiteRecommenderData init --examples
```

示例包含 HEA_PN、DDD_junction、医学影像和机器人方向。请根据自己的问题修改后再启用。

## 推荐与迭代

- **来源：**Crossref、OpenAlex、arXiv。来源失败会显示警告，真实检索不会悄悄替换成演示论文。
- **算法：**本地 Python 的规则评分与偏好调整，没有调用大模型，也没有训练语言模型。主要依据标题、摘要、关键词、方法词和研究阶段；分数不是论文质量或正确性的概率。
- **反馈：**评分、原因和明确选择的标签参与有限修正。自由文字备注会保存，但目前不会自动理解全部文字并生成检索策略；已读、没时间、难获取全文本身不等于低兴趣。
- **版本：**改分、撤回和重复保存不会不断叠加权重；不同方向与方向配置版本分别处理。修改研究问题、关键词等会生成新配置版本，旧评分保留，但不自动用于新版本学习。
- **研究阶段：**`survey`（综述入门）、`gap`（候选问题核对）、`focus`（聚焦跟踪）。研究问题由用户配置，报告不能代替全文核查，也不能据此宣称研究空白或新颖性。

详细算法、反馈门槛和证据范围见[反馈与推荐机制](docs/反馈与推荐机制.md)。

## 预览与每周推荐上限

所有方向共享每周正式推荐上限，**默认 10 篇，可在「设置与定时运行」中修改为更高的正整数**。每个本地安装独立保存自己的设置。

| 情况 | 是否占正式周配额 |
|---|---|
| 已正式推送，包括之后被打低分的论文 | 是 |
| 被筛掉的候选、返回 0 篇的批次 | 否 |
| 检索预览、离线演示、PDF 下载 | 否 |

预览可以显示以前正式推荐过的论文，不改变正式去重记录、不更新反馈策略、不提供评分入口。正式推荐会排除已正式推送过的论文，因此可能少于预览；预览不会自动转成正式批次。

检索结果不足时不会凑数。来源有 24 小时缓存，相同检索组合可能复用结果；新的词组会产生对应的新请求。反复点击正式推荐不一定会得到新论文。提高上限也不能保证候选足够；每方向每次候选最多 1000 篇。

## 公开原文 PDF

选择真实推荐或真实预览批次，点击「导出」→「下载论文 PDF（ZIP）」。无需先评分。程序尝试下载结果已有的公开 HTTPS PDF 直链；不会把摘要或网页生成成论文全文。

部分成功时，ZIP 包含已获取 PDF 和逐篇报告。全部失败时页面显示原因，不下载空 ZIP。缺少直链、登录限制、失效链接或出版商返回网页都可能导致失败；没有公开直链时需从原文页面按自己的权限获取。

下载不使用账号 cookie、机构登录或权限绕过。单篇上限 25 MiB，保存 PDF 不代表程序已经阅读全文。正常 DNS 解析直接使用系统结果；检测到代理 Fake-IP 时，可通过 Cloudflare DNS-over-HTTPS 查询出版商主机名，仅向该解析服务发送域名，不发送评分、研究方向或 PDF 路径。仍核验公网地址和 TLS 域名。

详见[PDF 保存与失败报告](docs/PDF保存.md)。MIT 软件许可证不授予论文全文再分发权限。

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
- [旧项目迁移](docs/旧项目迁移.md)
- [PDF 保存](docs/PDF保存.md)
- [自动运行与恢复](docs/自动运行与恢复.md)
- [分享、发布与隐私](docs/发布与隐私.md)
- [English Quick Start](docs/QUICKSTART_EN.md)
