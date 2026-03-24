metadata name = 'Linux Isaac VM Module'
metadata description = 'Module placeholder for deploying a single Linux Isaac VM and related resources.'

import {
  CommonTags
  ShutdownSchedule
  ImageConfig
  PlanConfig
  DiskConfig
} from '../types.bicep'

/*
  Required parameters
*/

@description('Name of the VM to deploy.')
param vmName string

@description('Location for module resources.')
param location string

@description('Resource ID of the existing subnet used by the VM NIC.')
param subnetId string

@description('Resource ID of the existing network security group associated to the VM NIC.')
param nsgId string

@description('Admin username for the Linux VM.')
param adminUsername string

@description('Password for the Linux VM admin account.')
@secure()
param adminPassword string

@description('VM size for the deployed VM.')
param vmSize string

@description('Marketplace image configuration.')
param image ImageConfig

@description('Marketplace plan configuration.')
param plan PlanConfig

@description('OS disk configuration.')
param osDisk DiskConfig

@description('Data disk configuration.')
param dataDisk DiskConfig

@description('Daily auto-shutdown schedule for the VM.')
param shutdownSchedule ShutdownSchedule

@description('Optional MDE.Linux extension settings. Set to null to skip extension deployment.')
param mdeLinux object?

@description('Tags applied to module resources.')
param tags CommonTags

var defaultMdeLinuxSettings = {
  autoUpdate: true
  forceReOnboarding: false
  vNextEnabled: false
}

var effectiveMdeLinuxSettings = union(defaultMdeLinuxSettings, mdeLinux ?? {})
var installDevDepsScript = loadTextContent('../../scripts/install-dev-deps.sh')
var installDevDepsScriptBase64 = base64(installDevDepsScript)
var installThinLincScript = loadTextContent('../../scripts/install-thinlinc-silent.sh')
var installThinLincScriptBase64 = base64(installThinLincScript)

/*
  Resources
*/

@description('Network interface for the VM with private networking only.')
resource networkInterface 'Microsoft.Network/networkInterfaces@2023-09-01' = {
  name: '${vmName}-nic'
  location: location
  tags: tags
  properties: {
    networkSecurityGroup: {
      id: nsgId
    }
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          privateIPAddressVersion: 'IPv4'
          subnet: {
            id: subnetId
          }
        }
      }
    ]
  }
}

@description('Linux VM configured for marketplace image and plan deployment.')
resource virtualMachine 'Microsoft.Compute/virtualMachines@2023-09-01' = {
  name: vmName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  plan: {
    publisher: plan.publisher
    product: plan.product
    name: plan.name
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    storageProfile: {
      imageReference: {
        publisher: image.publisher
        offer: image.offer
        sku: image.sku
        version: image.version
      }
      osDisk: {
        createOption: 'FromImage'
        osType: 'Linux'
        caching: osDisk.caching
        diskSizeGB: osDisk.sizeGb
        deleteOption: osDisk.deleteOption
        managedDisk: {
          storageAccountType: osDisk.storageAccountType
        }
      }
      dataDisks: [
        {
          lun: 0
          createOption: 'Empty'
          caching: dataDisk.caching
          diskSizeGB: dataDisk.sizeGb
          deleteOption: dataDisk.deleteOption
          managedDisk: {
            storageAccountType: dataDisk.storageAccountType
          }
        }
      ]
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      adminPassword: adminPassword
      linuxConfiguration: {
        disablePasswordAuthentication: false
      }
    }
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: networkInterface.id
          properties: {
            deleteOption: 'Delete'
          }
        }
      ]
    }
  }
}

@description('Runs install-dev-deps.sh and install-thinlinc-silent.sh on the VM during provisioning via CustomScript extension.')
resource installDevDepsExtension 'Microsoft.Compute/virtualMachines/extensions@2023-09-01' = {
  parent: virtualMachine
  name: 'install-dev-deps'
  location: location
  tags: tags
  properties: {
    publisher: 'Microsoft.Azure.Extensions'
    type: 'CustomScript'
    typeHandlerVersion: '2.1'
    autoUpgradeMinorVersion: true
    settings: {
      commandToExecute: format('bash -lc "echo {0} | base64 -d > /tmp/install-dev-deps.sh && echo {1} | base64 -d > /tmp/install-thinlinc-silent.sh && chmod +x /tmp/install-dev-deps.sh /tmp/install-thinlinc-silent.sh && /tmp/install-dev-deps.sh {2} && /tmp/install-thinlinc-silent.sh"', installDevDepsScriptBase64, installThinLincScriptBase64, adminUsername)
    }
  }
}

@description('Defender for Servers extension for Linux VM onboarding.')
resource mdeExtension 'Microsoft.Compute/virtualMachines/extensions@2023-09-01' = if (mdeLinux != null) {
  parent: virtualMachine
  name: 'MDE.Linux'
  location: location
  tags: tags
  properties: {
    publisher: 'Microsoft.Azure.AzureDefenderForServers'
    type: 'MDE.Linux'
    typeHandlerVersion: '1.0'
    autoUpgradeMinorVersion: true
    settings: {
      autoUpdate: effectiveMdeLinuxSettings.autoUpdate
      azureResourceId: virtualMachine.id
      forceReOnboarding: effectiveMdeLinuxSettings.forceReOnboarding
      vNextEnabled: effectiveMdeLinuxSettings.vNextEnabled
    }
  }
}

@description('Daily VM auto-shutdown schedule without notifications.')
resource autoShutdownSchedule 'Microsoft.DevTestLab/schedules@2018-09-15' = {
  name: 'shutdown-computevm-${vmName}'
  location: location
  tags: tags
  properties: {
    status: 'Enabled'
    taskType: 'ComputeVmShutdownTask'
    dailyRecurrence: {
      time: shutdownSchedule.time
    }
    timeZoneId: shutdownSchedule.timeZoneId
    targetResourceId: virtualMachine.id
    notificationSettings: {
      status: 'Disabled'
      timeInMinutes: 30
      webhookUrl: ''
      emailRecipient: ''
      notificationLocale: 'en'
    }
  }
}

/*
  Outputs
*/

@description('Resource ID of the deployed virtual machine.')
output vmResourceId string = virtualMachine.id

@description('Resource ID of the deployed network interface.')
output nicResourceId string = networkInterface.id
