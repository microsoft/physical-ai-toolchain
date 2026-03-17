import { describe, expect, it } from 'vitest'

import type { AzureBlobDataSource, HuggingFaceDataSource, LocalDataSource } from '../datasources'
import { getDataSourceDisplayName, requiresAuth } from '../datasources'

describe('getDataSourceDisplayName', () => {
  it('formats local data source', () => {
    const source: LocalDataSource = { type: 'local', path: '/data/demo', watchForChanges: false }
    expect(getDataSourceDisplayName(source)).toBe('Local: /data/demo')
  })

  it('formats Azure Blob data source', () => {
    const source: AzureBlobDataSource = {
      type: 'azure-blob',
      accountName: 'myaccount',
      containerName: 'datasets',
    }
    expect(getDataSourceDisplayName(source)).toBe('Azure: myaccount/datasets')
  })

  it('formats Hugging Face data source', () => {
    const source: HuggingFaceDataSource = {
      type: 'huggingface',
      repoId: 'lerobot/aloha_sim',
    }
    expect(getDataSourceDisplayName(source)).toBe('HF: lerobot/aloha_sim')
  })
})

describe('requiresAuth', () => {
  it('returns false for local data source', () => {
    const source: LocalDataSource = { type: 'local', path: '/data', watchForChanges: true }
    expect(requiresAuth(source)).toBe(false)
  })

  it('returns true for Azure Blob without credentials', () => {
    const source: AzureBlobDataSource = {
      type: 'azure-blob',
      accountName: 'acct',
      containerName: 'container',
    }
    expect(requiresAuth(source)).toBe(true)
  })

  it('returns false for Azure Blob with SAS token', () => {
    const source: AzureBlobDataSource = {
      type: 'azure-blob',
      accountName: 'acct',
      containerName: 'container',
      sasToken: 'sv=2024...',
    }
    expect(requiresAuth(source)).toBe(false)
  })

  it('returns false for Azure Blob with managed identity', () => {
    const source: AzureBlobDataSource = {
      type: 'azure-blob',
      accountName: 'acct',
      containerName: 'container',
      managedIdentity: true,
    }
    expect(requiresAuth(source)).toBe(false)
  })

  it('returns true for Hugging Face with token (has auth)', () => {
    const source: HuggingFaceDataSource = {
      type: 'huggingface',
      repoId: 'owner/repo',
      token: 'hf_abc123',
    }
    expect(requiresAuth(source)).toBe(true)
  })

  it('returns false for Hugging Face without token', () => {
    const source: HuggingFaceDataSource = {
      type: 'huggingface',
      repoId: 'owner/repo',
    }
    expect(requiresAuth(source)).toBe(false)
  })
})
