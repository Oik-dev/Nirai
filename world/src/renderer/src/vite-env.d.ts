/// <reference types="vite/client" />

import type { NiraiApi } from '../../preload/api'

declare global {
  interface Window {
    nirai: NiraiApi
  }
}

export {}
