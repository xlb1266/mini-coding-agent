/** Skills 管理接口定义。 */

import { del, get, postForm, put } from './request'

/** 一条 Skill 登记信息；正文（instructions）只在详情接口返回。 */
export interface SkillItem {
  id: number
  /** workspace/skills 下的目录名，Skill 的稳定标识，编辑名称不改变它。 */
  dirName: string
  name: string
  description: string
  enabled: boolean
  createdAt: string
  /** SKILL.md 的修改时间；手动改文件后刷新列表即可看到变化。 */
  updatedAt: string
}

/** 单个 Skill 的完整内容，instructions 为 SKILL.md 的 Markdown 正文。 */
export interface SkillDetail extends SkillItem {
  instructions: string
}

/** 编辑保存的载荷；名称与说明写入 frontmatter，指令为正文。 */
export interface SkillPayload {
  name: string
  description: string
  instructions: string
}

export function listSkills(): Promise<SkillItem[]> {
  return get<SkillItem[]>('/api/skills')
}

export function getSkill(dirName: string): Promise<SkillDetail> {
  return get<SkillDetail>(`/api/skills/${encodeURIComponent(dirName)}`)
}

export function importSkill(file: File): Promise<SkillItem> {
  const form = new FormData()
  form.append('file', file)
  return postForm<SkillItem>('/api/skills/import', form)
}

export function updateSkill(dirName: string, payload: SkillPayload): Promise<SkillItem> {
  return put<SkillItem>(`/api/skills/${encodeURIComponent(dirName)}`, payload)
}

export function setSkillEnabled(dirName: string, enabled: boolean): Promise<SkillItem> {
  return put<SkillItem>(`/api/skills/${encodeURIComponent(dirName)}/enabled`, { enabled })
}

export function deleteSkill(dirName: string): Promise<void> {
  return del<void>(`/api/skills/${encodeURIComponent(dirName)}`)
}
