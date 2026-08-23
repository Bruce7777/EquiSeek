import path from 'node:path';
import type { ForgeConfig } from '@electron-forge/shared-types';
import { MakerPKG } from '@electron-forge/maker-pkg';
import { MakerSquirrel } from '@electron-forge/maker-squirrel';
import { MakerZIP } from '@electron-forge/maker-zip';
import { VitePlugin } from '@electron-forge/plugin-vite';

const macSigningEnabled = process.env.EQUISEEK_SIGN_MACOS === '1';
const windowsSigningEnabled = process.env.EQUISEEK_SIGN_WINDOWS === '1';

function required(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required for a signed release build`);
  return value;
}

const macApplicationIdentity = macSigningEnabled
  ? required('EQUISEEK_MACOS_APPLICATION_IDENTITY')
  : undefined;
const macInstallerIdentity = macSigningEnabled
  ? required('EQUISEEK_MACOS_INSTALLER_IDENTITY')
  : undefined;
const macKeychain = macSigningEnabled ? process.env.EQUISEEK_MACOS_KEYCHAIN : undefined;
const windowsCertificateFile = windowsSigningEnabled
  ? required('EQUISEEK_WINDOWS_CERTIFICATE_FILE')
  : undefined;
const windowsCertificatePassword = windowsSigningEnabled
  ? required('EQUISEEK_WINDOWS_CERTIFICATE_PASSWORD')
  : undefined;

const config: ForgeConfig = {
  packagerConfig: {
    asar: true,
    appBundleId: 'ai.equiseek.research',
    appCategoryType: 'public.app-category.finance',
    executableName: 'EquiSeek',
    icon: path.resolve(__dirname, 'assets/icon'),
    extraResource: [
      path.resolve(__dirname, process.platform === 'win32' ? '../../dist/equiseek-sidecar.exe' : '../../dist/equiseek-sidecar'),
      path.resolve(__dirname, '../../build/release-compliance'),
    ],
    electronZipDir: process.env.EQUISEEK_ELECTRON_ZIP_DIR || undefined,
    osxSign: macSigningEnabled
      ? {
          identity: macApplicationIdentity,
          keychain: macKeychain,
          optionsForFile: () => ({ hardenedRuntime: true }),
        }
      : undefined,
    osxNotarize: macSigningEnabled
      ? {
          appleApiKey: required('APPLE_API_KEY'),
          appleApiKeyId: required('APPLE_API_KEY_ID'),
          appleApiIssuer: required('APPLE_API_ISSUER'),
        }
      : undefined,
    windowsSign: windowsSigningEnabled
      ? {
          certificateFile: windowsCertificateFile,
          certificatePassword: windowsCertificatePassword,
          description: 'EquiSeek local-first investment research',
          timestampServer: 'http://timestamp.digicert.com',
        }
      : undefined,
    win32metadata: {
      CompanyName: 'EquiSeek Contributors',
      FileDescription: 'EquiSeek local-first investment research',
      ProductName: 'EquiSeek',
      InternalName: 'EquiSeek',
      OriginalFilename: 'EquiSeek.exe',
    },
  },
  rebuildConfig: {},
  makers: [
    ...(macSigningEnabled
      ? [
          new MakerPKG(
            {
              name: 'EquiSeek',
              identity: macInstallerIdentity,
              keychain: macKeychain,
              identityValidation: true,
            },
            ['darwin'],
          ),
        ]
      : []),
    new MakerZIP({}, ['darwin']),
    new MakerSquirrel(
      {
        name: 'EquiSeek',
        authors: 'EquiSeek Contributors',
        description: 'EquiSeek local-first open-source AI investment research platform',
        setupIcon: path.resolve(__dirname, 'assets/icon.ico'),
        ...(windowsSigningEnabled
          ? {
              certificateFile: windowsCertificateFile,
              certificatePassword: windowsCertificatePassword,
            }
          : {}),
      },
      ['win32'],
    ),
  ],
  plugins: [
    new VitePlugin({
      build: [
        { entry: 'src/main/main.ts', config: 'vite.main.config.mjs' },
        { entry: 'src/preload/preload.ts', config: 'vite.preload.config.mjs' },
      ],
      renderer: [{ name: 'main_window', config: 'vite.renderer.config.mjs' }],
    }),
  ],
};

export default config;
