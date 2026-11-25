import { Toast } from '@douyinfe/semi-ui'

export async function showDownloadResult(download: Promise<void>): Promise<void> {
  try {
    await download
  } catch (error) {
    Toast.error(error instanceof Error ? error.message : '下载失败，请稍后重试')
  }
}
