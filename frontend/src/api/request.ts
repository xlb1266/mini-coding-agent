/** 统一的 /api 请求封装。
 *
 * 所有请求一律使用相对路径 /api/**，由 Vite dev server 代理到本机后端，
 * 业务代码不得写死后端地址。
 *
 * 后端错误统一走 detail：字符串为普通提示；对象形如
 * { message, fields } 用于表单校验失败，fields 为 {字段名: 原因}。
 */

export interface FieldErrors {
  [field: string]: string
}

const DEFAULT_HEADERS: Record<string, string> = {
  'Content-Type': 'application/json',
}

export class ApiError extends Error {
  readonly status: number
  /** 表单校验失败时的字段级原因，供表单在字段旁展示。 */
  readonly fields?: FieldErrors

  constructor(message: string, status: number, fields?: FieldErrors) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.fields = fields
  }
}

function parseErrorDetail(detail: unknown): { message: string; fields?: FieldErrors } {
  if (typeof detail === 'string') {
    return { message: detail }
  }
  if (detail && typeof detail === 'object') {
    const { message, fields } = detail as { message?: unknown; fields?: unknown }
    return {
      message: typeof message === 'string' ? message : '请求失败',
      fields:
        fields && typeof fields === 'object' && !Array.isArray(fields)
          ? (fields as FieldErrors)
          : undefined,
    }
  }
  return { message: '请求失败' }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { headers, ...rest } = init

  // FormData（文件上传）不能手动设置 Content-Type，交给浏览器生成 multipart 边界
  const isFormData = typeof FormData !== 'undefined' && rest.body instanceof FormData
  const requestHeaders = isFormData
    ? { ...headers }
    : { ...DEFAULT_HEADERS, ...headers }

  let response: Response
  try {
    response = await fetch(path, { ...rest, headers: requestHeaders })
  } catch {
    throw new ApiError('网络请求失败，请确认后端服务已启动', 0)
  }

  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`
    let fields: FieldErrors | undefined
    try {
      const data: unknown = await response.json()
      if (data && typeof data === 'object' && 'detail' in data) {
        const parsed = parseErrorDetail((data as { detail: unknown }).detail)
        message = parsed.message
        fields = parsed.fields
      }
    } catch {
      // 响应体不是 JSON 时使用默认错误信息
    }
    throw new ApiError(message, response.status, fields)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export function get<T>(path: string): Promise<T> {
  return request<T>(path)
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

/** 上传文件：body 为 FormData，不设置 Content-Type，由浏览器生成 multipart 边界。 */
export function postForm<T>(path: string, form: FormData): Promise<T> {
  return request<T>(path, { method: 'POST', body: form })
}

export function put<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PUT',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' })
}
