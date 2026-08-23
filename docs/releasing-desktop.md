# 桌面签名发布

当前产品版本统一为 `0.2.0`。首发二进制矩阵是 macOS arm64、macOS x64 与 Windows x64；Windows arm64 runner 和 Python sidecar 原生依赖验证完成前，不列入发布承诺。

## 产物

| 平台 | 架构 | 产物 | 必须验证 |
| --- | --- | --- | --- |
| macOS | arm64、x64 | PKG、ZIP | Developer ID Application/Installer、notarytool、公证票据、Gatekeeper |
| Windows | x64 | Squirrel Setup.exe、nupkg、RELEASES | Authenticode、时间戳、签名状态 |

普通 `.github/workflows/desktop.yml` 只做未签名的三架构构建回归，保留 7 天，不得作为正式下载。`.github/workflows/desktop-signed.yml` 只能手动触发，使用受保护的 `desktop-release` environment，生成签名产物并保留 14 天；它不会创建 GitHub Release 或推送任何远端。

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

首选满足当前 CA 私钥硬件要求的证书或云签名服务。仓库当前提供 PFX 注入路径，适合已有合规 PFX 的 CI；若改用 Azure Trusted Signing，应替换 Forge 的 `windowsSign` hook，不能把云凭据写入仓库。

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

## 本地与正式门禁

本地 `make desktop-package` 可以生成未签名测试包；本机没有证书时不得把它描述为正式安装包。正式发布必须同时满足：

1. Python/TypeScript/Electron 测试与完整 `pip-audit`、`npm audit` 通过；
2. sidecar 在冻结后和应用包内分别自检通过；
3. 两份 SBOM 可验证、许可证索引无 UNKNOWN/缺失文本；
4. macOS/Windows 平台签名检查通过；
5. 安装、首次启动、卸载和凭据安全存储在干净系统人工验收；
6. 公开地址、安全报告渠道、行情权利、品牌与商标最终核查完成后，才创建正式 Release。
