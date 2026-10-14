# GitHub 下载、安装、升级与发布

本项目供每位成员在自己的电脑上运行。GitHub 分发代码和发行文件，不替程序运行检索、保存个人数据库或执行周调度。无需 ChatGPT 账号、API Key、购买服务器或把 GitHub 密码交给他人。

## 下载稳定版

从本仓库右侧 **Releases** 进入稳定发行页，下载维护者上传的：

```text
Lite_Recommender_v2.1.1.zip
Lite_Recommender_v2.1.1.zip.sha256
Lite_Recommender_v2.1.1.MANIFEST.json
```

发行 ZIP 包括程序、网页资源、启动脚本、使用文档、通用方向示例和测试。旁边的 `Source code (zip/tar.gz)` 是 GitHub 自动生成的源码快照，也可运行，但不等于维护者构建并校验过的发行包。

校验 SHA-256。Linux：

```bash
sha256sum -c Lite_Recommender_v2.1.1.zip.sha256
```

macOS：

```bash
shasum -a 256 Lite_Recommender_v2.1.1.zip
```

Windows PowerShell：

```powershell
Get-FileHash .\Lite_Recommender_v2.1.1.zip -Algorithm SHA256
Get-Content .\Lite_Recommender_v2.1.1.zip.sha256
```

比对完整哈希值。校验值用于完整性核对，不是发布者身份签名；请从可信的仓库发行页取得文件和校验值。

## 安装与首次运行

