/** 本机系统能力接口：浏览器做不到、由本地后端代劳的桌面交互。 */

import { post } from './request'

/** 目录选择结果：path 为选中的绝对路径；用户取消时为 null。 */
export interface PickDirectoryResult {
  path: string | null
}

/**
 * 弹出后端所在本机的原生"选择文件夹"窗口。
 * 请求会一直挂起到用户完成选择或取消；窗口打不开时后端返回 500。
 */
export function pickDirectory(): Promise<PickDirectoryResult> {
  return post<PickDirectoryResult>('/api/system/pick-directory')
}
