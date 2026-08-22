import type { ForgeConfig } from '@electron-forge/shared-types';
import { MakerZIP } from '@electron-forge/maker-zip';
import { VitePlugin } from '@electron-forge/plugin-vite';

const config: ForgeConfig = {
  packagerConfig: {
    asar: true,
    appBundleId: 'ai.equiseek.research',
    executableName: 'EquiSeek',
    icon: undefined,
    extraResource: ['../../dist/equiseek-sidecar'],
    electronZipDir: process.env.EQUISEEK_ELECTRON_ZIP_DIR || undefined,
  },
  rebuildConfig: {},
  makers: [new MakerZIP({}, ['darwin', 'win32'])],
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
