metadata name = 'Isaac VM Shared Types'
metadata description = 'Shared exported types and default values for Isaac Linux VM Bicep deployments.'

/*
  Shared types
*/

@export()
@sealed()
@description('Common tags applied to deployed resources.')
type CommonTags = {
  @description('Deployment environment tag value.')
  environment: string
}

@export()
@sealed()
@description('Marketplace image configuration for the VM.')
type ImageConfig = {
  @description('Marketplace image publisher.')
  publisher: string

  @description('Marketplace image offer.')
  offer: string

  @description('Marketplace image SKU.')
  sku: string

  @description('Marketplace image version.')
  version: string
}

@export()
@sealed()
@description('Marketplace plan configuration required for paid/community images.')
type PlanConfig = {
  @description('Marketplace plan publisher.')
  publisher: string

  @description('Marketplace plan product.')
  product: string

  @description('Marketplace plan name.')
  name: string
}

@export()
@sealed()
@description('Managed disk sizing and SKU configuration.')
type DiskConfig = {
  @description('Managed disk storage SKU.')
  storageAccountType: 'Premium_LRS' | 'StandardSSD_LRS' | 'Standard_LRS'

  @description('Managed disk size in GiB.')
  @minValue(1)
  sizeGb: int

  @description('Disk caching mode.')
  caching: 'None' | 'ReadOnly' | 'ReadWrite'

  @description('Delete behavior when the VM is deleted.')
  deleteOption: 'Delete' | 'Detach'
}

/*
  Shared defaults
*/

@export()
@description('Default common tags aligned with the current reference VM deployment.')
var defaultCommonTags CommonTags = {
  environment: 'dev'
}

@export()
@description('Default marketplace image configuration for Isaac Sim Linux.')
var defaultImageConfig ImageConfig = {
  publisher: 'nvidia'
  offer: 'isaac_sim_developer_workstation'
  sku: 'isaac_sim_developer_workstation_community_linux'
  version: 'latest'
}

@export()
@description('Default marketplace plan configuration for Isaac Sim Linux.')
var defaultPlanConfig PlanConfig = {
  publisher: 'nvidia'
  product: 'isaac_sim_developer_workstation'
  name: 'isaac_sim_developer_workstation_community_linux'
}

@export()
@description('Default OS disk configuration aligned to reference intent.')
var defaultOsDiskConfig DiskConfig = {
  storageAccountType: 'Premium_LRS'
  sizeGb: 512
  caching: 'ReadWrite'
  deleteOption: 'Delete'
}

@export()
@description('Default data disk configuration aligned to reference intent.')
var defaultDataDiskConfig DiskConfig = {
  storageAccountType: 'Premium_LRS'
  sizeGb: 512
  caching: 'ReadWrite'
  deleteOption: 'Detach'
}
