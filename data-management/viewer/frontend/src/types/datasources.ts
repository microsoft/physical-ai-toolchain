/**
 * Data source and storage backend type definitions.
 * Supports local filesystem, Azure Blob Storage, and Hugging Face Hub.
 */

// ============================================================================
// Data Source Types
// ============================================================================

/** Local filesystem data source for edge device or development */
export interface LocalDataSource {
  type: 'local'
  /** Absolute or relative path to dataset directory */
  path: string
  /** Whether to watch for file changes */
  watchForChanges: boolean
}

/** Azure Blob Storage data source for cloud deployment */
export interface AzureBlobDataSource {
  type: 'azure-blob'
  /** Azure storage account name */
  accountName: string
  /** Blob container name */
  containerName: string
  /** SAS token for authentication (optional if using managed identity) */
  sasToken?: string
  /** Whether to use managed identity for authentication */
  managedIdentity?: boolean
}

/** Hugging Face Hub data source for public/private datasets */
export interface HuggingFaceDataSource {
  type: 'huggingface'
  /** Repository ID in format "owner/repo" */
  repoId: string
  /** Git revision (branch, tag, or commit hash) */
  revision?: string
  /** Hugging Face API token for private repos */
  token?: string
}

/** Union type for all data source configurations */
export type DataSource = LocalDataSource | AzureBlobDataSource | HuggingFaceDataSource

// ============================================================================
// Storage Backend Types
// ============================================================================

/** Storage backend type for annotation persistence */
export type StorageBackendType = 'local' | 'azure-blob' | 'indexeddb'

/** Storage backend configuration */
export interface StorageBackend {
  /** Type of storage backend */
  type: StorageBackendType
  /** Base path for storage (interpretation depends on type) */
  basePath?: string
}

// ============================================================================
// Connection Status
// ============================================================================

/** Connection status for remote data sources */
export type ConnectionStatus = 'connected' | 'disconnected' | 'connecting' | 'error'

/** Data source with connection status */
export interface DataSourceConnection {
  /** Data source configuration */
  source: DataSource
  /** Current connection status */
  status: ConnectionStatus
  /** Error message if status is 'error' */
  error?: string
  /** Last successful connection time */
  lastConnected?: string
}

// ============================================================================
// Helper Functions
// ============================================================================

/** Get display name for a data source */
export function getDataSourceDisplayName(source: DataSource): string {
  switch (source.type) {
    case 'local':
      return `Local: ${source.path}`
    case 'azure-blob':
      return `Azure: ${source.accountName}/${source.containerName}`
    case 'huggingface':
      return `HF: ${source.repoId}`
  }
}

/** Check if data source requires authentication */
export function requiresAuth(source: DataSource): boolean {
  switch (source.type) {
    case 'local':
      return false
    case 'azure-blob':
      return !source.managedIdentity && !source.sasToken
    case 'huggingface':
      return !!source.token
  }
}
