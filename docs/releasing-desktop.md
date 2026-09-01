# 桌面发布

当前产品版本统一为 `0.2.0`。首发二进制矩阵是 macOS arm64、macOS x64 与 Windows x64；Windows arm64 runner 和 Python sidecar 原生依赖验证完成前，不列入发布承诺。

## 产物

| 平台 | 架构 | 产物 | 必须验证 |
| --- | --- | --- | --- |
| macOS | arm64、x64 | 未签名 Alpha ZIP；未来签名版 PKG、ZIP | Alpha 明示未签名；签名版验证 Developer ID、公证票据与 Gatekeeper |
| Windows | x64 | 未签名 Alpha Setup.exe；未来签名版 Setup.exe、nupkg、RELEASES | Alpha 明示未签名；签名版验证 Authenticode 与时间戳 |

普通 `.github/workflows/desktop.yml` 只做未签名的三架构构建回归，保留 7 天，不作为用户下载入口。当前公开候选 tag `v0.2.0-alpha.6` 会触发 `.github/workflows/desktop-release-unsigned.yml`：三平台原生构建、确认没有发布者签名、统一生成带 `unsigned` 的文件名和 `SHA256SUMS`，最后创建 GitHub Pre-release。Alpha.1 在验收前撤销；Alpha.2 完成旧桌面清理，Alpha.3 同步最终投研助手布局，Alpha.4 修正 Windows CRLF Skill 加载；Alpha.5 明示无模型 Key 时的固定规则模式，并移除回答正文中重复的流水线说明；Alpha.6 深化 Agent Harness 的计划所有权、Deferred Tool、重复提醒、结果外置回读和事件 Trace。该流程不读取 secrets，只给最终 Release job 最小 `contents: write` 权限；后续 Alpha 必须连同版本和 Release notes 显式评审，避免旧说明被自动复用。

`.github/workflows/desktop-signed.yml` 继续保留为未来手动签名流程，使用受保护的 `desktop-release` environment，生成签名产物并保留 14 天；它不会创建 GitHub Release 或推送任何远端。

## 公开发布门禁

仓库已公开。每次面向普通用户发布时，不能只确认 tag 存在；必须在 GitHub Releases 中实际看到当前版本的 Pre-release，并确认：

- macOS arm64 ZIP、macOS x64 ZIP、Windows x64 Setup.exe 和 `SHA256SUMS` 共四个下载资产齐全；
- 三个安装包文件名都包含当前 Alpha 版本和 `unsigned`；
- Release 正文包含未签名提示并链接到[未签名安装指南](installing-unsigned-desktop.md)；
- 随机下载至少一个本机架构产物，SHA-256 与 `SHA256SUMS` 一致；
- 匿名访问仓库和 Releases 页面不再返回 404。

若页面只有 GitHub 自动生成的 Source code，说明发布 workflow 尚未完成或已经失败，不得把该状态作为面向不会编程用户的发布结果。

## 未签名 Alpha

未签名 Alpha 面向愿意接受系统警告的早期试用者，不得表述为“已签名”“已公证”“商店审核”或稳定正式版。Release 必须同时提供 macOS arm64/x64、Windows x64、SHA-256 校验和及中英文安装警告。macOS 用户需要通过 Gatekeeper 的明确人工确认，Windows 用户可能需要通过 SmartScreen 的“更多信息 → 仍要运行”；企业策略可能完全禁止安装。

## macOS 凭据

在 Apple Developer Program 申请并导出 Developer ID Application 与 Developer ID Installer 身份。CI 的一个 P12 可以同时包含两张证书及私钥；App Store Connect API key 用于 `notarytool`。

`desktop-release` environment 需要以下 secrets：

- `MACOS_CERTIFICATE_P12_BASE64`
- `MACOS_CERTIFICATE_PASSWORD`
- `MACOS_KEYCHAIN_PASSWORD`
- `MACOS_APPLICATION_IDENTITY`
- `MACOS_INSTALLER_IDENTITY`
- `APPLE_API_KEY_P8_BASE64`
- `APPLE_API_KEY_ID`
- `APPLE_API_ISSUER`

工作流在临时 keychain 导入证书，完成应用签名、公证、PKG 签名与 PKG 再公证，然后执行 `codesign --verify`、`stapler validate`、`spctl --assess` 和 `pkgutil --check-signature`。作业结束会删除临时 keychain、P12 与 P8。

## Windows 凭据

首选满足当前 CA 私钥硬件要求的云签名服务。新签发的公开信任代码签名证书通常不会提供可导出的 PFX；USB 硬件令牌需要连接令牌的自托管 Windows runner，不适合 GitHub 托管 runner。仓库当前的 PFX 注入路径仅用于已有且确实允许导出的合规证书；若使用 Microsoft Artifact Signing 或其他云签名服务，应替换 Forge 的 `windowsSign` hook，并通过 GitHub OIDC 或该服务的短期凭据授权，不能把长期云密钥写入仓库。

PFX 路径需要：

- `WINDOWS_CERTIFICATE_PFX_BASE64`
- `WINDOWS_CERTIFICATE_PASSWORD`

工作流签名应用内 EXE/DLL 与 Squirrel 安装器，并要求 `Get-AuthenticodeSignature` 对应用和 Setup.exe 都返回 `Valid`。PFX 在作业结束时删除。

## 许可证、SBOM 与校验和

每次 package/make 前，`scripts/prepare_release_compliance.py` 重新生成 `build/release-compliance`：

- 项目 `LICENSE`、`NOTICE` 与人工维护的 `THIRD_PARTY_NOTICES.md`；
- Python 和 npm 锁定依赖的原始许可证/NOTICE 文件；
- 缺少独立文本时使用 SPDX 3.28 标准许可证全文；
- Python 与 Electron/npm 的 CycloneDX 1.6 JSON SBOM；
- 包名、版本、许可证映射和 SHA-256 manifest。

该目录作为 Electron `extraResource` 进入每个应用包。签名工作流还为最终安装产物生成 `SHA256SUMS`。BaoStock wheel 只声明泛化的 `BSD License` 且未附许可证全文；生成器暂按外部扫描证据记录 `BSD-2-Clause` 映射并保留审查备注，必须在行情使用权终审时向权利方确认。

Forge 7.11.2 仍依赖有路径穿越告警的 packager 18.x。仓库通过 npm override 固定 `@electron/packager 20.0.4`（上游从 20.0.1 起移除 `extract-zip`），并用 `patch-package` 适配 packager 20 的 Promise hook API。升级 Forge 时必须先尝试删除 `apps/desktop/patches`，以全套 package/make 回归证明上游已原生兼容；不要长期无审查地叠加补丁。

## 本地与发布门禁

本地 `make desktop-package` 可以生成未签名测试包。未签名 Alpha 发布必须同时满足：

1. Python/TypeScript/Electron 测试与完整 `pip-audit`、`npm audit` 通过；
2. sidecar 在冻结后和应用包内分别自检通过；
3. 两份 SBOM 可验证、许可证索引无 UNKNOWN/缺失文本；
4. 文件名、标题和正文明确标记 `unsigned Alpha`，并附 Gatekeeper/SmartScreen 风险提示；
5. 三个平台资产和 `SHA256SUMS` 完整，安装、首次启动和凭据安全存储完成抽样验收；
6. 正式仓公开边界、安全报告渠道和负责任使用说明核查完成。

未来签名稳定版还必须增加 macOS/Windows 平台签名、公证、时间戳与干净系统安装验证；没有证书时不得把 Alpha 描述为签名稳定安装包。
