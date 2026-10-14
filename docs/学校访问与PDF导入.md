> 历史冻结功能说明：v2.2.0当前网页已移除PDF下载与导入入口。当前推荐流程见“三种推荐操作.md”。

# 学校访问、官方接口及本地 PDF 导入

本功能从 v2.0.5 起提供。程序仍可不配置任何账号使用公共检索、推荐和反馈。每个方向独立计算每周配额，默认各10篇。

## 学校图书馆访问

无需填写学校图书馆或机构登录链接。网页右上角选择“导出 → 下载论文 PDF（ZIP）”，程序优先复用本地文件和可用的 API/公开 PDF；需要机构授权时打开论文原文，尝试可见的机构登录入口。Elsevier 通常通过“Sign in via your institution”选择学校后进入学校身份认证，具体入口取决于学校是否支持联合认证。

首次在专用下载浏览器选择机构并完成学校登录，再回本地页面点“继续下载”。同一次程序运行复用该浏览器会话，退出程序后清除；机构自身的会话到期时仍可能要求重新认证。关闭网页标签不等于关闭 Python 服务。不要关闭下载浏览器；关闭它会使机构会话结束。

安装浏览器组件：在项目根目录运行 `python -m pip install '.[browser]'`，再运行 `python -m playwright install chromium`。本地 Linux 启动脚本会优先使用项目 `.venv`。下载模式需要可显示窗口的桌面会话，服务器后台环境不支持手动机构登录。可用 `LITE_BROWSER_EXECUTABLE` 指定兼容 Chromium 的可执行文件。

程序不读取登录表单、导出 Cookie 或保存认证状态；登录由使用者在浏览器完成。支持自动获取浏览器收到的 PDF、PDF 下载按钮和嵌入的 PDF。身份认证、验证码或权限问题会在本地页面提示继续／跳过，每次等待最多5分钟。不同出版商页面不保证自动识别成功，可在下载浏览器手动打开 PDF。

## Elsevier 官方接口

需自行申请并获准使用 Elsevier API，且具有对应摘要、全文和 PDF 格式权限。普通 ScienceDirect 网页登录或仅申请 API Key，不保证接口可用。API Key 和 Institutional Token 各人分别配置，不能放进 GitHub、分享包、前端代码或截图。

在启动服务的同一个终端设置环境变量，然后启动程序；已有服务须先停止再启动，刷新浏览器不能更新服务的环境变量。

Linux/macOS：

```bash
export ELSEVIER_API_KEY='填写自己的已获准API Key'
# 仅当 Elsevier 为你或机构提供此令牌时设置：
export ELSEVIER_INSTTOKEN='填写自己的机构令牌'
bash scripts/run_linux.sh /你的数据目录
```

macOS 也可运行 `bash scripts/run_macos.command /你的数据目录`。Windows PowerShell：

```powershell
$env:ELSEVIER_API_KEY = '填写自己的已获准API Key'
# 仅在具有机构令牌时设置：
$env:ELSEVIER_INSTTOKEN = '填写自己的机构令牌'
python -m lite_recommender --workspace '你的数据目录' serve --open
```

不要把实际密钥粘贴到共享说明中。环境变量只供本机 Python 服务读取，网页仅显示是否配置。

随后在设置中勾选「启用 Elsevier 官方接口补摘要及授权 PDF 下载」。

- 检索后、评分前，对缺少摘要的 Elsevier DOI（目前识别10.1016和10.1006前缀）尝试 `META_ABS`。每方向每轮最多20次，以限制等待和请求量；不改变检索词或检索源覆盖范围。
- 校验响应 DOI 与原记录一致后才采用摘要，记录 `abstract_source=elsevier_api`。
- 批量导出 PDF 时，对上述 DOI 先请求官方 PDF。接口拒绝、超限、缺少 PDF 格式权限或返回非 PDF 时回退公共直链；仍失败则网页导出进入机构浏览器及外部 DOI 查询。
- 凭据仅发给 `https://api.elsevier.com`，不跟随带凭据的重定向；接口错误不回显密钥。
- 仅联网失败或权限失败不会产生虚构摘要，也不会用登录页面充当 PDF。

官方参考：[API 认证](https://dev.elsevier.com/tecdoc_api_authentication.html)、[Article Retrieval API](https://dev.elsevier.com/documentation/ArticleRetrievalAPI.wadl)。本版本使用模拟接口验收；真实机构权限必须在使用者网络和账户上确认。

## 本地 PDF 导入与摘要核对

1. 在真实推荐、真实预览或“候选与逐篇筛选原因”中的论文卡片点「导入本地 PDF」，选择对应正文文件。每个文件最多25 MB，演示论文不接受导入。
2. 文件保存到 `<workspace>/_shared/pdfs/<paper_id>.pdf`。已有 PDF 不覆盖，导入不增加推荐配额，也不自动改变星级。
3. 若系统提供 Poppler 的 `pdftotext`，尝试从前两页提取明确的 Abstract/摘要段落。扫描件、复杂分栏或无明确摘要标记可能提取失败。
4. 在弹窗核对文章归属和摘要内容，必要时修正。确认后才更新论文元数据供后续检索评分使用；历史批次、原评分及推荐理由保持原样。可以仅保存 PDF，不确认摘要。
5. 已入推荐清单的论文，再点本批次「导出 → PDF」会复用已导入文件。待核验候选确认摘要后，请重新预览；历史候选分类和推荐快照保持原样，不自动转成正式推荐。

Poppler 属于可选系统依赖；缺少时 PDF 仍能导入，并可在核对窗口手动填写摘要。Windows 安装 Poppler 后将 `pdftotext.exe` 所在目录加入 PATH；macOS/Linux 使用系统包管理器安装 Poppler 工具。程序不自带 OCR，也不声称完成全文理解。

导入记录单独保存在同目录的 `<paper_id>.import.json`。升级保留原工作区；默认私有备份不包含 `_shared/pdfs` 正文及导入记录，需要时请单独备份此目录。

## 自动 Sci-Hub DOI 查询

机构返回明确无权限、机构页面访问失败、等待超时，或使用者点“机构无法获取，转查 Sci-Hub”后，自动用论文 DOI 打开内置外部查询入口，无需填写链接。当前默认入口为 `https://sci-hub.jp`。外部站点可能不可达、无该论文、出现验证或改变页面结构；均按论文记录失败，不保证获取成功，不自动扫描或切换镜像。

外部查询使用与机构隔离的浏览器上下文，不复制学校会话。收到完整 PDF 才保存；HTML、登录页和错误页不会改名为 PDF。可在外部浏览器完成验证、打开 PDF，或点“跳过此论文”。下载文件只写入个人工作区，不加入源码分享包。

浏览器 PDF 使用响应头拦截与有界分块写入；超过25 MiB或不完整的响应不保存。机构和外部上下文禁用 Service Worker，每次请求检查 DNS 公网地址。PDF结构与传输检查不代表正文归属已核验，下载报告保留来源和 `identity_verified=false`，请核对正文题名、DOI及版本。
