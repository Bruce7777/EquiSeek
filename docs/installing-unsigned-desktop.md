# 未签名桌面版下载与安装

EquiSeek 当前 Alpha 没有 Apple Developer ID、Apple 公证或 Windows Authenticode 证书。它可以通过 GitHub Releases 分发，但操作系统不会显示“已验证开发者”，并可能警告或拒绝启动。

## 1. 确认下载内容

只从 <https://github.com/Bruce7777/EquiSeek/releases> 下载。不要把 GitHub 自动生成的 `Source code (zip)` 或 `Source code (tar.gz)` 当作安装包。

完整 Release 应包含：

- Apple Silicon：`EquiSeek-macOS-arm64-<版本>-unsigned.zip`
- Intel Mac：`EquiSeek-macOS-x64-<版本>-unsigned.zip`
- Windows x64：`EquiSeek-Windows-x64-<版本>-unsigned-Setup.exe`
- `SHA256SUMS`

缺少对应平台资产时，表示 GitHub Actions 尚未完成或已经失败，请等待维护者修复，不要从第三方转载站下载。

## 2. 校验 SHA-256

macOS 终端：

```bash
shasum -a 256 EquiSeek-*-unsigned.zip
```

Windows PowerShell：

```powershell
Get-FileHash .\EquiSeek-Windows-*-unsigned-Setup.exe -Algorithm SHA256
```

结果必须与同一 Release 中 `SHA256SUMS` 的对应行完全一致。不一致时不要运行文件。

## 3. macOS 安装

1. 根据处理器下载 arm64 或 x64 ZIP；“关于本机”中显示 Apple M 系列芯片时使用 arm64。
2. 解压 ZIP，将 `EquiSeek.app` 拖入“应用程序”。
3. 首次启动时，按住 Control 点击 `EquiSeek.app`，选择“打开”，再确认一次“打开”。
4. 如果仍被拦截，打开“系统设置 → 隐私与安全性”，确认被拦截的应用确实是 EquiSeek，再选择“仍要打开”。

可能遇到：

- 系统提示“无法验证开发者”或“Apple 无法检查其是否包含恶意软件”：这是未签名、未公证包的预期提示，不等于系统已经验证软件安全。
- 公司、学校或受管理 Mac 可能不提供“仍要打开”，此时无法在不违反设备策略的前提下安装。
- 下载架构错误时应用可能无法运行，Intel Mac 使用 x64；Apple Silicon 优先使用 arm64。
- 不建议从网络复制命令来批量关闭 Gatekeeper 或移除整机安全保护。

## 4. Windows 安装

1. 下载 `EquiSeek-Windows-x64-<版本>-unsigned-Setup.exe` 并核对 SHA-256。
2. 运行安装程序。Microsoft Defender SmartScreen 可能显示“Windows 已保护你的电脑”或“未知发布者”。
3. 确认文件来源和哈希后，可选择“更多信息 → 仍要运行”。

可能遇到：

- 属性中发布者会显示为未知，因为当前没有 Authenticode 证书。
- 杀毒软件可能对新发布、下载量少且内嵌 Python 运行时的程序进行额外扫描、隔离或误报。
- 企业组策略可能完全隐藏“仍要运行”或由安全软件阻止；不要尝试绕过组织策略。
- 当前只发布 Windows x64，不支持 32 位 Windows 或 Windows ARM 原生包。

## 5. 首次运行与数据

- 应用无需注册账号，不连接券商，也不会自动下单。
- 首次使用联网行情、宏观研究或模型服务时，系统防火墙可能询问是否允许网络访问。
- 用户数据默认保存在 `~/.equiseek/user-data`；卸载程序不会默认删除研究记录、设置和本地凭据。
- Tushare、DeepSeek、Tavily 等 Token 需要用户自行申请，并受各自服务条款约束。

## 6. 安全报告与问题反馈

普通安装问题请使用 GitHub Issues。安全漏洞不要公开粘贴密钥、持仓或本地路径；请使用仓库 Security 页面中的私密漏洞报告渠道。
