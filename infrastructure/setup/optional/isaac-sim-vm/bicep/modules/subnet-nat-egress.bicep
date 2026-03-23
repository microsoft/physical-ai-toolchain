metadata name = 'Subnet NAT Egress Module'
metadata description = 'Optional module to attach a NAT Gateway to an existing subnet for outbound internet egress without VM public IPs.'

@description('Location for NAT resources.')
param location string

@description('Name of the existing virtual network that contains the target subnet.')
param virtualNetworkName string

@description('Name of the existing subnet to attach the NAT gateway to.')
param subnetName string

@description('Address prefix of the existing subnet (required for subnet update operations).')
param subnetAddressPrefix string

@description('Name of the NAT gateway resource to create.')
param natGatewayName string

@description('Name of the Public IP resource used by the NAT gateway.')
param publicIpName string

@description('Tags applied to NAT resources.')
param tags object

@description('Idle timeout for NAT gateway connections, in minutes.')
@minValue(4)
@maxValue(120)
param idleTimeoutInMinutes int = 10

resource natPublicIp 'Microsoft.Network/publicIPAddresses@2023-09-01' = {
  name: publicIpName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    idleTimeoutInMinutes: idleTimeoutInMinutes
    deleteOption: 'Delete'
  }
}

resource natGateway 'Microsoft.Network/natGateways@2023-09-01' = {
  name: natGatewayName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    idleTimeoutInMinutes: idleTimeoutInMinutes
    publicIpAddresses: [
      {
        id: natPublicIp.id
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' existing = {
  name: virtualNetworkName
}

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2023-09-01' = {
  parent: vnet
  name: subnetName
  properties: {
    addressPrefix: subnetAddressPrefix
    natGateway: {
      id: natGateway.id
    }
  }
}

output natGatewayResourceId string = natGateway.id
output publicIpResourceId string = natPublicIp.id
