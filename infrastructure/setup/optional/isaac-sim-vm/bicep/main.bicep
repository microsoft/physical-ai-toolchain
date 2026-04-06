metadata name = 'Isaac Linux VM Main'
metadata description = 'Main orchestration entrypoint for deploying a single Isaac Linux VM.'

targetScope = 'resourceGroup'

import {
  CommonTags
  ShutdownSchedule
  ImageConfig
  PlanConfig
  DiskConfig
  defaultCommonTags
  defaultShutdownSchedule
  defaultImageConfig
  defaultPlanConfig
  defaultOsDiskConfig
  defaultDataDiskConfig
} from 'types.bicep'

/*
  Common parameters
*/

@description('Name of the virtual machine to deploy.')
param vmName string

@description('Location for deployed resources. Defaults to the current resource group location.')
param location string = resourceGroup().location

@description('Resource group that receives VM resources. Defaults to the current deployment resource group.')
param vmResourceGroup string = resourceGroup().name

@description('Tags applied to created resources.')
param tags CommonTags?

/*
  Networking parameters
*/

@description('Resource ID of the existing subnet used by the VM NIC.')
param subnetId string

@description('Resource ID of the existing network security group associated to the VM NIC.')
param nsgId string

@description('When true, deploys a NAT gateway and attaches it to the target subnet for outbound internet egress without a VM public IP.')
param enableSubnetNatGatewayEgress bool = false

@description('Optional NAT gateway name override. Used when enableSubnetNatGatewayEgress is true.')
param natGatewayName string = ''

@description('Optional Public IP name override for NAT gateway. Used when enableSubnetNatGatewayEgress is true.')
param natGatewayPublicIpName string = ''

/*
  Compute parameters
*/

@description('Admin username for the Linux VM.')
param adminUsername string

@description('Password for the Linux VM admin account.')
@secure()
param adminPassword string

@description('Virtual machine size.')
param vmSize string = 'Standard_NV36ads_A10_v5'

@description('Whether to enable EncryptionAtHost for the virtual machine.')
param shouldEnableEncryptionAtHost bool = true

@description('Deployment priority for the virtual machine. Use Spot only for test workloads that tolerate eviction.')
param vmPriority 'Regular' | 'Spot' = 'Regular'

@description('Eviction policy used when vmPriority is Spot.')
param spotEvictionPolicy 'Deallocate' | 'Delete' = 'Deallocate'

@description('Marketplace image configuration.')
param image ImageConfig?

@description('Marketplace plan configuration required for image deployment.')
param plan PlanConfig?

@description('OS disk configuration.')
param osDisk DiskConfig?

@description('Data disk configuration.')
param dataDisk DiskConfig?

@description('Daily auto-shutdown schedule for the VM.')
param shutdownSchedule ShutdownSchedule?

@description('Optional MDE.Linux extension settings. Set to null to skip extension deployment.')
param mdeLinux object?

/*
  Effective defaults
*/

var effectiveTags CommonTags = tags ?? defaultCommonTags
var effectiveImage ImageConfig = image ?? defaultImageConfig
var effectivePlan PlanConfig = plan ?? defaultPlanConfig
var effectiveOsDisk DiskConfig = osDisk ?? defaultOsDiskConfig
var effectiveDataDisk DiskConfig = dataDisk ?? defaultDataDiskConfig
var effectiveShutdownSchedule ShutdownSchedule = shutdownSchedule ?? defaultShutdownSchedule
var subnetIdParts = split(subnetId, '/')
var subnetSubscriptionId = subnetIdParts[2]
var subnetResourceGroupName = subnetIdParts[4]
var virtualNetworkName = subnetIdParts[8]
var subnetName = subnetIdParts[10]
var effectiveNatGatewayName = empty(natGatewayName) ? 'nat-${uniqueString(subnetId)}' : natGatewayName
var effectiveNatGatewayPublicIpName = empty(natGatewayPublicIpName) ? 'pip-nat-${uniqueString(subnetId)}' : natGatewayPublicIpName
var existingSubnetDefaultOutboundAccess = existingSubnet.properties.?defaultOutboundAccess
var existingSubnetNsg = existingSubnet.properties.?networkSecurityGroup
var existingSubnetServiceEndpoints = existingSubnet.properties.?serviceEndpoints
var existingSubnetDelegations = existingSubnet.properties.?delegations
var existingSubnetRouteTable = existingSubnet.properties.?routeTable

resource existingVnet 'Microsoft.Network/virtualNetworks@2023-09-01' existing = {
  scope: resourceGroup(subnetSubscriptionId, subnetResourceGroupName)
  name: virtualNetworkName
}

resource existingSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-09-01' existing = {
  parent: existingVnet
  name: subnetName
}

var discoveredSubnetAddressPrefix = !empty(existingSubnet.properties.addressPrefix)
  ? existingSubnet.properties.addressPrefix
  : existingSubnet.properties.addressPrefixes[0]

/*
  Modules
*/

module linuxIsaacVmModule 'modules/linux-isaac-vm.bicep' = {
  name: take('linux-isaac-vm-${vmName}-${uniqueString(resourceGroup().id, vmName)}', 64)
  scope: resourceGroup(subscription().subscriptionId, vmResourceGroup)
  params: {
    vmName: vmName
    location: location
    subnetId: subnetId
    nsgId: nsgId
    adminUsername: adminUsername
    adminPassword: adminPassword
    vmSize: vmSize
    shouldEnableEncryptionAtHost: shouldEnableEncryptionAtHost
    vmPriority: vmPriority
    spotEvictionPolicy: spotEvictionPolicy
    image: effectiveImage
    plan: effectivePlan
    osDisk: effectiveOsDisk
    dataDisk: effectiveDataDisk
    shutdownSchedule: effectiveShutdownSchedule
    mdeLinux: mdeLinux
    tags: effectiveTags
  }
}

module subnetNatEgressModule 'modules/subnet-nat-egress.bicep' = if (enableSubnetNatGatewayEgress) {
  name: take('subnet-nat-egress-${uniqueString(subnetId)}', 64)
  scope: resourceGroup(subnetSubscriptionId, subnetResourceGroupName)
  params: {
    location: location
    virtualNetworkName: virtualNetworkName
    subnetName: subnetName
    subnetAddressPrefix: discoveredSubnetAddressPrefix
    natGatewayName: effectiveNatGatewayName
    publicIpName: effectiveNatGatewayPublicIpName
    existingDefaultOutboundAccess: existingSubnetDefaultOutboundAccess
    existingNsg: existingSubnetNsg
    existingServiceEndpoints: existingSubnetServiceEndpoints
    existingDelegations: existingSubnetDelegations
    existingRouteTable: existingSubnetRouteTable
    tags: effectiveTags
  }
}

@description('Resource ID of the deployed virtual machine.')
output vmResourceId string = linuxIsaacVmModule.outputs.vmResourceId

@description('Resource ID of the deployed network interface.')
output nicResourceId string = linuxIsaacVmModule.outputs.nicResourceId
