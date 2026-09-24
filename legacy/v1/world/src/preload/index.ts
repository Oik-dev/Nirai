import { contextBridge } from 'electron'
import { niraiApi } from './api'

contextBridge.exposeInMainWorld('nirai', niraiApi)
