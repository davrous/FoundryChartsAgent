param location string
param tags resourceInput<'Microsoft.KeyVault/vaults@2025-05-01'>.tags
param keyVaultName string

module keyVault 'br/public:avm/res/key-vault/vault:0.14.2' = {
  name: keyVaultName
  params: {
    name: keyVaultName
    location: location
    tags: tags
    sku: 'standard'
    enableRbacAuthorization: true
    softDeleteRetentionInDays: 7
    enableVaultForDeployment: false
    enableVaultForTemplateDeployment: false
    enableVaultForDiskEncryption: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'None'
    }
    enableTelemetry: false
  }
}

output vaultName string = keyVault.outputs.name
output vaultUri string = keyVault.outputs.uri