1. 安装 [Python 3.10 或更新版本](https://www.python.org/downloads/)。Windows 安装时按安装程序说明选择可在命令行调用 Python 的方式。
2. 解压 ZIP，保留一个独立的数据目录。推荐如下布局：

   ```text
   LiteratureTools/
   ├── Lite_Recommender_v2.1.1/       程序，可以更换版本
   └── LiteRecommenderData/           个人数据，升级时继续保留
   ```

3. 在解压后的程序根目录打开终端，按系统运行下面的启动方式。

Linux：

```bash
bash scripts/run_linux.sh ../LiteRecommenderData
```

Windows 命令提示符或 PowerShell：

```powershell
.\scripts\run_windows.cmd -Workspace ..\LiteRecommenderData
```

macOS：

```bash
bash scripts/run_macos.command ../LiteRecommenderData
```

macOS 也可在 Finder 双击 `scripts/run_macos.command`，这会使用项目根目录的 `workspace/`。如果系统无法直接打开，请使用上面的终端命令，或从项目根目录运行 Python：

```bash
python3 -m lite_recommender --workspace ../LiteRecommenderData init
python3 -m lite_recommender --workspace ../LiteRecommenderData serve --open
```

浏览器访问 `http://127.0.0.1:8765/`。如果没有自动打开浏览器，请手动输入地址。按 `Ctrl+C` 停止服务。若端口已被其他程序占用，可以在 CLI 的 `serve` 命令后加 `--port 8766`，然后访问相应地址。

`--workspace` 是全局参数，放在 `init`、`serve` 等命令**之前**；相对路径按命令执行时的工作目录解析。启动脚本会定位程序目录，传给脚本的相对数据路径则按调用者当前目录解析。需要避免歧义时，使用自己的绝对数据路径并加引号。

Linux 已实际验收。Windows 与 macOS 的说明和脚本未在本发布准备过程中实机测试。当前发行包需要 Python；尚未提供免 Python 安装包或应用商店版。

双击 Windows 启动脚本、不传数据路径时，数据默认存放在程序根目录的 `workspace/`。CLI 不传路径时默认使用当前工作目录下的 `workspace/`。确认自己实际使用的位置再备份和升级。

## 第一轮推荐

添加自己的研究方向，先用「离线演示」认识界面，再用「检索预览」调试真实检索词。预览不占周配额，不改变正式推送去重记录，不提供评分。启用方向并点击「检索并推荐」后才能保存正式反馈。

「设置与定时运行」中的每方向每周推荐上限默认 10 篇，可设置更高的正整数；所有方向使用同一上限设置，但独立计数。低分不会退回已正式推送的名额，筛掉的候选和空批次不占名额。电脑和服务需保持运行，内置周调度才会执行。

导出菜单提供 BibTeX、JSON、Markdown 周报和论文 PDF（ZIP）。PDF 下载尝试结果中的公开直链及使用者启用且获准的 Elsevier 官方接口，部分成功时保留逐篇报告，全部失败时不给空包。详细限制见[PDF 保存](PDF保存.md)。

## 升级并保留评分

每次升级先保留可恢复的备份，再启动新代码。不要将程序和个人数据整体覆盖成新版本。

1. 在旧版本页面下载个人备份，或在旧程序根目录对**实际的数据路径**执行 `backup`。内置备份不包含 PDF。
2. 按 `Ctrl+C` 关闭旧服务，并确认没有另一份程序正在使用该数据目录。
3. 复制**整个数据目录**到单独的备份位置，包含 `_shared/library.sqlite`、配置、批次文件和 `_shared/pdfs/`。此副本可用于回退。不要把私人备份上传 GitHub。
4. 将新发行 ZIP 解压到新的代码目录，与旧代码和数据目录分开。
5. 从新代码目录使用原数据目录启动。如果沿用上面的相邻目录布局，各版本都用 `../LiteRecommenderData`；否则使用原目录的绝对路径。
6. 核对方向、历史批次、评分和上限。升级不会因为代码版本变化自动改变研究方向；手动修改方向词表产生的配置版本另行处理。

如果之前的数据在旧代码下的 `workspace/`，先停止服务，将整个 `workspace/` 复制到计划长期使用的独立数据目录，保留原目录，然后让新版本指向复制后的目录。不要只复制 SQLite 而遗漏方向配置与批次快照。

若需回退：停止新服务，将备份恢复到另一个数据目录，然后用旧版本代码指向该副本。不要在未经检查的情况下让旧版本直接写入已被新版修改的数据。未来版本若增加数据迁移要求，以该版本发行说明为准。

## 课题组分享

管理员分享稳定发行页；成员各自下载、配置研究问题和评分。可在讨论后共享**通用方向模板**，把关键词、方法词和研究阶段作为起点；评分、未公开研究计划和全文资料不自动共享。

公开仓库适合公开代码。若课题组希望限制代码访问，可使用私有仓库并按 GitHub 的仓库访问权限邀请成员。两种分发方式都不会给软件增加跨设备账号同步。

本机地址 `127.0.0.1` 只指当前设备。手机不能输入这个地址访问实验室电脑上的程序，当前版本也没有多人身份和数据隔离。请不要将它直接暴露到公网或通过简单改监听地址作为多人服务。

## 维护者构建与发布

确保源码不含个人数据，版本号和变更日志一致，然后在项目根目录执行：

```bash
python3 -m lite_recommender verify
python3 scripts/build_release.py
```

`dist/` 输出源码发行 ZIP、SHA-256 和清单。构建按白名单收录代码、文档、通用示例和测试，排除个人 workspace、数据库、缓存、PDF、密钥和备份。人工检查仍有必要；白名单无法识别人为写进源码或示例的所有私人文字。

在临时目录解压刚构建的 ZIP，进入解压根目录：

```bash
python3 scripts/verify_release.py
python3 -m lite_recommender verify
python3 -m lite_recommender --workspace ../acceptance-data init
```

首次方向应为空、调度关闭。测试离线演示后，真实检索和 PDF 可用性另行测试，记录来源警告与平台验证范围。不要把模拟响应的成功写成出版商真实下载一定成功。

创建与版本一致的 Git tag 和 GitHub Release，上传 ZIP、校验和和清单，并附上安装说明、主要变化、已测试平台与限制。用户不需要下载维护者的运行目录。公开上传前审阅待提交文件，不要执行无检查的 `git add .` 把个人测试数据一起纳入。

发布后从 GitHub 重新下载文件，比对 SHA-256，解压校验清单并在全新数据目录运行；仓库提交版本、tag 和上传的包应对应同一份代码。修改发布代码后应使用新版本，避免同一个版本有不同字节内容。

## 常见问题

**没有 ChatGPT 能用吗？** 能。迭代是本地 Python 的规则与权重调整，备注不会自动被大模型分析。

**需要 GitHub 账号吗？** 获取公开发行文件无需登录账号；访问私有仓库需要获得相应权限。

**没有联网还能使用吗？** 可以查看已有结果、保存反馈、导出和运行合成演示。真实检索与 PDF 获取需要网络。

**为什么重新检索得到 0 篇？** 检查批次警告、剩余配额、关键词匹配、摘要依据和已推送去重；正式重检不自动扩大范围。先用预览调词，预览仍可能因为候选不足返回 0 篇。

**升级后历史消失了吗？** 优先核对是否误用了新空数据目录。停止新服务后用原数据路径重新启动，不要删除原目录或初始化到原数据的上层位置。

**可以自动下载所有全文吗？** 不能保证。默认尝试已有公开 PDF 链接；可选 Elsevier 官方接口需要相应授权。学校和 Sci-Hub 入口是浏览器链接，程序不自动继承登录状态或抓取第三方内容。
