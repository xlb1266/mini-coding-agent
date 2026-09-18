import { defineStore } from 'pinia'

import {
  deleteSkill as apiDeleteSkill,
  getSkill as apiGetSkill,
  importSkill as apiImportSkill,
  listSkills,
  setSkillEnabled as apiSetSkillEnabled,
  updateSkill as apiUpdateSkill,
  type SkillDetail,
  type SkillItem,
  type SkillPayload,
} from '../api/skills'

interface SkillsState {
  skills: SkillItem[]
  loading: boolean
  loaded: boolean
  /** 手动刷新时保留旧列表展示，仅标记刷新状态。 */
  refreshing: boolean
  importing: boolean
}

export const useSkillsStore = defineStore('skills', {
  state: (): SkillsState => ({
    skills: [],
    loading: false,
    loaded: false,
    refreshing: false,
    importing: false,
  }),
  actions: {
    /** 列表接口本身会重新扫描 workspace/skills 目录，手动创建的 Skill 也会出现。 */
    async fetchSkills(): Promise<void> {
      this.loading = true
      try {
        this.skills = await listSkills()
        this.loaded = true
      } finally {
        this.loading = false
      }
    },
    /** 刷新按钮：与 fetchSkills 相同，但不清空旧列表，只提示刷新中。 */
    async refreshSkills(): Promise<void> {
      if (this.refreshing) return
      this.refreshing = true
      try {
        await this.fetchSkills()
      } finally {
        this.refreshing = false
      }
    },
    async importSkill(file: File): Promise<SkillItem> {
      this.importing = true
      try {
        const created = await apiImportSkill(file)
        await this.fetchSkills()
        return created
      } finally {
        this.importing = false
      }
    },
    async updateSkill(dirName: string, payload: SkillPayload): Promise<SkillItem> {
      const updated = await apiUpdateSkill(dirName, payload)
      await this.fetchSkills()
      return updated
    },
    async setEnabled(dirName: string, enabled: boolean): Promise<void> {
      await apiSetSkillEnabled(dirName, enabled)
      await this.fetchSkills()
    },
    async removeSkill(dirName: string): Promise<void> {
      await apiDeleteSkill(dirName)
      await this.fetchSkills()
    },
    async loadSkill(dirName: string): Promise<SkillDetail> {
      return apiGetSkill(dirName)
    },
  },
})
